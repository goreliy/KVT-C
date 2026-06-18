"""Главная страница и детальный просмотр датчика."""
import os
from flask import Blueprint, render_template
from shared.config_manager import load_poller_config, load_runtime_json, load_system_config

main_bp = Blueprint('main', __name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'data')


def _has_value(metric):
    return isinstance(metric, dict) and metric.get('value') is not None


def _latest_sensor_snapshots():
    snapshots = {}
    candidates = []
    for name in os.listdir(DATA_DIR):
        if name.startswith('.current.json.') and name.endswith('.tmp'):
            candidates.append(os.path.join(DATA_DIR, name))
    candidates.sort(key=lambda path: os.path.getmtime(path), reverse=True)

    for path in candidates:
        payload = load_runtime_json(path, default={})
        for sensor in payload.get('sensors') or []:
            sid = sensor.get('id')
            if sid is None or sid in snapshots:
                continue
            if _has_value(sensor.get('temperature')) or _has_value(sensor.get('humidity')):
                snapshots[int(sid)] = sensor

    archive = load_runtime_json(os.path.join(DATA_DIR, 'archive.json'), default={})
    for sid_raw, sensor_data in (archive.get('sensors') or {}).items():
        try:
            sid = int(sid_raw)
        except (TypeError, ValueError):
            continue
        if sid in snapshots:
            continue
        points = sensor_data.get('data') or []
        if not points:
            continue
        point = points[-1]
        temp = point.get('temperature') or {}
        hum = point.get('humidity') or {}
        snapshots[sid] = {
            'id': sid,
            'name': sensor_data.get('name'),
            'combined_status': point.get('status') or 'normal',
            'temperature': {'value': temp.get('avg'), 'timestamp': point.get('timestamp'), 'status': point.get('status') or 'normal'},
            'humidity': {'value': hum.get('avg'), 'timestamp': point.get('timestamp'), 'status': point.get('status') or 'normal'},
        }
    return snapshots


def _sensor_snapshot_from_config(sensor, port_names, latest=None):
    latest = latest or {}
    temperature = latest.get('temperature') if _has_value(latest.get('temperature')) else {'value': None, 'timestamp': None, 'status': 'no_connection'}
    humidity = latest.get('humidity') if _has_value(latest.get('humidity')) else {'value': None, 'timestamp': None, 'status': 'no_connection'}
    return {
        **sensor,
        'display_number': sensor.get('local_number') or sensor.get('id'),
        'poll_port_id': sensor.get('poll_port_id') or 'default',
        'poll_port_name': port_names.get(str(sensor.get('poll_port_id') or 'default'), sensor.get('poll_port_id') or 'default'),
        'combined_status': latest.get('combined_status') or 'no_connection',
        'temperature': temperature,
        'humidity': humidity,
    }


def _with_configured_sensors(current):
    current = dict(current or {})
    cfg = load_system_config()
    ports = load_poller_config().get('poll_ports', [])
    port_names = {str(p.get('id') or 'default'): p.get('name') or str(p.get('id') or 'default') for p in ports}
    runtime_sensors = current.get('sensors') or []
    by_id = {int(s.get('id')): s for s in runtime_sensors if s.get('id') is not None}
    latest_by_id = _latest_sensor_snapshots()
    sensors = []
    for sensor in cfg.get('sensors', []):
        if not sensor.get('enabled', True):
            continue
        sid = int(sensor.get('id'))
        runtime = by_id.get(sid)
        if runtime and (_has_value(runtime.get('temperature')) or _has_value(runtime.get('humidity'))):
            sensors.append(runtime)
        else:
            sensors.append(_sensor_snapshot_from_config(sensor, port_names, latest_by_id.get(sid)))
    current['sensors'] = sensors
    current.setdefault('timestamp', None)
    current.setdefault('stats', {})
    return current


def _load_current():
    path = os.path.join(DATA_DIR, 'current.json')
    current = load_runtime_json(path, default={'sensors': [], 'timestamp': None, 'stats': {}})
    return _with_configured_sensors(current)


@main_bp.route('/')
def index():
    data = _load_current()
    return render_template('index.html', data=data)


@main_bp.route('/sensor/<int:sensor_id>')
def sensor_detail(sensor_id):
    data = _load_current()
    sensor = None
    for s in data.get('sensors', []):
        if s['id'] == sensor_id:
            sensor = s
            break
    return render_template('sensor.html', sensor=sensor, sensor_id=sensor_id)
