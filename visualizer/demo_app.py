"""Отдельный процесс учебного проекта. Никогда не запускает реальные службы.

KVT_HOME устанавливается родителем до импорта модулей с путями конфигурации.
HTTP слушает только loopback; каждый запрос авторизуется случайным токеном.
"""
import copy
from datetime import datetime
import json
import math
import os
from pathlib import Path
import time

from flask import abort, jsonify, render_template, request, send_from_directory
from shared.paths import app_root, SEEDED_CONFIGS
from visualizer.demo_content import HELP, STEPS


def seed_project(root):
    """Construct fixtures from code, never copy production configuration."""
    if not os.environ.get('KVT_HOME') or root.resolve() != Path(os.environ['KVT_HOME']).resolve():
        raise RuntimeError('Для учебного проекта нужен отдельный KVT_HOME')
    from shared import config_manager as cm
    from shared.notifications import defaults
    from poller.config import DEFAULT_POLLER_CONFIG
    config_dir = root / 'data' / 'config'
    config_dir.mkdir(parents=True, exist_ok=True)
    if (root / 'demo_project.json').exists():
        return
    if (config_dir / 'system_config.json').exists() and not os.environ.get('KVT_DEMO_TOKEN'):
        raise RuntimeError('Учебный сервер нельзя создавать поверх существующей конфигурации')
    # The frozen launcher seeds bundled defaults before importing a service.
    # Replace them here so source and EXE both start the same synthetic project.
    for name in SEEDED_CONFIGS:
        (config_dir / name).unlink(missing_ok=True)
    stamp = datetime.now().isoformat()
    sensors = []
    for sid, name in enumerate(['Склад — температура', 'Помещение А', 'Помещение Б'], 1):
        sensors.append(dict(id=sid, name=name, description='Виртуальный учебный датчик',
                            enabled=True, guarded=True, poll_port_id='default', local_number=sid,
                            modbus_slave_id=16, modbus_addr_temp=30000 + 2 * (sid - 1),
                            modbus_addr_hum=30001 + 2 * (sid - 1),
                            temp_limits=dict(min=18, max=25, warning_delta=1, alarm_delta=3),
                            hum_limits=dict(min=30, max=60, warning_delta=5, alarm_delta=10)))
    cm.save_json(str(config_dir / 'system_config.json'), dict(
        config_version='1.0.0', config_schema_version='1.0', created_at=stamp, updated_at=stamp,
        next_sensor_id=4, update_history=[], sensors=sensors,
        system=dict(name='Учебный склад', location='Учебный объект', description='Демо КВТ', timezone='Europe/Moscow'),
        network=dict(web_host='127.0.0.1', web_port=5000, poller_host='127.0.0.1', poller_port=5001,
                     archiver_host='127.0.0.1', archiver_port=5002)))
    poller = copy.deepcopy(DEFAULT_POLLER_CONFIG)
    poller['auto_start'] = False
    poller['poll_ports'][0]['name'] = 'Учебная линия'
    cm.save_poller_config(poller)
    theme = cm.load_theme_config()
    theme['app_title'] = 'КВТ · Учебный проект'
    cm.save_theme_config(theme)
    cm.save_notifications_config(defaults())
    cm.save_archive_config(dict(
        data_collection=dict(mode='periodic', source_file='./data/current.json', periodic={'interval_ms': 1000},
                             watch={'debounce_ms': 100}, combined={'min_interval_ms': 500, 'max_interval_ms': 5000}),
        storage=dict(json_file=dict(enabled=True, path='./data/archive.json', max_size_mb=100),
                     sqlite=dict(enabled=False, path='./data/archive.db', max_size_mb=100)),
        compression=dict(enabled=True, tolerance_temp=.1, tolerance_hum=.5),
        retention=dict(max_days=365, cleanup_on_low_space=True, min_free_space_mb=200)))
    cm.load_opcua_config()
    cm.load_mqtt_config()
    cm.save_mnemo_tree({'branches': [{'id': 'br1', 'name': 'Учебный склад', 'sensor_ids': [1, 2, 3], 'children': []}], 'show_flat_cards': True})
    cm.save_floorplan_config({'plans': [dict(id='training', name='Учебный склад', description='Разместите датчики',
        parent_id=None, background=None, bg_type=None, canvas_width=1200, canvas_height=800,
        sensors=[dict(sensor_id=1, x=25, y=30), dict(sensor_id=2, x=65, y=30)])]})
    cm.save_json(str(config_dir / 'layout.json'), {})
    from shared.logbook import load_reports_config, load_operators, load_holidays
    load_reports_config()
    load_operators()
    load_holidays()
    cm.atomic_save_json(str(root / 'demo_project.json'), {'type': 'kvt-training', 'created_at': stamp})


class Tutorial:
    def __init__(self, root):
        self.root = root
        self.path = root / 'tutorial.json'
        self.state = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else dict(step=0, completed=[], skipped=[], checkpoints={})
        self.checkpoint(0)

    def save(self):
        from shared.config_manager import atomic_save_json
        atomic_save_json(str(self.path), self.state)

    def checkpoint(self, step):
        checkpoints = self.state['checkpoints']
        if str(step) not in checkpoints:
            directory = self.root / 'data' / 'config'
            checkpoints[str(step)] = {name: json.loads((directory / name).read_text(encoding='utf-8-sig'))
                                      for name in SEEDED_CONFIGS if (directory / name).exists()}
            self.save()

    def public(self):
        return dict(step=self.state['step'], completed=self.state['completed'], skipped=self.state['skipped'], steps=STEPS)

    def complete(self):
        step = self.state['step']
        if step not in self.state['completed']:
            self.state['completed'].append(step)
            self.state['skipped'] = [n for n in self.state['skipped'] if n != step]
            self.save()

    def move(self, action, target=None):
        step = self.state['step']
        if action == 'repeat':
            from shared.config_manager import save_json
            snapshot = self.state['checkpoints'][str(step)]
            for name in SEEDED_CONFIGS:
                destination = self.root / 'data' / 'config' / name
                if name in snapshot:
                    save_json(str(destination), copy.deepcopy(snapshot[name]))
                else:
                    destination.unlink(missing_ok=True)
            self.state['completed'] = [n for n in self.state['completed'] if n < step]
            self.state['skipped'] = [n for n in self.state['skipped'] if n < step]
            self.state['checkpoints'] = {key: value for key, value in self.state['checkpoints'].items() if int(key) <= step}
        elif action in ('next', 'skip'):
            if action == 'next':
                if STEPS[step].get('writes') and step not in self.state['completed']:
                    raise ValueError('Сначала выполните действие и сохраните результат либо нажмите «Пропустить».')
                self.complete()
            elif step not in self.state['completed'] and step not in self.state['skipped']:
                self.state['skipped'].append(step)
            self.state['step'] = min(step + 1, len(STEPS) - 1)
        elif action == 'back':
            self.state['step'] = max(0, step - 1)
        elif action == 'goto':
            target = int(target)
            if not 0 <= target < len(STEPS) or str(target) not in self.state['checkpoints']:
                raise ValueError('Этот шаг ещё не открыт. Пройдите предыдущие шаги.')
            self.state['step'] = target
        else:
            raise ValueError('Неизвестное действие')
        self.checkpoint(self.state['step'])
        self.save()
        return self.public()


def create_demo_app():
    token = os.environ.get('KVT_DEMO_TOKEN')
    if not token or not os.environ.get('KVT_HOME'):
        raise RuntimeError('Демо запускается только через основную систему')
    root = Path(app_root())
    seed_project(root)
    from shared import config_manager as cm
    from visualizer.app import create_app
    from visualizer.routes import api
    app = create_app()
    tutorial = Tutorial(root)
    runtime = dict(last_used=time.monotonic(), last_capture=0, alarm=False, sent=0, poller_running=True, stopping=False)

    def generate_current(force=False):
        if not force and time.monotonic() - runtime['last_capture'] < 2:
            return
        stamp = datetime.now().isoformat()
        sensors = []
        for index, sensor in enumerate(cm.load_system_config().get('sensors', [])):
            alarm = runtime['alarm'] and index == 0
            limits = sensor.get('temp_limits', {})
            temp = float(limits.get('max', 25)) + float(limits.get('alarm_delta', 3)) + 5 if alarm else round(21 + index * .4 + math.sin(time.time() / 15) * .2, 1)
            low, high = float(limits.get('min', 18)), float(limits.get('max', 25))
            warning, alarm_delta = float(limits.get('warning_delta', 1)), float(limits.get('alarm_delta', 3))
            status = 'guarded' if sensor.get('guarded') else 'normal'
            if temp < low - alarm_delta or temp > high + alarm_delta:
                status = 'alarm'
            elif temp < low - warning:
                status = 'warning_low_temp'
            elif temp > high + warning:
                status = 'warning_high_temp'
            sensors.append({**sensor, 'timestamp': stamp, 'combined_status': status,
                'temperature': dict(value=temp, raw=int(temp * 10), status=status, timestamp=stamp),
                'humidity': dict(value=45 + index, raw=(45 + index) * 10, status='normal', timestamp=stamp)})
        current = dict(timestamp=stamp, sensors=sensors, stats={'total': len(sensors)}, demo=True)
        cm.atomic_save_json(str(root / 'data' / 'current.json'), current)
        api.ARCHIVE_SERVICE.capture_current()
        runtime['last_capture'] = time.monotonic()

    def poller_call(method, path, payload=None):
        """Adapter for the existing Poller forms; no sockets or serial ports."""
        cfg = cm.load_poller_config()
        suffix = path.split('?')[0].removeprefix('/api/poller')
        if suffix == '/config' and method == 'POST':
            cm.save_poller_config(payload)
            return {'config': payload}, 200
        if suffix == '/poll-ports' and method == 'GET':
            return {'poll_ports': cfg.get('poll_ports', [])}, 200
        if suffix.startswith('/poll-ports/') and method == 'DELETE':
            port_id = suffix.split('/')[2]
            cfg['poll_ports'] = [p for p in cfg['poll_ports'] if p['id'] != port_id]
            cm.save_poller_config(cfg)
        if suffix.endswith('/log') or suffix == '/log':
            return {'entries': [], 'log': [], 'demo': True}, 200
        if suffix == '/ports':
            return {'ports': ['COM8'], 'demo': True}, 200
        if suffix == '/scan':
            return {'found': [{'slave_id': 16}], 'demo': True}, 200
        if suffix == '/current':
            return cm.load_runtime_json(str(root / 'data' / 'current.json'), {}), 200
        if suffix.endswith('/stop'):
            runtime['poller_running'] = False
        elif suffix.endswith('/start') or suffix.endswith('/restart'):
            runtime['poller_running'] = True
        return dict(state='running' if runtime['poller_running'] else 'stopped', demo=True,
            poll_ports=[{**p, 'state': 'running' if runtime['poller_running'] else 'stopped'} for p in cfg.get('poll_ports', [])],
            total_polls=100, successful_polls=100, failed_polls=0, message='Учебная имитация; оборудование не опрашивается'), 200

    api._poller_call = poller_call
    # Rebase uploaded floorplan images to this project's own directory.
    original_static = app.view_functions['static']

    def demo_static(filename):
        if filename.startswith('floorplans/'):
            return send_from_directory(str(root / 'visualizer' / 'static'), filename)
        return original_static(filename=filename)

    app.view_functions['static'] = demo_static

    @app.context_processor
    def demo_context():
        return dict(demo_mode=True, demo_prefix=request.headers.get('X-KVT-Demo-Prefix', ''), demo_state=tutorial.public())

    @app.before_request
    def boundary():
        if request.headers.get('X-KVT-Demo-Token') != token:
            abort(403)
        runtime['last_used'] = time.monotonic()
        path = request.path
        if path.startswith('/static/'):
            return None
        generate_current()
        if path == '/api/availability/daily':
            return jsonify(date=datetime.now().date().isoformat(), ports={}, sensors={}, demo=True)
        if path == '/api/network/local-ip':
            return jsonify(ip='127.0.0.1', demo=True)
        if path.startswith('/api/mockserver/'):
            return jsonify(reachable=True, process_running=True, url='Учебная имитация', demo=True, status='ok')
        if path in ('/api/notifications/test', '/api/holidays/load-rf'):
            runtime['sent'] += path == '/api/notifications/test'
            return jsonify(ok=True, demo=True, message='Демо: действие имитировано. Внешний запрос не выполнялся.')
        if path == '/api/notifications/status':
            return jsonify(running=True, demo=True, counts={'pending': 0, 'sent': runtime['sent'], 'failed': 0})
        if path in ('/api/opcua/status', '/api/opcua/reload', '/api/mqtt/status', '/api/mqtt/reload'):
            cfg = cm.load_opcua_config() if 'opcua' in path else cm.load_mqtt_config()
            return jsonify(state='running' if cfg.get('enabled') else 'stopped', stale=False,
                           enabled=cfg.get('enabled'), autostart=cfg.get('autostart'), connected=bool(cfg.get('enabled')),
                           updated_at=datetime.now().isoformat(), endpoint='opc.tcp://127.0.0.1:4840/kvt/',
                           broker=cfg.get('broker', {}), message='Учебная имитация: сетевой сервис не запущен', demo=True)
        if path == '/api/config/bundle/import':
            return jsonify(error='Импорт в демо отключён. Используйте учебные формы настройки.'), 403
        if path == '/api/archive/config' and request.method == 'POST':
            patch = request.get_json(silent=True) or {}
            patch.setdefault('storage', {}).setdefault('json_file', {})['path'] = './data/archive.json'
            patch['storage'].setdefault('sqlite', {})['path'] = './data/archive.db'
            patch.setdefault('data_collection', {})['source_file'] = './data/current.json'
            try:
                return jsonify(api.ARCHIVE_SERVICE.save_config(patch))
            except ValueError as exc:
                return jsonify(error=str(exc)), 400
        # Explicitly allow audited local handlers only. New production APIs default to denied.
        endpoint = request.endpoint or ''
        local = ('main.', 'settings.', 'floorplan.', 'journal.', 'export.')
        allowed_api = {
            'api_current', 'api_config', 'api_save_config', 'api_config_bundle_summary', 'api_config_bundle_export',
            'api_sensors', 'api_sensor', 'api_add_sensor', 'api_update_sensor', 'api_delete_sensor',
            'api_poller_config', 'api_save_poller_config', 'api_poller_status', 'api_poller_current', 'api_poller_log',
            'api_poller_ports', 'api_poller_scan', 'api_poller_health', 'api_poller_start', 'api_poller_stop',
            'api_poller_reload', 'api_poller_poll_ports', 'api_delete_poller_poll_port',
            'api_start_poller_poll_port', 'api_stop_poller_poll_port', 'api_restart_poller_poll_port', 'api_poller_poll_port_log',
            'api_network_config', 'api_save_network_config', 'api_opcua_config', 'api_save_opcua_config',
            'api_mqtt_config', 'api_save_mqtt_config', 'api_mqtt_inbound',
            'api_archive_status', 'api_archive_capture', 'api_archive_query', 'api_archive_events', 'api_archive_ack_event',
            'api_archive_temperature_log', 'api_archive_violations', 'api_archive_ack_violation', 'api_archive_cleanup',
            'api_archive_export', 'api_archive_daily', 'api_archive_config', 'api_reports_config', 'api_save_reports_config',
            'api_operators', 'api_save_operators', 'api_holidays', 'api_save_holidays', 'api_logbook_daily',
            'api_logbook_signoff', 'api_logbook_batch_signoff', 'api_archive_sensor', 'api_events', 'api_archive_summary',
            'api_mnemo_tree', 'api_save_mnemo_tree', 'api_theme', 'api_save_theme',
        }
        if endpoint.startswith(local) or endpoint in ('demo_help', 'demo_state', 'demo_move', 'demo_alarm', 'demo_shutdown'):
            return None
        if endpoint.startswith('api.') and endpoint.split('.', 1)[1] in allowed_api:
            return None
        if endpoint.startswith('notifications.') and path == '/api/notifications':
            return None
        return jsonify(error='Это действие недоступно в учебном проекте'), 403

    @app.after_request
    def record_action(response):
        step = STEPS[tutorial.state['step']]
        if request.method != 'GET' and 200 <= response.status_code < 300:
            if any(request.path == pattern or (pattern.endswith('/') and request.path.startswith(pattern)) for pattern in step.get('writes', [])):
                tutorial.complete()
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.route('/help', endpoint='demo_help')
    def help_page():
        return render_template('help.html', help_sections=HELP)

    @app.route('/_demo/state', endpoint='demo_state')
    def state():
        return jsonify(tutorial.public())

    @app.route('/_demo/shutdown', methods=['POST'], endpoint='demo_shutdown')
    def shutdown():
        runtime['stopping'] = True
        return jsonify(ok=True)

    @app.route('/_demo/move', methods=['POST'], endpoint='demo_move')
    def move():
        payload = request.get_json(silent=True) or {}
        try:
            result = tutorial.move(payload.get('action'), payload.get('step'))
        except (ValueError, TypeError) as exc:
            return jsonify(error=str(exc)), 400
        if payload.get('action') == 'repeat':
            runtime['alarm'] = False
            generate_current(force=True)
        return jsonify(result)

    @app.route('/_demo/alarm', methods=['POST'], endpoint='demo_alarm')
    def alarm():
        runtime['alarm'] = not runtime['alarm']
        generate_current(force=True)
        return jsonify(alarm=runtime['alarm'], message='Учебная тревога' if runtime['alarm'] else 'Возврат в норму')

    app.demo_runtime = runtime
    app.demo_tutorial = tutorial
    generate_current(force=True)
    return app


if __name__ == '__main__':
    from werkzeug.serving import make_server
    app = create_demo_app()
    server = make_server('127.0.0.1', 0, app)
    server.timeout = 1
    from shared.config_manager import atomic_save_json
    atomic_save_json(str(Path(app_root()) / 'ready.json'), {'port': server.server_port})
    while not app.demo_runtime['stopping'] and time.monotonic() - app.demo_runtime['last_used'] < 1800:
        server.handle_request()
    server.server_close()
