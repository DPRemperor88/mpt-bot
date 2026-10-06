"""
handlers/admin.py — админ-панель (личный чат):
  * добавление ДЗ (FSM: дисциплина → дата → текст/вложение);
  * список ДЗ с завершением/удалением;
  * добавление модератора (только admin);
  * рассылка сообщений (только admin).

Панель и шаги опроса живут в одном сообщении: каждый шаг заменяет предыдущий,
ввод пользователя удаляется.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import ui
from config import TZ
from database import crud
from keyboards import (
    admin_menu,
    due_date_choose_keyboard,
    subjects_choose_keyboard,
)
from services import broadcast, get_role, get_subjects, notify_new_homework
from utils import DAY_RU_SHORT, esc, format_date_ru, format_date_short
from .states import AddHomework, AddModerator, Broadcast

admin_router = Router()

# Админ-панель работает только в личном чате.
admin_router.message.filter(F.chat.type == "private")
admin_router.callback_query.filter(F.message.chat.type == "private")

MODERATOR_ROLES = ("admin", "moderator")


async def _require_role(user_id: int, roles: tuple[str, ...]) -> str | None:
    """Возвращает роль, если она входит в список, иначе None."""
    role = await get_role(user_id)
    return role if role in roles else None


async def _screen(cq: CallbackQuery, text: str, reply_markup=None) -> None:
    """Показать экран в том сообщении, где нажата кнопка."""
    ui.track(cq.from_user.id, [cq.message.message_id])
    await ui.update(
        cq.bot,
        cq.message.chat.id,
        cq.from_user.id,
        text,
        reply_markup if reply_markup is not None else ui.EMPTY_KB,
    )


# ---------------------------------------------------------------------------
# Вход в админ-панель
# ---------------------------------------------------------------------------
@admin_router.message(F.text == "Админ-панель")
async def admin_panel(msg: Message) -> None:
    role = await _require_role(msg.from_user.id, MODERATOR_ROLES)
    await ui.delete_safe(msg.bot, msg.chat.id, msg.message_id)
    if role is None:
        await ui.show(msg.bot, msg.chat.id, msg.from_user.id, "Недостаточно прав.")
        return
    await ui.show(
        msg.bot,
        msg.chat.id,
        msg.from_user.id,
        "Админ-панель:",
        reply_markup=admin_menu(is_admin=(role == "admin")),
    )


@admin_router.callback_query(F.data == "adm:menu")
async def admin_menu_cb(cq: CallbackQuery) -> None:
    await cq.answer()
    role = await _require_role(cq.from_user.id, MODERATOR_ROLES)
    if role is None:
        await _screen(cq, "Недостаточно прав.")
        return
    await _screen(cq, "Админ-панель:", admin_menu(is_admin=(role == "admin")))


@admin_router.callback_query(F.data == "adm:close")
async def admin_close(cq: CallbackQuery) -> None:
    await cq.answer()
    await _screen(cq, "Панель закрыта.")


# ---------------------------------------------------------------------------
# Добавление ДЗ (FSM)
# ---------------------------------------------------------------------------
def _due_date_options() -> tuple[str, list[tuple[str, str]]]:
    """Текст шага 2 и список ближайших дат."""
    today = datetime.now(TZ).date()
    dates: list[tuple[str, str]] = []
    for i in range(7):
        d = today + timedelta(days=i)
        if i == 0:
            label = f"Сегодня · {format_date_short(d)}"
        elif i == 1:
            label = f"Завтра · {format_date_short(d)}"
        else:
            label = f"{format_date_short(d)} · {DAY_RU_SHORT[d.weekday()]}"
        dates.append((d.isoformat(), label))
    return "Шаг 2/3. Выберите дату сдачи ДЗ:", dates


@admin_router.callback_query(F.data == "adm:add_hw")
async def add_hw_start(cq: CallbackQuery, state: FSMContext) -> None:
    await cq.answer()
    if await _require_role(cq.from_user.id, MODERATOR_ROLES) is None:
        await _screen(cq, "Недостаточно прав.")
        return
    subjects = await get_subjects()
    await state.update_data(subjects=subjects)
    await _screen(cq, "Шаг 1/3. Выберите дисциплину:", subjects_choose_keyboard(subjects))
    await state.set_state(AddHomework.subject)


@admin_router.callback_query(F.data.startswith("hwsubj:"), AddHomework.subject)
async def add_hw_subject_cb(cq: CallbackQuery, state: FSMContext) -> None:
    await cq.answer()
    payload = cq.data.split(":", 1)[1]
    if payload == "cancel":
        await state.clear()
        await _screen(cq, "Отменено.")
        return
    if payload == "manual":
        await _screen(cq, "Введите название дисциплины текстом:")
        return

    data = await state.get_data()
    subjects = data.get("subjects", [])
    try:
        subject = subjects[int(payload)]
    except (ValueError, IndexError):
        await _screen(cq, "Ошибка выбора, попробуйте ещё раз.")
        return

    text, dates = _due_date_options()
    await state.update_data(subject=subject, due_dates=[iso for iso, _ in dates])
    await _screen(cq, text, due_date_choose_keyboard(dates))
    await state.set_state(AddHomework.due_date)


@admin_router.message(AddHomework.subject, F.text)
async def add_hw_subject_manual(msg: Message, state: FSMContext) -> None:
    subject = (msg.text or "").strip()
    await ui.delete_safe(msg.bot, msg.chat.id, msg.message_id)

    if not subject:
        await ui.update(
            msg.bot,
            msg.chat.id,
            msg.from_user.id,
            "Введите название дисциплины или нажмите кнопку.",
        )
        return

    text, dates = _due_date_options()
    await state.update_data(subject=subject, due_dates=[iso for iso, _ in dates])
    await ui.update(
        msg.bot,
        msg.chat.id,
        msg.from_user.id,
        text,
        reply_markup=due_date_choose_keyboard(dates),
    )
    await state.set_state(AddHomework.due_date)


@admin_router.callback_query(F.data.startswith("hwdate:"), AddHomework.due_date)
async def add_hw_due_cb(cq: CallbackQuery, state: FSMContext) -> None:
    await cq.answer()
    payload = cq.data.split(":", 1)[1]
    if payload == "cancel":
        await state.clear()
        await _screen(cq, "Отменено.")
        return

    data = await state.get_data()
    due_dates = data.get("due_dates", [])
    try:
        due = due_dates[int(payload)]
    except (ValueError, IndexError):
        await _screen(cq, "Ошибка, попробуйте ещё раз.")
        return

    await state.update_data(due_date=due)
    await _screen(
        cq,
        "Шаг 3/3. Введите текст домашнего задания.\n"
        "Можно прикрепить фото или файл — подпись станет текстом ДЗ.",
    )
    await state.set_state(AddHomework.text)


@admin_router.message(AddHomework.text, F.text)
async def add_hw_text(msg: Message, state: FSMContext) -> None:
    await _save_homework(msg, state, text=msg.text, media=None)


@admin_router.message(AddHomework.text, F.photo)
async def add_hw_photo(msg: Message, state: FSMContext) -> None:
    await _save_homework(msg, state, text=msg.caption, media=(msg.photo[-1].file_id, "photo"))


@admin_router.message(AddHomework.text, F.document)
async def add_hw_document(msg: Message, state: FSMContext) -> None:
    # без подписи берём имя файла, чтобы задание не осталось без описания
    text = msg.caption or msg.document.file_name
    await _save_homework(msg, state, text=text, media=(msg.document.file_id, "document"))


async def _save_homework(
    msg: Message,
    state: FSMContext,
    text: str | None,
    media: tuple[str, str] | None,
) -> None:
    user_id = msg.from_user.id
    chat_id = msg.chat.id
    data = await state.get_data()
    subject = data.get("subject")
    due_str = data.get("due_date")

    await ui.delete_safe(msg.bot, chat_id, msg.message_id)

    if not subject or not due_str:
        await state.clear()
        await ui.update(msg.bot, chat_id, user_id, "Данные потерялись — начните добавление заново.")
        return
    if not text and not media:
        await ui.update(msg.bot, chat_id, user_id, "Отправьте текст ДЗ или прикрепите файл/фото.")
        return

    due = date.fromisoformat(due_str)
    hw = await crud.add_homework(
        subject=subject,
        text=text or "",
        due_date=due,
        media_file_id=media[0] if media else None,
        media_type=media[1] if media else None,
        created_by=user_id,
    )
    await state.clear()
    await ui.update(
        msg.bot,
        chat_id,
        user_id,
        f"ДЗ по «{subject}» сохранено (сдать до {format_date_ru(due)}).",
    )
    await notify_new_homework(msg.bot, hw)


# ---------------------------------------------------------------------------
# Список ДЗ (завершение / удаление)
# ---------------------------------------------------------------------------
@admin_router.callback_query(F.data == "adm:list_hw")
async def list_hw(cq: CallbackQuery) -> None:
    await cq.answer()
    if await _require_role(cq.from_user.id, MODERATOR_ROLES) is None:
        await _screen(cq, "Недостаточно прав.")
        return
    await _render_hw_list(cq)


async def _render_hw_list(cq: CallbackQuery) -> None:
    items = await crud.list_homework(limit=20)
    if not items:
        await _screen(cq, "Домашних заданий пока нет.")
        return

    lines = ["<b>Домашние задания (последние 20):</b>"]
    for hw in items:
        status = "[активно]" if hw.is_active else "[завершено]"
        lines.append(
            f"\n{status} <b>#{hw.id}</b> {hw.subject} — {hw.text[:90]} "
            f"(до {format_date_ru(hw.due_date)})"
        )

    buttons = []
    for hw in items:
        if hw.is_active:
            buttons.append(
                [InlineKeyboardButton(text=f"Завершить #{hw.id}", callback_data=f"hwact:{hw.id}")]
            )
        buttons.append(
            [InlineKeyboardButton(text=f"Удалить #{hw.id}", callback_data=f"hwdel:{hw.id}")]
        )
    buttons.append([InlineKeyboardButton(text="Назад", callback_data="adm:menu")])

    await _screen(cq, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons))


@admin_router.callback_query(F.data.startswith("hwact:"))
async def hw_complete(cq: CallbackQuery) -> None:
    await cq.answer()
    if await _require_role(cq.from_user.id, MODERATOR_ROLES) is None:
        return
    hw_id = int(cq.data.split(":", 1)[1])
    await crud.set_homework_active(hw_id, False)
    await _render_hw_list(cq)


@admin_router.callback_query(F.data.startswith("hwdel:"))
async def hw_delete(cq: CallbackQuery) -> None:
    await cq.answer()
    if await _require_role(cq.from_user.id, MODERATOR_ROLES) is None:
        return
    hw_id = int(cq.data.split(":", 1)[1])
    await crud.delete_homework(hw_id)
    await _render_hw_list(cq)


# ---------------------------------------------------------------------------
# Участники
# ---------------------------------------------------------------------------
@admin_router.callback_query(F.data == "adm:users")
async def list_users_cb(cq: CallbackQuery) -> None:
    await cq.answer()
    if await _require_role(cq.from_user.id, MODERATOR_ROLES) is None:
        await _screen(cq, "Недостаточно прав.")
        return
    await _render_users(cq)


async def _render_users(cq: CallbackQuery) -> None:
    users = await crud.list_users()
    if not users:
        await _screen(cq, "Пользователей пока нет.")
        return

    roles = {"admin": "админ", "moderator": "модератор", "student": "студент"}
    lines = [f"<b>Участники: {len(users)}</b>", ""]
    for i, u in enumerate(users[:40], start=1):
        name = esc(u.full_name or "без имени")
        username = f" @{esc(u.username)}" if u.username else ""
        role = roles.get(u.role, esc(u.role))
        lines.append(f"{i}. {name}{username} — <code>{u.telegram_id}</code> ({role})")
    if len(users) > 40:
        lines.append(f"\nпоказаны первые 40 из {len(users)}")

    buttons = [[InlineKeyboardButton(text="Назад", callback_data="adm:menu")]]
    await _screen(cq, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons))


# ---------------------------------------------------------------------------
# Добавление модератора (только admin)
# ---------------------------------------------------------------------------
@admin_router.callback_query(F.data == "adm:add_mod")
async def add_mod_start(cq: CallbackQuery, state: FSMContext) -> None:
    await cq.answer()
    if await _require_role(cq.from_user.id, ("admin",)) is None:
        await _screen(cq, "Только главный администратор.")
        return
    await _screen(
        cq,
        "Отправьте Telegram ID нового модератора (числом)\n"
        "или перешлите любое его сообщение.",
    )
    await state.set_state(AddModerator.waiting)


@admin_router.message(AddModerator.waiting)
async def add_mod_handler(msg: Message, state: FSMContext) -> None:
    user_id: int | None = None

    if msg.forward_from is not None:
        user_id = msg.forward_from.id
    elif msg.forward_sender_name is not None:
        await ui.delete_safe(msg.bot, msg.chat.id, msg.message_id)
        await ui.update(
            msg.bot,
            msg.chat.id,
            msg.from_user.id,
            "Это сообщение от пользователя со скрытым профилем. "
            "Отправьте его Telegram ID числом.",
        )
        return
    else:
        txt = (msg.text or "").strip()
        if txt.lstrip("-").isdigit():
            user_id = int(txt)

    await ui.delete_safe(msg.bot, msg.chat.id, msg.message_id)

    if user_id is None:
        await ui.update(
            msg.bot,
            msg.chat.id,
            msg.from_user.id,
            "Не удалось определить ID. Отправьте числовой ID или перешлите сообщение.",
        )
        return

    # Регистрируем пользователя (если ещё не был), затем выдаём роль.
    await crud.get_or_create_user(user_id)
    await crud.set_user_role(user_id, "moderator")
    await state.clear()
    await ui.update(
        msg.bot,
        msg.chat.id,
        msg.from_user.id,
        f"Пользователь <code>{user_id}</code> назначен модератором.",
    )


# ---------------------------------------------------------------------------
# Рассылка (только admin)
# ---------------------------------------------------------------------------
@admin_router.callback_query(F.data == "adm:broadcast")
async def broadcast_start(cq: CallbackQuery, state: FSMContext) -> None:
    await cq.answer()
    if await _require_role(cq.from_user.id, ("admin",)) is None:
        await _screen(cq, "Только главный администратор.")
        return
    await _screen(cq, "Отправьте текст или медиа (фото/файл) для рассылки всем пользователям.")
    await state.set_state(Broadcast.waiting)


@admin_router.message(Broadcast.waiting)
async def broadcast_do(msg: Message, state: FSMContext) -> None:
    media: tuple[str, str] | None = None
    if msg.photo:
        media = (msg.photo[-1].file_id, "photo")
    elif msg.document:
        media = (msg.document.file_id, "document")

    text = msg.caption or msg.text or ""
    await state.clear()
    await ui.delete_safe(msg.bot, msg.chat.id, msg.message_id)

    delivered, total = await broadcast(msg.bot, text, media)
    await ui.update(
        msg.bot,
        msg.chat.id,
        msg.from_user.id,
        f"Рассылка завершена: доставлено {delivered} из {total}.",
    )
