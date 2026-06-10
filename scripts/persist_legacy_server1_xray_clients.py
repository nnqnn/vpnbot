#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sqlalchemy import select

from app.config import get_settings
from app.db.models import User, UserStatus
from app.db.session import build_engine, build_session_maker
from app.utils.time import utc_now


def user_email(telegram_id: int) -> str:
    return f"user-{telegram_id}@vpn.local"


def is_managed_email(email: str | None) -> bool:
    return bool(email and email.startswith("user-") and email.endswith("@vpn.local"))


def build_client(user_uuid: str, email: str, flow: str) -> dict[str, Any]:
    client: dict[str, Any] = {"id": str(user_uuid), "email": email}
    if flow:
        client["flow"] = flow
    return client


async def active_users() -> dict[str, str]:
    settings = get_settings()
    engine = build_engine(settings)
    session_maker = build_session_maker(engine)
    now = utc_now()
    try:
        async with session_maker() as session:
            result = await session.execute(
                select(User.telegram_id, User.uuid).where(
                    User.status == UserStatus.active,
                    User.device_limit_blocked.is_(False),
                    User.expiration_date.is_not(None),
                    User.expiration_date > now,
                )
            )
            return {user_email(int(telegram_id)): str(user_uuid) for telegram_id, user_uuid in result.all()}
    finally:
        await engine.dispose()


def sync_config(path: Path, inbound_tag: str, expected: dict[str, str], flow: str, xray_bin: str) -> bool:
    data = json.loads(path.read_text(encoding="utf-8"))
    inbound = next((item for item in data.get("inbounds", []) if item.get("tag") == inbound_tag), None)
    if inbound is None:
        raise RuntimeError(f"Inbound tag was not found: {inbound_tag}")

    settings = inbound.setdefault("settings", {})
    clients = settings.setdefault("clients", [])
    non_managed = [
        client
        for client in clients
        if not (isinstance(client, dict) and is_managed_email(client.get("email")))
    ]
    expected_clients = [build_client(expected[email], email, flow) for email in sorted(expected)]
    updated_clients = non_managed + expected_clients
    if clients == updated_clients:
        return False

    settings["clients"] = updated_clients
    backup = path.with_suffix(path.suffix + f".bak.legacy-users.{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}")
    shutil.copy2(path, backup)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    result = subprocess.run(
        [xray_bin, "run", "-test", "-c", str(path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    if result.returncode != 0:
        shutil.copy2(backup, path)
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "xray config test failed")
    print(f"legacy config synced: expected={len(expected)} backup={backup}")
    return True


async def amain() -> None:
    parser = argparse.ArgumentParser(description="Persist active DB users into server1 legacy Xray inbound config.")
    parser.add_argument("--config", default="/usr/local/etc/xray/config.json")
    parser.add_argument("--inbound-tag", default="vless-reality-8443")
    parser.add_argument("--flow", default="xtls-rprx-vision")
    parser.add_argument("--xray-bin", default="xray")
    args = parser.parse_args()

    expected = await active_users()
    changed = sync_config(Path(args.config), args.inbound_tag, expected, args.flow, args.xray_bin)
    if not changed:
        print(f"legacy config already up to date: expected={len(expected)}")


if __name__ == "__main__":
    asyncio.run(amain())
