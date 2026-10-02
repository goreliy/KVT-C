"""Notification configuration. Credentials never belong in public responses."""
import os
import re
import threading

from shared import config_manager as cm

_LOCK = threading.RLock()
SECRET_PATH = os.path.join(cm.CONFIG_DIR, 'notification_secrets.key')
ADDRESS = re.compile(r'^[^\s@,;<>]+@[^\s@,;<>]+$')


def defaults():
    return {
        'email': {
            'enabled': False, 'smtp_server': '', 'smtp_port': 587,
            'security': 'starttls', 'verify_certificate': True, 'ca_cert_path': '',
            'client_cert_path': '', 'client_key_path': '', 'timeout_seconds': 15,
            'smtp_user': '', 'from_address': '', 'recipients': [],
            'on_alarm': True, 'on_warning': True, 'on_recovery': False,
            'daily_report': False, 'daily_report_time': '08:00',
        },
        'telegram': {
            'enabled': False, 'chat_ids': [], 'on_alarm': True,
            'on_warning': True, 'on_recovery': False,
        },
        'per_sensor': {},
        'groups': [],
    }


def public_config(raw):
    config = cm._deep_merge(defaults(), raw)
    config['email'].pop('smtp_password', None)
    config['telegram'].pop('bot_token', None)
    config['email'].pop('password_set', None)
    config['telegram'].pop('token_set', None)
    return config


def load():
    try:
        raw = cm.load_json(cm.NOTIFICATIONS_CONFIG_PATH)
    except FileNotFoundError:
        raw = {}
    return public_config(raw)


def secrets():
    # Legacy JSON credentials are read until the next settings save migrates them.
    try:
        raw = cm.load_json(cm.NOTIFICATIONS_CONFIG_PATH)
    except FileNotFoundError:
        raw = {}
    legacy = {'smtp_password': raw.get('email', {}).get('smtp_password', ''),
              'bot_token': raw.get('telegram', {}).get('bot_token', '')}
    try:
        stored = cm.load_json(SECRET_PATH)
    except FileNotFoundError:
        stored = {}
    return {**legacy, **stored}


def for_ui():
    config = load()
    credentials = secrets()
    config['email']['password_set'] = bool(credentials.get('smtp_password'))
    config['telegram']['token_set'] = bool(credentials.get('bot_token'))
    return config


def validate(patch, current=None):
    if not isinstance(patch, dict):
        raise ValueError('Настройки должны быть JSON-объектом')
    for key in ('email', 'telegram', 'per_sensor'):
        if key in patch and not isinstance(patch[key], dict):
            raise ValueError(f'{key} должен быть объектом')
    current = cm._deep_merge(defaults(), current if current is not None else load())
    if 'replace_routing' in patch and not isinstance(patch['replace_routing'], bool):
        raise ValueError('replace_routing: требуется true или false')
    if patch.get('replace_routing'):
        current['per_sensor'] = {}
        current['groups'] = []
    config = public_config(cm._deep_merge(current, patch))
    config.pop('replace_routing', None)
    email = config['email']
    tg = config['telegram']
    for section, keys in ((email, ('enabled', 'verify_certificate', 'on_alarm', 'on_warning',
                                   'on_recovery', 'daily_report')),
                          (tg, ('enabled', 'on_alarm', 'on_warning', 'on_recovery'))):
        for key in keys:
            if not isinstance(section[key], bool):
                raise ValueError(f'{key}: требуется true или false')
    for key, low, high in (('smtp_port', 1, 65535), ('timeout_seconds', 1, 60)):
        if isinstance(email[key], bool) or (isinstance(email[key], float) and not email[key].is_integer()):
            raise ValueError(f'{key}: требуется целое число')
        try:
            email[key] = int(email[key])
        except (ValueError, TypeError):
            raise ValueError(f'{key}: требуется целое число')
        if not low <= email[key] <= high:
            raise ValueError(f'{key}: допустимо {low}–{high}')
    if email['security'] not in ('none', 'starttls', 'tls'):
        raise ValueError('Режим SMTP: none, starttls или tls')
    for key in ('smtp_server', 'smtp_user', 'from_address', 'ca_cert_path',
                'client_cert_path', 'client_key_path'):
        if not isinstance(email[key], str) or any(c in email[key] for c in '\r\n\x00'):
            raise ValueError(f'{key}: некорректная строка')
        email[key] = email[key].strip()
    if bool(email['client_cert_path']) != bool(email['client_key_path']):
        raise ValueError('Клиентский сертификат и ключ нужно указать вместе')
    if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', str(email['daily_report_time'])):
        raise ValueError('Время отчёта: ЧЧ:ММ')
    for section, key in ((email, 'recipients'), (tg, 'chat_ids')):
        values = section[key]
        if not isinstance(values, list) or len(values) > 100:
            raise ValueError(f'{key}: требуется список до 100 получателей')
        section[key] = list(dict.fromkeys(str(v).strip() for v in values if str(v).strip()))
    if email['from_address'] and not ADDRESS.fullmatch(email['from_address']):
        raise ValueError('Некорректный адрес отправителя')
    if any(not ADDRESS.fullmatch(v) for v in email['recipients']):
        raise ValueError('Некорректный email получателя')
    if email['enabled'] and (not email['smtp_server'] or not email['from_address']):
        raise ValueError('Для почты нужны сервер и отправитель')
    for sensor_id, flags in config['per_sensor'].items():
        if not str(sensor_id).isdigit() or int(sensor_id) < 1 or not isinstance(flags, dict):
            raise ValueError('per_sensor: нужны ID датчиков и объекты настроек')
        for flag, enabled in flags.items():
            if flag in ('email_recipients', 'telegram_chat_ids'):
                flags[flag] = _validate_targets(flag, enabled)
                continue
            if flag not in {f'{channel}_on_{event}' for channel in ('email', 'telegram')
                            for event in ('alarm', 'warning', 'recovery')} or not isinstance(enabled, bool):
                raise ValueError('per_sensor: некорректный флаг уведомлений')
    groups = config['groups']
    if not isinstance(groups, list) or len(groups) > 100:
        raise ValueError('groups: требуется список до 100 групп')
    seen = set()
    for group in groups:
        if not isinstance(group, dict) or set(group) - {'id', 'name', 'sensor_ids', 'email_recipients', 'telegram_chat_ids'}:
            raise ValueError('Некорректные настройки группы уведомлений')
        group_id = group.get('id')
        if not isinstance(group_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', group_id) or group_id in seen:
            raise ValueError('Группа должна иметь уникальный ID (буквы, цифры, дефис, подчёркивание)')
        seen.add(group_id)
        name = group.get('name')
        if not isinstance(name, str) or not name.strip() or len(name) > 120 or any(c in name for c in '\r\n\x00'):
            raise ValueError('Укажите имя группы (до 120 символов)')
        group['name'] = name.strip()
        ids = group.get('sensor_ids')
        if not isinstance(ids, list) or len(ids) > 256 or any(type(sid) is not int or sid < 1 for sid in ids):
            raise ValueError('sensor_ids: требуется список положительных ID датчиков')
        group['sensor_ids'] = list(dict.fromkeys(ids))
        for key in ('email_recipients', 'telegram_chat_ids'):
            if key in group:
                group[key] = _validate_targets(key, group[key])
    for channel, field, clear in (('email', 'smtp_password', 'clear_password'),
                                  ('telegram', 'bot_token', 'clear_token')):
        section = patch.get(channel, {})
        if field in section and not isinstance(section[field], str):
            raise ValueError(f'{field}: требуется строка')
        if clear in section and not isinstance(section[clear], bool):
            raise ValueError(f'{clear}: требуется true или false')
        config[channel].pop(clear, None)
    return config


def _validate_targets(key, values):
    if not isinstance(values, list) or len(values) > 100 or any(not isinstance(v, str) for v in values):
        raise ValueError(f'{key}: требуется список до 100 адресов')
    values = list(dict.fromkeys(v.strip() for v in values if v.strip()))
    if key == 'email_recipients' and any(not ADDRESS.fullmatch(v) for v in values):
        raise ValueError('Некорректный email получателя датчика или группы')
    if key == 'telegram_chat_ids' and any(not re.fullmatch(r'-?\d+|@[A-Za-z0-9_]+', v) for v in values):
        raise ValueError('Некорректный Telegram Chat ID датчика или группы')
    return values


def recipients_for(config, channel, sensor_id=None):
    """Explicit sensor targets override the union of groups, then global fallback.

    Missing key means inheritance; an empty list explicitly disables delivery.
    """
    global_key = 'recipients' if channel == 'email' else 'chat_ids'
    if sensor_id is None:
        return list(config[channel][global_key])
    key = 'email_recipients' if channel == 'email' else 'telegram_chat_ids'
    sensor = config.get('per_sensor', {}).get(str(sensor_id), {})
    if key in sensor:
        return list(sensor[key])
    matched = [g for g in config.get('groups', []) if int(sensor_id) in g['sensor_ids'] and key in g]
    if matched:
        return list(dict.fromkeys(target for group in matched for target in group[key]))
    return list(config[channel][global_key])


def save(patch):
    with _LOCK:
        config = validate(patch)
        credentials = secrets()
        for channel, field, clear in (('email', 'smtp_password', 'clear_password'),
                                      ('telegram', 'bot_token', 'clear_token')):
            section = patch.get(channel, {})
            if section.get(clear):
                credentials[field] = ''
            elif section.get(field):
                credentials[field] = section[field]
        if config['telegram']['enabled'] and not credentials.get('bot_token'):
            raise ValueError('Для Telegram нужен Bot Token')
        if credentials.get('bot_token') and not re.fullmatch(r'\d+:[A-Za-z0-9_-]+', credentials['bot_token']):
            raise ValueError('Некорректный Bot Token')
        cm.atomic_save_json(SECRET_PATH, credentials)
        try:
            os.chmod(SECRET_PATH, 0o600)
        except OSError:
            pass
        cm.save_json(cm.NOTIFICATIONS_CONFIG_PATH, config)
        return for_ui()
