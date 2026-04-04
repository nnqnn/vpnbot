from __future__ import annotations

from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards import admin_menu
from app.bot.states import AdminStates
from app.config import Settings
from app.db.repositories import UserRepository
from app.services.admin_service import AdminService
from app.utils.time import human_remaining

admin_router = Router(name="admin-router")


async def _is_admin(user_id: int, settings: Settings) -> bool:
    return settings.is_admin(user_id)


async def _deny_callback(callback: CallbackQuery) -> None:
    await callback.answer("Недостаточно прав", show_alert=True)


async def _deny_message(message: Message) -> None:
    await message.answer("Недостаточно прав.")


def _status_short(user) -> str:
    if user.status.value == "banned":
        return "banned"
    if user.device_limit_blocked:
        return "limit-block"
    if user.vpn_enabled:
        return "active"
    return "inactive"


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


@admin_router.callback_query(F.data == "admin:list_users")
async def admin_list_users(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    if not await _is_admin(callback.from_user.id, settings):
        await _deny_callback(callback)
        return
    users = await UserRepository(session).list_users(limit=50)
    if not users:
        await callback.message.edit_text("Пользователей пока нет.", reply_markup=admin_menu())
        await callback.answer()
        return
    lines = []
    for user in users:
        expires = human_remaining(user.expiration_date, settings.timezone) if user.expiration_date else "0"
        lines.append(f"{user.telegram_id} | {user.balance} ₽ | {_status_short(user)} | {expires}")
    text = "👥 Пользователи (последние 50):\n\n" + "\n".join(lines)
    await callback.message.edit_text(text[:3900], reply_markup=admin_menu())
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
    await callback.message.edit_text("Формат: <telegram_id> <days>", reply_markup=admin_menu())
    await callback.answer()


@admin_router.callback_query(F.data == "admin:remove_days")
async def admin_remove_days(callback: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    if not await _is_admin(callback.from_user.id, settings):
        await _deny_callback(callback)
        return
    await state.set_state(AdminStates.wait_remove_days)
    await callback.message.edit_text("Формат: <telegram_id> <days>", reply_markup=admin_menu())
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
        "Формат: <telegram_id> <days> <amount_rub>\nПример: 123456789 2 50",
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
        await message.answer("Формат: <telegram_id> <days>")
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
        await message.answer("Формат: <telegram_id> <days>")
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
        await message.answer("Формат: <telegram_id> <days> <amount_rub>")
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
    await state.clear()
