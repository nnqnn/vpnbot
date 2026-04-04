from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import User, UserStatus
from app.db.repositories import ReferralRepository, UserRepository
from app.services.xray_service import XrayService
from app.utils.security import generate_referral_code
from app.utils.time import human_remaining, localize, utc_now

logger = logging.getLogger(__name__)


class UserService:
    def __init__(self, settings: Settings, xray_service: XrayService) -> None:
        self.settings = settings
        self.xray_service = xray_service

    async def get_or_create_user(
        self,
        session: AsyncSession,
        telegram_id: int,
        username: str | None,
        start_param: str | None = None,
    ) -> tuple[User, bool]:
        user_repo = UserRepository(session)
        referral_repo = ReferralRepository(session)

        user = await user_repo.get_by_telegram_id(telegram_id)
        if user:
            if username and user.username != username:
                user.username = username
            return user, False

        inviter = await self._resolve_inviter(session, start_param=start_param, telegram_id=telegram_id)
        referral_code = await self._generate_unique_referral_code(session)
        now = utc_now()
        expiration = now + timedelta(days=self.settings.trial_days)
        user = await user_repo.create(
            telegram_id=telegram_id,
            username=username,
            uuid=self._new_uuid(),
            balance=Decimal("0.00"),
            expiration_date=expiration,
            status=UserStatus.active,
            vpn_enabled=True,
            referral_code=referral_code,
            referred_by=inviter.id if inviter else None,
        )

        if inviter and not await referral_repo.exists_for_invited(user.id):
            await referral_repo.create(inviter_id=inviter.id, invited_id=user.id, bonus_applied=True)
            self.extend_user_days(inviter, self.settings.referral_bonus_days)
            inviter.warning_sent_at = None
            if inviter.status == UserStatus.active and not inviter.device_limit_blocked:
                inviter.vpn_enabled = True

        await session.flush()
        return user, True

    async def activate_vpn_if_needed(self, user: User) -> None:
        if user.status == UserStatus.banned:
            return
        if user.device_limit_blocked:
            return
        await self.xray_service.enable_user(user.telegram_id, str(user.uuid))

    async def disable_vpn(self, user: User) -> None:
        await self.xray_service.disable_user(user.telegram_id)

    def build_status_text(self, user: User, traffic: tuple[int, int] | None = None) -> str:
        if user.status == UserStatus.banned:
            status_text = "🔒 Заблокирован"
        elif user.device_limit_blocked:
            status_text = "🚫 Отключен (лимит устройств)"
        elif user.vpn_enabled and user.expiration_date and user.expiration_date > utc_now():
            status_text = "✅ Активен"
        else:
            status_text = "⛔ Неактивен"

        if user.expiration_date:
            remaining = human_remaining(user.expiration_date, self.settings.timezone)
            end_at = localize(user.expiration_date, self.settings.timezone).strftime("%d.%m.%Y %H:%M")
        else:
            remaining = "0 дней"
            end_at = "не задано"

        traffic_text = "недоступно"
        if traffic:
            up_mb = traffic[0] / 1024 / 1024
            down_mb = traffic[1] / 1024 / 1024
            traffic_text = f"⬆️ {up_mb:.1f} MB / ⬇️ {down_mb:.1f} MB"

        return (
            f"{status_text}\n"
            f"Срок до: {end_at}\n"
            f"Осталось: {remaining}\n"
            f"Баланс: {user.balance} ₽\n"
            f"Трафик: {traffic_text}"
        )

    def extend_user_days(self, user: User, days: int) -> None:
        base = utc_now()
        if user.expiration_date and user.expiration_date > base:
            base = user.expiration_date
        user.expiration_date = base + timedelta(days=days)

    def reduce_user_days(self, user: User, days: int) -> None:
        if not user.expiration_date:
            return
        user.expiration_date = user.expiration_date - timedelta(days=days)
        if user.expiration_date < utc_now():
            user.expiration_date = utc_now()

    async def _resolve_inviter(
        self,
        session: AsyncSession,
        start_param: str | None,
        telegram_id: int,
    ) -> User | None:
        if not start_param or not start_param.startswith("ref_"):
            return None
        code = start_param.removeprefix("ref_").strip()
        if not code:
            return None

        user_repo = UserRepository(session)
        inviter = await user_repo.get_by_referral_code(code)
        if inviter is None:
            return None
        if inviter.telegram_id == telegram_id:
            return None
        return inviter

    async def _generate_unique_referral_code(self, session: AsyncSession) -> str:
        user_repo = UserRepository(session)
        for _ in range(20):
            code = generate_referral_code()
            if await user_repo.get_by_referral_code(code) is None:
                return code
        raise RuntimeError("Could not generate unique referral code")

    @staticmethod
    def _new_uuid():
        import uuid

        return uuid.uuid4()
