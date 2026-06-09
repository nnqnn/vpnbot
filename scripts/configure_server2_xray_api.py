#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def main() -> None:
    parser = argparse.ArgumentParser(description="Enable local Xray API/stats on server #2 without touching users.")
    parser.add_argument("--config", default="/usr/local/etc/xray/config.json")
    parser.add_argument("--api-port", type=int, default=10085)
    args = parser.parse_args()

    config_path = Path(args.config)
    data = json.loads(config_path.read_text(encoding="utf-8"))
    changed = ensure_xray_api(data, api_port=args.api_port)

    if not changed:
        print("xray api config already up to date")
        return

    backup_path = config_path.with_suffix(
        config_path.suffix + f".bak.{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    )
    shutil.copy2(config_path, backup_path)
    config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"updated {config_path}; backup={backup_path}")


def ensure_xray_api(data: dict[str, Any], *, api_port: int) -> bool:
    changed = False

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
