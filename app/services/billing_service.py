from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_UP, Decimal
from typing import TYPE_CHECKING

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import PaymentStatus, SubscriptionChargeSource, User, UserStatus
from app.db.repositories import (
    DeferredTariffPurchaseRepository,
    PaymentRepository,
    ReferralRepository,
    ReferralYearRewardRepository,
    SubscriptionChargeRepository,
    UserRepository,
)
from app.services.user_service import UserService
from app.services.xray_service import XrayService
from app.utils.time import utc_now

if TYPE_CHECKING:
    from app.services.payment_service import TelegaPayService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TariffPlan:
    code: str
    title: str
    price: Decimal
    days: int
    kind: str = "subscription"


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

    def list_tariffs(self) -> tuple[TariffPlan, ...]:
        return (
            TariffPlan(code="1m", title="1 месяц", price=Decimal("100"), days=30, kind="subscription"),
            TariffPlan(code="3m", title="3 месяца", price=Decimal("270"), days=90, kind="subscription"),
            TariffPlan(code="12m", title="12 месяцев", price=Decimal("990"), days=365, kind="subscription"),
            TariffPlan(
                code="wl_guide",
                title="Инструкция по обходу белых списков",
                price=Decimal("120"),
                days=0,
                kind="instruction",
            ),
        )

    def get_tariff(self, code: str) -> TariffPlan | None:
        for tariff in self.list_tariffs():
            if tariff.code == code:
                return tariff
        return None

    def build_tariffs_text(self) -> str:
        return (
            "💳 Выберите тариф/продукт:\n\n"
            "• 1 месяц — 100 ₽\n"
            "• 3 месяца — 270 ₽\n"
            "• 12 месяцев — 990 ₽ (-20%)\n"
            "• Инструкция по обходу белых списков — 120 ₽"
        )

    async def purchase_tariff(
        self,
        session: AsyncSession,
        user: User,
        tariff_code: str,
        payment_service: TelegaPayService,
        bot: Bot | None = None,
    ) -> tuple[str, str, str | None]:
        tariff = self.get_tariff(tariff_code)
        if tariff is None:
            return "error", "Неизвестный тариф.", None
        if user.status == UserStatus.banned:
            return "error", "Покупка недоступна: ваш аккаунт заблокирован.", None

        if user.balance >= tariff.price:
            if tariff.kind == "instruction":
                await self._apply_instruction_purchase(user=user, price=tariff.price)
                await session.flush()
                return "applied", self._instruction_delivery_text(user.balance), None
            await self._charge_subscription(
                session=session,
                user=user,
                price=tariff.price,
                days=tariff.days,
                source=SubscriptionChargeSource.manual,
                bot=bot,
            )
            await session.flush()
            return (
                "applied",
                (
                    f"✅ Тариф активирован: {tariff.title}\n"
                    f"Списано: {tariff.price} ₽\n"
                    f"Срок продлен на {tariff.days} дней\n"
                    f"Баланс: {user.balance} ₽"
                ),
                None,
            )

        shortage = tariff.price - user.balance
        payment_amount = self._payment_amount_for_shortage(shortage)
        payment, payment_url = await payment_service.create_payment(user=user, amount_rub=payment_amount)
        await DeferredTariffPurchaseRepository(session).create(
            user_id=user.id,
            payment_id=payment.id,
            tariff_code=tariff.code,
            tariff_price=tariff.price,
            tariff_days=tariff.days,
        )
        await session.flush()

        reserve = Decimal(str(payment_amount)) - shortage
        reserve_note = ""
        if reserve > Decimal("0"):
            reserve_note = f"\nПосле оплаты {reserve} ₽ останется на балансе."

        return (
            "payment_required",
            (
                f"Сумма платежа: {payment_amount} ₽{reserve_note}\n\n"
                "После успешной оплаты продукт активируется автоматически."
            ),
            payment_url,
        )

    async def purchase_month(self, session: AsyncSession, user: User, bot: Bot | None = None) -> tuple[bool, str]:
        month_price = Decimal(str(self.settings.month_price_rub))
        if user.status == UserStatus.banned:
            return False, "Покупка недоступна: ваш аккаунт заблокирован."
        if user.balance < month_price:
            return False, f"Недостаточно средств. Нужно минимум {month_price} ₽."

        await self._charge_subscription(
            session=session,
            user=user,
            price=month_price,
            days=30,
            source=SubscriptionChargeSource.manual,
            bot=bot,
        )
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
                    await self._charge_subscription(
                        session=session,
                        user=user,
                        price=month_price,
                        days=30,
                        source=SubscriptionChargeSource.auto,
                        bot=bot,
                    )
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
                if not user.expiration_date:
                    continue

                remaining = user.expiration_date - now
                if remaining <= timedelta(hours=6):
                    six_hour_cutoff = user.expiration_date - timedelta(hours=8)
                    if user.warning_sent_at and user.warning_sent_at >= six_hour_cutoff:
                        continue
                    text = (
                        "⏰ До окончания VPN-доступа осталось меньше 6 часов.\n"
                        "Откройте раздел «Купить VPN», чтобы продлить доступ без отключения."
                    )
                else:
                    if user.warning_sent_at is not None:
                        continue
                    text = (
                        "⚠️ До окончания VPN-доступа осталось меньше 24 часов.\n"
                        "Откройте раздел «Купить VPN», чтобы продлить доступ заранее."
                    )

                user.warning_sent_at = now
                await self._safe_send(bot, user.telegram_id, text)
            await session.commit()

    async def process_deferred_tariff_purchases(self, bot: Bot) -> tuple[int, int]:
        now = utc_now()
        applied = 0
        cancelled = 0
        async with self.session_maker() as session:
            deferred_repo = DeferredTariffPurchaseRepository(session)
            payment_repo = PaymentRepository(session)
            user_repo = UserRepository(session)
            pending = await deferred_repo.list_pending(limit=200)
            for purchase in pending:
                payment = await payment_repo.get_by_id(purchase.payment_id)
                if payment is None or payment.status in {PaymentStatus.cancelled, PaymentStatus.failed}:
                    purchase.cancelled_at = now
                    cancelled += 1
                    continue
                if payment.status != PaymentStatus.paid:
                    continue

                user = await user_repo.get_by_id(purchase.user_id)
                if user is None:
                    purchase.cancelled_at = now
                    cancelled += 1
                    continue
                if user.status == UserStatus.banned:
                    purchase.cancelled_at = now
                    cancelled += 1
                    await self._safe_send(
                        bot,
                        user.telegram_id,
                        "⚠️ Платеж зачислен в баланс, но тариф не активирован: аккаунт заблокирован.",
                    )
                    continue
                if user.balance < purchase.tariff_price:
                    logger.warning(
                        "Deferred tariff purchase is underfunded: user=%s payment_id=%s required=%s balance=%s",
                        user.telegram_id,
                        purchase.payment_id,
                        purchase.tariff_price,
                        user.balance,
                    )
                    continue

                tariff = self.get_tariff(purchase.tariff_code)
                if tariff and tariff.kind == "instruction":
                    await self._apply_instruction_purchase(user=user, price=purchase.tariff_price)
                    purchase.applied_at = now
                    applied += 1
                    await self._safe_send(
                        bot,
                        user.telegram_id,
                        self._instruction_delivery_text(user.balance),
                    )
                    continue

                await self._charge_subscription(
                    session=session,
                    user=user,
                    price=purchase.tariff_price,
                    days=int(purchase.tariff_days),
                    source=SubscriptionChargeSource.manual,
                    bot=bot,
                )
                purchase.applied_at = now
                applied += 1
                tariff_title = tariff.title if tariff else f"{purchase.tariff_days} дней"
                await self._safe_send(
                    bot,
                    user.telegram_id,
                    (
                        f"✅ Тариф активирован автоматически: {tariff_title}\n"
                        f"Списано: {purchase.tariff_price} ₽\n"
                        f"Баланс: {user.balance} ₽"
                    ),
                )
            await session.commit()
        return applied, cancelled

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

    async def _charge_subscription(
        self,
        session: AsyncSession,
        user: User,
        *,
        price: Decimal,
        days: int,
        source: SubscriptionChargeSource,
        bot: Bot | None,
    ) -> None:
        user.balance -= price
        now = utc_now()
        base = user.expiration_date if user.expiration_date and user.expiration_date > now else now
        user.expiration_date = base + timedelta(days=days)
        user.warning_sent_at = None

        if user.status == UserStatus.active and not user.device_limit_blocked:
            await self.xray_service.enable_user(user.telegram_id, str(user.uuid))
            user.vpn_enabled = True

        await self._record_subscription_payment(session, user, source=source)
        await self._maybe_apply_referral_year_reward(session, invited_user=user, bot=bot)

    async def _apply_instruction_purchase(self, user: User, *, price: Decimal) -> None:
        user.balance -= price

    def _instruction_delivery_text(self, balance_after_charge: Decimal) -> str:
        return (
            "✅ Покупка успешна: «Инструкция по обходу белых списков».\n"
            "Ссылка на инструкцию:\n"
            f"{self.settings.whitelist_instruction_url}\n\n"
            f"Баланс: {balance_after_charge} ₽"
        )

    def _payment_amount_for_shortage(self, shortage: Decimal) -> int:
        min_amount = Decimal(str(self.settings.payment_min_amount))
        needed = max(min_amount, max(Decimal("0.00"), shortage))
        return int(needed.to_integral_value(rounding=ROUND_UP))

    async def _disable_if_needed(self, user: User) -> None:
        await self.xray_service.disable_user(user.telegram_id)
        user.vpn_enabled = False

    @staticmethod
    async def _safe_send(bot: Bot, telegram_id: int, text: str) -> None:
        try:
            await bot.send_message(telegram_id, text)
        except Exception:  # noqa: BLE001
            logger.debug("Cannot send billing message to %s", telegram_id)
