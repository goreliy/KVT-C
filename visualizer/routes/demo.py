"""Запуск изолированного учебного Visualizer и локальный прокси к нему."""
import atexit
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
import time

import requests
from flask import Blueprint, Response, abort, jsonify, render_template, request, session
from shared.paths import app_root, bundle_root
from visualizer.demo_content import HELP

demo_bp = Blueprint('demo', __name__)
_projects = {}
_lock = threading.RLock()
_http = requests.Session()
_http.trust_env = False


def _stop(project):
    process = project['process']
    if process.poll() is None:
        try:
            if 'url' in project:
                _http.post(project['url'] + '/_demo/shutdown',
                           headers={'X-KVT-Demo-Token': project['token']}, timeout=2)
                process.wait(timeout=5)
                return
        except (requests.RequestException, subprocess.TimeoutExpired):
            pass
        # PyInstaller onefile has a bootloader parent and a Python child.
        # Kill only the tree of the process that this module just launched.
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=10, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        else:
            process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


@atexit.register
def _cleanup():
    for project in list(_projects.values()):
        _stop(project)


def _same_origin():
    origin = request.headers.get('Origin')
    if origin and origin.rstrip('/') != request.host_url.rstrip('/'):
        abort(403)


def _launch(project_id):
    root = Path(app_root()) / 'data' / 'demo_projects' / project_id
    root.mkdir(parents=True, exist_ok=True)
    ready = root / 'ready.json'
    ready.unlink(missing_ok=True)
    token = secrets.token_urlsafe(32)
    env = os.environ.copy()
    env.update(KVT_HOME=str(root), KVT_DEMO_TOKEN=token, PYTHONDONTWRITEBYTECODE='1')
    args = ['--internal-service', 'visualizer.demo_app'] if getattr(sys, 'frozen', False) else ['-B', '-m', 'visualizer.demo_app']
    # Do not inherit a production Flask secret or other service credentials.
    env.pop('KVT_SECRET_KEY', None)
    with (root / 'demo.log').open('ab') as log:
        process = subprocess.Popen([sys.executable, *args], cwd=bundle_root(), env=env,
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    project = {'process': process, 'root': root, 'token': token, 'last_used': time.monotonic()}
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline and process.poll() is None:
        if ready.exists():
            try:
                info = json.loads(ready.read_text(encoding='utf-8'))
                project['url'] = 'http://127.0.0.1:' + str(int(info['port']))
                return project
            except (ValueError, OSError, KeyError):
                pass
        time.sleep(.1)
    _stop(project)
    raise RuntimeError('Не удалось запустить учебный проект. Подробности в data/demo_projects/' + project_id + '/demo.log')


@demo_bp.route('/help')
def help_page():
    return render_template('help.html', help_sections=HELP)


@demo_bp.route('/demo/start', methods=['POST'])
def start():
    _same_origin()
    with _lock:
        for key, project in list(_projects.items()):
            if project['process'].poll() is not None or time.monotonic() - project['last_used'] > 1800:
                _stop(project)
                _projects.pop(key)
        project_id = session.get('demo_project') or secrets.token_hex(16)
        if project_id not in _projects:
            if len(_projects) >= 4:
                return jsonify(error='Уже открыты четыре демопроекта. Завершите один из них.'), 429
            try:
                _projects[project_id] = _launch(project_id)
            except (OSError, RuntimeError) as exc:
                return jsonify(error=str(exc)), 503
        session['demo_project'] = project_id
    return jsonify(url=f'/demo/project/{project_id}/?demo_resume=1')


@demo_bp.route('/demo/end', methods=['POST'])
def end():
    _same_origin()
    with _lock:
        project_id = session.pop('demo_project', None)
        project = _projects.pop(project_id, None)
        if project:
            _stop(project)
    return jsonify(url='/')


@demo_bp.route('/demo/project/<project_id>/', defaults={'path': ''}, methods=['GET', 'POST', 'PUT', 'DELETE'])
@demo_bp.route('/demo/project/<project_id>/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE'])
def proxy(project_id, path):
    if session.get('demo_project') != project_id:
        abort(404)
    if request.method != 'GET':
        _same_origin()
    with _lock:
        project = _projects.get(project_id)
        if not project or project['process'].poll() is not None:
            return render_template('demo_expired.html'), 410
        project['last_used'] = time.monotonic()
    prefix = f'/demo/project/{project_id}'
    try:
        upstream = _http.request(request.method, project['url'] + '/' + path,
                                 params=request.args, data=request.get_data(),
                                 headers={'X-KVT-Demo-Token': project['token'],
                                          'Content-Type': request.content_type or '',
                                          'X-KVT-Demo-Prefix': prefix},
                                 timeout=30, allow_redirects=False)
    except requests.RequestException:
        return jsonify(error='Учебный проект недоступен. Запустите «Демо» снова.'), 503
    content = upstream.content
    content_type = upstream.headers.get('Content-Type', '')
    if 'text/html' in content_type:
        # Rebase static assets and links even before JavaScript has loaded.
        content = re.sub(r'((?:href|src|action)=[\"\'])/(?!/)',
                         lambda match: match.group(1) + prefix + '/', upstream.text).encode('utf-8')
    headers = {name: value for name, value in upstream.headers.items()
               if name.lower() in ('content-type', 'content-disposition', 'location')}
    if headers.get('Location', '').startswith('/'):
        headers['Location'] = prefix + headers['Location']
    headers['Cache-Control'] = 'no-store'
    return Response(content, status=upstream.status_code, headers=headers)
