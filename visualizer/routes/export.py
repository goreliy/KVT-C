"""Страница экспорта данных: выбор датчиков, периода, формата (CSV/JSON)."""
import csv
import io
import json
import os
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, Response, jsonify
from shared.config_manager import get_sensors

export_bp = Blueprint('export', __name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'data')


def _load_archive():
    path = os.path.join(DATA_DIR, 'archive.json')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {'sensors': {}}


@export_bp.route('/export')
def export_page():
    return render_template('export.html')


@export_bp.route('/api/export/download')
def export_download():
    """
    Экспорт архивных данных в CSV или JSON.
    Query params:
      sensor_ids: comma-separated sensor IDs (empty = all)
      from: ISO date (YYYY-MM-DD)
      to: ISO date (YYYY-MM-DD)
      format: csv | json (default: csv)
    """
    fmt = request.args.get('format', 'csv').lower()
    if fmt not in ('csv', 'json'):
        return jsonify({'error': 'Формат должен быть csv или json'}), 400

    sensor_ids_raw = request.args.get('sensor_ids', '')
    date_from_str = request.args.get('from', '')
    date_to_str = request.args.get('to', '')

    # Parse dates
    now = datetime.now()
    try:
        dt_from = datetime.fromisoformat(date_from_str + 'T00:00:00') if date_from_str else now - timedelta(days=7)
    except ValueError:
        return jsonify({'error': 'Неверный формат даты "от"'}), 400
    try:
        dt_to = datetime.fromisoformat(date_to_str + 'T23:59:59') if date_to_str else now
    except ValueError:
        return jsonify({'error': 'Неверный формат даты "до"'}), 400

    archive = _load_archive()
    sensors_data = archive.get('sensors', {})

    # Determine which sensors to export
    if sensor_ids_raw:
        target_ids = [s.strip() for s in sensor_ids_raw.split(',') if s.strip()]
    else:
        target_ids = list(sensors_data.keys())

    # Collect filtered data
    rows = []
    for sid in target_ids:
        sdata = sensors_data.get(sid)
        if not sdata:
            continue
        sensor_name = sdata.get('name', f'Датчик {sid}')
        for point in sdata.get('data', []):
            try:
                ts = datetime.fromisoformat(point['timestamp'])
            except (ValueError, KeyError):
                continue
            if ts < dt_from or ts > dt_to:
                continue
            t = point.get('temperature', {})
            h = point.get('humidity', {})
            rows.append({
                'sensor_id': int(sid),
                'sensor_name': sensor_name,
                'timestamp': point['timestamp'],
                'temp_avg': t.get('avg'),
                'temp_min': t.get('min'),
                'temp_max': t.get('max'),
                'hum_avg': h.get('avg'),
                'hum_min': h.get('min'),
                'hum_max': h.get('max'),
                'status': point.get('status', ''),
                'sample_count': point.get('sample_count', 1),
            })

    rows.sort(key=lambda r: (r['sensor_id'], r['timestamp']))

    # Build filename
    from_label = dt_from.strftime('%Y%m%d')
    to_label = dt_to.strftime('%Y%m%d')
    filename = f'kvt_export_{from_label}_{to_label}.{fmt}'

    if fmt == 'json':
        content = json.dumps({
            'exported_at': now.isoformat(),
            'period': {'from': dt_from.isoformat(), 'to': dt_to.isoformat()},
            'record_count': len(rows),
            'data': rows,
        }, ensure_ascii=False, indent=2)
        return Response(
            content,
            mimetype='application/json',
            headers={'Content-Disposition': f'attachment; filename="{filename}"'}
        )

    # CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'sensor_id', 'sensor_name', 'timestamp',
        'temp_avg', 'temp_min', 'temp_max',
        'hum_avg', 'hum_min', 'hum_max',
        'status', 'sample_count'
    ])
    for r in rows:
        writer.writerow([
            r['sensor_id'], r['sensor_name'], r['timestamp'],
            r['temp_avg'], r['temp_min'], r['temp_max'],
            r['hum_avg'], r['hum_min'], r['hum_max'],
            r['status'], r['sample_count'],
        ])

    csv_bytes = output.getvalue().encode('utf-8-sig')
    return Response(
        csv_bytes,
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )
