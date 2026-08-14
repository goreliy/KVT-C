# 6. Подсистема 4: OPC UA Server

## 6.1 Назначение

OPC UA Server предназначен для read-only передачи текущих значений датчиков С2000-ВТ / С2000-ВТИ во внешние SCADA/АСУ ТП клиенты. Сервер не опрашивает оборудование сам: источником текущих данных является тот же нормализованный срез, который отдаёт Web Visualizer через `/api/current`. Дополнительно сервер предоставляет интерфейс OPC UA Historical Access (HA) для чтения архивных значений температуры и влажности из хранилища Archive Manager (см. 6.4).

Технологическая база:

- сервис: `opcua_server`;
- запуск: `python -m opcua_server.app`;
- библиотека: `asyncua==2.0.1` (opcua-asyncio 2.x);
- минимальная версия Python для сервиса: 3.10;
- endpoint по умолчанию: `opc.tcp://0.0.0.0:4840/kvt/` (привязка ко всем интерфейсам);
- **объявляемый (advertised) endpoint:** self-адреса `0.0.0.0`/`localhost`/пустой host автоматически заменяются на актуальный IP машины — OPC UA клиенты после discovery подключаются по адресу ИЗ ответа сервера, и объявленный `0.0.0.0` снаружи недостижим. При смене IP машины сервер автоматически перепубликует endpoint;
- namespace URI по умолчанию: `urn:kvt:c:monitoring`;
- статус сервиса: `data/opcua_status.json`; свежесть статуса (`stale`, `age_seconds`) вычисляется Web Visualizer на сервере по mtime файла — сравнение времени в браузере недопустимо из-за возможного расхождения часов контроллера и клиента.

## 6.2 Конфигурация OPC UA (opcua_config.json)

`data/config/opcua_config.json` является единственным источником настроек OPC UA. Конфиг входит в полный ZIP-архив переноса настроек.

```json
{
  "enabled": false,
  "server": {
    "host": "0.0.0.0",
    "port": 4840,
    "endpoint_path": "/kvt/",
    "server_name": "KVT-C OPC UA Server",
    "namespace_uri": "urn:kvt:c:monitoring",
    "namespace_name": "KVT-C"
  },
  "publishing": {
    "update_interval_ms": 1000,
    "stale_after_ms": 30000,
    "publish_only_enabled_sensors": true
  },
  "selection": {
    "sensor_ids": []
  },
  "fields": {
    "temperature": true,
    "humidity": true,
    "combined_status": true,
    "timestamp": true,
    "poll_port_metadata": true,
    "limits": true
  },
  "security": {
    "mode": "anonymous_readonly",
    "security_policies": ["None"],
    "certificate_path": "",
    "private_key_path": "",
    "users": []
  },
  "historical_access": {
    "enabled": false,
    "source": "archive_manager",
    "max_values_per_read": 5000
  }
}
```

Правила:

- `enabled=false` означает, что процесс может быть запущен, но endpoint не публикуется;
- пустой `selection.sensor_ids` означает «публиковать все включённые датчики»;
- непустой `selection.sensor_ids` ограничивает экспорт указанными датчиками;
- `publish_only_enabled_sensors=true` исключает отключённые датчики;
- смена `host`, `port`, `endpoint_path` или security-политики может требовать перезапуска `python run_kvt.py restart --service opcua`;
- поля `certificate_path`, `private_key_path`, `users` сохраняются и валидируются для будущего ужесточения, но текущий runtime поддерживает только `anonymous_readonly`.

## 6.3 Адресное пространство OPC UA

Корневая структура:

| Узел | Назначение |
|------|------------|
| `KVT/System` | Метаданные сервера, namespace, время последней публикации, число экспортируемых датчиков |
| `KVT/PollPorts/<poll_port_id>` | Состояние линии опроса: имя, транспорт, state, ping/error |
| `KVT/Sensors/Sensor_<id>` | Read-only переменные конкретного датчика |

Стабильные NodeId датчиков используют ID датчика, а не имя:

| NodeId | Тип | Значение |
|--------|-----|----------|
| `KVT.Sensors.<id>.Temperature` | Double | Температура |
| `KVT.Sensors.<id>.Humidity` | Double | Влажность |
| `KVT.Sensors.<id>.CombinedStatus` | String | Сводный статус |
| `KVT.Sensors.<id>.Timestamp` | String | Время последнего значения |
| `KVT.Sensors.<id>.PollPortId` | String | Линия опроса |

Если значения нет или оно устарело, числовой узел публикуется с BadNoData quality; browse label и служебные поля датчика остаются доступны, чтобы OPC UA клиент видел настроенный состав системы.

## 6.4 Historical Access (HA)

Помимо текущих значений, OPC UA Server предоставляет интерфейс OPC UA Historical Access (HistoryRead) для чтения архивных данных температуры и влажности по конкретному датчику и диапазону времени. Источник истории — хранилище Archive Manager (см. раздел 4); сам OPC UA архив не ведёт.

Настройки HA задаются в `opcua_config.json` (блок `historical_access`):

- `enabled` — включение HistoryRead (по умолчанию `false`, пока не запущена подсистема Archive Manager);
- `source` — источник истории (`archive_manager`);
- `max_values_per_read` — максимум точек, возвращаемых за один запрос HistoryRead.

Клиент вызывает HistoryRead по узлам `KVT.Sensors.<id>.Temperature` и `KVT.Sensors.<id>.Humidity`; сервер возвращает пары «значение + метка времени» из архива за указанный диапазон, ограничивая объём выдачи `max_values_per_read`. Работа HA зависит от доступности Archive Manager: при `enabled=false` или недоступном источнике HistoryRead возвращает Bad_HistoryOperationUnsupported / Bad_NoData.

## 6.5 Настройки и API

Страница `/settings/opcua` должна позволять оператору:

- включить/выключить публикацию;
- настроить host, port, endpoint path, имя сервера и namespace;
- задать интервал публикации и порог устаревания;
- выбрать все включённые датчики или явный список датчиков;
- включить/выключить группы полей: температура, влажность, статус, timestamp, порт опроса, границы;
- видеть текущий endpoint, статус сервиса и число опубликованных датчиков;
- сохранить security-заготовки без обещания защищённого режима, пока runtime поддерживает только anonymous read-only;
- включить Historical Access и задать источник истории и лимит точек за один HistoryRead.

API Web Visualizer:

| Метод | Endpoint | Описание |
|-------|----------|----------|
| GET | `/api/opcua/config` | Получить `opcua_config.json` |
| POST | `/api/opcua/config` | Провалидировать и сохранить настройки OPC UA |
| GET | `/api/opcua/status` | Получить `data/opcua_status.json` |
| POST | `/api/opcua/reload` | Сообщить оператору, что сервис перечитает конфиг или потребует restart |

## 6.6 Запуск

`run_kvt.py` является единой точкой запуска:

```bash
python run_kvt.py start --service opcua
python run_kvt.py status
python run_kvt.py restart --service opcua
python run_kvt.py stop --service opcua
```

При `--service all` сервис `opcua` запускается вместе с `poller`, `archiver` и `visualizer`. Логи: `logs/opcua.out.log`, `logs/opcua.err.log`; PID-файл: `.run/opcua.pid`.

## 6.7 Критерии приёмки

- `python run_kvt.py --help` содержит `opcua` в списке сервисов.
- `/settings/opcua` сохраняет все настройки без потери ввода во время ручного редактирования.
- `/api/opcua/config` и `/api/opcua/status` возвращают JSON.
- При `enabled=true` внешний OPC UA клиент подключается к `opc.tcp://<IP-сервера>:4840/kvt/` (актуальный IP машины, объявляемый сервером), видит `KVT/Sensors` и читает `KVT.Sensors.<id>.Temperature`.
- Выбор датчиков работает: пустой список экспортирует все включённые датчики, явный список — только указанные.
- OPC UA публикует тот же состав датчиков и те же fallback-значения, что `/api/current`.
- При `historical_access.enabled=true` и запущенном Archive Manager клиент выполняет HistoryRead по `KVT.Sensors.<id>.Temperature`/`.Humidity` и получает архивные значения за диапазон.
