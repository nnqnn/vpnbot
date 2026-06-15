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
from app.services.xray_service import OnlineDeviceStats, XrayService

logger = logging.getLogger(__name__)


class DeviceLimitService:
    EMAIL_PATTERN = re.compile(r"user-(\d+)@vpn\.local")
    ACCESS_PATTERN = re.compile(
        r"from\s+\[?(?P<ip>[0-9a-fA-F\.:]+)\]?:\d+.*email[:=]\s*(?P<email>[^\s,\]]+)"
    )

    def __init__(
        self,
        settings: Settings,
        session_maker: async_sessionmaker[AsyncSession],
        xray_service: XrayService,
        subscription_snapshot_service=None,
    ) -> None:
        self.settings = settings
        self.session_maker = session_maker
        self.xray_service = xray_service
        self.subscription_snapshot_service = subscription_snapshot_service

    async def enforce(self, bot: Bot) -> None:
        if self.settings.max_devices <= 0:
            return
        offending_ids, has_valid_snapshot = await self._collect_offending_telegram_ids()
        if not has_valid_snapshot:
            logger.warning("Device-limit check skipped: no valid combined online-device snapshot")
            return
        blocked_ids = await self._apply_blocks(bot, offending_ids) if offending_ids else set()
        recovered = await self._recover_unblocked_users(bot, offending_ids)
        if blocked_ids or recovered:
            await self._sync_subscription_snapshot()
        for telegram_id in blocked_ids:
            await self.xray_service.kick_hysteria_user(telegram_id)

    async def _apply_blocks(self, bot: Bot, offending_ids: set[int]) -> set[int]:
        blocked_ids: set[int] = set()
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
                blocked_ids.add(int(user.telegram_id))
                await self._safe_send(
                    bot,
                    user.telegram_id,
                    (
                        f"🚫 Доступ временно отключен: зафиксировано больше {self.settings.max_devices} устройств.\n"
                        "Отключите лишние устройства и подождите до следующей проверки."
                    ),
                )
            await session.commit()
        return blocked_ids

    async def _recover_unblocked_users(self, bot: Bot, offending_ids: set[int]) -> bool:
        now = datetime.now(timezone.utc)
        changed = False
        async with self.session_maker() as session:
            result = await session.execute(
                select(User).where(User.device_limit_blocked.is_(True), User.status == UserStatus.active)
            )
            users = list(result.scalars().all())
            for user in users:
                if user.telegram_id in offending_ids:
                    continue
                user.device_limit_blocked = False
                changed = True
                if user.expiration_date and user.expiration_date > now and not user.vpn_enabled:
                    await self.xray_service.enable_user(user.telegram_id, str(user.uuid))
                    user.vpn_enabled = True
                await self._safe_send(
                    bot,
                    user.telegram_id,
                    "✅ Ограничение по устройствам снято, VPN снова доступен.",
                )
            await session.commit()
        return changed

    async def _collect_offending_telegram_ids(self) -> tuple[set[int], bool]:
        if not self.settings.xray_api_enabled:
            logger.warning("Device-limit check skipped: XRAY_API_ENABLED=false")
            return set(), False
        api_result = await self._collect_offending_telegram_ids_api()
        if api_result is None:
            return set(), False
        return api_result, True

    async def _collect_offending_telegram_ids_api(self) -> set[int] | None:
        try:
            async with self.session_maker() as session:
                result = await session.execute(
                    select(User.telegram_id).where(
                        User.status == UserStatus.active,
                        User.vpn_enabled.is_(True),
                    )
                )
                telegram_ids = [int(tg_id) for (tg_id,) in result.all()]

            stats_by_id = await self.xray_service.get_users_online_device_stats(telegram_ids)
            if stats_by_id is None:
                logger.warning("Combined online-device stats unavailable; enforcement skipped")
                return None
            return self._offending_ids_from_device_stats(stats_by_id, self.settings.max_devices)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to collect combined online-device stats: %s", exc)
            return None

    async def _sync_subscription_snapshot(self) -> None:
        if self.subscription_snapshot_service is None:
            return
        try:
            await self.subscription_snapshot_service.sync_once()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to sync subscription snapshot after device-limit change: %s", exc)

    @staticmethod
    def _offending_ids_from_device_stats(
        stats_by_id: dict[int, OnlineDeviceStats],
        max_devices: int,
    ) -> set[int]:
        return {telegram_id for telegram_id, stats in stats_by_id.items() if stats.total > max_devices}

    @classmethod
    def _collect_offending_telegram_ids_from_logs(cls, log_path: Path, max_devices: int) -> set[int] | None:
        if not log_path.exists():
            logger.warning("Xray access log not found: %s", log_path)
            return None

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
