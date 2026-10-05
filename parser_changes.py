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
    """Возвращает {'date': 'YYYY-MM-DD', 'changes': [ {lesson, replace_from,
    replace_to, added_at}, ... ]} для заданной группы."""
    soup = BeautifulSoup(html, "html.parser")

    # Дата замен (из заголовка «Замены на <b>DD.MM.YYYY</b>»).
    change_date: date | None = None
    for h in soup.find_all(["h2", "h3", "h4", "h5"]):
        text = h.get_text(" ", strip=True)
        if "замен" in text.lower():
            m = re.search(r"(\d{2}\.\d{2}\.\d{4})", text)
            if m:
                change_date = datetime.strptime(m.group(1), "%d.%m.%Y").date()
                break
    if change_date is None:
        # запасной вариант — первая попавшаяся дата на странице
        m = re.search(r"(\d{2}\.\d{2}\.\d{4})", soup.get_text(" ", strip=True))
        change_date = (
            datetime.strptime(m.group(1), "%d.%m.%Y").date() if m else date.today()
        )

    changes: list[dict] = []
    for table in soup.find_all("table"):
        caption = table.find("caption")
        if caption is None:
            continue
        caption_text = caption.get_text(" ", strip=True)  # «Группа: П-4-23»
        groups = [
            x.strip()
            for x in re.split(r"Группа:|;|,", caption_text)
            if x.strip()
        ]
        if group_name not in groups:
            continue

        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 4:
                continue
            changes.append(
                {
                    "lesson": tds[0].get_text(" ", strip=True),
                    "replace_from": tds[1].get_text(" ", strip=True),
                    "replace_to": tds[2].get_text(" ", strip=True),
                    "added_at": tds[3].get_text(" ", strip=True),
                }
            )

    return {"date": change_date.isoformat(), "changes": changes}


async def fetch_changes(group_name: str, url: str | None = None) -> dict:
    """Скачивает и парсит замены для группы."""
    url = url or CHANGES_URL
    async with httpx.AsyncClient(timeout=40, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        if resp.encoding is None:
            resp.encoding = "utf-8"
        return parse_changes(resp.text, group_name)
