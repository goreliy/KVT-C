# Ревью кода KVT-C

Дата актуализации: 2026-07-03

## Краткое состояние

Проект уже собран вокруг правильной эксплуатационной оси: `run_kvt.py` остаётся единой точкой запуска, `visualizer` отвечает за UI/API, `poller` владеет Modbus-опросом, `archiver` ведёт архив/журнал, `opcua_server` публикует текущие значения во внешние SCADA/АСУ ТП, а `mqtt_bridge` добавлен как отдельный двунаправленный MQTT-сервис.

Большая часть ранних замечаний уже закрыта: общий atomic JSON writer внедрён, runtime JSON читается tolerant-слоем, Flask secret вынесен из кода, MockServer пишет stdout/stderr, `modbus_log.json` пишется компактно и throttled, poller config валидируется, scan не подменяет общий Modbus-клиент во время активного polling, runtime/cache/secret артефакты вынесены в `.gitignore`.

Главное направление дальнейшей стабилизации теперь не “добавить ещё один транспорт”, а сделать уже добавленные интеграции предсказуемыми в промышленной эксплуатации: честная документация возможностей, строгая входная валидация, очистка старых временных файлов, диагностика transport-ошибок и понятные degraded-состояния.

## Актуальные риски и рекомендации

| Приоритет | Область | Проблема | Риск | Рекомендация | Проверка |
|---|---|---|---|---|---|
| P0 | OPC UA Historical Access | В конфиге и UI есть настройки Historical Access, но runtime `HistoryRead` в сервере не реализован. | Интегратор может рассчитывать на чтение архива через OPC UA, хотя сервер отдаёт только текущие значения. | Пока HA не реализован, описывать его как заготовку. Для реализации добавить historized nodes/HistoryRead поверх `ArchiveService` и покрыть клиентским тестом. | OPC UA client должен читать историю `KVT.Sensors.<id>.Temperature/Humidity` за диапазон времени или README/UI должны явно говорить “не реализовано”. |
| P0 | API конфигов | Часть endpoints всё ещё принимает крупные JSON-объекты и сохраняет их почти целиком, особенно системные/сетевые настройки. | Неверные типы, лишние поля или конфликтующие значения могут попасть на диск и сломать запуск после reload. | Довести все настройки до patch-модели известных полей с coercion/validation, как уже сделано для poller, OPC UA и MQTT. | Невалидные host/port/period/limit возвращают 400 и не меняют файл. |
| P1 | Runtime temp cleanup | В `data/` остаются старые атомарные `.tmp` файлы (`.current.json.*.tmp`, `.modbus_log.json.*.tmp`, `.archive.json.*.tmp`). | Диск засоряется, диагностика путается, fallback-логика может использовать слишком старый временный снимок. | На старте сервисов удалять stale `.tmp` старше безопасного TTL или ограничить `_latest_sensor_snapshots()` возрастом кандидатов. | После рестарта старые `.tmp` не копятся; current fallback не берёт снимки старше заданного окна. |
| P1 | MQTT inbound | MQTT v1 сохраняет входящие payload в `mqtt_inbound.json`, но не смешивает их с `current.json`. | Ожидающий “MQTT как источник данных” пользователь может не увидеть входящие значения на мнемосхеме. | Если MQTT должен быть полноценным источником датчиков, добавить явный merge-layer с приоритетом, stale-age, source label и конфликтами с poller. | Входящий `kvt-c/inbound/sensors/<id>` либо явно отображается как external source, либо UI честно показывает, что это только входящий буфер. |
| P1 | UDP diagnostics | Боевой сетевой путь остаётся RTU-over-UDP; диагностика должна различать timeout, CRC, exception response и transport error. | “Нет данных” выглядит одинаково для разных причин, сложнее искать неисправность на объекте. | В status/log сохранять `error_type`, endpoint, tx/rx hex, response time и transport context для каждого сбоя. Modbus TCP не считать целевым сетевым транспортом. | Недоступный UDP endpoint, CRC error и Modbus exception видны как разные причины. |
| P1 | Launcher diagnostics | `run_kvt.py` запускает сервисы, но ещё не проверяет занятые порты до старта и не показывает tail stderr при failed start. | На Windows легко оставить старый процесс на порту и получить “запустилось”, хотя отвечает другой процесс. | Добавить preflight port check, tail stderr/stdout в status/failed start и аккуратную обработку stale PID. | При занятом порту launcher пишет PID/порт/последние строки ошибки. |
| P2 | Archive/API объём данных | Archive/events endpoints фильтруют крупные JSON в памяти и могут отдавать большие ответы. | При росте архива UI начнёт тормозить, а запросы станут дорогими. | Ввести limit/period defaults, постраничную отдачу и отдельные summary endpoints. | Большой архив не блокирует UI; запросы имеют явные лимиты. |
| P2 | Frontend polling | Часть периодических refresh-запросов может накладываться при медленном ответе. | UI создаёт лишнюю нагрузку и показывает гоняющиеся статусы. | Добавить in-flight guard/abort controller и stale-age отображение там, где polling частый. | При искусственно медленном API новый refresh не стартует поверх старого. |

## MQTT v1: что добавлено и где границы

Добавлен `mqtt_bridge` на `paho-mqtt==2.1.0`:

- настройки: `data/config/mqtt_config.json`, `/settings/mqtt`, `/api/mqtt/config`;
- runtime status: `data/mqtt_status.json`, `/api/mqtt/status`;
- входящий буфер: `data/mqtt_inbound.json`, `/api/mqtt/inbound`;
- launcher: `python run_kvt.py start --service mqtt`, общий `--service all`;
- topics при `base_topic=kvt-c`: `kvt-c/status`, `kvt-c/current`, `kvt-c/sensors/<sensor_id>`, `kvt-c/inbound/sensors/<sensor_id>`, `kvt-c/commands/republish`, `kvt-c/commands/ping`;
- пароль broker хранится отдельно в `data/config/mqtt_password.key` или `KVT_MQTT_PASSWORD`, не возвращается API и не попадает в config bundle.

Ограничение v1 осознанное: входящие MQTT-сообщения не перезаписывают poller state. Это безопасно для промышленной системы, потому что внешний broker пока не становится неявным источником истины для мнемосхемы, архиватора и журналов учёта.

## Закрытые пункты из прошлого ревью

- Hardcoded Flask secret заменён на `KVT_SECRET_KEY` или generated secret file.
- Общие atomic JSON helpers и tolerant runtime reads внедрены.
- `modbus_log.json` переведён на compact atomic write с throttling.
- Poller scan больше не подменяет общий `_modbus` во время активного polling.
- Poller config validation вынесена в отдельный слой.
- MockServer stdout/stderr больше не теряются.
- Mojibake/кодировки исправлены, добавлены `.editorconfig` и `.gitattributes`.
- Runtime/cache/secret артефакты добавлены в `.gitignore`.
- OPC UA read-only сервер, UI настроек, status API и launcher wiring добавлены.
- MQTT bridge, UI настроек, status/inbound API, launcher wiring и config bundle поддержка добавлены.

## Практические smoke-проверки

```bat
python run_kvt.py start
python run_kvt.py status
python run_kvt.py stop
```

```text
GET http://127.0.0.1:5000/api/current
GET http://127.0.0.1:5000/api/poller/status
GET http://127.0.0.1:5000/api/opcua/status
GET http://127.0.0.1:5000/api/mqtt/status
GET http://127.0.0.1:5000/api/mqtt/inbound
GET http://127.0.0.1:5001/api/poller/status
POST http://127.0.0.1:5001/api/poller/start
POST http://127.0.0.1:5001/api/poller/stop
```

Для MQTT дополнительно проверить с broker:

```text
subscribe: kvt-c/#
publish:   kvt-c/commands/republish {}
publish:   kvt-c/inbound/sensors/1 {"temperature":{"value":22.1,"status":"normal"},"humidity":{"value":45.2,"status":"normal"}}
```

## Рекомендуемый порядок следующих работ

1. Закрыть несоответствие OPC UA HA: либо реализовать HistoryRead, либо держать это как явно неготовую функцию в UI/README.
2. Добавить cleanup stale `.tmp` runtime-файлов и возрастной фильтр fallback-снимков.
3. Довести system/network APIs до строгой patch-validation модели.
4. Усилить UDP diagnostics и launcher diagnostics.
5. Решить продуктово, должен ли MQTT inbound становиться полноценным источником данных для UI/архива.
6. После роста архива добавить pagination/limits для тяжёлых endpoints.
