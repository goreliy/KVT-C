import argparse
import html
import json
import os
import sys

from flask import Flask, jsonify, render_template_string, request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.config_manager import load_poller_config
from .poller_service import PollerService

app = Flask(__name__)
SERVICE = PollerService()


PAGE_TEMPLATE = """
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>KVT Modbus Poller</title>
  <style>
    body { font-family: Arial, sans-serif; background: #101827; color: #e5e7eb; margin: 0; padding: 18px; }
    h2 { margin: 0 0 14px; }
    .box { background: #111827; border: 1px solid #334155; border-radius: 8px; padding: 12px; margin-bottom: 10px; }
    .row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
    input, button { padding: 7px 9px; border-radius: 6px; border: 1px solid #475569; background: #1f2937; color: #e5e7eb; }
    button { cursor: pointer; }
    button:hover { background: #374151; }
    pre { white-space: pre-wrap; overflow: auto; max-height: 300px; margin: 8px 0 0; font-size: 13px; line-height: 1.35; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 10px; }
    .muted { color: #94a3b8; font-size: 12px; }
    table { width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 13px; }
    th, td { border-bottom: 1px solid #334155; padding: 7px 8px; vertical-align: top; text-align: left; }
    th { color: #cbd5e1; background: #162033; position: sticky; top: 0; }
    .exchange-wrap { max-height: 430px; overflow: auto; }
    .ok { background: rgba(22, 163, 74, 0.12); }
    .error { background: rgba(220, 38, 38, 0.14); }
    tr.line-mark { box-shadow: inset 5px 0 0 var(--line-color, #64748b); }
    .badge { display: inline-block; margin-top: 4px; padding: 2px 7px; border-radius: 999px; font-size: 11px; font-weight: 700; }
    .badge.scan { background: rgba(56, 189, 248, 0.18); color: #7dd3fc; }
    .badge.values { background: rgba(34, 197, 94, 0.18); color: #86efac; }
    .badge.status { background: rgba(245, 158, 11, 0.18); color: #fcd34d; }
    .hex { font-family: Consolas, monospace; white-space: nowrap; }
  </style>
</head>
<body>
  <h2>KVT Modbus Poller</h2>
  <div class="box">
    <div class="row">
      <button onclick="postAction('/api/poller/start')">Старт</button>
      <button onclick="postAction('/api/poller/stop')">Стоп</button>
      <button onclick="postAction('/api/poller/reload')">Reload датчиков</button>
      <button onclick="refreshAll()">Обновить</button>
      <span class="muted">Страница сразу рендерит текущий статус, JS только обновляет данные.</span>
    </div>
  </div>
  <div class="box">
    <div class="row">
      <label>Slave ID:</label>
      <input id="slave" type="number" min="1" max="247" value="{{ config.device_slave_id or 16 }}">
      <button onclick="saveConfig()">Сохранить Slave ID</button>
      <label>Скан:</label>
      <input id="scanStart" type="number" min="1" max="247" value="1">
      <input id="scanEnd" type="number" min="1" max="247" value="32">
      <input id="scanTimeout" type="number" min="50" max="10000" value="{{ config.timeout_ms or 500 }}">
      <button onclick="scanDevices()">Поиск устройств</button>
    </div>
  </div>
  <div class="box">
    <b>Парный журнал Modbus: строка конфигурации -> TX -> RX</b>
    <div class="exchange-wrap">
      <table>
        <thead>
          <tr>
            <th>Время</th>
            <th>Конфиг</th>
            <th>Запрос</th>
            <th>TX</th>
            <th>RX</th>
            <th>Результат</th>
          </tr>
        </thead>
        <tbody id="exchangeRows">{{ exchange_rows|safe }}</tbody>
      </table>
    </div>
  </div>
  <div class="grid">
    <div class="box"><b>Актуальный статус</b><pre id="status">{{ status_json }}</pre></div>
    <div class="box"><b>Конфиг</b><pre id="config">{{ config_json }}</pre></div>
    <div class="box"><b>TX байты, реальные кадры</b><pre id="txbytes">{{ tx_text }}</pre></div>
    <div class="box"><b>RX байты, реальные кадры или NO RESPONSE</b><pre id="rxbytes">{{ rx_text }}</pre></div>
  </div>
  <div class="box"><b>Полный лог</b><pre id="log">{{ log_json }}</pre></div>
  <script>
    function setText(id, value) {
      var el = document.getElementById(id);
      if (el) el.textContent = value;
    }
    async function readJson(url, options) {
      var response = await fetch(url, Object.assign({cache: 'no-store'}, options || {}));
      var text = await response.text();
      var data = {};
      try { data = text ? JSON.parse(text) : {}; } catch (e) { data = {error: 'non-json', raw: text}; }
      if (!response.ok) throw new Error(data.error || ('HTTP ' + response.status));
      return data;
    }
    function queueText(items, noData) {
      if (!items || !items.length) return noData;
      return items.map(function(item) {
        var parsed = item.parsed || {};
        return '[' + (item.timestamp || '') + '] ' + (item.raw_hex || 'NO RESPONSE') + ' :: ' + (parsed.description || '');
      }).join('\\n');
    }
    function esc(value) {
      return String(value == null ? '' : value).replace(/[&<>"']/g, function(ch) {
        return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch];
      });
    }
    function lineColor(value) {
      var colors = ['#60a5fa', '#22c55e', '#f59e0b', '#f472b6', '#a78bfa', '#14b8a6', '#f97316', '#eab308'];
      var number = parseInt(value || '0', 10);
      if (!number || number < 1) return '#38bdf8';
      return colors[(number - 1) % colors.length];
    }
    function groupClass(value) {
      return ['scan', 'values', 'status'].indexOf(value) >= 0 ? value : '';
    }
    function exchangeRows(items) {
      if (!items || !items.length) return '<tr><td colspan="6" class="muted">нет обменов</td></tr>';
      return items.slice().reverse().map(function(item) {
        var source = item.source || {};
        var lineNo = source.config_line || '';
        var line = lineNo ? ('строка ' + lineNo + ', ') : '';
        var group = source.register_group || '';
        var cfg = line + (source.sensor_name || source.kind || '') + (source.sensor_id ? (' / id=' + source.sensor_id) : '');
        var cls = (item.status === 'ok' ? 'ok' : 'error') + ' line-mark';
        var path = source.config_path ? '<br><span class="muted">' + esc(source.config_path) + '</span>' : '';
        var badge = group ? '<br><span class="badge ' + groupClass(group) + '">' + esc(group) + '</span>' : '';
        return '<tr class="' + cls + '" style="--line-color:' + lineColor(lineNo) + '">' +
          '<td>' + esc(item.timestamp || '') + '</td>' +
          '<td>' + esc(cfg) + path + badge + '</td>' +
          '<td>' + esc(item.request || '') + '<br><span class="muted">slave=' + esc(item.slave_id) + ', f=' + esc(item.function) + ', addr=' + esc(item.start_addr) + ', qty=' + esc(item.quantity) + '</span></td>' +
          '<td class="hex">' + esc(item.tx_hex || '') + '</td>' +
          '<td class="hex">' + esc(item.rx_hex || 'NO RESPONSE') + '</td>' +
          '<td>' + esc(item.result || '') + (item.response_time_ms != null ? '<br><span class="muted">' + esc(item.response_time_ms) + ' ms</span>' : '') + '</td>' +
        '</tr>';
      }).join('');
    }
    async function refreshAll() {
      try {
        var status = await readJson('/api/poller/status');
        var config = await readJson('/api/poller/config');
        var log = await readJson('/api/poller/log?limit=80');
        setText('status', JSON.stringify(status, null, 2));
        setText('config', JSON.stringify(config, null, 2));
        setText('log', JSON.stringify(log, null, 2));
        document.getElementById('exchangeRows').innerHTML = exchangeRows(log.exchange_queue || []);
        setText('txbytes', queueText(log.tx_queue, 'нет TX'));
        setText('rxbytes', queueText(log.rx_queue, 'нет RX'));
        document.getElementById('slave').value = config.device_slave_id || 16;
      } catch (e) {
        setText('status', 'Ошибка обновления: ' + e.message);
      }
    }
    async function postAction(url) {
      try { await readJson(url, {method: 'POST'}); } catch (e) { setText('status', 'Ошибка: ' + e.message); }
      await refreshAll();
    }
    async function saveConfig() {
      var config = await readJson('/api/poller/config');
      config.device_slave_id = parseInt(document.getElementById('slave').value || '16');
      await readJson('/api/poller/config', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(config)});
      await refreshAll();
    }
    async function scanDevices() {
      var start = parseInt(document.getElementById('scanStart').value || '1');
      var end = parseInt(document.getElementById('scanEnd').value || '32');
      var timeout = parseInt(document.getElementById('scanTimeout').value || '500');
      setText('status', 'Сканирование...');
      var result = await readJson('/api/poller/scan?start_id=' + start + '&end_id=' + end + '&timeout_ms=' + timeout);
      setText('status', JSON.stringify(result, null, 2));
      await refreshAll();
    }
    setInterval(refreshAll, 3000);
  </script>
</body>
</html>
"""


def _json_text(payload):
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _queue_text(items, no_data):
    if not items:
        return no_data
    lines = []
    for item in items:
        parsed = item.get("parsed", {})
        lines.append(f"[{item.get('timestamp', '')}] {item.get('raw_hex') or 'NO RESPONSE'} :: {parsed.get('description', '')}")
    return "\n".join(lines)


def _line_color(value):
    colors = ["#60a5fa", "#22c55e", "#f59e0b", "#f472b6", "#a78bfa", "#14b8a6", "#f97316", "#eab308"]
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        number = 0
    if number < 1:
        return "#38bdf8"
    return colors[(number - 1) % len(colors)]


def _group_class(value):
    return value if value in {"scan", "values", "status"} else ""


def _exchange_rows(items):
    if not items:
        return '<tr><td colspan="6" class="muted">нет обменов</td></tr>'
    rows = []
    for item in reversed(items):
        source = item.get("source", {})
        line_no = source.get("config_line")
        line = f"строка {line_no}, " if line_no else ""
        config_text = f"{line}{source.get('sensor_name') or source.get('kind') or ''}"
        if source.get("sensor_id"):
            config_text += f" / id={source.get('sensor_id')}"
        row_class = f"{item.get('status', 'error')} line-mark"
        path_text = source.get("config_path")
        path_line = f'<br><span class="muted">{html.escape(str(path_text))}</span>' if path_text else ""
        group = source.get("register_group", "")
        group_badge = (
            f'<br><span class="badge {html.escape(_group_class(group))}">{html.escape(str(group))}</span>'
            if group
            else ""
        )
        request_meta = f"slave={item.get('slave_id')}, f={item.get('function')}, addr={item.get('start_addr')}, qty={item.get('quantity')}"
        timing = f"<br><span class=\"muted\">{html.escape(str(item.get('response_time_ms')))} ms</span>" if item.get("response_time_ms") is not None else ""
        rows.append(
            "<tr class=\"{row_class}\" style=\"--line-color:{line_color}\">"
            "<td>{timestamp}</td>"
            "<td>{config}{path_line}{group_badge}</td>"
            "<td>{request}<br><span class=\"muted\">{meta}</span></td>"
            "<td class=\"hex\">{tx}</td>"
            "<td class=\"hex\">{rx}</td>"
            "<td>{result}{timing}</td>"
            "</tr>".format(
                row_class=html.escape(row_class),
                line_color=_line_color(line_no),
                timestamp=html.escape(str(item.get("timestamp", ""))),
                config=html.escape(config_text),
                path_line=path_line,
                group_badge=group_badge,
                request=html.escape(str(item.get("request", ""))),
                meta=html.escape(request_meta),
                tx=html.escape(str(item.get("tx_hex") or "")),
                rx=html.escape(str(item.get("rx_hex") or "NO RESPONSE")),
                result=html.escape(str(item.get("result", ""))),
                timing=timing,
            )
        )
    return "\n".join(rows)


@app.route("/")
def index():
    config = load_poller_config()
    status = SERVICE.status()
    log = SERVICE.log_payload(limit=80)
    return render_template_string(
        PAGE_TEMPLATE,
        config=config,
        status_json=_json_text(status),
        config_json=_json_text(config),
        log_json=_json_text(log),
        exchange_rows=_exchange_rows(log.get("exchange_queue")),
        tx_text=_queue_text(log.get("tx_queue"), "нет TX"),
        rx_text=_queue_text(log.get("rx_queue"), "нет RX"),
    )


@app.route("/api/poller/status")
def api_status():
    return jsonify(SERVICE.status())


@app.route("/api/poller/health")
def api_health():
    return jsonify(SERVICE.health())


@app.route("/api/poller/current")
def api_current():
    return jsonify(SERVICE.current_payload())


@app.route("/api/poller/log")
def api_log():
    limit = request.args.get("limit", 100, type=int)
    return jsonify(SERVICE.log_payload(limit=limit))


@app.route("/api/poller/config")
def api_config():
    return jsonify(load_poller_config())


@app.route("/api/poller/config", methods=["POST"])
def api_config_save():
    data = request.get_json(silent=True) or {}
    SERVICE.apply_config(data)
    return jsonify({"status": "ok", "config": load_poller_config()})


@app.route("/api/poller/start", methods=["POST"])
def api_start():
    SERVICE.start()
    return jsonify({"status": "ok"})


@app.route("/api/poller/stop", methods=["POST"])
def api_stop():
    SERVICE.stop()
    return jsonify({"status": "ok"})


@app.route("/api/poller/reload", methods=["POST"])
def api_reload():
    return jsonify(SERVICE.reload_sensors())


@app.route("/api/poller/ports")
def api_ports():
    return jsonify({"ports": SERVICE.available_ports()})


@app.route("/api/poller/scan")
def api_scan():
    start_id = request.args.get("start_id", 1, type=int)
    end_id = request.args.get("end_id", 32, type=int)
    timeout_ms = request.args.get("timeout_ms", 500, type=int)
    return jsonify(SERVICE.scan_devices(start_id=start_id, end_id=end_id, timeout_ms=timeout_ms))


def main():
    parser = argparse.ArgumentParser(description="KVT Modbus Poller")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5001)
    parser.add_argument("--open-browser", default="false")
    args = parser.parse_args()

    cfg = load_poller_config()
    if bool(cfg.get("auto_start", True)):
        SERVICE.start()
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
