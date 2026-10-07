"""
main.py — точка запуска бота.

Поднимает aiogram 3.x + SQLite (SQLAlchemy 2.0 async) + APScheduler.
Планировщик в фоне обновляет расписание (раз в N часов) и замены (раз в M минут).
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    TelegramObject,
    Message,
    CallbackQuery,
    ErrorEvent,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
import services
import delivery
from database import init_db
from database.db import engine
from handlers import admin_router, group_router, user_router


# ---------------------------------------------------------------------------
# Троттлинг (антиспам)
# ---------------------------------------------------------------------------
class ThrottlingMiddleware(BaseMiddleware):
    """Ограничивает частоту действий одного пользователя (минимальный интервал)."""

    def __init__(self, limit_seconds: float):
        self.limit = limit_seconds
        self._last: dict[int, float] = {}
        super().__init__()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        # Каждый элемент альбома приходит отдельным update. Ввод FSM не теряем.
        if isinstance(event, Message) and (
            event.photo or event.document or
            (data.get("raw_state") and not (event.text or "").startswith("/"))
        ):
            return await handler(event, data)

        uid = user.id
        now = time.monotonic()
        if len(self._last) > 1000:
            self._last = {key: stamp for key, stamp in self._last.items() if now - stamp < 60}
        if now - self._last.get(uid, 0.0) < self.limit:
            if isinstance(event, CallbackQuery):
                await event.answer("Подождите немного и повторите нажатие.")
            return None
        self._last[uid] = now
        return await handler(event, data)


# ---------------------------------------------------------------------------
# Меню команд (список по «/»)
# ---------------------------------------------------------------------------
PRIVATE_COMMANDS = [
    BotCommand(command="start", description="Главное меню"),
    BotCommand(command="menu", description="Меню"),
    BotCommand(command="admin", description="Админ-панель"),
    BotCommand(command="cancel", description="Отменить текущий ввод"),
]

GROUP_COMMANDS = [
    BotCommand(command="today", description="ДЗ на сегодня"),
    BotCommand(command="tomorrow", description="ДЗ на завтра"),
    BotCommand(command="menu", description="Меню"),
]


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not config.BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN не задан: создайте файл .env (см. .env.example)"
        )

    await init_db()

    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    # Меню команд: своё для личных чатов и своё для групп.
    await bot.set_my_commands(PRIVATE_COMMANDS, scope=BotCommandScopeAllPrivateChats())
    await bot.set_my_commands(GROUP_COMMANDS, scope=BotCommandScopeAllGroupChats())

    dp = Dispatcher(storage=MemoryStorage(), events_isolation=SimpleEventIsolation())

    # Антиспам на сообщения и нажатия кнопок.
    throttle = ThrottlingMiddleware(config.THROTTLE_SECONDS)
    dp.message.middleware(throttle)
    dp.callback_query.middleware(throttle)

    dp.include_router(user_router)
    dp.include_router(group_router)
    dp.include_router(admin_router)

    @dp.errors()
    async def report_error(event: ErrorEvent) -> bool:
        exc = event.exception
        logging.error("Ошибка обработки update %s", event.update.update_id,
                      exc_info=(type(exc), exc, exc.__traceback__))
        message = event.update.message
        if message is None and event.update.callback_query:
            message = event.update.callback_query.message
        if message is not None:
            try:
                await bot.send_message(message.chat.id, "Не удалось выполнить действие. Попробуйте ещё раз немного позже.")
            except Exception as reply_error:
                logging.warning("Не удалось сообщить об ошибке: %s", type(reply_error).__name__)
        return True

    # Фоновые задачи.
    scheduler = AsyncIOScheduler(timezone=config.TIMEZONE)

    async def job_schedule() -> None:
        try:
            await services.refresh_schedule()
            logging.info("Расписание обновлено")
        except Exception:
            logging.exception("Ошибка обновления расписания")

    async def job_changes() -> None:
        try:
            new = await services.refresh_changes(bot)
            if new:
                logging.info("Обнаружено новых замен: %d", new)
        except Exception:
            logging.exception("Ошибка обновления замен")

    async def job_morning() -> None:
        try:
            if await services.morning_post(bot):
                logging.info("Утреннее расписание отправлено")
        except Exception:
            logging.exception("Ошибка утренней рассылки расписания")

    scheduler.add_job(
        job_schedule,
        "interval",
        minutes=config.SCHEDULE_REFRESH_MINUTES,
        next_run_time=datetime.now(config.TZ) + timedelta(seconds=5),
    )
    scheduler.add_job(
        job_changes,
        "interval",
        minutes=config.CHANGES_REFRESH_MINUTES,
        next_run_time=datetime.now(config.TZ) + timedelta(seconds=10),
    )
    # Проверка «пора отправить расписание» — каждые 5 минут
    scheduler.add_job(
        job_morning,
        "interval",
        minutes=5,
        next_run_time=datetime.now(config.TZ) + timedelta(seconds=20),
    )
    scheduler.add_job(delivery.drain, "interval", seconds=10, args=[bot], max_instances=1)
    scheduler.start()

    try:
        # Накопленные задания и файлы после перезапуска не отбрасываем.
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Бот остановлен")
