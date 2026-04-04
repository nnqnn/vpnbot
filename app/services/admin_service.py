from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User, UserStatus
from app.db.repositories import UserRepository
from app.services.xray_service import XrayService
from app.utils.time import utc_now


class AdminService:
    def __init__(self, xray_service: XrayService) -> None:
        self.xray_service = xray_service

    async def get_user_by_telegram_id(self, session: AsyncSession, telegram_id: int) -> User | None:
        return await UserRepository(session).get_by_telegram_id(telegram_id)

    async def add_days(self, user: User, days: int) -> None:
        base = user.expiration_date if user.expiration_date and user.expiration_date > utc_now() else utc_now()
        user.expiration_date = base + timedelta(days=days)
        user.warning_sent_at = None
        if user.status == UserStatus.active and not user.device_limit_blocked:
            await self.xray_service.enable_user(user.telegram_id, str(user.uuid))
            user.vpn_enabled = True

    async def remove_days(self, user: User, days: int) -> None:
        if not user.expiration_date:
            user.expiration_date = utc_now()
        else:
            user.expiration_date = user.expiration_date - timedelta(days=days)
            if user.expiration_date < utc_now():
                user.expiration_date = utc_now()
        if user.expiration_date <= utc_now() and user.vpn_enabled:
            await self.xray_service.disable_user(user.telegram_id)
            user.vpn_enabled = False

    async def ban(self, user: User) -> None:
        user.status = UserStatus.banned
        user.vpn_enabled = False
        await self.xray_service.disable_user(user.telegram_id)

    async def unban(self, user: User) -> None:
        user.status = UserStatus.active
        if user.expiration_date and user.expiration_date > utc_now() and not user.device_limit_blocked:
            await self.xray_service.enable_user(user.telegram_id, str(user.uuid))
            user.vpn_enabled = True

    async def grant_bonus(self, user: User, days: int = 0, amount: Decimal | None = None) -> None:
        if amount and amount > 0:
            user.balance = (user.balance or Decimal("0.00")) + amount
        if days > 0:
            await self.add_days(user, days)
