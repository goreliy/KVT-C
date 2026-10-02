import io
import json
import os
import socketserver
import ssl
import tempfile
import threading
import unittest
import zipfile
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from shared import notifications as config
from shared import config_manager as cm
from shared import notification_service as delivery
from shared.config_bundle import export_config_bundle


def email_config():
    return {**config.defaults()['email'], 'enabled': True, 'smtp_server': 'localhost',
            'from_address': 'kvt@example.test', 'recipients': ['operator@example.test']}


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = os.path.join(self.temp.name, 'notifications.json')
        for name, target in (('NOTIFICATIONS_CONFIG_PATH', self.path),):
            p = patch.object(cm, name, target)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(config, 'SECRET_PATH', os.path.join(self.temp.name, 'secrets.key'))
        p.start()
        self.addCleanup(p.stop)

    def test_legacy_secret_migration_keep_replace_clear(self):
        cm.save_json(self.path, {'email': {'smtp_password': 'old'}, 'telegram': {'bot_token': '123:secret'}})
        self.assertNotIn('old', json.dumps(config.for_ui()))
        self.assertNotIn('123:secret', json.dumps(config.for_ui()))
        config.save({'email': {'security': 'tls'}})
        self.assertEqual(config.secrets()['smtp_password'], 'old')
        self.assertNotIn('smtp_password', cm.load_json(self.path)['email'])
        config.save({'email': {'smtp_password': '  new  '}})
        config.save({'email': {'smtp_password': ''}})
        self.assertEqual(config.secrets()['smtp_password'], '  new  ')
        config.save({'email': {'clear_password': True}})
        self.assertFalse(config.for_ui()['email']['password_set'])

    def test_validation_and_no_writes_on_rejection(self):
        for bad in (None, [], {'email': []}, {'email': {'security': 'auto'}},
                    {'email': {'smtp_port': 0}}, {'email': {'enabled': 'false'}},
                    {'email': {'smtp_server': 'server\r\nInjected: x'}},
                    {'email': {'from_address': 'bad'}}, {'email': {'recipients': 'x'}},
                    {'email': {'timeout_seconds': 120}}, {'email': {'daily_report_time': '25:00'}},
                    {'email': {'client_cert_path': 'cert.pem'}}, {'telegram': {'bot_token': 123}},
                    {'per_sensor': {'1': {'email_on_alarm': 'false'}}}):
            with self.subTest(payload=bad), self.assertRaises(ValueError):
                config.save(bad)
        self.assertFalse(os.path.exists(self.path))

    def test_export_excludes_legacy_credentials_and_key_file(self):
        root = Path(self.temp.name) / 'bundle'
        folder = root / 'data' / 'config'
        folder.mkdir(parents=True)
        (folder / 'notifications.json').write_text(json.dumps({'email': {'smtp_password': 'never-export'},
                                                              'telegram': {'bot_token': '123:private'}}))
        (folder / 'notification_secrets.key').write_text('private')
        data, _, _ = export_config_bundle(str(root), False)
        with zipfile.ZipFile(io.BytesIO(data)) as bundle:
            raw = bundle.read('config/notifications.json').decode()
            self.assertNotIn('never-export', raw)
            self.assertNotIn('123:private', raw)
            self.assertNotIn('config/notification_secrets.key', bundle.namelist())

    def test_routes_persist_replace_delete_and_preserve_partial_updates(self):
        group = {'id': 'warehouse', 'name': 'Склад', 'sensor_ids': [1, 2],
                 'email_recipients': ['group@example.test'], 'telegram_chat_ids': ['-100123']}
        config.save({'groups': [group], 'per_sensor': {'1': {'email_recipients': ['sensor@example.test']}}})
        saved = config.load()
        self.assertEqual(saved['groups'], [group])
        config.save({'email': {'security': 'tls'}})
        self.assertEqual(config.load()['per_sensor'], saved['per_sensor'])
        config.save({'per_sensor': {'1': {'email_on_alarm': False}}})
        self.assertEqual(config.load()['per_sensor']['1']['email_recipients'], ['sensor@example.test'])
        config.save({'replace_routing': True, 'per_sensor': {}, 'groups': []})
        self.assertEqual(config.load()['per_sensor'], {})
        self.assertEqual(config.load()['groups'], [])
        self.assertNotIn('replace_routing', cm.load_json(self.path))

    def test_routing_validation_rejects_bad_addresses_and_group_members(self):
        group = {'id': 'test', 'name': 'Test', 'sensor_ids': [1]}
        for bad in ({'groups': {}}, {'groups': [group, group]}, {'groups': [{**group, 'name': ''}]},
                    {'groups': [{**group, 'sensor_ids': [True]}]}, {'groups': [{**group, 'sensor_ids': ['1']}]},
                    {'groups': [{**group, 'email_recipients': ['bad-address']}]},
                    {'groups': [{**group, 'telegram_chat_ids': ['https://example.test']}]},
                    {'per_sensor': {'1': {'email_recipients': 'someone@example.test'}}},
                    {'per_sensor': {'1': {'email_recipients': ['user@example.test\r\nX: injected']}}},
                    {'per_sensor': {'0': {'email_recipients': []}}}, {'replace_routing': 'true'}):
            with self.subTest(payload=bad), self.assertRaises(ValueError):
                config.save(bad)


class RecipientRoutingTests(unittest.TestCase):
    def setUp(self):
        self.cfg = config.defaults()
        self.cfg['email']['recipients'] = ['global@example.test']
        self.cfg['telegram']['chat_ids'] = ['100']
        self.cfg['groups'] = [
            {'id': 'a', 'name': 'A', 'sensor_ids': [1, 2], 'email_recipients': ['a@example.test', 'shared@example.test'], 'telegram_chat_ids': ['101']},
            {'id': 'b', 'name': 'B', 'sensor_ids': [2, 3], 'email_recipients': ['b@example.test', 'shared@example.test']},
        ]

    def test_sensor_overrides_groups_and_global_with_channel_specific_inheritance(self):
        self.cfg['per_sensor'] = {'2': {'email_recipients': ['personal@example.test']}}
        self.assertEqual(config.recipients_for(self.cfg, 'email', 2), ['personal@example.test'])
        self.assertEqual(config.recipients_for(self.cfg, 'telegram', 2), ['101'])
        self.assertEqual(config.recipients_for(self.cfg, 'email', 4), ['global@example.test'])
        self.assertEqual(config.recipients_for(self.cfg, 'email'), ['global@example.test'])

    def test_overlapping_groups_union_targets_without_duplicate_messages(self):
        self.assertEqual(config.recipients_for(self.cfg, 'email', 2),
                         ['a@example.test', 'shared@example.test', 'b@example.test'])
        self.assertEqual(config.recipients_for(self.cfg, 'telegram', 3), ['100'])

    def test_empty_targets_disable_sensor_or_group_and_missing_key_inherits(self):
        self.cfg['per_sensor'] = {'2': {'email_recipients': []}}
        self.assertEqual(config.recipients_for(self.cfg, 'email', 2), [])
        self.assertEqual(config.recipients_for(self.cfg, 'telegram', 2), ['101'])
        self.cfg['groups'][0]['email_recipients'] = []
        self.assertEqual(config.recipients_for(self.cfg, 'email', 1), [])
        del self.cfg['groups'][0]['email_recipients']
        self.assertEqual(config.recipients_for(self.cfg, 'email', 1), ['global@example.test'])


class SMTPHandler(socketserver.StreamRequestHandler):
    def setup(self):
        if self.server.implicit_tls:
            self.request = self.server.context.wrap_socket(self.request, server_side=True)
        super().setup()

    def respond(self, text):
        self.wfile.write(text.encode('ascii') + b'\r\n')
        self.wfile.flush()

    def handle(self):
        self.request.settimeout(3)
        self.respond('220 local test server')
        while True:
            line = self.rfile.readline().decode('ascii').strip()
            if not line:
                break
            command = line.split(' ', 1)[0].upper()
            self.server.commands.append(command)
            if command in ('EHLO', 'HELO'):
                self.respond('250-localhost')
                self.respond('250-STARTTLS')
                self.respond('250 AUTH PLAIN')
            elif command == 'STARTTLS':
                self.respond('220 ready')
                self.rfile.close()
                self.wfile.close()
                self.request = self.server.context.wrap_socket(self.request, server_side=True)
                self.rfile = self.request.makefile('rb')
                self.wfile = self.request.makefile('wb')
            elif command == 'AUTH':
                self.respond('235 authenticated')
            elif command in ('MAIL', 'RCPT', 'RSET'):
                self.respond('250 accepted')
            elif command == 'DATA':
                self.respond('354 send data')
                data = bytearray()
                while True:
                    item = self.rfile.readline()
                    if item == b'.\r\n':
                        break
                    data.extend(item)
                self.server.messages.append(bytes(data))
                self.respond('250 queued')
            elif command == 'QUIT':
                self.respond('221 bye')
                break
            else:
                self.respond('500 unsupported')

    def finish(self):
        try:
            super().finish()
        finally:
            self.request.close()


class SMTPTestServer(socketserver.ThreadingTCPServer):
    def handle_error(self, request, client_address):
        # Rejected TLS handshakes are expected in certificate validation tests.
        pass


class SMTPIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.key_path = str(Path(cls.temp.name) / 'key.pem')
        cls.cert_path = str(Path(cls.temp.name) / 'cert.pem')
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
        cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(datetime.utcnow() - timedelta(days=1))
                .not_valid_after(datetime.utcnow() + timedelta(days=1))
                .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]), critical=False)
                .sign(key, hashes.SHA256()))
        Path(cls.cert_path).write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        Path(cls.key_path).write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                                       serialization.PrivateFormat.PKCS8,
                                                       serialization.NoEncryption()))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def server(self, implicit=False):
        # All traffic stays on loopback; no external messages are sent.
        server = SMTPTestServer(('127.0.0.1', 0), SMTPHandler)
        server.daemon_threads = True
        server.implicit_tls = implicit
        server.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server.context.load_cert_chain(self.cert_path, self.key_path)
        server.commands, server.messages = [], []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def test_plain_starttls_and_implicit_tls_real_connections(self):
        for mode in ('none', 'starttls', 'tls'):
            with self.subTest(mode=mode):
                server = self.server(mode == 'tls')
                cfg = {**email_config(), 'smtp_port': server.server_address[1], 'security': mode,
                       'ca_cert_path': self.cert_path, 'smtp_user': 'user'}
                delivery.send_email(cfg, 'test-password', 'Тест КВТ', 'Температура: 25 °C')
                self.assertEqual(len(server.messages), 1)
                self.assertIn(b'Content-Type: text/plain; charset="utf-8"', server.messages[0])
                self.assertIn('AUTH', server.commands)
                self.assertEqual('STARTTLS' in server.commands, mode == 'starttls')
                if mode == 'starttls':
                    self.assertLess(server.commands.index('STARTTLS'), server.commands.index('AUTH'))
                    self.assertEqual(server.commands.count('EHLO'), 2)

    def test_starttls_failure_never_falls_back_to_plaintext(self):
        fake = MagicMock()
        smtp = fake.return_value.__enter__.return_value
        smtp.starttls.side_effect = delivery.smtplib.SMTPNotSupportedError('unsupported')
        with patch.object(delivery.smtplib, 'SMTP', fake), self.assertRaises(delivery.smtplib.SMTPNotSupportedError):
            delivery.send_email(email_config(), 'secret', 'test', 'test')
        smtp.login.assert_not_called()
        smtp.send_message.assert_not_called()

    def test_authentication_failure_does_not_send_message(self):
        fake = MagicMock()
        smtp = fake.return_value.__enter__.return_value
        smtp.login.side_effect = delivery.smtplib.SMTPAuthenticationError(535, b'private detail')
        with patch.object(delivery.smtplib, 'SMTP', fake), self.assertRaises(delivery.smtplib.SMTPAuthenticationError):
            delivery.send_email({**email_config(), 'smtp_user': 'user'}, 'secret', 'test', 'test')
        smtp.send_message.assert_not_called()

    def test_telegram_https_transport_and_error_redaction(self):
        with patch.object(delivery.requests, 'post') as post:
            post.return_value.ok = True
            post.return_value.json.return_value = {'ok': True}
            delivery.send_telegram('123:token', '-100123', 'Тест')
            self.assertEqual(post.call_args.args[0], 'https://api.telegram.org/bot123:token/sendMessage')
            self.assertEqual(post.call_args.kwargs['json']['chat_id'], '-100123')
            post.return_value.ok = False
            with self.assertRaises(RuntimeError) as error:
                delivery.send_telegram('123:token', '-100123', 'Тест')
            self.assertNotIn('123:token', str(error.exception))

    def test_untrusted_cert_and_wrong_hostname_fail_before_authentication(self):
        for ca, host in (('', 'localhost'), (self.cert_path, '127.0.0.1')):
            with self.subTest(ca=bool(ca), host=host):
                server = self.server(True)
                cfg = {**email_config(), 'smtp_port': server.server_address[1], 'security': 'tls',
                       'ca_cert_path': ca, 'smtp_server': host, 'smtp_user': 'user'}
                with self.assertRaises(ssl.SSLCertVerificationError):
                    delivery.send_email(cfg, 'secret', 'test', 'test')
                self.assertNotIn('AUTH', server.commands)
                self.assertEqual(server.messages, [])

    def test_explicit_unverified_tls_and_no_authentication(self):
        server = self.server(True)
        cfg = {**email_config(), 'smtp_port': server.server_address[1], 'security': 'tls',
               'verify_certificate': False}
        delivery.send_email(cfg, '', 'test', 'test')
        self.assertNotIn('AUTH', server.commands)
        self.assertEqual(len(server.messages), 1)

    def test_tls_certificate_checks_are_enabled_by_default(self):
        context = delivery.tls_context(email_config())
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)
        cfg = {**email_config(), 'verify_certificate': False}
        context = delivery.tls_context(cfg)
        self.assertFalse(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_NONE)


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = delivery.NotificationService(self.temp.name)
        self.cfg = config.defaults()
        self.cfg['email'] = email_config()
        self.sensor = {'id': 1, 'name': 'Room', 'enabled': True, 'guarded': True,
                       'temp_limits': {'min': 10, 'max': 25, 'warning_delta': 1, 'alarm_delta': 5}}

    def collect(self, value, cfg=None, timestamp=None):
        reading = {'timestamp': timestamp or datetime.now().isoformat(),
                   'sensors': [{'id': 1, 'temperature': {'value': value}}]}
        with closing(self.service._connect()) as db:
            self.service.collect(db, cfg or self.cfg, reading, [self.sensor])
            db.commit()
            return db.execute('SELECT * FROM outbox ORDER BY id').fetchall()

    def test_transitions_recovery_and_restart_deduplication(self):
        self.cfg['email']['on_recovery'] = True
        self.assertEqual(len(self.collect(27)), 1)
        self.assertEqual(len(self.collect(28)), 1)
        self.assertEqual(len(self.collect(32)), 2)
        self.service = delivery.NotificationService(self.temp.name)
        self.assertEqual(len(self.collect(33)), 2)
        self.assertEqual(len(self.collect(20)), 3)
        self.assertEqual(len(self.collect(32)), 4)

    def test_disabled_unguarded_stale_invalid_and_per_sensor_filters(self):
        self.assertEqual(len(self.collect(None)), 0)
        self.assertEqual(len(self.collect(float('nan'))), 0)
        self.assertEqual(len(self.collect(32, timestamp=(datetime.now()-timedelta(minutes=10)).isoformat())), 0)
        self.sensor['guarded'] = False
        self.assertEqual(len(self.collect(32)), 0)
        self.sensor['guarded'] = True
        self.sensor['notifications'] = {'email_on_alarm': False}
        self.assertEqual(len(self.collect(32)), 0)
        self.assertEqual(len(self.collect(27)), 1)

    def test_daily_report_enqueues_once_per_day(self):
        self.cfg['email'].update(daily_report=True, daily_report_time='00:00')
        self.assertEqual(len(self.collect(20)), 1)
        self.assertEqual(len(self.collect(20)), 1)

    def test_temperature_and_humidity_alerts_are_independent(self):
        self.sensor['hum_limits'] = {'min': 20, 'max': 50, 'warning_delta': 5, 'alarm_delta': 10}
        reading = {'timestamp': datetime.now().isoformat(), 'sensors': [
            {'id': 1, 'temperature': {'value': 32}, 'humidity': {'value': 70}}]}
        with closing(self.service._connect()) as db:
            self.service.collect(db, self.cfg, reading, [self.sensor])
            db.commit()
            messages = [r['body'] for r in db.execute('SELECT body FROM outbox')]
            self.assertEqual(len(messages), 2)
            self.assertTrue(any('Температура' in body for body in messages))
            self.assertTrue(any('Влажность' in body for body in messages))

    def test_retries_persist_and_stop_after_three_attempts(self):
        self.collect(32)
        with patch.object(config, 'load', return_value=self.cfg), patch.object(config, 'secrets', return_value={}), \
             patch.object(delivery, 'load_system_config', return_value={'sensors': []}), \
             patch.object(delivery, 'send_email', side_effect=TimeoutError) as send:
            self.service.tick()
            self.service.tick()
            self.assertEqual(send.call_count, 1)
            for _ in range(2):
                with closing(self.service._connect()) as db:
                    db.execute('UPDATE outbox SET next_try=0')
                    db.commit()
                self.service.tick()
        with closing(self.service._connect()) as db:
            job = db.execute('SELECT * FROM outbox').fetchone()
            self.assertEqual(job['attempts'], 3)
            self.assertEqual(job['status'], 'failed')

    def test_success_separate_recipients_and_disabled_channel_cancellation(self):
        self.cfg['email']['recipients'].append('other@example.test')
        self.collect(32)
        with patch.object(config, 'load', return_value=self.cfg), patch.object(config, 'secrets', return_value={}), \
             patch.object(delivery, 'load_system_config', return_value={'sensors': []}), \
             patch.object(delivery, 'send_email') as send:
            self.service.tick()
            self.service.tick()
            self.assertEqual(send.call_count, 2)
            self.assertEqual(send.call_args_list[0].args[4], ['operator@example.test'])
        with closing(self.service._connect()) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM outbox WHERE status='sent'").fetchone()[0], 2)
        self.collect(20)
        self.collect(32)
        self.cfg['email']['enabled'] = False
        with patch.object(config, 'load', return_value=self.cfg), patch.object(config, 'secrets', return_value={}), \
             patch.object(delivery, 'load_system_config', return_value={'sensors': []}), \
             patch.object(delivery, 'send_email') as send:
            self.service.tick()
            send.assert_not_called()

    def test_sensor_only_targets_survive_restart_and_are_actually_delivered(self):
        self.cfg['email']['recipients'] = []
        self.cfg['per_sensor'] = {'1': {'email_recipients': ['personal@example.test']}}
        jobs = self.collect(32)
        self.assertEqual([(j['recipient'], j['sensor_id']) for j in jobs], [('personal@example.test', 1)])
        self.service = delivery.NotificationService(self.temp.name)
        with patch.object(config, 'load', return_value=self.cfg), patch.object(config, 'secrets', return_value={}), \
             patch.object(delivery, 'load_system_config', return_value={'sensors': []}), \
             patch.object(delivery, 'send_email') as send:
            self.service.tick()
            self.assertEqual(send.call_args.args[4], ['personal@example.test'])

    def test_pending_message_is_cancelled_after_recipient_routing_changes(self):
        self.cfg['per_sensor'] = {'1': {'email_recipients': ['old@example.test']}}
        self.collect(32)
        self.cfg['per_sensor']['1']['email_recipients'] = ['new@example.test']
        with patch.object(config, 'load', return_value=self.cfg), patch.object(config, 'secrets', return_value={}), \
             patch.object(delivery, 'load_system_config', return_value={'sensors': []}), \
             patch.object(delivery, 'send_email') as send:
            self.service.tick()
            send.assert_not_called()
        with closing(self.service._connect()) as db:
            self.assertEqual(db.execute('SELECT status FROM outbox').fetchone()['status'], 'cancelled')

    def test_group_targets_are_deduplicated_in_outbox_and_delivered(self):
        self.cfg['groups'] = [
            {'id': 'a', 'name': 'A', 'sensor_ids': [1], 'email_recipients': ['a@example.test', 'shared@example.test']},
            {'id': 'b', 'name': 'B', 'sensor_ids': [1], 'email_recipients': ['b@example.test', 'shared@example.test']},
        ]
        self.assertEqual(len(self.collect(32)), 3)
        with patch.object(config, 'load', return_value=self.cfg), patch.object(config, 'secrets', return_value={}), \
             patch.object(delivery, 'load_system_config', return_value={'sensors': []}), \
             patch.object(delivery, 'send_email') as send:
            self.service.tick()
            self.assertEqual([c.args[4][0] for c in send.call_args_list],
                             ['a@example.test', 'shared@example.test', 'b@example.test'])

    def test_existing_outbox_database_is_migrated_without_losing_jobs(self):
        with closing(delivery.sqlite3.connect(self.service.db_path)) as db:
            db.execute('''CREATE TABLE outbox(id INTEGER PRIMARY KEY, channel TEXT, recipient TEXT,
                          subject TEXT, body TEXT, message_id TEXT, attempts INTEGER DEFAULT 0,
                          next_try REAL DEFAULT 0, status TEXT DEFAULT 'pending', error TEXT,
                          created_at TEXT, sent_at TEXT)''')
            db.execute("INSERT INTO outbox(channel,recipient) VALUES ('email','operator@example.test')")
            db.commit()
        with closing(self.service._connect()) as db:
            job = db.execute('SELECT * FROM outbox').fetchone()
            self.assertEqual(job['recipient'], 'operator@example.test')
            self.assertIsNone(job['sensor_id'])


class ApiTests(ConfigTests):
    def setUp(self):
        super().setUp()
        from flask import Flask
        from visualizer.routes.notifications import notifications_bp
        self.app = Flask(__name__)
        self.app.register_blueprint(notifications_bp, url_prefix='/api')
        self.client = self.app.test_client()

    def test_save_get_and_test_use_saved_settings_without_exposing_password(self):
        payload = {'email': {**email_config(), 'smtp_password': 'hidden-password'}}
        response = self.client.post('/api/notifications', json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b'hidden-password', response.data)
        response = self.client.get('/api/notifications')
        self.assertTrue(response.json['email']['password_set'])
        with patch('visualizer.routes.notifications.send_email') as send:
            response = self.client.post('/api/notifications/test', json={'channel': 'email'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(send.call_args.args[1], 'hidden-password')
        self.assertEqual(self.client.post('/api/notifications', json={'email': {'smtp_port': -1}}).status_code, 400)

    def test_error_redaction(self):
        self.client.post('/api/notifications', json={'email': email_config()})
        with patch('visualizer.routes.notifications.send_email', side_effect=RuntimeError('private-password')):
            response = self.client.post('/api/notifications/test', json={'channel': 'email'})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(b'private-password', response.data)

    def test_sensor_test_uses_saved_routes_for_email_and_telegram(self):
        payload = {'email': email_config(), 'telegram': {'bot_token': '123:token'},
                   'groups': [{'id': 'warehouse', 'name': 'Warehouse', 'sensor_ids': [1],
                               'email_recipients': ['group@example.test'], 'telegram_chat_ids': ['-100123']}],
                   'per_sensor': {'1': {'email_recipients': ['sensor@example.test']}}}
        self.assertEqual(self.client.post('/api/notifications', json=payload).status_code, 200)
        with patch('visualizer.routes.notifications.load_system_config', return_value={'sensors': [{'id': 1}]}), \
             patch('visualizer.routes.notifications.send_email') as send_email, \
             patch('visualizer.routes.notifications.send_telegram') as send_tg:
            self.assertEqual(self.client.post('/api/notifications/test', json={'channel': 'email', 'sensor_id': 1}).status_code, 200)
            self.assertEqual(send_email.call_args.args[4], ['sensor@example.test'])
            self.assertEqual(self.client.post('/api/notifications/test', json={'channel': 'telegram', 'sensor_id': 1}).status_code, 200)
            self.assertEqual(send_tg.call_args.args[1], '-100123')
            self.assertEqual(self.client.post('/api/notifications/test', json={'channel': 'email', 'sensor_id': 999}).status_code, 400)

    def test_settings_template_contains_modes_and_never_contains_secrets(self):
        from jinja2 import Environment, FileSystemLoader
        template_dir = Path(__file__).resolve().parents[1] / 'visualizer' / 'templates'
        env = Environment(loader=FileSystemLoader(str(template_dir)), autoescape=True)
        cfg = config.for_ui()
        request = MagicMock(path='/settings/notifications')
        html = env.get_template('settings/notifications.html').render(
            notif=cfg, active_theme='dark', app_title='КВТ', theme_colors={}, request=request,
            url_for=lambda *a, **kw: '/static/css/style.css')
        for mode in ('none', 'starttls', 'tls'):
            self.assertIn(f'value="{mode}"', html)
        self.assertIn('notificationPayload', html)
        self.assertNotIn('Заглушка', html)


if __name__ == '__main__':
    unittest.main()
