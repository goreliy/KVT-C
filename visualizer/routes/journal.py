"""Журналы: события, температуры, превышения и складской журнал учёта."""
import os
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, render_template, request

from archiver.archive_service import ArchiveService
from shared.config_manager import atomic_save_json, load_runtime_json
from shared.logbook import LogbookError, load_reports_config, print_context


journal_bp = Blueprint("journal", __name__)

from shared.paths import data_dir as _data_dir

DATA_DIR = _data_dir()
ARCHIVE_SERVICE = ArchiveService()


def _load_json(filename):
    path = os.path.join(DATA_DIR, filename)
    return load_runtime_json(path, default={})


def _save_json(filename, data):
    path = os.path.join(DATA_DIR, filename)
    atomic_save_json(path, data)


@journal_bp.route("/events")
def events_page():
    return render_template("journal/events.html")


@journal_bp.route("/journal/temperatures")
def temperatures_page():
    return render_template("journal/temperatures.html")


@journal_bp.route("/journal/violations")
def violations_page():
    return render_template("journal/violations.html")


@journal_bp.route("/logbook")
def logbook_page():
    return render_template("logbook.html")


@journal_bp.route("/logbook/<report_id>/print")
def logbook_print_page(report_id):
    try:
        context = print_context(
            report_id,
            year=request.args.get("year", type=int),
            month=request.args.get("month", type=int),
        )
    except LogbookError as exc:
        reports = load_reports_config().get("reports") or []
        fallback = reports[0].get("id") if reports else None
        if fallback and fallback != report_id:
            context = print_context(fallback)
        else:
            return str(exc), 400
    return render_template("logbook_print.html", **context)


@journal_bp.route("/api/journal/events")
def api_events():
    """Events with filtering by sensor_id, type, date range, limit."""
    events_data = _load_json("events.json")
    events = events_data.get("events", [])

    sensor_id = request.args.get("sensor_id", type=int)
    event_type = request.args.get("type")
    date_from = request.args.get("from")
    date_to = request.args.get("to")
    limit = request.args.get("limit", 50, type=int)

    if sensor_id:
        events = [e for e in events if e.get("sensor_id") == sensor_id]
    if event_type:
        events = [e for e in events if event_type in e.get("event_type", "")]
    if date_from:
        try:
            dt_from = datetime.fromisoformat(date_from)
            events = [e for e in events if datetime.fromisoformat(e["timestamp"]) >= dt_from]
        except (ValueError, KeyError):
            pass
    if date_to:
        try:
            dt_to = datetime.fromisoformat(date_to)
            events = [e for e in events if datetime.fromisoformat(e["timestamp"]) <= dt_to]
        except (ValueError, KeyError):
            pass

    events.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    return jsonify({"total": len(events), "events": events[:limit]})


@journal_bp.route("/api/journal/events/<int:event_id>/ack", methods=["POST"])
def api_ack_event(event_id):
    """Acknowledge an event."""
    events_data = _load_json("events.json")
    events = events_data.get("events", [])

    body = request.get_json(silent=True) or {}
    operator = body.get("operator", "operator")
    comment = body.get("comment", "")

    for event in events:
        if event.get("id") == event_id:
            event["acknowledged"] = True
            event["acknowledged_at"] = datetime.now().isoformat()
            event["acknowledged_by"] = operator
            if comment:
                event["comment"] = comment
            _save_json("events.json", events_data)
            return jsonify(event)

    return jsonify({"error": "Событие не найдено"}), 404


@journal_bp.route("/api/journal/temperatures")
def api_temperatures():
    """Aggregated min/max/avg per sensor for a given period type."""
    period = request.args.get("period", "day")  # hour, day, week
    sensor_id = request.args.get("sensor_id", type=int)

    now = datetime.now()
    if period == "hour":
        dt_from = now - timedelta(hours=24)
    elif period == "week":
        dt_from = now - timedelta(days=30)
    else:
        dt_from = now - timedelta(days=7)

    payload = ARCHIVE_SERVICE.temperature_log(
        sensor_id=sensor_id,
        period_type=period,
        date_from=dt_from.isoformat(),
        date_to=now.isoformat(),
    )
    return jsonify({
        "data": payload.get("data", []),
        "period_type": period,
        "total": payload.get("total", 0),
    })


@journal_bp.route("/api/journal/violations")
def api_violations():
    """Threshold violations with duration, peak values, ack status."""
    return jsonify(ARCHIVE_SERVICE.violations(
        sensor_id=request.args.get("sensor_id", type=int),
        date_from=request.args.get("from"),
        date_to=request.args.get("to"),
        status=request.args.get("status", "all"),
        limit=request.args.get("limit", 50, type=int),
    ))


@journal_bp.route("/api/journal/violations/<int:violation_id>/ack", methods=["POST"])
def api_ack_violation(violation_id):
    """Acknowledge a violation (same as ack event since violations are events)."""
    return api_ack_event(violation_id)
