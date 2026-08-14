# 13. Зависимости (requirements.txt)

Фактическое содержимое `requirements.txt` (сверено 2026-07-30):

```
pymodbus==2.5.3
pyserial==3.5
requests==2.28.1
jsonschema==4.17.3
asyncua==2.0.1; python_version >= "3.10"
# asyncua тянет cryptography и pyopenssl. На armv7-контроллере нет компилятора, а cffi>=2.0
# (нужен новым cryptography, который тянет свежий pyopenssl) не имеет armv7l-колеса → сборка
# падает. Фиксируем версии, совместимые с предустановленными на контроллере cryptography 42 /
# cffi 1.16 (на них ничего не пересобирается):
cryptography>=42.0.4,<43
pyopenssl>=24,<25
paho-mqtt==2.1.0

# Опционально — только для будущего Archive Manager с PostgreSQL и планировщика отчётов.
# Кодом пока НЕ используются. У sqlalchemy==1.4.46 нет готового wheel под Python 3.12+,
# поэтому он собирается из исходников и ронял установку на контроллере. Включайте при
# необходимости, желательно современные версии с колёсами под вашу платформу (aarch64/cp312):
#   sqlalchemy>=2.0,<3
#   apscheduler>=3.10

# Python 3.14+ no longer has ast.Str; old Werkzeug 2.0.x crashes while Flask
# builds routes. Keep the legacy stack for old Python, use current Flask stack
# for new Python.
flask==2.0.3; python_version < "3.14"
Werkzeug==2.0.3; python_version < "3.14"
Jinja2==3.0.3; python_version < "3.14"
MarkupSafe==2.1.1; python_version < "3.14"

flask>=3.1,<4; python_version >= "3.14"
Werkzeug>=3.1,<4; python_version >= "3.14"
Jinja2>=3.1,<4; python_version >= "3.14"
MarkupSafe>=2.1,<4; python_version >= "3.14"
```

## 13.1 Пояснения

| Пакет | Для чего |
|---|---|
| `pymodbus` | Служебные утилиты Modbus |
| `pyserial` | COM-порт (RS-485) для транспорта `serial` |
| `requests` | HTTP-клиент: проксирование visualizer → poller/archiver |
| `jsonschema` | Валидация JSON |
| `asyncua` | OPC UA сервер (Подсистема 4); только Python ≥ 3.10 |
| `paho-mqtt` | MQTT Bridge (Подсистема 5) |
| `cryptography`, `pyopenssl` | Транзитивные для `asyncua`; версии зафиксированы для ARM |
| Flask/Werkzeug/Jinja2/MarkupSafe | Веб-стек; две ветки версий по `python_version` |

## 13.2 Минимальная версия Python

- **3.9+** — для системы в целом (в `run_kvt.py` используется `tuple[str, int]`, PEP 585).
- **3.10+** — если нужен OPC UA (`asyncua`).

## 13.3 Установка на ARM-контроллер

На контроллерах (например ИнСАТ M3000-T) как правило **нет компилятора**, поэтому:

- всё должно ставиться из готовых wheel-пакетов; версии `cryptography`/`pyopenssl` зафиксированы
  именно поэтому;
- перед установкой убедиться, что `pip` работоспособен (`python3 -m ensurepip --upgrade`);
- устанавливать через `python3 -m pip install -r requirements.txt`;
- закомментированные `sqlalchemy`/`apscheduler` не включать без необходимости — они требуют
  компиляции.

## 13.4 Отсутствующие зависимости (для нереализованных подсистем)

Пакеты `python-telegram-bot`, `matplotlib`, `Pillow`, `psycopg2-binary` в `requirements.txt
**не входят**, поскольку соответствующие подсистемы (Telegram Bot — §8, генератор отчётов,
PostgreSQL-хранилище архива) не реализованы. При их реализации зависимости нужно будет добавить
с учётом наличия wheel-пакетов под ARM.
