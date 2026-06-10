from __future__ import annotations

import base64
import asyncio
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.db.models import UserStatus
from app.db.repositories import SubscriptionTokenRepository
from app.bot.handlers.user import _build_raw_vless_access_text, _build_vpn_access_text
from app.main import _run_timed_startup_step
from app.subscription_server import (
    SubscriptionHandler,
    _is_browser_navigation,
    _is_raw_subscription_request,
    _raw_subscription_url,
)
from app.services.subscription_builder import (
    SubscriptionProfile,
    build_happ_link,
    build_happ_redirect_url,
    build_subscription_response,
    build_subscription_url,
    build_xray_json_subscription_response,
)
from app.services.subscription_sync_service import build_snapshot_payload
from app.services.xray_service import XrayService
from scripts.configure_server2_xray_api import ensure_xray_api
from scripts.reconcile_server2_xray_users import (
    runtime_matches,
    strip_managed_clients_from_config,
    sync_managed_clients_in_config,
)


def _profile() -> SubscriptionProfile:
    return SubscriptionProfile(
        product="kVPN",
        public_base_url="https://vpn.nnqnn.tech",
        profile_title="kVPN @kkVPNrobot",
        update_interval_hours=1,
        traffic_total_bytes=0,
        support_url="https://t.me/support",
        announce_url="https://t.me/news",
        announce_text="kVPN auto update",
        vless_public_host="s2.nnqnn.tech",
        vless_public_port=9443,
        vless_security="reality",
        vless_type="tcp",
        vless_sni="yandex.ru",
        vless_flow="xtls-rprx-vision",
        vless_fp="chrome",
        vless_pbk="PUBLIC_KEY",
        vless_sid="a1b2c3d4e5f6a7b8",
        vless_path="",
        vless_header_type="",
        vless_remark_prefix="kVPN",
        whitelist_max_nodes=2,
    )


def _decode_body(body: str) -> str:
    return base64.b64decode(body).decode("utf-8")


def test_subscription_token_generation_is_long_and_random() -> None:
    first = SubscriptionTokenRepository._new_token()
    second = SubscriptionTokenRepository._new_token()

    assert len(first) >= 32
    assert first != second


def test_subscription_response_includes_main_node_for_active_user() -> None:
    snapshot = {
        "users": {
            "tok": {
                "telegram_id": 123,
                "uuid": "00000000-0000-0000-0000-000000000001",
                "main_vpn_active": True,
                "whitelist_enabled": False,
                "expire": 1781259930,
            }
        }
    }

    response = build_subscription_response(snapshot=snapshot, product="kVPN", token="tok", profile=_profile())

    assert response is not None
    decoded = _decode_body(response.body)
    assert decoded.startswith("vless://00000000-0000-0000-0000-000000000001@s2.nnqnn.tech:9443")
    assert "pbk=PUBLIC_KEY" in decoded
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["support-url"] == "https://t.me/support"
    assert response.headers["subscription-userinfo"].endswith("expire=1781259930")
    assert json.loads(base64.b64decode(response.headers["routing"]).decode("utf-8"))["domainStrategy"] == "IPIfNonMatch"


def test_subscription_response_includes_whitelist_nodes_only_for_buyer_with_expired_vpn() -> None:
    snapshot = {
        "users": {
            "tok": {
                "telegram_id": 123,
                "uuid": "00000000-0000-0000-0000-000000000001",
                "main_vpn_active": False,
                "whitelist_enabled": True,
                "expire": 0,
            }
        }
    }
    source = "\n".join(
        [
            "vless://wl-1@example.com:443?encryption=none#good",
            "vless://ru@example.com:443?encryption=none#russia",
            "vless://wl-2@example.com:443?encryption=none#good2",
        ]
    )

    response = build_subscription_response(
        snapshot=snapshot,
        product="kVPN",
        token="tok",
        profile=_profile(),
        whitelist_source_text=source,
    )

    assert response is not None
    decoded = _decode_body(response.body)
    assert "00000000-0000-0000-0000-000000000001@s2.nnqnn.tech" not in decoded
    assert decoded.splitlines() == [
        "vless://wl-1@example.com:443?encryption=none#good",
        "vless://wl-2@example.com:443?encryption=none#good2",
    ]
    assert response.headers["subscription-userinfo"].endswith("expire=0")


def test_subscription_response_returns_none_for_invalid_token() -> None:
    response = build_subscription_response(snapshot={"users": {}}, product="kVPN", token="bad", profile=_profile())

    assert response is None


def test_xray_json_response_uses_worker_profile_for_whitelist_only_user() -> None:
    snapshot = {
        "users": {
            "tok": {
                "telegram_id": 123,
                "uuid": "00000000-0000-0000-0000-000000000001",
                "main_vpn_active": False,
                "whitelist_enabled": True,
                "expire": 0,
            }
        }
    }
    worker_profile = {
        "outbounds": [
            {"tag": "auto-001", "protocol": "vless"},
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "block", "protocol": "blackhole"},
        ],
        "routing": {
            "rules": [{"network": "tcp,udp", "balancerTag": "auto"}],
            "balancers": [{"tag": "auto", "selector": ["auto-"], "strategy": {"type": "leastPing"}}],
        },
        "remarks": "worker profile",
    }

    response = build_xray_json_subscription_response(
        snapshot=snapshot,
        product="kVPN",
        token="tok",
        profile=_profile(),
        whitelist_profile=worker_profile,
    )

    assert response is not None
    configs = json.loads(response.body)
    assert isinstance(configs, list)
    assert len(configs) == 1
    config = configs[0]
    assert response.headers["content-type"] == "application/json; charset=utf-8"
    assert response.headers["support-url"] == "https://t.me/support"
    assert response.headers["subscription-userinfo"].endswith("expire=0")
    assert [outbound["tag"] for outbound in config["outbounds"]] == ["auto-001", "direct", "block"]
    assert config["remarks"] == "kVPN @kkVPNrobot - Обход белых списков"
    assert "vless://wl-1" not in response.body


def test_xray_json_response_separates_main_and_whitelist_profiles_for_full_access() -> None:
    snapshot = {
        "users": {
            "tok": {
                "telegram_id": 123,
                "uuid": "00000000-0000-0000-0000-000000000001",
                "main_vpn_active": True,
                "whitelist_enabled": True,
                "expire": 1781259930,
            }
        }
    }
    worker_profile = {
        "outbounds": [
            {"tag": "auto-001", "protocol": "vless"},
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "block", "protocol": "blackhole"},
        ],
        "routing": {
            "rules": [{"network": "tcp,udp", "balancerTag": "auto"}],
            "balancers": [{"tag": "auto", "selector": ["auto-"], "strategy": {"type": "leastPing"}}],
        },
    }

    response = build_xray_json_subscription_response(
        snapshot=snapshot,
        product="kVPN",
        token="tok",
        profile=_profile(),
        whitelist_profile=worker_profile,
    )

    assert response is not None
    configs = json.loads(response.body)
    assert isinstance(configs, list)
    assert len(configs) == 2
    main_config, whitelist_config = configs
    assert main_config["remarks"] == "kVPN @kkVPNrobot - Основной VPN"
    assert main_config["outbounds"][0]["tag"] == "proxy"
    assert main_config["outbounds"][0]["settings"]["vnext"][0]["address"] == "s2.nnqnn.tech"
    assert main_config["outbounds"][0]["streamSettings"]["realitySettings"]["serverName"] == "yandex.ru"
    assert whitelist_config["remarks"] == "kVPN @kkVPNrobot - Обход белых списков"
    assert whitelist_config["outbounds"][0]["tag"] == "auto-001"
    assert whitelist_config["routing"]["balancers"][0]["selector"] == ["auto-"]
    assert response.headers["subscription-userinfo"].endswith("expire=1781259930")


def test_xray_json_response_builds_main_only_profile_without_whitelist_fetch() -> None:
    snapshot = {
        "users": {
            "tok": {
                "telegram_id": 123,
                "uuid": "00000000-0000-0000-0000-000000000001",
                "main_vpn_active": True,
                "whitelist_enabled": False,
                "expire": 1781259930,
            }
        }
    }

    response = build_xray_json_subscription_response(
        snapshot=snapshot,
        product="kVPN",
        token="tok",
        profile=_profile(),
        whitelist_profile=None,
    )

    assert response is not None
    configs = json.loads(response.body)
    assert isinstance(configs, list)
    assert len(configs) == 1
    config = configs[0]
    assert [outbound["tag"] for outbound in config["outbounds"]] == ["proxy", "direct", "block"]
    assert config["routing"]["rules"][-1] == {"network": "tcp,udp", "outboundTag": "proxy"}


def test_xray_json_response_supports_vless_ws_tls_profile() -> None:
    snapshot = {
        "users": {
            "tok": {
                "telegram_id": 123,
                "uuid": "00000000-0000-0000-0000-000000000001",
                "main_vpn_active": True,
                "whitelist_enabled": False,
                "expire": 1781259930,
            }
        }
    }
    base_profile = _profile()
    profile = replace(
        base_profile,
        vless_public_host="abc.trycloudflare.com",
        vless_public_port=443,
        vless_security="tls",
        vless_type="ws",
        vless_sni="abc.trycloudflare.com",
        vless_flow="",
        vless_pbk="",
        vless_sid="",
        vless_path="/kvpn-ws",
    )

    response = build_xray_json_subscription_response(
        snapshot=snapshot,
        product="kVPN",
        token="tok",
        profile=profile,
        whitelist_profile=None,
    )

    assert response is not None
    config = json.loads(response.body)[0]
    outbound = config["outbounds"][0]
    assert outbound["settings"]["vnext"][0]["address"] == "abc.trycloudflare.com"
    assert outbound["settings"]["vnext"][0]["port"] == 443
    assert "flow" not in outbound["settings"]["vnext"][0]["users"][0]
    assert outbound["streamSettings"]["network"] == "ws"
    assert outbound["streamSettings"]["security"] == "tls"
    assert outbound["streamSettings"]["wsSettings"] == {
        "path": "/kvpn-ws",
        "headers": {"Host": "abc.trycloudflare.com"},
    }
    assert outbound["streamSettings"]["tlsSettings"]["serverName"] == "abc.trycloudflare.com"


def test_xray_json_response_returns_empty_profile_list_without_entitlements() -> None:
    snapshot = {
        "users": {
            "tok": {
                "telegram_id": 123,
                "uuid": "00000000-0000-0000-0000-000000000001",
                "main_vpn_active": False,
                "whitelist_enabled": False,
                "expire": 0,
            }
        }
    }

    response = build_xray_json_subscription_response(
        snapshot=snapshot,
        product="kVPN",
        token="tok",
        profile=_profile(),
        whitelist_profile=None,
    )

    assert response is not None
    assert json.loads(response.body) == []


def test_subscription_links() -> None:
    https_url = build_subscription_url("https://vpn.nnqnn.tech/", "kVPN", "abc")

    assert https_url == "https://vpn.nnqnn.tech/sub/kVPN/abc"
    assert build_happ_link(https_url) == "happ://add/https://vpn.nnqnn.tech/sub/kVPN/abc"
    assert build_happ_redirect_url("https://vpn.nnqnn.tech/", "kVPN", "abc") == (
        "https://vpn.nnqnn.tech/add/kVPN/abc"
    )
    assert _raw_subscription_url("https://vpn.nnqnn.tech/", "kVPN", "abc") == (
        "https://vpn.nnqnn.tech/sub/kVPN/abc?format=raw"
    )


def test_subscription_server_browser_redirect_detection() -> None:
    assert _is_browser_navigation("text/html,application/xhtml+xml")
    assert not _is_browser_navigation("application/json,*/*")
    assert _is_raw_subscription_request({"format": ["raw"]})
    assert _is_raw_subscription_request({"raw": ["1"]})
    assert not _is_raw_subscription_request({})


def test_bot_vpn_access_text_contains_happ_and_https_links() -> None:
    settings = SimpleNamespace(support_url="https://t.me/support")

    text = _build_vpn_access_text(
        settings=settings,
        main_status="активен до 10.06.2026 10:00",
        whitelist_status="доступен",
        happ_open_url="https://vpn.nnqnn.tech/add/kVPN/abc",
        happ_link="happ://add/https://vpn.nnqnn.tech/sub/kVPN/abc",
        https_link="https://vpn.nnqnn.tech/sub/kVPN/abc",
    )

    assert "https://vpn.nnqnn.tech/add/kVPN/abc" in text
    assert "https://vpn.nnqnn.tech/sub/kVPN/abc" in text
    assert "<code>https://vpn.nnqnn.tech/sub/kVPN/abc</code>" not in text
    assert '<a href="https://vpn.nnqnn.tech/sub/kVPN/abc">' not in text
    assert "Основной VPN: <b>активен до 10.06.2026 10:00</b>" in text
    assert "Обход белых списков: <b>доступен</b>" in text


def test_bot_raw_vless_access_text_keeps_legacy_key_format() -> None:
    settings = SimpleNamespace(support_url="https://t.me/support")
    link = "vless://uuid@example.com:443?encryption=none#VPN-1"

    text = _build_raw_vless_access_text(settings=settings, link=link)

    assert f"<code>{link}</code>" in text
    assert "Ваш VPN-ключ" in text


def test_subscription_server_log_message_redacts_tokens() -> None:
    message = 'GET /sub/kVPN/secret-token-value?x=1 HTTP/1.1'

    assert SubscriptionHandler._redact_tokens(message) == 'GET /sub/kVPN/*** HTTP/1.1'


def test_snapshot_payload_marks_main_and_whitelist_entitlements() -> None:
    now = datetime(2026, 6, 9, tzinfo=timezone.utc)
    active_user = SimpleNamespace(
        id=1,
        telegram_id=101,
        uuid="00000000-0000-0000-0000-000000000001",
        status=UserStatus.active,
        device_limit_blocked=False,
        expiration_date=now + timedelta(days=1),
    )
    expired_whitelist_user = SimpleNamespace(
        id=2,
        telegram_id=102,
        uuid="00000000-0000-0000-0000-000000000002",
        status=UserStatus.active,
        device_limit_blocked=False,
        expiration_date=now - timedelta(days=1),
    )

    snapshot = build_snapshot_payload(
        users=[active_user, expired_whitelist_user],
        whitelist_user_ids={2},
        tokens_by_user_id={1: "tok1", 2: "tok2"},
        product="kVPN",
        now=now,
    )

    assert snapshot["users"]["tok1"]["main_vpn_active"] is True
    assert snapshot["users"]["tok1"]["whitelist_enabled"] is False
    assert snapshot["users"]["tok2"]["main_vpn_active"] is False
    assert snapshot["users"]["tok2"]["whitelist_enabled"] is True


def test_old_chain_client_is_not_managed_by_bot() -> None:
    assert XrayService._is_managed_email("user-123@vpn.local") is True
    assert XrayService._is_managed_email("old-server@chain.local") is False


def test_remote_xray_config_sync_preserves_old_chain_and_removes_stale_users() -> None:
    service = XrayService(SimpleNamespace(xray_inbound_tag="upstream-in", vless_flow="xtls-rprx-vision"))
    config = {
        "inbounds": [
            {
                "tag": "upstream-in",
                "settings": {
                    "clients": [
                        {"id": "old-chain-id", "email": "old-server@chain.local"},
                        {"id": "stale-id", "email": "user-1@vpn.local", "flow": "xtls-rprx-vision"},
                    ]
                },
            }
        ]
    }

    changed = service._sync_managed_clients_in_config(
        config,
        {
            "user-2@vpn.local": "active-id-2",
            "user-3@vpn.local": "active-id-3",
        },
    )

    clients = config["inbounds"][0]["settings"]["clients"]
    assert changed is True
    assert {"id": "old-chain-id", "email": "old-server@chain.local"} in clients
    assert {"id": "stale-id", "email": "user-1@vpn.local", "flow": "xtls-rprx-vision"} not in clients
    assert {"id": "active-id-2", "email": "user-2@vpn.local", "flow": "xtls-rprx-vision"} in clients
    assert {"id": "active-id-3", "email": "user-3@vpn.local", "flow": "xtls-rprx-vision"} in clients


def test_server2_xray_api_config_preserves_old_chain_client() -> None:
    config = {
        "inbounds": [
            {
                "tag": "public-migrate-443",
                "listen": "0.0.0.0",
                "port": 443,
                "protocol": "vless",
                "settings": {"clients": []},
                "streamSettings": {"network": "tcp", "security": "reality"},
            },
            {
                "tag": "upstream-in",
                "port": 9443,
                "protocol": "vless",
                "settings": {
                    "decryption": "none",
                    "clients": [
                        {
                            "id": "7676d793-32f7-47b5-9444-b1a92bb6b96d",
                            "email": "old-server@chain.local",
                        }
                    ]
                },
                "streamSettings": {
                    "network": "tcp",
                    "security": "reality",
                    "realitySettings": {
                        "target": "www.cloudflare.com:443",
                        "serverNames": ["www.cloudflare.com"],
                        "privateKey": "PRIVATE_KEY",
                        "shortIds": ["a1b2c3d4e5f6a7b8"],
                    },
                },
            }
        ],
        "routing": {"rules": []},
    }

    changed = ensure_xray_api(config, api_port=10085)

    assert changed is True
    assert not any(inbound["tag"] == "public-migrate-443" for inbound in config["inbounds"])
    upstream = next(inbound for inbound in config["inbounds"] if inbound["tag"] == "upstream-in")
    assert upstream["settings"]["clients"][0]["email"] == "old-server@chain.local"
    assert "yandex.ru" in upstream["streamSettings"]["realitySettings"]["serverNames"]
    cdn_ws = next(inbound for inbound in config["inbounds"] if inbound["tag"] == "cdn-ws-in")
    assert cdn_ws["listen"] == "127.0.0.1"
    assert cdn_ws["port"] == 10086
    assert cdn_ws["streamSettings"]["network"] == "ws"
    assert cdn_ws["streamSettings"]["wsSettings"]["path"] == "/kvpn-ws"
    assert "flow" not in cdn_ws["settings"]["clients"][0]
    assert any(inbound["tag"] == "api" for inbound in config["inbounds"])
    assert config["routing"]["rules"][0] == {"type": "field", "inboundTag": ["api"], "outboundTag": "api"}


def test_remote_reconcile_config_is_idempotent_and_preserves_unmanaged_clients() -> None:
    config = {
        "inbounds": [
            {
                "tag": "upstream-in",
                "settings": {
                    "clients": [
                        {"id": "old-chain-id", "email": "old-server@chain.local"},
                        {"id": "active-id", "email": "user-2@vpn.local", "flow": "xtls-rprx-vision"},
                    ]
                },
            }
        ]
    }

    first_changed = sync_managed_clients_in_config(
        config,
        inbound_tag="upstream-in",
        expected={"user-2@vpn.local": "active-id"},
        flow="xtls-rprx-vision",
    )
    second_changed = sync_managed_clients_in_config(
        config,
        inbound_tag="upstream-in",
        expected={"user-2@vpn.local": "active-id"},
        flow="xtls-rprx-vision",
    )

    assert first_changed is False
    assert second_changed is False
    assert config["inbounds"][0]["settings"]["clients"] == [
        {"id": "old-chain-id", "email": "old-server@chain.local"},
        {"id": "active-id", "email": "user-2@vpn.local", "flow": "xtls-rprx-vision"},
    ]


def test_remote_reconcile_syncs_cdn_ws_inbound_without_flow() -> None:
    config = {
        "inbounds": [
            {
                "tag": "upstream-in",
                "settings": {"clients": []},
            },
            {
                "tag": "cdn-ws-in",
                "settings": {"clients": [{"id": "stale-id", "email": "user-1@vpn.local", "flow": "bad"}]},
            },
        ]
    }

    changed_main = sync_managed_clients_in_config(
        config,
        inbound_tag="upstream-in",
        expected={"user-2@vpn.local": "active-id"},
        flow="xtls-rprx-vision",
    )
    changed_cdn = sync_managed_clients_in_config(
        config,
        inbound_tag="cdn-ws-in",
        expected={"user-2@vpn.local": "active-id"},
        flow="",
    )

    assert changed_main is True
    assert changed_cdn is True
    assert config["inbounds"][0]["settings"]["clients"] == [
        {"id": "active-id", "email": "user-2@vpn.local", "flow": "xtls-rprx-vision"},
    ]
    assert config["inbounds"][1]["settings"]["clients"] == [
        {"id": "active-id", "email": "user-2@vpn.local"},
    ]


def test_remote_reconcile_can_strip_managed_users_from_config() -> None:
    config = {
        "inbounds": [
            {
                "tag": "upstream-in",
                "settings": {
                    "clients": [
                        {"id": "old-chain-id", "email": "old-server@chain.local"},
                        {"id": "active-id", "email": "user-2@vpn.local", "flow": "xtls-rprx-vision"},
                    ]
                },
            }
        ]
    }

    changed = strip_managed_clients_from_config(config, inbound_tag="upstream-in")
    second_changed = strip_managed_clients_from_config(config, inbound_tag="upstream-in")

    assert changed is True
    assert second_changed is False
    assert config["inbounds"][0]["settings"]["clients"] == [
        {"id": "old-chain-id", "email": "old-server@chain.local"},
    ]


def test_runtime_user_match_skips_existing_same_uuid_user() -> None:
    stdout = json.dumps(
        {
            "user": {
                "email": "user-2@vpn.local",
                "account": {"id": "active-id", "flow": "xtls-rprx-vision"},
            }
        }
    )

    assert runtime_matches(
        stdout,
        email="user-2@vpn.local",
        user_uuid="active-id",
        flow="xtls-rprx-vision",
    )
    assert not runtime_matches(
        stdout,
        email="user-2@vpn.local",
        user_uuid="different-id",
        flow="xtls-rprx-vision",
    )


def test_startup_step_timeout_does_not_escape_to_polling() -> None:
    asyncio.run(_run_timed_startup_step("slow", asyncio.sleep(0.05), 0.01))
