# Ревью кода KVT-C

Дата ревью: 2026-06-03

## Краткое состояние проекта

Проект уже имеет рабочую структуру: `visualizer` отвечает за Flask UI/API, `poller` вынесен в отдельный сервис опроса, `shared/config_manager.py` хранит общий слой конфигураций, `MocTestServer` используется как стенд, а `run_kvt.py` остается правильной единой точкой запуска. Это хорошая база для Windows-эксплуатации: запуск, PID-файлы и логи собраны в одном месте, а транспортная логика не размазана по UI.

Основные риски сейчас связаны не с отсутствием новых функций, а с надежностью runtime-состояния: JSON пишется разными способами, часть данных poller меняется из нескольких потоков, конфиги принимаются через API почти целиком, а диагностика отдельных ошибок недостаточно подробная. Для системы мониторинга это критично: пользователь может видеть "нет данных", хотя реальная причина - гонка, битый JSON, timeout, CRC или сбой запуска вспомогательного процесса.

Отдельно стоит зафиксировать: сетевой путь для устройства должен быть UDP. Modbus TCP не должен появляться как целевой транспорт для сети. Если в стенде сейчас есть TCP-эмуляция, ее нельзя считать проверкой боевого сетевого сценария.

## Критичные риски и рекомендации

| Приоритет | Область | Проблема | Риск | Рекомендация | Проверка |
|---|---|---|---|---|---|
| P0 | `PollerService` | `_modbus`, `_config`, очереди логов и статусы читаются/меняются из thread/API/scan/apply_config без единой модели владения. | При `apply_config` или `scan_devices` во время цикла опроса можно закрыть клиент во время запроса, потерять лог, получить неконсистентный `current.json` или ложный `state=error`. | Сделать один poller-thread владельцем Modbus-клиента; команды config/reload/scan передавать через очередь или выполнять под явной паузой poll loop. Не подменять общий `self._modbus` внутри `scan_devices`. | Во время активного polling многократно вызвать config apply и scan; не должно быть traceback, битого JSON и смешанных TX/RX записей. |
| P0 | JSON/data | `shared/config_manager.save_json` пишет напрямую, `poller` пишет атомарно только часть файлов, mock generators тоже пишут напрямую. | При падении процесса или параллельном чтении можно получить частичный JSON; похожие BOM/JSONDecodeError уже видны в runtime-логах. | Ввести общий helper `atomic_save_json()` и использовать его для config/runtime/mock JSON. Для config сохранять backup до замены, для runtime - безопасную замену через temp file и `os.replace`. | Искусственно прерванная запись не портит основной файл; чтение возвращает старую валидную версию или понятную ошибку. |
| P1 | UDP transport | В коде есть serial и UDP, но диагностика UDP пока недостаточно точная: `connect()` только создает socket, а endpoint фактически проверяется на первом обмене. | UI может писать "Cannot reach UDP endpoint" до реальной проверки или не различать timeout, CRC, exception response и transport error. | Для сети считать целевым только RTU-over-UDP: сохранять RTU frame + CRC, улучшить классификацию ошибок и статусы по UDP-обмену. Modbus TCP не использовать как сетевой путь. | UDP-обмен показывает transport, endpoint, tx/rx hex, response_time_ms и отдельный error_type. |
| P1 | Конфиги API | `/api/config`, `/api/network/config`, `/api/poller/config` принимают большие куски JSON без строгой схемы. | Можно сохранить неверные типы, диапазоны, лишние поля или конфликтующие настройки, после чего сервис сломается при reload/start. | API должен принимать patch известных полей, приводить типы, проверять диапазоны и сохранять только валидный config. | Невалидный `poll_period_ms`, `udp_port`, `timeout_ms`, sensor limits возвращают 400 и не меняют файл. |

## Узкие места и деградации под нагрузкой

1. **Перезапись `modbus_log.json` на каждую запись обмена.** Сейчас `_log()` вызывает `_write_log_file()` для TX/RX, а `_log_exchange()` тоже пишет файл. Один датчик обычно дает чтение значений и статусов, то есть несколько TX/RX/exchange записей. При 20-50 датчиках и коротком `poll_period_ms` это превращается в постоянную перезапись большого JSON. Рекомендация: писать log snapshot не на каждую запись, а батчем в конце poll cycle или по debounce-интервалу, например не чаще 1 раза в секунду.

2. **Опрос датчиков полностью последовательный.** Для каждого датчика идут чтение values, чтение statuses и retries. При timeout 500 мс, `retry_count=3` и нескольких недоступных датчиках один цикл может растянуться на секунды или десятки секунд. В это время `current.json` обновляется реже, UI показывает устаревшие данные, а новый цикл фактически не стартует по расписанию. Рекомендация: считать и показывать фактическую длительность цикла, ограничивать retry budget на цикл, отдельно помечать "slow/offline" датчики.

3. **`scan_devices()` конкурирует с обычным polling.** Scan потенциально обходит диапазон slave id и для каждого делает Modbus-запрос. Если он запущен параллельно с poll loop, это не только нагрузка, но и конфликт владения transport-клиентом. Рекомендация: scan должен быть отдельным режимом: pause polling -> scan своим локальным клиентом -> restore polling, либо scan запрещен при активном polling.

4. **`status()` пересчитывает агрегаты по истории логов на каждый запрос.** При небольшом `log_max_entries` это терпимо, но UI может опрашивать status часто, а лимит логов легко увеличить. Рекомендация: хранить rolling counters и rolling response time stats при добавлении логов, а `status()` отдавать готовый snapshot.

5. **Чтение JSON с диска на каждый UI/API запрос.** `current_payload()` и visualizer API читают `current.json`, `archive.json`, `events.json` напрямую. При частом frontend polling это создает лишнюю нагрузку на диск и может поймать момент незавершенной записи. Рекомендация: короткий cache по mtime для read-only endpoints и единый tolerant JSON loader.

6. **Большие JSON-ответы без пагинации/ограничения по времени.** Archive/events endpoints фильтруют данные в памяти и могут вернуть крупный массив. На маленьком архиве нормально, но со временем UI начнет тормозить. Рекомендация: ограничивать period/limit, возвращать summary отдельно от detailed data, для больших архивов перейти на постраничную отдачу.

7. **Frontend polling может накладываться сам на себя.** В poller UI есть периодический refresh; если предыдущий запрос завис или стал медленным, следующий может стартовать поверх него. Рекомендация: добавить флаг in-flight на стороне JS, отмену/пропуск следующего refresh и отображение stale-age.

8. **Рост `data/current.json` и `modbus_log.json` влияет на старт и UI.** Чем больше sensors/log entries, тем дороже сериализация, чтение и pretty JSON. Рекомендация: хранить текущие данные компактно, pretty print оставить только для config или debug режима, а operational logs писать в bounded format.

9. **Mock generators тоже пишут файлы напрямую.** При демонстрациях они могут создавать нагрузку и конкурировать с visualizer чтением. Рекомендация: использовать тот же atomic writer и отдельный update interval/backpressure для mock current/archive.

Порядок работ по деградации: сначала убрать гонки и прямые записи JSON, затем добавить измерение длительности poll cycle, потом throttling логов, и только после этого оптимизировать UI/API чтение.

## Безопасность и эксплуатация

`run_kvt.py` нужно сохранить как основной launcher. Он уже задает рабочую директорию, PID-файлы, stdout/stderr логи и понятные команды `start`, `stop`, `restart`, `status`. Улучшать стоит саму диагностику launcher: проверка занятых портов до старта, вывод последних строк stderr при failed start, отдельная команда или подсказка путей к логам, аккуратная обработка stale PID.

Пункт про `0.0.0.0` не включаю как рекомендацию к исправлению: это может быть осознанной настройкой для доступа по локальной сети. Важно не менять это автоматически в ревью. Если сервис выставляется в сеть, тогда отдельно решается вопрос аутентификации и прав доступа к settings/API.

Flask secret остается реальной рекомендацией к исправлению, потому что он не зависит от сетевой топологии и не должен быть захардкожен.

## Надежность файлов, JSON и логов

`shared/config_manager.py` уже читает и пишет `utf-8-sig`, что полезно для Windows и файлов с BOM. Но запись напрямую в целевой файл лучше заменить на общий атомарный writer. Для runtime JSON достаточно `utf-8`; для config JSON можно оставить `utf-8-sig`, если это удобно для ручного открытия в Windows-редакторах.

Рекомендуемая политика:

- config JSON: `utf-8-sig`, validation перед записью, backup перед заменой, атомарная запись;
- current/log/events/archive JSON: `utf-8`, атомарная запись, tolerant read при missing/corrupt file;
- export CSV: `utf-8-sig`, потому что это удобно для Excel;
- process logs: append-файлы с ограничением размера или простой ротацией.

`data/modbus_log.json.tmp` показывает, что временные файлы уже появляются. Это нормально для атомарной записи, но при старте стоит уметь отличать старый `.tmp` от активной записи и не считать его рабочими данными.

## Modbus, UDP и Serial transport

Боевой сетевой путь должен быть RTU-over-UDP: RTU frame с CRC передается через UDP socket. Modbus TCP как сетевой транспорт здесь не нужен и не должен предлагаться как улучшение. Serial RTU остается отдельным локальным транспортом для COM-порта.

Что улучшить именно для текущей модели:

- UDP `connect()` не должен создавать иллюзию успешной связи с устройством: socket создан, но endpoint проверяется только при запросе;
- ошибки нужно классифицировать: `udp_timeout`, `serial_open_failed`, `crc_error`, `unexpected_slave`, `unexpected_function`, `modbus_exception`, `short_response`;
- в status/log полезно отдавать endpoint и transport context: `transport`, `udp_host`, `udp_port`, `timeout_ms`, `tx_hex`, `rx_hex`, `response_time_ms`;
- retry нужно делать осознанно: timeout и CRC не одно и то же, а после нескольких timeout подряд датчик можно временно помечать offline без полного набора запросов;
- если `MocTestServer` остается, он должен эмулировать UDP RTU frame path для сетевого режима. TCP-эмуляцию лучше удалить из рекомендаций по целевому transport или явно пометить как старый/небоевой стенд, который не проверяет UDP.

## Flask API и UI

Visualizer API удобен как прокси к poller, но часть endpoints принимает raw JSON и сразу пишет файл. Главная рекомендация: отделить входную модель API от полного config-файла на диске.

Рекомендуемый слой:

1. `load_*_config()` возвращает нормализованный config с defaults.
2. API принимает patch только с известными полями.
3. Validator приводит типы и проверяет диапазоны.
4. Save выполняется атомарно.
5. Poller reload/apply получает уже нормализованный config.

Для UI важно показывать не только `state=error`, но и причину: UDP timeout, CRC, device exception, JSON/config error, no sensors, stale data. Это уменьшит ситуации, когда все выглядит как "нет данных", хотя причина понятна на transport уровне.

## Наблюдаемость и smoke-проверки

Отдельный пункт "надо добавить pytest" убран из списка обязательных рекомендаций, как requested. Вместо этого стоит оставить практические smoke-проверки для ручной эксплуатации после исправлений:

```bat
python run_kvt.py start
python run_kvt.py status
python run_kvt.py stop
```

```text
GET http://127.0.0.1:5000/api/current
GET http://127.0.0.1:5000/api/poller/status
GET http://127.0.0.1:5001/api/poller/status
POST http://127.0.0.1:5001/api/poller/start
POST http://127.0.0.1:5001/api/poller/stop
GET http://127.0.0.1:5001/api/poller/scan?start_id=1&end_id=32&timeout_ms=500
```

Что стоит проверять руками:

- UDP transport как основной сетевой режим;
- serial transport отдельно, если используется COM-порт;
- BOM-tainted config regression;
- concurrent config update while poller is running;
- запуск на Windows из свежего venv;
- поведение UI при недоступном UDP endpoint и при CRC/timeout ошибках.

## Рекомендуемый порядок исправлений

1. Вынести atomic JSON read/write в общий helper и перевести config/runtime/mock записи на него.
2. Переработать thread-safety в `PollerService`: не подменять общий `_modbus` из scan, синхронизировать apply/reload/status/log snapshots.
3. Добавить измерение длительности poll cycle, counters по типам ошибок и stale-age для текущих данных.
4. Уменьшить деградацию под нагрузкой: throttling записи `modbus_log.json`, отказ от второй группы чтения после явного timeout, rolling stats вместо пересчета history.
5. Ввести validation layer для poller/system/network/sensor configs.
6. Исправить Flask secret.
7. Добавить логи MockServer вместо `DEVNULL`, PID/status и вывод причины failed start.
8. Нормализовать кодировки Python-файлов и убрать mojibake.
9. Уточнить стенд: сетевой transport только UDP RTU frame; TCP-эмуляцию не использовать как проверку целевого режима.
10. После стабилизации решить вопрос с runtime-файлами в дереве: `.pyc`, logs, `data/*.json`, backups.

## Итог

Проект рабочий и уже имеет правильные опорные части: единый launcher, разделение visualizer/poller и RTU frame path. Главное направление улучшений: надежность JSON, потокобезопасный poller, корректный UDP transport, понятная диагностика и снижение деградации при росте числа датчиков. Пункты про обязательное добавление pytest и изменение `0.0.0.0` убраны из ревью; Modbus TCP не рассматривается как целевой сетевой путь.

## Комментарии: уже сделано

Обновлено 2026-06-03:

Закрытые пункты ревью:

- `P0 | Безопасность`: hardcoded Flask secret убран из `visualizer/app.py`.
- `P1 | MockServer diagnostics`: запуск MockServer больше не теряет stdout/stderr.
- `P1 | Кодировки`: найденный mojibake исправлен, добавлены правила UTF-8.
- `P1 | Runtime logs`: `modbus_log.json` переведен на throttled compact atomic-запись.
- `P2 | Репозиторий/runtime`: добавлен и расширен `.gitignore` для runtime/cache/secret артефактов.
- `Узкие места, пункт 3`: при падении чтения `values` для датчика `statuses` явно пропускаются, в `exchange_queue` пишется `status="skipped"` с причиной `values read failed`, а в stats добавлен `skipped_status_reads`.

- Исправлена mojibake-кодировка в `Общее ТЗ на систему КВТ С.md`: документ снова отображается в GitHub нормальной кириллицей.
- Исправлены русские строки в `visualizer/routes/api.py`: сообщения API для Poller/UDP снова читаемые.
- Исправлены русские строки и комментарии в `shared/config_manager.py`.
- После исправления выполнена проверка: поиск Unicode replacement character по текстовым файлам ничего не нашел, повторный скан на обратимо чинимый mojibake не выявил новых кандидатов.
- `python -m py_compile visualizer\routes\api.py shared\config_manager.py` проходит.
- Внедрен общий `atomic_save_json()` и tolerant `load_runtime_json()` в `shared/config_manager.py`; config JSON, `current.json`, `modbus_log.json`, journal/events write path и visualizer runtime reads переведены на общий безопасный слой.
- `PollerService.apply_config()` больше не закрывает Modbus-клиент прямо во время активного запроса: конфиг сохраняется и применяется между циклами с переподключением.
- `scan_devices()` больше не подменяет общий `_modbus`; scan запрещен во время активного polling и использует отдельный клиент в остановленном режиме.
- Запись `modbus_log.json` throttled/debounced и принудительно сбрасывается в конце poll cycle; в status/current добавлена `last_cycle_duration_ms`.
- Flask secret вынесен из hardcoded строки: используется `KVT_SECRET_KEY` или локальный generated secret в `data/config/flask_secret.key`.
- MockServer больше не запускается с `DEVNULL`: stdout/stderr пишутся в `logs/mockserver.out.log` и `logs/mockserver.err.log`, status API показывает return code и хвост stderr.
- Poller config validation вынесена в `poller/config.py` и используется Visualizer API и Poller API.
- Добавлен `.gitignore` для `__pycache__`, `*.pyc`, `.run/`, `logs/`, временных файлов и `data/config/flask_secret.key`.
- Проверка после прохода: `python -m py_compile shared\config_manager.py poller\config.py poller\poller_service.py poller\app.py visualizer\app.py visualizer\routes\api.py visualizer\routes\journal.py` проходит.
- Закреплены правила кодировки: добавлены `.editorconfig` и `.gitattributes` для UTF-8 текстовых файлов.
- `modbus_log.json` теперь пишется компактным JSON (`indent=None`) через общий atomic writer, что уменьшает размер файла и нагрузку записи.
- `.gitignore` расширен для runtime JSON (`data/current.json`, `data/modbus_log.json`, `data/events.json`, `data/archive.json`) и `data/config/backups/`; текущие рабочие данные не удалялись.
