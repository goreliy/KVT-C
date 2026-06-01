"""
Главное Flask приложение - веб-интерфейс управления Mock Server
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.python_compat import patch_legacy_werkzeug_ast
patch_legacy_werkzeug_ast()

from flask import Flask, render_template, jsonify, request

from .mock_modbus.api import modbus_api, init_server as init_modbus, get_server as get_modbus
from .mock_current.api import current_api, init_generator as init_current, get_generator as get_current
from .mock_archive.api import archive_api, init_server as init_archive, get_server as get_archive

app = Flask(__name__, template_folder='templates', static_folder='static')

app.register_blueprint(modbus_api)
app.register_blueprint(current_api)
app.register_blueprint(archive_api)

_config = None


def load_config(config_path: str = None) -> dict:
    global _config
    if config_path and os.path.exists(config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            _config = json.load(f)
    else:
        _config = default_config()
    return _config


def default_config() -> dict:
    return {
        "config_version": "1.0",
        "ui": {"port": 8000, "host": "0.0.0.0"},
        "servers": {
            "modbus": {"enabled": True, "config": None},
            "current": {"enabled": True, "config": None},
            "archive": {"enabled": True, "config": None}
        },
        "auto_start": False,
        "log_level": "INFO"
    }


def init_app(config: dict = None):
    global _config
    _config = config or default_config()

    servers_cfg = _config.get("servers", {})
    _init_if_enabled = [
        ("modbus", init_modbus),
        ("current", init_current),
        ("archive", init_archive),
    ]
    for name, init_fn in _init_if_enabled:
        srv_cfg = servers_cfg.get(name, {})
        if srv_cfg.get("enabled", True):
            init_fn(srv_cfg.get("config"))

    return app


# ── Страницы ──

@app.route('/')
def dashboard():
    return render_template('dashboard.html')


@app.route('/modbus')
def modbus_page():
    return render_template('modbus.html')


@app.route('/current')
def current_page():
    return render_template('current.html')


@app.route('/archive')
def archive_page():
    return render_template('archive.html')


@app.route('/scenarios')
def scenarios_page():
    return render_template('scenarios.html')


# ── API управления всеми серверами ──

def _get_all_servers():
    return get_modbus(), get_current(), get_archive()


@app.route('/api/status', methods=['GET'])
def get_all_status():
    modbus, current, archive = _get_all_servers()
    return jsonify({
        "modbus": modbus.get_status(),
        "current": current.get_status(),
        "archive": archive.get_status()
    })


@app.route('/api/start_all', methods=['POST'])
def start_all():
    for srv in _get_all_servers():
        srv.start()
    return jsonify({"status": "ok", "message": "All servers started"})


@app.route('/api/stop_all', methods=['POST'])
def stop_all():
    for srv in _get_all_servers():
        srv.stop()
    return jsonify({"status": "ok", "message": "All servers stopped"})


@app.route('/api/config', methods=['GET'])
def get_config():
    modbus, current, archive = _get_all_servers()
    return jsonify({
        "ui": _config.get("ui", {}),
        "modbus": modbus.config,
        "current": current.config,
        "archive": archive.config
    })


@app.route('/api/config', methods=['POST'])
def save_config():
    new_config = request.get_json()
    if not new_config:
        return jsonify({"error": "No config provided"}), 400

    modbus, current, archive = _get_all_servers()
    config_map = {"modbus": modbus, "current": current, "archive": archive}
    for key, srv in config_map.items():
        if key in new_config:
            srv.update_config(new_config[key])

    return jsonify({"status": "ok"})


@app.route('/api/scenarios', methods=['GET'])
def get_scenarios():
    from .scenarios import SCENARIOS
    return jsonify({
        "scenarios": [
            {"name": name, "description": getattr(cls, 'description', name)}
            for name, cls in SCENARIOS.items()
        ]
    })


@app.route('/api/set_scenario_all', methods=['POST'])
def set_scenario_all():
    params = request.get_json()
    if not params or 'scenario' not in params:
        return jsonify({"error": "scenario required"}), 400

    scenario = params['scenario']
    get_modbus().set_scenario(scenario)
    get_current().set_scenario(scenario)
    return jsonify({"status": "ok", "scenario": scenario})


def run_server(host: str = "0.0.0.0", port: int = 8000, debug: bool = False):
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == '__main__':
    init_app()
    run_server(debug=True)
