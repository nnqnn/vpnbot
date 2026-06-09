#!/usr/bin/env python3
from __future__ import annotations

import asyncio

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
