from aiogram import Dispatcher

from app.bot.handlers.admin import admin_router
from app.bot.handlers.user import user_router


def register_handlers(dp: Dispatcher) -> None:
    dp.include_router(user_router)
    dp.include_router(admin_router)
