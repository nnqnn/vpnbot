from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import urlencode

import httpx
from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import Payment, User
from app.db.repositories import PaymentRepository
from app.utils.security import generate_payment_label

logger = logging.getLogger("payments")


class YooMoneyService:
    OPERATION_HISTORY_URL = "https://yoomoney.ru/api/operation-history"
    QUICKPAY_URL = "https://yoomoney.ru/quickpay/confirm.xml"

    def __init__(self, settings: Settings, session_maker: async_sessionmaker[AsyncSession]) -> None:
        self.settings = settings
        self.session_maker = session_maker

    async def create_payment(self, user: User, amount_rub: int) -> tuple[Payment, str]:
        if amount_rub < self.settings.payment_min_amount:
            raise ValueError(f"Минимальная сумма пополнения: {self.settings.payment_min_amount} ₽")

        label = generate_payment_label(prefix=f"U{user.telegram_id}")
        amount = Decimal(str(amount_rub))

        async with self.session_maker() as session:
            payment_repo = PaymentRepository(session)
            payment = await payment_repo.create_pending(user_id=user.id, amount=amount, provider_label=label)
            await session.commit()

        payment_url = self._build_quickpay_url(amount_rub=amount_rub, label=label)
        logger.info("Created payment id=%s user=%s amount=%s label=%s", payment.id, user.telegram_id, amount, label)
        return payment, payment_url

    async def poll_pending_payments(self, bot: Bot) -> int:
        processed = 0
        async with self.session_maker() as session:
            payment_repo = PaymentRepository(session)
            pending = await payment_repo.pending(limit=200)
            for payment in pending:
                try:
                    if payment.created_at < datetime.now(timezone.utc) - timedelta(minutes=self.settings.payment_ttl_minutes):
                        # await payment_repo.mark_cancelled(payment)
                        # TODO: Uncomment this when we have a way to handle cancelled payments
                        continue

                    op = await self._find_success_operation(payment.provider_label, payment.amount)
                    if op is None:
                        continue

                    operation_id = op.get("operation_id")
                    if not operation_id:
                        continue

                    user = await session.get(User, payment.user_id)
                    if user is None:
                        continue

                    await payment_repo.mark_paid(payment, operation_id=operation_id, paid_at=self._parse_operation_dt(op))
                    user.balance = (user.balance or Decimal("0.00")) + payment.amount
                    processed += 1

                    await self._safe_send(
                        bot,
                        user.telegram_id,
                        f"✅ Платеж зачислен: +{payment.amount} ₽\nТекущий баланс: {user.balance} ₽",
                    )
                    logger.info("Payment confirmed id=%s operation_id=%s user=%s", payment.id, operation_id, user.telegram_id)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Failed to process payment id=%s: %s", payment.id, exc)

            await session.commit()
        return processed

    async def _find_success_operation(self, label: str, amount: Decimal) -> dict | None:
        payload = {"label": label, "type": "deposition", "records": 30}
        headers = {"Authorization": f"Bearer {self.settings.yoomoney_token}"}

        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(self.OPERATION_HISTORY_URL, data=payload, headers=headers)
            response.raise_for_status()
            data = response.json()

        operations = data.get("operations", [])
        for operation in operations:
            if operation.get("status") != "success":
                continue
            operation_amount = Decimal(str(operation.get("amount", "0")))
            if operation_amount < amount:
                continue
            if operation.get("label") != label:
                continue
            return operation
        return None

    def _build_quickpay_url(self, amount_rub: int, label: str) -> str:
        payload = {
            "receiver": self.settings.yoomoney_wallet,
            "quickpay-form": "button",
            "targets": "VPN balance topup",
            "paymentType": "SB",
            "sum": str(amount_rub),
            "label": label,
        }
        if self.settings.yoomoney_success_url:
            payload["successURL"] = self.settings.yoomoney_success_url
        return f"{self.QUICKPAY_URL}?{urlencode(payload)}"

    @staticmethod
    def _parse_operation_dt(operation: dict) -> datetime | None:
        raw = operation.get("datetime")
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    async def _safe_send(bot: Bot, telegram_id: int, text: str) -> None:
        try:
            await bot.send_message(telegram_id, text)
        except Exception:  # noqa: BLE001
            logger.warning("Cannot deliver payment notification to %s", telegram_id)
