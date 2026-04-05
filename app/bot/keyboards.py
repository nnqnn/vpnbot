from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="ℹ️ Информация", callback_data="menu:info")
    kb.button(text="🛒 Оплатить месяц", callback_data="menu:buy_month")
    kb.button(text="💰 Пополнить баланс", callback_data="menu:topup")
    kb.button(text="🎁 Рефералы", callback_data="menu:referrals")
    if is_admin:
        kb.button(text="🛠 Админ-панель", callback_data="admin:open")
    kb.button(text="🔗 Моя VPN-ссылка", callback_data="menu:vpn_link")
    kb.adjust(2, 2, 1, 1)
    return kb.as_markup()


def topup_amounts_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="100 ₽", callback_data="topup:100")
    kb.button(text="300 ₽", callback_data="topup:300")
    kb.button(text="500 ₽", callback_data="topup:500")
    kb.button(text="1000 ₽", callback_data="topup:1000")
    kb.button(text="⬅️ Назад", callback_data="menu:back")
    kb.adjust(2, 2, 1)
    return kb.as_markup()


def admin_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="👥 Список пользователей", callback_data="admin:list_users")
    kb.button(text="💳 Баланс пользователя", callback_data="admin:get_balance")
    kb.button(text="➕ Выдать дни", callback_data="admin:add_days")
    kb.button(text="🌍 Выдать дни всем", callback_data="admin:add_days_all")
    kb.button(text="➖ Отнять дни", callback_data="admin:remove_days")
    kb.button(text="🔒 Забанить", callback_data="admin:ban")
    kb.button(text="🔓 Разбанить", callback_data="admin:unban")
    kb.button(text="🎁 Выдать бонус", callback_data="admin:bonus")
    kb.button(text="📣 Массовая рассылка", callback_data="admin:broadcast")
    kb.button(text="⬅️ В меню", callback_data="menu:back")
    kb.adjust(1, 1, 2, 2, 2, 1, 1)
    return kb.as_markup()


def payment_link_menu(payment_url: str, payment_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="💸 Перейти к оплате", url=payment_url)
    kb.button(text="✅ Я оплатил", callback_data=f"payment:confirm:{payment_id}")
    kb.button(text="⬅️ Назад", callback_data="menu:back")
    kb.adjust(1)
    return kb.as_markup()


def subscription_gate_menu(channel_url: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📢 Подписаться на канал", url=channel_url)
    kb.button(text="🔄 Проверить подписку", callback_data="gate:check_subscription")
    kb.adjust(1)
    return kb.as_markup()


def terms_gate_menu(rules_url: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📄 Правила сервиса и политика", url=rules_url)
    kb.button(text="✅ Принимаю", callback_data="gate:accept_terms")
    kb.adjust(1)
    return kb.as_markup()


def info_menu(*, is_admin: bool, support_url: str, rules_url: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🛟 Техническая поддержка", url=support_url)
    kb.button(text="📄 Правила сервиса", url=rules_url)
    kb.button(text="⬅️ В меню", callback_data="menu:back")
    if is_admin:
        kb.button(text="🛠 Админ-панель", callback_data="admin:open")
    kb.adjust(1)
    return kb.as_markup()
