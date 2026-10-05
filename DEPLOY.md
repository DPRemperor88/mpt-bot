# 🚀 Деплой бота на VPS (Docker)

Инструкция для твоего сервера: **Ubuntu 20.04 LTS, 1 vCPU / 1 ГБ RAM / 10 ГБ NVMe**.
Всё делается один раз, дальше бот работает сам 24/7 и поднимается после перезагрузок.

---

## 0. Что понадобится

- **IP-адрес сервера** и **пароль root** — возьми в панели хостинга (раздел «Сеть» / «Доступ»).
- **Токен бота** от [@BotFather](https://t.me/BotFather).
- **Твой Telegram ID** от [@userinfobot](https://t.me/userinfobot).

---

## 1. Подключиться к серверу

Открой **PowerShell на своём компьютере** и выполни (замени `IP` на адрес сервера):

```powershell
ssh root@IP
```

- Первый раз спросит `Are you sure you want to continue connecting?` → напиши `yes` и Enter.
- Затем введи пароль (он **не отображается** при вводе — это нормально) и Enter.

Если подключился — увидишь приглашение вида `root@vps-01:~#`.

---

## 2. Установить Docker

На сервере выполни одной строкой:

```bash
curl -fsSL https://get.docker.com | sh
```

Проверь, что установилось:

```bash
docker --version
docker compose version
```

Обе команды должны вывести версию (например `Docker version 27.x` и `Docker Compose version v2.x`).

---

## 3. Загрузить проект на сервер

Открой **второе окно PowerShell на своём компьютере** (важно — это команды для Windows, не для сервера):

```powershell
cd C:\path\to\bot
tar -czf bot.tar.gz --exclude=.venv --exclude=__pycache__ --exclude=bot.db --exclude=.git .
scp bot.tar.gz root@IP:/root/
```

Затем **вернись в окно, где подключён сервер**, и распакуй:

```bash
mkdir -p /root/bot
tar -xzf /root/bot.tar.gz -C /root/bot
cd /root/bot
ls
```

Должны увидеть `main.py`, `config.py`, `Dockerfile`, `docker-compose.yml` и остальные файлы.

---

## 4. Создать .env с токеном

На сервере:

```bash
nano /root/bot/.env
```

Вставь (замени на свои значения):

```
BOT_TOKEN=123456:AA...твой_токен
ADMIN_IDS=123456789
```

Сохранить и выйти: **Ctrl+O** → **Enter** → **Ctrl+X**.

Проверить:

```bash
cat /root/bot/.env
```

---

## 5. Запустить бота

```bash
cd /root/bot
docker compose up -d --build
```

Первая сборка займёт 1–3 минуты (скачивается Python и библиотеки).

---

## 6. Проверить, что работает

```bash
docker compose logs -f
```

В логах должно появиться что-то вроде:

```
Bot: @имя_твоего_бота
Run polling for bot @имя_твоего_бота
```

Выход из просмотра логов — **Ctrl+C** (бот при этом продолжает работать).

Теперь напиши боту `/start` в Telegram — он должен ответить меню. 🎉

---

## Управление ботом

| Действие | Команда (в папке `/root/bot`) |
|---|---|
| Логи в реальном времени | `docker compose logs -f` |
| Последние 100 строк логов | `docker compose logs --tail=100` |
| Перезапустить | `docker compose restart` |
| Остановить | `docker compose down` |
| Запустить снова | `docker compose up -d` |
| Обновить код после правок | залить заново (шаг 3) → `docker compose up -d --build` |
| Статус | `docker compose ps` |

---

## Автозапуск

Ничего делать не нужно: в `docker-compose.yml` стоит `restart: unless-stopped`, а Docker
сам стартует при загрузке системы. Бот поднимется автоматически после перезагрузки сервера.

---

## База данных и бэкапы

Файл `bot.db` лежит на сервере в `/root/bot/data/bot.db` и **не теряется** при пересборке образа.

Сделать бэкап:

```bash
cp /root/bot/data/bot.db ~/bot-backup-$(date +%F).db
```

---

## Частые проблемы

**`docker: command not found`** — Docker не установился. Повтори шаг 2 и проверь вывод на ошибки.

**`permission denied` при scp** — неверный пароль или IP. Проверь данные в панели хостинга.

**Бот не отвечает, в логах `BOT_TOKEN не задан`** — проверь `.env` (шаг 4), затем `docker compose restart`.

**Ошибка парсинга расписания в логах** — сайт МПТ мог изменить вёрстку. Пришли текст ошибки,
и мы поправим `parser.py`.

**Не хватает места на диске (10 ГБ)** — почисти старые образы:
```bash
docker system prune -a
```
