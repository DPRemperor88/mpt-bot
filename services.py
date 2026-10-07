"""
services.py — сервисный слой: обновление расписания/замен, получение данных
для вывода и рассылки уведомлений.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta

import parser as schedule_parser
import parser_changes
from config import CALL_SCHEDULE, GROUP_NAME, HOMEWORK_CLOSE_HOUR, TZ
from database import crud
from utils import (
    OTHER_WEEK,
    format_date_ru,
    normalize_subject,
    render_schedule_text,
)


# ---------------------------------------------------------------------------
# Расписание
# ---------------------------------------------------------------------------
async def ensure_schedule() -> dict:
    """Возвращает расписание из кэша; при пустом кэше — парсит сайт."""
    data = await crud.get_schedule(GROUP_NAME)
    if data is None:
        data = await refresh_schedule()
    return data


async def refresh_schedule() -> dict:
    """Парсит сайт и сохраняет расписание в кэш."""
    data = await schedule_parser.fetch_schedule(GROUP_NAME)
    await crud.save_schedule(
        GROUP_NAME,
        json.dumps(data["days"], ensure_ascii=False),
        data["anchor_date"],
        data["anchor_week"],
    )
    return data


# ---------------------------------------------------------------------------
# Дни, когда у дисциплины есть пара
# ---------------------------------------------------------------------------
def subject_pair_number(schedule: dict, target: date, subject: str) -> int | None:
    """Номер пары по дисциплине в указанный день (None, если пары нет)."""
    anchor = date.fromisoformat(schedule["anchor_date"])
    week = schedule_parser.week_type_for(target, anchor, schedule["anchor_week"])
    lessons = schedule.get("days", {}).get(str(target.weekday()), [])
    key = normalize_subject(subject)

    for lesson in lessons:
        variants = lesson.get("variants", {})
        variant = variants.get(week) or variants.get(OTHER_WEEK.get(week))
        if not variant:
            continue
        if normalize_subject(variant.get("subject")) == key:
            return lesson.get("number")
    return None


async def subject_lesson_days(subject: str, horizon: int = 14) -> list[tuple[date, int]]:
    """
    Ближайшие дни, когда у дисциплины есть пара: список (дата, номер пары).
    Горизонт по умолчанию — две недели, чтобы попали обе недели цикла.
    """
    schedule = await ensure_schedule()
    today = datetime.now(TZ).date()

    result: list[tuple[date, int]] = []
    for offset in range(horizon):
        target = today + timedelta(days=offset)
        number = subject_pair_number(schedule, target, subject)
        if number is not None:
            result.append((target, number))
    return result


# ---------------------------------------------------------------------------
# Автозакрытие ДЗ
# ---------------------------------------------------------------------------
def homework_closed_at(due_date: date) -> datetime:
    """Момент, когда ДЗ по этой дате считается завершённым."""
    return datetime(
        due_date.year,
        due_date.month,
        due_date.day,
        HOMEWORK_CLOSE_HOUR,
        0,
        tzinfo=TZ,
    )


def annotate_homework(items: list, now: datetime) -> list[tuple]:
    """
    Дополняет каждое ДЗ признаком «срок сдачи уже прошёл».
    Такие задания закрываются в разделе ДЗ, но остаются в расписании.
    """
    return [(hw, now >= homework_closed_at(hw.due_date)) for hw in items]


async def get_subjects() -> list[str]:
    """Список дисциплин группы (для выбора при добавлении ДЗ)."""
    data = await ensure_schedule()
    return schedule_parser.subjects_from_schedule(data)


async def get_changes_map(query_date: date) -> dict[int, str]:
    """Карта «номер пары → текст замены» для конкретной даты (или пусто)."""
    data = await crud.get_changes(GROUP_NAME)
    if not data or not data.get("date"):
        return {}
    try:
        change_date = date.fromisoformat(data["date"])
    except (TypeError, ValueError):
        return {}
    if change_date != query_date:
        return {}
    result: dict[int, str] = {}
    for c in data["changes"]:
        lesson = str(c.get("lesson", ""))
        if lesson.isdigit():
            result[int(lesson)] = c.get("replace_to", "")
    return result


# ---------------------------------------------------------------------------
# Расписание дня и утренняя рассылка
# ---------------------------------------------------------------------------
def _pair_start(time_range: str) -> tuple[int, int] | None:
    """Время начала пары из строки вида '8.30-10.00'."""
    head = (time_range or "").split("-")[0].strip().replace(".", ":")
    parts = head.split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return None
    return int(parts[0]), int(parts[1])


async def schedule_day(target: date) -> tuple[str, list]:
    """Текст расписания на день и список ДЗ с этим сроком (для вложений)."""
    data = await ensure_schedule()
    anchor = date.fromisoformat(data["anchor_date"])
    week = schedule_parser.week_type_for(target, anchor, data["anchor_week"])
    lessons = data.get("days", {}).get(str(target.weekday()), [])

    header = f"<b>{format_date_ru(target)}</b>\nНеделя: <b>{week}</b>"
    if not lessons:
        return header + "\n\n—", []

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
    return header + "\n\n" + body, list(homework.values())


async def morning_post(bot) -> bool:
    """
    Отправляет расписание дня за час до первой пары.
    Возвращает True, если рассылка состоялась.
    """
    now = datetime.now(TZ)
    today = now.date()

    if await crud.get_meta("morning_post") == today.isoformat():
        return False

    data = await ensure_schedule()
    lessons = data.get("days", {}).get(str(today.weekday()), [])
    numbers = [lesson.get("number") for lesson in lessons if lesson.get("number")]
    if not numbers:
        return False

    start = _pair_start(CALL_SCHEDULE.get(min(numbers), ""))
    if start is None:
        return False

    moment = datetime(
        today.year, today.month, today.day, start[0], start[1], tzinfo=TZ
    ) - timedelta(hours=1)

    if now < moment:
        return False
    if now > moment + timedelta(hours=2):
        # бот лежал и время ушло — помечаем день, чтобы не пытаться до вечера
        await crud.set_meta("morning_post", today.isoformat())
        return False

    text, _ = await schedule_day(today)
    ids = await crud.users_for_notification("morning")
    await broadcast(bot, text, user_ids=ids)
    await broadcast_to_groups(bot, text)
    await crud.set_meta("morning_post", today.isoformat())
    return True


# ---------------------------------------------------------------------------
# Замены
# ---------------------------------------------------------------------------
def _change_key(c: dict) -> tuple:
    return (c.get("lesson", ""), c.get("replace_from", ""), c.get("replace_to", ""))


async def refresh_changes(bot) -> int:
    """
    Обновляет замены. При появлении НОВЫХ замен — рассылает уведомление всем
    пользователям и возвращает количество новых. При холодном старте (кэша ещё
    нет) ничего не рассылает, чтобы не слать спам о старых заменах.
    """
    data = await parser_changes.fetch_changes(GROUP_NAME)
    new_snapshot = data["changes"]

    prev = await crud.get_changes(GROUP_NAME)
    if prev is None:
        await crud.save_changes(
            GROUP_NAME,
            data["date"],
            json.dumps(new_snapshot, ensure_ascii=False),
        )
        return 0

    prev_keys = {_change_key(c) for c in prev.get("changes", [])}
    new_items = [c for c in new_snapshot if _change_key(c) not in prev_keys]

    await crud.save_changes(
        GROUP_NAME,
        data["date"],
        json.dumps(new_snapshot, ensure_ascii=False),
    )

    if new_items:
        await notify_changes(bot, new_items, data["date"])
    return len(new_items)


async def notify_changes(bot, items: list, change_date_str: str) -> None:
    try:
        d = date.fromisoformat(change_date_str)
        header = f"<b>Изменения в расписании на {format_date_ru(d)}</b>\n\n"
    except (TypeError, ValueError):
        header = "<b>Изменения в расписании</b>\n\n"

    lines = [
        f"• Пара {c['lesson']}: {c['replace_from']} → {c['replace_to']}"
        for c in items
    ]
    message = header + "\n".join(lines)
    ids = await crud.users_for_notification("changes")
    await broadcast(bot, message, user_ids=ids)   # личка тем, кто подписан
    await broadcast_to_groups(bot, message)       # и в группы, где есть бот


# ---------------------------------------------------------------------------
# Рассылка
# ---------------------------------------------------------------------------
async def broadcast(
    bot,
    text: str,
    media: tuple[str, str] | None = None,
    user_ids: list[int] | None = None,
) -> tuple[int, int]:
    """
    Шлёт сообщение (или медиа с подписью) пользователям.
    user_ids=None — всем зарегистрированным. Возвращает (доставлено, всего).
    """
    ids = user_ids
    if ids is None:
        ids = [u.telegram_id for u in await crud.list_users()]

    delivered = 0
    for uid in ids:
        try:
            if media:
                file_id, media_type = media
                if media_type == "photo":
                    await bot.send_photo(uid, file_id, caption=text or None)
                else:
                    await bot.send_document(uid, file_id, caption=text or None)
            else:
                await bot.send_message(uid, text)
            delivered += 1
        except Exception:
            # пользователь заблокировал бота или ушёл из чата — пропускаем
            pass
        await asyncio.sleep(0.05)  # не упереться в лимиты Telegram
    return delivered, len(ids)


async def broadcast_to_groups(bot, text: str) -> int:
    """Шлёт сообщение во все группы, куда добавлен бот. Возвращает число доставок."""
    chats = await crud.list_group_chats()
    delivered = 0
    for chat in chats:
        try:
            await bot.send_message(chat.chat_id, text)
            delivered += 1
        except Exception:
            # бота удалили из группы — пропускаем
            pass
        await asyncio.sleep(0.05)
    return delivered


async def notify_new_homework(bot, hw) -> None:
    """Рассылает уведомление о новом домашнем задании вместе с вложениями."""
    body = hw.text.strip() if (hw.text and hw.text.strip()) else "(задание во вложении)"
    text = (
        f"<b>Новое домашнее задание</b>\n\n"
        f"<b>{hw.subject}</b>: {body}\n"
        f"Сдать до: {format_date_ru(hw.due_date)}"
    )
    files = await _homework_files(hw)

    for uid in await crud.users_for_notification("homework"):
        try:
            await bot.send_message(uid, text)
        except Exception:
            # пользователь заблокировал бота — пропускаем
            continue
        await send_homework_media(bot, uid, hw, files)
        await asyncio.sleep(0.05)


async def _homework_files(hw) -> list[tuple[str, str]]:
    """Все вложения задания: устаревшее одиночное поле плюс таблица файлов."""
    files: list[tuple[str, str]] = []
    if hw.media_file_id:
        files.append((hw.media_file_id, hw.media_type or "document"))
    for row in await crud.list_homework_files(hw.id):
        files.append((row.file_id, row.file_type))
    return files


async def send_homework_media(bot, chat_id: int, hw, files=None) -> list[int]:
    """
    Отправляет все вложения домашнего задания в указанный чат.
    Возвращает id отправленных сообщений.
    """
    if files is None:
        files = await _homework_files(hw)

    sent_ids: list[int] = []
    for index, (file_id, file_type) in enumerate(files):
        caption = hw.subject if index == 0 else None
        try:
            if file_type == "photo":
                sent = await bot.send_photo(chat_id, file_id, caption=caption)
            else:
                sent = await bot.send_document(chat_id, file_id, caption=caption)
        except Exception:
            # файл недоступен (удалён или устарел) — выдачу не роняем
            continue
        sent_ids.append(sent.message_id)
    return sent_ids


# ---------------------------------------------------------------------------
# Роли
# ---------------------------------------------------------------------------
async def get_role(telegram_id: int) -> str:
    user = await crud.get_user(telegram_id)
    return user.role if user else "student"
