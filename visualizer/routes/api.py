"""REST API для фронтенда."""
import json
import os
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request
from shared.config_manager import (
    load_system_config, save_system_config,
    load_poller_config, save_poller_config,
    load_notifications_config, save_notifications_config,
    get_sensors, get_sensor_by_id, add_sensor, update_sensor, delete_sensor,
    validate_sensor
)

api_bp = Blueprint('api', __name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'data')


def _load_archive():
    path = os.path.join(DATA_DIR, 'archive.json')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {'sensors': {}}


def _load_events():
    path = os.path.join(DATA_DIR, 'events.json')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {'events': []}


@api_bp.route('/current')
def api_current():
    path = os.path.join(DATA_DIR, 'current.json')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return jsonify(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return jsonify({'error': 'Нет данных'}), 404


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
    save_poller_config(data)
    return jsonify(data)


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