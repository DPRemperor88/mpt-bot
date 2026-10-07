"""
services.py — сервисный слой: обновление расписания/замен, получение данных
для вывода и рассылки уведомлений.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from uuid import uuid4
from datetime import date, datetime, timedelta, timezone

import parser as schedule_parser
import parser_changes
from config import CALL_SCHEDULE, GROUP_NAME, HOMEWORK_CLOSE_HOUR, TZ
from database import crud
import delivery
from utils import (
    esc,
    format_date_ru,
    normalize_subject,
    render_schedule_text,
)

logger = logging.getLogger(__name__)
_schedule_lock = asyncio.Lock()
_changes_lock = asyncio.Lock()


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
    async with _schedule_lock:
        data = await schedule_parser.fetch_schedule(GROUP_NAME)
        await crud.save_schedule(
            GROUP_NAME,
            json.dumps(data["days"], ensure_ascii=False),
            data["anchor_date"],
            data["anchor_week"],
        )
    data["updated_at"] = datetime.now(timezone.utc).replace(tzinfo=None)
    return data


# ---------------------------------------------------------------------------
# Дни, когда у дисциплины есть пара
# ---------------------------------------------------------------------------
def subject_pair_number(schedule: dict, target: date, subject: str, changes=None) -> int | None:
    """Номер пары по дисциплине в указанный день (None, если пары нет)."""
    anchor = date.fromisoformat(schedule["anchor_date"])
    week = schedule_parser.week_type_for(target, anchor, schedule["anchor_week"])
    lessons = effective_lessons(schedule, target, changes or {})
    key = normalize_subject(subject)

    for lesson in lessons:
        variants = lesson.get("variants", {})
        variant = variants.get(week)
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
        number = subject_pair_number(schedule, target, subject, await get_changes_map(target))
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
    result: dict[int, str] = {}
    for c in data["changes"]:
        if c.get("date", change_date.isoformat()) != query_date.isoformat():
            continue
        lesson = str(c.get("lesson", ""))
        for number in lesson_numbers(lesson):
            result[number] = c.get("replace_to", "")
    return result


def lesson_numbers(value: str) -> list[int]:
    """Номер, диапазон или список пар; неизвестный формат не угадываем."""
    if re.fullmatch(r"\d+\s*[-–]\s*\d+", value):
        start, end = map(int, re.split(r"\s*[-–]\s*", value))
        return list(range(start, end + 1)) if 1 <= start <= end <= 20 else []
    if re.fullmatch(r"\d+(?:\s*[,;]\s*\d+)*", value):
        return [int(n) for n in re.split(r"\s*[,;]\s*", value) if 1 <= int(n) <= 20]
    return []


def is_cancelled(replacement: str) -> bool:
    value = normalize_subject(replacement)
    return bool(re.match(r"^(?:(?:пара|пары|занятие|занятия|урок|уроки)\s+)?отмен[её]н[аоы]?\b", value)) or value in {
        "нет", "нет пары", "нет занятий", "пары не будет", "занятий не будет", "—", "-"
    }


def effective_lessons(schedule: dict, target: date, changes: dict[int, str]) -> list[dict]:
    """Единая выборка действующих пар. Неясный текст замены сохраняется дословно."""
    week = schedule_parser.week_type_for(target, date.fromisoformat(schedule["anchor_date"]), schedule["anchor_week"])
    result = {}
    for lesson in schedule.get("days", {}).get(str(target.weekday()), []):
        variant = lesson.get("variants", {}).get(week, {})
        if variant.get("subject", "").strip():
            result[lesson["number"]] = lesson
    for number, replacement in changes.items():
        if is_cancelled(replacement):
            result.pop(number, None)
        elif number not in result and replacement.strip():
            result[number] = {"number": number, "variants": {week: {"subject": replacement, "teacher": ""}}}
    return [result[n] for n in sorted(result)]


# ---------------------------------------------------------------------------
# Расписание дня и утренняя рассылка
# ---------------------------------------------------------------------------
def _pair_start(time_range: str) -> tuple[int, int] | None:
    """Время начала пары из строки вида '8.30-10.00'."""
    head = (time_range or "").split("-")[0].strip().replace(".", ":")
    parts = head.split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return None
    hour, minute = map(int, parts)
    return (hour, minute) if 0 <= hour < 24 and 0 <= minute < 60 else None


async def schedule_day(target: date) -> tuple[str, list]:
    """Текст расписания на день и список ДЗ с этим сроком (для вложений)."""
    data = await ensure_schedule()
    anchor = date.fromisoformat(data["anchor_date"])
    week = schedule_parser.week_type_for(target, anchor, data["anchor_week"])
    changes_map = await get_changes_map(target)
    lessons = effective_lessons(data, target, changes_map)

    header = f"<b>{format_date_ru(target)}</b>\nНеделя: <b>{week}</b>"
    updated = data.get("updated_at")
    if updated:
        header += "\nОбновлено: " + updated.replace(tzinfo=timezone.utc).astimezone(TZ).strftime("%d.%m %H:%M")

    day_homework = await crud.list_homework_on(target)
    with_files = await crud.homework_ids_with_files([hw.id for hw in day_homework])

    homework: dict[str, list] = {}
    for hw in day_homework:
        homework.setdefault(normalize_subject(hw.subject), []).append(hw)

    display = {
        key: "\n".join(hw.text.strip() or ("(вложение)" if hw.id in with_files or hw.media_file_id else "—") for hw in items)
        for key, items in homework.items()
    }

    body = render_schedule_text(lessons, week, CALL_SCHEDULE, display, changes_map)
    for number, replacement in sorted(changes_map.items()):
        if is_cancelled(replacement):
            body += f"\n\n<b>Пара {number}:</b> {esc(replacement)}"
    shown = {normalize_subject(lesson["variants"][week]["subject"]) for lesson in lessons}
    for key, items in homework.items():
        if key not in shown:
            body += f"\n\n<b>ДЗ: {esc(items[0].subject)}</b>\n{esc(display[key])}"
    return header + "\n\n" + (body.strip() or "—"), day_homework


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
    lessons = effective_lessons(data, today, await get_changes_map(today))
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
    ids += [chat.chat_id for chat in await crud.list_group_chats()]
    rows = delivery_rows(f"morning:{today.isoformat()}", ids, text)
    await crud.enqueue(rows, meta=("morning_post", today.isoformat()))
    return True


# ---------------------------------------------------------------------------
# Замены
# ---------------------------------------------------------------------------
def _change_key(c: dict) -> tuple:
    return (c.get("date", ""), c.get("lesson", ""), c.get("replace_from", ""), c.get("replace_to", ""))


def delivery_rows(event_key: str, ids: list[int], text: str, files=()) -> list[dict]:
    body = delivery.payload(text, files)
    return [{"event_key": event_key, "chat_id": uid, "payload_json": body} for uid in dict.fromkeys(ids)]


async def refresh_changes(bot) -> int:
    """Атомарно сохраняет снимок и доставку изменений; первый снимок без рассылки."""
    async with _changes_lock:
        data = await parser_changes.fetch_changes(GROUP_NAME)
        prev = await crud.get_changes(GROUP_NAME)
        rows = []
        updates = []
        if prev is not None:
            previous = [dict(c, date=c.get("date") or prev["date"]) for c in prev["changes"]]
            old_keys = {_change_key(c) for c in previous}
            new_keys = {_change_key(c) for c in data["changes"]}
            old_slots = {(c["date"], c["lesson"]) for c in previous}
            new_slots = {(c["date"], c["lesson"]) for c in data["changes"]}
            for c in data["changes"]:
                if _change_key(c) not in old_keys:
                    label = "Исправлена замена" if (c["date"], c["lesson"]) in old_slots else "Новая замена"
                    updates.append((label, c))
            for c in previous:
                if (_change_key(c) not in new_keys and (c["date"], c["lesson"]) not in new_slots
                        and c["date"] in data["dates"]):
                    updates.append(("Замена снята", c))
            if updates:
                ids = await crud.users_for_notification("changes")
                ids += [chat.chat_id for chat in await crud.list_group_chats()]
                lines = ["<b>Изменения в расписании</b>"]
                for label, c in updates:
                    lines.append(f"{label} · {esc(c['date'])} · пара {esc(c['lesson'])}: "
                                 f"{esc(c['replace_from'])} → {esc(c['replace_to'])}")
                rows = delivery_rows(f"changes:{uuid4().hex}", ids, "\n".join(lines))
        await crud.save_changes(GROUP_NAME, data["date"], json.dumps(data["changes"], ensure_ascii=False), deliveries=rows)
        return len(updates)


async def broadcast(bot, text: str, media=None, user_ids=None) -> tuple[int, int]:
    """Создаёт сохраняемую рассылку. Возвращает доставлено сейчас / всего."""
    ids = user_ids if user_ids is not None else [u.telegram_id for u in await crud.list_users()]
    event_key = f"broadcast:{uuid4().hex}"
    await crud.enqueue(delivery_rows(event_key, ids, text, [media] if media else []))
    await delivery.drain(bot)
    return await crud.delivery_counts(event_key)


async def create_homework(subject: str, text: str, due_date: date, created_by: int, files: list):
    """ДЗ, файлы, журнал и уведомления сохраняются одной транзакцией."""
    body = text.strip() or "(задание во вложении)"
    message = (f"<b>Новое домашнее задание</b>\n\n<b>{esc(subject)}</b>: {esc(body)}\n"
               f"Сдать до: {format_date_ru(due_date)}")
    ids = await crud.users_for_notification("homework")
    rows = delivery_rows(f"homework:{uuid4().hex}", ids, message, files)
    return await crud.add_homework(subject=subject, text=text, due_date=due_date,
                                   created_by=created_by, files=files, deliveries=rows)


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
        caption = esc(hw.subject[:200]) if index == 0 else None
        try:
            if file_type == "photo":
                sent = await bot.send_photo(chat_id, file_id, caption=caption)
            else:
                sent = await bot.send_document(chat_id, file_id, caption=caption)
        except Exception as exc:
            logger.warning("Не удалось отправить вложение ДЗ %s: %s", hw.id, type(exc).__name__)
            raise
        sent_ids.append(sent.message_id)
    return sent_ids


# ---------------------------------------------------------------------------
# Роли
# ---------------------------------------------------------------------------
async def get_role(telegram_id: int) -> str:
    from config import ADMIN_IDS
    if telegram_id in ADMIN_IDS:
        return "admin"
    user = await crud.get_user(telegram_id)
    return user.role if user else "student"
