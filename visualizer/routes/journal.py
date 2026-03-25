"""Журналы: события, температуры, превышения."""
import json
import os
from datetime import datetime, timedelta
from flask import Blueprint, render_template, jsonify, request
from shared.config_manager import get_sensors, get_sensor_by_id

journal_bp = Blueprint('journal', __name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'data')


def _load_json(filename):
    path = os.path.join(DATA_DIR, filename)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_json(filename, data):
    path = os.path.join(DATA_DIR, filename)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _parse_period(period_str):
    """Return (dt_from, dt_to) for a named period."""
    now = datetime.now()
    periods = {
        '1h': timedelta(hours=1),
        '6h': timedelta(hours=6),
        '24h': timedelta(hours=24),
        '7d': timedelta(days=7),
        '30d': timedelta(days=30),
    }
    delta = periods.get(period_str, timedelta(hours=24))
    return now - delta, now


# ── Pages ──

@journal_bp.route('/events')
def events_page():
    return render_template('journal/events.html')


@journal_bp.route('/journal/temperatures')
def temperatures_page():
    return render_template('journal/temperatures.html')


@journal_bp.route('/journal/violations')
def violations_page():
    return render_template('journal/violations.html')


# ── API: Events ──

@journal_bp.route('/api/journal/events')
def api_events():
    """Events with filtering by sensor_id, type, date range, limit."""
    events_data = _load_json('events.json')
    events = events_data.get('events', [])

    sensor_id = request.args.get('sensor_id', type=int)
    event_type = request.args.get('type')
    date_from = request.args.get('from')
    date_to = request.args.get('to')
    limit = request.args.get('limit', 50, type=int)

    if sensor_id:
        events = [e for e in events if e.get('sensor_id') == sensor_id]
    if event_type:
        events = [e for e in events if event_type in e.get('event_type', '')]
    if date_from:
        try:
            dt_from = datetime.fromisoformat(date_from)
            events = [e for e in events if datetime.fromisoformat(e['timestamp']) >= dt_from]
        except ValueError:
            pass
    if date_to:
        try:
            dt_to = datetime.fromisoformat(date_to)
            events = [e for e in events if datetime.fromisoformat(e['timestamp']) <= dt_to]
        except ValueError:
            pass

    # Sort newest first
    events.sort(key=lambda e: e.get('timestamp', ''), reverse=True)

    return jsonify({'total': len(events), 'events': events[:limit]})


@journal_bp.route('/api/journal/events/<int:event_id>/ack', methods=['POST'])
def api_ack_event(event_id):
    """Acknowledge an event."""
    events_data = _load_json('events.json')
    events = events_data.get('events', [])

    body = request.get_json(silent=True) or {}
    operator = body.get('operator', 'operator')
    comment = body.get('comment', '')

    for ev in events:
        if ev.get('id') == event_id:
            ev['acknowledged'] = True
            ev['acknowledged_at'] = datetime.now().isoformat()
            ev['acknowledged_by'] = operator
            if comment:
                ev['comment'] = comment
            _save_json('events.json', events_data)
            return jsonify(ev)

    return jsonify({'error': 'Событие не найдено'}), 404


# ── API: Temperature log (aggregated from archive.json) ──

@journal_bp.route('/api/journal/temperatures')
def api_temperatures():
    """Aggregated min/max/avg per sensor for a given period type."""
    archive = _load_json('archive.json')
    period = request.args.get('period', 'day')  # hour, day, week
    sensor_id = request.args.get('sensor_id')

    now = datetime.now()
    if period == 'hour':
        dt_from = now - timedelta(hours=24)
    elif period == 'week':
        dt_from = now - timedelta(days=30)
    else:  # day
        dt_from = now - timedelta(days=7)

    result = []
    sensors_data = archive.get('sensors', {})
    target_keys = [sensor_id] if sensor_id else list(sensors_data.keys())

    for sid in target_keys:
        sdata = sensors_data.get(sid)
        if not sdata:
            continue

        # Group data points by period bucket
        buckets = {}
        for point in sdata.get('data', []):
            try:
                ts = datetime.fromisoformat(point['timestamp'])
            except (ValueError, KeyError):
                continue
            if ts < dt_from:
                continue

            if period == 'hour':
                key = ts.strftime('%Y-%m-%d %H:00')
            elif period == 'week':
                # Group by ISO week
                iso = ts.isocalendar()
                key = f'{iso[0]}-W{iso[1]:02d}'
            else:  # day
                key = ts.strftime('%Y-%m-%d')

            if key not in buckets:
                buckets[key] = {'temps': [], 'hums': [], 'samples': 0}

            t = point.get('temperature', {})
            h = point.get('humidity', {})
            if t.get('avg') is not None:
                buckets[key]['temps'].append(t)
            if h.get('avg') is not None:
                buckets[key]['hums'].append(h)
            buckets[key]['samples'] += point.get('sample_count', 1)

        # Build aggregated entries
        for bucket_key in sorted(buckets.keys()):
            b = buckets[bucket_key]
            temps = b['temps']
            hums = b['hums']
            if not temps:
                continue
            entry = {
                'sensor_id': int(sid),
                'sensor_name': sdata.get('name', f'Датчик {sid}'),
                'period': bucket_key,
                'period_type': period,
                'temp_min': round(min(t['min'] for t in temps), 1),
                'temp_max': round(max(t['max'] for t in temps), 1),
                'temp_avg': round(sum(t['avg'] for t in temps) / len(temps), 1),
                'hum_min': round(min(h['min'] for h in hums), 1) if hums else None,
                'hum_max': round(max(h['max'] for h in hums), 1) if hums else None,
                'hum_avg': round(sum(h['avg'] for h in hums) / len(hums), 1) if hums else None,
                'sample_count': b['samples'],
            }
            result.append(entry)

    return jsonify({'data': result, 'period_type': period, 'total': len(result)})


# ── API: Violations (derived from events with duration tracking) ──

@journal_bp.route('/api/journal/violations')
def api_violations():
    """Threshold violations with duration, peak values, ack status."""
    events_data = _load_json('events.json')
    events = events_data.get('events', [])

    sensor_id = request.args.get('sensor_id', type=int)
    date_from = request.args.get('from')
    date_to = request.args.get('to')
    limit = request.args.get('limit', 50, type=int)

    # Filter alarm/warning events as violations
    violation_types = ('alarm', 'warning')
    violations = [e for e in events if any(vt in e.get('event_type', '') for vt in violation_types)]

    if sensor_id:
        violations = [v for v in violations if v.get('sensor_id') == sensor_id]
    if date_from:
        try:
            dt_from = datetime.fromisoformat(date_from)
            violations = [v for v in violations if datetime.fromisoformat(v['timestamp']) >= dt_from]
        except ValueError:
            pass
    if date_to:
        try:
            dt_to = datetime.fromisoformat(date_to)
            violations = [v for v in violations if datetime.fromisoformat(v['timestamp']) <= dt_to]
        except ValueError:
            pass

    violations.sort(key=lambda v: v.get('timestamp', ''), reverse=True)

    # Enrich with violation-specific fields
    enriched = []
    for v in violations[:limit]:
        vtype = v.get('event_type', '')
        if 'alarm' in vtype:
            violation_level = 'alarm'
        else:
            violation_level = 'warning'

        param = 'temperature'
        if 'hum' in vtype:
            param = 'humidity'

        direction = 'high'
        if 'low' in vtype:
            direction = 'low'

        enriched.append({
            'id': v.get('id'),
            'sensor_id': v.get('sensor_id'),
            'timestamp': v.get('timestamp'),
            'parameter': param,
            'direction': direction,
            'violation_type': f'{violation_level}_{direction}',
            'level': violation_level,
            'value': v.get('value'),
            'message': v.get('message', ''),
            'acknowledged': v.get('acknowledged', False),
            'acknowledged_at': v.get('acknowledged_at'),
            'acknowledged_by': v.get('acknowledged_by'),
        })

    return jsonify({'violations': enriched, 'total': len(enriched)})


@journal_bp.route('/api/journal/violations/<int:violation_id>/ack', methods=['POST'])
def api_ack_violation(violation_id):
    """Acknowledge a violation (same as ack event since violations are events)."""
    return api_ack_event(violation_id)
