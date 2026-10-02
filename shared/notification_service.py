"""SMTP/Telegram delivery and durable transition outbox owned by the archiver."""
import math
import os
import smtplib
import sqlite3
import ssl
import threading
import time
from contextlib import closing
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

import requests

from shared import notifications as settings
from shared.config_manager import load_system_config, load_runtime_json, atomic_save_json
from shared.paths import data_dir, app_root


def _certificate_path(path):
    return path if os.path.isabs(path) else os.path.join(app_root(), path)


def tls_context(config):
    ca = config.get('ca_cert_path')
    context = ssl.create_default_context(cafile=_certificate_path(ca) if ca else None)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    if not config['verify_certificate']:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    if config.get('client_cert_path'):
        context.load_cert_chain(_certificate_path(config['client_cert_path']),
                                _certificate_path(config['client_key_path']))
    return context


def send_email(config, password, subject, body, recipients=None, message_id=None):
    recipients = config['recipients'] if recipients is None else recipients
    if not config['smtp_server'] or not config['from_address'] or not recipients:
        raise ValueError('Нужны SMTP сервер, отправитель и получатели')
    message = EmailMessage()
    message['From'] = config['from_address']
    message['To'] = ', '.join(recipients)
    message['Subject'] = subject
    message['Date'] = formatdate(localtime=True)
    message['Message-ID'] = message_id or make_msgid(domain='kvt.local')
    message.set_content(body)
    mode = config['security']
    if mode not in ('none', 'starttls', 'tls'):
        raise ValueError('Неизвестный режим SMTP')
    kwargs = {'timeout': config['timeout_seconds']}
    if mode == 'tls':
        kwargs['context'] = tls_context(config)
    smtp_class = smtplib.SMTP_SSL if mode == 'tls' else smtplib.SMTP
    with smtp_class(config['smtp_server'], config['smtp_port'], **kwargs) as smtp:
        smtp.ehlo_or_helo_if_needed()
        if mode == 'starttls':
            smtp.starttls(context=tls_context(config))
            smtp.ehlo()
        if config['smtp_user']:
            smtp.login(config['smtp_user'], password)
        refused = smtp.send_message(message, from_addr=config['from_address'], to_addrs=recipients)
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)


def send_telegram(token, chat_id, body):
    if not token:
        raise ValueError('Bot Token не задан')
    response = requests.post(f'https://api.telegram.org/bot{token}/sendMessage',
                             json={'chat_id': chat_id, 'text': body[:4096]}, timeout=15)
    if not response.ok or not response.json().get('ok'):
        # Do not expose URLs containing the bot token.
        raise RuntimeError('Telegram отклонил сообщение')


def error_message(exc):
    if isinstance(exc, ssl.SSLCertVerificationError):
        return 'Не удалось проверить сертификат SMTP сервера'
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return 'SMTP: неверные учётные данные или запрещённый способ входа'
    if isinstance(exc, smtplib.SMTPNotSupportedError):
        return 'SMTP сервер не поддерживает выбранный TLS или авторизацию'
    if isinstance(exc, smtplib.SMTPResponseException):
        return f'SMTP отклонил запрос (код {exc.smtp_code})'
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return 'SMTP отклонил получателя'
    if isinstance(exc, (TimeoutError, requests.Timeout)):
        return 'Истекло время ожидания сервера'
    return f'Ошибка отправки ({type(exc).__name__}); проверьте соединение и настройки'


def _level(value, limits):
    try:
        value = float(value)
        if not math.isfinite(value):
            return None
        for severity, delta_key in (('alarm', 'alarm_delta'), ('warning', 'warning_delta')):
            delta = float(limits.get(delta_key) or 0)
            if limits.get('max') is not None and value > float(limits['max']) + delta:
                return severity + '_high'
            if limits.get('min') is not None and value < float(limits['min']) - delta:
                return severity + '_low'
    except (TypeError, ValueError):
        return None
    return 'normal'


class NotificationService:
    def __init__(self, root=None):
        self.root = root or data_dir()
        self.db_path = os.path.join(self.root, 'notifications.db')
        self.status_path = os.path.join(self.root, 'notifications_status.json')
        self._stop = threading.Event()
        self._thread = None

    def _connect(self):
        os.makedirs(self.root, exist_ok=True)
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.executescript('''
            CREATE TABLE IF NOT EXISTS states (key TEXT PRIMARY KEY, level TEXT);
            CREATE TABLE IF NOT EXISTS outbox (
                id INTEGER PRIMARY KEY, channel TEXT, recipient TEXT,
                subject TEXT, body TEXT, message_id TEXT, attempts INTEGER DEFAULT 0,
                next_try REAL DEFAULT 0, status TEXT DEFAULT 'pending', error TEXT,
                created_at TEXT, sent_at TEXT);
        ''')
        if 'sensor_id' not in {row['name'] for row in db.execute('PRAGMA table_info(outbox)')}:
            db.execute('ALTER TABLE outbox ADD COLUMN sensor_id INTEGER')
        return db

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name='Notifications', daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:
                atomic_save_json(self.status_path, {'running': True, 'updated_at': datetime.now().isoformat(),
                                                     'error': error_message(exc)})
            self._stop.wait(2)

    @staticmethod
    def _enqueue(db, channel, config, subject, body, sensor_id=None):
        recipients = config['recipients' if channel == 'email' else 'chat_ids']
        for recipient in recipients:
            db.execute('''INSERT INTO outbox(channel,recipient,subject,body,message_id,created_at,sensor_id)
                          VALUES (?,?,?,?,?,?,?)''',
                       (channel, recipient, subject, body, make_msgid(domain='kvt.local'),
                        datetime.now().isoformat(), sensor_id))

    def collect(self, db, config, current, sensors):
        known = {str(s['id']): s for s in sensors if s.get('id') is not None}
        for reading in current.get('sensors') or []:
            sensor = known.get(str(reading.get('id')))
            if not sensor or not sensor.get('enabled', True) or not sensor.get('guarded', True):
                continue
            if reading.get('combined_status') in ('no_connection', 'offline'):
                continue
            for parameter, label, unit in (('temperature', 'Температура', '°C'), ('humidity', 'Влажность', '%')):
                measurement = reading.get(parameter) or {}
                # Never notify on a stale sample, including after a poller shutdown.
                stamp = measurement.get('timestamp') or current.get('timestamp')
                try:
                    sample_time = datetime.fromisoformat(str(stamp).replace('Z', '+00:00'))
                    if sample_time.tzinfo is None:
                        sample_time = sample_time.astimezone()
                    age = (datetime.now(timezone.utc) - sample_time).total_seconds()
                    if age > 120 or age < -60:
                        continue
                except (ValueError, TypeError):
                    continue
                limits = sensor.get('temp_limits' if parameter == 'temperature' else 'hum_limits')
                if limits is None:
                    limits = (sensor.get('limits') or {}).get(parameter) or {}
                level = _level(measurement.get('value'), limits)
                if level is None:
                    continue
                for channel in ('email', 'telegram'):
                    cfg = config[channel]
                    key = f'{channel}:{sensor["id"]}:{parameter}'
                    old = db.execute('SELECT level FROM states WHERE key=?', (key,)).fetchone()
                    previous = old['level'] if old else 'normal'
                    if not cfg['enabled']:
                        db.execute('DELETE FROM states WHERE key=?', (key,))
                        continue
                    if previous == level:
                        continue
                    db.execute('INSERT OR REPLACE INTO states VALUES (?,?)', (key, level))
                    event = 'recovery' if level == 'normal' else level.split('_')[0]
                    flags = {**sensor.get('notifications', {}), **config['per_sensor'].get(str(sensor['id']), {})}
                    if not cfg.get('on_' + event) or not flags.get(f'{channel}_on_{event}', True):
                        continue
                    name = sensor.get('name') or str(sensor['id'])
                    status = {'alarm': 'Тревога', 'warning': 'Предупреждение', 'recovery': 'Возврат в норму'}[event]
                    subject = f'КВТ: {status} — {name}'
                    body = f'{subject}\n{label}: {measurement.get("value")} {unit}\nСостояние: {level}\nВремя: {stamp}'
                    target_key = 'recipients' if channel == 'email' else 'chat_ids'
                    routed = {**cfg, target_key: settings.recipients_for(config, channel, sensor['id'])}
                    self._enqueue(db, channel, routed, subject, body, sensor['id'])
        email = config['email']
        now = datetime.now()
        if email['enabled'] and email['daily_report'] and current.get('sensors') and now.strftime('%H:%M') >= email['daily_report_time']:
            key = 'email:daily_report'
            today = now.date().isoformat()
            old = db.execute('SELECT level FROM states WHERE key=?', (key,)).fetchone()
            if not old or old['level'] != today:
                body = '\n'.join(f'{s.get("name", s.get("id"))}: T={(s.get("temperature") or {}).get("value")} °C, '
                                 f'H={(s.get("humidity") or {}).get("value")} %, {s.get("combined_status")}'
                                 for s in current['sensors'])
                self._enqueue(db, 'email', email, 'КВТ: ежедневная сводка ' + today,
                              'Текущие показания, время снимка: ' + str(current.get('timestamp')) + '\n' + body)
                db.execute('INSERT OR REPLACE INTO states VALUES (?,?)', (key, today))

    def tick(self):
        config = settings.load()
        credentials = settings.secrets()
        with closing(self._connect()) as db:
            current = load_runtime_json(os.path.join(self.root, 'current.json'))
            self.collect(db, config, current, load_system_config().get('sensors', []))
            db.commit()  # Persist transitions and jobs before any network operation.
            self._write_status(db)
            jobs = db.execute("SELECT * FROM outbox WHERE status='pending' AND next_try<=? ORDER BY id LIMIT 20", (time.time(),)).fetchall()
            for job in jobs:
                if self._stop.is_set():
                    break
                channel = job['channel']
                cfg = config[channel]
                allowed = settings.recipients_for(config, channel, job['sensor_id'])
                if not cfg['enabled'] or job['recipient'] not in allowed:
                    db.execute("UPDATE outbox SET status='cancelled' WHERE id=?", (job['id'],))
                    db.commit()
                    continue
                try:
                    if channel == 'email':
                        send_email(cfg, credentials.get('smtp_password', ''), job['subject'], job['body'],
                                   [job['recipient']], job['message_id'])
                    else:
                        send_telegram(credentials.get('bot_token', ''), job['recipient'], job['body'])
                    db.execute("UPDATE outbox SET status='sent',sent_at=?,error=NULL WHERE id=?",
                               (datetime.now().isoformat(), job['id']))
                except Exception as exc:
                    attempts = job['attempts'] + 1
                    db.execute('UPDATE outbox SET attempts=?,next_try=?,status=?,error=? WHERE id=?',
                               (attempts, time.time() + 30 * attempts,
                                'failed' if attempts >= 3 else 'pending', error_message(exc), job['id']))
                db.commit()
                self._write_status(db)
            self._write_status(db)

    def _write_status(self, db):
        counts = {r['status']: r['n'] for r in db.execute('SELECT status,count(*) AS n FROM outbox GROUP BY status')}
        last = db.execute('SELECT channel,status,error,created_at,sent_at FROM outbox ORDER BY id DESC LIMIT 1').fetchone()
        failure = db.execute("SELECT error FROM outbox WHERE error IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
        atomic_save_json(self.status_path, {'running': True, 'updated_at': datetime.now().isoformat(),
                                           'counts': counts, 'last': dict(last) if last else None,
                                           'last_error': failure['error'] if failure else None})


SERVICE = NotificationService()
