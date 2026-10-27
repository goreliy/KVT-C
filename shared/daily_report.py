"""Mail-safe daily summary: HTML tables and self-contained PNG charts."""
import base64
import math
from datetime import datetime, timedelta
from html import escape
from io import BytesIO


COLORS = ('#2563eb', '#0891b2', '#d97706', '#7c3aed', '#dc2626', '#059669', '#db2777', '#475569')
OFFLINE = ('offline', 'no_connection', 'disabled')


def local_time(value):
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return stamp.astimezone().replace(tzinfo=None) if stamp.tzinfo else stamp
    except (TypeError, ValueError):
        return None


def time_text(value):
    stamp = local_time(value)
    return stamp.strftime('%d.%m.%Y %H:%M:%S') if stamp else '—'


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def value_text(value):
    value = number(value)
    return f'{value:.2f}'.replace('.', ',') if value is not None else '—'


def _chart(series, start, end):
    # Imported only for reports; ordinary alarm delivery does not require Pillow.
    from PIL import Image, ImageDraw, ImageFont
    image = Image.new('RGB', (1000, 330), 'white')
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype('DejaVuSans.ttf', 16)
    except OSError:
        try:
            font = ImageFont.truetype('arial.ttf', 16)
        except OSError:
            font = ImageFont.load_default(size=16)
    left, top, right, bottom = 72, 22, 972, 280
    values = [value for points in series.values() for _, value in points if value is not None]
    if not values:
        return None
    lo, hi = min(values), max(values)
    margin = max((hi - lo) * .12, .5)
    lo, hi = lo - margin, hi + margin
    for index in range(6):
        y = bottom - (bottom - top) * index / 5
        draw.line((left, y, right, y), fill='#e2e8f0')
        draw.text((8, y - 9), f'{lo + (hi-lo)*index/5:.1f}', font=font, fill='#64748b')
        x = left + (right - left) * index / 5
        draw.line((x, top, x, bottom), fill='#f1f5f9')
        stamp = start + (end - start) * index / 5
        draw.text((x - 24, bottom + 15), stamp.strftime('%H:%M'), font=font, fill='#64748b')
    for index, points in enumerate(series.values()):
        color = COLORS[index % len(COLORS)]
        previous = None
        for stamp, value in points:
            if value is None:
                previous = None
                continue
            x = left + (stamp-start).total_seconds() / (end-start).total_seconds() * (right-left)
            y = bottom - (value-lo) / (hi-lo) * (bottom-top)
            if previous and (stamp-previous[0]).total_seconds() <= 1800:
                draw.line((previous[1], previous[2], x, y), fill=color, width=3)
            draw.ellipse((x-2, y-2, x+2, y+2), fill=color)
            previous = stamp, x, y
    output = BytesIO()
    image.save(output, format='PNG', optimize=True)
    return base64.b64encode(output.getvalue()).decode('ascii')


def build_report(current, sensors, archive_rows, now=None, history_error=False):
    """Return a JSON-serializable immutable outbox payload and plain fallback."""
    now = local_time(now or datetime.now())
    start = now - timedelta(hours=24)
    known = {str(sensor['id']): sensor for sensor in sensors}
    readings = current.get('sensors') or []
    series = {metric: {str(s['id']): [] for s in readings} for metric in ('t', 'h')}
    stats = {metric: {} for metric in ('t', 'h')}
    for row in archive_rows:
        sid = str(row['sensor_id'])
        if sid not in series['t']:
            continue
        stamp = local_time(row['timestamp_start'])
        end = local_time(row.get('timestamp_end')) or stamp
        if not stamp or end < start or stamp > now:
            continue
        for metric in ('t', 'h'):
            value = number(row.get(metric + '_avg')) if row.get('status') not in OFFLINE else None
            series[metric][sid].append((max(start, stamp), value))
            if end > stamp:
                # A compressed interval explicitly represents unchanged readings,
                # so it is continuous even when it lasts more than 30 minutes.
                cursor = max(start, stamp) + timedelta(minutes=30)
                while cursor < min(now, end):
                    series[metric][sid].append((cursor, value))
                    cursor += timedelta(minutes=30)
                series[metric][sid].append((min(now, end), value))
            if value is not None:
                bucket = stats[metric].setdefault(sid, [])
                bucket.append((number(row.get(metric+'_min')), value, number(row.get(metric+'_max')),
                               max(1, int(row.get('sample_count') or 1))))
    for metric in series:
        for points in series[metric].values():
            points.sort(key=lambda point: point[0])

    def summary(metric, sid):
        rows = stats[metric].get(sid, [])
        if not rows:
            return '—'
        low = min(r[0] if r[0] is not None else r[1] for r in rows)
        high = max(r[2] if r[2] is not None else r[1] for r in rows)
        avg = sum(r[1]*r[3] for r in rows) / sum(r[3] for r in rows)
        return ' / '.join(value_text(v) for v in (low, avg, high))

    plain = [f'КВТ: ежедневная сводка {now:%d.%m.%Y}',
             f'Снимок: {time_text(current.get("timestamp"))}',
             f'История за 24 часа: {time_text(start)} — {time_text(now)}']
    table_rows, stat_rows, legend = [], [], []
    attention = 0
    for index, reading in enumerate(readings):
        sid = str(reading['id'])
        sensor = known.get(sid, {})
        name = sensor.get('name') or reading.get('name') or f'Датчик {sid}'
        status = reading.get('combined_status')
        stamps = [(reading.get(metric) or {}).get('timestamp') or current.get('timestamp')
                  for metric in ('temperature', 'humidity')]
        stale = any(local_time(stamp) is None or not -60 <= (now-local_time(stamp)).total_seconds() <= 120
                    for stamp in stamps)
        if status in OFFLINE or not sensor.get('enabled', True):
            label = 'Отключён' if status == 'disabled' or not sensor.get('enabled', True) else 'Нет связи'
            color = '#b91c1c'
        elif stale:
            label, color = 'Данные устарели', '#b45309'
        elif status == 'alarm' or str(status).startswith('alarm_'):
            label, color = 'Тревога', '#b91c1c'
        elif str(status).startswith('warning'):
            label, color = 'Предупреждение', '#b45309'
        elif status in ('normal', 'guarded'):
            label, color = ('Норма' if sensor.get('guarded', True) else 'Снят с охраны'), '#047857'
        else:
            label, color = 'Нет статуса', '#64748b'
        attention += label not in ('Норма', 'Снят с охраны')
        values = [value_text((reading.get(metric) or {}).get('value')) if status not in OFFLINE else '—'
                  for metric in ('temperature', 'humidity')]
        cells = [str(index+1), escape(str(name)), *values,
                 f'<span style="color:{color};font-weight:bold">{label}</span>',
                 '<br>'.join(escape(time_text(stamp)) for stamp in dict.fromkeys(stamps))]
        table_rows.append('<tr>' + ''.join(f'<td style="{CELL}">{cell}</td>' for cell in cells) + '</tr>')
        stat_cells = [escape(str(name)), summary('t', sid), summary('h', sid)]
        stat_rows.append('<tr>' + ''.join(f'<td style="{CELL}">{cell}</td>' for cell in stat_cells) + '</tr>')
        plain.append(f'{name}: T={values[0]} °C; H={values[1]} %; {label}; {time_text(stamps[0])}')
        plain.append(f'  Мин / сред / макс: T {summary("t", sid)} °C; H {summary("h", sid)} %')
        legend.append(f'<span style="color:{COLORS[index % len(COLORS)]}">● {index+1}. {escape(str(name))}</span>')
    images, charts = {}, []
    for metric, title, unit in (('t', 'Температура', '°C'), ('h', 'Влажность', '%')):
        chart = _chart(series[metric], start, now)
        charts.append(f'<h3 style="font-size:17px;margin:28px 0 12px">{title}, {unit}</h3>')
        if chart:
            cid = 'kvt-daily-' + metric
            images[cid] = chart
            charts.append(f'<img src="cid:{cid}" alt="{title} за последние 24 часа" width="900" style="display:block;width:100%;max-width:900px;height:auto">')
        else:
            charts.append('<p style="color:#64748b">Нет архивных данных за выбранный период.</p>')
    history_note = ('Архив недоступен. Показаны только последние показания.' if history_error else
                    'Статистика по доступным архивным измерениям; среднее взвешено по числу отсчётов. '
                    'Пробелы более 30 минут и отсутствие связи показаны разрывами.')
    plain.append(history_note)
    html = f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"></head>
<body style="margin:0;background:#f1f5f9;color:#172033;font-family:Arial,sans-serif;font-size:14px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td style="padding:24px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:980px;margin:auto;background:white;border:1px solid #e2e8f0"><tr><td style="padding:28px">
<p style="color:#2563eb;font-size:12px;font-weight:bold;letter-spacing:2px;margin:0 0 10px">КВТ · МОНИТОРИНГ МИКРОКЛИМАТА</p>
<h1 style="font-size:26px;margin:0 0 12px">Ежедневная сводка · {now:%d.%m.%Y}</h1>
<p style="color:#64748b;line-height:1.7">Снимок: <b>{time_text(current.get('timestamp'))}</b><br>
Сформировано: {time_text(now)} · Местное время системы КВТ</p>
<p style="padding:14px;background:#eff6ff;color:#1e40af">Датчиков: <b>{len(readings)}</b> &nbsp; · &nbsp; Требуют внимания: <b>{attention}</b></p>
<h2 style="font-size:19px;margin:26px 0 12px">Текущие показания</h2>
{_table(['№', 'Датчик / помещение', 'T, °C', 'H, %', 'Состояние', 'Время измерения¹'], table_rows)}
<p style="color:#64748b;font-size:12px">¹ Если времена различаются: температура, затем влажность. Устаревшие значения приведены справочно.</p>
<h2 style="font-size:19px;margin:28px 0 8px">Последние 24 часа</h2>
<p style="color:#64748b">{time_text(start)} — {time_text(now)}</p>
{_table(['Датчик / помещение', 'T: мин / сред / макс, °C', 'H: мин / сред / макс, %'], stat_rows)}
<p style="color:#64748b;font-size:12px;line-height:1.6">{history_note}</p>
<p style="line-height:1.8">{' &nbsp; '.join(legend)}</p>
{''.join(charts)}
<p style="border-top:1px solid #e2e8f0;margin-top:28px;padding-top:16px;color:#64748b;font-size:12px">Автоматическая сводка КВТ. Графики встроены в письмо и не требуют доступа к серверу.</p>
</td></tr></table></td></tr></table></body></html>'''
    return '\n'.join(plain), {'html': html, 'images': images}


CELL = 'padding:12px 10px;border-bottom:1px solid #e2e8f0;text-align:left;vertical-align:top;line-height:1.5'


def _table(headers, rows):
    head = ''.join(f'<th style="{CELL}background:#f8fafc;color:#475569;font-size:12px">{header}</th>' for header in headers)
    return f'<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;font-size:13px"><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>'
