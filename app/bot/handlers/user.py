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
    vpn_tariffs_menu,
)
from app.config import Settings
from app.db.repositories import ReferralRepository, UserPolicyRepository, UserRepository
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


def _referral_promo_text(settings: Settings) -> str:
    return (
        f"КАЖДЫЙ ПРИГЛАШЕННЫЙ ПОЛЬЗОВАТЕЛЬ = +{settings.referral_bonus_days} бесплатных дня.\n\n"
        f"{settings.referral_paid_invites_for_year_reward} оплативших подписку рефералов = "
        f"+{settings.referral_year_reward_days} бесплатных дней VPN."
    )


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
    text += f"\n\n{_referral_promo_text(settings)}\n\nРеферальная ссылка: https://t.me/{bot_info.username}?start=ref_{user.referral_code}"
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
    text += f"\n\n{_referral_promo_text(settings)}\n\nРеферальная ссылка: https://t.me/{bot_info.username}?start=ref_{user.referral_code}"

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


@user_router.callback_query(F.data.in_({"menu:tariffs", "menu:buy_month"}))
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
    if not await _ensure_access_for_callback(callback, session, settings, callback.bot, user):
        return

    await callback.message.edit_text(
        billing_service.build_tariffs_text(),
        reply_markup=vpn_tariffs_menu(),
    )
    await callback.answer()


@user_router.callback_query(F.data.startswith("tariff:"))
async def tariff_purchase_handler(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    billing_service: BillingService,
    payment_service: TelegaPayService,
    bot: Bot,
) -> None:
    user = await _get_user(session, callback.from_user.id)
    if user is None:
        await callback.answer("Пользователь не найден. Нажмите /start", show_alert=True)
        return
    if not await _ensure_access_for_callback(callback, session, settings, bot, user):
        return

    tariff_code = callback.data.split(":", 1)[1].strip()
    try:
        status, text, payment_url = await billing_service.purchase_tariff(
            session=session,
            user=user,
            tariff_code=tariff_code,
            payment_service=payment_service,
            bot=callback.bot,
        )
    except (PaymentProviderError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if status == "error":
        await callback.answer(text, show_alert=True)
        return
    if status == "payment_required" and payment_url:
        await callback.message.edit_text(text, reply_markup=payment_link_menu(payment_url))
        await callback.answer()
        return

    await callback.message.edit_text(
        text,
        reply_markup=main_menu(is_admin=settings.is_admin(callback.from_user.id)),
    )
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
            "Перейдите по ссылке и оплатите через СБП.\n"
            "Бот проверяет статус автоматически."
        ),
        reply_markup=payment_link_menu(payment_url),
    )
    await callback.answer()


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
        (
            "🔐 <b>Мой VPN</b>\n\n"
            "Для простого подключения на любом устройстве скачайте "
            "<a href=\"https://happ.su\">Happ</a>.\n\n"
            "<b>Инструкция по подключению:</b>\n"
            "1) Скопируйте ваш VPN-ключ целиком (текст ниже).\n"
            "2) Откройте Happ.\n"
            "3) Нажмите на плюсик (+).\n"
            "4) Выберите «Вставить из буфера».\n"
            "5) Подключитесь к созданному профилю.\n\n"
            "Ваш VPN-ключ:\n"
            f"<code>{escape(link)}</code>\n\n"
            "Если вы используете другое приложение, можно подключиться этим же ключом.\n\n"
            "Доп. инструкция: https://t.me/kvpnpublic/2\n\n"
            f"Техническая поддержка, если необходима помощь: {settings.support_url}"
        ),
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

    user_repo = UserRepository(session)
    invited_count = await user_repo.count_referrals(user.id)
    paid_count = await ReferralRepository(session).count_invited_with_subscription_payment(user.id)
    threshold = settings.referral_paid_invites_for_year_reward
    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start=ref_{user.referral_code}"
    text = (
        "🎁 Реферальная программа\n\n"
        f"За каждого приглашенного: +{settings.referral_bonus_days} дня.\n"
        f"{threshold} оплативших подписку рефералов: +{settings.referral_year_reward_days} дней.\n\n"
        f"Приглашено: {invited_count}\n"
        f"Оплатили подписку: {paid_count}/{threshold}\n\n"
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
        (
            f"Стоимость 1 месяца: {Decimal(str(settings.month_price_rub))} ₽\n"
            "Нажмите «Купить VPN» в меню, чтобы выбрать тариф."
        ),
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
    text += f"\n\n{_referral_promo_text(settings)}\n\nРеферальная ссылка: https://t.me/{bot_info.username}?start=ref_{user.referral_code}"
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
    text += f"\n\n{_referral_promo_text(settings)}\n\nРеферальная ссылка: https://t.me/{bot_info.username}?start=ref_{user.referral_code}"
    await callback.message.edit_text(
        text,
        reply_markup=main_menu(is_admin=settings.is_admin(callback.from_user.id)),
    )
    await callback.answer("Спасибо")
