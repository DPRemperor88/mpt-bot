"""
keyboards.py — все клавиатуры бота (reply и inline).
"""
from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)


# ---------------------------------------------------------------------------
# Главное меню (Reply Keyboard) — личный чат
# ---------------------------------------------------------------------------
def main_menu(is_privileged: bool = False) -> ReplyKeyboardMarkup:
    """Главное меню. Кнопка админ-панели видна только админам/модераторам."""
    keyboard = [
        [
            KeyboardButton(text="Расписание на сегодня"),
            KeyboardButton(text="Расписание на завтра"),
        ],
        [
            KeyboardButton(text="ДЗ на сегодня"),
            KeyboardButton(text="ДЗ на завтра"),
        ],
        [KeyboardButton(text="Все ДЗ")],
    ]
    if is_privileged:
        keyboard.append([KeyboardButton(text="Админ-панель")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


# ---------------------------------------------------------------------------
# Меню группового чата (Inline Keyboard) — только ДЗ
# ---------------------------------------------------------------------------
def group_menu() -> InlineKeyboardMarkup:
    """В группе доступны только домашние задания на сегодня и завтра."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="ДЗ на сегодня", callback_data="grp:today")],
            [InlineKeyboardButton(text="ДЗ на завтра", callback_data="grp:tomorrow")],
        ]
    )


# ---------------------------------------------------------------------------
# Админ-панель (Reply Keyboard) — режим админа и модератора
# ---------------------------------------------------------------------------
def admin_reply_menu(is_admin: bool = False) -> ReplyKeyboardMarkup:
    """Нижнее меню в режиме админ-панели."""
    keyboard = [
        [KeyboardButton(text="Добавить ДЗ"), KeyboardButton(text="Список ДЗ")],
        [KeyboardButton(text="Участники"), KeyboardButton(text="Журнал")],
    ]
    if is_admin:
        keyboard.append(
            [KeyboardButton(text="Добавить модератора"), KeyboardButton(text="Рассылка")]
        )
    keyboard.append([KeyboardButton(text="Выйти")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


# ---------------------------------------------------------------------------
# Выбор дисциплины (шаг 1 добавления ДЗ)
# ---------------------------------------------------------------------------
# Названия дисциплин бывают длинными и не влезают в 64-байтовый лимит
# callback_data, поэтому в callback передаём только порядковый номер,
# а сам список храним в FSM-состоянии.
def subjects_choose_keyboard(subjects: list[str]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=s, callback_data=f"hwsubj:{i}")]
        for i, s in enumerate(subjects)
    ]
    buttons.append(
        [InlineKeyboardButton(text="Ввести вручную", callback_data="hwsubj:manual")]
    )
    buttons.append([InlineKeyboardButton(text="Отмена", callback_data="hwsubj:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ---------------------------------------------------------------------------
# Шаг 3 добавления ДЗ: текст и файлы собраны
# ---------------------------------------------------------------------------
def hw_text_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Готово", callback_data="hwdone")],
            [InlineKeyboardButton(text="Отмена", callback_data="hwtext:cancel")],
        ]
    )


# ---------------------------------------------------------------------------
# Выбор даты сдачи (шаг 2 добавления ДЗ)
# ---------------------------------------------------------------------------
def due_date_choose_keyboard(dates: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    """dates: список (iso_date, label). В callback — индекс."""
    buttons = [
        [InlineKeyboardButton(text=label, callback_data=f"hwdate:{i}")]
        for i, (_iso, label) in enumerate(dates)
    ]
    buttons.append([InlineKeyboardButton(text="Отмена", callback_data="hwdate:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)
