from aiogram.fsm.state import State, StatesGroup


class AdminStates(StatesGroup):
    wait_balance_user = State()
    wait_add_days = State()
    wait_add_days_all = State()
    wait_remove_days = State()
    wait_ban_user = State()
    wait_unban_user = State()
    wait_bonus = State()
    wait_broadcast_text = State()
