"""Generate a local summary preview without sending mail or changing the queue."""
import argparse
import json
import math
from datetime import datetime, timedelta
from pathlib import Path

from archiver.archive_service import ArchiveService
from shared.config_manager import load_runtime_json, load_system_config
from shared.daily_report import build_report
from shared.paths import data_dir


def demo():
    now = datetime(2026, 10, 27, 7, 30)
    names = ['к. 168 Контролёр СМД', 'к. 168 Р-ДИП прогон', 'к. 166 Филоненко',
             'к. 168 Проверка', 'к. 168 Упаковка']
    sensors = [{'id': i+1, 'name': name} for i, name in enumerate(names)]
    readings, rows = [], []
    for i, sensor in enumerate(sensors):
        for step in range(145):
            stamp = now - timedelta(hours=24) + timedelta(minutes=10*step)
            t = 22 + i*.18 + math.sin(step/15+i)*.6
            h = 25 + i*.8 + math.sin(step/22+i)*2
            rows.append({'sensor_id': sensor['id'], 'timestamp_start': stamp,
                         'timestamp_end': stamp, 't_min': t, 't_avg': t, 't_max': t,
                         'h_min': h, 'h_avg': h, 'h_max': h, 'sample_count': 1, 'status': 'guarded'})
        readings.append({**sensor, 'temperature': {'value': t}, 'humidity': {'value': h},
                         'combined_status': 'guarded'})
    return {'timestamp': now.isoformat(), 'sensors': readings}, sensors, rows, now


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--demo', action='store_true', help='Use explicitly marked sample data')
    parser.add_argument('--output', default='build/daily-report-preview.html')
    args = parser.parse_args()
    if args.demo:
        current, sensors, rows, now = demo()
    else:
        current = load_runtime_json(str(Path(data_dir()) / 'current.json'))
        sensors = load_system_config().get('sensors', [])
        rows = list(ArchiveService().iter_measurements())
        now = datetime.now()
    body, report = build_report(current, sensors, rows, now=now)
    html = report['html']
    for cid, encoded in report['images'].items():
        html = html.replace('cid:' + cid, 'data:image/png;base64,' + encoded)
    if args.demo:
        html = html.replace('<h1 ', '<p style="color:#b45309;font-weight:bold">ПРИМЕР ОФОРМЛЕНИЯ · Демонстрационные измерения</p><h1 ', 1)
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding='utf-8')
    output.with_suffix('.txt').write_text(body, encoding='utf-8')
    print(output.as_uri())


if __name__ == '__main__':
    main()
