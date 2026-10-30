import base64
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from email import policy
from email.parser import BytesParser
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image
from shared.daily_report import build_report
from shared import notification_service as delivery
from shared import notifications


class DailyReportTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 27, 7, 30, 0)
        self.current = {'timestamp': self.now.isoformat() + '.188822', 'sensors': [
            {'id': 1, 'name': 'Помещение <1>', 'combined_status': 'guarded',
             'temperature': {'value': 22.625}, 'humidity': {'value': 24}}]}
        self.sensors = [{'id': 1, 'name': 'Помещение <1>'}]
        self.rows = [self.row(-12, 20, 30, 1), self.row(-10, 24, 40, 3),
                     self.row(-32, 1000, 1000, 1), self.row(1, 1000, 1000, 1)]

    def row(self, hours, t, h, count):
        return {'sensor_id': 1, 'timestamp_start': self.now + timedelta(hours=hours),
                't_min': t, 't_max': t, 't_avg': t, 'h_min': h, 'h_max': h, 'h_avg': h,
                'sample_count': count, 'status': 'guarded'}

    def test_table_precision_escaping_period_stats_and_pngs(self):
        body, report = build_report(self.current, self.sensors, self.rows, now=self.now)
        self.assertIn('27.10.2026 07:30', body)
        self.assertNotIn('07:30:00', body + report['html'])
        self.assertNotIn('188822', body + report['html'])
        self.assertIn('Помещение &lt;1&gt;', report['html'])
        self.assertNotIn('Помещение <1>', report['html'])
        self.assertIn('температура мин 20 / макс 24 °C', body)
        self.assertIn('влажность мин 30 / макс 40 %', body)
        self.assertIn('Сейчас: температура 23 °C; влажность 24 %', body)
        self.assertIn('За предыдущий день · 26.10.2026', report['html'])
        self.assertIn('Норма', body)
        self.assertNotIn('guarded', body)
        self.assertEqual(len(report['images']), 2)
        for cid, value in report['images'].items():
            image = Image.open(BytesIO(base64.b64decode(value)))
            self.assertEqual(image.format, 'PNG')
            self.assertEqual(image.size, (1000, 330))
            self.assertIn(f'cid:{cid}', report['html'])
        self.assertLess(report['html'].index('Текущие показания'), report['html'].index('cid:kvt-daily-t'))

    def test_missing_nonfinite_offline_and_stale_data_are_explicit(self):
        self.current['timestamp'] = (self.now-timedelta(minutes=10)).isoformat()
        self.current['sensors'][0]['temperature']['value'] = float('nan')
        body, report = build_report(self.current, self.sensors, [], now=self.now)
        self.assertIn('Данные устарели', body)
        self.assertIn('температура — °C', body)
        self.assertNotIn('nan', body)
        self.assertEqual(report['images'], {})
        self.assertIn('Нет архивных данных', report['html'])
        self.rows[0]['status'] = 'no_connection'
        self.current['sensors'][0]['combined_status'] = 'no_connection'
        body, report = build_report(self.current, self.sensors, self.rows[:1], now=self.now, history_error=True)
        self.assertIn('Нет связи', body)
        self.assertIn('Архив недоступен', body)
        self.assertEqual(report['images'], {})

    def test_mime_contains_plain_html_and_cid_images(self):
        body, report = build_report(self.current, self.sensors, self.rows, now=self.now)
        config = {**notifications.defaults()['email'], 'smtp_server': 'localhost',
                  'from_address': 'from@example.test', 'recipients': ['to@example.test'], 'security': 'none'}
        fake = MagicMock()
        fake.return_value.__enter__.return_value.send_message.return_value = {}
        with patch.object(delivery.smtplib, 'SMTP', fake):
            delivery.send_email(config, '', 'Сводка', body, report=report)
        message = fake.return_value.__enter__.return_value.send_message.call_args.args[0]
        parsed = BytesParser(policy=policy.default).parsebytes(message.as_bytes())
        self.assertIn('Норма', parsed.get_body(preferencelist=('plain',)).get_content())
        self.assertIn('<table', parsed.get_body(preferencelist=('html',)).get_content())
        parts = [part for part in parsed.walk() if part.get_content_type() == 'image/png']
        self.assertEqual(len(parts), 2)
        for part in parts:
            self.assertEqual(part.get_content_disposition(), 'inline')
            self.assertTrue(part.get_payload(decode=True).startswith(b'\x89PNG'))
            self.assertIn('cid:' + part['Content-ID'][1:-1], report['html'])

    def test_existing_outbox_migrates_and_report_survives_restart(self):
        with tempfile.TemporaryDirectory() as root:
            with sqlite3.connect(str(Path(root) / 'notifications.db')) as db:
                db.execute('''CREATE TABLE outbox (id INTEGER PRIMARY KEY, channel TEXT,
                    recipient TEXT, subject TEXT, body TEXT, message_id TEXT, attempts INTEGER DEFAULT 0,
                    next_try REAL DEFAULT 0, status TEXT DEFAULT 'pending', error TEXT,
                    created_at TEXT, sent_at TEXT)''')
                db.execute("INSERT INTO outbox(body) VALUES ('legacy')")
            cfg = notifications.defaults()
            cfg['email'].update(enabled=True, daily_report=True, daily_report_time='00:00',
                                recipients=['to@example.test'])
            self.current['timestamp'] = datetime.now().isoformat()
            with closing(delivery.NotificationService(root)._connect()) as db:
                with patch('archiver.archive_service.ArchiveService.iter_measurements', return_value=iter(self.rows)):
                    delivery.NotificationService(root).collect(db, cfg, self.current, self.sensors)
                db.commit()
                job = db.execute('SELECT * FROM outbox WHERE id=2').fetchone()
                saved = json.loads(job['report_json'])
                self.assertIn('<table', saved['html'])
                self.assertIsNone(db.execute('SELECT report_json FROM outbox WHERE id=1').fetchone()[0])
            with closing(delivery.NotificationService(root)._connect()) as db:
                self.assertEqual(json.loads(db.execute('SELECT report_json FROM outbox WHERE id=2').fetchone()[0]), saved)

    def test_gaps_are_not_drawn_but_compressed_intervals_are_continuous(self):
        first = self.row(-31.5, 20, 30, 1)
        last = self.row(-7.5001, 20, 30, 1)
        _, report = build_report(self.current, self.sensors, [first, last], now=self.now)
        image = Image.open(BytesIO(base64.b64decode(report['images']['kvt-daily-t'])))
        self.assertNotEqual(image.getpixel((522, 151)), (37, 99, 235))
        first['timestamp_end'] = self.now
        _, report = build_report(self.current, self.sensors, [first], now=self.now)
        image = Image.open(BytesIO(base64.b64decode(report['images']['kvt-daily-t'])))
        self.assertEqual(image.getpixel((522, 151)), (37, 99, 235))

    def test_previous_calendar_day_boundaries_and_crossing_interval(self):
        # Yesterday starts 31.5 hours before this morning's 07:30 report.
        rows = [self.row(-31.5, 18.125, 29.125, 1),
                self.row(-7.5001, 25.125, 45.125, 1),
                self.row(-7.5, 999, 999, 1),  # today's midnight is excluded
                self.row(-2, 999, 999, 1),   # today's morning is excluded
                self.row(-31.5001, -999, -999, 1)]
        interval = self.row(-33, 16.125, 20.125, 1)
        interval['timestamp_end'] = self.now - timedelta(hours=30)
        rows.append(interval)
        body, report = build_report(self.current, self.sensors, rows, now=self.now)
        self.assertIn('температура мин 16 / макс 25 °C', body)
        self.assertIn('влажность мин 20 / макс 45 %', body)
        self.assertNotIn('999', body + report['html'])

    def test_offline_and_nonfinite_history_does_not_change_extrema(self):
        offline = self.row(-15, -999, 999, 1)
        offline['status'] = 'offline'
        nonfinite = self.row(-14, float('nan'), float('inf'), 1)
        body, _ = build_report(self.current, self.sensors,
                               self.rows + [offline, nonfinite], now=self.now)
        self.assertIn('температура мин 20 / макс 24 °C', body)
        self.assertIn('влажность мин 30 / макс 40 %', body)

    def test_delivery_retry_uses_persisted_html_and_images(self):
        with tempfile.TemporaryDirectory() as root:
            service = delivery.NotificationService(root)
            cfg = notifications.defaults()
            cfg['email'].update(enabled=True, recipients=['to@example.test'])
            body, report = build_report(self.current, self.sensors, self.rows, now=self.now)
            with closing(service._connect()) as db:
                service._enqueue(db, 'email', cfg['email'], 'Сводка', body, report=report)
                db.commit()
            with patch.object(delivery.settings, 'load', return_value=cfg), \
                 patch.object(delivery.settings, 'secrets', return_value={}), \
                 patch.object(delivery, 'load_system_config', return_value={'sensors': []}), \
                 patch.object(delivery, 'send_email', side_effect=[TimeoutError(), None]) as send:
                service.tick()
                with closing(service._connect()) as db:
                    db.execute('UPDATE outbox SET next_try=0')
                    db.commit()
                delivery.NotificationService(root).tick()
            self.assertEqual(send.call_count, 2)
            self.assertEqual(send.call_args_list[0], send.call_args_list[1])
            self.assertEqual(send.call_args.kwargs['report'], report)
            with closing(service._connect()) as db:
                self.assertEqual(db.execute('SELECT status FROM outbox').fetchone()[0], 'sent')


if __name__ == '__main__':
    unittest.main()
