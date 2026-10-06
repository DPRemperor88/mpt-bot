"""
database/crud.py — CRUD-операции над пользователями, ДЗ и кэшем расписания.

Все функции асинхронные и открывают собственную сессию, поэтому их можно
вызывать из любого хэндлера/сервиса без ручного управления сессиями.
"""
from __future__ import annotations

import json
from datetime import date

from sqlalchemy import delete, select

from .db import SessionLocal
from .models import ChangesCache, GroupChat, Homework, HomeworkFile, ScheduleCache, User


# ---------------------------------------------------------------------------
# Пользователи
# ---------------------------------------------------------------------------
async def get_or_create_user(
    telegram_id: int,
    username: str | None = None,
    full_name: str | None = None,
) -> User:
    """Возвращает пользователя, при необходимости создаёт и обновляет имя."""
    async with SessionLocal() as s:
        res = await s.execute(select(User).where(User.telegram_id == telegram_id))
        user = res.scalar_one_or_none()
        if user is None:
            user = User(
                telegram_id=telegram_id,
                username=username,
                full_name=full_name,
                role="student",
            )
            s.add(user)
        else:
            if username is not None:
                user.username = username
            if full_name is not None:
                user.full_name = full_name
        await s.commit()
        return user


async def get_user(telegram_id: int) -> User | None:
    async with SessionLocal() as s:
        res = await s.execute(select(User).where(User.telegram_id == telegram_id))
        return res.scalar_one_or_none()


async def set_user_role(telegram_id: int, role: str) -> None:
    async with SessionLocal() as s:
        res = await s.execute(select(User).where(User.telegram_id == telegram_id))
        user = res.scalar_one_or_none()
        if user is not None:
            user.role = role
            await s.commit()


async def list_users() -> list[User]:
    async with SessionLocal() as s:
        res = await s.execute(select(User).order_by(User.id))
        return list(res.scalars().all())


# ---------------------------------------------------------------------------
# Домашние задания
# ---------------------------------------------------------------------------
async def add_homework(
    subject: str,
    text: str,
    due_date: date,
    media_file_id: str | None = None,
    media_type: str | None = None,
    created_by: int | None = None,
) -> Homework:
    async with SessionLocal() as s:
        hw = Homework(
            subject=subject,
            text=text,
            due_date=due_date,
            media_file_id=media_file_id,
            media_type=media_type,
            created_by=created_by,
        )
        s.add(hw)
        await s.commit()
        return hw


async def list_active_homework(on_date: date) -> list[Homework]:
    """Активные ДЗ: не завершены и дедлайн ещё не прошёл (due_date >= on_date)."""
    async with SessionLocal() as s:
        res = await s.execute(
            select(Homework)
            .where(Homework.is_active.is_(True), Homework.due_date >= on_date)
            .order_by(Homework.created_at.desc(), Homework.due_date.asc())
        )
        return list(res.scalars().all())


async def list_homework_on(due_date: date) -> list[Homework]:
    """Активные ДЗ с дедлайном ровно на указанную дату (для группового чата)."""
    async with SessionLocal() as s:
        res = await s.execute(
            select(Homework)
            .where(Homework.is_active.is_(True), Homework.due_date == due_date)
            .order_by(Homework.subject)
        )
        return list(res.scalars().all())


async def list_homework(limit: int = 30) -> list[Homework]:
    """Все ДЗ (для админ-панели), последние сверху."""
    async with SessionLocal() as s:
        res = await s.execute(
            select(Homework).order_by(Homework.created_at.desc()).limit(limit)
        )
        return list(res.scalars().all())


async def set_homework_active(homework_id: int, active: bool) -> None:
    async with SessionLocal() as s:
        res = await s.execute(select(Homework).where(Homework.id == homework_id))
        hw = res.scalar_one_or_none()
        if hw is not None:
            hw.is_active = active
            await s.commit()


async def add_homework_files(homework_id: int, files: list[tuple[str, str]]) -> None:
    """files: список пар (file_id, тип: photo | document)."""
    if not files:
        return
    async with SessionLocal() as s:
        for file_id, file_type in files:
            s.add(
                HomeworkFile(
                    homework_id=homework_id,
                    file_id=file_id,
                    file_type=file_type,
                )
            )
        await s.commit()


async def list_homework_files(homework_id: int) -> list[HomeworkFile]:
    async with SessionLocal() as s:
        res = await s.execute(
            select(HomeworkFile)
            .where(HomeworkFile.homework_id == homework_id)
            .order_by(HomeworkFile.id)
        )
        return list(res.scalars().all())


async def delete_homework(homework_id: int) -> None:
    async with SessionLocal() as s:
        await s.execute(
            delete(HomeworkFile).where(HomeworkFile.homework_id == homework_id)
        )
        await s.execute(delete(Homework).where(Homework.id == homework_id))
        await s.commit()


# ---------------------------------------------------------------------------
# Кэш расписания
# ---------------------------------------------------------------------------
async def save_schedule(
    group_name: str,
    days_json: str,
    anchor_date: str,
    anchor_week: str,
) -> None:
    async with SessionLocal() as s:
        res = await s.execute(
            select(ScheduleCache).where(ScheduleCache.group_name == group_name)
        )
        row = res.scalar_one_or_none()
        if row is None:
            row = ScheduleCache(
                group_name=group_name,
                days_json=days_json,
                anchor_date=date.fromisoformat(anchor_date),
                anchor_week=anchor_week,
            )
            s.add(row)
        else:
            row.days_json = days_json
            row.anchor_date = date.fromisoformat(anchor_date)
            row.anchor_week = anchor_week
        await s.commit()


async def get_schedule(group_name: str) -> dict | None:
    async with SessionLocal() as s:
        res = await s.execute(
            select(ScheduleCache).where(ScheduleCache.group_name == group_name)
        )
        row = res.scalar_one_or_none()
    if row is None:
        return None
    return {
        "days": json.loads(row.days_json),
        "anchor_date": row.anchor_date.isoformat(),
        "anchor_week": row.anchor_week,
    }


# ---------------------------------------------------------------------------
# Кэш замен
# ---------------------------------------------------------------------------
async def save_changes(
    group_name: str,
    change_date: str | None,
    snapshot_json: str,
) -> None:
    async with SessionLocal() as s:
        res = await s.execute(
            select(ChangesCache).where(ChangesCache.group_name == group_name)
        )
        row = res.scalar_one_or_none()
        parsed_date = date.fromisoformat(change_date) if change_date else None
        if row is None:
            row = ChangesCache(
                group_name=group_name,
                change_date=parsed_date,
                snapshot_json=snapshot_json,
            )
            s.add(row)
        else:
            row.change_date = parsed_date
            row.snapshot_json = snapshot_json
        await s.commit()


async def get_changes(group_name: str) -> dict | None:
    async with SessionLocal() as s:
        res = await s.execute(
            select(ChangesCache).where(ChangesCache.group_name == group_name)
        )
        row = res.scalar_one_or_none()
    if row is None:
        return None
    return {
        "date": row.change_date.isoformat() if row.change_date else None,
        "changes": json.loads(row.snapshot_json),
    }


# ---------------------------------------------------------------------------
# Групповые чаты
# ---------------------------------------------------------------------------
async def add_group_chat(chat_id: int, title: str | None = None) -> None:
    """Запомнить группу, куда добавлен бот."""
    async with SessionLocal() as s:
        res = await s.execute(select(GroupChat).where(GroupChat.chat_id == chat_id))
        row = res.scalar_one_or_none()
        if row is None:
            s.add(GroupChat(chat_id=chat_id, title=title))
        else:
            row.title = title
        await s.commit()


async def remove_group_chat(chat_id: int) -> None:
    """Забыть группу, из которой бота удалили."""
    async with SessionLocal() as s:
        await s.execute(delete(GroupChat).where(GroupChat.chat_id == chat_id))
        await s.commit()


async def list_group_chats() -> list[GroupChat]:
    async with SessionLocal() as s:
        res = await s.execute(select(GroupChat).order_by(GroupChat.id))
        return list(res.scalars().all())
