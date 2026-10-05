# Образ для Telegram-бота (Python 3.12, облегчённый)
FROM python:3.12-slim

# Не буферизовать вывод (чтобы логи сразу шли в docker logs) + часовой пояс
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/Moscow

WORKDIR /app

# Сначала только зависимости — слой кэшируется и пересборка идёт быстро
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Затем код проекта
COPY . .

# База bot.db живёт в /data (подключён томом в docker-compose.yml)
CMD ["python", "main.py"]
