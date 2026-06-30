# KVT-C

Система мониторинга температуры/влажности с веб-интерфейсом и отдельным Modbus poller.

## Что есть в проекте
- `visualizer` (Flask, порт `5000`) — веб UI, настройки, API.
- `poller` (Flask, порт `5001`) — опрос Modbus, лог обменов, текущее состояние.
- `archiver` (Flask, порт `5002`) — Archive Manager: архивирование `current.json`, `archive.json`, `archive_daily.json`, SQLite-зеркало и REST API.
- `opcua` (asyncua, порт `4840`) — read-only OPC UA сервер для передачи текущих данных датчиков внешним SCADA/АСУ ТП клиентам.
- `MocTestServer` — тестовый сервер/генератор данных (опционально).
- `data/config/*.json` — рабочие конфиги системы.

## Единая точка запуска
Запуск/остановка/статус из одного места:

```bash
python run_kvt.py start
python run_kvt.py status
python run_kvt.py stop
```

Дополнительно:

```bash
python run_kvt.py restart
python run_kvt.py start --service poller
python run_kvt.py start --service archiver
python run_kvt.py start --service opcua
python run_kvt.py stop --service visualizer
```

Логи:
- `logs/poller.out.log`, `logs/poller.err.log`
- `logs/archiver.out.log`, `logs/archiver.err.log`
- `logs/opcua.out.log`, `logs/opcua.err.log`
- `logs/visualizer.out.log`, `logs/visualizer.err.log`

PID-файлы:
- `.run/poller.pid`
- `.run/archiver.pid`
- `.run/opcua.pid`
- `.run/visualizer.pid`

## Установка
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Адреса сервисов
- Visualizer UI: `http://127.0.0.1:5000/`
- Poller API/UI: `http://127.0.0.1:5001/`
- Archive Manager API/UI: `http://127.0.0.1:5002/`
- OPC UA endpoint: `opc.tcp://127.0.0.1:4840/kvt/`
- Poller status API: `http://127.0.0.1:5001/api/poller/status`
- Archive status API: `http://127.0.0.1:5002/api/archive/status`

При запуске через `python run_kvt.py start` сервисы слушают адреса из `data/config/system_config.json`.
По умолчанию `web_host` и `poller_host` равны `0.0.0.0`, поэтому интерфейс доступен и по IP машины:
- Visualizer UI: `http://<IP-компьютера>:5000/`
- Poller API/UI: `http://<IP-компьютера>:5001/`
- Archive Manager API/UI: `http://<IP-компьютера>:5002/`
- OPC UA endpoint: `opc.tcp://<IP-компьютера>:4840/kvt/`

## Archive Manager и журнал учёта
- Archive Manager читает `data/current.json`, пишет сжатые измерения в `data/archive.json`, поддерживает SQLite-файл `data/archive.db` при включённом хранилище и формирует файловую суточную вьюху `data/archive_daily.json`.
- Основные API доступны и через visualizer (`/api/archive/status`, `/api/archive/query`, `/api/archive/temperature-log`, `/api/archive/violations`, `/api/archive/export`) и через отдельный сервис archiver на порту `5002`.
- Настройки архива доступны на `/settings/archive`; там же можно вручную снять текущий срез и запустить очистку по retention.
- Складская отчётность доступна на `/logbook`: суточные min/max/avg температуры и влажности, число превышений, ежедневная подпись оператора, пакетная подпись выходных/праздников и печатный лист `/logbook/<report_id>/print`.
- Конфиги журналов, операторов и календаря: `data/config/reports_config.json`, `data/config/operators.json`, `data/config/holidays.json`; подписи со снимками значений хранятся в `data/logbook_signoffs.json`.
- Админка журналов находится на `/settings/reports`.

## OPC UA сервер
- Конфиг сервера хранится в `data/config/opcua_config.json`; по умолчанию сервис отключён (`enabled: false`) и включается на `/settings/opcua`.
- Endpoint по умолчанию: `opc.tcp://0.0.0.0:4840/kvt/`; namespace URI: `urn:kvt:c:monitoring`.
- Сервер запускается отдельным процессом через `python run_kvt.py start --service opcua`; при `--service all` он запускается вместе с poller/archiver/visualizer.
- OPC UA публикует тот же нормализованный срез датчиков, что и `/api/current`: значения из `current.json`, fallback на последние временные снимки и архивные значения, плюс привязка к `poll_port_id`.
- Адресное пространство: `KVT/System`, `KVT/PollPorts/<poll_port_id>`, `KVT/Sensors/Sensor_<id>`. NodeId датчиков стабильные: `KVT.Sensors.<id>.Temperature`, `Humidity`, `CombinedStatus`, `Timestamp`, `PollPortId`.
- На `/settings/opcua` настраиваются host/port/path, namespace, интервал публикации, список датчиков, экспортируемые поля и заготовки security. Runtime сейчас поддерживает read-only anonymous mode; certificate/user-password сохраняются в конфиге для следующего ужесточения.
- Статус пишется в `data/opcua_status.json` и доступен через Visualizer API `/api/opcua/status`.

## Импорт/экспорт конфигурации
- Страница переноса настроек: `http://127.0.0.1:5000/settings/config-transfer`.
- ZIP-архив содержит `manifest.json`, все верхнеуровневые JSON-конфиги из `data/config/` (включая `opcua_config.json`), изображения планов из `visualizer/static/floorplans/` и диагностические снимки `current.json`, `availability_daily.json`, `modbus_log.json`, `events.json`, `opcua_status.json`.
- При импорте восстанавливаются конфиги и изображения планов; диагностические файлы остаются только для анализа и обратно не накатываются.
- Перед импортом текущая конфигурация автоматически сохраняется в `data/config/import_backups/`.

## Визуализация актуальности
- На каждой плашке датчика на главном экране отображается строка `Последние данные: ...`.
- Время берется из `sensor.temperature.timestamp` (fallback: общий `current.timestamp`).
- Метка нужна для быстрого контроля свежести данных по каждому датчику.

## Мнемосхема (главный экран)
- **Ping в карточке датчика** — строка `Ping линии` показывается ТОЛЬКО для датчиков, привязанных к Ethernet-линии (UDP/C2000-ПП), и содержит последний ping линии опроса (`<мс> мс` / `нет связи` / `—`). У датчиков на COM-линии строка скрыта. Отдельной плашки «Доступность Ethernet-линий» на мнемосхеме больше нет.
- **Дерево датчиков** — настраиваемая группировка датчиков по веткам (с подветками). В корне каждой ветки выводятся название, число датчиков и текущий диапазон min-max температуры и влажности. Конфигурация хранится в `data/config/mnemo_tree.json`.
- **Режим редактирования мнемосхемы** — кнопка «Режим редактирования» в шапке открывает встроенный редактор дерева (добавить ветку/подветку, переименовать, удалить, выбрать датчики чекбоксами, сохранить/отменить).
- Суточные счётчики доступности (включая `Получено данных сегодня` и ping линий) ведутся в `data/availability_daily.json`.

> Примечание по эксплуатации: visualizer запускается без debug, поэтому шаблоны кэшируются. Включён `TEMPLATES_AUTO_RELOAD`, но процесс нужно один раз перезапустить (`python run_kvt.py restart --service visualizer`). Если правки не видны — проверьте, нет ли «забытого» процесса на порту 5000 (`Get-NetTCPConnection -LocalPort 5000`), который перехватывает запросы.

## Poller: Modbus transport
Текущая реализация поддерживает два режима:
- `serial` — через COM-порт (RTU)
- `udp` — нативный RTU-over-UDP (RTU кадр + CRC через UDP сокет)

Спецификация следующего этапа описана в `Общее ТЗ на систему КВТ С.md`: несколько именованных линий опроса `poll_ports[]`, независимый worker на каждый COM/UDP-порт под управлением `PollPortManager`, датчики с привязкой к конкретной линии, транспорт `udp_c2000pp` с 5-байтовой UDP-обёрткой `10 LEN SEQ 10` на передачу и приём, тот же менеджер портов в `/settings/poller`, группировка плашек мнемосхемы и SVG-планы.

Ключи в `data/config/poller_config.json`:

```json
{
  "transport": "serial",
  "com_port": "COM8",
  "baudrate": 9600,
  "bytesize": 8,
  "parity": "N",
  "stopbits": 1,
  "udp_host": "127.0.0.1",
  "udp_port": 502,
  "timeout_ms": 500
}
```

Переключение режима также доступно в UI:
`Settings -> Poller`.

## Быстрый smoke-test
1. `python run_kvt.py start`
2. Открыть `http://127.0.0.1:5000/`
3. Проверить `http://127.0.0.1:5001/api/poller/status`
4. Проверить `http://127.0.0.1:5002/api/archive/status`
5. Открыть `/settings/opcua`, включить OPC UA при необходимости и выполнить `python run_kvt.py restart --service opcua`
6. Проверить `http://127.0.0.1:5000/api/opcua/status` и подключение к `opc.tcp://127.0.0.1:4840/kvt/`
7. `python run_kvt.py stop`

## Замечания по проекту (актуальные)
- Docker/compose артефактов в текущем дереве нет.
- Основной поддерживаемый сценарий запуска: `run_kvt.py`.

## Журнал документации
- 2026-06-30: добавлен отдельный OPC UA сервер на `asyncua` (`opcua_server`, `data/config/opcua_config.json`, `/settings/opcua`, `/api/opcua/*`, запуск через `run_kvt.py --service opcua`) для read-only передачи текущих данных датчиков внешним клиентам.
- 2026-06-24: реализован `archiver` как Archive Manager (`archive.json`, `archive_daily.json`, SQLite-зеркало, REST API, запуск через `run_kvt.py`) и складской журнал учёта (`/settings/reports`, `/logbook`, подписи со снимками значений, пакетная подпись нерабочих дней, печатный лист).
- 2026-06-19: добавлен полный импорт/экспорт конфигурационного ZIP-архива (`/settings/config-transfer`, `/api/config/bundle/*`) для передачи настроек, планов, мнемосхемы и диагностических снимков на завод и восстановления на другой установке.
- 2026-06-17: с мнемосхемы убрана плашка «Доступность Ethernet-линий»; ping показывается строкой в карточке каждого Ethernet-датчика (у COM-датчиков скрыт); добавлены дерево датчиков (`mnemo_tree.json`, API `/api/mnemo/tree`) с min-max в корне ветки и встроенный «Режим редактирования». В `visualizer/app.py` включён авто-перечитыватель шаблонов/статики. Обновлены `README.md`, `Общее ТЗ на систему КВТ С.md` (раздел 5) и `.kiro` specs.
- 2026-06-03: исправлена кодировка русских строк в `Общее ТЗ на систему КВТ С.md`, `visualizer/routes/api.py` и `shared/config_manager.py`; общий ТЗ снова корректно отображается в GitHub.
- 2026-06-03: файл ревью `CODE_REVIEW_RECOMMENDATIONS.md` обновлен: пункт про кодировки отмечен как выполненный, а оставшиеся рекомендации сохранены как открытые.
- 2026-06-03: выполнен первый проход по `CODE_REVIEW_RECOMMENDATIONS.md`: добавлены atomic JSON helpers, tolerant runtime JSON reads, отложенное применение poller config, запрет scan во время polling, throttling записи `modbus_log.json`, generated Flask secret, MockServer stdout/stderr logs, валидация poller config и `.gitignore` для runtime/cache/secret артефактов.
- 2026-06-03: дополнительно закрыты пункты ревью `P1 Кодировки`, `P1 Runtime logs`, `P2 Репозиторий/runtime`: добавлены `.editorconfig`/`.gitattributes`, `modbus_log.json` переведен на компактную atomic-запись, `.gitignore` расширен на runtime JSON и `data/config/backups/`.
- 2026-06-03: закрыт пункт ревью про умножение timeout на группы регистров: при ошибке чтения `values` poller явно пропускает `statuses`, пишет `status="skipped"` в exchange log и считает `skipped_status_reads`.
