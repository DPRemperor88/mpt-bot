"""Разбиение HTML и возобновляемая доставка уведомлений из SQLite."""
from __future__ import annotations

import asyncio
import html
import json
import logging
from datetime import datetime, timedelta
from html.parser import HTMLParser

from aiogram.exceptions import (
    TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError,
    TelegramRetryAfter, TelegramServerError,
)

from database import crud
from database.models import _utcnow

logger = logging.getLogger(__name__)
_drain_lock = asyncio.Lock()


def split_html(text: str, limit: int = 3500) -> list[str]:
    """Делит доверенный HTML-шаблон, сохраняя теги, entities и UTF-16 лимит."""
    class Splitter(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.parts = []
            self.stack = []
            self.current = ""
            self.size = 0

        def flush(self):
            if self.size:
                self.parts.append(self.current + "".join(f"</{tag}>" for tag, _ in reversed(self.stack)))
            self.current = "".join(raw for _, raw in self.stack)
            self.size = 0

        def handle_starttag(self, tag, attrs):
            raw = self.get_starttag_text()
            self.stack.append((tag, raw))
            self.current += raw

        def handle_endtag(self, tag):
            if not self.stack or self.stack[-1][0] != tag:
                raise ValueError("Некорректный HTML-шаблон")
            self.current += f"</{tag}>"
            self.stack.pop()

        def handle_data(self, data):
            for char in data:
                size = len(char.encode("utf-16-le")) // 2
                if self.size + size > limit:
                    self.flush()
                self.current += html.escape(char)
                self.size += size

    parser = Splitter()
    parser.feed(text)
    parser.close()
    if parser.stack:
        raise ValueError("Незакрытый HTML-тег")
    parser.flush()
    return parser.parts or ["—"]


def payload(text: str, files=()) -> str:
    actions = [{"method": "send_message", "text": chunk} for chunk in split_html(text)]
    actions.extend({"method": "send_photo" if kind == "photo" else "send_document", "file_id": file_id}
                   for file_id, kind in files)
    return json.dumps(actions, ensure_ascii=False)


async def send_text(bot, chat_id: int, text: str, reply_markup=None) -> list[int]:
    chunks = split_html(text)
    result = []
    for i, chunk in enumerate(chunks):
        sent = await bot.send_message(chat_id, chunk, reply_markup=reply_markup if i == len(chunks) - 1 else None)
        result.append(sent.message_id)
    return result


async def drain(bot) -> None:
    """Один worker на процесс. Сбой одного получателя не останавливает других."""
    if _drain_lock.locked():
        return
    async with _drain_lock:
        pause = await crud.get_meta("delivery_pause_until")
        if pause and datetime.fromisoformat(pause) > _utcnow():
            return
        for _ in range(50):
            pending = await crud.pending_deliveries(limit=1)
            if not pending:
                break
            row = pending[0]
            try:
                actions = json.loads(row.payload_json)
                for index in range(row.cursor, len(actions)):
                    action = actions[index]
                    method = action["method"]
                    if method == "send_message":
                        await bot.send_message(row.chat_id, action["text"])
                    elif method == "send_photo":
                        await bot.send_photo(row.chat_id, action["file_id"])
                    elif method == "send_document":
                        await bot.send_document(row.chat_id, action["file_id"])
                    else:
                        raise ValueError("Неизвестный метод доставки")
                    await crud.update_delivery(row.id, cursor=index + 1)
                    await asyncio.sleep(0.05)
            except TelegramRetryAfter as exc:
                resume = _utcnow() + timedelta(seconds=exc.retry_after + 1)
                await crud.update_delivery(row.id, attempts=row.attempts + 1,
                    next_attempt_at=resume, last_error="rate_limit")
                await crud.set_meta("delivery_pause_until", resume.isoformat())
                # Лимит может относиться ко всему боту: не продолжаем рассылку сейчас.
                return
            except (TelegramForbiddenError, TelegramBadRequest, ValueError, KeyError, TypeError) as exc:
                logger.warning("Доставка %s прекращена: %s", row.id, type(exc).__name__)
                await crud.update_delivery(row.id, status="failed", last_error=type(exc).__name__)
            except (TelegramNetworkError, TelegramServerError, OSError) as exc:
                delay = min(3600, 30 * 2 ** min(row.attempts, 7))
                logger.warning("Доставка %s отложена: %s", row.id, type(exc).__name__)
                await crud.update_delivery(row.id, attempts=row.attempts + 1,
                    next_attempt_at=_utcnow() + timedelta(seconds=delay), last_error=type(exc).__name__)
            except Exception as exc:
                logger.error("Ошибка доставки %s: %s", row.id, type(exc).__name__)
                await crud.update_delivery(row.id, next_attempt_at=_utcnow() + timedelta(minutes=5),
                    attempts=row.attempts + 1, last_error=type(exc).__name__)
            else:
                await crud.update_delivery(row.id, status="sent", last_error=None)
