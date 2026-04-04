from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import User, UserStatus
from app.db.repositories import UserRepository
from app.services.user_service import UserService
from app.services.xray_service import XrayService
from app.utils.time import utc_now

logger = logging.getLogger(__name__)


class BillingService:
    def __init__(
        self,
        settings: Settings,
        session_maker: async_sessionmaker[AsyncSession],
        user_service: UserService,
        xray_service: XrayService,
    ) -> None:
        self.settings = settings
        self.session_maker = session_maker
        self.user_service = user_service
        self.xray_service = xray_service

    async def purchase_month(self, session: AsyncSession, user: User) -> tuple[bool, str]:
        month_price = Decimal(str(self.settings.month_price_rub))
        if user.status == UserStatus.banned:
            return False, "Покупка недоступна: ваш аккаунт заблокирован."
        if user.balance < month_price:
            return False, f"Недостаточно средств. Нужно минимум {month_price} ₽."

        user.balance -= month_price
        now = utc_now()
        base = user.expiration_date if user.expiration_date and user.expiration_date > now else now
        user.expiration_date = base + timedelta(days=30)
        user.warning_sent_at = None

        if not user.device_limit_blocked:
            await self.xray_service.enable_user(user.telegram_id, str(user.uuid))
            user.vpn_enabled = True

        await session.flush()
        return True, (
            f"✅ Оплата прошла успешно.\n"
            f"Списано: {month_price} ₽\n"
            f"Баланс: {user.balance} ₽"
        )

    async def run_auto_renew(self, bot: Bot) -> None:
        month_price = Decimal(str(self.settings.month_price_rub))
        now = utc_now()
        async with self.session_maker() as session:
            repo = UserRepository(session)
            users = await repo.users_for_auto_renew(now)

            for user in users:
                if user.status == UserStatus.banned:
                    await self._disable_if_needed(user)
                    continue

                if user.balance >= month_price:
                    user.balance -= month_price
                    user.expiration_date = now + timedelta(days=30)
                    user.warning_sent_at = None
                    if not user.device_limit_blocked:
                        await self.xray_service.enable_user(user.telegram_id, str(user.uuid))
                        user.vpn_enabled = True
                    await self._safe_send(
                        bot,
                        user.telegram_id,
                        (
                            f"✅ Автопродление выполнено.\n"
                            f"Списано: {month_price} ₽\n"
                            f"Новый срок: {user.expiration_date:%d.%m.%Y %H:%M}\n"
                            f"Баланс: {user.balance} ₽"
                        ),
                    )
                else:
                    if user.vpn_enabled:
                        await self._disable_if_needed(user)
                    await self._safe_send(
                        bot,
                        user.telegram_id,
                        "⛔ VPN отключен: на балансе недостаточно средств для продления.",
                    )

            await session.commit()

    async def send_expiration_warnings(self, bot: Bot) -> None:
        now = utc_now()
        async with self.session_maker() as session:
            repo = UserRepository(session)
            users = await repo.users_for_warning(now=now, warning_window_hours=24)
            for user in users:
                if user.warning_sent_at and (now - user.warning_sent_at) < timedelta(hours=20):
                    continue
                user.warning_sent_at = now
                await self._safe_send(
                    bot,
                    user.telegram_id,
                    "⚠️ До окончания VPN-доступа осталось меньше 24 часов. Пополните баланс заранее.",
                )
            await session.commit()

    async def reconcile_states(self) -> None:
        now = utc_now()
        async with self.session_maker() as session:
            result = await session.execute(select(User))
            users = list(result.scalars().all())
            for user in users:
                should_enable = (
                    user.status == UserStatus.active
                    and not user.device_limit_blocked
                    and user.expiration_date is not None
                    and user.expiration_date > now
                )
                if should_enable and not user.vpn_enabled:
                    await self.xray_service.enable_user(user.telegram_id, str(user.uuid))
                    user.vpn_enabled = True
                if not should_enable and user.vpn_enabled:
                    await self._disable_if_needed(user)
            await session.commit()

    async def _disable_if_needed(self, user: User) -> None:
        await self.xray_service.disable_user(user.telegram_id)
        user.vpn_enabled = False

    @staticmethod
    async def _safe_send(bot: Bot, telegram_id: int, text: str) -> None:
        try:
            await bot.send_message(telegram_id, text)
        except Exception:  # noqa: BLE001
            logger.debug("Cannot send billing message to %s", telegram_id)
