"""
parser.py — асинхронный парсер расписания сайта МПТ.

Структура страницы https://mpt.ru/raspisanie/ :
  * блок «на дату» с пометкой «Неделя: Числитель/Знаменатель»;
  * вкладки «отделение/специальность» → вкладки групп;
  * у каждой группы — таблицы по дням недели (Пара | Предмет | Преподаватель).

Пары, которые чередуются между неделями, закодированы цветными плашками
(<div class="label label-danger"> / <div class="label label-info">):
  label-danger (красная) = числитель, label-info (синяя) = знаменатель
(см. config.WEEK_COLOR_MAP). Парсер раскладывает их на два варианта недели.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

import httpx
from bs4 import BeautifulSoup

from config import SCHEDULE_URL, TZ, WEEK_COLOR_MAP

# Русские названия дней → индекс weekday() (0 = понедельник).
DAY_NAMES = {
    "ПОНЕДЕЛЬНИК": 0,
    "ВТОРНИК": 1,
    "СРЕДА": 2,
    "ЧЕТВЕРГ": 3,
    "ПЯТНИЦА": 4,
    "СУББОТА": 5,
    "ВОСКРЕСЕНЬЕ": 6,
}

WEEK_TYPES = ("числитель", "знаменатель")
OTHER_WEEK = {"числитель": "знаменатель", "знаменатель": "числитель"}


def _day_index(header_text: str) -> int | None:
    """По тексту заголовка таблицы определяет индекс дня недели."""
    up = header_text.upper()
    for name, idx in DAY_NAMES.items():
        if name in up:
            return idx
    return None


def _label_variants(cell) -> dict | None:
    """
    Если в ячейке есть цветные плашки — возвращает {'числитель': текст,
    'знаменатель': текст}. Иначе None (значит ячейка «однозначная»).
    """
    labels = cell.find_all("div", class_=re.compile(r"\blabel\b"))
    if not labels:
        return None
    result = {"числитель": "", "знаменатель": ""}
    for lbl in labels:
        classes = lbl.get("class") or []
        # Класс называется "label-danger"/"label-info", поэтому проверяем вхождение.
        colors = [c.removeprefix("label-") for c in classes if c.startswith("label-")]
        color = next((c for c in colors if c in WEEK_COLOR_MAP), None)
        if color is None:
            raise ValueError("Неизвестный цвет недели в расписании")
        week = WEEK_COLOR_MAP[color]
        result[week] = lbl.get_text(" ", strip=True)
    return result


def _parse_variants(subject_cell, teacher_cell) -> dict:
    """
    Возвращает {'числитель': {subject, teacher}, 'знаменатель': {subject, teacher}}.
    Для обычной пары оба варианта совпадают; для «двойной» — различаются.
    """
    sv = _label_variants(subject_cell)
    tv = _label_variants(teacher_cell)

    if sv is None and tv is None:
        subject = subject_cell.get_text(" ", strip=True)
        teacher = teacher_cell.get_text(" ", strip=True)
        return {w: {"subject": subject, "teacher": teacher} for w in WEEK_TYPES}

    if sv is None:
        subj = subject_cell.get_text(" ", strip=True)
        sv = {w: subj for w in WEEK_TYPES}
    if tv is None:
        teach = teacher_cell.get_text(" ", strip=True)
        tv = {w: teach for w in WEEK_TYPES}

    return {
        w: {"subject": sv.get(w, ""), "teacher": tv.get(w, "")}
        for w in WEEK_TYPES
    }


def parse_schedule(html: str, group_name: str) -> dict:
    """Разбирает HTML страницы расписания для одной группы."""
    soup = BeautifulSoup(html, "html.parser")

    # 1) Текущая неделя, объявленная сайтом («Неделя: Знаменатель»).
    anchor_week = None
    for h in soup.find_all(["h2", "h3", "h4"]):
        text = h.get_text(" ", strip=True)
        if text.lower().startswith("неделя"):
            span = h.find("span")
            if span is not None:
                span_text = span.get_text(" ", strip=True).lower()
                anchor_week = next((w for w in WEEK_TYPES if w in span_text), None)
            break
    if anchor_week is None:
        raise ValueError("Не удалось определить неделю расписания")

    # 2) Найти вкладку группы (вкладки: <ul class="nav nav-tabs"><li><a href="#id">…).
    target_id: str | None = None
    for a in soup.select('ul.nav-tabs a[href]'):
        label = a.get_text(" ", strip=True)
        tokens = [t.strip() for t in re.split(r"[;,]", label) if t.strip()]
        if group_name in tokens:
            target_id = a.get("href", "").lstrip("#")
            break
    if not target_id:
        raise ValueError(f"Группа «{group_name}» не найдена на странице расписания")

    pane = soup.find(id=target_id)
    if pane is None:
        raise ValueError(
            f"Вкладка группы «{group_name}» найдена, но её содержимое не обнаружено"
        )

    # 3) Разобрать таблицы по дням.
    days: dict[str, list] = {}
    locations: dict[str, str] = {}
    for table in pane.find_all("table"):
        h4 = table.find("h4")
        if h4 is None:
            continue
        day_index = _day_index(h4.get_text(" ", strip=True))
        if day_index is None:
            continue

        span = h4.find("span")
        if span is not None:
            locations[str(day_index)] = span.get_text(" ", strip=True)

        lessons = days.setdefault(str(day_index), [])
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 3:
                continue
            num_txt = tds[0].get_text(" ", strip=True)
            if not num_txt.isdigit():
                continue
            lessons.append(
                {
                    "number": int(num_txt),
                    "variants": _parse_variants(tds[1], tds[2]),
                }
            )

    if not days or not any(days.values()):
        raise ValueError("Пустое или нераспознанное расписание; кэш сохранён")
    return {
        "group": group_name,
        "anchor_date": datetime.now(TZ).date().isoformat(),
        "anchor_week": anchor_week,
        "days": days,
        "locations": locations,
    }


async def fetch_schedule(group_name: str, url: str | None = None) -> dict:
    """Скачивает и парсит расписание группы."""
    url = url or SCHEDULE_URL
    async with httpx.AsyncClient(timeout=40, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        if resp.encoding is None:
            resp.encoding = "utf-8"
        return parse_schedule(resp.text, group_name)


def week_type_for(target: date, anchor_date: date, anchor_week: str) -> str:
    """
    Определяет тип недели (числитель/знаменатель) для произвольной даты,
    отталкиваясь от «якоря» — недели, которую сайт объявил текущей.
    Недели чередуются, поэтому считаем разницу в неделях по понедельникам.
    """
    def monday(d: date) -> date:
        return d - timedelta(days=d.weekday())

    diff_weeks = (monday(target) - monday(anchor_date)).days // 7
    return anchor_week if diff_weeks % 2 == 0 else OTHER_WEEK[anchor_week]


def subjects_from_schedule(data: dict) -> list[str]:
    """Все уникальные названия дисциплин из расписания (для админ-панели)."""
    out: set[str] = set()
    for lessons in data.get("days", {}).values():
        for lesson in lessons:
            for w in WEEK_TYPES:
                subject = (lesson["variants"].get(w, {}).get("subject") or "").strip()
                if subject:
                    out.add(subject)
    return sorted(out)
