from __future__ import annotations

import re
from collections import deque
from decimal import Decimal, InvalidOperation
from html import escape as html_escape
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards import admin_menu, admin_partners_menu
from app.bot.states import AdminStates
from app.config import Settings
from app.db.repositories import PartnerReferralLinkRepository, ReferralRepository, UserRepository
from app.services.admin_service import AdminService
from app.utils.security import generate_referral_code
from app.utils.time import human_remaining, utc_now

admin_router = Router(name="admin-router")
ACCESS_EMAIL_PATTERN = re.compile(r"email[:=]\s*(?P<email>[^\s,\]]+)")


async def _is_admin(user_id: int, settings: Settings) -> bool:
    return settings.is_admin(user_id)


async def _deny_callback(callback: CallbackQuery) -> None:
    await callback.answer("Недостаточно прав", show_alert=True)


async def _deny_message(message: Message) -> None:
    await message.answer("Недостаточно прав.")


async def _notify_user(bot, telegram_id: int, text: str) -> None:
    try:
        await bot.send_message(telegram_id, text)
    except Exception:  # noqa: BLE001
        return


def _username(user) -> str:
    if user.username:
        return f"@{user.username}"
    return "-"


def _status_short(user) -> str:
    if user.status.value == "banned":
        return "ban"
    if user.device_limit_blocked:
        return "lim"
    return "y" if user.vpn_enabled else "n"


async def _generate_unique_partner_code(session: AsyncSession) -> str:
    user_repo = UserRepository(session)
    partner_repo = PartnerReferralLinkRepository(session)
    for _ in range(40):
        code = generate_referral_code()
        if await user_repo.get_by_referral_code(code) is not None:
            continue
        if await partner_repo.get_by_code(code) is not None:
            continue
        return code
    raise RuntimeError("Could not generate unique partner referral code")


async def _build_partner_links_text(
    session: AsyncSession,
    *,
    bot_username: str,
    limit: int = 30,
) -> str:
    stats = await PartnerReferralLinkRepository(session).list_with_stats(limit=limit)
    if not stats:
        return (
            "🤝 Партнерские ссылки\n\n"
            "Ссылок пока нет.\n"
            "Нажмите «Создать партнерскую ссылку»."
        )

    lines = []
    for link, clicks, paid in stats:
        url = f"https://t.me/{bot_username}?start=ref_{link.code}"
        lines.append(
            (
                f"{html_escape(link.label)} | ref_{link.code}\n"
                f"Перешло: {clicks} | Оплатило: {paid}\n"
                f"{url}"
            )
        )

    return "🤝 Партнерские ссылки\n\n" + "\n\n".join(lines)


def _count_online_users_from_access_log(log_path: Path, max_lines: int = 200) -> tuple[int, int] | None:
    if not log_path.exists():
        return None
    lines = deque(maxlen=max_lines)
    try:
        with log_path.open("rb") as f:
            f.seek(0, 2)
            file_size = f.tell()
            chunk_size = 4096
            buffer = b""
            pos = file_size
            while pos > 0 and buffer.count(b"\n") <= max_lines:
                read_size = min(chunk_size, pos)
                pos -= read_size
                f.seek(pos)
                buffer = f.read(read_size) + buffer
            for raw_line in buffer.splitlines()[-max_lines:]:
                lines.append(raw_line.decode("utf-8", errors="ignore"))
    except OSError:
        return None
    accepted = 0
    emails: set[str] = set()
    for line in lines:
        if "accepted" not in line.lower():
            continue
        accepted += 1
        match = ACCESS_EMAIL_PATTERN.search(line)
        if not match:
            continue
        emails.add(match.group("email").strip())
    return len(emails), accepted


@admin_router.message(Command("admin"))
async def admin_command(message: Message, settings: Settings) -> None:
    if not await _is_admin(message.from_user.id, settings):
        await _deny_message(message)
        return
    await message.answer("Админ-панель:", reply_markup=admin_menu())


@admin_router.callback_query(F.data == "admin:open")
async def admin_open(callback: CallbackQuery, settings: Settings) -> None:
    if not await _is_admin(callback.from_user.id, settings):
        await _deny_callback(callback)
        return
    await callback.message.edit_text("Админ-панель:", reply_markup=admin_menu())
    await callback.answer()


@admin_router.callback_query(F.data == "admin:partners")
async def admin_partners(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    if not await _is_admin(callback.from_user.id, settings):
        await _deny_callback(callback)
        return
    bot_info = await callback.bot.get_me()
    bot_username = bot_info.username or "your_bot"
    text = await _build_partner_links_text(session, bot_username=bot_username, limit=30)
    await callback.message.edit_text(text[:3900], reply_markup=admin_partners_menu())
    await callback.answer()


@admin_router.callback_query(F.data == "admin:partner_create")
async def admin_partner_create(callback: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    if not await _is_admin(callback.from_user.id, settings):
        await _deny_callback(callback)
        return
    await state.set_state(AdminStates.wait_partner_label)
    await callback.message.edit_text(
        "Введите название партнерки (пример: channel_news_01):",
        reply_markup=admin_partners_menu(),
    )
    await callback.answer()


@admin_router.callback_query(F.data == "admin:list_users")
async def admin_list_users(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    if not await _is_admin(callback.from_user.id, settings):
        await _deny_callback(callback)
        return
    user_repo = UserRepository(session)
    referral_repo = ReferralRepository(session)
    users = await user_repo.list_users(limit=50)
    if not users:
        await callback.message.edit_text("Пользователей пока нет.", reply_markup=admin_menu())
        await callback.answer()
        return
    lines = []
    for user in users:
        expires = human_remaining(user.expiration_date, settings.timezone) if user.expiration_date else "0"
        referrals_count = await user_repo.count_referrals(user.id)
        paid_referrals_count = await referral_repo.count_invited_with_subscription_payment(user.id)
        lines.append(
            (
                f"{user.telegram_id} | {_username(user)} | {user.balance} ₽ | "
                f"ref:{referrals_count} paid:{paid_referrals_count} | {expires} | {_status_short(user)}"
            )
        )
    text = "👥 Пользователи (последние 50):\n\n" + "\n".join(lines)
    await callback.message.edit_text(text[:3900], reply_markup=admin_menu())
    await callback.answer()


@admin_router.callback_query(F.data == "admin:vpn_online_count")
async def admin_vpn_online_count(callback: CallbackQuery, settings: Settings) -> None:
    if not await _is_admin(callback.from_user.id, settings):
        await _deny_callback(callback)
        return
    result = _count_online_users_from_access_log(settings.xray_access_log_path, max_lines=200)
    if result is None:
        await callback.message.edit_text(
            f"Лог доступа Xray не найден: {settings.xray_access_log_path}",
            reply_markup=admin_menu(),
        )
        await callback.answer()
        return
    online_count, accepted_count = result
    text = (
        "📡 Онлайн VPN (оценка по access.log)\n\n"
        f"Уникальных ключей (email): {online_count}\n"
        f"Accepted строк в последних 200: {accepted_count}"
    )
    if accepted_count > 0 and online_count == 0:
        text += "\n\n⚠️ В accepted-логах не найдено email. Проверьте формат access.log Xray."
    await callback.message.edit_text(text, reply_markup=admin_menu())
    await callback.answer()


@admin_router.callback_query(F.data == "admin:get_balance")
async def admin_get_balance(callback: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    if not await _is_admin(callback.from_user.id, settings):
        await _deny_callback(callback)
        return
    await state.set_state(AdminStates.wait_balance_user)
    await callback.message.edit_text("Введите telegram_id пользователя:", reply_markup=admin_menu())
    await callback.answer()


@admin_router.callback_query(F.data == "admin:add_days")
async def admin_add_days(callback: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    if not await _is_admin(callback.from_user.id, settings):
        await _deny_callback(callback)
        return
    await state.set_state(AdminStates.wait_add_days)
    await callback.message.edit_text("Формат: telegram_id days", reply_markup=admin_menu())
    await callback.answer()


@admin_router.callback_query(F.data == "admin:add_days_all")
async def admin_add_days_all(callback: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    if not await _is_admin(callback.from_user.id, settings):
        await _deny_callback(callback)
        return
    await state.set_state(AdminStates.wait_add_days_all)
    await callback.message.edit_text("Введите количество дней для всех пользователей:", reply_markup=admin_menu())
    await callback.answer()


@admin_router.callback_query(F.data == "admin:remove_days")
async def admin_remove_days(callback: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    if not await _is_admin(callback.from_user.id, settings):
        await _deny_callback(callback)
        return
    await state.set_state(AdminStates.wait_remove_days)
    await callback.message.edit_text("Формат: telegram_id days", reply_markup=admin_menu())
    await callback.answer()


@admin_router.callback_query(F.data == "admin:broadcast")
async def admin_broadcast(callback: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    if not await _is_admin(callback.from_user.id, settings):
        await _deny_callback(callback)
        return
    await state.set_state(AdminStates.wait_broadcast_text)
    await callback.message.edit_text("Введите текст рассылки для всех пользователей:", reply_markup=admin_menu())
    await callback.answer()


@admin_router.callback_query(F.data == "admin:ban")
async def admin_ban(callback: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    if not await _is_admin(callback.from_user.id, settings):
        await _deny_callback(callback)
        return
    await state.set_state(AdminStates.wait_ban_user)
    await callback.message.edit_text("Введите telegram_id для бана:", reply_markup=admin_menu())
    await callback.answer()


@admin_router.callback_query(F.data == "admin:unban")
async def admin_unban(callback: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    if not await _is_admin(callback.from_user.id, settings):
        await _deny_callback(callback)
        return
    await state.set_state(AdminStates.wait_unban_user)
    await callback.message.edit_text("Введите telegram_id для разбана:", reply_markup=admin_menu())
    await callback.answer()


@admin_router.callback_query(F.data == "admin:bonus")
async def admin_bonus(callback: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    if not await _is_admin(callback.from_user.id, settings):
        await _deny_callback(callback)
        return
    await state.set_state(AdminStates.wait_bonus)
    await callback.message.edit_text(
        "Формат: telegram_id days amount_rub\nПример: 123456789 2 50",
        reply_markup=admin_menu(),
    )
    await callback.answer()


@admin_router.message(Command("cancel"))
async def admin_cancel(message: Message, state: FSMContext, settings: Settings) -> None:
    if not await _is_admin(message.from_user.id, settings):
        await _deny_message(message)
        return
    await state.clear()
    await message.answer("Отменено.", reply_markup=admin_menu())


@admin_router.message(AdminStates.wait_partner_label)
async def process_partner_label(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if not await _is_admin(message.from_user.id, settings):
        await _deny_message(message)
        return

    label = (message.text or "").strip()
    if not label:
        await message.answer("Название не должно быть пустым. Введите название партнерки.")
        return
    if len(label) > 255:
        await message.answer("Слишком длинное название. Используйте до 255 символов.")
        return

    code = await _generate_unique_partner_code(session)
    link = await PartnerReferralLinkRepository(session).create(code=code, label=label)

    bot_info = await message.bot.get_me()
    bot_username = bot_info.username or "your_bot"
    partner_url = f"https://t.me/{bot_username}?start=ref_{link.code}"
    await message.answer(
        (
            "✅ Партнерская ссылка создана.\n\n"
            f"Название: {html_escape(link.label)}\n"
            f"Код: {link.code}\n"
            f"Ссылка: {partner_url}\n\n"
            "Формат такой же, как у обычной реферальной ссылки."
        ),
        reply_markup=admin_partners_menu(),
    )
    await state.clear()


@admin_router.message(AdminStates.wait_balance_user)
async def process_balance_lookup(message: Message, state: FSMContext, session: AsyncSession, settings: Settings) -> None:
    if not await _is_admin(message.from_user.id, settings):
        await _deny_message(message)
        return
    try:
        telegram_id = int(message.text.strip())
    except ValueError:
        await message.answer("Нужен целочисленный telegram_id")
        return

    user = await UserRepository(session).get_by_telegram_id(telegram_id)
    if not user:
        await message.answer("Пользователь не найден")
    else:
        await message.answer(
            (
                f"Пользователь: {user.telegram_id}\n"
                f"Баланс: {user.balance} ₽\n"
                f"Статус: {_status_short(user)}"
            )
        )
    await state.clear()


@admin_router.message(AdminStates.wait_add_days)
async def process_add_days(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
    admin_service: AdminService,
) -> None:
    if not await _is_admin(message.from_user.id, settings):
        await _deny_message(message)
        return
    payload = message.text.strip().split()
    if len(payload) != 2:
        await message.answer("Формат: telegram_id days")
        return
    try:
        telegram_id, days = map(int, payload)
    except ValueError:
        await message.answer("Нужны целочисленные значения.")
        return
    if days <= 0:
        await message.answer("Количество дней должно быть больше 0.")
        return
    user = await UserRepository(session).get_by_telegram_id(telegram_id)
    if not user:
        await message.answer("Пользователь не найден")
        await state.clear()
        return
    await admin_service.add_days(user, days)
    await message.answer(f"Добавлено {days} дней пользователю {telegram_id}.")
    await _notify_user(
        message.bot,
        telegram_id,
        f"🎁 Администратор выдал вам {days} дополнительных дн. VPN.",
    )
    await state.clear()


@admin_router.message(AdminStates.wait_add_days_all)
async def process_add_days_all(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
    admin_service: AdminService,
) -> None:
    if not await _is_admin(message.from_user.id, settings):
        await _deny_message(message)
        return
    try:
        days = int(message.text.strip())
    except ValueError:
        await message.answer("Нужно целочисленное количество дней.")
        return
    if days <= 0:
        await message.answer("Количество дней должно быть больше 0.")
        return

    users = await UserRepository(session).list_all_users()
    now = utc_now()
    changed = 0
    notified = 0
    skipped_without_time = 0
    for user in users:
        if user.status.value != "active" or user.device_limit_blocked:
            skipped_without_time += 1
            continue
        if not user.expiration_date or user.expiration_date <= now:
            skipped_without_time += 1
            continue
        await admin_service.add_days(user, days)
        changed += 1
        try:
            await message.bot.send_message(
                user.telegram_id,
                f"🎁 Администратор выдал вам {days} дополнительных дн. VPN.",
            )
            notified += 1
        except Exception:  # noqa: BLE001
            continue

    await message.answer(
        f"Готово: +{days} дн. выдано {changed} пользователям.\n"
        f"Пропущено (нет активного срока/статуса): {skipped_without_time}\n"
        f"Уведомления доставлены: {notified}."
    )
    await state.clear()


@admin_router.message(AdminStates.wait_broadcast_text)
async def process_broadcast(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if not await _is_admin(message.from_user.id, settings):
        await _deny_message(message)
        return
    text = (message.text or "").strip()
    if not text:
        await message.answer("Текст рассылки не должен быть пустым.")
        return

    users = await UserRepository(session).list_all_users()
    sent = 0
    failed = 0
    for user in users:
        try:
            await message.bot.send_message(user.telegram_id, text, parse_mode=None)
            sent += 1
        except Exception:  # noqa: BLE001
            failed += 1

    await message.answer(f"Рассылка завершена.\nДоставлено: {sent}\nОшибок: {failed}")
    await state.clear()


@admin_router.message(AdminStates.wait_remove_days)
async def process_remove_days(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
    admin_service: AdminService,
) -> None:
    if not await _is_admin(message.from_user.id, settings):
        await _deny_message(message)
        return
    payload = message.text.strip().split()
    if len(payload) != 2:
        await message.answer("Формат: telegram_id days")
        return
    try:
        telegram_id, days = map(int, payload)
    except ValueError:
        await message.answer("Нужны целочисленные значения.")
        return
    if days <= 0:
        await message.answer("Количество дней должно быть больше 0.")
        return
    user = await UserRepository(session).get_by_telegram_id(telegram_id)
    if not user:
        await message.answer("Пользователь не найден")
        await state.clear()
        return
    await admin_service.remove_days(user, days)
    await message.answer(f"Снято {days} дней у пользователя {telegram_id}.")
    await state.clear()


@admin_router.message(AdminStates.wait_ban_user)
async def process_ban(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
    admin_service: AdminService,
) -> None:
    if not await _is_admin(message.from_user.id, settings):
        await _deny_message(message)
        return
    try:
        telegram_id = int(message.text.strip())
    except ValueError:
        await message.answer("Нужен целочисленный telegram_id")
        return
    user = await UserRepository(session).get_by_telegram_id(telegram_id)
    if not user:
        await message.answer("Пользователь не найден")
        await state.clear()
        return
    await admin_service.ban(user)
    await message.answer(f"Пользователь {telegram_id} забанен.")
    await state.clear()


@admin_router.message(AdminStates.wait_unban_user)
async def process_unban(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
    admin_service: AdminService,
) -> None:
    if not await _is_admin(message.from_user.id, settings):
        await _deny_message(message)
        return
    try:
        telegram_id = int(message.text.strip())
    except ValueError:
        await message.answer("Нужен целочисленный telegram_id")
        return
    user = await UserRepository(session).get_by_telegram_id(telegram_id)
    if not user:
        await message.answer("Пользователь не найден")
        await state.clear()
        return
    await admin_service.unban(user)
    await message.answer(f"Пользователь {telegram_id} разбанен.")
    await state.clear()


@admin_router.message(AdminStates.wait_bonus)
async def process_bonus(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
    admin_service: AdminService,
) -> None:
    if not await _is_admin(message.from_user.id, settings):
        await _deny_message(message)
        return
    payload = message.text.strip().split()
    if len(payload) != 3:
        await message.answer("Формат: telegram_id days amount_rub")
        return
    try:
        telegram_id = int(payload[0])
        days = int(payload[1])
        amount = Decimal(payload[2])
    except (ValueError, InvalidOperation):
        await message.answer("Неверный формат значений.")
        return
    if days < 0 or amount < 0:
        await message.answer("Дни и сумма должны быть неотрицательными.")
        return

    user = await UserRepository(session).get_by_telegram_id(telegram_id)
    if not user:
        await message.answer("Пользователь не найден")
        await state.clear()
        return
    await admin_service.grant_bonus(user=user, days=days, amount=amount)
    await message.answer(f"Бонус выдан пользователю {telegram_id}: +{days} дн., +{amount} ₽.")
    if days > 0:
        await _notify_user(
            message.bot,
            telegram_id,
            f"🎁 Администратор выдал вам {days} дополнительных дн. VPN.",
        )
    await state.clear()
