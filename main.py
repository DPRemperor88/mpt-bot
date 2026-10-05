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
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import TelegramObject
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
import services
from database import init_db
from handlers import admin_router, user_router


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

        uid = user.id
        now = time.monotonic()
        if now - self._last.get(uid, 0.0) < self.limit:
            return None  # слишком часто — молча отбрасываем
        self._last[uid] = now
        return await handler(event, data)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not config.BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN не задан: создайте файл .env (см. .env.example) "
            "или впишите токен в config.py"
        )

    await init_db()

    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    # Антиспам на сообщения и нажатия кнопок.
    throttle = ThrottlingMiddleware(config.THROTTLE_SECONDS)
    dp.message.middleware(throttle)
    dp.callback_query.middleware(throttle)

    dp.include_router(user_router)
    dp.include_router(admin_router)

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

    scheduler.add_job(
        job_schedule,
        "interval",
        minutes=config.SCHEDULE_REFRESH_MINUTES,
        next_run_time=datetime.now() + timedelta(seconds=5),
    )
    scheduler.add_job(
        job_changes,
        "interval",
        minutes=config.CHANGES_REFRESH_MINUTES,
        next_run_time=datetime.now() + timedelta(seconds=10),
    )
    scheduler.start()

    try:
        # Сбрасываем накопленные апдейты и запускаем long polling.
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Бот остановлен")
