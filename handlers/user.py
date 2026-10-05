"""
handlers/user.py — хэндлеры обычного пользователя:
  /start, /menu, «Расписание на сегодня/завтра», «Домашнее задание».
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

import parser as schedule_parser
from config import ADMIN_IDS, CALL_SCHEDULE, GROUP_NAME, TZ
from database import crud
from keyboards import main_menu
from services import active_hw_map, ensure_schedule, get_changes_map
from utils import format_date_ru, render_homework_list, render_schedule_text

user_router = Router()


# ---------------------------------------------------------------------------
# Старт / меню
# ---------------------------------------------------------------------------
@user_router.message(CommandStart())
async def cmd_start(msg: Message) -> None:
    await _greet(msg, greeting=True)


@user_router.message(Command("menu"))
async def cmd_menu(msg: Message) -> None:
    await _greet(msg, greeting=False)


async def _greet(msg: Message, greeting: bool) -> None:
    tg = msg.from_user
    user = await crud.get_or_create_user(tg.id, tg.username, tg.full_name)

    # Пользователь из ADMIN_IDS автоматически получает роль admin.
    if tg.id in ADMIN_IDS and user.role != "admin":
        await crud.set_user_role(tg.id, "admin")
        user.role = "admin"

    privileged = user.role in ("admin", "moderator")
    if greeting:
        text = (
            f"👋 Привет, {tg.first_name}!\n"
            f"Я бот расписания группы <b>{GROUP_NAME}</b>.\n"
            f"Выберите действие в меню ниже."
        )
    else:
        text = "Главное меню:"
    await msg.answer(text, reply_markup=main_menu(privileged))


# ---------------------------------------------------------------------------
# Расписание
# ---------------------------------------------------------------------------
@user_router.message(F.text == "📅 Расписание на сегодня")
async def schedule_today(msg: Message) -> None:
    await _send_schedule(msg, 0)


@user_router.message(F.text == "📅 Расписание на завтра")
async def schedule_tomorrow(msg: Message) -> None:
    await _send_schedule(msg, 1)


async def _send_schedule(msg: Message, offset: int) -> None:
    target = datetime.now(TZ).date() + timedelta(days=offset)

    data = await ensure_schedule()
    anchor_date = date.fromisoformat(data["anchor_date"])
    week = schedule_parser.week_type_for(target, anchor_date, data["anchor_week"])

    lessons = data.get("days", {}).get(str(target.weekday()), [])

    header = f"📅 <b>{format_date_ru(target)}</b>\nНеделя: <b>{week}</b>"

    if not lessons:
        await msg.answer(header + "\n\n—")
        return

    hw_by_subject = await active_hw_map(target)
    changes_map = await get_changes_map(target)
    body = render_schedule_text(lessons, week, CALL_SCHEDULE, hw_by_subject, changes_map)
    await msg.answer(header + "\n\n" + body)


# ---------------------------------------------------------------------------
# Домашние задания
# ---------------------------------------------------------------------------
@user_router.message(F.text == "📚 Домашнее задание")
async def homework_list(msg: Message) -> None:
    today = datetime.now(TZ).date()
    items = await crud.list_active_homework(today)
    if not items:
        await msg.answer("Активных домашних заданий нет 🎉")
        return
    await msg.answer(render_homework_list(items))
