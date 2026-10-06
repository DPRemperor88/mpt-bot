"""
services.py — сервисный слой: обновление расписания/замен, получение данных
для вывода и рассылки уведомлений.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date

import parser as schedule_parser
import parser_changes
from config import GROUP_NAME
from database import crud
from utils import format_date_ru


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
    await broadcast(bot, header + "\n".join(lines))


# ---------------------------------------------------------------------------
# Рассылка
# ---------------------------------------------------------------------------
async def broadcast(bot, text: str, media: tuple[str, str] | None = None) -> tuple[int, int]:
    """
    Шлёт сообщение (или медиа с подписью) всем зарегистрированным пользователям.
    Возвращает (доставлено, всего). Ошибки отдельных пользователей игнорируются.
    """
    users = await crud.list_users()
    delivered = 0
    for u in users:
        try:
            if media:
                file_id, media_type = media
                if media_type == "photo":
                    await bot.send_photo(u.telegram_id, file_id, caption=text or None)
                else:
                    await bot.send_document(u.telegram_id, file_id, caption=text or None)
            else:
                await bot.send_message(u.telegram_id, text)
            delivered += 1
        except Exception:
            # пользователь заблокировал бота или ушёл из чата — пропускаем
            pass
        await asyncio.sleep(0.05)  # не упереться в лимиты Telegram
    return delivered, len(users)


async def notify_new_homework(bot, hw) -> None:
    """Рассылает уведомление о новом домашнем задании (с вложением, если есть)."""
    body = hw.text.strip() if (hw.text and hw.text.strip()) else "(задание во вложении)"
    text = (
        f"<b>Новое домашнее задание</b>\n\n"
        f"<b>{hw.subject}</b>: {body}\n"
        f"Сдать до: {format_date_ru(hw.due_date)}"
    )
    media = (hw.media_file_id, hw.media_type) if (hw.media_file_id and hw.media_type) else None
    await broadcast(bot, text, media)


async def send_homework_media(bot, chat_id: int, hw) -> list[int]:
    """
    Отправляет вложение домашнего задания (фото или файл) в указанный чат.
    Возвращает id отправленных сообщений; пустой список, если вложения нет.
    """
    if not hw.media_file_id:
        return []
    try:
        if hw.media_type == "photo":
            sent = await bot.send_photo(chat_id, hw.media_file_id, caption=hw.subject)
        else:
            sent = await bot.send_document(chat_id, hw.media_file_id, caption=hw.subject)
    except Exception:
        # файл недоступен (удалён или устарел) — выдачу не роняем
        return []
    return [sent.message_id]


# ---------------------------------------------------------------------------
# Роли
# ---------------------------------------------------------------------------
async def get_role(telegram_id: int) -> str:
    user = await crud.get_user(telegram_id)
    return user.role if user else "student"
