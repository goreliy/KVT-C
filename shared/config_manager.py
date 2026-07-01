"""
Менеджер конфигурации системы КВТ.
Чтение, запись, валидация и версионирование system_config.json.
"""
import json
import os
import shutil
import threading
from datetime import datetime
from copy import deepcopy

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'config')
SYSTEM_CONFIG_PATH = os.path.join(CONFIG_DIR, 'system_config.json')
POLLER_CONFIG_PATH = os.path.join(CONFIG_DIR, 'poller_config.json')
ARCHIVE_CONFIG_PATH = os.path.join(CONFIG_DIR, 'archive_config.json')
OPCUA_CONFIG_PATH = os.path.join(CONFIG_DIR, 'opcua_config.json')
NOTIFICATIONS_CONFIG_PATH = os.path.join(CONFIG_DIR, 'notifications.json')
LAYOUT_CONFIG_PATH = os.path.join(CONFIG_DIR, 'layout.json')
THEME_CONFIG_PATH = os.path.join(CONFIG_DIR, 'theme_config.json')
FLOORPLAN_CONFIG_PATH = os.path.join(CONFIG_DIR, 'floorplan_config.json')
MNEMO_TREE_CONFIG_PATH = os.path.join(CONFIG_DIR, 'mnemo_tree.json')
BACKUP_DIR = os.path.join(CONFIG_DIR, 'backups')


def _ensure_dirs():
    os.makedirs(CONFIG_DIR, exist_ok=True)
    os.makedirs(BACKUP_DIR, exist_ok=True)


def load_json(path):
    with open(path, 'r', encoding='utf-8-sig') as f:
        return json.load(f)


def load_runtime_json(path, default=None):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {} if default is None else deepcopy(default)


def atomic_save_json(path, data, encoding='utf-8', indent=2):
    _ensure_dirs()
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    basename = os.path.basename(path)
    tmp = os.path.join(directory, f'.{basename}.{os.getpid()}.{threading.get_ident()}.tmp')
    with open(tmp, 'w', encoding=encoding) as f:
        json.dump(data, f, ensure_ascii=False, indent=indent, separators=(',', ':') if indent is None else None)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    os.replace(tmp, path)


def save_json(path, data):
    atomic_save_json(path, data, encoding='utf-8-sig', indent=2)


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


def _default_opcua_config():
    return {
        "enabled": False,
        "server": {
            "host": "0.0.0.0",
            "port": 4840,
            "endpoint_path": "/kvt/",
            "server_name": "KVT-C OPC UA Server",
            "namespace_uri": "urn:kvt:c:monitoring",
            "namespace_name": "KVT-C",
        },
        "publishing": {
            "update_interval_ms": 1000,
            "stale_after_ms": 30000,
            "publish_only_enabled_sensors": True,
        },
        "selection": {
            "sensor_ids": [],
        },
        "fields": {
            "temperature": True,
            "humidity": True,
            "combined_status": True,
            "timestamp": True,
            "poll_port_metadata": True,
            "limits": True,
        },
        "security": {
            "mode": "anonymous_readonly",
            "security_policies": ["None"],
            "certificate_path": "",
            "private_key_path": "",
            "users": [],
        },
        "historical_access": {
            "enabled": False,
            "source": "archive_manager",
            "max_values_per_read": 5000,
        },
    }


def _deep_merge(base, patch):
    result = deepcopy(base)
    if not isinstance(patch, dict):
        return result
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "да"}
    return bool(value)


def _as_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_opcua_config(payload, validate_sensor_ids=False):
    config = _deep_merge(_default_opcua_config(), payload or {})
    errors = []

    config["enabled"] = _as_bool(config.get("enabled"))

    server = config.setdefault("server", {})
    server["host"] = str(server.get("host") or "0.0.0.0").strip() or "0.0.0.0"
    server["port"] = _as_int(server.get("port"), 4840)
    if not 1 <= server["port"] <= 65535:
        errors.append("OPC UA port должен быть от 1 до 65535")

    endpoint_path = str(server.get("endpoint_path") or "/kvt/").strip() or "/kvt/"
    if not endpoint_path.startswith("/"):
        endpoint_path = "/" + endpoint_path
    if not endpoint_path.endswith("/"):
        endpoint_path += "/"
    if " " in endpoint_path:
        errors.append("OPC UA endpoint_path не должен содержать пробелы")
    server["endpoint_path"] = endpoint_path
    server["server_name"] = str(server.get("server_name") or "KVT-C OPC UA Server").strip() or "KVT-C OPC UA Server"
    server["namespace_uri"] = str(server.get("namespace_uri") or "urn:kvt:c:monitoring").strip() or "urn:kvt:c:monitoring"
    server["namespace_name"] = str(server.get("namespace_name") or "KVT-C").strip() or "KVT-C"

    publishing = config.setdefault("publishing", {})
    publishing["update_interval_ms"] = _as_int(publishing.get("update_interval_ms"), 1000)
    publishing["stale_after_ms"] = _as_int(publishing.get("stale_after_ms"), 30000)
    if not 250 <= publishing["update_interval_ms"] <= 60000:
        errors.append("OPC UA update_interval_ms должен быть от 250 до 60000")
    if not 1000 <= publishing["stale_after_ms"] <= 3600000:
        errors.append("OPC UA stale_after_ms должен быть от 1000 до 3600000")
    publishing["publish_only_enabled_sensors"] = _as_bool(publishing.get("publish_only_enabled_sensors"))

    selection = config.setdefault("selection", {})
    sensor_ids = []
    seen = set()
    for raw_id in selection.get("sensor_ids") or []:
        try:
            sensor_id = int(raw_id)
        except (TypeError, ValueError):
            errors.append(f"Некорректный ID датчика для OPC UA: {raw_id}")
            continue
        if sensor_id not in seen:
            seen.add(sensor_id)
            sensor_ids.append(sensor_id)
    if validate_sensor_ids and sensor_ids:
        valid_ids = {int(s.get("id")) for s in load_system_config().get("sensors", []) if s.get("id") is not None}
        missing = [sid for sid in sensor_ids if sid not in valid_ids]
        if missing:
            errors.append("OPC UA sensor_ids отсутствуют в конфигурации датчиков: " + ", ".join(map(str, missing)))
    selection["sensor_ids"] = sensor_ids

    defaults = _default_opcua_config()["fields"]
    fields = config.setdefault("fields", {})
    for key, default in defaults.items():
        fields[key] = _as_bool(fields.get(key, default))
    for key in list(fields.keys()):
        if key not in defaults:
            fields.pop(key, None)

    security = config.setdefault("security", {})
    mode = str(security.get("mode") or "anonymous_readonly").strip()
    if mode not in {"anonymous_readonly", "certificate", "user_password"}:
        errors.append("OPC UA security.mode должен быть anonymous_readonly, certificate или user_password")
        mode = "anonymous_readonly"
    security["mode"] = mode
    policies = security.get("security_policies")
    if not isinstance(policies, list) or not policies:
        policies = ["None"]
    security["security_policies"] = [str(item).strip() for item in policies if str(item).strip()] or ["None"]
    security["certificate_path"] = str(security.get("certificate_path") or "").strip()
    security["private_key_path"] = str(security.get("private_key_path") or "").strip()
    users = security.get("users")
    security["users"] = users if isinstance(users, list) else []
    if mode == "certificate" and (not security["certificate_path"] or not security["private_key_path"]):
        errors.append("Для OPC UA security.mode=certificate нужны certificate_path и private_key_path")
    if mode == "user_password" and not security["users"]:
        errors.append("Для OPC UA security.mode=user_password нужен хотя бы один пользователь")

    ha = config.setdefault("historical_access", {})
    ha["enabled"] = _as_bool(ha.get("enabled"))
    ha_source = str(ha.get("source") or "archive_manager").strip()
    if ha_source not in {"archive_manager"}:
        errors.append("OPC UA historical_access.source должен быть archive_manager")
        ha_source = "archive_manager"
    ha["source"] = ha_source
    ha["max_values_per_read"] = _as_int(ha.get("max_values_per_read"), 5000)
    if not 100 <= ha["max_values_per_read"] <= 1000000:
        errors.append("OPC UA historical_access.max_values_per_read должен быть от 100 до 1000000")

    return config, errors


def load_opcua_config():
    try:
        data = load_json(OPCUA_CONFIG_PATH)
    except (FileNotFoundError, json.JSONDecodeError):
        data = _default_opcua_config()
        save_json(OPCUA_CONFIG_PATH, data)
        return data
    config, _errors = _coerce_opcua_config(data, validate_sensor_ids=False)
    return config


def validated_opcua_config_patch(patch, current=None):
    current = current if current is not None else load_opcua_config()
    return _coerce_opcua_config(_deep_merge(current, patch or {}), validate_sensor_ids=True)


def save_opcua_config(config):
    save_json(OPCUA_CONFIG_PATH, config)
    return config


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


# --- Mnemoscheme tree (группировка датчиков по веткам) ---

def _default_mnemo_tree():
    return {"branches": [], "show_flat_cards": True}


def _next_branch_id(existing_ids):
    n = 1
    while f"br{n}" in existing_ids:
        n += 1
    return f"br{n}"


def _sanitize_branches(raw, existing_ids, depth=0):
    """Рекурсивная очистка/валидация веток дерева мнемосхемы."""
    result = []
    if not isinstance(raw, list) or depth > 8:
        return result
    for item in raw:
        if not isinstance(item, dict):
            continue
        branch_id = str(item.get('id') or '').strip()
        if not branch_id or branch_id in existing_ids:
            branch_id = _next_branch_id(existing_ids)
        existing_ids.add(branch_id)
        name = str(item.get('name') or '').strip() or 'Ветка'
        sensor_ids = []
        seen = set()
        for sid in (item.get('sensor_ids') or []):
            try:
                sid_int = int(sid)
            except (TypeError, ValueError):
                continue
            if sid_int not in seen:
                seen.add(sid_int)
                sensor_ids.append(sid_int)
        result.append({
            'id': branch_id,
            'name': name[:80],
            'sensor_ids': sensor_ids,
            'children': _sanitize_branches(item.get('children'), existing_ids, depth + 1),
        })
    return result


def load_mnemo_tree():
    try:
        data = load_json(MNEMO_TREE_CONFIG_PATH)
    except (FileNotFoundError, json.JSONDecodeError):
        data = _default_mnemo_tree()
        save_json(MNEMO_TREE_CONFIG_PATH, data)
        return data
    data.setdefault('branches', [])
    data.setdefault('show_flat_cards', True)
    return data


def save_mnemo_tree(config):
    config = config or {}
    branches = _sanitize_branches(config.get('branches'), set())
    payload = {
        'branches': branches,
        'show_flat_cards': bool(config.get('show_flat_cards', True)),
        'updated_at': datetime.now().isoformat(),
    }
    save_json(MNEMO_TREE_CONFIG_PATH, payload)
    return payload


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
    sensor.setdefault('poll_port_id', 'default')
    try:
        sensor['local_number'] = int(sensor.get('local_number') or sensor.get('id') or 0)
    except (TypeError, ValueError):
        errors.append('Номер датчика внутри линии должен быть числом')
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
            same_port = str(es.get('poll_port_id') or 'default') == str(sensor.get('poll_port_id') or 'default')
            if same_port and es.get('modbus_addr_temp') == addr_t:
                errors.append(f'Адрес Modbus {addr_t} уже используется датчиком "{es["name"]}" на этой линии опроса')
            try:
                same_local_number = int(es.get('local_number')) == int(sensor.get('local_number'))
            except (TypeError, ValueError):
                same_local_number = False
            if same_port and es.get('local_number') and sensor.get('local_number') and same_local_number:
                errors.append(f'Номер {sensor["local_number"]} уже используется датчиком "{es["name"]}" на этой линии опроса')

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
    sensor_data.setdefault('poll_port_id', 'default')
    sensor_data.setdefault('local_number', sensor_data['id'])
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
