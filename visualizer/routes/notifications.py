"""Notification settings and explicit test delivery."""
import os
from datetime import datetime

from flask import Blueprint, jsonify, request

from shared import notifications as settings
from shared.config_manager import load_runtime_json, load_system_config
from shared.notification_service import send_email, send_telegram, error_message
from shared.paths import data_dir

notifications_bp = Blueprint('notifications', __name__)


@notifications_bp.route('/notifications', methods=['GET', 'POST'])
def config():
    if request.method == 'GET':
        return jsonify(settings.for_ui())
    try:
        return jsonify(settings.save(request.get_json(silent=True)))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except OSError:
        return jsonify(error='Не удалось сохранить настройки уведомлений'), 500


@notifications_bp.route('/notifications/status')
def status():
    payload = load_runtime_json(os.path.join(data_dir(), 'notifications_status.json'), {'running': False})
    try:
        payload['running'] = (datetime.now() - datetime.fromisoformat(payload['updated_at'])).total_seconds() < 120
    except (KeyError, ValueError, TypeError):
        payload['running'] = False
    return jsonify(payload)


@notifications_bp.route('/notifications/test', methods=['POST'])
def test():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or body.get('channel') not in ('email', 'telegram'):
        return jsonify(error='Укажите канал email или telegram'), 400
    channel = body['channel']
    try:
        config = settings.validate({}, settings.load())
        cfg = config[channel]
        sensor_id = body.get('sensor_id')
        if sensor_id is not None:
            if type(sensor_id) is not int or sensor_id not in {s.get('id') for s in load_system_config().get('sensors', [])}:
                raise ValueError('Датчик не найден')
        recipients = settings.recipients_for(config, channel, sensor_id)
        credentials = settings.secrets()
        if channel == 'email':
            send_email(cfg, credentials.get('smtp_password', ''), 'КВТ: тест уведомлений',
                       'Тестовое сообщение системы КВТ. Настройки почты работают.', recipients)
        else:
            if not recipients:
                raise ValueError('Не заданы Chat IDs')
            for chat in recipients:
                send_telegram(credentials.get('bot_token', ''), chat, 'КВТ: тест уведомлений')
        return jsonify(ok=True, message='Сообщение принято сервером')
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except Exception as exc:
        return jsonify(error=error_message(exc)), 502
