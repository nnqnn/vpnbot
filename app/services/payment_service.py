from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import Payment, PaymentStatus, User
from app.db.repositories import PaymentRepository
from app.utils.security import generate_payment_label

logger = logging.getLogger("payments")


class PaymentProviderError(RuntimeError):
    pass


class TelegaPayService:
    PAID_STATUSES = {"completed"}
    CANCELLED_STATUSES = {"cancelled", "expired"}

    def __init__(self, settings: Settings, session_maker: async_sessionmaker[AsyncSession]) -> None:
        self.settings = settings
        self.session_maker = session_maker

    async def create_payment(self, user: User, amount_rub: int) -> tuple[Payment, str]:
        if amount_rub < self.settings.payment_min_amount:
            raise ValueError(f"Минимальная сумма пополнения: {self.settings.payment_min_amount} ₽")

        order_id = generate_payment_label(prefix=f"ORD{user.telegram_id}")
        amount = Decimal(str(amount_rub))
        payload = {
            "amount": float(amount),
            "currency": "RUB",
            "description": f"Balance top-up for TG {user.telegram_id}",
            "order_id": order_id,
            "payment_method": "SBP",
            "user_id": str(user.telegram_id),
        }
        if self.settings.telegapay_return_url:
            payload["return_url"] = self.settings.telegapay_return_url

        data = await self._post("create_paylink", payload)
        transaction_id = str(data.get("transaction_id", "")).strip()
        payment_url = str(data.get("payment_url", "")).strip()

        if not data.get("success"):
            raise PaymentProviderError("Платежный шлюз не подтвердил создание ссылки")
        if not transaction_id or not payment_url:
            raise PaymentProviderError("Платежный шлюз вернул неполные данные платежа")

        async with self.session_maker() as session:
            payment_repo = PaymentRepository(session)
            payment = await payment_repo.create_pending(user_id=user.id, amount=amount, provider_label=order_id)
            payment.external_operation_id = transaction_id
            await session.commit()

        logger.info(
            "Created TelegaPay payment id=%s user=%s amount=%s order_id=%s tx=%s",
            payment.id,
            user.telegram_id,
            amount,
            order_id,
            transaction_id,
        )
        return payment, payment_url

    async def poll_pending_payments(self, bot: Bot) -> int:
        processed = 0
        now = datetime.now(timezone.utc)
        async with self.session_maker() as session:
            payment_repo = PaymentRepository(session)
            pending = await payment_repo.pending(limit=60)
            for payment in pending:
                try:
                    if payment.created_at < now - timedelta(minutes=self.settings.payment_ttl_minutes):
                        await payment_repo.mark_cancelled(payment)
                        continue

                    if not payment.external_operation_id:
                        logger.warning("Pending payment id=%s has no transaction_id", payment.id)
                        continue

                    status_data = await self._safe_check_status(payment.external_operation_id)
                    if status_data is None:
                        continue

                    status = str(status_data.get("status", "")).lower().strip()
                    if status in self.PAID_STATUSES:
                        user = await session.get(User, payment.user_id)
                        if user is None:
                            continue

                        await payment_repo.mark_paid(
                            payment,
                            operation_id=payment.external_operation_id,
                            paid_at=self._parse_operation_dt(status_data),
                        )
                        user.balance = (user.balance or Decimal("0.00")) + payment.amount
                        processed += 1

                        await self._safe_send(
                            bot,
                            user.telegram_id,
                            f"✅ Платеж зачислен: +{payment.amount} ₽\nТекущий баланс: {user.balance} ₽",
                        )
                        logger.info(
                            "TelegaPay payment confirmed id=%s tx=%s user=%s",
                            payment.id,
                            payment.external_operation_id,
                            user.telegram_id,
                        )
                        continue

                    if status in self.CANCELLED_STATUSES:
                        await payment_repo.mark_cancelled(payment)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Failed to process payment id=%s: %s", payment.id, exc)

            await session.commit()
        return processed

    async def confirm_pending_payment(
        self,
        payment: Payment,
        user: User,
    ) -> tuple[bool, str]:
        if payment.user_id != user.id:
            return False, "Этот платеж вам не принадлежит."
        if payment.status == PaymentStatus.paid:
            return True, f"Платеж уже зачислен. Баланс: {user.balance} ₽"
        if payment.status != PaymentStatus.pending:
            return True, "Платеж уже завершен или отменен."
        if not payment.external_operation_id:
            return False, "У платежа отсутствует transaction_id. Создайте новый платеж."

        transaction_id = payment.external_operation_id
        try:
            response = await self._post("confirm_payment", {"transaction_id": transaction_id})
        except PaymentProviderError as exc:
            logger.warning("TelegaPay confirm failed tx=%s: %s", transaction_id, exc)
            return False, f"Не удалось отправить подтверждение оплаты: {exc}"

        if not response.get("success", True):
            return False, "Платежный шлюз не принял подтверждение. Попробуйте позже."

        return (
            True,
            (
                "✅ Получили ваш сигнал об оплате.\n"
                "Как только статус станет успешным, баланс зачислится."
            ),
        )

    async def _safe_check_status(self, transaction_id: str) -> dict | None:
        try:
            return await self._post("check_status", {"transaction_id": transaction_id})
        except PaymentProviderError as exc:
            logger.warning("TelegaPay status check failed tx=%s: %s", transaction_id, exc)
            return None

    async def _post(self, endpoint: str, payload: dict) -> dict:
        url = f"{self.settings.telegapay_base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        headers = {"X-API-Key": self.settings.telegapay_api_key}
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise PaymentProviderError(f"Ошибка сети при обращении к TelegaPay: {exc}") from exc

        if response.status_code >= 400:
            detail = response.text
            try:
                detail = response.json().get("error") or detail
            except Exception:  # noqa: BLE001
                pass
            raise PaymentProviderError(f"TelegaPay {response.status_code}: {detail}")

        try:
            return response.json()
        except ValueError as exc:
            raise PaymentProviderError("Некорректный JSON от TelegaPay") from exc

    @staticmethod
    def _parse_operation_dt(operation: dict) -> datetime | None:
        for key in ("completed_at", "confirmed_at", "processed_at", "created_at"):
            raw = operation.get(key)
            if not raw:
                continue
            try:
                return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except ValueError:
                continue
        return None

    @staticmethod
    async def _safe_send(bot: Bot, telegram_id: int, text: str) -> None:
        try:
            await bot.send_message(telegram_id, text)
        except Exception:  # noqa: BLE001
            logger.warning("Cannot deliver payment notification to %s", telegram_id)
