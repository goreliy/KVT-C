# 2. Архитектура системы

## 2.1 Структурная схема

```mermaid
flowchart TB
    subgraph hardware [Аппаратный уровень]
        sensors[Датчики С2000-ВТ]
        rs485[RS-485]
    end
    
    subgraph poller [Подсистема 1: Modbus Poller]
        modbus_service[modbus_poller.py]
        current_json[(current.json)]
        modbus_log[(modbus_log.json)]
    end
    
    subgraph archiver [Подсистема 2: Archive Manager]
        archive_service[archive_manager.py]
        sqlite_db[(SQLite)]
        postgres_db[(PostgreSQL)]
        json_archive[(archive.json)]
    end
    
    subgraph visualizer [Подсистема 3: Web Visualizer]
        flask_app[Flask App]
        web_ui[Web UI]
    end

    subgraph opcua [Подсистема 4: OPC UA Server]
        opcua_service[opcua_server.app]
        opcua_status[(opcua_status.json)]
    end
    
    subgraph telegram_bot [Подсистема 6: Telegram Bot]
        bot_service[telegram_bot.py]
        scheduler[Планировщик отчётов]
        chart_gen[Генератор графиков]
    end
    
    sensors -->|Modbus RTU| rs485
    rs485 --> modbus_service
    modbus_service --> current_json
    modbus_service --> modbus_log
    
    current_json --> archive_service
    archive_service --> sqlite_db
    archive_service --> postgres_db
    archive_service --> json_archive
    
    current_json --> flask_app
    sqlite_db --> flask_app
    postgres_db --> flask_app
    json_archive --> flask_app
    flask_app --> web_ui
    current_json --> opcua_service
    opcua_service --> opcua_status
    
    current_json --> bot_service
    sqlite_db --> bot_service
    postgres_db --> bot_service
    bot_service --> scheduler
    bot_service --> chart_gen
    archive_service -->|события/превышения| bot_service
```

## 2.2 Взаимодействие подсистем

```mermaid
sequenceDiagram
    participant P as Modbus Poller
    participant F as current.json
    participant A as Archive Manager
    participant DB as Storage
    participant V as Web Visualizer
    participant O as OPC UA Server
    participant T as Telegram Bot
    
    loop Каждый период опроса
        P->>P: Опрос датчиков по Modbus
        P->>F: Запись текущих значений
    end
    
    loop Каждый период архивации
        A->>F: Чтение current.json
        A->>A: Компрессия данных
        A->>DB: Сохранение в архив
        A->>A: Очистка старых данных
        A-->>T: Уведомление о превышениях
    end
    
    V->>F: Текущие значения
    V->>DB: Исторические данные
    V->>V: Отрисовка UI

    loop Каждый интервал OPC UA публикации
        O->>F: Чтение нормализованного текущего среза
        O->>O: Обновление read-only OPC UA nodes
    end
    
    loop По расписанию
        T->>DB: Запрос данных для отчёта
        T->>T: Генерация графиков
        T->>T: Отправка отчёта в Telegram
    end
    
    Note over T: Обработка команд от пользователей
    T->>F: Запрос текущих значений
    T->>DB: Запрос истории/журнала
```

## 2.3 Независимые подсистемы

| № | Подсистема | Порт | Назначение | Состояние |
|---|------------|------|------------|-----------|
| 1 | Modbus Poller | 5001 | Опрос датчиков по Modbus RTU | реализовано |
| 2 | Archive Manager | 5002 | Архивирование и хранение данных | реализовано |
| 3 | Web Visualizer | 5000 | Веб-интерфейс визуализации, журнал учёта | реализовано |
| 4 | OPC UA Server | 4840 | Read-only передача текущих данных датчиков по OPC UA | реализовано |
| 5 | MQTT Bridge | 1883 (брокер) | Двунаправленный обмен текущими данными по MQTT (см. §7) | реализовано |
| 6 | Telegram Bot | — | Уведомления, графики, отчёты через Telegram (см. §8) | ⚠️ только спецификация |

> Сводная таблица состояния реализации всех подсистем — в [индексе документации](../README.md).
> Запуск и управление сервисами — единой точкой `run_kvt.py` (см. §11); сервисы `opcua` и `mqtt`
> поднимаются при `--service all` только при включённом в их конфигурации `autostart`.
