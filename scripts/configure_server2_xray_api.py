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
    parser.add_argument("--server-name", action="append", default=["www.cloudflare.com", "yandex.ru"])
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
        server_names=args.server_name,
        short_id=args.short_id,
        flow=args.flow,
        private_key=args.private_key,
    )

    if not changed:
        print("xray api config already up to date")
        return

    backup_path = config_path.with_suffix(
        config_path.suffix + f".bak.{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    )
    shutil.copy2(config_path, backup_path)
    config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"updated {config_path}; backup={backup_path}")


def ensure_xray_api(
    data: dict[str, Any],
    *,
    api_port: int,
    inbound_tag: str = "upstream-in",
    direct_port: int = 9443,
    server_names: list[str] | None = None,
    short_id: str = "a1b2c3d4e5f6a7b8",
    flow: str = "xtls-rprx-vision",
    private_key: str = "",
) -> bool:
    changed = False
    changed = ensure_direct_vless_reality_inbound(
        data,
        inbound_tag=inbound_tag,
        direct_port=direct_port,
        server_names=server_names or ["www.cloudflare.com", "yandex.ru"],
        short_id=short_id,
        flow=flow,
        private_key=private_key,
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


def ensure_direct_vless_reality_inbound(
    data: dict[str, Any],
    *,
    inbound_tag: str,
    direct_port: int,
    server_names: list[str],
    short_id: str,
    flow: str,
    private_key: str,
) -> bool:
    changed = False
    inbounds = data.setdefault("inbounds", [])
    inbound = _find_by_tag(inbounds, inbound_tag)
    if inbound is None:
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
                    "target": "yandex.ru:443",
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
    if not reality.get("target") and not reality.get("dest"):
        reality["target"] = "yandex.ru:443"
        changed = True

    existing_names = reality.get("serverNames")
    if not isinstance(existing_names, list):
        existing_names = []
    merged_names = sorted({str(name) for name in existing_names + server_names if str(name)})
    if reality.get("serverNames") != merged_names:
        reality["serverNames"] = merged_names
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
