"""
handlers/group.py — работа бота в групповом чате:

  * при добавлении бота в группу — приветствие с инлайн-меню;
  * команды /start, /menu, /today, /tomorrow;
  * кнопки «ДЗ на сегодня» / «ДЗ на завтра».

В группе доступны ТОЛЬКО домашние задания (без расписания).
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


async def _send_group_menu(message: Message) -> None:
    await message.answer(WELCOME, reply_markup=group_menu())


async def _show_homework(cq: CallbackQuery, offset: int) -> None:
    target = datetime.now(TZ).date() + timedelta(days=offset)
    label = "сегодня" if offset == 0 else "завтра"
    items = await crud.list_homework_on(target)
    await cq.message.edit_text(
        render_group_homework(items, target, label),
        reply_markup=group_menu(),
    )


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
        await event.bot.send_message(event.chat.id, WELCOME, reply_markup=group_menu())


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
    await msg.answer(
        render_group_homework(items, target, "сегодня"),
        reply_markup=group_menu(),
    )


@group_router.message(Command("tomorrow"))
async def group_tomorrow_cmd(msg: Message) -> None:
    target = datetime.now(TZ).date() + timedelta(days=1)
    items = await crud.list_homework_on(target)
    await msg.answer(
        render_group_homework(items, target, "завтра"),
        reply_markup=group_menu(),
    )


# ---------------------------------------------------------------------------
# Кнопки
# ---------------------------------------------------------------------------
@group_router.callback_query(F.data == "grp:today")
async def grp_today(cq: CallbackQuery) -> None:
    await cq.answer()
    await _show_homework(cq, 0)


@group_router.callback_query(F.data == "grp:tomorrow")
async def grp_tomorrow(cq: CallbackQuery) -> None:
    await cq.answer()
    await _show_homework(cq, 1)
