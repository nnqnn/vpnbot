from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from sqlalchemy import select

from app.bot.handlers import register_handlers
from app.bot.middlewares import DbSessionMiddleware
from app.config import get_settings
from app.db.models import User, UserStatus
from app.db.session import build_engine, build_session_maker, init_db
from app.logging_config import setup_logging
from app.services.admin_service import AdminService
from app.services.billing_service import BillingService
from app.services.device_limit_service import DeviceLimitService
from app.services.payment_service import TelegaPayService
from app.services.scheduler_service import SchedulerService
from app.services.user_service import UserService
from app.services.xray_service import XrayService
from app.utils.time import utc_now

logger = logging.getLogger(__name__)


async def _sync_xray_state(session_maker, xray_service: XrayService) -> None:
    now = utc_now()
    async with session_maker() as session:
        enabled_result = await session.execute(
            select(User).where(
                User.status == UserStatus.active,
                User.device_limit_blocked.is_(False),
                User.expiration_date.is_not(None),
                User.expiration_date > now,
            )
        )
        users = list(enabled_result.scalars().all())

        managed_result = await session.execute(select(User.telegram_id))
        managed_ids = [int(tg_id) for (tg_id,) in managed_result.all()]

    payload = [(int(user.telegram_id), str(user.uuid)) for user in users]
    await xray_service.sync_enabled_users(payload, all_managed_telegram_ids=managed_ids)
    logger.info("Xray synced users=%s", len(payload))


async def main() -> None:
    settings = get_settings()
    setup_logging(settings)

    engine = build_engine(settings)
    session_maker = build_session_maker(engine)
    await init_db(engine)

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.update.middleware(DbSessionMiddleware(session_maker))
    register_handlers(dp)

    xray_service = XrayService(settings)
    user_service = UserService(settings, xray_service)
    billing_service = BillingService(settings, session_maker, user_service, xray_service)
    payment_service = TelegaPayService(settings, session_maker)
    admin_service = AdminService(xray_service)
    device_limit_service = DeviceLimitService(settings, session_maker, xray_service)

    await _sync_xray_state(session_maker, xray_service)
    await billing_service.reconcile_states()

    scheduler = SchedulerService(
        settings=settings,
        bot=bot,
        billing_service=billing_service,
        payment_service=payment_service,
        device_limit_service=device_limit_service,
    )
    scheduler.start()

    logger.info("Bot is starting polling")
    try:
        await dp.start_polling(
            bot,
            settings=settings,
            user_service=user_service,
            billing_service=billing_service,
            payment_service=payment_service,
            xray_service=xray_service,
            admin_service=admin_service,
        )
    finally:
        await scheduler.shutdown()
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
