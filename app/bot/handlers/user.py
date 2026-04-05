from __future__ import annotations

from html import escape
from decimal import Decimal

from aiogram import Bot, F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards import main_menu, payment_link_menu, topup_amounts_menu
from app.config import Settings
from app.db.repositories import PaymentRepository, UserRepository
from app.services.billing_service import BillingService
from app.services.payment_service import PaymentProviderError, TelegaPayService
from app.services.user_service import UserService
from app.services.xray_service import XrayService

user_router = Router(name="user-router")


async def _get_user(session: AsyncSession, telegram_id: int):
    return await UserRepository(session).get_by_telegram_id(telegram_id)


@user_router.message(CommandStart())
async def start_handler(
    message: Message,
    command: CommandObject | None,
    session: AsyncSession,
    settings: Settings,
    user_service: UserService,
    bot: Bot,
) -> None:
    start_param = command.args if command else None
    user, is_new = await user_service.get_or_create_user(
        session=session,
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        start_param=start_param,
    )
    if is_new:
        await user_service.activate_vpn_if_needed(user)

    bot_info = await bot.get_me()
    text = (
        "👋 Добро пожаловать в kVPN.\n\n"
        "Здесь лучший VPN по доступной цене\n"
        f"Тариф: {settings.month_price_rub} ₽ / 30 дней."
    )
    if is_new:
        text += f"\n\n🎁 Стартовый бонус: {settings.trial_days} день(дней)."
    text += f"\n\nКАЖДЫЙ ПРИГЛАШЕННЫЙ ПОЛЬЗОВАТЕЛЬ = +{settings.referral_bonus_days} бесплатных дня. Реферальная ссылка: https://t.me/{bot_info.username}?start=ref_{user.referral_code}"
    await message.answer(text, reply_markup=main_menu(is_admin=settings.is_admin(message.from_user.id)))


@user_router.callback_query(F.data == "menu:back")
async def menu_back_handler(callback: CallbackQuery, settings: Settings) -> None:
    await callback.message.edit_text(
        "Главное меню:",
        reply_markup=main_menu(is_admin=settings.is_admin(callback.from_user.id)),
    )
    await callback.answer()


@user_router.callback_query(F.data == "menu:status")
async def status_handler(
    callback: CallbackQuery,
    session: AsyncSession,
    user_service: UserService,
    xray_service: XrayService,
    settings: Settings,
) -> None:
    user = await _get_user(session, callback.from_user.id)
    if user is None:
        await callback.answer("Пользователь не найден. Нажмите /start", show_alert=True)
        return
    traffic = await xray_service.get_user_traffic(user.telegram_id)
    text = user_service.build_status_text(user, traffic=traffic)
    await callback.message.edit_text(text, reply_markup=main_menu(is_admin=settings.is_admin(callback.from_user.id)))
    await callback.answer()


@user_router.callback_query(F.data == "menu:balance")
async def balance_handler(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    user = await _get_user(session, callback.from_user.id)
    if user is None:
        await callback.answer("Пользователь не найден. Нажмите /start", show_alert=True)
        return
    await callback.message.edit_text(
        f"💳 Текущий баланс: {user.balance} ₽",
        reply_markup=main_menu(is_admin=settings.is_admin(callback.from_user.id)),
    )
    await callback.answer()


@user_router.callback_query(F.data == "menu:buy_month")
async def buy_month_handler(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    billing_service: BillingService,
) -> None:
    user = await _get_user(session, callback.from_user.id)
    if user is None:
        await callback.answer("Пользователь не найден. Нажмите /start", show_alert=True)
        return
    success, text = await billing_service.purchase_month(session, user)
    if success:
        await callback.message.edit_text(
            text,
            reply_markup=main_menu(is_admin=settings.is_admin(callback.from_user.id)),
        )
    else:
        await callback.answer(text, show_alert=True)
    await callback.answer()


@user_router.callback_query(F.data == "menu:topup")
async def topup_menu_handler(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "Выберите сумму пополнения:",
        reply_markup=topup_amounts_menu(),
    )
    await callback.answer()


@user_router.callback_query(F.data.startswith("topup:"))
async def topup_create_handler(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    payment_service: TelegaPayService,
) -> None:
    user = await _get_user(session, callback.from_user.id)
    if user is None:
        await callback.answer("Пользователь не найден. Нажмите /start", show_alert=True)
        return

    amount_raw = callback.data.split(":")[1]
    amount = int(amount_raw)
    if amount < settings.payment_min_amount:
        await callback.answer(f"Минимальная сумма пополнения: {settings.payment_min_amount} ₽", show_alert=True)
        return

    try:
        payment, payment_url = await payment_service.create_payment(user=user, amount_rub=amount)
    except (PaymentProviderError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.message.edit_text(
        (
            f"Платеж создан.\n"
            f"Сумма: {amount} ₽\n"
            f"ID: {payment.id}\n\n"
            "1) Перейдите по ссылке и оплатите через СБП.\n"
            "2) Нажмите «Я оплатил».\n"
            "Бот также проверяет статус автоматически."
        ),
        reply_markup=payment_link_menu(payment_url, payment.id),
    )
    await callback.answer()


@user_router.callback_query(F.data.startswith("payment:confirm:"))
async def payment_confirm_handler(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    payment_service: TelegaPayService,
) -> None:
    payload = callback.data.split(":")
    if len(payload) != 3:
        await callback.answer("Некорректный формат подтверждения", show_alert=True)
        return
    try:
        payment_id = int(payload[2])
    except ValueError:
        await callback.answer("Некорректный ID платежа", show_alert=True)
        return

    user = await _get_user(session, callback.from_user.id)
    if user is None:
        await callback.answer("Пользователь не найден. Нажмите /start", show_alert=True)
        return

    payment = await PaymentRepository(session).get_by_id_for_user(payment_id=payment_id, user_id=user.id)
    if payment is None:
        await callback.answer("Платеж не найден", show_alert=True)
        return

    is_paid, text = await payment_service.confirm_pending_payment(payment=payment, user=user)
    if is_paid:
        await callback.message.edit_text(
            text,
            reply_markup=main_menu(is_admin=settings.is_admin(callback.from_user.id)),
        )
        await callback.answer()
        return

    await callback.answer(text, show_alert=True)


@user_router.callback_query(F.data == "menu:vpn_link")
async def vpn_link_handler(
    callback: CallbackQuery,
    session: AsyncSession,
    xray_service: XrayService,
    settings: Settings,
) -> None:
    user = await _get_user(session, callback.from_user.id)
    if user is None:
        await callback.answer("Пользователь не найден. Нажмите /start", show_alert=True)
        return

    link = xray_service.build_vless_link(str(user.uuid), user.telegram_id)
    await callback.message.edit_text(
        f"🔗 Ваша постоянная VLESS-ссылка:\n\n<code>{escape(link)}</code>",
        reply_markup=main_menu(is_admin=settings.is_admin(callback.from_user.id)),
    )
    await callback.answer()


@user_router.callback_query(F.data == "menu:referrals")
async def referrals_handler(
    callback: CallbackQuery,
    session: AsyncSession,
    bot: Bot,
    settings: Settings,
) -> None:
    user = await _get_user(session, callback.from_user.id)
    if user is None:
        await callback.answer("Пользователь не найден. Нажмите /start", show_alert=True)
        return

    repo = UserRepository(session)
    invited_count = await repo.count_referrals(user.id)
    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start=ref_{user.referral_code}"
    text = (
        "🎁 Реферальная программа\n\n"
        f"За каждого приглашенного: +{settings.referral_bonus_days} дня.\n"
        f"Приглашено: {invited_count}\n\n"
        f"Ваша ссылка:\n{link}"
    )
    await callback.message.edit_text(text, reply_markup=main_menu(is_admin=settings.is_admin(callback.from_user.id)))
    await callback.answer()


@user_router.message(F.text == "/balance")
async def balance_shortcut(message: Message, session: AsyncSession) -> None:
    user = await _get_user(session, message.from_user.id)
    if not user:
        await message.answer("Нажмите /start")
        return
    await message.answer(f"Баланс: {user.balance} ₽")


@user_router.message(F.text == "/pay")
async def pay_shortcut(message: Message, settings: Settings) -> None:
    await message.answer(
        f"Стоимость месяца: {Decimal(str(settings.month_price_rub))} ₽\nНажмите «Пополнить» в меню.",
        reply_markup=main_menu(is_admin=settings.is_admin(message.from_user.id)),
    )
