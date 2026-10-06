"""
ui.py — состояние интерфейса: один актуальный экран на пользователя.

Разделы (расписание, ДЗ, админ-панель, шаги опроса) обновляют одно и то же
сообщение: предыдущее удаляется, новое встаёт на его место. Чат не засоряется,
разделы переключаются как вкладки.
"""
from __future__ import annotations

from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, Message

# user_id -> message_id текущего экрана
_views: dict[int, int] = {}

# Пустая клавиатура: снимает инлайн-кнопки при редактировании.
EMPTY_KB = InlineKeyboardMarkup(inline_keyboard=[])


def track(user_id: int, message_id: int) -> None:
    """Запомнить сообщение как текущий экран пользователя."""
    _views[user_id] = message_id


async def delete_safe(bot: Bot, chat_id: int, message_id: int) -> None:
    """Удалить сообщение, игнорируя ошибки (уже удалено, нет прав, старше 48 часов)."""
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass


async def show(
    bot: Bot,
    chat_id: int,
    user_id: int,
    text: str,
    reply_markup: Any = None,
) -> Message:
    """Удалить предыдущий экран и отправить новый."""
    previous = _views.get(user_id)
    if previous is not None:
        await delete_safe(bot, chat_id, previous)
    sent = await bot.send_message(chat_id, text, reply_markup=reply_markup)
    track(user_id, sent.message_id)
    return sent


async def update(
    bot: Bot,
    chat_id: int,
    user_id: int,
    text: str,
    reply_markup: Any = EMPTY_KB,
) -> None:
    """Обновить текущий экран редактированием; если экрана нет — отправить новый."""
    message_id = _views.get(user_id)
    if message_id is None:
        await show(bot, chat_id, user_id, text, reply_markup)
        return
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
        )
    except TelegramBadRequest as exc:
        # текст не изменился — ничего делать не нужно
        if "message is not modified" in str(exc):
            return
        await show(bot, chat_id, user_id, text, reply_markup)
    except Exception:
        await show(bot, chat_id, user_id, text, reply_markup)
