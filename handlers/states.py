"""
handlers/states.py — состояния FSM для опросов в админ-панели.
"""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class AddHomework(StatesGroup):
    """Пошаговое добавление домашнего задания."""
    subject = State()   # выбор/ввод дисциплины
    due_date = State()  # выбор даты сдачи
    text = State()      # ввод текста ДЗ (и/или вложение)


class AddModerator(StatesGroup):
    """Добавление модератора (ID или пересланное сообщение)."""
    waiting = State()


class Broadcast(StatesGroup):
    """Рассылка сообщения всем пользователям."""
    waiting = State()
