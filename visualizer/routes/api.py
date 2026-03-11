"""REST API для фронтенда."""
import json
import os
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