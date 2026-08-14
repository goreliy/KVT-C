# 9. Настройки системы и версионность

## 9.1 Главный конфигурационный файл (system_config.json)

Файл содержит все настройки системы с поддержкой версионности.

```json
{
  "config_version": "1.0.3",
  "config_schema_version": "1.0",
  "created_at": "2026-01-01T00:00:00",
  "updated_at": "2026-01-14T15:30:45",
  "update_history": [
    {
      "version": "1.0.1",
      "timestamp": "2026-01-05T10:00:00",
      "changes": "Добавлен датчик ХРАН. № 3"
    },
    {
      "version": "1.0.2",
      "timestamp": "2026-01-10T14:30:00",
      "changes": "Изменены границы для датчика 1"
    },
    {
      "version": "1.0.3",
      "timestamp": "2026-01-14T15:30:45",
      "changes": "Удалён датчик ХРАН. № 2"
    }
  ],
  
  "system": {
    "name": "КВТ-10",
    "description": "Комплект контроля температуры и влажности",
    "location": "Склад №1",
    "timezone": "Europe/Moscow"
  },
  
  "sensors": [
    {
      "id": 1,
      "enabled": true,
      "poll_port_id": "default",
      "local_number": 1,
      "display_number": "COM8-001",
      "name": "ХРАН. № 1",
      "description": "Хранилище 1, зона А",
      "modbus_slave_id": 16,
      "modbus_addr_temp": 1,
      "modbus_addr_hum": 2,
      "temp_limits": {
        "min": -40.0,
        "max": 85.0,
        "warning_delta": 3.0,
        "alarm_delta": 5.0
      },
      "hum_limits": {
        "min": 0.0,
        "max": 100.0,
        "warning_delta": 5.0,
        "alarm_delta": 10.0
      },
      "guarded": true,
      "notifications": {
        "email_on_warning": true,
        "email_on_alarm": true,
        "telegram_on_alarm": true
      },
      "created_at": "2026-01-01T00:00:00"
    },
    {
      "id": 3,
      "enabled": true,
      "poll_port_id": "pp_udp_1",
      "local_number": 1,
      "display_number": "UDP-192.168.66.100-001",
      "name": "ХРАН. № 3",
      "description": "Хранилище 3, зона B",
      "modbus_slave_id": 16,
      "modbus_addr_temp": 5,
      "modbus_addr_hum": 6,
      "temp_limits": {
        "min": 2.0,
        "max": 8.0,
        "warning_delta": 1.0,
        "alarm_delta": 2.0
      },
      "hum_limits": {
        "min": 30.0,
        "max": 70.0,
        "warning_delta": 5.0,
        "alarm_delta": 10.0
      },
      "guarded": true,
      "notifications": {
        "email_on_warning": false,
        "email_on_alarm": true,
        "telegram_on_alarm": true
      },
      "created_at": "2026-01-05T10:00:00"
    }
  ],
  
  "next_sensor_id": 4
}
```

## 9.2 Версионность конфигурации

### 9.2.1 Схема версионирования

- **config_version** - версия текущей конфигурации (изменяется при каждом изменении)
- **config_schema_version** - версия схемы/формата конфигурации (изменяется при изменении структуры)

### 9.2.2 Автоматическое резервное копирование

При каждом изменении конфигурации создаётся резервная копия:

```
data/config/
├── system_config.json              # Текущая конфигурация
├── backups/
│   ├── system_config_1.0.1.json    # Резервная копия версии 1.0.1
│   ├── system_config_1.0.2.json    # Резервная копия версии 1.0.2
│   └── system_config_1.0.3.json    # Резервная копия версии 1.0.3
```

### 9.2.3 Настройки резервного копирования

```json
{
  "backup": {
    "enabled": true,
    "max_backups": 50,
    "backup_on_change": true,
    "backup_path": "./data/config/backups/"
  }
}
```

## 9.3 API управления конфигурацией

| Метод | Endpoint | Описание |
|-------|----------|----------|
| GET | /api/config | Получить текущую конфигурацию |
| GET | /api/config/version | Получить версию конфигурации |
| GET | /api/config/history | История изменений |
| POST | /api/config | Сохранить конфигурацию |
| GET | /api/config/backup/{version} | Получить резервную копию |
| POST | /api/config/restore/{version} | Восстановить из резервной копии |
| POST | /api/config/export | Экспорт конфигурации |
| POST | /api/config/import | Импорт конфигурации |
| GET | /api/config/bundle/summary | Сводка по текущему переносимому набору конфигурации |
| GET | /api/config/bundle/export | Скачать полный конфигурационный ZIP-архив |
| POST | /api/config/bundle/import | Восстановить конфигурацию из полного ZIP-архива |

### 9.3.1 Полный конфигурационный архив для завода

Система ДОЛЖНА предоставлять страницу `/settings/config-transfer`, через которую администратор может выгрузить единый ZIP-архив всех настраиваемых данных и загрузить такой архив обратно. Назначение архива — передать текущую конфигурацию на завод/в сервис для анализа того, какие датчики, линии опроса, мнемосхемы, планы и параметры были настроены на объекте, а также восстановить этот набор на другой установке.

Состав архива:

```
manifest.json
config/*.json
assets/floorplans/*.{png,jpg,jpeg,gif,bmp,webp,svg}
diagnostics/current.json
diagnostics/availability_daily.json
diagnostics/modbus_log.json
diagnostics/events.json
diagnostics/opcua_status.json
```

- `config/*.json` включает все верхнеуровневые JSON-файлы из `data/config/`: датчики и системные настройки (`system_config.json`), линии опроса (`poller_config.json`), OPC UA (`opcua_config.json`), архивирование, уведомления, оформление, layout, планы (`floorplan_config.json`), дерево мнемосхемы (`mnemo_tree.json`) и прочие конфиги, если они добавлены в систему.
- `assets/floorplans/` включает загруженные картинки/SVG планов помещений из `visualizer/static/floorplans/`.
- `diagnostics/` включает снимки текущей работы для анализа на заводе: последние текущие данные, суточную доступность, журнал Modbus-обменов, журнал событий и статус OPC UA. Эти файлы экспортируются только для диагностики и при импорте НЕ восстанавливаются.
- `manifest.json` содержит формат архива, версию формата, дату создания, список файлов и область восстановления.

При импорте система ДОЛЖНА:

- проверить `manifest.json`, версию формата и структуру путей внутри ZIP;
- запретить path traversal, неизвестные top-level директории и неподдерживаемые расширения картинок;
- проверить наличие обязательных конфигов: `system_config.json`, `poller_config.json`, `opcua_config.json`, `archive_config.json`, `notifications.json`, `theme_config.json`, `layout.json`, `floorplan_config.json`, `mnemo_tree.json`;
- перед записью создать резервный ZIP текущего состояния в `data/config/import_backups/`;
- восстановить JSON-конфиги и картинки планов, после чего запросить перечитывание конфигурации Poller через API;
- вернуть оператору список импортированных файлов, путь к резервной копии и предупреждения, если живой Poller недоступен.

## 9.4 API управления датчиками

| Метод | Endpoint | Описание |
|-------|----------|----------|
| GET | /api/sensors | Список всех датчиков |
| GET | /api/sensors/{id} | Получить датчик по ID |
| POST | /api/sensors | Добавить датчик(и) |
| PUT | /api/sensors/{id} | Обновить датчик |
| DELETE | /api/sensors/{id} | Удалить датчик |
| POST | /api/sensors/batch | Добавить несколько датчиков |
| DELETE | /api/sensors/batch | Удалить несколько датчиков |

### 9.4.1 Добавление одного датчика

```http
POST /api/sensors
Content-Type: application/json

{
  "name": "ХРАН. № 4",
  "description": "Новое хранилище",
  "poll_port_id": "pp_udp_1",
  "local_number": 2,
  "modbus_slave_id": 16,
  "modbus_addr_temp": 7,
  "modbus_addr_hum": 8,
  "temp_limits": {"min": -10.0, "max": 40.0, "warning_delta": 2.0, "alarm_delta": 5.0},
  "hum_limits": {"min": 20.0, "max": 80.0, "warning_delta": 5.0, "alarm_delta": 10.0},
  "guarded": true
}
```

### 9.4.2 Добавление нескольких датчиков (batch)

```http
POST /api/sensors/batch
Content-Type: application/json

{
  "sensors": [
    {
      "name": "ХРАН. № 5",
      "poll_port_id": "default",
      "local_number": 5,
      "modbus_slave_id": 16,
      "modbus_addr_temp": 9,
      "modbus_addr_hum": 10,
      "temp_limits": {"min": -10.0, "max": 40.0},
      "hum_limits": {"min": 20.0, "max": 80.0}
    },
    {
      "name": "ХРАН. № 6",
      "poll_port_id": "default",
      "local_number": 6,
      "modbus_slave_id": 16,
      "modbus_addr_temp": 11,
      "modbus_addr_hum": 12,
      "temp_limits": {"min": -10.0, "max": 40.0},
      "hum_limits": {"min": 20.0, "max": 80.0}
    }
  ],
  "change_description": "Добавлены датчики для зоны C"
}
```

### 9.4.3 Удаление нескольких датчиков

```http
DELETE /api/sensors/batch
Content-Type: application/json

{
  "sensor_ids": [5, 6, 7],
  "change_description": "Удалены датчики зоны C"
}
```

## 9.5 Валидация конфигурации

При изменении конфигурации выполняется валидация:

| Проверка | Описание |
|----------|----------|
| Уникальность ID | ID датчика должен быть уникальным |
| Уникальность адресов | Адреса Modbus не должны пересекаться внутри одной линии опроса; одинаковые адреса на разных `poll_port_id` допустимы |
| Привязка к линии | `poll_port_id` должен ссылаться на существующую включённую или отключённую линию опроса |
| Номер внутри линии | `local_number` должен быть уникальным в пределах одного `poll_port_id` |
| Границы значений | min < max для всех лимитов |
| Обязательные поля | name, modbus_slave_id, адреса |
| Допустимые значения | slave_id: 1-247, адреса: 0-65535 |

## 9.6 Миграция конфигурации

При изменении схемы конфигурации (config_schema_version) система автоматически мигрирует старые конфигурации:

```python
# Пример миграции с версии 0.9 на 1.0
def migrate_0_9_to_1_0(old_config):
    new_config = old_config.copy()
    new_config["config_schema_version"] = "1.0"
    
    # Добавляем новые обязательные поля
    for sensor in new_config["sensors"]:
        if "warning_delta" not in sensor["temp_limits"]:
            sensor["temp_limits"]["warning_delta"] = 3.0
        if "warning_delta" not in sensor["hum_limits"]:
            sensor["hum_limits"]["warning_delta"] = 5.0
    
    return new_config
```
