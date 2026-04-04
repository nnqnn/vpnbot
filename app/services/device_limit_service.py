from __future__ import annotations

import logging
import re
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import User, UserStatus
from app.services.xray_service import XrayService

logger = logging.getLogger(__name__)


class DeviceLimitService:
    EMAIL_PATTERN = re.compile(r"user-(\d+)@vpn\.local")
    ACCESS_PATTERN = re.compile(r"from\s+(?P<ip>[0-9a-fA-F\.:]+):\d+.*email:(?P<email>[^\s]+)")

    def __init__(
        self,
        settings: Settings,
        session_maker: async_sessionmaker[AsyncSession],
        xray_service: XrayService,
    ) -> None:
        self.settings = settings
        self.session_maker = session_maker
        self.xray_service = xray_service

    async def enforce(self, bot: Bot) -> None:
        if self.settings.max_devices <= 0:
            return
        offending_ids = self._collect_offending_telegram_ids(self.settings.xray_access_log_path, self.settings.max_devices)
        if not offending_ids:
            await self._recover_unblocked_users(bot, set())
            return
        await self._apply_blocks(bot, offending_ids)
        await self._recover_unblocked_users(bot, offending_ids)

    async def _apply_blocks(self, bot: Bot, offending_ids: set[int]) -> None:
        async with self.session_maker() as session:
            result = await session.execute(
                select(User).where(User.telegram_id.in_(offending_ids), User.status == UserStatus.active)
            )
            users = list(result.scalars().all())
            for user in users:
                if user.device_limit_blocked:
                    continue
                user.device_limit_blocked = True
                if user.vpn_enabled:
                    await self.xray_service.disable_user(user.telegram_id)
                    user.vpn_enabled = False
                await self._safe_send(
                    bot,
                    user.telegram_id,
                    (
                        "🚫 Доступ временно отключен: зафиксировано больше 4 устройств.\n"
                        "Отключите лишние устройства и подождите до следующей проверки."
                    ),
                )
            await session.commit()

    async def _recover_unblocked_users(self, bot: Bot, offending_ids: set[int]) -> None:
        now = datetime.now(timezone.utc)
        async with self.session_maker() as session:
            result = await session.execute(
                select(User).where(User.device_limit_blocked.is_(True), User.status == UserStatus.active)
            )
            users = list(result.scalars().all())
            for user in users:
                if user.telegram_id in offending_ids:
                    continue
                user.device_limit_blocked = False
                if user.expiration_date and user.expiration_date > now and not user.vpn_enabled:
                    await self.xray_service.enable_user(user.telegram_id, str(user.uuid))
                    user.vpn_enabled = True
                await self._safe_send(
                    bot,
                    user.telegram_id,
                    "✅ Ограничение по устройствам снято, VPN снова доступен.",
                )
            await session.commit()

    @classmethod
    def _collect_offending_telegram_ids(cls, log_path: Path, max_devices: int) -> set[int]:
        if not log_path.exists():
            logger.warning("Xray access log not found: %s", log_path)
            return set()

        ips_by_email: dict[str, set[str]] = defaultdict(set)
        for line in cls._tail(log_path, max_lines=50000):
            match = cls.ACCESS_PATTERN.search(line)
            if not match:
                continue
            email = match.group("email")
            ips_by_email[email].add(match.group("ip"))

        offending_ids: set[int] = set()
        for email, ips in ips_by_email.items():
            if len(ips) <= max_devices:
                continue
            email_match = cls.EMAIL_PATTERN.fullmatch(email.strip())
            if email_match:
                offending_ids.add(int(email_match.group(1)))
        return offending_ids

    @staticmethod
    def _tail(path: Path, max_lines: int) -> list[str]:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            return list(deque(f, maxlen=max_lines))

    @staticmethod
    async def _safe_send(bot: Bot, telegram_id: int, text: str) -> None:
        try:
            await bot.send_message(telegram_id, text)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to send device-limit notification to %s: %s", telegram_id, exc)
