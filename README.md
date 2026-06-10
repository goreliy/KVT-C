# KVT-C

Система мониторинга температуры/влажности с веб-интерфейсом и отдельным Modbus poller.

## Что есть в проекте
- `visualizer` (Flask, порт `5000`) — веб UI, настройки, API.
- `poller` (Flask, порт `5001`) — опрос Modbus, лог обменов, текущее состояние.
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
python run_kvt.py stop --service visualizer
```

Логи:
- `logs/poller.out.log`, `logs/poller.err.log`
- `logs/visualizer.out.log`, `logs/visualizer.err.log`

PID-файлы:
- `.run/poller.pid`
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
- Poller status API: `http://127.0.0.1:5001/api/poller/status`

## Визуализация актуальности
- На каждой плашке датчика на главном экране отображается строка `Последние данные: ...`.
- Время берется из `sensor.temperature.timestamp` (fallback: общий `current.timestamp`).
- Метка нужна для быстрого контроля свежести данных по каждому датчику.

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
4. `python run_kvt.py stop`

## Замечания по проекту (актуальные)
- В репозитории нет подсистемы `archiver` (старые упоминания удалены из документации).
- Docker/compose артефактов в текущем дереве нет.
- Основной поддерживаемый сценарий запуска: `run_kvt.py`.

## Журнал документации
- 2026-06-03: исправлена кодировка русских строк в `Общее ТЗ на систему КВТ С.md`, `visualizer/routes/api.py` и `shared/config_manager.py`; общий ТЗ снова корректно отображается в GitHub.
- 2026-06-03: файл ревью `CODE_REVIEW_RECOMMENDATIONS.md` обновлен: пункт про кодировки отмечен как выполненный, а оставшиеся рекомендации сохранены как открытые.
- 2026-06-03: выполнен первый проход по `CODE_REVIEW_RECOMMENDATIONS.md`: добавлены atomic JSON helpers, tolerant runtime JSON reads, отложенное применение poller config, запрет scan во время polling, throttling записи `modbus_log.json`, generated Flask secret, MockServer stdout/stderr logs, валидация poller config и `.gitignore` для runtime/cache/secret артефактов.
- 2026-06-03: дополнительно закрыты пункты ревью `P1 Кодировки`, `P1 Runtime logs`, `P2 Репозиторий/runtime`: добавлены `.editorconfig`/`.gitattributes`, `modbus_log.json` переведен на компактную atomic-запись, `.gitignore` расширен на runtime JSON и `data/config/backups/`.
- 2026-06-03: закрыт пункт ревью про умножение timeout на группы регистров: при ошибке чтения `values` poller явно пропускает `statuses`, пишет `status="skipped"` в exchange log и считает `skipped_status_reads`.
