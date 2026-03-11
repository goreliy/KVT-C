"""Главная страница и детальный просмотр датчика."""
import json
import os
from flask import Blueprint, render_template

main_bp = Blueprint('main', __name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'data')


def _load_current():
    path = os.path.join(DATA_DIR, 'current.json')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {'sensors': [], 'timestamp': None, 'statistics': {}}


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


@main_bp.route('/events')
def events_page():
    return render_template('events.html')