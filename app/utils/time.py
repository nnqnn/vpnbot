from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def add_days(dt: datetime, days: int) -> datetime:
    return dt + timedelta(days=days)


def add_month_days(dt: datetime, days: int = 30) -> datetime:
    return dt + timedelta(days=days)


def localize(dt: datetime, tz_name: str) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo(tz_name))


def human_remaining(target: datetime, tz_name: str) -> str:
    now = utc_now()
    delta = target - now
    if delta.total_seconds() <= 0:
        return "0 дней"
    days = delta.days
    hours = (delta.seconds // 3600) % 24
    if days > 0:
        return f"{days} дн. {hours} ч."
    return f"{hours} ч."
