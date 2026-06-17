"""REST API для фронтенда."""
import os
import sys
import time
import subprocess
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request
import requests
from poller.config import validated_poller_config_patch
from shared.availability import is_ethernet_port, sync_daily_availability_from_current
from shared.config_manager import (
    load_system_config, save_system_config,
    load_poller_config, save_poller_config,
    load_notifications_config, save_notifications_config,
    load_theme_config, save_theme_config,
    load_runtime_json,
    get_sensors, get_sensor_by_id, add_sensor, update_sensor, delete_sensor,
    validate_sensor
)

api_bp = Blueprint('api', __name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'data')
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOG_DIR = os.path.join(ROOT_DIR, 'logs')
MOCKSERVER_OUT_LOG = os.path.join(LOG_DIR, 'mockserver.out.log')
MOCKSERVER_ERR_LOG = os.path.join(LOG_DIR, 'mockserver.err.log')
_MOCK_SERVER_PROCESS = None
_LOCAL_HTTP = requests.Session()
_LOCAL_HTTP.trust_env = False


def _mock_server_url() -> str:
    poller_cfg = load_poller_config()
    return str(poller_cfg.get('mock_server_url', 'http://127.0.0.1:8000')).rstrip('/')


def _is_mock_reachable(base_url: str) -> bool:
    try:
        resp = _LOCAL_HTTP.get(f"{base_url}/api/status", timeout=1.5)
        return resp.ok
    except requests.RequestException:
        return False


def _mock_status_payload():
    base_url = _mock_server_url()
    process_running = _MOCK_SERVER_PROCESS is not None and _MOCK_SERVER_PROCESS.poll() is None
    returncode = _MOCK_SERVER_PROCESS.poll() if _MOCK_SERVER_PROCESS is not None else None
    return {
        'url': base_url,
        'reachable': _is_mock_reachable(base_url),
        'process_running': process_running,
        'returncode': returncode,
        'stderr_tail': _tail_file(MOCKSERVER_ERR_LOG),
        'stdout_log': MOCKSERVER_OUT_LOG,
        'stderr_log': MOCKSERVER_ERR_LOG,
    }


def _tail_file(path: str, max_chars: int = 2000) -> str:
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_chars))
            return handle.read().strip()
    except FileNotFoundError:
        return ''


def _poller_base_url() -> str:
    cfg = load_system_config().get('network', {})
    host = str(cfg.get('poller_host', '127.0.0.1'))
    if host in ('0.0.0.0', '::'):
        host = '127.0.0.1'
    port = int(cfg.get('poller_port', 5001))
    return f"http://{host}:{port}"


def _poller_call(method: str, path: str, payload=None):
    try:
        url = f"{_poller_base_url()}{path}"
        timeout = 25 if '/api/poller/scan' in path else 3
        if method == 'GET':
            response = _LOCAL_HTTP.get(url, timeout=timeout)
        elif method == 'DELETE':
            response = _LOCAL_HTTP.delete(url, timeout=timeout)
        else:
            response = _LOCAL_HTTP.post(url, json=payload, timeout=timeout)
        content_type = response.headers.get('Content-Type', '')
        if 'application/json' in content_type.lower():
            try:
                data = response.json()
            except ValueError:
                data = {'error': f'Poller вернул некорректный JSON (HTTP {response.status_code})'}
                return data, 502
            return data, response.status_code
        text = (response.text or '').strip()
        snippet = text[:300] if text else '<empty>'
        return {'error': f'Poller вернул не-JSON ответ (HTTP {response.status_code}): {snippet}'}, 502
    except requests.RequestException as ex:
        return {'error': f'Poller недоступен: {ex}'}, 502
    except Exception as ex:
        return {'error': f'Ошибка проксирования Poller: {ex}'}, 500


def _load_archive():
    path = os.path.join(DATA_DIR, 'archive.json')
    return load_runtime_json(path, default={'sensors': {}})


def _load_events():
    path = os.path.join(DATA_DIR, 'events.json')
    return load_runtime_json(path, default={'events': []})


@api_bp.route('/current')
def api_current():
    path = os.path.join(DATA_DIR, 'current.json')
    current = load_runtime_json(path, default={})
    if not current:
        return jsonify({'error': 'Нет данных'}), 404
    return jsonify(current)


@api_bp.route('/availability/daily')
def api_availability_daily():
    current = load_runtime_json(os.path.join(DATA_DIR, 'current.json'), default={})
    poller_config = load_poller_config()
    payload = sync_daily_availability_from_current(current, poller_config, ping_ethernet=True)
    for port in poller_config.get('poll_ports', []):
        if not is_ethernet_port(port):
            continue
        port_id = str(port.get('id') or 'default')
        payload.setdefault('ports', {}).setdefault(port_id, {
            'id': port_id,
            'name': port.get('name') or port_id,
            'transport': port.get('transport'),
            'remote_host': port.get('remote_host') or port.get('udp_host'),
            'remote_port': port.get('remote_port') or port.get('udp_port'),
            'poll_cycles': 0,
            'poll_ok_cycles': 0,
            'poll_failed_cycles': 0,
            'network_checks': 0,
            'network_ok_checks': 0,
            'network_history': [],
        })
    return jsonify(payload)


@api_bp.route('/config')
def api_config():
    return jsonify(load_system_config())


@api_bp.route('/config', methods=['POST'])
def api_save_config():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Нет данных'}), 400
    config = save_system_config(data, data.get('_change_description', 'Обновление через API'))
    return jsonify(config)


# --- Sensors CRUD ---

@api_bp.route('/sensors')
def api_sensors():
    return jsonify(get_sensors())


@api_bp.route('/sensors/<int:sensor_id>')
def api_sensor(sensor_id):
    s = get_sensor_by_id(sensor_id)
    if s:
        return jsonify(s)
    return jsonify({'error': 'Датчик не найден'}), 404


@api_bp.route('/sensors', methods=['POST'])
def api_add_sensor():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Нет данных'}), 400
    sensor, errors = add_sensor(data)
    if errors:
        return jsonify({'errors': errors}), 400
    return jsonify(sensor), 201


@api_bp.route('/sensors/<int:sensor_id>', methods=['PUT'])
def api_update_sensor(sensor_id):
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Нет данных'}), 400
    sensor, errors = update_sensor(sensor_id, data)
    if errors:
        return jsonify({'errors': errors}), 400
    return jsonify(sensor)


@api_bp.route('/sensors/<int:sensor_id>', methods=['DELETE'])
def api_delete_sensor(sensor_id):
    sensor, errors = delete_sensor(sensor_id)
    if errors:
        return jsonify({'errors': errors}), 404
    return jsonify({'deleted': sensor})


# --- Poller config ---

@api_bp.route('/poller/config')
def api_poller_config():
    return jsonify(load_poller_config())


@api_bp.route('/poller/config', methods=['POST'])
def api_save_poller_config():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Нет данных'}), 400
    config, errors = validated_poller_config_patch(data, load_poller_config())
    if errors:
        return jsonify({'errors': errors}), 400
    proxied, code = _poller_call('POST', '/api/poller/config', config)
    if code < 400:
        return jsonify(proxied.get('config', config))
    save_poller_config(config)
    return jsonify({**config, '_warning': 'Poller недоступен, конфигурация сохранена локально'}), 202


@api_bp.route('/poller/status')
def api_poller_status():
    data, code = _poller_call('GET', '/api/poller/status')
    return jsonify(data), code


@api_bp.route('/poller/current')
def api_poller_current():
    data, code = _poller_call('GET', '/api/poller/current')
    return jsonify(data), code


@api_bp.route('/poller/log')
def api_poller_log():
    limit = request.args.get('limit', 100, type=int)
    poll_port_id = request.args.get('poll_port_id')
    suffix = f'&poll_port_id={poll_port_id}' if poll_port_id else ''
    data, code = _poller_call('GET', f'/api/poller/log?limit={limit}{suffix}')
    return jsonify(data), code


@api_bp.route('/poller/ports')
def api_poller_ports():
    data, code = _poller_call('GET', '/api/poller/ports')
    return jsonify(data), code


@api_bp.route('/poller/scan')
def api_poller_scan():
    poll_port_id = request.args.get('poll_port_id')
    start_id = request.args.get('start_id', 1, type=int)
    end_id = request.args.get('end_id', 32, type=int)
    timeout_ms = request.args.get('timeout_ms', 500, type=int)
    port_arg = f'poll_port_id={poll_port_id}&' if poll_port_id else ''
    data, code = _poller_call('GET', f'/api/poller/scan?{port_arg}start_id={start_id}&end_id={end_id}&timeout_ms={timeout_ms}')
    return jsonify(data), code


@api_bp.route('/poller/health')
def api_poller_health():
    data, code = _poller_call('GET', '/api/poller/health')
    return jsonify(data), code


@api_bp.route('/poller/start', methods=['POST'])
def api_poller_start():
    data, code = _poller_call('POST', '/api/poller/start')
    return jsonify(data), code


@api_bp.route('/poller/stop', methods=['POST'])
def api_poller_stop():
    data, code = _poller_call('POST', '/api/poller/stop')
    return jsonify(data), code


@api_bp.route('/poller/reload', methods=['POST'])
def api_poller_reload():
    data, code = _poller_call('POST', '/api/poller/reload')
    return jsonify(data), code


@api_bp.route('/poller/poll-ports')
def api_poller_poll_ports():
    data, code = _poller_call('GET', '/api/poller/poll-ports')
    return jsonify(data), code


@api_bp.route('/poller/poll-ports', methods=['POST'])
def api_save_poller_poll_ports():
    payload = request.get_json(silent=True) or {}
    data, code = _poller_call('POST', '/api/poller/poll-ports', payload)
    return jsonify(data), code


@api_bp.route('/poller/poll-ports/<port_id>', methods=['DELETE'])
def api_delete_poller_poll_port(port_id):
    data, code = _poller_call('DELETE', f'/api/poller/poll-ports/{port_id}')
    return jsonify(data), code


@api_bp.route('/poller/poll-ports/<port_id>/start', methods=['POST'])
def api_start_poller_poll_port(port_id):
    data, code = _poller_call('POST', f'/api/poller/poll-ports/{port_id}/start')
    return jsonify(data), code


@api_bp.route('/poller/poll-ports/<port_id>/stop', methods=['POST'])
def api_stop_poller_poll_port(port_id):
    data, code = _poller_call('POST', f'/api/poller/poll-ports/{port_id}/stop')
    return jsonify(data), code


@api_bp.route('/poller/poll-ports/<port_id>/restart', methods=['POST'])
def api_restart_poller_poll_port(port_id):
    data, code = _poller_call('POST', f'/api/poller/poll-ports/{port_id}/restart')
    return jsonify(data), code


@api_bp.route('/poller/poll-ports/<port_id>/log')
def api_poller_poll_port_log(port_id):
    limit = request.args.get('limit', 100, type=int)
    data, code = _poller_call('GET', f'/api/poller/poll-ports/{port_id}/log?limit={limit}')
    return jsonify(data), code


@api_bp.route('/mockserver/status')
def api_mockserver_status():
    return jsonify(_mock_status_payload())


@api_bp.route('/mockserver/start', methods=['POST'])
def api_mockserver_start():
    global _MOCK_SERVER_PROCESS
    base_url = _mock_server_url()
    if _is_mock_reachable(base_url):
        try:
            _LOCAL_HTTP.post(f"{base_url}/api/start_all", timeout=2)
        except requests.RequestException:
            pass
        return jsonify({'status': 'ok', **_mock_status_payload()})

    if _MOCK_SERVER_PROCESS is None or _MOCK_SERVER_PROCESS.poll() is not None:
        script_path = os.path.join(ROOT_DIR, 'MocTestServer', 'server', 'run.py')
        poller_cfg = load_poller_config()
        host = str(poller_cfg.get('mock_server_host', '127.0.0.1'))
        port = int(poller_cfg.get('mock_server_port', 8000))
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(MOCKSERVER_OUT_LOG, 'a', encoding='utf-8') as stdout, open(MOCKSERVER_ERR_LOG, 'a', encoding='utf-8') as stderr:
            _MOCK_SERVER_PROCESS = subprocess.Popen(
                [sys.executable, script_path, '--host', host, '--port', str(port)],
                cwd=ROOT_DIR,
                stdout=stdout,
                stderr=stderr
            )
        for _ in range(15):
            if _is_mock_reachable(base_url):
                break
            time.sleep(0.2)

    if _is_mock_reachable(base_url):
        try:
            _LOCAL_HTTP.post(f"{base_url}/api/start_all", timeout=2)
        except requests.RequestException:
            pass
        return jsonify({'status': 'ok', **_mock_status_payload()})
    return jsonify({'error': 'Не удалось запустить Mock Server'}), 500


@api_bp.route('/mockserver/stop', methods=['POST'])
def api_mockserver_stop():
    global _MOCK_SERVER_PROCESS
    base_url = _mock_server_url()
    try:
        _LOCAL_HTTP.post(f"{base_url}/api/stop_all", timeout=2)
    except requests.RequestException:
        pass

    if _MOCK_SERVER_PROCESS is not None and _MOCK_SERVER_PROCESS.poll() is None:
        _MOCK_SERVER_PROCESS.terminate()
        try:
            _MOCK_SERVER_PROCESS.wait(timeout=3)
        except subprocess.TimeoutExpired:
            _MOCK_SERVER_PROCESS.kill()
        _MOCK_SERVER_PROCESS = None
    return jsonify({'status': 'ok', **_mock_status_payload()})


# --- Network config ---

@api_bp.route('/network/config')
def api_network_config():
    config = load_system_config()
    return jsonify(config.get('network', {}))


@api_bp.route('/network/config', methods=['POST'])
def api_save_network_config():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Нет данных'}), 400
    config = load_system_config()
    config['network'] = data
    save_system_config(config, 'Обновлены сетевые настройки')
    return jsonify(data)


# --- Archive / History data ---

@api_bp.route('/archive/sensor/<int:sensor_id>')
def api_archive_sensor(sensor_id):
    """
    Архивные данные датчика.
    Query params:
      period: 1h, 6h, 24h, 7d, 30d (default: 24h)
      from: ISO datetime
      to: ISO datetime
    """
    archive = _load_archive()
    sensor_key = str(sensor_id)
    sensor_data = archive.get('sensors', {}).get(sensor_key)
    if not sensor_data:
        return jsonify({'error': 'Нет архивных данных для датчика'}), 404

    # Parse period or from/to
    period = request.args.get('period', '24h')
    from_str = request.args.get('from')
    to_str = request.args.get('to')

    now = datetime.now()

    if from_str and to_str:
        try:
            dt_from = datetime.fromisoformat(from_str)
            dt_to = datetime.fromisoformat(to_str)
        except ValueError:
            return jsonify({'error': 'Неверный формат даты'}), 400
    else:
        period_map = {
            '1h': timedelta(hours=1),
            '6h': timedelta(hours=6),
            '12h': timedelta(hours=12),
            '24h': timedelta(hours=24),
            '3d': timedelta(days=3),
            '7d': timedelta(days=7),
            '14d': timedelta(days=14),
            '30d': timedelta(days=30),
        }
        delta = period_map.get(period, timedelta(hours=24))
        dt_from = now - delta
        dt_to = now

    # Filter data by time range
    filtered = []
    for point in sensor_data.get('data', []):
        try:
            ts = datetime.fromisoformat(point['timestamp'])
        except (ValueError, KeyError):
            continue
        if dt_from <= ts <= dt_to:
            filtered.append(point)

    # Get sensor config for limits
    sensor_config = get_sensor_by_id(sensor_id)
    limits = {}
    if sensor_config:
        limits = {
            'temp_limits': sensor_config.get('temp_limits', {}),
            'hum_limits': sensor_config.get('hum_limits', {}),
        }

    return jsonify({
        'sensor_id': sensor_id,
        'sensor_name': sensor_data.get('name', f'Датчик {sensor_id}'),
        'period': period,
        'from': dt_from.isoformat(),
        'to': dt_to.isoformat(),
        'data_count': len(filtered),
        'limits': limits,
        'data': filtered
    })


@api_bp.route('/events')
def api_events():
    """
    Журнал событий.
    Query params:
      sensor_id: фильтр по датчику
      type: фильтр по типу (alarm, warning)
      limit: количество записей (default: 50)
    """
    events_data = _load_events()
    events = events_data.get('events', [])

    sensor_id = request.args.get('sensor_id', type=int)
    event_type = request.args.get('type')
    limit = request.args.get('limit', 50, type=int)

    if sensor_id:
        events = [e for e in events if e.get('sensor_id') == sensor_id]
    if event_type:
        events = [e for e in events if event_type in e.get('event_type', '')]

    return jsonify({
        'total': len(events),
        'events': events[:limit]
    })


@api_bp.route('/archive/summary')
def api_archive_summary():
    """Сводка по всем датчикам за период (для главной)."""
    archive = _load_archive()
    period = request.args.get('period', '24h')
    now = datetime.now()
    period_map = {
        '1h': timedelta(hours=1),
        '6h': timedelta(hours=6),
        '24h': timedelta(hours=24),
        '7d': timedelta(days=7),
        '30d': timedelta(days=30),
    }
    delta = period_map.get(period, timedelta(hours=24))
    dt_from = now - delta

    summary = {}
    for sid, sdata in archive.get('sensors', {}).items():
        temps = []
        hums = []
        for point in sdata.get('data', []):
            try:
                ts = datetime.fromisoformat(point['timestamp'])
            except (ValueError, KeyError):
                continue
            if ts >= dt_from:
                t = point.get('temperature', {})
                h = point.get('humidity', {})
                if t.get('avg') is not None:
                    temps.append(t)
                if h.get('avg') is not None:
                    hums.append(h)

        if temps:
            summary[sid] = {
                'name': sdata.get('name'),
                'temp_min': min(t['min'] for t in temps),
                'temp_max': max(t['max'] for t in temps),
                'temp_avg': round(sum(t['avg'] for t in temps) / len(temps), 1),
                'hum_min': min(h['min'] for h in hums) if hums else None,
                'hum_max': max(h['max'] for h in hums) if hums else None,
                'hum_avg': round(sum(h['avg'] for h in hums) / len(hums), 1) if hums else None,
                'data_points': len(temps)
            }

    return jsonify(summary)


# --- Theme config ---

@api_bp.route('/theme')
def api_theme():
    return jsonify(load_theme_config())


@api_bp.route('/theme', methods=['POST'])
def api_save_theme():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Нет данных'}), 400
    # Validate theme name
    if data.get('theme') not in ('dark', 'light'):
        return jsonify({'error': 'Тема должна быть "dark" или "light"'}), 400
    # Validate app_title
    title = data.get('app_title', '').strip()
    if not title:
        return jsonify({'error': 'Название приложения не может быть пустым'}), 400
    if len(title) > 50:
        return jsonify({'error': 'Название приложения не более 50 символов'}), 400
    config = save_theme_config(data)
    return jsonify(config)

