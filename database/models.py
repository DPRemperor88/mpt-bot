"""
database/models.py — модели SQLAlchemy 2.0.

Таблицы:
  * users           — зарегистрированные пользователи и их роли;
  * homework        — домашние задания, добавленные через админ-панель;
  * schedule_cache  — кэш расписания (JSON по дням недели, обе недели сразу);
  * changes_cache   — снимок страницы «Изменения в расписании» для группы.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    """Текущее время в UTC (naive, для хранения в SQLite)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    """Пользователь бота. role: student | moderator | admin."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    role: Mapped[str] = mapped_column(String(16), default="student")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class Homework(Base):
    """Домашнее задание. due_date — дата, до которой нужно сдать."""

    __tablename__ = "homework"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject: Mapped[str] = mapped_column(String(256), index=True)
    text: Mapped[str] = mapped_column(Text)
    due_date: Mapped[date] = mapped_column(Date, index=True)
    # Прикреплённый файл/фото (file_id Telegram) и его тип: photo | document.
    media_file_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    media_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class ScheduleCache(Base):
    """
    Кэш расписания одной группы.

    days_json — JSON вида {"0": [lesson, ...], ...}, где ключ — индекс дня
    недели (0 = понедельник). Каждый lesson уже содержит варианты для обеих
    недель (числитель/знаменатель), поэтому хранится одна строка на группу.
    anchor_date / anchor_week — «якорь» чётности: какую неделю сайт объявил
    текущей на момент парсинга.
    """

    __tablename__ = "schedule_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_name: Mapped[str] = mapped_column(String(64), unique=True)
    days_json: Mapped[str] = mapped_column(Text)
    anchor_date: Mapped[date] = mapped_column(Date)
    anchor_week: Mapped[str] = mapped_column(String(16))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class ChangesCache(Base):
    """Снимок замен для группы (snapshot_json — JSON-список замен)."""

    __tablename__ = "changes_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_name: Mapped[str] = mapped_column(String(64), unique=True)
    change_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    snapshot_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class GroupChat(Base):
    """Групповой чат, куда добавлен бот (для уведомлений о заменах)."""

    __tablename__ = "group_chats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class HomeworkFile(Base):
    """Файл, прикреплённый к домашнему заданию (их может быть несколько)."""

    __tablename__ = "homework_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    homework_id: Mapped[int] = mapped_column(Integer, index=True)
    file_id: Mapped[str] = mapped_column(String(256))
    file_type: Mapped[str] = mapped_column(String(16))  # photo | document
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class ActionLog(Base):
    """Журнал действий модераторов и администраторов."""

    __tablename__ = "action_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    action: Mapped[str] = mapped_column(String(32))
    details: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class NotificationSetting(Base):
    """Настройка уведомлений. Нет строки — уведомление включено."""

    __tablename__ = "notification_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    kind: Mapped[str] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (
        UniqueConstraint("telegram_id", "kind", name="uq_notify_user_kind"),
    )


class Meta(Base):
    """Мелкое состояние бота: ключ и значение."""

    __tablename__ = "meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
