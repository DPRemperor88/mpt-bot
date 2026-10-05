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
    """'06.10.2026 (Вторник)' → '06.10 (Вторник)' (без года, компактнее)."""
    return f"{d.day:02d}.{d.month:02d} ({DAY_RU_FULL[d.weekday()]})"


def format_date_short(d: date) -> str:
    """'06.10'."""
    return f"{d.day:02d}.{d.month:02d}"


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
            lines.append(f"🔄 <b>Замена:</b> {esc(changes_by_num[num])}")
        lines.append("")
    return "\n".join(lines).strip()


def render_homework_list(items: list) -> str:
    """
    Формирует список активных ДЗ:
        [Дисциплина] — [Текст ДЗ] (до [Дата])
    """
    lines: list[str] = []
    for hw in items:
        marker = " 📎" if hw.media_file_id else ""
        lines.append(
            f"📌 <b>{esc(hw.subject)}</b> — {esc(hw.text)} "
            f"(до {format_date_ru(hw.due_date)}){marker}"
        )
    return "\n".join(lines)
