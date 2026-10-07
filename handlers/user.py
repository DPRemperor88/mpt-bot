"""
handlers/user.py — хэндлеры обычного пользователя (личный чат):
  /start (приветствие с Telegram ID), /menu, «Расписание на сегодня/завтра»,
  «ДЗ на сегодня/завтра».

Разделы показываются в одном экране: предыдущий удаляется. Если у ДЗ есть
вложения, они отправляются вместе с текстом.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import ui
from delivery import send_text
from config import ADMIN_IDS, GROUP_NAME, TZ
from database import crud
from keyboards import main_menu, notifications_keyboard
from services import annotate_homework, schedule_day, send_homework_media
from utils import esc, render_homework_all, render_homework_day

user_router = Router()

# Личный чат (в группах работает отдельный group_router).
user_router.message.filter(F.chat.type == "private")
user_router.callback_query.filter(F.message.chat.type == "private")


# ---------------------------------------------------------------------------
# Старт / меню
# ---------------------------------------------------------------------------
@user_router.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext) -> None:
    await state.clear()
    await _greet(msg, greeting=True)


@user_router.message(Command("menu"))
async def cmd_menu(msg: Message, state: FSMContext) -> None:
    await state.clear()
    await _greet(msg, greeting=False)


@user_router.message(Command("cancel"))
async def cmd_cancel(msg: Message, state: FSMContext) -> None:
    await state.clear()
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
            extra = "Роль: главный администратор."
        elif user.role == "moderator":
            extra = "Роль: модератор."
        else:
            extra = "Отправьте свой ID администратору, чтобы стать модератором."
        text = (
            f"Привет, {esc(tg.first_name)}!\n"
            f"Это бот расписания и домашних заданий группы <b>{esc(GROUP_NAME)}</b>.\n\n"
            f"Ваш Telegram ID: <code>{tg.id}</code>\n"
            f"{extra}"
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
    text, day_homework = await schedule_day(target)

    await ui.clear(msg.bot, chat_id, user_id)
    ids = await send_text(msg.bot, chat_id, text)
    for hw in day_homework:
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


@user_router.message(F.text == "Все ДЗ")
async def homework_all(msg: Message) -> None:
    user_id = msg.from_user.id
    chat_id = msg.chat.id
    await ui.delete_safe(msg.bot, chat_id, msg.message_id)

    now = datetime.now(TZ)
    items = await crud.list_upcoming_homework(now.date())
    # закрытые не показываем
    items = [hw for hw, passed in annotate_homework(items, now) if not passed]
    with_files = await crud.homework_ids_with_files([hw.id for hw in items])

    await ui.clear(msg.bot, chat_id, user_id)
    if not items:
        await ui.show(msg.bot, chat_id, user_id, "Заданий нет.")
        return

    ids = await send_text(msg.bot, chat_id, render_homework_all(items, with_files))
    for hw in items:
        ids.extend(await send_homework_media(msg.bot, chat_id, hw))
    ui.track(user_id, ids)


@user_router.message(F.text == "Уведомления")
async def notifications(msg: Message) -> None:
    user_id = msg.from_user.id
    chat_id = msg.chat.id
    await ui.delete_safe(msg.bot, chat_id, msg.message_id)

    settings = await crud.notification_settings(user_id)
    await ui.show(
        msg.bot,
        chat_id,
        user_id,
        "<b>Уведомления:</b>",
        notifications_keyboard(settings),
    )


@user_router.callback_query(F.data.startswith("ntf:"))
async def notification_toggle(cq: CallbackQuery) -> None:
    await cq.answer()
    kind = cq.data.split(":", 1)[1]
    if kind not in crud.NOTIFICATION_KINDS:
        return

    await crud.toggle_notification(cq.from_user.id, kind)
    settings = await crud.notification_settings(cq.from_user.id)

    ui.track(cq.from_user.id, [cq.message.message_id])
    await ui.update(
        cq.bot,
        cq.message.chat.id,
        cq.from_user.id,
        "<b>Уведомления:</b>",
        notifications_keyboard(settings),
    )


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
        await ui.show(msg.bot, chat_id, user_id, "Заданий нет.")
        return

    ids = await send_text(
        msg.bot,
        chat_id,
        render_homework_day(items, target, label, with_files),
    )
    for hw in items:
        ids.extend(await send_homework_media(msg.bot, chat_id, hw))
    ui.track(user_id, ids)
