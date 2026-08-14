# 10. Структура проекта

> ⚠️ **Раздел описывает ЦЕЛЕВУЮ структуру, включая нереализованные части**
> (`telegram_bot/`, `archiver/storage/*`, `visualizer/services/*`, `shared/models.py`,
> `shared/utils.py`, `Dockerfile`/`docker-compose.yml` и др.).
> **Фактическое дерево кода** — в [project-structure.md](../06-development/project-structure.md).

```
kvt/
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── README.md
│
├── poller/                      # Подсистема 1: Modbus Poller
│   ├── __init__.py
│   ├── app.py                   # Flask приложение (API)
│   ├── modbus_client.py         # Работа с Modbus RTU
│   ├── poller_service.py        # Основной сервис опроса
│   ├── config.py                # Конфигурация
│   └── models.py                # Модели данных
│
├── archiver/                    # Подсистема 2: Archive Manager
│   ├── __init__.py
│   ├── app.py                   # Flask приложение (API)
│   ├── archive_service.py       # Сервис архивирования
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── sqlite_storage.py
│   │   ├── postgres_storage.py
│   │   └── json_storage.py
│   ├── compressor.py            # Алгоритм сжатия данных
│   ├── cleaner.py               # Очистка старых данных
│   ├── temperature_logger.py    # Агрегация температур по периодам
│   ├── violation_tracker.py     # Отслеживание превышений границ
│   └── config.py
│
├── visualizer/                  # Подсистема 3: Web Visualizer
│   ├── __init__.py
│   ├── app.py                   # Flask приложение
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── main.py              # Главная страница
│   │   ├── sensor.py            # Детали датчика
│   │   ├── journal.py           # Журналы (температуры, превышения)
│   │   ├── settings.py          # Настройки
│   │   ├── config_api.py        # API конфигурации
│   │   ├── floorplan.py         # План помещения (страница + API)
│   │   └── api.py               # API для фронтенда
│   ├── services/
│   │   ├── config_service.py    # Сервис управления конфигурацией
│   │   ├── notification_service.py
│   │   └── report_service.py
│   ├── static/
│   │   ├── css/
│   │   ├── js/
│   │   └── backgrounds/
│   └── templates/
│       ├── base.html
│       ├── index.html
│       ├── sensor.html
│       ├── journal/
│       │   ├── temperatures.html
│       │   └── violations.html
│       └── settings/
│           ├── index.html
│           ├── sensors.html
│           ├── poller.html
│           ├── opcua.html
│           ├── archive.html
│           ├── notifications.html
│           ├── reports.html
│           ├── appearance.html
│           └── system.html
│
├── opcua_server/                # Подсистема 4: OPC UA Server
│   ├── __init__.py
│   ├── app.py                   # CLI entrypoint
│   ├── service.py               # asyncua runtime, статус, публикация current
│   └── nodes.py                 # стабильные NodeId и выбор датчиков
│
├── telegram_bot/                # Подсистема 6: Telegram Bot
│   ├── __init__.py
│   ├── bot.py                   # Основной модуль бота
│   ├── handlers.py              # Обработчики команд
│   ├── notifications.py         # Отправка уведомлений
│   ├── report_generator.py      # Генерация отчётов
│   ├── chart_generator.py       # Генерация графиков (matplotlib)
│   ├── scheduler.py             # Планировщик регулярных отчётов
│   └── config.py                # Конфигурация бота
│
├── shared/                      # Общие модули
│   ├── __init__.py
│   ├── models.py                # Общие модели данных
│   ├── config_manager.py        # Менеджер конфигурации
│   ├── current_data.py          # Нормализованный current payload для UI/API/OPC UA
│   └── utils.py                 # Утилиты
│
├── data/                        # Данные (volume в Docker)
│   ├── current.json
│   ├── modbus_log.json
│   ├── opcua_status.json        # Текущее состояние OPC UA сервера
│   ├── availability_daily.json  # Суточный учёт доступности линий и датчиков (ping, счётчики)
│   ├── archive.db
│   ├── archive.json
│   └── config/
│       ├── system_config.json   # Главный конфиг с датчиками
│       ├── poller_config.json
│       ├── opcua_config.json    # Конфиг OPC UA сервера и состава экспортируемых датчиков
│       ├── archive_config.json
│       ├── telegram_config.json # Конфиг Telegram-бота
│       ├── layout.json
│       ├── theme_config.json    # Настройки оформления (темы, цвета, название)
│       ├── floorplan_config.json # Планы помещений
│       ├── mnemo_tree.json      # Дерево датчиков на мнемосхеме
│       ├── notifications.json
│       ├── backups/             # Резервные копии конфигов
│       └── import_backups/      # ZIP-бэкапы перед импортом полного архива
│
└── tests/
    ├── test_poller.py
    ├── test_archiver.py
    ├── test_opcua.py
    ├── test_visualizer.py
    ├── test_telegram_bot.py
    └── test_config.py
```
