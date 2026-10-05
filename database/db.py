"""
database/db.py — асинхронный движок, фабрика сессий и инициализация схемы.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import DATABASE_URL
from .models import Base

# echo=False в проде; при отладке можно поставить True, чтобы видеть SQL.
engine = create_async_engine(DATABASE_URL, echo=False)

SessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    """Создаёт таблицы, если их ещё нет (идемпотентно)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
