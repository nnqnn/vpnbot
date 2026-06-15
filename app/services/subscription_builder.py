from __future__ import annotations

import base64
import json
from copy import deepcopy
from dataclasses import dataclass
from ipaddress import ip_address
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
    profile_web_page_url: str
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
    vless_xhttp_mode: str
    vless_header_type: str
    vless_remark_prefix: str
    whitelist_max_nodes: int
    main_bridge_enabled: bool = False
    main_bridge_max_nodes: int = 8
    fallback_vless_public_host: str = ""
    fallback_vless_public_port: int = 443
    fallback_vless_security: str = "reality"
    fallback_vless_type: str = "tcp"
    fallback_vless_sni: str = "yandex.ru"
    fallback_vless_flow: str = "xtls-rprx-vision"
    fallback_vless_fp: str = "chrome"
    fallback_vless_pbk: str = ""
    fallback_vless_sid: str = "a1b2c3d4e5f6a7b8"
    fallback_vless_path: str = ""
    fallback_vless_xhttp_mode: str = "packet-up"
    noflow_vless_public_host: str = ""
    noflow_vless_public_port: int = 8443
    noflow_vless_security: str = "reality"
    noflow_vless_type: str = "tcp"
    noflow_vless_sni: str = "www.yandex.ru"
    noflow_vless_fp: str = "chrome"
    noflow_vless_pbk: str = ""
    noflow_vless_sid: str = "a1b2c3d4e5f6a7b8"
    noflow_vless_path: str = ""
    noflow_vless_xhttp_mode: str = "packet-up"
    xhttp_vless_public_host: str = ""
    xhttp_vless_public_port: int = 8444
    xhttp_vless_security: str = "tls"
    xhttp_vless_type: str = "xhttp"
    xhttp_vless_sni: str = "s2.nnqnn.tech"
    xhttp_vless_fp: str = "chrome"
    xhttp_vless_path: str = "/kvpn-xhttp"
    xhttp_vless_xhttp_mode: str = "packet-up"
    hysteria2_public_host: str = ""
    hysteria2_public_port: int = 443
    hysteria2_sni: str = "s2.nnqnn.tech"
    hysteria2_fp: str = "chrome"
    hysteria2_auth: str = ""
    hysteria2_udp_idle_timeout: int = 60
    legacy_vless_public_host: str = ""
    legacy_vless_public_port: int = 8443
    legacy_vless_security: str = "reality"
    legacy_vless_type: str = "tcp"
    legacy_vless_sni: str = "yandex.ru"
    legacy_vless_flow: str = "xtls-rprx-vision"
    legacy_vless_fp: str = "chrome"
    legacy_vless_pbk: str = ""
    legacy_vless_sid: str = ""
    legacy_vless_path: str = ""
    legacy_vless_xhttp_mode: str = "packet-up"


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
    profile_web_page_url = profile.profile_web_page_url or https_url
    headers = {
        "content-type": "text/plain; charset=utf-8",
        "cache-control": "no-store",
        "content-disposition": f"attachment; filename={profile.product}_{user.telegram_id}",
        "support-url": profile.support_url,
        "profile-title": f"base64:{b64_text(profile.profile_title)}",
        "profile-update-interval": str(profile.update_interval_hours),
        "announce": f"base64:{b64_text(_build_announce(profile.announce_text, user))}",
        "subscription-userinfo": _build_userinfo(user, profile),
        "announce-url": profile.announce_url,
        "profile-web-page-url": profile_web_page_url,
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
    profile_web_page_url = profile.profile_web_page_url or https_url
    headers = {
        "content-type": "application/json; charset=utf-8",
        "cache-control": "no-store",
        "content-disposition": f"attachment; filename={profile.product}_{user.telegram_id}.json",
        "support-url": profile.support_url,
        "profile-title": f"base64:{b64_text(profile.profile_title)}",
        "profile-update-interval": str(profile.update_interval_hours),
        "subscription-auto-update-enable": "1",
        "announce": f"base64:{b64_text(_build_announce(profile.announce_text, user))}",
        "subscription-userinfo": _build_userinfo(user, profile),
        "announce-url": profile.announce_url,
        "profile-web-page-url": profile_web_page_url,
    }
    body = json.dumps(configs, ensure_ascii=False, separators=(",", ":"))
    return SubscriptionResponse(body=body, headers=headers, nodes=_profile_remarks(configs))


def build_debug_xray_json_subscription_response(
    *,
    snapshot: dict[str, Any],
    product: str,
    token: str,
    profile: SubscriptionProfile,
) -> SubscriptionResponse | None:
    if product != profile.product:
        return None

    raw_user = snapshot.get("users", {}).get(token)
    if not isinstance(raw_user, dict):
        return None

    user = snapshot_user_from_payload(raw_user)
    config = build_debug_direct_tcp_profile(user, profile)
    headers = {
        "content-type": "application/json; charset=utf-8",
        "cache-control": "no-store",
        "content-disposition": f"attachment; filename={profile.product}_{user.telegram_id}_debug.json",
        "support-url": profile.support_url,
        "profile-title": f"base64:{b64_text('kVPN DEBUG DIRECT TCP ONLY')}",
        "profile-update-interval": "1",
        "subscription-auto-update-enable": "0",
        "announce-url": profile.announce_url,
        "profile-web-page-url": profile.announce_url,
    }
    body = json.dumps([config], ensure_ascii=False, separators=(",", ":"))
    return SubscriptionResponse(body=body, headers=headers, nodes=[str(config["remarks"])])


def build_xray_json_profiles(
    user: SnapshotUser,
    profile: SubscriptionProfile,
    *,
    whitelist_profile: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    configs: list[dict[str, Any]] = []
    if user.whitelist_enabled and isinstance(whitelist_profile, dict):
        configs.append(_normalize_whitelist_profile(whitelist_profile, profile))
    if user.main_vpn_active:
        configs.extend(_build_main_profile_configs(user, profile, bridge_profile=whitelist_profile))
    return configs


def build_xray_json_config(
    user: SnapshotUser,
    profile: SubscriptionProfile,
    *,
    whitelist_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if user.main_vpn_active:
        return _build_single_main_config(user, profile, bridge_profile=whitelist_profile)
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
    if profile.vless_type == "xhttp":
        optional["host"] = profile.vless_sni or profile.vless_public_host
        optional["mode"] = profile.vless_xhttp_mode or "packet-up"
    for key, value in optional.items():
        if value:
            params[key] = value

    query = urlencode(params, doseq=False)
    remark = quote(f"{profile.vless_remark_prefix}-{user.telegram_id}")
    return f"vless://{user.uuid}@{profile.vless_public_host}:{profile.vless_public_port}?{query}#{remark}"


def build_main_xray_outbound(user: SnapshotUser, profile: SubscriptionProfile, *, tag: str = "proxy") -> dict[str, Any]:
    return _build_vless_xray_outbound(
        user=user,
        tag=tag,
        host=profile.vless_public_host,
        port=profile.vless_public_port,
        security=profile.vless_security,
        transport=profile.vless_type,
        sni=profile.vless_sni,
        flow=profile.vless_flow,
        fp=profile.vless_fp,
        pbk=profile.vless_pbk,
        sid=profile.vless_sid,
        path=profile.vless_path,
        xhttp_mode=profile.vless_xhttp_mode,
    )


def build_fallback_xray_outbound(
    user: SnapshotUser,
    profile: SubscriptionProfile,
    *,
    tag: str = "proxy-direct",
) -> dict[str, Any] | None:
    if not profile.fallback_vless_public_host:
        return None
    if profile.fallback_vless_security == "reality" and not profile.fallback_vless_pbk:
        return None
    return _build_vless_xray_outbound(
        user=user,
        tag=tag,
        host=profile.fallback_vless_public_host,
        port=profile.fallback_vless_public_port,
        security=profile.fallback_vless_security,
        transport=profile.fallback_vless_type,
        sni=profile.fallback_vless_sni,
        flow=profile.fallback_vless_flow,
        fp=profile.fallback_vless_fp,
        pbk=profile.fallback_vless_pbk,
        sid=profile.fallback_vless_sid,
        path=profile.fallback_vless_path,
        xhttp_mode=profile.fallback_vless_xhttp_mode,
    )


def build_legacy_xray_outbound(
    user: SnapshotUser,
    profile: SubscriptionProfile,
    *,
    tag: str = "proxy-legacy",
) -> dict[str, Any] | None:
    if not profile.legacy_vless_public_host:
        return None
    if profile.legacy_vless_security == "reality" and not profile.legacy_vless_pbk:
        return None
    return _build_vless_xray_outbound(
        user=user,
        tag=tag,
        host=profile.legacy_vless_public_host,
        port=profile.legacy_vless_public_port,
        security=profile.legacy_vless_security,
        transport=profile.legacy_vless_type,
        sni=profile.legacy_vless_sni,
        flow=profile.legacy_vless_flow,
        fp=profile.legacy_vless_fp,
        pbk=profile.legacy_vless_pbk,
        sid=profile.legacy_vless_sid,
        path=profile.legacy_vless_path,
        xhttp_mode=profile.legacy_vless_xhttp_mode,
    )


def build_noflow_xray_outbound(
    user: SnapshotUser,
    profile: SubscriptionProfile,
    *,
    tag: str = "proxy",
) -> dict[str, Any] | None:
    if not profile.noflow_vless_public_host:
        return None
    if profile.noflow_vless_security == "reality" and not profile.noflow_vless_pbk:
        return None
    return _build_vless_xray_outbound(
        user=user,
        tag=tag,
        host=profile.noflow_vless_public_host,
        port=profile.noflow_vless_public_port,
        security=profile.noflow_vless_security,
        transport=profile.noflow_vless_type,
        sni=profile.noflow_vless_sni,
        flow="",
        fp=profile.noflow_vless_fp,
        pbk=profile.noflow_vless_pbk,
        sid=profile.noflow_vless_sid,
        path=profile.noflow_vless_path,
        xhttp_mode=profile.noflow_vless_xhttp_mode,
    )


def build_xhttp_xray_outbound(
    user: SnapshotUser,
    profile: SubscriptionProfile,
    *,
    tag: str = "proxy",
) -> dict[str, Any] | None:
    if not profile.xhttp_vless_public_host:
        return None
    return _build_vless_xray_outbound(
        user=user,
        tag=tag,
        host=profile.xhttp_vless_public_host,
        port=profile.xhttp_vless_public_port,
        security=profile.xhttp_vless_security,
        transport=profile.xhttp_vless_type,
        sni=profile.xhttp_vless_sni,
        flow="",
        fp=profile.xhttp_vless_fp,
        pbk="",
        sid="",
        path=profile.xhttp_vless_path,
        xhttp_mode=profile.xhttp_vless_xhttp_mode,
    )


def build_hysteria2_xray_outbound(
    user: SnapshotUser,
    profile: SubscriptionProfile,
    *,
    tag: str = "proxy",
) -> dict[str, Any] | None:
    if not profile.hysteria2_public_host or not profile.hysteria2_auth:
        return None
    host = profile.hysteria2_public_host
    return {
        "tag": tag,
        "protocol": "hysteria",
        "settings": {
            "version": 2,
            "address": host,
            "port": profile.hysteria2_public_port,
        },
        "streamSettings": {
            "network": "hysteria",
            "security": "tls",
            "tlsSettings": {
                "serverName": profile.hysteria2_sni or host,
                "fingerprint": profile.hysteria2_fp or "chrome",
            },
            "hysteriaSettings": {
                "version": 2,
                "auth": profile.hysteria2_auth,
                "udpIdleTimeout": profile.hysteria2_udp_idle_timeout,
            },
        },
    }


def _build_chained_main_outbound(
    user: SnapshotUser,
    profile: SubscriptionProfile,
    *,
    tag: str,
    bridge_tag: str,
) -> dict[str, Any]:
    outbound = build_fallback_xray_outbound(user, profile, tag=tag)
    if outbound is None:
        outbound = build_main_xray_outbound(user, profile, tag=tag)
    outbound["proxySettings"] = {
        "tag": bridge_tag,
        "transportLayer": True,
    }
    return outbound


def _build_main_profile_configs(
    user: SnapshotUser,
    profile: SubscriptionProfile,
    *,
    bridge_profile: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    configs: list[dict[str, Any]] = []

    hysteria2_outbound = build_hysteria2_xray_outbound(user, profile)
    if hysteria2_outbound is not None:
        configs.append(
            _build_single_outbound_main_config(
                profile,
                remarks="Основной #1 🇳🇱",
                outbound=hysteria2_outbound,
                server_host=profile.hysteria2_public_host,
            )
        )

    noflow_outbound = build_noflow_xray_outbound(user, profile)
    if noflow_outbound is not None:
        configs.append(
            _build_single_outbound_main_config(
                profile,
                remarks="Запасной #1 🇳🇱",
                outbound=noflow_outbound,
                server_host=profile.noflow_vless_public_host,
            )
        )

    xhttp_outbound = build_xhttp_xray_outbound(user, profile)
    if xhttp_outbound is not None:
        configs.append(
            _build_single_outbound_main_config(
                profile,
                remarks="Запасной #2 🇳🇱",
                outbound=xhttp_outbound,
                server_host=profile.xhttp_vless_public_host,
            )
        )

    configs.append(_build_single_main_config(user, profile, bridge_profile=bridge_profile))

    return configs


def _build_bridge_outbounds(bridge_profile: dict[str, Any] | None, *, max_nodes: int) -> list[dict[str, Any]]:
    if max_nodes <= 0 or not isinstance(bridge_profile, dict):
        return []

    raw_outbounds = bridge_profile.get("outbounds")
    if not isinstance(raw_outbounds, list):
        return []

    bridge_outbounds: list[dict[str, Any]] = []
    for outbound in raw_outbounds:
        if not isinstance(outbound, dict):
            continue
        if outbound.get("protocol") in {"freedom", "blackhole", "dns"}:
            continue
        if not isinstance(outbound.get("settings"), dict):
            continue

        copied = deepcopy(outbound)
        copied["tag"] = f"bridge-{len(bridge_outbounds) + 1:03d}"
        copied.pop("proxySettings", None)
        bridge_outbounds.append(copied)
        if len(bridge_outbounds) >= max_nodes:
            break
    return bridge_outbounds


def _build_vless_xray_outbound(
    *,
    user: SnapshotUser,
    tag: str,
    host: str,
    port: int,
    security: str,
    transport: str,
    sni: str,
    flow: str,
    fp: str,
    pbk: str,
    sid: str,
    path: str,
    xhttp_mode: str,
) -> dict[str, Any]:
    client: dict[str, Any] = {
        "id": user.uuid,
        "encryption": "none",
    }
    if flow:
        client["flow"] = flow

    stream_settings: dict[str, Any] = {
        "network": transport,
        "security": security,
    }
    if transport == "tcp":
        stream_settings["tcpSettings"] = {}
    elif transport == "grpc":
        stream_settings["grpcSettings"] = {"serviceName": path.lstrip("/")}
    elif transport == "xhttp":
        stream_settings["xhttpSettings"] = {
            "path": path or "/",
            "host": sni or host,
            "mode": xhttp_mode or "packet-up",
        }
    elif transport == "ws":
        stream_settings["wsSettings"] = {
            "path": path or "/",
            "headers": {"Host": sni or host},
        }

    if security == "tls":
        stream_settings["tlsSettings"] = {
            "serverName": sni or host,
            "fingerprint": fp or "chrome",
        }
    elif security == "reality":
        stream_settings["realitySettings"] = {
            "show": False,
            "serverName": sni or host,
            "fingerprint": fp or "chrome",
            "publicKey": pbk,
            "shortId": sid,
            "spiderX": "/",
        }

    return {
        "tag": tag,
        "protocol": "vless",
        "settings": {
            "vnext": [
                {
                    "address": host,
                    "port": port,
                    "users": [client],
                }
            ]
        },
        "streamSettings": stream_settings,
    }


def _build_single_outbound_main_config(
    profile: SubscriptionProfile,
    *,
    remarks: str,
    outbound: dict[str, Any],
    server_host: str,
) -> dict[str, Any]:
    config = _build_base_client_config(profile)
    config["remarks"] = remarks
    config["outbounds"] = [
        outbound,
        {
            "tag": "direct",
            "protocol": "freedom",
            "settings": {"domainStrategy": "UseIPv4"},
        },
        {"tag": "block", "protocol": "blackhole"},
    ]
    direct_hosts = [host for host in {profile.vless_public_host, server_host} if host]
    direct_ip_rules = [host for host in direct_hosts if _is_ip_address(host)]
    direct_ip_rules.append("geoip:private")
    direct_domain_rules = [f"domain:{host}" for host in direct_hosts if not _is_ip_address(host)]
    rules: list[dict[str, Any]] = [{"ip": direct_ip_rules, "outboundTag": "direct"}]
    if direct_domain_rules:
        rules.append({"domain": direct_domain_rules, "outboundTag": "direct"})
    rules.extend(
        [
            {"network": "udp", "port": "443", "outboundTag": "block"},
            {"protocol": ["bittorrent"], "outboundTag": "block"},
            {"network": "tcp,udp", "outboundTag": "proxy"},
        ]
    )
    config["routing"] = {
        "domainMatcher": "hybrid",
        "domainStrategy": "AsIs",
        "rules": rules,
    }
    return config


def _build_base_client_config(profile: SubscriptionProfile) -> dict[str, Any]:
    return {
        "log": {"loglevel": "warning"},
        "dns": {
            "servers": ["1.1.1.1", "8.8.8.8", "localhost"],
            "queryStrategy": "UseIPv4",
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


def _build_single_main_config(
    user: SnapshotUser,
    profile: SubscriptionProfile,
    *,
    bridge_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = _build_base_client_config(profile)
    config["remarks"] = "Запасной #3 🇳🇱"
    primary_outbound = build_main_xray_outbound(user, profile, tag="proxy")
    fallback_outbound = build_fallback_xray_outbound(user, profile, tag="proxy-direct")
    legacy_outbound = build_legacy_xray_outbound(user, profile, tag="proxy-legacy")
    bridge_outbounds = _build_bridge_outbounds(bridge_profile, max_nodes=profile.main_bridge_max_nodes) if profile.main_bridge_enabled else []
    chained_outbounds = [
        _build_chained_main_outbound(user, profile, tag=f"proxy-bridge-{index:03d}", bridge_tag=str(bridge["tag"]))
        for index, bridge in enumerate(bridge_outbounds, start=1)
    ]

    if fallback_outbound is None and legacy_outbound is None and not chained_outbounds:
        config["outbounds"] = [
            primary_outbound,
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "block", "protocol": "blackhole"},
        ]
        final_rule = {"network": "tcp,udp", "outboundTag": "proxy"}
        balancers: list[dict[str, Any]] = []
    else:
        primary_outbound["tag"] = "proxy-cdn"
        proxy_selectors = ["proxy-cdn"]
        config["outbounds"] = [
            primary_outbound,
        ]
        if fallback_outbound is not None:
            config["outbounds"].append(fallback_outbound)
            proxy_selectors.append("proxy-direct")
        if legacy_outbound is not None:
            config["outbounds"].append(legacy_outbound)
            proxy_selectors.append("proxy-legacy")
        config["outbounds"].extend(chained_outbounds)
        proxy_selectors.extend(str(outbound["tag"]) for outbound in chained_outbounds)
        config["outbounds"].extend(bridge_outbounds)
        config["outbounds"].extend([
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "block", "protocol": "blackhole"},
        ])
        final_rule = {"network": "tcp,udp", "balancerTag": "proxy-auto"}
        balancers = [
            {
                "tag": "proxy-auto",
                "selector": proxy_selectors,
                "fallbackTag": "proxy-cdn",
                "strategy": {"type": "leastPing"},
            }
        ]
        config["observatory"] = {
            "subjectSelector": proxy_selectors,
            "probeUrl": "https://www.gstatic.com/generate_204",
            "probeInterval": "1m",
            "enableConcurrency": True,
        }

    config["routing"] = {
        "domainMatcher": "hybrid",
        "domainStrategy": "AsIs",
        "rules": [
            {"ip": [profile.vless_public_host, "geoip:private"], "outboundTag": "direct"},
            {"network": "udp", "port": "443", "outboundTag": "block"},
            {"protocol": ["bittorrent"], "outboundTag": "block"},
            final_rule,
        ],
    }
    if balancers:
        config["routing"]["balancers"] = balancers
    return config


def _build_blocked_config(profile: SubscriptionProfile) -> dict[str, Any]:
    config = _build_base_client_config(profile)
    config["outbounds"] = [
        {"tag": "direct", "protocol": "freedom"},
        {"tag": "block", "protocol": "blackhole"},
    ]
    config["routing"] = {
        "domainMatcher": "hybrid",
        "domainStrategy": "AsIs",
        "rules": [
            {"ip": ["geoip:private"], "outboundTag": "direct"},
            {"network": "tcp,udp", "outboundTag": "block"},
        ],
    }
    return config


def build_debug_direct_tcp_profile(user: SnapshotUser, profile: SubscriptionProfile) -> dict[str, Any]:
    outbound = build_main_xray_outbound(user, profile, tag="proxy")
    stream_settings = outbound.setdefault("streamSettings", {})
    if stream_settings.get("network") == "tcp":
        stream_settings["tcpSettings"] = {"acceptProxyProtocol": False}
    stream_settings["sockopt"] = {
        "tcpNoDelay": True,
        "tcpKeepAliveIdle": 60,
        "tcpKeepAliveInterval": 30,
    }
    return {
        "log": {"loglevel": "debug"},
        "dns": {
            "servers": ["1.1.1.1", "8.8.8.8", "localhost"],
            "queryStrategy": "UseIPv4",
        },
        "remarks": "kVPN DEBUG DIRECT TCP ONLY",
        "outbounds": [
            outbound,
            {
                "tag": "direct",
                "protocol": "freedom",
                "settings": {"domainStrategy": "UseIPv4"},
            },
            {"tag": "block", "protocol": "blackhole"},
        ],
        "routing": {
            "domainMatcher": "hybrid",
            "domainStrategy": "AsIs",
            "rules": [
                {"ip": [profile.vless_public_host, "geoip:private"], "outboundTag": "direct"},
                {"network": "udp", "port": "443", "outboundTag": "block"},
                {"protocol": ["bittorrent"], "outboundTag": "block"},
                {"network": "tcp", "outboundTag": "proxy"},
                {"network": "udp", "outboundTag": "proxy"},
            ],
        },
    }


def _normalize_whitelist_profile(whitelist_profile: dict[str, Any], profile: SubscriptionProfile) -> dict[str, Any]:
    config = deepcopy(whitelist_profile)
    config["remarks"] = "Обход белых списков"
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
        "domainStrategy": "AsIs",
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


def _is_ip_address(value: str) -> bool:
    try:
        ip_address(value)
    except ValueError:
        return False
    return True
