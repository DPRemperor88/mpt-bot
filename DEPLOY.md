# Деплой бота на VPS (Docker)

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

Если подключился — увидишь приглашение вида `root@ubuntu-server:~#`.

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
tar -czf bot.tar.gz --exclude=.venv --exclude=__pycache__ --exclude=bot.db --exclude=.git --exclude=bot.tar.gz --exclude=data .
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

Теперь напиши боту `/start` в Telegram — он должен ответить меню.

---

## Управление ботом

| Действие | Команда (в папке `/root/bot`) |
|---|---|
| Логи в реальном времени | `docker compose logs -f` |
| Последние 100 строк логов | `docker compose logs --tail=100` |
| Перезапустить | `docker compose restart` |
| Остановить | `docker compose down` |
| Запустить снова | `docker compose up -d` |
| Обновить код после правок | `.\deploy.ps1` на своём ПК (см. ниже) |
| Статус | `docker compose ps` |

---

## Обновление кода после правок (безопасно)

Данные при обновлении **не теряются**: `bot.db` лежит на сервере в `/root/bot/data/`
и не входит ни в архив, ни в образ.

Порядок:

1. Правишь код на своём компьютере.
2. Сохраняешь версию в git:
   ```powershell
   git add -A
   git commit -m "fix: что изменил"
   ```
3. Обновляешь сервер **одной командой**:
   ```powershell
   cd C:\path\to\bot
   .\deploy.ps1
   ```
   Скрипт сам соберёт архив (без `.venv`, `.git`, `bot.db`), зальёт на сервер,
   пересоберёт контейнер и покажет логи. Пароль спросит 2 раза — это нормально.

   Адрес сервера скрипт берёт из `.env` (строка `SERVER_HOST=root@IP`) — так IP
   не хранится в репозитории. Либо передай явно: `.\deploy.ps1 -Server root@IP`.

Вручную (если скрипт не подходит):

```powershell
cd C:\path\to\bot
tar -czf bot.tar.gz --exclude=.venv --exclude=__pycache__ --exclude=bot.db --exclude=.git --exclude=data --exclude=bot.tar.gz .
scp bot.tar.gz root@IP:/root/
ssh root@IP "cd /root/bot && tar -xzf /root/bot.tar.gz -C /root/bot && docker compose up -d --build"
```

### ⚠️ Не запускай бота локально с тем же токеном

Пока работает серверный бот, локальный запуск с тем же `BOT_TOKEN` вызовет конфликт:
Telegram отдаёт обновления только одному подключению. Нужно протестировать локально —
сначала останови серверного (`docker compose down`), после — подними обратно (`docker compose up -d`).

### Откат, если что-то сломалось

```powershell
git log --oneline        # посмотреть версии
git checkout <хеш>       # вернуться на рабочую версию
.\deploy.ps1             # залить её на сервер
```

### Бэкап базы перед рискованными изменениями

```bash
cp /root/bot/data/bot.db ~/bot-$(date +%F).db
```

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
