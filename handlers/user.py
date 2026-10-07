"""
handlers/user.py — хэндлеры обычного пользователя (личный чат):
  /start (приветствие с Telegram ID), /menu, «Расписание на сегодня/завтра»,
  «ДЗ на сегодня/завтра».

Разделы показываются в одном экране: предыдущий удаляется. Если у ДЗ есть
вложения, они отправляются вместе с текстом.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

import parser as schedule_parser
import ui
from config import ADMIN_IDS, CALL_SCHEDULE, GROUP_NAME, TZ
from database import crud
from keyboards import main_menu
from services import (
    annotate_homework,
    ensure_schedule,
    get_changes_map,
    send_homework_media,
)
from utils import (
    format_date_ru,
    normalize_subject,
    render_homework_day,
    render_schedule_text,
)

user_router = Router()

# Личный чат (в группах работает отдельный group_router).
user_router.message.filter(F.chat.type == "private")


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
        if user.role == "admin":
            extra = "Вы являетесь главным администратором."
        elif user.role == "moderator":
            extra = "Вы являетесь модератором."
        else:
            extra = "Отправьте свой ID администратору, чтобы получить права модератора."
        text = (
            f"Привет, {tg.first_name}!\n"
            f"Это бот расписания и домашних заданий группы <b>{GROUP_NAME}</b>.\n\n"
            f"Ваш Telegram ID: <code>{tg.id}</code>\n"
            f"{extra}\n\n"
            f"Выберите действие в меню ниже."
        )
    else:
        text = "Главное меню:"

    await ui.delete_safe(msg.bot, msg.chat.id, msg.message_id)
    await ui.clear(msg.bot, msg.chat.id, tg.id)
    await ui.set_menu(msg.bot, msg.chat.id, tg.id, text, main_menu(privileged))


# ---------------------------------------------------------------------------
# Расписание
# ---------------------------------------------------------------------------
@user_router.message(F.text == "Расписание на сегодня")
async def schedule_today(msg: Message) -> None:
    await _send_schedule(msg, 0)


@user_router.message(F.text == "Расписание на завтра")
async def schedule_tomorrow(msg: Message) -> None:
    await _send_schedule(msg, 1)


async def _send_schedule(msg: Message, offset: int) -> None:
    user_id = msg.from_user.id
    chat_id = msg.chat.id
    await ui.delete_safe(msg.bot, chat_id, msg.message_id)

    target = datetime.now(TZ).date() + timedelta(days=offset)

    data = await ensure_schedule()
    anchor_date = date.fromisoformat(data["anchor_date"])
    week = schedule_parser.week_type_for(target, anchor_date, data["anchor_week"])

    lessons = data.get("days", {}).get(str(target.weekday()), [])

    header = f"<b>{format_date_ru(target)}</b>\nНеделя: <b>{week}</b>"

    if not lessons:
        await ui.show(msg.bot, chat_id, user_id, header + "\n\n—")
        return

    # В расписании показываем задания, срок сдачи которых приходится на этот день.
    day_homework = await crud.list_homework_on(target)
    with_files = await crud.homework_ids_with_files([hw.id for hw in day_homework])

    homework: dict[str, object] = {}
    for hw in day_homework:
        homework.setdefault(normalize_subject(hw.subject), hw)

    display = {
        key: (hw.text.strip() or ("(вложение)" if hw.id in with_files else "—"))
        for key, hw in homework.items()
    }

    changes_map = await get_changes_map(target)
    body = render_schedule_text(lessons, week, CALL_SCHEDULE, display, changes_map)

    await ui.clear(msg.bot, chat_id, user_id)
    sent = await msg.bot.send_message(chat_id, header + "\n\n" + body)
    ids = [sent.message_id]

    # Вложения заданий этого дня (каждое по одному разу).
    seen: set[int] = set()
    for lesson in lessons:
        variant = lesson.get("variants", {}).get(week)
        if not variant:
            continue
        hw = homework.get(normalize_subject(variant.get("subject")))
        if hw is not None and hw.id not in seen:
            seen.add(hw.id)
            ids.extend(await send_homework_media(msg.bot, chat_id, hw))

    ui.track(user_id, ids)


# ---------------------------------------------------------------------------
# Домашние задания
# ---------------------------------------------------------------------------
@user_router.message(F.text == "ДЗ на сегодня")
async def homework_today(msg: Message) -> None:
    await _send_homework(msg, 0)


@user_router.message(F.text == "ДЗ на завтра")
async def homework_tomorrow(msg: Message) -> None:
    await _send_homework(msg, 1)


@user_router.message(F.text == "Домашнее задание")
async def homework_old_button(msg: Message) -> None:
    """Кнопка из прежней версии меню: показываем ДЗ на сегодня."""
    await _send_homework(msg, 0)


async def _send_homework(msg: Message, offset: int) -> None:
    user_id = msg.from_user.id
    chat_id = msg.chat.id
    label = "сегодня" if offset == 0 else "завтра"
    await ui.delete_safe(msg.bot, chat_id, msg.message_id)

    now = datetime.now(TZ)
    target = now.date() + timedelta(days=offset)

    items = await crud.list_homework_on(target)
    # задания, срок по которым уже прошёл, в разделе ДЗ не показываем
    items = [hw for hw, passed in annotate_homework(items, now) if not passed]
    with_files = await crud.homework_ids_with_files([hw.id for hw in items])

    await ui.clear(msg.bot, chat_id, user_id)
    if not items:
        await ui.show(msg.bot, chat_id, user_id, f"Домашних заданий на {label} нет.")
        return

    sent = await msg.bot.send_message(
        chat_id,
        render_homework_day(items, target, label, with_files),
    )
    ids = [sent.message_id]
    for hw in items:
        ids.extend(await send_homework_media(msg.bot, chat_id, hw))
    ui.track(user_id, ids)
