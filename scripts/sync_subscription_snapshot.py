#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Allow direct execution: `python3 scripts/sync_subscription_snapshot.py`
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings
from app.db.session import build_engine, build_session_maker, init_db
from app.services.subscription_sync_service import SubscriptionSnapshotService


async def main() -> None:
    settings = get_settings()
    engine = build_engine(settings)
    session_maker = build_session_maker(engine)
    try:
        await init_db(engine)
        snapshot = await SubscriptionSnapshotService(settings, session_maker).sync_once()
        print(f"subscription snapshot synced: users={len(snapshot.get('users', {}))}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
