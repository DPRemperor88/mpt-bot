"""
parser_changes.py — парсер страницы «Изменения в расписании».

Структура https://mpt.ru/izmeneniya-v-raspisanii/ :
  <h4>Замены на <b>06.10.2026</b> (Вторник)</h4>
  <table class="table table-striped">
      <caption>Группа: <b>П-4-23</b></caption>
      <tr><th>Пара</th><th>Что заменяют</th><th>На что заменяют</th><th>Замена добавлена</th></tr>
      <tr><td class="lesson-number">3</td>
          <td class="replace-from">…</td>
          <td class="replace-to">…</td>
          <td class="updated-at">05.10.2026 14:50:25</td></tr>
  </table>

Название группы в caption может быть составным через запятую или точку с
запятой (например «ИИ-1-25, ИИ-11-26»), поэтому проверяем вхождение токеном.
"""
from __future__ import annotations

import re
from datetime import date, datetime

import httpx
from bs4 import BeautifulSoup

from config import CHANGES_URL


def parse_changes(html: str, group_name: str) -> dict:
    """Возвращает опубликованные dates и changes с собственной date у каждой записи."""
    soup = BeautifulSoup(html, "html.parser")

    # Дата замен (из заголовка «Замены на <b>DD.MM.YYYY</b>»).
    change_date: date | None = None
    dates: list[str] = []
    changes: list[dict] = []
    recognized_table = False
    for node in soup.find_all(["h2", "h3", "h4", "h5", "table"]):
        if node.name == "table":
            caption = node.find("caption")
            if caption is None:
                if node.select(".lesson-number, .replace-from, .replace-to"):
                    raise ValueError("Таблица замен без названия группы")
                continue
            recognized_table = recognized_table or "Группа:" in caption.get_text()
            groups = [x.strip() for x in re.split(r"Группа:|;|,", caption.get_text(" ", strip=True)) if x.strip()]
            if group_name not in groups:
                continue
            if change_date is None:
                raise ValueError("Таблица замен без даты")
            for tr in node.find_all("tr"):
                tds = tr.find_all("td")
                if not tds:
                    continue
                if len(tds) < 4:
                    raise ValueError("Неизвестная структура строки замен")
                changes.append({
                    "date": change_date.isoformat(),
                    "lesson": tds[0].get_text(" ", strip=True),
                    "replace_from": tds[1].get_text(" ", strip=True),
                    "replace_to": tds[2].get_text(" ", strip=True),
                    "added_at": tds[3].get_text(" ", strip=True),
                })
            continue
        text = node.get_text(" ", strip=True)
        if "замен" in text.lower():
            change_date = None
            m = re.search(r"(\d{2}\.\d{2}\.\d{4})", text)
            if m:
                change_date = datetime.strptime(m.group(1), "%d.%m.%Y").date()
                dates.append(change_date.isoformat())
    if not dates:
        raise ValueError("Не найдены заголовки с датами замен; кэш сохранён")
    if not recognized_table and not re.search(r"замен нет|изменений нет|нет замен", soup.get_text(" ", strip=True).lower()):
        raise ValueError("Не распознаны таблицы или явное отсутствие замен")
    return {"date": dates[0], "dates": list(dict.fromkeys(dates)), "changes": changes}


async def fetch_changes(group_name: str, url: str | None = None) -> dict:
    """Скачивает и парсит замены для группы."""
    url = url or CHANGES_URL
    async with httpx.AsyncClient(timeout=40, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        if resp.encoding is None:
            resp.encoding = "utf-8"
        return parse_changes(resp.text, group_name)
