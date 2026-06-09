from __future__ import annotations

import base64
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, unquote, urlencode, urlsplit


@dataclass(frozen=True, slots=True)
class SubscriptionProfile:
    product: str
    public_base_url: str
    profile_title: str
    update_interval_hours: int
    traffic_total_bytes: int
    support_url: str
    announce_url: str
    announce_text: str
    vless_public_host: str
    vless_public_port: int
    vless_security: str
    vless_type: str
    vless_sni: str
    vless_flow: str
    vless_fp: str
    vless_pbk: str
    vless_sid: str
    vless_path: str
    vless_header_type: str
    vless_remark_prefix: str
    whitelist_max_nodes: int


@dataclass(frozen=True, slots=True)
class SnapshotUser:
    telegram_id: int
    uuid: str
    main_vpn_active: bool
    whitelist_enabled: bool
    expire: int


@dataclass(frozen=True, slots=True)
class SubscriptionResponse:
    body: str
    headers: dict[str, str]
    nodes: list[str]


def b64_text(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def build_happ_link(https_url: str) -> str:
    return f"happ://add/{https_url}"


def build_happ_redirect_url(base_url: str, product: str, token: str) -> str:
    base = base_url.rstrip("/")
    return f"{base}/add/{quote(product, safe='')}/{quote(token, safe='')}"


def build_subscription_url(base_url: str, product: str, token: str) -> str:
    base = base_url.rstrip("/")
    return f"{base}/sub/{quote(product, safe='')}/{quote(token, safe='')}"


def snapshot_user_from_payload(payload: dict[str, Any]) -> SnapshotUser:
    return SnapshotUser(
        telegram_id=int(payload["telegram_id"]),
        uuid=str(payload["uuid"]),
        main_vpn_active=bool(payload.get("main_vpn_active")),
        whitelist_enabled=bool(payload.get("whitelist_enabled")),
        expire=int(payload.get("expire") or 0),
    )


def build_subscription_response(
    *,
    snapshot: dict[str, Any],
    product: str,
    token: str,
    profile: SubscriptionProfile,
    whitelist_source_text: str = "",
) -> SubscriptionResponse | None:
    if product != profile.product:
        return None

    raw_user = snapshot.get("users", {}).get(token)
    if not isinstance(raw_user, dict):
        return None

    user = snapshot_user_from_payload(raw_user)
    nodes: list[str] = []
    if user.main_vpn_active:
        nodes.append(build_main_vless_node(user, profile))
    if user.whitelist_enabled:
        nodes.extend(filter_whitelist_vless_nodes(whitelist_source_text, max_nodes=profile.whitelist_max_nodes))

    nodes_text = "\n".join(nodes)
    https_url = build_subscription_url(profile.public_base_url, profile.product, token)
    headers = {
        "content-type": "text/plain; charset=utf-8",
        "cache-control": "no-store",
        "content-disposition": f"attachment; filename={profile.product}_{user.telegram_id}",
        "support-url": "https://t.me/kvpnpublic",
        "profile-title": f"base64:{b64_text(profile.profile_title)}",
        "profile-update-interval": str(profile.update_interval_hours),
        "announce": f"base64:{b64_text(_build_announce(profile.announce_text, user))}",
        "subscription-userinfo": _build_userinfo(user, profile),
        "announce-url": profile.announce_url,
        "profile-web-page-url": https_url,
        "routing": b64_text(json.dumps(build_default_routing(), ensure_ascii=False, separators=(",", ":"))),
    }
    return SubscriptionResponse(body=b64_text(nodes_text), headers=headers, nodes=nodes)


def build_xray_json_subscription_response(
    *,
    snapshot: dict[str, Any],
    product: str,
    token: str,
    profile: SubscriptionProfile,
    whitelist_profile: dict[str, Any] | None = None,
) -> SubscriptionResponse | None:
    if product != profile.product:
        return None

    raw_user = snapshot.get("users", {}).get(token)
    if not isinstance(raw_user, dict):
        return None

    user = snapshot_user_from_payload(raw_user)
    configs = build_xray_json_profiles(user, profile, whitelist_profile=whitelist_profile)
    https_url = build_subscription_url(profile.public_base_url, profile.product, token)
    headers = {
        "content-type": "application/json; charset=utf-8",
        "cache-control": "no-store",
        "content-disposition": f"attachment; filename={profile.product}_{user.telegram_id}.json",
        "support-url": "https://t.me/kvpn_support",
        "profile-title": f"base64:{b64_text(profile.profile_title)}",
        "profile-update-interval": str(profile.update_interval_hours),
        "subscription-auto-update-enable": "1",
        "announce": f"base64:{b64_text(_build_announce(profile.announce_text, user))}",
        "subscription-userinfo": _build_userinfo(user, profile),
        "announce-url": profile.announce_url,
        "profile-web-page-url": https_url,
    }
    body = json.dumps(configs, ensure_ascii=False, separators=(",", ":"))
    return SubscriptionResponse(body=body, headers=headers, nodes=_profile_remarks(configs))


def build_xray_json_profiles(
    user: SnapshotUser,
    profile: SubscriptionProfile,
    *,
    whitelist_profile: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    configs: list[dict[str, Any]] = []
    if user.main_vpn_active:
        configs.append(_build_single_main_config(user, profile))
    if user.whitelist_enabled and isinstance(whitelist_profile, dict):
        configs.append(_normalize_whitelist_profile(whitelist_profile, profile))
    return configs


def build_xray_json_config(
    user: SnapshotUser,
    profile: SubscriptionProfile,
    *,
    whitelist_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if user.main_vpn_active:
        return _build_single_main_config(user, profile)
    if user.whitelist_enabled and isinstance(whitelist_profile, dict):
        return _normalize_whitelist_profile(whitelist_profile, profile)
    return _build_blocked_config(profile)


def build_main_vless_node(user: SnapshotUser, profile: SubscriptionProfile) -> str:
    params: dict[str, str] = {"encryption": "none", "type": profile.vless_type}
    optional = {
        "security": profile.vless_security,
        "sni": profile.vless_sni,
        "flow": profile.vless_flow,
        "fp": profile.vless_fp,
        "pbk": profile.vless_pbk,
        "sid": profile.vless_sid,
        "path": profile.vless_path,
        "headerType": profile.vless_header_type,
    }
    for key, value in optional.items():
        if value:
            params[key] = value

    query = urlencode(params, doseq=False)
    remark = quote(f"{profile.vless_remark_prefix}-{user.telegram_id}")
    return f"vless://{user.uuid}@{profile.vless_public_host}:{profile.vless_public_port}?{query}#{remark}"


def build_main_xray_outbound(user: SnapshotUser, profile: SubscriptionProfile, *, tag: str = "proxy") -> dict[str, Any]:
    client: dict[str, Any] = {
        "id": user.uuid,
        "encryption": "none",
    }
    if profile.vless_flow:
        client["flow"] = profile.vless_flow

    stream_settings: dict[str, Any] = {
        "network": profile.vless_type,
        "security": profile.vless_security,
    }
    if profile.vless_type == "tcp":
        stream_settings["tcpSettings"] = {}
    elif profile.vless_type == "grpc":
        stream_settings["grpcSettings"] = {"serviceName": profile.vless_path.lstrip("/")}
    elif profile.vless_type == "xhttp":
        stream_settings["xhttpSettings"] = {
            "path": profile.vless_path or "/",
            "host": profile.vless_sni or profile.vless_public_host,
            "mode": "auto",
        }

    if profile.vless_security == "tls":
        stream_settings["tlsSettings"] = {
            "serverName": profile.vless_sni or profile.vless_public_host,
            "fingerprint": profile.vless_fp or "chrome",
        }
    elif profile.vless_security == "reality":
        stream_settings["realitySettings"] = {
            "show": False,
            "serverName": profile.vless_sni or profile.vless_public_host,
            "fingerprint": profile.vless_fp or "chrome",
            "publicKey": profile.vless_pbk,
            "shortId": profile.vless_sid,
            "spiderX": "/",
        }

    return {
        "tag": tag,
        "protocol": "vless",
        "settings": {
            "vnext": [
                {
                    "address": profile.vless_public_host,
                    "port": profile.vless_public_port,
                    "users": [client],
                }
            ]
        },
        "streamSettings": stream_settings,
    }


def _build_base_client_config(profile: SubscriptionProfile) -> dict[str, Any]:
    return {
        "log": {"loglevel": "warning"},
        "dns": {
            "servers": [
                "https://dns.google/dns-query",
                "https://cloudflare-dns.com/dns-query",
            ],
            "enableParallelQuery": True,
        },
        "inbounds": [
            {
                "tag": "socks",
                "port": 10808,
                "listen": "127.0.0.1",
                "protocol": "socks",
                "settings": {"udp": True, "auth": "noauth"},
                "sniffing": {
                    "enabled": True,
                    "routeOnly": True,
                    "destOverride": ["http", "tls", "quic"],
                },
            },
            {
                "tag": "http",
                "port": 10809,
                "listen": "127.0.0.1",
                "protocol": "http",
                "settings": {"allowTransparent": False},
                "sniffing": {
                    "enabled": True,
                    "routeOnly": True,
                    "destOverride": ["http", "tls", "quic"],
                },
            },
        ],
        "remarks": profile.profile_title,
    }


def _build_single_main_config(user: SnapshotUser, profile: SubscriptionProfile) -> dict[str, Any]:
    config = _build_base_client_config(profile)
    config["remarks"] = f"{profile.profile_title} - Основной VPN"
    config["outbounds"] = [
        build_main_xray_outbound(user, profile, tag="proxy"),
        {"tag": "direct", "protocol": "freedom"},
        {"tag": "block", "protocol": "blackhole"},
    ]
    config["routing"] = {
        "domainMatcher": "hybrid",
        "domainStrategy": "IPIfNonMatch",
        "rules": [
            {"ip": ["geoip:private"], "outboundTag": "direct"},
            {"domain": ["geosite:category-ads-all"], "outboundTag": "block"},
            {"protocol": ["bittorrent"], "outboundTag": "block"},
            {"network": "tcp,udp", "outboundTag": "proxy"},
        ],
    }
    return config


def _build_blocked_config(profile: SubscriptionProfile) -> dict[str, Any]:
    config = _build_base_client_config(profile)
    config["outbounds"] = [
        {"tag": "direct", "protocol": "freedom"},
        {"tag": "block", "protocol": "blackhole"},
    ]
    config["routing"] = {
        "domainMatcher": "hybrid",
        "domainStrategy": "IPIfNonMatch",
        "rules": [
            {"ip": ["geoip:private"], "outboundTag": "direct"},
            {"network": "tcp,udp", "outboundTag": "block"},
        ],
    }
    return config


def _normalize_whitelist_profile(whitelist_profile: dict[str, Any], profile: SubscriptionProfile) -> dict[str, Any]:
    config = deepcopy(whitelist_profile)
    config["remarks"] = f"{profile.profile_title} - Обход белых списков"
    config.setdefault("log", {"loglevel": "warning"})
    config.setdefault("outbounds", [])
    if not isinstance(config["outbounds"], list):
        config["outbounds"] = []
    config.setdefault("routing", {})
    if not isinstance(config["routing"], dict):
        config["routing"] = {}
    return config


def _merge_main_outbound_into_balancer(config: dict[str, Any], main_outbound: dict[str, Any]) -> None:
    outbounds = config.setdefault("outbounds", [])
    if not isinstance(outbounds, list):
        outbounds = []
        config["outbounds"] = outbounds

    main_tag = str(main_outbound["tag"])
    outbounds[:] = [outbound for outbound in outbounds if not isinstance(outbound, dict) or outbound.get("tag") != main_tag]
    outbounds.insert(0, main_outbound)

    routing = config.setdefault("routing", {})
    if not isinstance(routing, dict):
        routing = {}
        config["routing"] = routing

    balancers = routing.setdefault("balancers", [])
    if not isinstance(balancers, list):
        balancers = []
        routing["balancers"] = balancers

    auto_balancer = None
    for balancer in balancers:
        if isinstance(balancer, dict) and balancer.get("tag") == "auto":
            auto_balancer = balancer
            break

    if auto_balancer is None:
        auto_balancer = {
            "tag": "auto",
            "selector": ["auto-"],
            "fallbackTag": "block",
            "strategy": {"type": "leastPing"},
        }
        balancers.append(auto_balancer)
    else:
        selector = auto_balancer.setdefault("selector", [])
        if not isinstance(selector, list):
            selector = []
            auto_balancer["selector"] = selector
        if "auto-" not in selector:
            selector.append("auto-")
        auto_balancer.setdefault("fallbackTag", "block")
        auto_balancer.setdefault("strategy", {"type": "leastPing"})

    rules = routing.setdefault("rules", [])
    if not isinstance(rules, list):
        rules = []
        routing["rules"] = rules
    has_auto_network_rule = any(
        isinstance(rule, dict) and rule.get("network") == "tcp,udp" and rule.get("balancerTag") == "auto"
        for rule in rules
    )
    if not has_auto_network_rule:
        rules.append({"network": "tcp,udp", "balancerTag": "auto"})


def _config_outbound_tags(config: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    outbounds = config.get("outbounds")
    if not isinstance(outbounds, list):
        return tags
    for outbound in outbounds:
        if isinstance(outbound, dict) and isinstance(outbound.get("tag"), str):
            tags.append(outbound["tag"])
    return tags


def _profile_remarks(configs: list[dict[str, Any]]) -> list[str]:
    remarks: list[str] = []
    for config in configs:
        value = config.get("remarks")
        if isinstance(value, str):
            remarks.append(value)
    return remarks


def filter_whitelist_vless_nodes(source_text: str, *, max_nodes: int) -> list[str]:
    nodes: list[str] = []
    for raw_line in source_text.splitlines():
        line = raw_line.strip()
        if not line.startswith("vless://"):
            continue
        if _looks_like_russian_node(line):
            continue
        nodes.append(line)
        if len(nodes) >= max_nodes:
            break
    return nodes


def build_default_routing() -> dict[str, Any]:
    return {
        "domainStrategy": "IPIfNonMatch",
        "rules": [
            {
                "type": "field",
                "protocol": ["bittorrent"],
                "outboundTag": "block",
            },
            {
                "type": "field",
                "ip": ["geoip:private"],
                "outboundTag": "direct",
            },
            {
                "type": "field",
                "network": "tcp,udp",
                "outboundTag": "proxy",
            },
        ],
    }


def _build_userinfo(user: SnapshotUser, profile: SubscriptionProfile) -> str:
    expire = user.expire if user.main_vpn_active else 0
    return f"upload=0; download=0; total={profile.traffic_total_bytes}; expire={expire}"


def _build_announce(base_text: str, user: SnapshotUser) -> str:
    main_status = "active" if user.main_vpn_active else "inactive"
    whitelist_status = "enabled" if user.whitelist_enabled else "disabled"
    return "\n".join(
        [
            base_text.strip(),
            f"Main VPN: {main_status}",
            f"Whitelist bypass: {whitelist_status}",
        ]
    ).strip()


def _looks_like_russian_node(link: str) -> bool:
    lower = link.lower()
    if "%f0%9f%87%b7%f0%9f%87%ba" in lower:
        return True
    if "#ru" in lower or "russia" in lower or "%d1%80%d0%be%d1%81" in lower:
        return True

    try:
        decoded_name = unquote(urlsplit(link).fragment).strip().lower()
    except ValueError:
        return False
    return decoded_name.startswith("ru") or decoded_name.startswith("russia")
