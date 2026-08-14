# 11. Запуск и развёртывание

## 11.1 Локальный запуск (разработка)

```bash
# Установка зависимостей
pip install -r requirements.txt

# Запуск отдельных подсистем напрямую (отладка, в разных терминалах)
python -m poller.app --port 5001
python -m archiver.app --port 5002
python -m visualizer.app --port 5000
python -m opcua_server.app
python -m mqtt_bridge.app

# ШТАТНЫЙ способ — единый launcher
python run_kvt.py start                      # все сервисы
python run_kvt.py status
python run_kvt.py restart --service opcua
python run_kvt.py stop --service mqtt
```

Доступные значения `--service`: `all` (по умолчанию), `poller`, `archiver`, `visualizer`,
`opcua`, `mqtt`. Сервисы `opcua` и `mqtt` при `--service all` поднимаются **только при
включённом `autostart`** в их конфигурации; ручной запуск по имени доступен всегда.

Логи — `logs/<service>.out.log` и `logs/<service>.err.log`; PID-файлы — `.run/<service>.pid`.

> ⚠️ Модуля `telegram_bot.bot` в коде нет — подсистема Telegram Bot (§8) не реализована.

## 11.2 Docker Compose

> ⚠️ **НЕ РЕАЛИЗОВАНО.** Ни `docker-compose.yml`, ни `Dockerfile` в дереве проекта отсутствуют.
> Приведённые ниже §11.2–11.3 — целевой вариант контейнеризации на будущее (в нём также
> присутствует нереализованный сервис `telegram_bot`). Штатный способ развёртывания —
> `run_kvt.py` (§11.1).

```yaml
version: '3.8'

services:
  poller:
    build:
      context: .
      dockerfile: Dockerfile
    command: python -m poller.app
    ports:
      - "5001:5001"
    volumes:
      - ./data:/app/data
      - /dev:/dev
    privileged: true  # Для доступа к COM-портам
    restart: unless-stopped

  archiver:
    build:
      context: .
      dockerfile: Dockerfile
    command: python -m archiver.app
    ports:
      - "5002:5002"
    volumes:
      - ./data:/app/data
    depends_on:
      - poller
    restart: unless-stopped

  visualizer:
    build:
      context: .
      dockerfile: Dockerfile
    command: python -m visualizer.app
    ports:
      - "5000:5000"
    volumes:
      - ./data:/app/data
    depends_on:
      - poller
      - archiver
    restart: unless-stopped

  opcua:
    build:
      context: .
      dockerfile: Dockerfile
    command: python -m opcua_server.app
    ports:
      - "4840:4840"
    volumes:
      - ./data:/app/data
    depends_on:
      - poller
    restart: unless-stopped

  telegram_bot:
    build:
      context: .
      dockerfile: Dockerfile
    command: python -m telegram_bot.bot
    volumes:
      - ./data:/app/data
    depends_on:
      - archiver
    restart: unless-stopped
```

## 11.3 Dockerfile

```dockerfile
FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "visualizer.app"]
```
