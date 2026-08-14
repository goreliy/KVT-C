# KVT-C

Система мониторинга температуры и влажности для оборудования **Болид**: веб-интерфейс, архивирование, складской журнал учёта, OPC UA и MQTT для внешних SCADA/АСУ ТП. Данные собираются с адресных датчиков **С2000-ВТ** и читаются из преобразователя протокола **С2000-ПП** по Modbus RTU.

## О системе и оборудовании

Цепочка сбора данных построена на приборах ИСО «Орион» (Болид) и настраивается фирменной утилитой **UProg**:

1. **Датчики — [С2000-ВТ](https://bolid.ru/production/s2000-vt.html)** — адресные измерители температуры и влажности. Подключаются к **ДПЛС** (двухпроводной линии связи).
2. **Контроллер ДПЛС — [С2000-КДЛ](https://bolid.ru/production/s2000-kdl.html)** (или **[С2000-КДЛ-Modbus](https://bolid.ru/production/s2_kdl_modbus.html)**) — опрашивает адресные С2000-ВТ на ДПЛС. Адреса датчиков и параметры линии задаются в UProg.
3. **Преобразователь протокола — [С2000-ПП](https://bolid.ru/production/s2000-pp.html)** — собирает данные по внутреннему протоколу Болид («Орион», RS-485) и отдаёт их наружу как **ведомый Modbus RTU**. Таблица зон/регистров Modbus настраивается в UProg.
4. **Система КВТ (это ПО)** — модуль `poller` циклически **запрашивает С2000-ПП по Modbus RTU**. Подключение к серверу — одним из двух способов:
   - через **[USB-RS485](https://bolid.ru/production/usb-rs485.html)** — как локальный **COM-порт** (транспорт `serial`);
   - через **[С2000-Ethernet](https://bolid.ru/production/s2000-ethernet.html)** — Modbus RTU «поверх сети» по UDP/TCP (транспорт `udp` / `udp_c2000pp`), когда С2000-ПП подключён Modbus-портом к С2000-Ethernet.

Полученные данные `poller` публикует остальным подсистемам: веб-визуализация, архив/журнал учёта, OPC UA и MQTT bridge.

```mermaid
flowchart LR
    subgraph field["Полевой уровень — ДПЛС (2 провода)"]
        vt1["С2000-ВТ №1<br/>температура + влажность"]
        vt2["С2000-ВТ №2"]
        vtn["С2000-ВТ … №N"]
    end

    kdl["С2000-КДЛ<br/>(или С2000-КДЛ-Modbus)<br/>контроллер ДПЛС"]
    pp["С2000-ПП<br/>преобразователь протокола<br/>Орион (Болид) → Modbus RTU"]

    vt1 -->|ДПЛС| kdl
    vt2 -->|ДПЛС| kdl
    vtn -->|ДПЛС| kdl
    kdl -->|"RS-485, протокол Орион (Болид)"| pp

    subgraph pclink["Подключение к серверу КВТ"]
        usb["USB-RS485<br/>→ COM-порт"]
        eth["С2000-Ethernet<br/>Modbus RTU поверх UDP/TCP"]
    end

    pp -->|"Modbus RTU (RS-485)"| usb
    pp -->|"Modbus RTU (RS-485)"| eth

    subgraph kvt["Система КВТ (это ПО)"]
        poller["poller :5001<br/>Modbus RTU-запросы к С2000-ПП"]
        viz["visualizer :5000<br/>веб-интерфейс"]
        arch["archiver :5002<br/>архив + журнал учёта"]
        opc["opcua :4840<br/>OPC UA для SCADA"]
        mqtt["mqtt<br/>MQTT bridge"]
    end

    usb -->|"serial (COM)"| poller
    eth -->|"udp / udp_c2000pp"| poller
    poller --> viz
    poller --> arch
    poller --> opc
    poller --> mqtt
```

> Настройка приборов Болид (адресация С2000-ВТ на ДПЛС, параметры С2000-КДЛ, таблица Modbus-регистров С2000-ПП) выполняется утилитой **UProg**. Сама система КВТ прибором не управляет — она только читает готовые значения из С2000-ПП по Modbus RTU. Mock-сервер из `MocTestServer` эмулирует ответы С2000-ПП для отладки без реального железа.

## Документация

Вся документация — в каталоге **[docs/](docs/)**, начните с [индекса `docs/README.md`](docs/README.md)
(там же — сводная таблица состояния реализации подсистем).

| Раздел | Что внутри |
|---|---|
| [docs/01-requirements](docs/01-requirements/) | Требования (EARS) — что система должна делать |
| [docs/02-architecture](docs/02-architecture/) | Архитектура: подсистемы, модули, модели данных |
| [docs/03-specification](docs/03-specification/) | Детальная спецификация по подсистемам (§1–§15) |
| [docs/04-protocols](docs/04-protocols/) | Протоколы обмена с оборудованием (С2000-Ethernet) |
| [docs/05-tasks](docs/05-tasks/) | План реализации со статусами |
| [docs/06-development](docs/06-development/) | Разработчику: продукт, стек, структура кода |

## Что есть в проекте
- `visualizer` (Flask, порт `5000`) — веб UI, настройки, API, журналы, журнал учёта.
- `poller` (Flask, порт `5001`) — опрос С2000-ПП по Modbus RTU, лог обменов, текущее состояние.
- `archiver` (Flask, порт `5002`) — Archive Manager: архивирование `current.json`, `archive.json`, `archive_daily.json`, SQLite-зеркало и REST API.
- `opcua` (asyncua, порт `4840`) — read-only OPC UA сервер для передачи текущих данных датчиков внешним SCADA/АСУ ТП клиентам.
- `mqtt` (paho-mqtt) — двунаправленный MQTT bridge: публикация текущих данных и приём входящих MQTT-сообщений.
- `MocTestServer` — тестовый сервер/генератор данных, эмулирует С2000-ПП (опционально).
- `data/config/*.json` — рабочие конфиги системы.

Не реализовано (описано в спецификации как планируемое): Telegram-бот, email-уведомления,
генератор отчётов по расписанию, Docker-контейнеризация, PostgreSQL-хранилище архива,
OPC UA Historical Access.

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
python run_kvt.py start --service mqtt
python run_kvt.py stop --service visualizer
```

`--service all` (по умолчанию) поднимает `poller`, `archiver`, `visualizer` и только те интеграционные сервисы `opcua`/`mqtt`, у которых включён `autostart` в настройках.

Логи:
- `logs/poller.out.log`, `logs/poller.err.log`
- `logs/archiver.out.log`, `logs/archiver.err.log`
- `logs/opcua.out.log`, `logs/opcua.err.log`
- `logs/mqtt.out.log`, `logs/mqtt.err.log`
- `logs/visualizer.out.log`, `logs/visualizer.err.log`

PID-файлы:
- `.run/poller.pid`
- `.run/archiver.pid`
- `.run/opcua.pid`
- `.run/mqtt.pid`
- `.run/visualizer.pid`


## Win64 onefile EXE
Готовый переносимый файл лежит в `win64/KVT-C.exe`. Его можно скопировать на Windows x64 ПК и запускать без установленного Python и без `pip install`. При запуске двойным кликом exe сам поднимает сервисы, ждёт Visualizer и открывает браузер со страницей визуализации.

Команды те же, что у `run_kvt.py`:

```powershell
.\KVT-C.exe start
.\KVT-C.exe status
.\KVT-C.exe restart
.\KVT-C.exe stop
```

При первом запуске рядом с exe автоматически создаются рабочие каталоги `data/`, `logs/` и `.run/`; стартовые JSON-конфиги зашиты внутрь exe и копируются только если файлов ещё нет. Если браузер не открылся автоматически, откройте вручную: `http://<IP-сервера>:5000/` или `http://127.0.0.1:5000/` на этой же машине. Консольное окно после двойного клика можно закрыть — сервисы продолжают работать в фоне; остановка: `KVT-C.exe stop`.

Для пересборки из исходников:

```powershell
python -m PyInstaller --noconfirm --clean --distpath win64 --workpath build\win64 kvt_c_win64.spec
```
## Установка
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Адреса сервисов
Сервисы слушают адреса из `data/config/system_config.json` (по умолчанию `0.0.0.0` — все интерфейсы). Жёсткий `127.0.0.1` в системе не используется: свой IP машина определяет автоматически (`shared/net.py`), актуальное значение показывает `python run_kvt.py status` и `GET /api/network/local-ip`. Подставьте IP машины, на которой установлена система (`<IP-сервера>`):
- Visualizer UI: `http://<IP-сервера>:5000/`
- Poller API/UI: `http://<IP-сервера>:5001/`
- Archive Manager API/UI: `http://<IP-сервера>:5002/`
- OPC UA endpoint: `opc.tcp://<IP-сервера>:4840/kvt/`
- MQTT broker: адрес брокера задаётся в `data/config/mqtt_config.json` и на `/settings/mqtt` (self-адреса разрешаются в IP машины)
- Poller status API: `http://<IP-сервера>:5001/api/poller/status`
- Archive status API: `http://<IP-сервера>:5002/api/archive/status`

## Poller: подключение к С2000-ПП (Modbus transport)
Модуль `poller` — Modbus RTU **мастер**, С2000-ПП — ведомый. Поддерживаются режимы транспорта:
- `serial` — через **COM-порт** (RTU). Используется с преобразователем **USB-RS485**.
- `udp` — нативный RTU-over-UDP (RTU-кадр + CRC через UDP-сокет).
- `udp_c2000pp` — RTU в 5-байтовой UDP-обёртке `10 LEN SEQ 10` на передачу и приём — путь через **С2000-Ethernet**.

Несколько именованных линий опроса задаются в `poll_ports[]` (независимый worker на каждый COM/UDP-порт под управлением `PollPortManager`), датчики привязываются к конкретной линии. Полная спецификация — [docs/03-specification/03-poller.md](docs/03-specification/03-poller.md); байтовый разбор обмена через С2000-Ethernet — [docs/04-protocols/c2000-ethernet-modbus-over-udp.md](docs/04-protocols/c2000-ethernet-modbus-over-udp.md).

**Пер-линейные параметры опроса и «медленный цикл».** У каждой линии свои `poll_period_ms` (0 = общий), `timeout_ms`, `retry_count` (-1 = общий) и `slow_poll_period_ms` (по умолчанию 30000). Датчик, не ответивший `retry_count` повторов, переводится в «медленный цикл» (одна попытка раз в `slow_poll_period_ms`), при этом остаётся в `current.json` (признак `slow_poll: true`) и автоматически возвращается в обычный опрос при первом ответе — из опроса датчик не выпадает никогда. Настраивается в редакторе линии на `/settings/poller`.

**Опрос никогда не останавливается сам.** Цикл каждой линии защищён от любых ошибок (таймауты, сбои порта, ошибки журнала), watchdog каждые 5 секунд пересоздаёт упавший worker, а журнал Modbus ограничен `log_max_entries` целиком (записи + TX/RX/обмены, перезатирается по кольцу) — рост `modbus_log.json` не может остановить опрос. Остановка — только явной командой оператора.

**Свой IP вместо 127.0.0.1.** Все внутренние обращения и объявляемые адреса строятся через `shared/net.py`: self-маркеры (`0.0.0.0`/`localhost`/пусто) заменяются на актуальный IP машины, смена IP подхватывается на лету; жёсткого `127.0.0.1` в коде и конфигах нет. Endpoint `GET /api/network/local-ip` отдаёт текущий IP; в ethernet-линии поле «Локальный IP» заполняется автоматически (кнопка «Определить»).

**COM-порт на Linux.** Поле «COM-порт» в редакторе линии имеет галочку «Выбрать из доступных» — список портов системы из `GET /api/poller/ports` (`COMx` на Windows, `/dev/tty*` на Linux). tty-имена, введённые без пути, автоматически дополняются `/dev/` (`ttyUSB0` → `/dev/ttyUSB0`).

Базовые ключи в `data/config/poller_config.json`:

```json
{
  "transport": "serial",
  "com_port": "COM8",
  "baudrate": 9600,
  "bytesize": 8,
  "parity": "N",
  "stopbits": 1,
  "udp_host": "",
  "udp_port": 502,
  "timeout_ms": 500
}
```

Переключение режима и настройка линий — в UI: `Settings → Poller`.

## Archive Manager и журнал учёта
- Archive Manager читает `data/current.json`, пишет сжатые измерения в `data/archive.json`, поддерживает SQLite-файл `data/archive.db` при включённом хранилище и формирует файловую суточную вьюху `data/archive_daily.json`.
- Основные API доступны и через visualizer (`/api/archive/status`, `/api/archive/query`, `/api/archive/temperature-log`, `/api/archive/violations`, `/api/archive/export`), и через отдельный сервис archiver на порту `5002`.
- Настройки архива — на `/settings/archive`; там же можно вручную снять текущий срез и запустить очистку по retention.
- Складская отчётность — на `/logbook`: суточные min/max/avg температуры и влажности, число превышений, ежедневная подпись оператора, пакетная подпись выходных/праздников и печатный лист `/logbook/<report_id>/print`.
- Конфиги журналов, операторов и календаря: `data/config/reports_config.json`, `data/config/operators.json`, `data/config/holidays.json`; подписи со снимками значений хранятся в `data/logbook_signoffs.json`.
- Админка журналов — на `/settings/reports`.

## OPC UA сервер
- Конфиг сервера хранится в `data/config/opcua_config.json`; публикация включается/выключается кнопками «Запустить/Остановить» на `/settings/opcua` (флаг `enabled`), а отдельная галочка `autostart` задаёт запуск процесса при `python run_kvt.py start` / `--service all`.
- Endpoint по умолчанию: `opc.tcp://0.0.0.0:4840/kvt/` (привязка ко всем интерфейсам); **клиентам объявляется endpoint с реальным IP машины** — `0.0.0.0` снаружи недостижим, поэтому в discovery/endpoint URL self-адреса заменяются на актуальный IP (при смене IP сервер перепубликует endpoint автоматически). Namespace URI: `urn:kvt:c:monitoring`.
- Сервер запускается отдельным процессом через `python run_kvt.py start --service opcua`; при `--service all` — только если включён `autostart`. Флаг `enabled` процесс применяет вживую (~2 секунды), без перезапуска.
- OPC UA публикует тот же нормализованный срез датчиков, что и `/api/current`: значения из `current.json`, fallback на последние временные снимки и архивные значения, плюс привязка к `poll_port_id`.
- Адресное пространство: `KVT/System`, `KVT/PollPorts/<poll_port_id>`, `KVT/Sensors/Sensor_<id>`. NodeId датчиков стабильные: `KVT.Sensors.<id>.Temperature`, `Humidity`, `CombinedStatus`, `Timestamp`, `PollPortId`.
- На `/settings/opcua` настраиваются host/port/path, namespace, интервал публикации, список датчиков, экспортируемые поля и заготовки security. Runtime сейчас поддерживает read-only anonymous mode; certificate/user-password сохраняются в конфиге для следующего ужесточения.
- Статус пишется в `data/opcua_status.json` и доступен через Visualizer API `/api/opcua/status`.
- Historical Access (HA): поля настройки уже есть в `opcua_config.json` и на `/settings/opcua`, но runtime HistoryRead ещё не реализован; пока сервер публикует только текущие значения.

## MQTT bridge
- Конфиг bridge хранится в `data/config/mqtt_config.json`; пароль broker хранится отдельно в `data/config/mqtt_password.key` или берётся из `KVT_MQTT_PASSWORD` и не попадает в Git/config bundle.
- Сервис запускается отдельным процессом через `python run_kvt.py start --service mqtt`; при `--service all` — только если включён `autostart`. Флаг `enabled` в конфиге включает/выключает соединение с broker.
- Публикуется тот же нормализованный срез датчиков, что и `/api/current`: общий retained snapshot и отдельные retained payload по каждому датчику.
- Topics при `base_topic="kvt-c"`: `kvt-c/status`, `kvt-c/current`, `kvt-c/sensors/<sensor_id>`, `kvt-c/inbound/sensors/<sensor_id>`, `kvt-c/commands/republish`, `kvt-c/commands/ping`.
- Входящие payload датчиков сохраняются в `data/mqtt_inbound.json` и доступны через `/api/mqtt/inbound`; они не перезаписывают `data/current.json` в текущей версии.
- Настройки автозапуска, broker, username/password, TLS-пути, QoS, retain, interval и base topic доступны на `/settings/mqtt`; статус — через `/api/mqtt/status`.

## Импорт/экспорт конфигурации
- Страница переноса настроек: `http://<IP-сервера>:5000/settings/config-transfer`.
- ZIP-архив содержит `manifest.json`, все верхнеуровневые JSON-конфиги из `data/config/` (включая `opcua_config.json` и `mqtt_config.json`), изображения планов из `visualizer/static/floorplans/` и диагностические снимки `current.json`, `availability_daily.json`, `modbus_log.json`, `events.json`, `opcua_status.json`, `mqtt_status.json`, `mqtt_inbound.json`.
- При импорте восстанавливаются конфиги и изображения планов; диагностические файлы остаются только для анализа и обратно не накатываются.
- Перед импортом текущая конфигурация автоматически сохраняется в `data/config/import_backups/`.

## Визуализация актуальности
- На каждой плашке датчика на главном экране отображается строка `Последние данные: ...`.
- Время берётся из `sensor.temperature.timestamp` (fallback: общий `current.timestamp`).
- Метка нужна для быстрого контроля свежести данных по каждому датчику.

## Мнемосхема (главный экран)
- **Ping в карточке датчика** — строка `Ping линии` показывается ТОЛЬКО для датчиков, привязанных к Ethernet-линии (UDP/С2000-ПП через С2000-Ethernet), и содержит последний ping линии опроса (`<мс> мс` / `нет связи` / `—`). У датчиков на COM-линии строка скрыта.
- **Дерево датчиков** — настраиваемая группировка датчиков по веткам (с подветками). В корне каждой ветки выводятся название, число датчиков и текущий диапазон min-max температуры и влажности. Конфигурация хранится в `data/config/mnemo_tree.json`.
- **Режим редактирования мнемосхемы** — кнопка «Режим редактирования» в шапке открывает встроенный редактор дерева (добавить ветку/подветку, переименовать, удалить, выбрать датчики чекбоксами, сохранить/отменить).
- Суточные счётчики доступности (включая `Получено данных сегодня` и ping линий) ведутся в `data/availability_daily.json`.

> Примечание по эксплуатации: visualizer запускается без debug, поэтому шаблоны кэшируются. Включён `TEMPLATES_AUTO_RELOAD`, но процесс нужно один раз перезапустить (`python run_kvt.py restart --service visualizer`). Если правки не видны — проверьте, нет ли «забытого» процесса на порту 5000 (`Get-NetTCPConnection -LocalPort 5000`), который перехватывает запросы.

## Быстрый smoke-test
`<IP-сервера>` — IP машины с системой (показывает `python run_kvt.py status`).
1. `python run_kvt.py start`
2. Открыть `http://<IP-сервера>:5000/`
3. Проверить `http://<IP-сервера>:5001/api/poller/status`
4. Проверить `http://<IP-сервера>:5002/api/archive/status`
5. Открыть `/settings/opcua`, при необходимости нажать «Запустить»
6. Проверить `http://<IP-сервера>:5000/api/opcua/status` и подключение к `opc.tcp://<IP-сервера>:4840/kvt/`
7. Открыть `/settings/mqtt`, при необходимости задать broker и проверить `http://<IP-сервера>:5000/api/mqtt/status`
8. `python run_kvt.py stop`

## Замечания по проекту (актуальные)
- Docker/compose артефактов в текущем дереве нет.
- Основной поддерживаемый сценарий запуска: `run_kvt.py`.

## Журнал документации
- 2026-08-14: улучшен UX onefile exe: запуск `KVT-C.exe` без аргументов теперь стартует сервисы, ждёт Visualizer, открывает браузер на веб-интерфейсе и оставляет понятное сообщение в консоли.
- 2026-08-14: добавлена win64 onefile-сборка `win64/KVT-C.exe`: один исполняемый файл запускает все сервисы через внутренний режим `--internal-service`, сам разворачивает стартовые конфиги рядом с exe; документация дополнена командами запуска и пересборки.
- 2026-08-14: fixed Modbus polling per sensor: sensor `modbus_slave_id` is now used directly for TX requests and offline snapshots; port `device_slave_id` no longer overrides sensor address; added regression test `tests/test_poller_slave_id.py`.
- 2026-07-15: пер-линейные параметры опроса (`poll_period_ms`/`retry_count`/`slow_poll_period_ms` у каждой линии) и «медленный цикл» — датчик без ответа опрашивается реже, но никогда не выпадает из опроса; опрос защищён от любых ошибок + watchdog (никогда не останавливается сам); журнал Modbus ограничен `log_max_entries` целиком; убран жёсткий `127.0.0.1` (динамическое определение своего IP, `shared/net.py`); OPC UA объявляет клиентам endpoint с реальным IP (исправлено «не подключается сторонняя система» при host 0.0.0.0), свежесть статуса считается на сервере по mtime (исправлен ложный «процесс не запущен» при расхождении часов); выбор COM-порта из доступных (`/dev/tty*` на Linux) с автодописыванием `/dev/`. Обновлены `Общее ТЗ` (3.2.2.2–3.2.2.3, 3.9, 6.1), `.kiro` requirements (2.16–2.19, 15.7), design, tasks.
- 2026-07-03: добавлен двунаправленный MQTT bridge (`mqtt_bridge`, `data/config/mqtt_config.json`, `/settings/mqtt`, `/api/mqtt/*`, запуск через `run_kvt.py --service mqtt`) для публикации текущих данных и приёма входящих MQTT-сообщений.
- 2026-07-03: для OPC UA и MQTT добавлена отдельная галочка `autostart`: `run_kvt.py start --service all` поднимает эти процессы только при включённом автозапуске, ручной `--service opcua|mqtt` остаётся доступен.
- 2026-07-01: README переписан — добавлены описание оборудования Болид (С2000-ВТ → ДПЛС/С2000-КДЛ → С2000-ПП → Modbus RTU через USB-RS485 или С2000-Ethernet, настройка UProg) и диаграмма пути данных; на `/settings/opcua` добавлены кнопки «Запустить/Остановить».
- 2026-06-30: добавлен отдельный OPC UA сервер на `asyncua` (`opcua_server`, `data/config/opcua_config.json`, `/settings/opcua`, `/api/opcua/*`, запуск через `run_kvt.py --service opcua`) для read-only передачи текущих данных датчиков внешним клиентам.
- 2026-06-24: реализован `archiver` как Archive Manager (`archive.json`, `archive_daily.json`, SQLite-зеркало, REST API, запуск через `run_kvt.py`) и складской журнал учёта (`/settings/reports`, `/logbook`, подписи со снимками значений, пакетная подпись нерабочих дней, печатный лист).
- 2026-06-19: добавлен полный импорт/экспорт конфигурационного ZIP-архива (`/settings/config-transfer`, `/api/config/bundle/*`) для передачи настроек, планов, мнемосхемы и диагностических снимков на завод и восстановления на другой установке.
- 2026-06-17: с мнемосхемы убрана плашка «Доступность Ethernet-линий»; ping показывается строкой в карточке каждого Ethernet-датчика (у COM-датчиков скрыт); добавлены дерево датчиков (`mnemo_tree.json`, API `/api/mnemo/tree`) с min-max в корне ветки и встроенный «Режим редактирования». В `visualizer/app.py` включён авто-перечитыватель шаблонов/статики. Обновлены `README.md`, `Общее ТЗ на систему КВТ С.md` (раздел 5) и `.kiro` specs.
- 2026-06-03: исправлена кодировка русских строк в `Общее ТЗ на систему КВТ С.md`, `visualizer/routes/api.py` и `shared/config_manager.py`; общий ТЗ снова корректно отображается в GitHub.
- 2026-06-03: выполнен первый проход по ревью: добавлены atomic JSON helpers, tolerant runtime JSON reads, отложенное применение poller config, запрет scan во время polling, throttling записи `modbus_log.json`, generated Flask secret, MockServer stdout/stderr logs, валидация poller config и `.gitignore` для runtime/cache/secret артефактов.
- 2026-06-03: дополнительно закрыты пункты ревью `P1 Кодировки`, `P1 Runtime logs`, `P2 Репозиторий/runtime`: добавлены `.editorconfig`/`.gitattributes`, `modbus_log.json` переведён на компактную atomic-запись, `.gitignore` расширен на runtime JSON и `data/config/backups/`.
- 2026-06-03: закрыт пункт ревью про умножение timeout на группы регистров: при ошибке чтения `values` poller явно пропускает `statuses`, пишет `status="skipped"` в exchange log и считает `skipped_status_reads`.


