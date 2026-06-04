from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace

from app.bot.handlers.admin import _broadcast_to_users
from app.bot.keyboards import admin_menu
from app.bot.states import AdminStates
from app.services import billing_service as billing_module
from app.services.billing_service import BillingService, WHITELIST_PRODUCT_CODE


def test_admin_menu_has_whitelist_broadcast_callback() -> None:
    callbacks = [
        button.callback_data
        for row in admin_menu().inline_keyboard
        for button in row
        if button.callback_data
    ]

    assert "admin:broadcast_whitelist" in callbacks
    assert hasattr(AdminStates, "wait_broadcast_whitelist_text")


def test_broadcast_to_users_counts_sent_and_failed() -> None:
    class Bot:
        def __init__(self) -> None:
            self.messages = []

        async def send_message(self, telegram_id: int, text: str, parse_mode=None) -> None:
            if telegram_id == 2:
                raise RuntimeError("delivery failed")
            self.messages.append((telegram_id, text, parse_mode))

    users = [SimpleNamespace(telegram_id=1), SimpleNamespace(telegram_id=2), SimpleNamespace(telegram_id=3)]
    bot = Bot()

    sent, failed = asyncio.run(_broadcast_to_users(bot, users, "hello"))

    assert sent == 2
    assert failed == 1
    assert bot.messages == [(1, "hello", None), (3, "hello", None)]


def test_instruction_purchase_records_product_purchase(monkeypatch) -> None:
    created = []

    class FakeProductPurchaseRepository:
        def __init__(self, session) -> None:
            self.session = session

        async def create(self, **kwargs):
            created.append((self.session, kwargs))

    monkeypatch.setattr(billing_module, "ProductPurchaseRepository", FakeProductPurchaseRepository)
    service = BillingService(settings=None, session_maker=None, user_service=None, xray_service=None)
    user = SimpleNamespace(id=42, balance=Decimal("200.00"))
    session = object()

    asyncio.run(
        service._apply_instruction_purchase(
            session=session,
            user=user,
            product_code=WHITELIST_PRODUCT_CODE,
            price=Decimal("120.00"),
            source="balance",
        )
    )

    assert user.balance == Decimal("80.00")
    assert created == [
        (
            session,
            {
                "user_id": 42,
                "product_code": WHITELIST_PRODUCT_CODE,
                "amount": Decimal("120.00"),
                "source": "balance",
                "payment_id": None,
            },
        )
    ]
