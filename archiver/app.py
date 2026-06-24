"""Standalone Archive Manager service.

Port 5002 by default. The visualizer imports the same ArchiveService directly,
so the UI keeps working even when this standalone service is not running.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.python_compat import patch_legacy_werkzeug_ast
patch_legacy_werkzeug_ast()

from flask import Flask, Response, jsonify, request, send_file

from .archive_service import ArchiveService


app = Flask(__name__)
SERVICE = ArchiveService()


def _json_body():
    return request.get_json(silent=True) or {}


@app.route("/")
def index():
    status = SERVICE.status()
    return (
        "<!doctype html><html lang='ru'><meta charset='utf-8'>"
        "<title>KVT Archive Manager</title>"
        "<style>body{font-family:Arial,sans-serif;background:#101827;color:#e5e7eb;padding:18px}"
        "pre{background:#111827;border:1px solid #334155;border-radius:8px;padding:12px;overflow:auto}"
        "button,a{color:#93c5fd}</style>"
        "<h2>KVT Archive Manager</h2>"
        "<p>REST API: /api/archive/status, /api/archive/query, /api/archive/temperature-log, "
        "/api/archive/violations, /api/archive/export</p>"
        f"<pre>{status}</pre></html>"
    )


@app.route("/api/archive/status")
def api_status():
    return jsonify(SERVICE.status())


@app.route("/api/archive/start", methods=["POST"])
def api_start():
    return jsonify(SERVICE.start())


@app.route("/api/archive/stop", methods=["POST"])
def api_stop():
    return jsonify(SERVICE.stop())


@app.route("/api/archive/capture", methods=["POST"])
def api_capture():
    return jsonify(SERVICE.capture_current())


@app.route("/api/archive/query")
def api_query():
    return jsonify(SERVICE.query(
        sensor_id=request.args.get("sensor_id", type=int),
        date_from=request.args.get("from"),
        date_to=request.args.get("to"),
        resolution=request.args.get("resolution", "raw"),
    ))


@app.route("/api/archive/events")
def api_events():
    return jsonify(SERVICE.events(
        sensor_id=request.args.get("sensor_id", type=int),
        event_type=request.args.get("type"),
        date_from=request.args.get("from"),
        date_to=request.args.get("to"),
        limit=request.args.get("limit", 200, type=int),
    ))


@app.route("/api/archive/events/<int:event_id>/ack", methods=["POST"])
def api_ack_event(event_id):
    body = _json_body()
    event = SERVICE.acknowledge_event(
        event_id,
        operator=body.get("operator") or body.get("acknowledged_by") or "operator",
        comment=body.get("comment") or "",
    )
    if not event:
        return jsonify({"error": "Событие не найдено"}), 404
    return jsonify(event)


@app.route("/api/archive/temperature-log")
def api_temperature_log():
    return jsonify(SERVICE.temperature_log(
        sensor_id=request.args.get("sensor_id", type=int),
        period_type=request.args.get("period_type", "day"),
        date_from=request.args.get("from"),
        date_to=request.args.get("to"),
    ))


@app.route("/api/archive/violations")
def api_violations():
    return jsonify(SERVICE.violations(
        sensor_id=request.args.get("sensor_id", type=int),
        date_from=request.args.get("from"),
        date_to=request.args.get("to"),
        status=request.args.get("status", "all"),
        limit=request.args.get("limit", 200, type=int),
    ))


@app.route("/api/archive/violations/<int:violation_id>/ack", methods=["POST"])
def api_ack_violation(violation_id):
    return api_ack_event(violation_id)


@app.route("/api/archive/cleanup", methods=["POST"])
def api_cleanup():
    return jsonify(SERVICE.cleanup())


@app.route("/api/archive/export")
def api_export():
    fmt = request.args.get("format", "json").lower()
    payload = SERVICE.export(
        sensor_id=request.args.get("sensor_id", type=int),
        date_from=request.args.get("from"),
        date_to=request.args.get("to"),
        fmt=fmt,
        resolution=request.args.get("resolution", "raw"),
    )
    if fmt == "csv":
        return Response(
            payload,
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=archive-export.csv"},
        )
    return jsonify(payload)


@app.route("/api/archive/daily")
def api_daily():
    return jsonify(SERVICE.load_daily_view())


@app.route("/api/archive/config", methods=["GET", "POST"])
def api_config():
    if request.method == "GET":
        return jsonify(SERVICE.config())
    try:
        return jsonify(SERVICE.save_config(_json_body()))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


def main():
    parser = argparse.ArgumentParser(description="KVT Archive Manager")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5002)
    parser.add_argument("--no-auto-start", action="store_true")
    args = parser.parse_args()

    if not args.no_auto_start:
        SERVICE.start()
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
