from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings
from app.db.models import User, UserStatus
from app.db.session import build_engine, build_session_maker
from app.utils.time import utc_now


async def _run() -> None:
    settings = get_settings()
    engine = build_engine(settings)
    session_maker = build_session_maker(engine)
    now = utc_now()
    changed = 0
    try:
        async with session_maker() as session:
            result = await session.execute(
                select(User).where(
                    User.status == UserStatus.active,
                    User.device_limit_blocked.is_(True),
                    User.expiration_date.is_not(None),
                    User.expiration_date > now,
                )
            )
            users = list(result.scalars().all())
            for user in users:
                user.device_limit_blocked = False
                user.vpn_enabled = True
                changed += 1
            await session.commit()
        print(f"Cleared device-limit blocks: {changed}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_run())
