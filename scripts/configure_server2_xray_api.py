#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def main() -> None:
    parser = argparse.ArgumentParser(description="Enable server2 direct VLESS inbound and local Xray API.")
    parser.add_argument("--config", default="/usr/local/etc/xray/config.json")
    parser.add_argument("--api-port", type=int, default=10085)
    parser.add_argument("--inbound-tag", default="upstream-in")
    parser.add_argument("--direct-port", type=int, default=9443)
    parser.add_argument("--public-reality-inbound-tag", default="direct-reality-8443")
    parser.add_argument("--public-reality-port", type=int, default=443)
    parser.add_argument("--public-reality-server-name", action="append", default=None)
    parser.add_argument("--public-reality-dest", default="www.yandex.ru:443")
    parser.add_argument("--noflow-reality-inbound-tag", default="direct-reality-noflow-8443")
    parser.add_argument("--noflow-reality-port", type=int, default=8443)
    parser.add_argument("--noflow-reality-server-name", action="append", default=None)
    parser.add_argument("--noflow-reality-dest", default="www.yandex.ru:443")
    parser.add_argument("--cdn-ws-inbound-tag", default="cdn-ws-in")
    parser.add_argument("--cdn-ws-port", type=int, default=10086)
    parser.add_argument("--cdn-ws-path", default="/kvpn-ws")
    parser.add_argument("--xhttp-inbound-tag", default="xhttp-in")
    parser.add_argument("--xhttp-port", type=int, default=10087)
    parser.add_argument("--xhttp-path", default="/kvpn-xhttp")
    parser.add_argument("--xhttp-mode", default="packet-up")
    parser.add_argument("--hysteria2-inbound-tag", default="hysteria2-udp-443")
    parser.add_argument("--hysteria2-port", type=int, default=443)
    parser.add_argument("--hysteria2-cert-file", default="/etc/letsencrypt/live/s2.nnqnn.tech/fullchain.pem")
    parser.add_argument("--hysteria2-key-file", default="/etc/letsencrypt/live/s2.nnqnn.tech/privkey.pem")
    parser.add_argument("--hysteria2-masquerade-url", default="https://www.yandex.ru/")
    parser.add_argument("--server-name", action="append", default=["www.cloudflare.com", "www.yandex.ru", "yandex.ru"])
    parser.add_argument("--short-id", default="a1b2c3d4e5f6a7b8")
    parser.add_argument("--flow", default="xtls-rprx-vision")
    parser.add_argument("--private-key", default="")
    args = parser.parse_args()

    config_path = Path(args.config)
    data = json.loads(config_path.read_text(encoding="utf-8"))
    changed = ensure_xray_api(
        data,
        api_port=args.api_port,
        inbound_tag=args.inbound_tag,
        direct_port=args.direct_port,
        public_reality_inbound_tag=args.public_reality_inbound_tag,
        public_reality_port=args.public_reality_port,
        public_reality_server_names=args.public_reality_server_name,
        public_reality_dest=args.public_reality_dest,
        noflow_reality_inbound_tag=args.noflow_reality_inbound_tag,
        noflow_reality_port=args.noflow_reality_port,
        noflow_reality_server_names=args.noflow_reality_server_name,
        noflow_reality_dest=args.noflow_reality_dest,
        cdn_ws_inbound_tag=args.cdn_ws_inbound_tag,
        cdn_ws_port=args.cdn_ws_port,
        cdn_ws_path=args.cdn_ws_path,
        xhttp_inbound_tag=args.xhttp_inbound_tag,
        xhttp_port=args.xhttp_port,
        xhttp_path=args.xhttp_path,
        xhttp_mode=args.xhttp_mode,
        hysteria2_inbound_tag=args.hysteria2_inbound_tag,
        hysteria2_port=args.hysteria2_port,
        hysteria2_cert_file=args.hysteria2_cert_file,
        hysteria2_key_file=args.hysteria2_key_file,
        hysteria2_masquerade_url=args.hysteria2_masquerade_url,
        server_names=args.server_name,
        short_id=args.short_id,
        flow=args.flow,
        private_key=args.private_key,
    )

    if not changed:
        config_path.chmod(0o644)
        print("xray api config already up to date")
        return

    backup_path = config_path.with_suffix(
        config_path.suffix + f".bak.{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    )
    shutil.copy2(config_path, backup_path)
    config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    config_path.chmod(0o644)
    print(f"updated {config_path}; backup={backup_path}")


def ensure_xray_api(
    data: dict[str, Any],
    *,
    api_port: int,
    inbound_tag: str = "upstream-in",
    direct_port: int = 9443,
    public_reality_inbound_tag: str = "direct-reality-8443",
    public_reality_port: int = 443,
    public_reality_server_names: list[str] | None = None,
    public_reality_dest: str = "www.yandex.ru:443",
    noflow_reality_inbound_tag: str = "direct-reality-noflow-8443",
    noflow_reality_port: int = 8443,
    noflow_reality_server_names: list[str] | None = None,
    noflow_reality_dest: str = "www.yandex.ru:443",
    cdn_ws_inbound_tag: str = "cdn-ws-in",
    cdn_ws_port: int = 10086,
    cdn_ws_path: str = "/kvpn-ws",
    xhttp_inbound_tag: str = "xhttp-in",
    xhttp_port: int = 10087,
    xhttp_path: str = "/kvpn-xhttp",
    xhttp_mode: str = "packet-up",
    hysteria2_inbound_tag: str = "hysteria2-udp-443",
    hysteria2_port: int = 443,
    hysteria2_cert_file: str = "/etc/letsencrypt/live/s2.nnqnn.tech/fullchain.pem",
    hysteria2_key_file: str = "/etc/letsencrypt/live/s2.nnqnn.tech/privkey.pem",
    hysteria2_masquerade_url: str = "https://www.yandex.ru/",
    server_names: list[str] | None = None,
    short_id: str = "a1b2c3d4e5f6a7b8",
    flow: str = "xtls-rprx-vision",
    private_key: str = "",
) -> bool:
    changed = False
    changed = ensure_ipv4_only_egress(data) or changed
    changed = remove_conflicting_public_migrate_inbound(data, keep_tag=inbound_tag) or changed
    changed = ensure_direct_vless_reality_inbound(
        data,
        inbound_tag=inbound_tag,
        direct_port=direct_port,
        server_names=server_names or ["www.cloudflare.com", "www.yandex.ru", "yandex.ru"],
        short_id=short_id,
        flow=flow,
        private_key=private_key,
    ) or changed
    changed = ensure_direct_vless_reality_inbound(
        data,
        inbound_tag=public_reality_inbound_tag,
        direct_port=public_reality_port,
        server_names=public_reality_server_names or ["www.yandex.ru", "yandex.ru"],
        short_id=short_id,
        flow=flow,
        private_key=private_key,
        dest=public_reality_dest,
        replace_server_names=True,
    ) or changed
    changed = ensure_direct_vless_reality_inbound(
        data,
        inbound_tag=noflow_reality_inbound_tag,
        direct_port=noflow_reality_port,
        server_names=noflow_reality_server_names or ["www.yandex.ru", "yandex.ru"],
        short_id=short_id,
        flow="",
        private_key=private_key,
        dest=noflow_reality_dest,
        replace_server_names=True,
    ) or changed
    changed = ensure_cdn_vless_ws_inbound(
        data,
        source_inbound_tag=inbound_tag,
        inbound_tag=cdn_ws_inbound_tag,
        port=cdn_ws_port,
        path=cdn_ws_path,
    ) or changed
    changed = ensure_vless_xhttp_inbound(
        data,
        source_inbound_tag=inbound_tag,
        inbound_tag=xhttp_inbound_tag,
        port=xhttp_port,
        path=xhttp_path,
        mode=xhttp_mode,
    ) or changed
    changed = ensure_hysteria2_inbound(
        data,
        inbound_tag=hysteria2_inbound_tag,
        port=hysteria2_port,
        cert_file=hysteria2_cert_file,
        key_file=hysteria2_key_file,
        masquerade_url=hysteria2_masquerade_url,
    ) or changed

    api = data.setdefault("api", {})
    if api.get("tag") != "api":
        api["tag"] = "api"
        changed = True
    services = set(api.get("services") or [])
    required_services = {"HandlerService", "StatsService"}
    if not required_services.issubset(services):
        api["services"] = sorted(services | required_services)
        changed = True

    inbounds = data.setdefault("inbounds", [])
    api_inbound = _find_by_tag(inbounds, "api")
    expected_api_inbound = {
        "tag": "api",
        "listen": "127.0.0.1",
        "port": api_port,
        "protocol": "dokodemo-door",
        "settings": {"address": "127.0.0.1"},
    }
    if api_inbound is None:
        inbounds.append(expected_api_inbound)
        changed = True
    elif _merge_dict(api_inbound, expected_api_inbound):
        changed = True

    routing = data.setdefault("routing", {})
    rules = routing.setdefault("rules", [])
    api_rule = {"type": "field", "inboundTag": ["api"], "outboundTag": "api"}
    if not any(rule.get("inboundTag") == ["api"] and rule.get("outboundTag") == "api" for rule in rules):
        rules.insert(0, api_rule)
        changed = True
    vpn_inbound_tags = sorted(
        {
            inbound_tag,
            public_reality_inbound_tag,
            noflow_reality_inbound_tag,
            cdn_ws_inbound_tag,
            xhttp_inbound_tag,
            hysteria2_inbound_tag,
        }
    )
    vpn_direct_rule = {
        "type": "field",
        "inboundTag": vpn_inbound_tags,
        "network": "tcp,udp",
        "outboundTag": "direct",
    }
    existing_vpn_rule = next(
        (
            rule
            for rule in rules
            if isinstance(rule, dict)
            and rule.get("outboundTag") == "direct"
            and rule.get("network") == "tcp,udp"
            and any(tag in set(rule.get("inboundTag") or []) for tag in vpn_inbound_tags)
        ),
        None,
    )
    if existing_vpn_rule is None:
        rules.append(vpn_direct_rule)
        changed = True
    elif existing_vpn_rule != vpn_direct_rule:
        existing_vpn_rule.clear()
        existing_vpn_rule.update(vpn_direct_rule)
        changed = True

    stats = data.setdefault("stats", {})
    if stats != {}:
        data["stats"] = {}
        changed = True

    policy = data.setdefault("policy", {})
    levels = policy.setdefault("levels", {})
    level0 = levels.setdefault("0", {})
    for key in ("statsUserUplink", "statsUserDownlink", "statsUserOnline"):
        if level0.get(key) is not True:
            level0[key] = True
            changed = True

    return changed


def ensure_ipv4_only_egress(data: dict[str, Any]) -> bool:
    changed = False

    dns = data.setdefault("dns", {})
    if dns.get("queryStrategy") != "UseIPv4":
        dns["queryStrategy"] = "UseIPv4"
        changed = True
    servers = dns.setdefault("servers", ["localhost"])
    if servers != ["localhost"]:
        dns["servers"] = ["localhost"]
        changed = True

    routing = data.setdefault("routing", {})
    if routing.get("domainStrategy") != "AsIs":
        routing["domainStrategy"] = "AsIs"
        changed = True

    outbounds = data.setdefault("outbounds", [])
    direct = _find_by_tag(outbounds, "direct")
    expected_direct = {
        "protocol": "freedom",
        "tag": "direct",
        "sendThrough": "0.0.0.0",
        "settings": {
            "domainStrategy": "UseIPv4",
        },
    }
    if direct is None:
        outbounds.insert(0, expected_direct)
        changed = True
    elif _merge_dict(direct, expected_direct):
        changed = True

    block = _find_by_tag(outbounds, "block")
    expected_block = {"protocol": "blackhole", "tag": "block"}
    if block is None:
        outbounds.append(expected_block)
        changed = True
    elif _merge_dict(block, expected_block):
        changed = True

    return changed


def ensure_cdn_vless_ws_inbound(
    data: dict[str, Any],
    *,
    source_inbound_tag: str,
    inbound_tag: str,
    port: int,
    path: str,
) -> bool:
    changed = False
    inbounds = data.setdefault("inbounds", [])
    inbound = _find_by_tag(inbounds, inbound_tag)
    source = _find_by_tag(inbounds, source_inbound_tag)
    source_clients = []
    if source is not None:
        source_clients = source.get("settings", {}).get("clients", [])
        if not isinstance(source_clients, list):
            source_clients = []

    expected = {
        "tag": inbound_tag,
        "listen": "127.0.0.1",
        "port": port,
        "protocol": "vless",
        "settings": {
            "decryption": "none",
            "clients": [
                _client_without_flow(client)
                for client in source_clients
                if isinstance(client, dict)
            ],
        },
        "streamSettings": {
            "network": "ws",
            "security": "none",
            "wsSettings": {
                "path": path,
            },
        },
    }

    if inbound is None:
        inbounds.append(expected)
        return True

    for key in ("tag", "listen", "port", "protocol"):
        if inbound.get(key) != expected[key]:
            inbound[key] = expected[key]
            changed = True

    settings = inbound.setdefault("settings", {})
    if settings.get("decryption") != "none":
        settings["decryption"] = "none"
        changed = True
    clients = settings.setdefault("clients", [])
    if not isinstance(clients, list):
        settings["clients"] = []
        changed = True
    else:
        for client in clients:
            if isinstance(client, dict) and "flow" in client:
                client.pop("flow", None)
                changed = True

    stream = inbound.setdefault("streamSettings", {})
    expected_stream = expected["streamSettings"]
    if _merge_dict(stream, expected_stream):
        changed = True
    return changed


def ensure_vless_xhttp_inbound(
    data: dict[str, Any],
    *,
    source_inbound_tag: str,
    inbound_tag: str,
    port: int,
    path: str,
    mode: str,
) -> bool:
    changed = False
    inbounds = data.setdefault("inbounds", [])
    inbound = _find_by_tag(inbounds, inbound_tag)
    source = _find_by_tag(inbounds, source_inbound_tag)
    source_clients = []
    if source is not None:
        source_clients = source.get("settings", {}).get("clients", [])
        if not isinstance(source_clients, list):
            source_clients = []

    expected = {
        "tag": inbound_tag,
        "listen": "127.0.0.1",
        "port": port,
        "protocol": "vless",
        "settings": {
            "decryption": "none",
            "clients": [
                _client_without_flow(client)
                for client in source_clients
                if isinstance(client, dict)
            ],
        },
        "streamSettings": {
            "network": "xhttp",
            "security": "none",
            "xhttpSettings": {
                "path": path,
                "mode": mode,
            },
        },
    }

    if inbound is None:
        inbounds.append(expected)
        return True

    for key in ("tag", "listen", "port", "protocol"):
        if inbound.get(key) != expected[key]:
            inbound[key] = expected[key]
            changed = True

    settings = inbound.setdefault("settings", {})
    if settings.get("decryption") != "none":
        settings["decryption"] = "none"
        changed = True
    clients = settings.setdefault("clients", [])
    if not isinstance(clients, list):
        settings["clients"] = []
        changed = True
    else:
        for client in clients:
            if isinstance(client, dict) and "flow" in client:
                client.pop("flow", None)
                changed = True

    stream = inbound.setdefault("streamSettings", {})
    expected_stream = expected["streamSettings"]
    if _merge_dict(stream, expected_stream):
        changed = True
    return changed


def ensure_hysteria2_inbound(
    data: dict[str, Any],
    *,
    inbound_tag: str,
    port: int,
    cert_file: str,
    key_file: str,
    masquerade_url: str,
) -> bool:
    changed = False
    inbounds = data.setdefault("inbounds", [])
    inbound = _find_by_tag(inbounds, inbound_tag)
    expected = {
        "tag": inbound_tag,
        "listen": "0.0.0.0",
        "port": port,
        "protocol": "hysteria",
        "settings": {
            "version": 2,
            "users": [],
        },
        "streamSettings": {
            "network": "hysteria",
            "security": "tls",
            "tlsSettings": {
                "certificates": [
                    {
                        "certificateFile": cert_file,
                        "keyFile": key_file,
                    }
                ]
            },
            "hysteriaSettings": {
                "version": 2,
                "udpIdleTimeout": 60,
                "masquerade": {
                    "type": "proxy",
                    "url": masquerade_url,
                    "rewriteHost": True,
                },
            },
        },
    }

    if inbound is None:
        inbounds.append(expected)
        return True

    for key in ("tag", "listen", "port", "protocol"):
        if inbound.get(key) != expected[key]:
            inbound[key] = expected[key]
            changed = True

    settings = inbound.setdefault("settings", {})
    if settings.get("version") != 2:
        settings["version"] = 2
        changed = True
    users = settings.setdefault("users", [])
    if not isinstance(users, list):
        settings["users"] = []
        changed = True
    if "clients" in settings:
        settings.pop("clients", None)
        changed = True

    stream = inbound.setdefault("streamSettings", {})
    expected_stream = expected["streamSettings"]
    if _merge_dict(stream, expected_stream):
        changed = True
    return changed


def _client_without_flow(client: dict[str, Any]) -> dict[str, Any]:
    data = dict(client)
    data.pop("flow", None)
    return data


def remove_conflicting_public_migrate_inbound(data: dict[str, Any], *, keep_tag: str) -> bool:
    inbounds = data.setdefault("inbounds", [])
    if not isinstance(inbounds, list):
        return False
    updated = [
        inbound
        for inbound in inbounds
        if not (
            isinstance(inbound, dict)
            and inbound.get("tag") == "public-migrate-443"
            and inbound.get("tag") != keep_tag
            and int(inbound.get("port") or 0) == 443
        )
    ]
    if len(updated) == len(inbounds):
        return False
    data["inbounds"] = updated
    return True


def ensure_direct_vless_reality_inbound(
    data: dict[str, Any],
    *,
    inbound_tag: str,
    direct_port: int,
    server_names: list[str],
    short_id: str,
    flow: str,
    private_key: str,
    dest: str | None = None,
    replace_server_names: bool = False,
) -> bool:
    changed = False
    inbounds = data.setdefault("inbounds", [])
    inbound = _find_by_tag(inbounds, inbound_tag)
    if inbound is None:
        private_key = private_key or _first_reality_private_key(inbounds)
        if not private_key:
            raise ValueError(
                f"Inbound '{inbound_tag}' does not exist and --private-key was not provided; "
                "cannot create VLESS Reality inbound safely."
            )
        inbound = {
            "tag": inbound_tag,
            "listen": "0.0.0.0",
            "port": direct_port,
            "protocol": "vless",
            "settings": {
                "decryption": "none",
                "clients": [],
            },
            "streamSettings": {
                "network": "tcp",
                "security": "reality",
                "tcpSettings": {},
                "realitySettings": {
                    "show": False,
                    "dest": dest or "www.yandex.ru:443",
                    "serverNames": sorted(set(server_names)),
                    "privateKey": private_key,
                    "shortIds": [short_id],
                },
            },
        }
        inbounds.append(inbound)
        return True

    expected_top = {"tag": inbound_tag, "port": direct_port, "protocol": "vless"}
    if _merge_dict(inbound, expected_top):
        changed = True

    settings = inbound.setdefault("settings", {})
    if settings.get("decryption") != "none":
        settings["decryption"] = "none"
        changed = True
    clients = settings.setdefault("clients", [])
    if not isinstance(clients, list):
        settings["clients"] = []
        changed = True

    stream = inbound.setdefault("streamSettings", {})
    expected_stream = {"network": "tcp", "security": "reality", "tcpSettings": {}}
    if _merge_dict(stream, expected_stream):
        changed = True

    reality = stream.setdefault("realitySettings", {})
    if reality.get("show") is not False:
        reality["show"] = False
        changed = True
    if "privateKey" not in reality or not reality.get("privateKey"):
        if not private_key:
            raise ValueError(f"Inbound '{inbound_tag}' has no REALITY privateKey and --private-key was not provided.")
        reality["privateKey"] = private_key
        changed = True
    if dest:
        if reality.get("dest") != dest:
            reality["dest"] = dest
            changed = True
        if "target" in reality:
            reality.pop("target", None)
            changed = True
    elif not reality.get("target") and not reality.get("dest"):
        reality["dest"] = "www.yandex.ru:443"
        changed = True

    existing_names = reality.get("serverNames")
    if not isinstance(existing_names, list):
        existing_names = []
    next_names = (
        sorted({str(name) for name in server_names if str(name)})
        if replace_server_names
        else sorted({str(name) for name in existing_names + server_names if str(name)})
    )
    if reality.get("serverNames") != next_names:
        reality["serverNames"] = next_names
        changed = True

    existing_short_ids = reality.get("shortIds")
    if not isinstance(existing_short_ids, list):
        existing_short_ids = []
    if short_id and short_id not in existing_short_ids:
        reality["shortIds"] = existing_short_ids + [short_id]
        changed = True

    for client in settings.get("clients", []):
        if isinstance(client, dict) and client.get("email", "").startswith("user-") and flow:
            if client.get("flow") != flow:
                client["flow"] = flow
                changed = True

    return changed


def _find_by_tag(items: list[dict[str, Any]], tag: str) -> dict[str, Any] | None:
    for item in items:
        if item.get("tag") == tag:
            return item
    return None


def _first_reality_private_key(inbounds: list[dict[str, Any]]) -> str:
    for inbound in inbounds:
        if not isinstance(inbound, dict):
            continue
        stream = inbound.get("streamSettings")
        if not isinstance(stream, dict) or stream.get("security") != "reality":
            continue
        reality = stream.get("realitySettings")
        if not isinstance(reality, dict):
            continue
        private_key = reality.get("privateKey")
        if isinstance(private_key, str) and private_key:
            return private_key
    return ""


def _merge_dict(target: dict[str, Any], expected: dict[str, Any]) -> bool:
    changed = False
    for key, value in expected.items():
        if isinstance(value, dict):
            child = target.setdefault(key, {})
            if _merge_dict(child, value):
                changed = True
        elif target.get(key) != value:
            target[key] = value
            changed = True
    return changed


if __name__ == "__main__":
    main()
