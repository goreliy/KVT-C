import argparse
import os
import sys

from flask import Flask, jsonify, request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.config_manager import load_poller_config
from .poller_service import PollerService

app = Flask(__name__)
SERVICE = PollerService()


@app.route("/")
def index():
    return """
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>KVT Poller Service</title>
  <style>
    body{font-family:Arial,sans-serif;background:#0f172a;color:#e2e8f0;margin:0;padding:20px}
    .box{background:#111827;border:1px solid #334155;border-radius:10px;padding:14px;margin-bottom:12px}
    .row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
    input,button{padding:8px;border-radius:8px;border:1px solid #475569;background:#1e293b;color:#e2e8f0}
    button{cursor:pointer}
    pre{white-space:pre-wrap;max-height:320px;overflow:auto}
  </style>
</head>
<body>
  <h2>KVT Modbus Poller</h2>
  <div class="box">
    <div class="row">
      <button onclick="callPost('/api/poller/start')">Старт</button>
      <button onclick="callPost('/api/poller/stop')">Стоп</button>
      <button onclick="callPost('/api/poller/reload')">Reload датчиков</button>
      <button onclick="refreshAll()">Обновить</button>
    </div>
  </div>
  <div class="box">
    <div class="row">
      <label>Slave ID устройства:</label>
      <input id="slave" type="number" min="1" max="247" value="16">
      <button onclick="saveConfig()">Сохранить Slave ID</button>
    </div>
    <div class="row" style="margin-top:8px">
      <label>Скан: </label>
      <input id="scanStart" type="number" min="1" max="247" value="1">
      <input id="scanEnd" type="number" min="1" max="247" value="32">
      <input id="scanTimeout" type="number" min="50" max="10000" value="500">
      <button onclick="scan()">Поиск устройств</button>
    </div>
  </div>
  <div class="box"><b>Статус</b><pre id="status">...</pre></div>
  <div class="box"><b>Конфиг</b><pre id="config">...</pre></div>
  <div class="box"><b>TX байты (последние 30)</b><pre id="txbytes">...</pre></div>
  <div class="box"><b>RX байты (последние 30)</b><pre id="rxbytes">...</pre></div>
  <div class="box"><b>Лог (последние 30)</b><pre id="log">...</pre></div>
  <script>
    function setText(id, value){
      var el = document.getElementById(id);
      if (el) el.textContent = value;
    }
    async function getJson(url){
      var r = await fetch(url, {cache:'no-store'});
      var txt = await r.text();
      var payload = {};
      try { payload = txt ? JSON.parse(txt) : {}; } catch (e) { payload = {error: 'non-json response', raw: txt.slice(0,300)}; }
      if(!r.ok){ throw new Error(payload.error || ('HTTP ' + r.status)); }
      return payload;
    }
    async function callPost(url){
      try { await getJson(url); } catch(e){ setText('status', 'Ошибка: ' + e.message); }
      await refreshAll();
    }
    async function saveConfig(){
      const cfg=await getJson('/api/poller/config');
      cfg.device_slave_id=parseInt(document.getElementById('slave').value||'16');
      await fetch('/api/poller/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(cfg)});
      await refreshAll();
    }
    async function scan(){
      var s=parseInt(document.getElementById('scanStart').value||'1');
      var e=parseInt(document.getElementById('scanEnd').value||'32');
      var t=parseInt(document.getElementById('scanTimeout').value||'500');
      setText('status', 'Сканирование...');
      var r=await getJson('/api/poller/scan?start_id=' + s + '&end_id=' + e + '&timeout_ms=' + t);
      setText('status', JSON.stringify(r,null,2));
    }
    async function refreshAll(){
      try{
        setText('status', 'Загрузка...');
        var results = await Promise.all([
          getJson('/api/poller/status'),
          getJson('/api/poller/config'),
          getJson('/api/poller/log?limit=30')
        ]);
        var st = results[0], cfg = results[1], lg = results[2];
        setText('status', JSON.stringify(st,null,2));
        setText('config', JSON.stringify(cfg,null,2));
        setText('log', JSON.stringify(lg,null,2));
        var txQueue = lg.tx_queue || [];
        var rxQueue = lg.rx_queue || [];
        var tx = txQueue.map(function(x){
          var d = (x.parsed && x.parsed.description) ? x.parsed.description : '';
          return '[' + (x.timestamp || '') + '] ' + (x.raw_hex || '') + ' :: ' + d;
        }).join('\n');
        var rx = rxQueue.map(function(x){
          var d = (x.parsed && x.parsed.description) ? x.parsed.description : '';
          return '[' + (x.timestamp || '') + '] ' + (x.raw_hex || 'NO RESPONSE') + ' :: ' + d;
        }).join('\n');
        setText('txbytes', tx || 'нет TX');
        setText('rxbytes', rx || 'нет RX');
        document.getElementById('slave').value = cfg.device_slave_id || 16;
      } catch(e){
        setText('status', 'Ошибка обновления: ' + e.message);
      }
    }
    window.addEventListener('load', function(){
      setText('status', 'Инициализация UI...');
      refreshAll();
      setInterval(refreshAll, 5000);
    });
  </script>
</body>
</html>
"""


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
