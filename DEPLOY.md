# Развёртывание бота на VPS (Docker)

Инструкция для сервера на Ubuntu 22.04/24.04 LTS.

## Подключение к серверу

```bash
ssh root@IP
```

При первом подключении подтвердите отпечаток (`yes`), затем введите пароль root.

## Установка Docker

```bash
curl -fsSL https://get.docker.com | sh
docker --version
docker compose version
```

## Загрузка проекта

Соберите архив проекта и загрузите на сервер (из корня проекта):

```bash
tar -czf bot.tar.gz --exclude=.venv --exclude=__pycache__ --exclude=bot.db --exclude=.git --exclude=data --exclude=bot.tar.gz .
scp bot.tar.gz root@IP:/root/
```

На сервере:

```bash
mkdir -p /root/bot
tar -xzf /root/bot.tar.gz -C /root/bot
cd /root/bot
```

## Настройка .env

```bash
nano /root/bot/.env
```

```env
BOT_TOKEN=...
ADMIN_IDS=...
```

## Запуск

```bash
cd /root/bot
docker compose up -d --build
docker compose logs -f
```

В логах должна появиться строка `Run polling for bot @имя_бота`.

## Управление

| Действие | Команда |
|---|---|
| Логи | `docker compose logs -f` |
| Перезапуск | `docker compose restart` |
| Остановка | `docker compose down` |
| Запуск | `docker compose up -d` |
| Обновление кода | залить файлы заново, затем `docker compose up -d --build` |

Контейнер настроен с `restart: unless-stopped` и поднимается автоматически после
перезагрузки сервера.

## Обновление и база данных

Файл `bot.db` хранится на сервере в `/root/bot/data/` и не теряется при пересборке.
Перед обновлением сделайте согласованную резервную копию средствами SQLite
(простое копирование работающего файла может пропустить незавершённые записи):

```bash
docker compose exec -T bot python -c "import sqlite3, datetime; src=sqlite3.connect('/data/bot.db'); dst=sqlite3.connect('/data/backup-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.db'); src.backup(dst); dst.close(); src.close()"
```

Копия появится в `/root/bot/data/`. Перенесите резервную копию также на другой
носитель. Затем загрузите обновлённый код и выполните:

```bash
docker compose up -d --build
docker compose logs --tail=100 bot
```

При запуске бот последовательно применяет миграции. Старые пользователи, ДЗ,
файлы и кэш сохраняются; добавляются журнал версий схемы и очередь `deliveries`.
Таблица очереди хранится в том же подключённом томе, что и остальные данные.
Не запускайте два экземпляра бота одновременно. Для отката остановите бот и
восстановите соответствующие друг другу версию кода и резервную копию БД.

Рассылки с временной ошибкой повторяются автоматически. Постоянные ошибки
помечаются `failed` и попадают в логи; для диагностики в таблице `deliveries`
есть `status`, `cursor`, `attempts`, `next_attempt_at` и `last_error`.

## Частые проблемы

- `docker: command not found` — Docker не установлен.
- `permission denied` при scp — неверный пароль или IP.
- `BOT_TOKEN не задан` в логах — проверьте `/root/bot/.env`.
- Не хватает места на диске — `docker system prune -a`.

## Примечание

Не запускайте бота локально с тем же `BOT_TOKEN`, пока работает серверный:
Telegram отдаёт обновления только одному подключению. Для локального теста
сначала остановите серверный (`docker compose down`).
