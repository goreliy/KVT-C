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

## Poller: Modbus transport
Поддерживаются два режима:
- `serial` — через COM-порт (RTU)
- `udp` — нативный RTU-over-UDP (RTU кадр + CRC через UDP сокет)

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
