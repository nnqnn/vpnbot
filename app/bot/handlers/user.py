from __future__ import annotations

import logging
from html import escape
from decimal import Decimal

from aiogram import Bot, F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards import (
    info_menu,
    main_menu,
    payment_link_menu,
    subscription_gate_menu,
    terms_gate_menu,
    topup_amounts_menu,
)
from app.config import Settings
from app.db.repositories import PaymentRepository, UserPolicyRepository, UserRepository
from app.services.billing_service import BillingService
from app.services.payment_service import PaymentProviderError, TelegaPayService
from app.services.user_service import UserService
from app.services.xray_service import XrayService

user_router = Router(name="user-router")
logger = logging.getLogger(__name__)


async def _get_user(session: AsyncSession, telegram_id: int):
    return await UserRepository(session).get_by_telegram_id(telegram_id)


async def _is_subscribed(bot: Bot, settings: Settings, telegram_id: int) -> bool:
    try:
        member = await bot.get_chat_member(settings.required_channel, telegram_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Cannot verify subscription for %s: %s", telegram_id, exc)
        return False
    return getattr(member, "status", "") not in {"left", "kicked"}


async def _has_accepted_terms(session: AsyncSession, user_id: int) -> bool:
    return await UserPolicyRepository(session).is_terms_accepted(user_id)


async def _send_subscription_gate(message: Message, settings: Settings, *, edit: bool) -> None:
    text = (
        "Перед использованием бота подпишитесь на канал проекта.\n\n"
        "После подписки нажмите «Проверить подписку»."
    )
    if edit:
        await _safe_edit_text(message, text, reply_markup=subscription_gate_menu(settings.required_channel_url))
    else:
        await message.answer(text, reply_markup=subscription_gate_menu(settings.required_channel_url))


async def _send_terms_gate(message: Message, settings: Settings, *, edit: bool) -> None:
    text = (
        "Для продолжения нужно принять правила сервиса и политику конфиденциальности.\n\n"
        "Ознакомьтесь с документом и нажмите «Принимаю»."
    )
    if edit:
        await _safe_edit_text(message, text, reply_markup=terms_gate_menu(settings.rules_url))
    else:
        await message.answer(text, reply_markup=terms_gate_menu(settings.rules_url))


async def _safe_edit_text(message: Message, text: str, reply_markup) -> None:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except Exception as exc:  # noqa: BLE001
        if "message is not modified" in str(exc).lower():
            return
        raise


async def _ensure_access_for_callback(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    bot: Bot,
    user,
) -> bool:
    if not await _is_subscribed(bot, settings, callback.from_user.id):
        await _send_subscription_gate(callback.message, settings, edit=True)
        await callback.answer("Сначала подпишитесь на канал.", show_alert=True)
        return False
    if not await _has_accepted_terms(session, user.id):
        await _send_terms_gate(callback.message, settings, edit=True)
        await callback.answer("Примите правила сервиса.", show_alert=True)
        return False
    return True


async def _ensure_access_for_message(
    message: Message,
    session: AsyncSession,
    settings: Settings,
    bot: Bot,
    user,
) -> bool:
    if not await _is_subscribed(bot, settings, message.from_user.id):
        await _send_subscription_gate(message, settings, edit=False)
        return False
    if not await _has_accepted_terms(session, user.id):
        await _send_terms_gate(message, settings, edit=False)
        return False
    return True


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
    user, is_new, inviter_telegram_id = await user_service.get_or_create_user(
        session=session,
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        start_param=start_param,
    )
    if is_new and inviter_telegram_id:
        try:
            await bot.send_message(
                inviter_telegram_id,
                (
                    f"🎉 По вашей реферальной ссылке зарегистрировался пользователь.\n"
                    f"Бонус: +{settings.referral_bonus_days} дня к вашему сроку VPN."
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Cannot deliver referral bonus notification to %s: %s", inviter_telegram_id, exc)

    if not await _is_subscribed(bot, settings, message.from_user.id):
        await _send_subscription_gate(message, settings, edit=False)
        return

    if not await _has_accepted_terms(session, user.id):
        await _send_terms_gate(message, settings, edit=False)
        return

    if is_new:
        await user_service.activate_vpn_if_needed(user)

    bot_info = await bot.get_me()
    text = (
        "👋 Добро пожаловать в kVPN.\n\n"
        "Здесь лучший VPN по доступной цене\n"
        f"Тариф: {settings.month_price_rub} ₽ / 30 дней."
    )
    if is_new:
        text += f"\n\n🎁 Стартовый бонус: {settings.trial_days} день."
    text += f"\n\nКАЖДЫЙ ПРИГЛАШЕННЫЙ ПОЛЬЗОВАТЕЛЬ = +{settings.referral_bonus_days} бесплатных дня.\n\nРеферальная ссылка: https://t.me/{bot_info.username}?start=ref_{user.referral_code}"
    await message.answer(text, reply_markup=main_menu(is_admin=settings.is_admin(message.from_user.id)))


@user_router.callback_query(F.data == "menu:back")
async def menu_back_handler(
    callback: CallbackQuery,
    settings: Settings,
    session: AsyncSession,
    bot: Bot,
) -> None:
    user = await _get_user(session, callback.from_user.id)
    if user is None:
        await callback.answer("Пользователь не найден. Нажмите /start", show_alert=True)
        return
    if not await _ensure_access_for_callback(callback, session, settings, bot, user):
        return

    bot_info = await bot.get_me()
    text = (
        "👋 Добро пожаловать в kVPN.\n\n"
        "Здесь лучший VPN по доступной цене\n"
        f"Тариф: {settings.month_price_rub} ₽ / 30 дней."
    )
    text += f"\n\nКАЖДЫЙ ПРИГЛАШЕННЫЙ ПОЛЬЗОВАТЕЛЬ = +{settings.referral_bonus_days} бесплатных дня.\n\nРеферальная ссылка: https://t.me/{bot_info.username}?start=ref_{user.referral_code}"

    await callback.message.edit_text(
        text,
        reply_markup=main_menu(is_admin=settings.is_admin(callback.from_user.id)),
    )
    await callback.answer()


@user_router.callback_query(F.data == "menu:info")
async def info_handler(
    callback: CallbackQuery,
    session: AsyncSession,
    user_service: UserService,
    bot: Bot,
    settings: Settings,
) -> None:
    user = await _get_user(session, callback.from_user.id)
    if user is None:
        await callback.answer("Пользователь не найден. Нажмите /start", show_alert=True)
        return
    if not await _ensure_access_for_callback(callback, session, settings, bot, user):
        return
    text = user_service.build_status_text(user)
    await callback.message.edit_text(
        text,
        reply_markup=info_menu(
            is_admin=settings.is_admin(callback.from_user.id),
            support_url=settings.support_url,
            rules_url=settings.rules_url,
        ),
    )
    await callback.answer()


@user_router.callback_query(F.data == "menu:balance")
async def balance_handler(callback: CallbackQuery, session: AsyncSession, settings: Settings, bot: Bot) -> None:
    user = await _get_user(session, callback.from_user.id)
    if user is None:
        await callback.answer("Пользователь не найден. Нажмите /start", show_alert=True)
        return
    if not await _ensure_access_for_callback(callback, session, settings, bot, user):
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
    bot: Bot,
) -> None:
    user = await _get_user(session, callback.from_user.id)
    if user is None:
        await callback.answer("Пользователь не найден. Нажмите /start", show_alert=True)
        return
    if not await _ensure_access_for_callback(callback, session, settings, bot, user):
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
async def topup_menu_handler(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    bot: Bot,
) -> None:
    user = await _get_user(session, callback.from_user.id)
    if user is None:
        await callback.answer("Пользователь не найден. Нажмите /start", show_alert=True)
        return
    if not await _ensure_access_for_callback(callback, session, settings, bot, user):
        return
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
    bot: Bot,
) -> None:
    user = await _get_user(session, callback.from_user.id)
    if user is None:
        await callback.answer("Пользователь не найден. Нажмите /start", show_alert=True)
        return
    if not await _ensure_access_for_callback(callback, session, settings, bot, user):
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
    bot: Bot,
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
    if not await _ensure_access_for_callback(callback, session, settings, bot, user):
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
    bot: Bot,
) -> None:
    user = await _get_user(session, callback.from_user.id)
    if user is None:
        await callback.answer("Пользователь не найден. Нажмите /start", show_alert=True)
        return
    if not await _ensure_access_for_callback(callback, session, settings, bot, user):
        return

    link = xray_service.build_vless_link(str(user.uuid), user.telegram_id)
    await callback.message.edit_text(
        f"🔗 Ваша постоянная VLESS-ссылка:\n\n<code>{escape(link)}</code>\n\nИнструкция по подключению на разных устройствах: https://t.me/kvpnpublic/2",
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
    if not await _ensure_access_for_callback(callback, session, settings, bot, user):
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
async def balance_shortcut(message: Message, session: AsyncSession, settings: Settings, bot: Bot) -> None:
    user = await _get_user(session, message.from_user.id)
    if not user:
        await message.answer("Нажмите /start")
        return
    if not await _ensure_access_for_message(message, session, settings, bot, user):
        return
    await message.answer(f"Баланс: {user.balance} ₽")


@user_router.message(F.text == "/pay")
async def pay_shortcut(message: Message, settings: Settings, session: AsyncSession, bot: Bot) -> None:
    user = await _get_user(session, message.from_user.id)
    if not user:
        await message.answer("Нажмите /start")
        return
    if not await _ensure_access_for_message(message, session, settings, bot, user):
        return
    await message.answer(
        f"Стоимость месяца: {Decimal(str(settings.month_price_rub))} ₽\nНажмите «Пополнить» в меню.",
        reply_markup=main_menu(is_admin=settings.is_admin(message.from_user.id)),
    )


@user_router.callback_query(F.data == "gate:check_subscription")
async def gate_check_subscription(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    bot: Bot,
    user_service: UserService,
) -> None:
    user = await _get_user(session, callback.from_user.id)
    if user is None:
        await callback.answer("Нажмите /start", show_alert=True)
        return

    if not await _is_subscribed(bot, settings, callback.from_user.id):
        await _send_subscription_gate(callback.message, settings, edit=True)
        await callback.answer("Вы пока не подписались на канал.", show_alert=True)
        return

    if not await _has_accepted_terms(session, user.id):
        await _send_terms_gate(callback.message, settings, edit=True)
        await callback.answer("Подписка подтверждена.", show_alert=False)
        return

    await user_service.activate_vpn_if_needed(user)
    bot_info = await bot.get_me()
    text = (
        "👋 Добро пожаловать в kVPN.\n\n"
        "Здесь лучший VPN по доступной цене\n"
        f"Тариф: {settings.month_price_rub} ₽ / 30 дней."
    )
    text += f"\n\nКАЖДЫЙ ПРИГЛАШЕННЫЙ ПОЛЬЗОВАТЕЛЬ = +{settings.referral_bonus_days} бесплатных дня.\n\nРеферальная ссылка: https://t.me/{bot_info.username}?start=ref_{user.referral_code}"
    await callback.message.edit_text(
        text,
        reply_markup=main_menu(is_admin=settings.is_admin(callback.from_user.id)),
    )
    await callback.answer("Готово")


@user_router.callback_query(F.data == "gate:accept_terms")
async def gate_accept_terms(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    bot: Bot,
    user_service: UserService,
) -> None:
    user = await _get_user(session, callback.from_user.id)
    if user is None:
        await callback.answer("Нажмите /start", show_alert=True)
        return

    if not await _is_subscribed(bot, settings, callback.from_user.id):
        await _send_subscription_gate(callback.message, settings, edit=True)
        await callback.answer("Сначала подпишитесь на канал.", show_alert=True)
        return

    await UserPolicyRepository(session).accept_terms(user.id)
    await user_service.activate_vpn_if_needed(user)

    bot_info = await bot.get_me()
    text = (
        "👋 Добро пожаловать в kVPN.\n\n"
        "Здесь лучший VPN по доступной цене\n"
        f"Тариф: {settings.month_price_rub} ₽ / 30 дней."
    )
    text += f"\n\nКАЖДЫЙ ПРИГЛАШЕННЫЙ ПОЛЬЗОВАТЕЛЬ = +{settings.referral_bonus_days} бесплатных дня.\n\nРеферальная ссылка: https://t.me/{bot_info.username}?start=ref_{user.referral_code}"
    await callback.message.edit_text(
        text,
        reply_markup=main_menu(is_admin=settings.is_admin(callback.from_user.id)),
    )
    await callback.answer("Спасибо")
