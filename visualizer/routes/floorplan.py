"""Страница «План помещения» — просмотр планов с размещёнными датчиками."""
import os
import json
import uuid
from flask import Blueprint, render_template, request, jsonify, current_app
from werkzeug.utils import secure_filename
from shared.config_manager import (
    load_floorplan_config, save_floorplan_config, load_system_config
)

floorplan_bp = Blueprint('floorplan', __name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'data')
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static', 'floorplans')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'svg'}


def _ensure_upload_dir():
    os.makedirs(UPLOAD_DIR, exist_ok=True)


def _allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# --- Pages ---

@floorplan_bp.route('/')
def floorplan_index():
    config = load_floorplan_config()
    sensors = load_system_config().get('sensors', [])
    return render_template('floorplan.html', plans=config.get('plans', []), sensors=sensors)


# --- API ---

@floorplan_bp.route('/api/plans', methods=['GET'])
def api_plans():
    config = load_floorplan_config()
    return jsonify(config.get('plans', []))


@floorplan_bp.route('/api/plans', methods=['POST'])
def api_create_plan():
    data = request.get_json()
    if not data or not data.get('name', '').strip():
        return jsonify({'error': 'Название плана обязательно'}), 400

    config = load_floorplan_config()
    plan = {
        'id': str(uuid.uuid4())[:8],
        'name': data['name'].strip(),
        'description': data.get('description', ''),
        'parent_id': data.get('parent_id', None),
        'background': None,
        'bg_type': None,
        'canvas_width': data.get('canvas_width', 1200),
        'canvas_height': data.get('canvas_height', 800),
        'sensors': []
    }
    config['plans'].append(plan)
    save_floorplan_config(config)
    return jsonify(plan), 201


@floorplan_bp.route('/api/plans/<plan_id>', methods=['PUT'])
def api_update_plan(plan_id):
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Нет данных'}), 400

    config = load_floorplan_config()
    for p in config['plans']:
        if p['id'] == plan_id:
            p['name'] = data.get('name', p['name'])
            p['description'] = data.get('description', p['description'])
            p['parent_id'] = data.get('parent_id', p.get('parent_id'))
            p['canvas_width'] = data.get('canvas_width', p.get('canvas_width', 1200))
            p['canvas_height'] = data.get('canvas_height', p.get('canvas_height', 800))
            save_floorplan_config(config)
            return jsonify(p)
    return jsonify({'error': 'План не найден'}), 404


@floorplan_bp.route('/api/plans/<plan_id>', methods=['DELETE'])
def api_delete_plan(plan_id):
    config = load_floorplan_config()
    # Also remove children
    ids_to_remove = {plan_id}
    changed = True
    while changed:
        changed = False
        for p in config['plans']:
            if p.get('parent_id') in ids_to_remove and p['id'] not in ids_to_remove:
                ids_to_remove.add(p['id'])
                changed = True
    config['plans'] = [p for p in config['plans'] if p['id'] not in ids_to_remove]
    save_floorplan_config(config)
    return jsonify({'deleted': list(ids_to_remove)})


@floorplan_bp.route('/api/plans/<plan_id>/background', methods=['POST'])
def api_upload_background(plan_id):
    _ensure_upload_dir()
    config = load_floorplan_config()
    plan = None
    for p in config['plans']:
        if p['id'] == plan_id:
            plan = p
            break
    if not plan:
        return jsonify({'error': 'План не найден'}), 404

    if 'file' not in request.files:
        return jsonify({'error': 'Файл не передан'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'Файл не выбран'}), 400
    if not _allowed_file(file.filename):
        return jsonify({'error': f'Допустимые форматы: {", ".join(ALLOWED_EXTENSIONS)}'}), 400

    ext = file.filename.rsplit('.', 1)[1].lower()
    filename = f'{plan_id}_{uuid.uuid4().hex[:6]}.{ext}'
    filepath = os.path.join(UPLOAD_DIR, filename)
    file.save(filepath)

    # Remove old background file if exists
    if plan.get('background'):
        old_path = os.path.join(UPLOAD_DIR, os.path.basename(plan['background']))
        if os.path.exists(old_path):
            try:
                os.remove(old_path)
            except OSError:
                pass

    plan['background'] = f'/static/floorplans/{filename}'
    plan['bg_type'] = 'svg' if ext == 'svg' else 'image'
    save_floorplan_config(config)
    return jsonify({'background': plan['background'], 'bg_type': plan['bg_type']})


@floorplan_bp.route('/api/plans/<plan_id>/sensors', methods=['POST'])
def api_save_plan_sensors(plan_id):
    """Save sensor placements for a plan."""
    data = request.get_json()
    if data is None:
        return jsonify({'error': 'Нет данных'}), 400

    config = load_floorplan_config()
    for p in config['plans']:
        if p['id'] == plan_id:
            p['sensors'] = data.get('sensors', [])
            save_floorplan_config(config)
            return jsonify(p['sensors'])
    return jsonify({'error': 'План не найден'}), 404
