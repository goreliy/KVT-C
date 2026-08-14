# Технологический стек и сборка

Актуально на 2026-07-30, сверено с `requirements.txt` и кодом.

## Язык и среда выполнения

- **Python 3.9+** — минимум для системы (в `run_kvt.py` используется аннотация `tuple[str, int]`,
  PEP 585, недоступная в 3.8).
- **Python 3.10+** — требуется для подсистемы OPC UA (`asyncua`).
- Шага сборки нет — запуск напрямую интерпретатором.
- Целевые платформы: x86_64 и **ARM v7** (контроллеры, например ИнСАТ M3000-T).
  На ARM-контроллерах обычно нет компилятора, поэтому зависимости должны ставиться
  из готовых wheel-пакетов.

## Зависимости (requirements.txt)

Основные:

| Пакет | Версия | Назначение |
|---|---|---|
| `pymodbus` | 2.5.3 | Служебные утилиты Modbus |
| `pyserial` | 3.5 | Доступ к COM-порту (RS-485) |
| `requests` | 2.28.1 | HTTP-клиент (проксирование visualizer → poller/archiver) |
| `jsonschema` | 4.17.3 | Валидация JSON |
| `asyncua` | 2.0.1 | OPC UA сервер (только Python ≥ 3.10) |
| `paho-mqtt` | 2.1.0 | MQTT-клиент (публикация и приём) |
| `cryptography` | ≥42.0.4,<43 | Транзитивная для asyncua; версия зафиксирована под ARM-сборки |
| `pyopenssl` | ≥24,<25 | Транзитивная для asyncua; зафиксирована во избежание сборки `cffi` из исходников |

Flask-стек ставится **двумя ветками** по версии Python:

| Python | Flask | Werkzeug | Jinja2 | MarkupSafe |
|---|---|---|---|---|
| < 3.14 | 2.0.3 | 2.0.3 | 3.0.3 | 2.1.1 |
| ≥ 3.14 | ≥3.1,<4 | ≥3.1,<4 | ≥3.1,<4 | ≥2.1,<4 |

Причина: Werkzeug 2.0.x обращается к `ast.Str`, удалённому в Python 3.14. Для старого стека
в коде есть патч совместимости `shared/python_compat.py` (`patch_legacy_werkzeug_ast()`),
вызываемый до импорта Flask.

**Опциональные, закомментированы в `requirements.txt` и кодом пока не используются:**
`sqlalchemy` и `apscheduler` — зарезервированы под будущее PostgreSQL-хранилище архива и
планировщик отчётов. Не устанавливать без необходимости: `sqlalchemy==1.4.46` не имеет
wheel-пакета под Python 3.12+ и требует компиляции.

## Фронтенд

- Серверный рендеринг HTML через Jinja2.
- Чистый CSS (без препроцессоров и бандлеров).
- Vanilla JavaScript (без фреймворков).

## Хранение данных

- **JSON-файлы** — основной способ хранения и межпроцессного обмена (`data/*.json`),
  запись атомарная (`atomic_save_json`).
- **Конфигурация** — JSON в `data/config/` с версионированием и автоматическими бэкапами.
- **SQLite** (`data/archive.db`) — зеркало архива, наполняется из `archive.json`.
  Первичное хранилище архива — `archive.json`.
- **PostgreSQL** — описан в спецификации, кодом не реализован.

## Команды

### Запуск (штатный способ)

```bash
# Все сервисы (opcua и mqtt — только при включённом autostart в их конфигах)
python run_kvt.py start
python run_kvt.py status
python run_kvt.py restart
python run_kvt.py stop

# Отдельный сервис
python run_kvt.py start --service poller       # 5001
python run_kvt.py start --service archiver     # 5002
python run_kvt.py start --service visualizer   # 5000
python run_kvt.py start --service opcua        # 4840
python run_kvt.py start --service mqtt         # брокер 1883
```

Логи — `logs/<service>.out.log` и `logs/<service>.err.log`; PID-файлы — `.run/<service>.pid`.

### Запуск модулей напрямую (отладка)

```bash
python -m visualizer.app
python -m poller.app
python -m archiver.app
python -m opcua_server.app
python -m mqtt_bridge.app
```

### Установка

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
source .venv/bin/activate         # Linux
pip install -r requirements.txt
```

### Тесты

```bash
python -m pytest tests/ -q
```

Если в окружении установлен несовместимый плагин `pytest-aiohttp`, отключить его:

```bash
python -m pytest tests/ -q -p no:aiohttp
```

Существующие тесты: `tests/test_launcher.py`, `tests/test_mqtt.py`, `tests/test_opcua.py`.

### Мок-сервер (отладка без оборудования)

```bash
pip install -r MocTestServer/requirements.txt
python MocTestServer/server/run.py
```

## Контейнеризация

Docker в текущем дереве **не реализован** — `Dockerfile` и `docker-compose.yml` отсутствуют,
хотя описаны в спецификации (§11). Штатный способ развёртывания — `run_kvt.py`.

## Языковые соглашения

- Документация, интерфейс, сообщения об ошибках и комментарии — **на русском**.
- Идентификаторы в коде (переменные, функции, классы) — **на английском**.
