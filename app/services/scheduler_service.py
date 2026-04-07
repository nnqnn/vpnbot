from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import Settings
from app.services.billing_service import BillingService
from app.services.device_limit_service import DeviceLimitService
from app.services.payment_service import TelegaPayService

logger = logging.getLogger(__name__)


class SchedulerService:
    def __init__(
        self,
        settings: Settings,
        bot: Bot,
        billing_service: BillingService,
        payment_service: TelegaPayService,
        device_limit_service: DeviceLimitService,
    ) -> None:
        self.settings = settings
        self.bot = bot
        self.billing_service = billing_service
        self.payment_service = payment_service
        self.device_limit_service = device_limit_service
        self.scheduler = AsyncIOScheduler(timezone=ZoneInfo(settings.timezone))

    def start(self) -> None:
        self.scheduler.add_job(
            self._payments_job,
            trigger=IntervalTrigger(seconds=self.settings.payment_poll_interval_seconds),
            id="payments_job",
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.add_job(
            self._auto_renew_job,
            trigger=IntervalTrigger(minutes=self.settings.auto_renew_interval_minutes),
            id="auto_renew_job",
            max_instances=1,
            coalesce=True,
        )
        if self.settings.xray_sync_interval_minutes > 0:
            self.scheduler.add_job(
                self._xray_sync_job,
                trigger=IntervalTrigger(minutes=self.settings.xray_sync_interval_minutes),
                id="xray_sync_job",
                max_instances=1,
                coalesce=True,
            )
        self.scheduler.add_job(
            self._notify_job,
            trigger=IntervalTrigger(minutes=self.settings.notify_interval_minutes),
            id="notify_job",
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.add_job(
            self._device_limit_job,
            trigger=IntervalTrigger(minutes=self.settings.device_limit_interval_minutes),
            id="device_limit_job",
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.start()
        logger.info("Scheduler started")

    async def shutdown(self) -> None:
        self.scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")

    async def _payments_job(self) -> None:
        processed = await self.payment_service.poll_pending_payments(self.bot)
        if processed:
            logger.info("Processed %s pending payments", processed)

    async def _auto_renew_job(self) -> None:
        await self.billing_service.run_auto_renew(self.bot)
        await self.billing_service.reconcile_states()

    async def _xray_sync_job(self) -> None:
        await self.billing_service.sync_xray_runtime_state()

    async def _notify_job(self) -> None:
        await self.billing_service.send_expiration_warnings(self.bot)

    async def _device_limit_job(self) -> None:
        await self.device_limit_service.enforce(self.bot)
