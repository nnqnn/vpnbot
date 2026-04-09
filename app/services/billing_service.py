from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import SubscriptionChargeSource, User, UserStatus
from app.db.repositories import (
    ReferralRepository,
    ReferralYearRewardRepository,
    SubscriptionChargeRepository,
    UserRepository,
)
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

    async def purchase_month(self, session: AsyncSession, user: User, bot: Bot | None = None) -> tuple[bool, str]:
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

        await self._record_subscription_payment(session, user, source=SubscriptionChargeSource.manual)
        await self._maybe_apply_referral_year_reward(session, invited_user=user, bot=bot)

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
                    await self._record_subscription_payment(session, user, source=SubscriptionChargeSource.auto)
                    await self._maybe_apply_referral_year_reward(session, invited_user=user, bot=bot)
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

    async def sync_xray_runtime_state(self) -> tuple[int, int]:
        now = utc_now()
        async with self.session_maker() as session:
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
        await self.xray_service.sync_enabled_users(enabled_users, all_managed_telegram_ids=managed_ids)
        logger.info("Xray runtime sync done: enabled=%s managed=%s", len(enabled_users), len(managed_ids))
        return len(enabled_users), len(managed_ids)

    async def _record_subscription_payment(
        self,
        session: AsyncSession,
        user: User,
        source: SubscriptionChargeSource,
    ) -> None:
        await SubscriptionChargeRepository(session).create(user_id=user.id, source=source)

    async def _maybe_apply_referral_year_reward(
        self,
        session: AsyncSession,
        invited_user: User,
        bot: Bot | None,
    ) -> None:
        threshold = max(1, int(self.settings.referral_paid_invites_for_year_reward))
        reward_days = max(1, int(self.settings.referral_year_reward_days))

        referral_repo = ReferralRepository(session)
        referral = await referral_repo.get_by_invited(invited_user.id)
        if referral is None:
            return

        user_repo = UserRepository(session)
        inviter = await user_repo.get_by_id(referral.inviter_id)
        if inviter is None:
            return

        paid_referrals = await referral_repo.count_invited_with_subscription_payment(inviter.id)
        eligible_groups = paid_referrals // threshold
        if eligible_groups <= 0:
            return

        reward_repo = ReferralYearRewardRepository(session)
        reward_state = await reward_repo.ensure(inviter.id)
        rewarded_groups = int(reward_state.rewarded_groups or 0)
        pending_groups = eligible_groups - rewarded_groups
        if pending_groups <= 0:
            return

        total_reward_days = reward_days * pending_groups
        self.user_service.extend_user_days(inviter, total_reward_days)
        inviter.warning_sent_at = None

        if inviter.status == UserStatus.active and not inviter.device_limit_blocked:
            await self.xray_service.enable_user(inviter.telegram_id, str(inviter.uuid))
            inviter.vpn_enabled = True

        reward_state.rewarded_groups = rewarded_groups + pending_groups
        logger.info(
            "Applied referral year reward: inviter=%s paid_referrals=%s groups=%s days=%s",
            inviter.telegram_id,
            paid_referrals,
            pending_groups,
            total_reward_days,
        )

        if bot is not None:
            await self._safe_send(
                bot,
                inviter.telegram_id,
                (
                    "🎉 Поздравляем! Вы получили реферальный бонус.\n"
                    f"Оплативших приглашенных: {paid_referrals}\n"
                    f"Начислено: +{total_reward_days} дн. VPN."
                ),
            )

    async def _disable_if_needed(self, user: User) -> None:
        await self.xray_service.disable_user(user.telegram_id)
        user.vpn_enabled = False

    @staticmethod
    async def _safe_send(bot: Bot, telegram_id: int, text: str) -> None:
        try:
            await bot.send_message(telegram_id, text)
        except Exception:  # noqa: BLE001
            logger.debug("Cannot send billing message to %s", telegram_id)
