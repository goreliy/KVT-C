# Структура проекта

Актуально на 2026-07-30, сверено с деревом кода.

## Архитектура

Пять независимых сервисов, обменивающихся данными через JSON-файлы в `data/`
(единый launcher — `run_kvt.py`):

```
                     Modbus RTU (COM / UDP через С2000-Ethernet)
                                    │
                          ┌─────────▼─────────┐
                          │  poller  :5001    │  → data/current.json
                          │                   │  → data/modbus_log.json
                          └─────────┬─────────┘  → data/availability_daily.json
                                    │ current.json
              ┌─────────────────────┼─────────────────────┐
              ▼                     ▼                     ▼
    ┌───────────────────┐ ┌──────────────────┐ ┌──────────────────┐
    │ archiver  :5002   │ │  opcua    :4840  │ │  mqtt   (брокер) │
    │ archive.json      │ │ opcua_status.json│ │ mqtt_status.json │
    │ archive.db (зерк.)│ └──────────────────┘ │ mqtt_inbound.json│
    │ archive_daily.json│                      └──────────────────┘
    └─────────┬─────────┘
              │
              ▼
    ┌───────────────────┐
    │ visualizer :5000  │ ← веб-интерфейс, настройки, журналы, журнал учёта
    └───────────────────┘
```

Взаимодействие: `poller` и `archiver` не знают друг о друге напрямую — связь через файлы.
`visualizer` дополнительно проксирует HTTP-запросы к `poller` и `archiver`.

## Дерево каталогов

```
├── run_kvt.py                      # ЕДИНАЯ точка запуска всех сервисов (start/stop/restart/status)
│
├── shared/                         # Общие модули для всех подсистем
│   ├── config_manager.py           # Загрузка/сохранение всех конфигов, валидация, CRUD датчиков, бэкапы
│   ├── current_data.py             # Нормализация текущего среза (единый источник для UI/API/OPC UA/MQTT)
│   ├── net.py                      # Определение своего IP; resolve_self_host/local_ip (без хардкода 127.0.0.1)
│   ├── availability.py             # Суточный учёт доступности линий и датчиков, ping
│   ├── logbook.py                  # Журнал учёта: журналы, операторы, календарь, суточные строки, подписи
│   ├── config_bundle.py            # Экспорт/импорт полного конфигурационного ZIP-архива
│   └── python_compat.py            # Патч ast для запуска Flask 2.0.x на новых Python
│
├── poller/                         # Подсистема 1: Modbus Poller (порт 5001)
│   ├── app.py                      # Flask REST API + служебная страница
│   ├── poller_service.py           # PollPortManager, воркеры линий, цикл опроса, медленный цикл, watchdog
│   ├── modbus_client.py            # Modbus RTU: COM, UDP, UDP+обёртка С2000-ПП, CRC16
│   └── config.py                   # Нормализация и валидация poller_config.json
│
├── archiver/                       # Подсистема 2: Archive Manager (порт 5002)
│   ├── app.py                      # Flask REST API + служебная страница
│   └── archive_service.py          # Забор данных, компрессия, ретенция, SQLite-зеркало, суточная вьюха
│
├── visualizer/                     # Подсистема 3: Web Visualizer (порт 5000)
│   ├── app.py                      # Flask app factory (create_app), регистрация blueprint'ов
│   ├── routes/                     # 6 blueprint'ов: main, settings, api, floorplan, journal, export
│   ├── templates/                  # Jinja2: base.html, index, sensor, floorplan, logbook, logbook_print
│   │   ├── settings/               # Страницы настроек (13 шаблонов, включая opcua, mqtt, network)
│   │   └── journal/                # Журналы: events, temperatures, violations
│   └── static/                     # css/, floorplans/
│
├── opcua_server/                   # Подсистема 4: OPC UA Server (порт 4840)
│   ├── app.py                      # CLI entrypoint
│   ├── service.py                  # asyncua-рантайм, адресное пространство, публикация, статус
│   └── nodes.py                    # Стабильные NodeId, выбор датчиков и полей
│
├── mqtt_bridge/                    # Подсистема 5: MQTT Bridge
│   ├── app.py                      # CLI entrypoint
│   └── service.py                  # paho-mqtt: публикация, подписки, команды, статус
│
├── data/                           # Runtime-данные (JSON)
│   ├── current.json                # Текущие показания от поллера
│   ├── modbus_log.json             # Журнал обмена Modbus (кольцевой, лимит log_max_entries)
│   ├── archive.json                # Сжатые исторические измерения (основное хранилище архива)
│   ├── archive.db                  # SQLite-зеркало архива (при включённом хранилище)
│   ├── archive_daily.json          # Суточная вьюха (источник для журнала учёта)
│   ├── availability_daily.json     # Суточная доступность линий и датчиков
│   ├── events.json                 # Журнал событий (тревоги/предупреждения)
│   ├── logbook_signoffs.json       # Подписи журнала учёта (со снимками значений)
│   ├── opcua_status.json           # Состояние OPC UA сервиса
│   ├── mqtt_status.json            # Состояние MQTT сервиса
│   ├── mqtt_inbound.json           # Журнал входящих MQTT-сообщений
│   └── config/                     # Все конфигурации
│       ├── system_config.json      # Датчики, сеть, метаданные системы, версионность
│       ├── poller_config.json      # Линии опроса (poll_ports[]), параметры Modbus
│       ├── archive_config.json     # Режимы забора, хранилища, компрессия, ретенция
│       ├── opcua_config.json       # OPC UA: сервер, публикация, выбор датчиков, security, HA
│       ├── mqtt_config.json        # MQTT: брокер, топики, публикация, приём, TLS
│       ├── notifications.json      # Настройки уведомлений
│       ├── layout.json             # Позиции плашек на мнемосхеме
│       ├── theme_config.json       # Темы, цвета, название
│       ├── floorplan_config.json   # Планы помещений с размещением датчиков
│       ├── mnemo_tree.json         # Дерево датчиков
│       ├── reports_config.json     # Журналы учёта (складская отчётность)
│       ├── operators.json          # Операторы (ФИО) для подписей журнала
│       ├── holidays.json           # Календарь выходных и праздников
│       ├── flask_secret.key        # Секрет Flask (генерируется автоматически)
│       ├── backups/                # Версионированные бэкапы конфигурации
│       └── import_backups/         # Бэкапы перед импортом ZIP-архива
│
├── MocTestServer/                  # Мок-сервер: эмуляция С2000-ПП и данных для отладки без железа
├── tests/                          # test_launcher.py, test_mqtt.py, test_opcua.py
├── docs/                           # Документация (см. docs/README.md)
├── logs/                           # Логи сервисов (*.out.log, *.err.log)
├── .run/                           # PID-файлы запущенных сервисов
└── requirements.txt                # Зависимости Python
```

## Ключевые паттерны кода

- **Единая точка запуска:** `run_kvt.py` — словарь `SERVICES` описывает все сервисы (модуль, порт,
  host, PID-файл, логи). Сервисы `opcua` и `mqtt` поднимаются при `--service all` только при
  включённом `autostart` в их конфигурации.
- **Flask app factory:** `create_app()` в `visualizer/app.py`.
- **Blueprints:** `main_bp`, `settings_bp` (`/settings`), `api_bp` (`/api`), `floorplan_bp`
  (`/floorplan`), `journal_bp`, `export_bp`.
- **Централизованная конфигурация:** всё чтение/запись конфигов идёт через
  `shared/config_manager.py`; там же валидация и версионирование с автоматическим бэкапом.
- **Атомарная запись JSON:** `atomic_save_json` (запись во временный файл + `os.replace`) —
  чтобы другие подсистемы не прочитали половину файла.
- **Единый нормализованный срез:** `shared/current_data.py` — один источник состава датчиков и
  fallback-значений для UI, `/api/current`, OPC UA и MQTT.
- **Собственный IP вместо петлевого:** `shared/net.py` — self-маркеры (`0.0.0.0`, `localhost`,
  пусто) разрешаются в актуальный IP машины; жёсткого `127.0.0.1` в коде и конфигах нет.
- **Непрерывность опроса:** цикл воркера линии защищён от любых исключений, watchdog (5 с)
  пересоздаёт упавшие воркеры; журнал ограничен `log_max_entries` целиком.
- **Тема через context_processor:** доступна во всех шаблонах.
- **`sys.path.insert(0, ...)`** в `app.py` каждой подсистемы — чтобы резолвился пакет `shared`.
- **Патч совместимости:** `patch_legacy_werkzeug_ast()` вызывается до импорта Flask
  (Werkzeug 2.0.x на новых Python).

## Чего в коде НЕТ (описано в спецификации как план)

Чтобы не искать несуществующее: `telegram_bot/`, `report_gen/`, `Dockerfile`/`docker-compose.yml`,
`shared/models.py`, `shared/utils.py`, `archiver/storage/*`, `visualizer/services/*`,
`visualizer/routes/sensor.py` (страница датчика — в `main.py`), `visualizer/routes/logbook.py`
(журнал учёта — в `journal.py` + `api.py` + `shared/logbook.py`).
Сводка состояния — в [индексе документации](../README.md).
