# mpt-bot

Telegram-бот расписания и домашних заданий студенческой группы МПТ
(Московский приборостроительный техникум).

## Что делает

- Парсит расписание одной группы с `mpt.ru/raspisanie/` и кэширует его в SQLite.
- Учитывает чётность недель (числитель / знаменатель).
- Отслеживает замены на `mpt.ru/izmeneniya-v-raspisanii/` и уведомляет о новых.
- Хранит домашние задания, добавленные вручную через админ-панель.
- Показывает ДЗ в личном чате и в групповом чате (только на сегодня и завтра).
- Разграничивает роли: студент, модератор, администратор.

## Стек

- Python 3.10+
- aiogram 3.x
- SQLAlchemy 2.0 (async), SQLite через aiosqlite
- httpx, BeautifulSoup4
- APScheduler

## Запуск

### Локально

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1        # Windows
source .venv/bin/activate         # Linux/macOS
pip install -r requirements.txt
cp .env.example .env              # указать BOT_TOKEN и ADMIN_IDS
python main.py
```

### Docker

```bash
docker compose up -d --build
```

Развёртывание на VPS описано в `DEPLOY.md`.

## Настройка

Параметры задаются в `config.py` и `.env`.

| Параметр | Назначение |
|---|---|
| `BOT_TOKEN` | токен бота |
| `ADMIN_IDS` | Telegram ID администраторов, через запятую |
| `GROUP_NAME` | название группы, как на сайте МПТ |
| `CALL_SCHEDULE` | время пар: номер пары -> `"HH.MM-HH.MM"` |
| `WEEK_COLOR_MAP` | цвет плашки на сайте -> неделя |
| `SCHEDULE_REFRESH_MINUTES` | период обновления расписания |
| `CHANGES_REFRESH_MINUTES` | период обновления замен |
| `THROTTLE_SECONDS` | минимальный интервал между действиями пользователя |
| `TIMEZONE` | часовой пояс |

## Роли

| Действие | Студент | Модератор | Администратор |
|---|---|---|---|
| Расписание и ДЗ | да | да | да |
| Добавление/завершение/удаление ДЗ | — | да | да |
| Добавление модераторов | — | — | да |
| Рассылка | — | — | да |

## Команды

Личный чат:

- `/start` — приветствие с Telegram ID, главное меню
- `/menu` — главное меню

Групповой чат:

- `/start`, `/menu` — меню ДЗ
- `/today`, `/tomorrow` — ДЗ на сегодня / завтра

## Парсинг

- `parser.py` — расписание. Страница отдаёт HTML-таблицы: вкладка отделения,
  затем вкладка группы, затем таблицы по дням недели. Пары, чередующиеся между
  неделями, закодированы цветом; парсер раскладывает их на числитель и
  знаменатель.
- `parser_changes.py` — замены. Таблица с заголовком «Группа: ...». Снимок
  сравнивается с предыдущим, новые записи рассылаются пользователям.

Данные хранятся в SQLite: таблицы `users`, `homework`, `schedule_cache`,
`changes_cache`.

## Структура

```
config.py            конфигурация
parser.py            парсер расписания
parser_changes.py    парсер замен
keyboards.py         клавиатуры
services.py          обновление данных, рассылки
utils.py             форматирование
main.py              точка входа (aiogram, APScheduler, throttling)
database/            модели, сессия, CRUD
handlers/            user, group, admin, states
Dockerfile
docker-compose.yml
deploy.ps1           обновление на VPS одной командой
DEPLOY.md            инструкция по развёртыванию
```

## Примечания

Бот рассчитан на один экземпляр: SQLite и `MemoryStorage` для FSM. Для
нескольких реплик потребуется Redis для FSM и PostgreSQL вместо SQLite.
