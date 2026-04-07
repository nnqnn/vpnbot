from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

# Allow direct execution: `python3 scripts/resync_xray_runtime.py`
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings
from app.db.models import User, UserStatus
from app.db.session import build_engine, build_session_maker
from app.services.xray_service import XrayService
from app.utils.time import utc_now


async def _collect_users(session_maker) -> tuple[list[int], list[tuple[int, str]]]:
    now = utc_now()
    async with session_maker() as session:
        result = await session.execute(select(User))
        users = list(result.scalars().all())

    managed_ids = [int(user.telegram_id) for user in users]
    enabled_users = [
        (int(user.telegram_id), str(user.uuid))
        for user in users
        if (
            user.status == UserStatus.active
            and not user.device_limit_blocked
            and user.expiration_date is not None
            and user.expiration_date > now
        )
    ]
    return managed_ids, enabled_users


async def _run(dry_run: bool, rebuild: bool) -> None:
    settings = get_settings()
    if settings.xray_control_mode.strip().lower() != "api":
        raise SystemExit("This script is intended for XRAY_CONTROL_MODE=api.")

    engine = build_engine(settings)
    session_maker = build_session_maker(engine)
    xray_service = XrayService(settings)

    try:
        managed_ids, enabled_users = await _collect_users(session_maker)
        print(f"Managed users in DB: {len(managed_ids)}")
        print(f"Enabled users to restore: {len(enabled_users)}")

        if dry_run:
            print("Dry run: no changes applied.")
            return

        if rebuild:
            await xray_service.sync_enabled_users([], all_managed_telegram_ids=managed_ids)
            print("Step 1/2: removed managed users from Xray runtime.")
            await xray_service.sync_enabled_users(enabled_users, all_managed_telegram_ids=managed_ids)
            print("Step 2/2: restored enabled users to Xray runtime.")
        else:
            await xray_service.sync_enabled_users(enabled_users, all_managed_telegram_ids=managed_ids)
            print("Upsert mode: synced enabled users into Xray runtime.")
        print("Done.")
    finally:
        await engine.dispose()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rebuild Xray runtime users from DB without restarting Xray.")
    parser.add_argument("--dry-run", action="store_true", help="Only print planned counts.")
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Hard mode: remove all managed users first, then add enabled users.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(_run(dry_run=args.dry_run, rebuild=args.rebuild))
