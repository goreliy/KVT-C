"""MQTT bridge runtime for publishing and receiving KVT-C sensor data."""
import json
import os
import threading
import time
from datetime import datetime, timezone

from shared.config_manager import (
    atomic_save_json,
    load_mqtt_config,
    load_mqtt_password,
    load_runtime_json,
)
from shared.current_data import load_current_payload
from shared.net import resolve_self_host

try:
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover - exercised on deployments missing the optional dependency.
    mqtt = None


from shared.paths import app_root as _app_root, data_dir as _data_dir

ROOT_DIR = _app_root()
DATA_DIR = _data_dir()
STATUS_PATH = os.path.join(DATA_DIR, "mqtt_status.json")
INBOUND_PATH = os.path.join(DATA_DIR, "mqtt_inbound.json")


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_base_topic(config_or_topic):
    if isinstance(config_or_topic, dict):
        topic = ((config_or_topic.get("topics") or {}).get("base") or "kvt-c")
    else:
        topic = config_or_topic or "kvt-c"
    topic = str(topic).strip().strip("/")
    while "//" in topic:
        topic = topic.replace("//", "/")
    return topic or "kvt-c"


def topic_join(*parts):
    return "/".join(str(part).strip("/") for part in parts if str(part).strip("/"))


def mqtt_topic_map(config):
    base = normalize_base_topic(config)
    return {
        "base": base,
        "status": topic_join(base, "status"),
        "current": topic_join(base, "current"),
        "sensor_prefix": topic_join(base, "sensors"),
        "inbound_sensor_filter": topic_join(base, "inbound", "sensors", "+"),
        "command_republish": topic_join(base, "commands", "republish"),
        "command_ping": topic_join(base, "commands", "ping"),
    }


def sensor_topic(config, sensor_id):
    return topic_join(mqtt_topic_map(config)["sensor_prefix"], int(sensor_id))


def config_signature(config):
    return json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def json_payload(payload):
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def build_snapshot_payload(current=None):
    return dict(current if current is not None else load_current_payload())


def build_sensor_payloads(current):
    result = {}
    for sensor in (current or {}).get("sensors") or []:
        sid = sensor.get("id")
        if sid is None:
            continue
        result[int(sid)] = dict(sensor)
    return result


def _status_payload(config, state, connected=False, message="", error=None, **extra):
    broker = config.get("broker") or {}
    topics = mqtt_topic_map(config)
    payload = {
        "service": "mqtt",
        "updated_at": utc_now(),
        "state": state,
        "enabled": bool(config.get("enabled")),
        "autostart": bool(config.get("autostart")),
        "connected": bool(connected),
        "broker": {
            "host": broker.get("host"),
            "port": broker.get("port"),
            "client_id": broker.get("client_id"),
            "username_set": bool(broker.get("username")),
        },
        "base_topic": topics["base"],
        "topics": {
            "status": topics["status"],
            "current": topics["current"],
            "sensors": topic_join(topics["sensor_prefix"], "<sensor_id>"),
            "inbound_sensors": topics["inbound_sensor_filter"],
            "command_republish": topics["command_republish"],
            "command_ping": topics["command_ping"],
        },
        "message": message,
    }
    if error:
        payload["error"] = str(error)
    payload.update(extra)
    return payload


def write_status(config, state, connected=False, message="", error=None, **extra):
    payload = _status_payload(config, state, connected=connected, message=message, error=error, **extra)
    atomic_save_json(STATUS_PATH, payload, encoding="utf-8", indent=2)
    return payload


def load_inbound_payload():
    return load_runtime_json(INBOUND_PATH, default={
        "service": "mqtt",
        "updated_at": None,
        "sensors": {},
        "commands": [],
    })


def _bounded_commands(commands, limit=50):
    return list(commands or [])[-limit:]


def store_inbound_sensor(sensor_id, payload, topic, received_at=None):
    if not isinstance(payload, dict):
        raise ValueError("MQTT inbound sensor payload must be a JSON object")
    received_at = received_at or utc_now()
    data = load_inbound_payload()
    data["service"] = "mqtt"
    data["updated_at"] = received_at
    sensors = data.setdefault("sensors", {})
    entry = dict(payload)
    entry["id"] = int(sensor_id)
    entry["topic"] = topic
    entry["received_at"] = received_at
    sensors[str(int(sensor_id))] = entry
    data["commands"] = _bounded_commands(data.get("commands"))
    atomic_save_json(INBOUND_PATH, data, encoding="utf-8", indent=2)
    return entry


def store_command(command, payload, topic, received_at=None):
    received_at = received_at or utc_now()
    data = load_inbound_payload()
    commands = _bounded_commands(data.get("commands"))
    commands.append({
        "command": command,
        "topic": topic,
        "payload": payload,
        "received_at": received_at,
    })
    data["service"] = "mqtt"
    data["updated_at"] = received_at
    data["commands"] = _bounded_commands(commands)
    data.setdefault("sensors", {})
    atomic_save_json(INBOUND_PATH, data, encoding="utf-8", indent=2)
    return data["commands"][-1]


def _decode_json_payload(raw):
    text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw or "")
    text = text.strip()
    if not text:
        return {}
    return json.loads(text)


def _reason_ok(reason_code):
    if hasattr(reason_code, "is_failure"):
        return not reason_code.is_failure
    try:
        return int(reason_code) == 0
    except (TypeError, ValueError):
        return str(reason_code).lower() in {"0", "success"}


class MqttBridgeService:
    def __init__(self, overrides=None, client_factory=None):
        self.overrides = overrides or {}
        self.client_factory = client_factory
        self._stop = threading.Event()
        self._connected = False
        self._republish_requested = False
        self._client = None
        self._lock = threading.Lock()

    def stop(self, *_args):
        self._stop.set()

    def _effective_config(self):
        config = load_mqtt_config()
        broker = config.setdefault("broker", {})
        for key, value in self.overrides.items():
            if value is not None:
                broker[key] = value
        return config

    def run_forever(self):
        while not self._stop.is_set():
            config = self._effective_config()
            if not config.get("enabled"):
                write_status(config, "disabled", connected=False, message="MQTT bridge is disabled in mqtt_config.json")
                self._wait(2.0)
                continue
            try:
                self._serve_enabled(config)
            except Exception as exc:
                write_status(config, "error", connected=False, message="MQTT bridge failed", error=exc)
                self._wait(3.0)

    def _wait(self, seconds):
        self._stop.wait(seconds)

    def _serve_enabled(self, config):
        if mqtt is None:
            raise RuntimeError("paho-mqtt is not installed; run pip install -r requirements.txt")

        self._connected = False
        self._republish_requested = False
        signature = config_signature(config)
        topics = mqtt_topic_map(config)
        broker = config.get("broker") or {}
        publishing = config.get("publishing") or {}
        interval = max(0.25, int(publishing.get("interval_ms") or 1000) / 1000.0)
        last_publish = 0.0

        client = self._make_client(config)
        self._client = client
        write_status(config, "connecting", connected=False, message="Connecting to MQTT broker")
        client.connect_async(
            resolve_self_host(broker.get("host")),
            int(broker.get("port") or 1883),
            keepalive=int(broker.get("keepalive_seconds") or 60),
        )
        client.loop_start()
        try:
            while not self._stop.is_set():
                latest = self._effective_config()
                if config_signature(latest) != signature:
                    write_status(config, "reloading", connected=self._connected, message="MQTT config changed; reconnecting")
                    break
                if self._connected and publishing.get("enabled", True):
                    now = time.monotonic()
                    if self._republish_requested or now - last_publish >= interval:
                        exported = self.publish_current(client, config)
                        last_publish = now
                        self._republish_requested = False
                        write_status(
                            config,
                            "running",
                            connected=True,
                            message="MQTT bridge is publishing current sensor values",
                            exported_sensor_count=exported,
                        )
                self._wait(0.2)
        finally:
            try:
                if self._connected:
                    self._publish_status(client, config, "stopped", "MQTT bridge is stopping")
            finally:
                try:
                    client.disconnect()
                finally:
                    client.loop_stop()
                self._connected = False
                self._client = None

    def _make_client(self, config):
        broker = config.get("broker") or {}
        client_id = broker.get("client_id") or "kvt-c-mqtt"
        if self.client_factory is not None:
            client = self.client_factory(config)
        elif hasattr(mqtt, "CallbackAPIVersion"):
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        else:
            client = mqtt.Client(client_id=client_id)

        topics = mqtt_topic_map(config)
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        client.will_set(
            topics["status"],
            json_payload(_status_payload(config, "offline", connected=False, message="MQTT bridge disconnected unexpectedly")),
            qos=int((config.get("publishing") or {}).get("qos") or 0),
            retain=True,
        )
        username = broker.get("username") or None
        password = load_mqtt_password() or None
        if username or password:
            client.username_pw_set(username, password)

        tls = config.get("tls") or {}
        if tls.get("enabled"):
            client.tls_set(
                ca_certs=tls.get("ca_cert_path") or None,
                certfile=tls.get("client_cert_path") or None,
                keyfile=tls.get("client_key_path") or None,
            )
            if tls.get("insecure"):
                client.tls_insecure_set(True)

        client.on_connect = self._on_connect(config)
        client.on_disconnect = self._on_disconnect(config)
        client.on_message = self._on_message(config)
        return client

    def _on_connect(self, config):
        def callback(client, _userdata, _flags, reason_code, _properties=None):
            if not _reason_ok(reason_code):
                self._connected = False
                write_status(config, "error", connected=False, message="MQTT broker rejected connection", error=reason_code)
                return
            self._connected = True
            receiving = config.get("receiving") or {}
            topics = mqtt_topic_map(config)
            if receiving.get("enabled", True):
                qos = int(receiving.get("qos") or 0)
                client.subscribe(topics["inbound_sensor_filter"], qos=qos)
                client.subscribe(topics["command_republish"], qos=qos)
                client.subscribe(topics["command_ping"], qos=qos)
            self._publish_status(client, config, "running", "MQTT bridge connected")
            write_status(config, "running", connected=True, message="MQTT bridge connected")
            self._republish_requested = True
        return callback

    def _on_disconnect(self, config):
        def callback(_client, _userdata, *args):
            reason_code = args[-2] if len(args) >= 2 else (args[-1] if args else None)
            self._connected = False
            state = "stopped" if self._stop.is_set() else "disconnected"
            message = "MQTT bridge stopped" if self._stop.is_set() else "MQTT broker disconnected; reconnecting"
            write_status(config, state, connected=False, message=message, error=None if self._stop.is_set() else reason_code)
        return callback

    def _on_message(self, config):
        def callback(client, _userdata, message):
            topic = str(message.topic or "")
            topics = mqtt_topic_map(config)
            try:
                payload = _decode_json_payload(message.payload)
                if topic == topics["command_republish"]:
                    store_command("republish", payload, topic)
                    self._republish_requested = True
                    write_status(config, "running", connected=self._connected, message="MQTT republish command received")
                    return
                if topic == topics["command_ping"]:
                    store_command("ping", payload, topic)
                    self._publish_status(client, config, "running", "MQTT ping command received")
                    return
                prefix = topic_join(topics["base"], "inbound", "sensors") + "/"
                if topic.startswith(prefix):
                    sensor_id = topic[len(prefix):].split("/", 1)[0]
                    store_inbound_sensor(int(sensor_id), payload, topic)
                    write_status(config, "running", connected=self._connected, message=f"MQTT inbound sensor {sensor_id} received")
                    return
            except Exception as exc:
                write_status(config, "running", connected=self._connected, message="MQTT inbound message rejected", error=exc)
        return callback

    def _publish_status(self, client, config, state, message):
        topics = mqtt_topic_map(config)
        payload = _status_payload(config, state, connected=self._connected, message=message)
        client.publish(
            topics["status"],
            json_payload(payload),
            qos=int((config.get("publishing") or {}).get("qos") or 0),
            retain=True,
        )

    def publish_current(self, client, config):
        current = build_snapshot_payload()
        topics = mqtt_topic_map(config)
        publishing = config.get("publishing") or {}
        qos = int(publishing.get("qos") or 0)
        retain = bool(publishing.get("retain", True))
        sensors = build_sensor_payloads(current)
        if publishing.get("publish_snapshot", True):
            client.publish(topics["current"], json_payload(current), qos=qos, retain=retain)
        if publishing.get("publish_per_sensor", True):
            for sid, payload in sensors.items():
                client.publish(sensor_topic(config, sid), json_payload(payload), qos=qos, retain=retain)
        return len(sensors)
