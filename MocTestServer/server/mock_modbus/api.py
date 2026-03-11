"""
REST API для Mock Modbus Server
"""

from flask import Blueprint, jsonify, request
from .server import ModbusServer

modbus_api = Blueprint('modbus_api', __name__, url_prefix='/api/modbus')

_server: ModbusServer = None


def init_server(config=None):
    global _server
    _server = ModbusServer(config)
    return _server


def get_server() -> ModbusServer:
    global _server
    if _server is None:
        _server = ModbusServer()
    return _server


@modbus_api.route('/status', methods=['GET'])
def get_status():
    return jsonify(get_server().get_status())


@modbus_api.route('/start', methods=['POST'])
def start():
    get_server().start()
    return jsonify({"status": "ok", "message": "Modbus server started"})


@modbus_api.route('/stop', methods=['POST'])
def stop():
    get_server().stop()
    return jsonify({"status": "ok", "message": "Modbus server stopped"})


@modbus_api.route('/registers', methods=['GET'])
def get_registers():
    return jsonify(get_server().get_registers())


@modbus_api.route('/config', methods=['GET'])
def get_config():
    return jsonify(get_server().config)


@modbus_api.route('/config', methods=['POST'])
def update_config():
    new_config = request.get_json()
    if not new_config:
        return jsonify({"error": "No config provided"}), 400
    get_server().update_config(new_config)
    return jsonify({"status": "ok"})


@modbus_api.route('/set_scenario', methods=['POST'])
def set_scenario():
    params = request.get_json()
    if not params or 'scenario' not in params:
        return jsonify({"error": "scenario required"}), 400
    get_server().set_scenario(params['scenario'])
    return jsonify({"status": "ok", "scenario": params['scenario']})


@modbus_api.route('/log', methods=['GET'])
def get_request_log():
    limit = request.args.get('limit', 100, type=int)
    return jsonify(get_server().get_request_log(limit))


@modbus_api.route('/log/clear', methods=['POST'])
def clear_request_log():
    get_server().clear_request_log()
    return jsonify({"status": "ok"})
