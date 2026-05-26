from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.bot.handlers.admin import (
    _build_users_summary_text,
    _format_user_list_line,
    _status_short,
    _users_export_filename,
)
from app.db.repositories import UserSummaryStats


def _user(
    *,
    status: str = "active",
    vpn_enabled: bool = False,
    device_limit_blocked: bool = False,
    username: str | None = "alice",
):
    return SimpleNamespace(
        telegram_id=123456789,
        username=username,
        balance=Decimal("12.30"),
        expiration_date=None,
        status=SimpleNamespace(value=status),
        vpn_enabled=vpn_enabled,
        device_limit_blocked=device_limit_blocked,
    )


def test_status_short_values() -> None:
    assert _status_short(_user(vpn_enabled=True)) == "y"
    assert _status_short(_user(vpn_enabled=False)) == "n"
    assert _status_short(_user(status="banned", vpn_enabled=True)) == "ban"
    assert _status_short(_user(vpn_enabled=True, device_limit_blocked=True)) == "lim"


def test_format_user_list_line_matches_admin_format() -> None:
    line = _format_user_list_line(
        _user(vpn_enabled=True),
        referrals_count=2,
        paid_referrals_count=1,
        timezone_name="Europe/Moscow",
    )

    assert line == "123456789 | @alice | 12.30 ₽ | ref:2 paid:1 | 0 | y"


def test_format_user_list_line_without_username() -> None:
    line = _format_user_list_line(
        _user(username=None),
        referrals_count=0,
        paid_referrals_count=0,
        timezone_name="Europe/Moscow",
    )

    assert line == "123456789 | - | 12.30 ₽ | ref:0 paid:0 | 0 | n"


def test_build_users_summary_text() -> None:
    summary = UserSummaryStats(
        total_users=10,
        active_users=8,
        banned_users=2,
        vpn_enabled_users=6,
        device_limit_blocked_users=1,
        active_subscription_users=7,
        expired_or_without_subscription_users=3,
        total_balance=Decimal("1234.50"),
        total_referrals=4,
        paid_referrals=2,
        paid_payments=5,
        paid_payments_amount=Decimal("1500"),
    )

    text = _build_users_summary_text(summary)

    assert "Всего пользователей: 10" in text
    assert "Активных / забаненных: 8 / 2" in text
    assert "Суммарный баланс: 1234.50 ₽" in text
    assert "Сумма оплат: 1500.00 ₽" in text


def test_users_export_filename() -> None:
    now = datetime(2026, 5, 26, 9, 8, 7, tzinfo=timezone.utc)

    assert _users_export_filename(now) == "users_export_20260526_090807.txt"
