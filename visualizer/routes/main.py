"""Главная страница и детальный просмотр датчика."""
from flask import Blueprint, render_template
from shared.current_data import load_current_payload, with_configured_sensors
 
main_bp = Blueprint('main', __name__)

_with_configured_sensors = with_configured_sensors
_load_current = load_current_payload


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
