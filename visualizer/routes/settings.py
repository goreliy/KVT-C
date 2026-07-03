"""Страницы настроек системы."""
from flask import Blueprint, render_template
from shared.config_manager import (
    load_system_config, load_poller_config,
    load_opcua_config,
    load_mqtt_config, mqtt_password_set,
    load_archive_config, load_notifications_config,
    load_theme_config
)
from poller.config import normalized_poller_config

settings_bp = Blueprint('settings', __name__)


@settings_bp.route('/')
def settings_index():
    config = load_system_config()
    return render_template('settings/index.html', config=config)


@settings_bp.route('/sensors')
def settings_sensors():
    config = load_system_config()
    poller = normalized_poller_config()
    return render_template('settings/sensors.html', config=config, poller=poller)


@settings_bp.route('/poller')
def settings_poller():
    poller = normalized_poller_config()
    return render_template('settings/poller.html', poller=poller)


@settings_bp.route('/opcua')
def settings_opcua():
    config = load_system_config()
    opcua = load_opcua_config()
    return render_template('settings/opcua.html', config=config, opcua=opcua)


@settings_bp.route('/mqtt')
def settings_mqtt():
    mqtt = load_mqtt_config()
    return render_template('settings/mqtt.html', mqtt=mqtt, mqtt_password_set=mqtt_password_set())


@settings_bp.route('/network')
def settings_network():
    config = load_system_config()
    return render_template('settings/network.html', config=config)


@settings_bp.route('/archive')
def settings_archive():
    archive = load_archive_config()
    return render_template('settings/archive.html', archive=archive)


@settings_bp.route('/reports')
def settings_reports():
    return render_template('settings/reports.html')


@settings_bp.route('/notifications')
def settings_notifications():
    notif = load_notifications_config()
    return render_template('settings/notifications.html', notif=notif)


@settings_bp.route('/appearance')
def settings_appearance():
    theme = load_theme_config()
    return render_template('settings/appearance.html', theme=theme)


@settings_bp.route('/system')
def settings_system():
    config = load_system_config()
    return render_template('settings/system.html', config=config)


@settings_bp.route('/config-transfer')
def settings_config_transfer():
    return render_template('settings/config_transfer.html')
