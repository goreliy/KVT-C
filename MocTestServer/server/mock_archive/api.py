"""
REST API для Mock Archive Server
"""

from flask import Blueprint, jsonify, request, Response
from .server import ArchiveServer

archive_api = Blueprint('archive_api', __name__, url_prefix='/api/archive')

_server: ArchiveServer = None


def init_server(config=None):
    global _server
    _server = ArchiveServer(config)
    return _server


def get_server() -> ArchiveServer:
    global _server
    if _server is None:
        _server = ArchiveServer()
    return _server


@archive_api.route('/status', methods=['GET'])
def get_status():
    return jsonify(get_server().get_status())


@archive_api.route('/start', methods=['POST'])
def start():
    get_server().start()
    return jsonify({"status": "ok", "message": "Archive server started"})


@archive_api.route('/stop', methods=['POST'])
def stop():
    get_server().stop()
    return jsonify({"status": "ok", "message": "Archive server stopped"})


@archive_api.route('/query', methods=['GET'])
def query():
    sensor_id = request.args.get('sensor_id', 1, type=int)
    from_time = request.args.get('from')
    to_time = request.args.get('to')
    resolution = request.args.get('resolution', 'minute')

    if not from_time or not to_time:
        return jsonify({"error": "from and to parameters required"}), 400

    return jsonify(get_server().query(sensor_id, from_time, to_time, resolution))


@archive_api.route('/events', methods=['GET'])
def get_events():
    return jsonify(get_server().get_events(
        from_time=request.args.get('from'),
        to_time=request.args.get('to'),
        sensor_id=request.args.get('sensor_id', type=int),
        event_type=request.args.get('event_type'),
        priority=request.args.get('priority'),
        acknowledged=request.args.get('acknowledged', type=lambda x: x.lower() == 'true')
                     if request.args.get('acknowledged') else None,
        limit=request.args.get('limit', 100, type=int),
        offset=request.args.get('offset', 0, type=int)
    ))


@archive_api.route('/events/<int:event_id>/acknowledge', methods=['POST'])
def acknowledge_event(event_id):
    params = request.get_json() or {}
    result = get_server().acknowledge_event(event_id, params.get('user', 'operator'))
    if result:
        return jsonify(result)
    return jsonify({"error": "Event not found"}), 404


@archive_api.route('/export', methods=['GET'])
def export_data():
    sensor_id = request.args.get('sensor_id', 1, type=int)
    from_time = request.args.get('from')
    to_time = request.args.get('to')
    fmt = request.args.get('format', 'json')

    if not from_time or not to_time:
        return jsonify({"error": "from and to parameters required"}), 400

    result = get_server().export_data(sensor_id, from_time, to_time, fmt)
    if fmt == 'csv':
        return Response(result, mimetype='text/csv',
                        headers={'Content-Disposition': f'attachment; filename=sensor_{sensor_id}.csv'})
    return jsonify(result)


@archive_api.route('/config', methods=['GET'])
def get_config():
    return jsonify(get_server().config)


@archive_api.route('/config', methods=['POST'])
def update_config():
    new_config = request.get_json()
    if not new_config:
        return jsonify({"error": "No config provided"}), 400
    get_server().update_config(new_config)
    return jsonify({"status": "ok"})


@archive_api.route('/regenerate', methods=['POST'])
def regenerate():
    get_server().regenerate()
    return jsonify({"status": "ok", "message": "Data regenerated"})


@archive_api.route('/cleanup', methods=['POST'])
def cleanup():
    params = request.get_json() or {}
    return jsonify(get_server().cleanup(params.get('days_to_keep', 7)))


@archive_api.route('/save', methods=['POST'])
def save_to_file():
    get_server().save_to_file()
    return jsonify({"status": "ok", "message": "Archive saved to file"})


# ── Журнал температур ──

@archive_api.route('/temperature-log', methods=['GET'])
def get_temperature_log():
    """GET /api/archive/temperature-log - Журнал температур/влажности по периодам"""
    return jsonify(get_server().get_temperature_log(
        sensor_id=request.args.get('sensor_id', type=int),
        period_type=request.args.get('period_type', 'hour'),
        from_time=request.args.get('from'),
        to_time=request.args.get('to')
    ))


# ── Журнал превышений ──

@archive_api.route('/violations', methods=['GET'])
def get_violations():
    """GET /api/archive/violations - Журнал превышений границ"""
    return jsonify(get_server().get_violations(
        sensor_id=request.args.get('sensor_id', type=int),
        from_time=request.args.get('from'),
        to_time=request.args.get('to'),
        status=request.args.get('status', 'all'),
        parameter=request.args.get('parameter'),
        acknowledged=request.args.get('acknowledged', type=lambda x: x.lower() == 'true')
                     if request.args.get('acknowledged') else None,
        limit=request.args.get('limit', 100, type=int),
        offset=request.args.get('offset', 0, type=int)
    ))


@archive_api.route('/violations/<int:violation_id>/acknowledge', methods=['POST'])
def acknowledge_violation(violation_id):
    """POST /api/archive/violations/{id}/ack - Квитировать превышение"""
    params = request.get_json() or {}
    result = get_server().acknowledge_violation(
        violation_id, params.get('user', 'operator'), params.get('comment')
    )
    if result:
        return jsonify(result)
    return jsonify({"error": "Violation not found"}), 404
