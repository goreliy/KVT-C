"""
REST API для Mock Current Generator
"""

import json
import os

from flask import Blueprint, jsonify, request
from .generator import CurrentGenerator, DATA_DIR

current_api = Blueprint('current_api', __name__, url_prefix='/api/current')

_generator: CurrentGenerator = None


def init_generator(config=None):
    global _generator
    _generator = CurrentGenerator(config)
    return _generator


def get_generator() -> CurrentGenerator:
    global _generator
    if _generator is None:
        _generator = CurrentGenerator()
    return _generator


@current_api.route('/status', methods=['GET'])
def get_status():
    return jsonify(get_generator().get_status())


@current_api.route('/start', methods=['POST'])
def start():
    get_generator().start()
    return jsonify({"status": "ok", "message": "Generator started"})


@current_api.route('/stop', methods=['POST'])
def stop():
    get_generator().stop()
    return jsonify({"status": "ok", "message": "Generator stopped"})


@current_api.route('/generate', methods=['POST'])
def generate_once():
    data = get_generator().generate_once()
    return jsonify({"status": "ok", "data": data})


@current_api.route('/preview', methods=['GET'])
def get_preview():
    return jsonify(get_generator().get_preview())


@current_api.route('/config', methods=['GET'])
def get_config():
    return jsonify(get_generator().config)


@current_api.route('/config', methods=['POST'])
def update_config():
    new_config = request.get_json()
    if not new_config:
        return jsonify({"error": "No config provided"}), 400
    get_generator().update_config(new_config)
    return jsonify({"status": "ok"})


@current_api.route('/set_scenario', methods=['POST'])
def set_scenario():
    params = request.get_json()
    if not params or 'scenario' not in params:
        return jsonify({"error": "scenario required"}), 400
    get_generator().set_scenario(params['scenario'])
    return jsonify({"status": "ok", "scenario": params['scenario']})


@current_api.route('/set_sensor', methods=['POST'])
def set_sensor():
    params = request.get_json()
    if not params or 'sensor_id' not in params:
        return jsonify({"error": "sensor_id required"}), 400
    get_generator().set_sensor_value(
        params['sensor_id'], params.get('temperature'), params.get('humidity')
    )
    return jsonify({"status": "ok"})


def _compute_log_statistics(entries: list) -> dict:
    """Вычислить статистику из записей лога"""
    tx_count = sum(1 for e in entries if e.get("direction") == "TX")
    rx_count = sum(1 for e in entries if e.get("direction") == "RX")
    errors = sum(1 for e in entries if e.get("direction") == "RX" and e.get("parsed", {}).get("error"))
    response_times = [e["response_time_ms"] for e in entries if e.get("response_time_ms") is not None]

    return {
        "total_entries": len(entries),
        "tx_count": tx_count, "rx_count": rx_count,
        "error_count": errors,
        "avg_response_time_ms": round(sum(response_times) / len(response_times), 2) if response_times else 0,
        "min_response_time_ms": min(response_times) if response_times else 0,
        "max_response_time_ms": max(response_times) if response_times else 0
    }


@current_api.route('/modbus_log', methods=['GET'])
def get_modbus_log():
    """GET /api/current/modbus_log - Получить лог Modbus из файла"""
    log_path = os.path.join(DATA_DIR, 'modbus_log.json')

    if not os.path.exists(log_path):
        return jsonify({"max_entries": 0, "entries": [], "statistics": {}})

    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        entries = data.get("entries", [])
        data["statistics"] = _compute_log_statistics(entries)

        limit = request.args.get('limit', 100, type=int)
        if len(entries) > limit:
            data["entries"] = entries[-limit:]

        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e), "max_entries": 0, "entries": [], "statistics": {}})
