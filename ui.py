"""
ui.py — состояние интерфейса.

Экран — это сообщения с содержимым раздела (расписание, ДЗ, панель). При
переключении раздела предыдущий экран удаляется целиком.

Отдельно живёт сообщение, несущее reply-клавиатуру (нижнее меню). Его удалять
нельзя: Telegram прячет клавиатуру, если исчезает сообщение, которым она была
установлена. Поэтому меню держится на постоянном сообщении, а разделы
переключаются ниже.
"""
from __future__ import annotations

from typing import Any, Iterable

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, Message

# user_id -> id сообщений текущего экрана (содержимое раздела)
_views: dict[int, list[int]] = {}

# user_id -> id сообщения, несущего reply-клавиатуру
_menus: dict[int, int] = {}

# Пустая клавиатура: снимает инлайн-кнопки при редактировании.
EMPTY_KB = InlineKeyboardMarkup(inline_keyboard=[])


def track(user_id: int, message_ids: Iterable[int]) -> None:
    """Запомнить сообщения текущего экрана пользователя."""
    _views[user_id] = [int(mid) for mid in message_ids]


async def delete_safe(bot: Bot, chat_id: int, message_id: int) -> None:
    """Удалить сообщение, игнорируя ошибки (уже удалено, нет прав, старше 48 часов)."""
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass


async def clear(bot: Bot, chat_id: int, user_id: int) -> None:
    """Удалить все сообщения текущего экрана."""
    for message_id in _views.pop(user_id, []):
        await delete_safe(bot, chat_id, message_id)


async def set_menu(
    bot: Bot,
    chat_id: int,
    user_id: int,
    text: str,
    reply_markup: Any,
) -> Message:
    """
    Отправить сообщение с нижним меню. Предыдущее такое сообщение заменяется.
    Это сообщение не входит в экран и не удаляется при смене раздела.
    """
    previous = _menus.get(user_id)
    if previous is not None:
        await delete_safe(bot, chat_id, previous)
    sent = await bot.send_message(chat_id, text, reply_markup=reply_markup)
    _menus[user_id] = sent.message_id
    return sent


async def show(
    bot: Bot,
    chat_id: int,
    user_id: int,
    text: str,
    reply_markup: Any = None,
) -> Message:
    """Удалить предыдущий экран и отправить новый (одно сообщение)."""
    await clear(bot, chat_id, user_id)
    sent = await bot.send_message(chat_id, text, reply_markup=reply_markup)
    track(user_id, [sent.message_id])
    return sent


async def update(
    bot: Bot,
    chat_id: int,
    user_id: int,
    text: str,
    reply_markup: Any = EMPTY_KB,
) -> None:
    """Обновить экран редактированием. Экран из нескольких сообщений пересоздаётся."""
    ids = _views.get(user_id, [])
    if len(ids) != 1:
        await show(bot, chat_id, user_id, text, reply_markup)
        return
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=ids[0],
            text=text,
            reply_markup=reply_markup,
        )
    except TelegramBadRequest as exc:
        # текст не изменился — перерисовывать нечего
        if "message is not modified" in str(exc):
            return
        await show(bot, chat_id, user_id, text, reply_markup)
    except Exception:
        await show(bot, chat_id, user_id, text, reply_markup)
