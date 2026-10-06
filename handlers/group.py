"""
handlers/group.py — работа бота в групповом чате:

  * при добавлении бота в группу — приветствие с инлайн-меню;
  * команды /start, /menu, /today, /tomorrow;
  * кнопки «ДЗ на сегодня» / «ДЗ на завтра».

В группе доступны ТОЛЬКО домашние задания (без расписания).
Вложения ДЗ отправляются вместе с текстом; предыдущие вложения удаляются.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message

from config import GROUP_NAME, TZ
from database import crud
from keyboards import group_menu
from services import annotate_homework, send_homework_media
from utils import render_group_homework

group_router = Router()

# Обрабатываем только групповые чаты.
group_router.message.filter(F.chat.type.in_({"group", "supergroup"}))
group_router.callback_query.filter(F.message.chat.type.in_({"group", "supergroup"}))

WELCOME = (
    f"Бот группы <b>{GROUP_NAME}</b>.\n"
    "Здесь можно посмотреть домашние задания на сегодня и завтра.\n"
    "Расписание доступно в личном чате с ботом."
)

# chat_id -> id сообщений с вложениями (чтобы подчищать предыдущую выдачу)
_media: dict[int, list[int]] = {}


async def _clear_media(bot, chat_id: int) -> None:
    for message_id in _media.pop(chat_id, []):
        try:
            await bot.delete_message(chat_id, message_id)
        except Exception:
            pass


async def _remember(chat) -> None:
    """Запоминает группу, чтобы сюда приходили уведомления о заменах."""
    try:
        await crud.add_group_chat(chat.id, getattr(chat, "title", None))
    except Exception:
        pass


async def _send_group_menu(message: Message) -> None:
    await _remember(message.chat)
    await message.answer(WELCOME, reply_markup=group_menu())


async def _show_homework(
    bot,
    chat,
    target,
    label: str,
    items: list,
    menu_message: Message | None = None,
) -> None:
    """Показывает ДЗ: текст (в меню или новым сообщением) плюс вложения."""
    await _remember(chat)
    chat_id = chat.id
    now = datetime.now(TZ)
    items = [hw for hw, passed in annotate_homework(items, now) if not passed]

    text = render_group_homework(items, target, label)

    if menu_message is not None:
        await menu_message.edit_text(text, reply_markup=group_menu())
    else:
        await bot.send_message(chat_id, text, reply_markup=group_menu())

    await _clear_media(bot, chat_id)
    ids: list[int] = []
    for hw in items:
        ids.extend(await send_homework_media(bot, chat_id, hw))
    _media[chat_id] = ids


# ---------------------------------------------------------------------------
# Бота добавили в группу
# ---------------------------------------------------------------------------
@group_router.my_chat_member()
async def on_my_chat_member(event: ChatMemberUpdated) -> None:
    if event.chat.type not in ("group", "supergroup"):
        return
    new = event.new_chat_member.status
    old = event.old_chat_member.status
    added = new in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR)
    was_present = old in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR)

    if added and not was_present:
        # запоминаем группу, чтобы слать сюда уведомления о заменах
        await crud.add_group_chat(event.chat.id, event.chat.title)
        await event.bot.send_message(event.chat.id, WELCOME, reply_markup=group_menu())
    elif was_present and not added:
        await crud.remove_group_chat(event.chat.id)


# ---------------------------------------------------------------------------
# Команды
# ---------------------------------------------------------------------------
@group_router.message(CommandStart())
async def group_start(msg: Message) -> None:
    await _send_group_menu(msg)


@group_router.message(Command("menu"))
async def group_menu_cmd(msg: Message) -> None:
    await _send_group_menu(msg)


@group_router.message(Command("today"))
async def group_today_cmd(msg: Message) -> None:
    target = datetime.now(TZ).date()
    items = await crud.list_homework_on(target)
    await _show_homework(msg.bot, msg.chat, target, "сегодня", items)


@group_router.message(Command("tomorrow"))
async def group_tomorrow_cmd(msg: Message) -> None:
    target = datetime.now(TZ).date() + timedelta(days=1)
    items = await crud.list_homework_on(target)
    await _show_homework(msg.bot, msg.chat, target, "завтра", items)


# ---------------------------------------------------------------------------
# Кнопки
# ---------------------------------------------------------------------------
@group_router.callback_query(F.data == "grp:today")
async def grp_today(cq: CallbackQuery) -> None:
    await cq.answer()
    target = datetime.now(TZ).date()
    items = await crud.list_homework_on(target)
    await _show_homework(
        cq.bot, cq.message.chat, target, "сегодня", items, menu_message=cq.message
    )


@group_router.callback_query(F.data == "grp:tomorrow")
async def grp_tomorrow(cq: CallbackQuery) -> None:
    await cq.answer()
    target = datetime.now(TZ).date() + timedelta(days=1)
    items = await crud.list_homework_on(target)
    await _show_homework(
        cq.bot, cq.message.chat, target, "завтра", items, menu_message=cq.message
    )
