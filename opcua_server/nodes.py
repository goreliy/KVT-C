"""Stable OPC UA node identifiers and sensor selection helpers."""


SENSOR_FIELDS = {
    "temperature": "Temperature",
    "humidity": "Humidity",
    "combined_status": "CombinedStatus",
    "timestamp": "Timestamp",
    "poll_port_id": "PollPortId",
    "poll_port_name": "PollPortName",
    "transport": "Transport",
    "temp_min": "TempMin",
    "temp_max": "TempMax",
    "hum_min": "HumMin",
    "hum_max": "HumMax",
}


def sensor_node_id(sensor_id, field_name):
    return f"KVT.Sensors.{int(sensor_id)}.{field_name}"


def sensor_object_node_id(sensor_id):
    return f"KVT.Sensors.{int(sensor_id)}"


def poll_port_object_node_id(port_id):
    return f"KVT.PollPorts.{_node_token(port_id)}"


def poll_port_node_id(port_id, field_name):
    return f"KVT.PollPorts.{_node_token(port_id)}.{field_name}"


def _node_token(value):
    text = str(value or "default").strip() or "default"
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in text)


def selected_sensors(current_payload, opcua_config):
    sensors = list((current_payload or {}).get("sensors") or [])
    selected_ids = opcua_config.get("selection", {}).get("sensor_ids") or []
    if selected_ids:
        selected = {int(item) for item in selected_ids}
        sensors = [sensor for sensor in sensors if int(sensor.get("id")) in selected]
    if opcua_config.get("publishing", {}).get("publish_only_enabled_sensors", True):
        sensors = [sensor for sensor in sensors if sensor.get("enabled", True)]
    return sensors
