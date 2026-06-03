"""
Менеджер конфигурации системы КВТ.
Чтение, запись, валидация и версионирование system_config.json.
"""
import json
import os
import shutil
from datetime import datetime
from copy import deepcopy

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'config')
SYSTEM_CONFIG_PATH = os.path.join(CONFIG_DIR, 'system_config.json')
POLLER_CONFIG_PATH = os.path.join(CONFIG_DIR, 'poller_config.json')
ARCHIVE_CONFIG_PATH = os.path.join(CONFIG_DIR, 'archive_config.json')
NOTIFICATIONS_CONFIG_PATH = os.path.join(CONFIG_DIR, 'notifications.json')
LAYOUT_CONFIG_PATH = os.path.join(CONFIG_DIR, 'layout.json')
THEME_CONFIG_PATH = os.path.join(CONFIG_DIR, 'theme_config.json')
FLOORPLAN_CONFIG_PATH = os.path.join(CONFIG_DIR, 'floorplan_config.json')
BACKUP_DIR = os.path.join(CONFIG_DIR, 'backups')


def _ensure_dirs():
    os.makedirs(CONFIG_DIR, exist_ok=True)
    os.makedirs(BACKUP_DIR, exist_ok=True)


def load_json(path):
    with open(path, 'r', encoding='utf-8-sig') as f:
        return json.load(f)


def save_json(path, data):
    _ensure_dirs()
    with open(path, 'w', encoding='utf-8-sig') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_system_config():
    return load_json(SYSTEM_CONFIG_PATH)


def save_system_config(config, change_description=""):
    _ensure_dirs()
    # Bump version
    old_version = config.get('config_version', '1.0.0')
    parts = old_version.split('.')
    parts[-1] = str(int(parts[-1]) + 1)
    new_version = '.'.join(parts)
    config['config_version'] = new_version
    config['updated_at'] = datetime.now().isoformat()

    # Add to history
    if 'update_history' not in config:
        config['update_history'] = []
    config['update_history'].append({
        'version': new_version,
        'timestamp': config['updated_at'],
        'changes': change_description
    })

    # Backup
    backup_path = os.path.join(BACKUP_DIR, f'system_config_{new_version}.json')
    save_json(backup_path, config)

    # Save main
    save_json(SYSTEM_CONFIG_PATH, config)
    return config


def load_poller_config():
    return load_json(POLLER_CONFIG_PATH)


def save_poller_config(config):
    save_json(POLLER_CONFIG_PATH, config)


def load_archive_config():
    return load_json(ARCHIVE_CONFIG_PATH)


def save_archive_config(config):
    save_json(ARCHIVE_CONFIG_PATH, config)


def load_notifications_config():
    return load_json(NOTIFICATIONS_CONFIG_PATH)


def save_notifications_config(config):
    save_json(NOTIFICATIONS_CONFIG_PATH, config)


def load_layout_config():
    return load_json(LAYOUT_CONFIG_PATH)


def save_layout_config(config):
    save_json(LAYOUT_CONFIG_PATH, config)


def _default_theme_config():
    return {
        "theme": "dark",
        "app_title": "КВТ Мониторинг",
        "colors": {
            "dark": {
                "bg_dark": "#1a1a2e",
                "bg_card": "#16213e",
                "bg_input": "#0f3460",
                "text_primary": "#e0e0e0",
                "text_secondary": "#a0a0a0",
                "border_color": "#2a2a4a",
                "color_normal": "#4CAF50",
                "color_guarded": "#2196F3",
                "color_warning": "#FF9800",
                "color_alarm": "#F44336",
                "color_offline": "#9E9E9E",
                "navbar_bg": "#16213e",
                "navbar_brand_color": "#2196F3"
            },
            "light": {
                "bg_dark": "#f0f2f5",
                "bg_card": "#ffffff",
                "bg_input": "#f5f7fa",
                "text_primary": "#1a1a2e",
                "text_secondary": "#6b7280",
                "border_color": "#d1d5db",
                "color_normal": "#16a34a",
                "color_guarded": "#2563eb",
                "color_warning": "#ea580c",
                "color_alarm": "#dc2626",
                "color_offline": "#9ca3af",
                "navbar_bg": "#ffffff",
                "navbar_brand_color": "#2563eb"
            }
        }
    }


def load_theme_config():
    try:
        return load_json(THEME_CONFIG_PATH)
    except (FileNotFoundError, json.JSONDecodeError):
        default = _default_theme_config()
        save_json(THEME_CONFIG_PATH, default)
        return default


def save_theme_config(config):
    save_json(THEME_CONFIG_PATH, config)
    return config


# --- Floor Plan Config ---

def _default_floorplan_config():
    return {
        "plans": []
    }


def load_floorplan_config():
    try:
        return load_json(FLOORPLAN_CONFIG_PATH)
    except (FileNotFoundError, json.JSONDecodeError):
        default = _default_floorplan_config()
        save_json(FLOORPLAN_CONFIG_PATH, default)
        return default


def save_floorplan_config(config):
    save_json(FLOORPLAN_CONFIG_PATH, config)
    return config


# --- Sensor CRUD ---

def get_sensors(config=None):
    if config is None:
        config = load_system_config()
    return config.get('sensors', [])


def get_sensor_by_id(sensor_id, config=None):
    for s in get_sensors(config):
        if s['id'] == sensor_id:
            return s
    return None


def validate_sensor(sensor, existing_sensors=None, exclude_id=None):
    """Валидация данных датчика. Возвращает список ошибок."""
    errors = []
    if not sensor.get('name', '').strip():
        errors.append('Имя датчика обязательно')
    slave_id = sensor.get('modbus_slave_id')
    if slave_id is None or not (1 <= slave_id <= 247):
        errors.append('Modbus Slave ID должен быть от 1 до 247')
    addr_t = sensor.get('modbus_addr_temp')
    addr_h = sensor.get('modbus_addr_hum')
    if addr_t is None or addr_h is None:
        errors.append('Адреса Modbus обязательны')
    elif addr_h != addr_t + 1:
        errors.append('Адрес влажности должен быть = адрес температуры + 1')

    # Check uniqueness
    if existing_sensors:
        for es in existing_sensors:
            if exclude_id and es['id'] == exclude_id:
                continue
            if es.get('modbus_addr_temp') == addr_t:
                errors.append(f'Адрес Modbus {addr_t} уже используется датчиком "{es["name"]}"')

    # Limits validation
    for key in ('temp_limits', 'hum_limits'):
        lim = sensor.get(key, {})
        if lim.get('min') is not None and lim.get('max') is not None:
            if lim['min'] >= lim['max']:
                errors.append(f'{key}: min должен быть меньше max')

    return errors


def add_sensor(sensor_data, config=None):
    if config is None:
        config = load_system_config()
    sensor_data['id'] = config.get('next_sensor_id', 1)
    sensor_data['created_at'] = datetime.now().isoformat()
    sensor_data.setdefault('enabled', True)
    sensor_data.setdefault('guarded', True)
    sensor_data.setdefault('notifications', {
        'email_on_warning': True, 'email_on_alarm': True, 'telegram_on_alarm': True
    })

    errors = validate_sensor(sensor_data, config.get('sensors', []))
    if errors:
        return None, errors

    config['sensors'].append(sensor_data)
    config['next_sensor_id'] = sensor_data['id'] + 1
    save_system_config(config, f'Добавлен датчик "{sensor_data["name"]}"')
    return sensor_data, []


def update_sensor(sensor_id, updates, config=None):
    if config is None:
        config = load_system_config()
    for i, s in enumerate(config['sensors']):
        if s['id'] == sensor_id:
            merged = {**s, **updates}
            merged['id'] = sensor_id  # protect id
            errors = validate_sensor(merged, config['sensors'], exclude_id=sensor_id)
            if errors:
                return None, errors
            config['sensors'][i] = merged
            save_system_config(config, f'Обновлён датчик "{merged["name"]}"')
            return merged, []
    return None, ['Датчик не найден']


def delete_sensor(sensor_id, config=None):
    if config is None:
        config = load_system_config()
    for i, s in enumerate(config['sensors']):
        if s['id'] == sensor_id:
            removed = config['sensors'].pop(i)
            save_system_config(config, f'Удалён датчик "{removed["name"]}"')
            return removed, []
    return None, ['Датчик не найден']
