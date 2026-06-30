"""Shared current-data normalization for UI/API and integration services."""
import os

from shared.config_manager import load_poller_config, load_runtime_json, load_system_config


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, 'data')


def _has_value(metric):
    return isinstance(metric, dict) and metric.get('value') is not None


def _latest_sensor_snapshots():
    snapshots = {}
    candidates = []
    try:
        names = os.listdir(DATA_DIR)
    except OSError:
        names = []
    for name in names:
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


def _empty_metric(metric=None, status='no_connection'):
    payload = dict(metric or {}) if isinstance(metric, dict) else {}
    payload['value'] = None
    payload.setdefault('raw', None)
    payload.setdefault('timestamp', None)
    payload['status'] = payload.get('status') or status
    return payload


def _metric_snapshot(metric, keep_empty=False):
    if _has_value(metric):
        return metric
    if keep_empty:
        return _empty_metric(metric)
    return _empty_metric()


def _sensor_snapshot_from_config(sensor, port_names, latest=None, live_snapshot=False):
    latest = latest or {}
    temperature = _metric_snapshot(latest.get('temperature'), keep_empty=live_snapshot)
    humidity = _metric_snapshot(latest.get('humidity'), keep_empty=live_snapshot)
    poll_port_id = latest.get('poll_port_id') or sensor.get('poll_port_id') or 'default'
    payload = {
        **sensor,
        'display_number': latest.get('display_number') or sensor.get('local_number') or sensor.get('id'),
        'poll_port_id': poll_port_id,
        'poll_port_name': latest.get('poll_port_name') or port_names.get(str(poll_port_id), poll_port_id),
        'combined_status': latest.get('combined_status') or 'no_connection',
        'temperature': temperature,
        'humidity': humidity,
    }
    if live_snapshot:
        for key in ('transport', 'local_number', 'modbus_slave_id', 'modbus_addr_temp', 'modbus_addr_hum'):
            if latest.get(key) is not None:
                payload[key] = latest.get(key)
    return payload


def _offline_sensor_snapshot(sensor, port_names, port_status=None, timestamp=None):
    port_status = port_status or {}
    timestamp = timestamp or port_status.get('last_poll_at')
    return _sensor_snapshot_from_config(sensor, port_names, {
        'poll_port_id': sensor.get('poll_port_id') or 'default',
        'poll_port_name': port_status.get('name'),
        'transport': port_status.get('transport'),
        'combined_status': 'no_connection',
        'temperature': {'value': None, 'raw': None, 'timestamp': timestamp, 'status': 'offline'},
        'humidity': {'value': None, 'raw': None, 'timestamp': timestamp, 'status': 'offline'},
    }, live_snapshot=True)


def _port_has_authoritative_live_status(port_status):
    if not port_status:
        return False
    state = str(port_status.get('state') or '').lower()
    return bool(port_status.get('running')) or state in {'starting', 'running', 'degraded', 'error'}


def with_configured_sensors(current):
    current = dict(current or {})
    cfg = load_system_config()
    ports = load_poller_config().get('poll_ports', [])
    port_names = {str(p.get('id') or 'default'): p.get('name') or str(p.get('id') or 'default') for p in ports}
    port_statuses = {
        str(p.get('id') or 'default'): p
        for p in (current.get('poll_ports') or [])
        if p.get('id') is not None
    }
    runtime_sensors = current.get('sensors') or []
    by_id = {int(s.get('id')): s for s in runtime_sensors if s.get('id') is not None}
    latest_by_id = _latest_sensor_snapshots()
    sensors = []
    for sensor in cfg.get('sensors', []):
        if not sensor.get('enabled', True):
            continue
        sid = int(sensor.get('id'))
        runtime = by_id.get(sid)
        port_id = str(sensor.get('poll_port_id') or 'default')
        port_status = port_statuses.get(port_id)
        if runtime is not None:
            sensors.append(_sensor_snapshot_from_config(sensor, port_names, runtime, live_snapshot=True))
        elif _port_has_authoritative_live_status(port_status):
            sensors.append(_offline_sensor_snapshot(sensor, port_names, port_status, current.get('timestamp')))
        else:
            sensors.append(_sensor_snapshot_from_config(sensor, port_names, latest_by_id.get(sid)))
    current['sensors'] = sensors
    current.setdefault('timestamp', None)
    current.setdefault('stats', {})
    return current


def load_current_payload():
    path = os.path.join(DATA_DIR, 'current.json')
    current = load_runtime_json(path, default={'sensors': [], 'timestamp': None, 'stats': {}})
    return with_configured_sensors(current)
