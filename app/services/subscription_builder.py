from __future__ import annotations

import base64
import json
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
        "support-url": profile.support_url,
        "profile-title": f"base64:{b64_text(profile.profile_title)}",
        "profile-update-interval": str(profile.update_interval_hours),
        "announce": f"base64:{b64_text(_build_announce(profile.announce_text, user))}",
        "subscription-userinfo": _build_userinfo(user, profile),
        "announce-url": profile.announce_url,
        "profile-web-page-url": https_url,
        "routing": b64_text(json.dumps(build_default_routing(), ensure_ascii=False, separators=(",", ":"))),
    }
    return SubscriptionResponse(body=b64_text(nodes_text), headers=headers, nodes=nodes)


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
