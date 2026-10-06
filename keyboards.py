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
        [KeyboardButton(text="Расписание на сегодня")],
        [KeyboardButton(text="Расписание на завтра")],
        [KeyboardButton(text="Домашнее задание")],
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
# Админ-панель (Inline Keyboard)
# ---------------------------------------------------------------------------
def admin_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="Добавить ДЗ", callback_data="adm:add_hw")],
        [InlineKeyboardButton(text="Список ДЗ", callback_data="adm:list_hw")],
        [InlineKeyboardButton(text="Участники", callback_data="adm:users")],
    ]
    if is_admin:
        rows.append(
            [InlineKeyboardButton(text="Добавить модератора", callback_data="adm:add_mod")]
        )
        rows.append(
            [InlineKeyboardButton(text="Рассылка", callback_data="adm:broadcast")]
        )
    rows.append([InlineKeyboardButton(text="Закрыть", callback_data="adm:close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
