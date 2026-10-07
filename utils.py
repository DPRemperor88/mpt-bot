"""
utils.py — вспомогательные функции: экранирование HTML, нормализация названий,
форматирование дат и сборка текста сообщений.
"""
from __future__ import annotations

import html
import re
from datetime import date

DAY_RU_FULL = [
    "Понедельник", "Вторник", "Среда", "Четверг",
    "Пятница", "Суббота", "Воскресенье",
]
DAY_RU_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

OTHER_WEEK = {"числитель": "знаменатель", "знаменатель": "числитель"}


def esc(value: object) -> str:
    """Экранирует спецсимволы HTML, чтобы пользовательский ввод не ломал разметку."""
    return html.escape(str(value))


def normalize_subject(value: str | None) -> str:
    """Нормализует название дисциплины для сопоставления (регистр/пробелы)."""
    return re.sub(r"\s+", " ", (value or "")).strip().lower()


def format_date_ru(d: date) -> str:
    """'06.10 (Вторник)'."""
    return f"{d.day:02d}.{d.month:02d} ({DAY_RU_FULL[d.weekday()]})"


def format_date_short(d: date) -> str:
    """'06.10'."""
    return f"{d.day:02d}.{d.month:02d}"


def shorten(text: str, limit: int = 30) -> str:
    """Обрезает длинный текст для подписи кнопки."""
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def render_schedule_text(
    lessons: list,
    week: str,
    call_schedule: dict,
    hw_by_subject: dict,
    changes_by_num: dict | None = None,
) -> str:
    """
    Формирует текст «Расписания на день» в формате:

        Дисциплина HH.MM-HH.MM
        <i>Преподаватель</i>
        ДЗ: текст или «—»

    Если по номеру пары есть замена — добавляет строку «Замена: …».
    """
    changes_by_num = changes_by_num or {}
    lines: list[str] = []
    for lesson in sorted(lessons, key=lambda x: x.get("number", 0)):
        variants = lesson.get("variants", {})
        variant = variants.get(week) or variants.get(OTHER_WEEK.get(week))
        if not variant:
            continue
        subject = (variant.get("subject") or "").strip()
        if not subject:
            continue
        teacher = (variant.get("teacher") or "").strip() or "—"
        num = lesson.get("number")
        time = call_schedule.get(num, "—")

        lines.append(f"{esc(subject)} {time}")
        lines.append(f"<i>{esc(teacher)}</i>")
        homework = hw_by_subject.get(normalize_subject(subject))
        lines.append(f"ДЗ: {esc(homework) if homework else '—'}")
        if num in changes_by_num:
            lines.append(f"<b>Замена:</b> {esc(changes_by_num[num])}")
        lines.append("")
    return "\n".join(lines).strip()


def _homework_body(hw, with_files: set[int]) -> str:
    """Текст задания с пометкой, если есть вложение."""
    text = esc(hw.text).strip()
    has_file = bool(hw.media_file_id) or hw.id in with_files
    if text:
        return f"{text} (вложение)" if has_file else text
    return "(вложение)" if has_file else "—"


def render_homework_day(
    items: list,
    target: date,
    label: str,
    with_files: set[int] | None = None,
) -> str:
    """
    Формирует список ДЗ на конкретный день (личка и групповой чат).

    label — «сегодня» или «завтра». with_files — id заданий, у которых есть вложения.
    """
    with_files = with_files or set()

    header = f"<b>Домашнее задание на {label} — {format_date_ru(target)}</b>"
    if not items:
        return f"{header}\n\nЗаданий нет."

    lines = [header, ""]
    for hw in items:
        lines.append(f"• <b>{esc(hw.subject)}</b> — {_homework_body(hw, with_files)}")
    return "\n".join(lines)


def render_homework_all(items: list, with_files: set[int] | None = None) -> str:
    """Список всех предстоящих ДЗ, сгруппированный по дате сдачи."""
    with_files = with_files or set()

    header = "<b>Все задания:</b>"
    if not items:
        return f"{header}\n\nЗаданий нет."

    lines = [header]
    current: date | None = None
    for hw in items:
        if hw.due_date != current:
            current = hw.due_date
            lines.append(f"\n<b>{format_date_ru(current)}</b>")
        lines.append(f"• {esc(hw.subject)} — {_homework_body(hw, with_files)}")
    return "\n".join(lines)
