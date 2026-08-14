"""Async OPC UA server runtime."""
import asyncio
import json
import os
from datetime import datetime, timezone

from shared.config_manager import atomic_save_json, load_opcua_config
from shared.current_data import load_current_payload
from shared.net import resolve_self_host
from opcua_server.nodes import (
    poll_port_node_id,
    poll_port_object_node_id,
    selected_sensors,
    sensor_node_id,
    sensor_object_node_id,
)

try:
    from asyncua import Server, ua
except ImportError:  # pragma: no cover - exercised on deployments missing the optional service dependency.
    Server = None
    ua = None


from shared.paths import app_root as _app_root

ROOT_DIR = _app_root()
STATUS_PATH = os.path.join(ROOT_DIR, "data", "opcua_status.json")


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def endpoint_from_config(config):
    server = config.get("server") or {}
    host = str(server.get("host") or "0.0.0.0").strip() or "0.0.0.0"
    port = int(server.get("port") or 4840)
    path = str(server.get("endpoint_path") or "/kvt/")
    if not path.startswith("/"):
        path = "/" + path
    if not path.endswith("/"):
        path += "/"
    return f"opc.tcp://{host}:{port}{path}"


def advertised_endpoint_from_config(config):
    """Endpoint, публикуемый клиентам. host 0.0.0.0/localhost/пусто заменяется на
    реальный IP машины: OPC UA клиенты после discovery подключаются по адресу ИЗ
    ответа сервера, и объявленный 0.0.0.0 снаружи недостижим — поэтому наружу
    всегда объявляем актуальный IP."""
    server = config.get("server") or {}
    host = resolve_self_host(server.get("host"))
    port = int(server.get("port") or 4840)
    path = str(server.get("endpoint_path") or "/kvt/")
    if not path.startswith("/"):
        path = "/" + path
    if not path.endswith("/"):
        path += "/"
    return f"opc.tcp://{host}:{port}{path}"


def config_signature(config):
    return json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _write_status(payload):
    data = {
        "service": "opcua",
        "updated_at": utc_now(),
        **payload,
    }
    atomic_save_json(STATUS_PATH, data, encoding="utf-8", indent=2)
    return data


def _metric_value(sensor, metric_name):
    metric = sensor.get(metric_name) or {}
    return metric.get("value"), metric.get("value") is not None


def _limit_value(sensor, group, key):
    limits = sensor.get(group) or {}
    value = limits.get(key)
    try:
        return float(value), value is not None
    except (TypeError, ValueError):
        return None, False


class OpcUaService:
    def __init__(self, overrides=None):
        self.overrides = overrides or {}
        self._stop = asyncio.Event()
        self._namespace_idx = None
        self._objects = {}
        self._variables = {}

    def stop(self):
        self._stop.set()

    def _effective_config(self):
        config = load_opcua_config()
        server = config.setdefault("server", {})
        for key, value in self.overrides.items():
            if value is not None:
                server[key] = value
        return config

    async def run_forever(self):
        while not self._stop.is_set():
            config = self._effective_config()
            endpoint = advertised_endpoint_from_config(config)
            if not config.get("enabled"):
                _write_status({
                    "state": "disabled",
                    "enabled": False,
                    "endpoint": endpoint,
                    "namespace_uri": (config.get("server") or {}).get("namespace_uri"),
                    "exported_sensor_count": 0,
                    "message": "OPC UA server is disabled in opcua_config.json",
                })
                await self._sleep(2.0)
                continue
            try:
                await self._serve_enabled(config)
            except Exception as exc:
                _write_status({
                    "state": "error",
                    "enabled": True,
                    "endpoint": endpoint,
                    "namespace_uri": (config.get("server") or {}).get("namespace_uri"),
                    "exported_sensor_count": 0,
                    "error": str(exc),
                })
                await self._sleep(3.0)

    async def _sleep(self, seconds):
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def _serve_enabled(self, config):
        if Server is None:
            raise RuntimeError("asyncua is not installed; run pip install -r requirements.txt")
        security = config.get("security") or {}
        if security.get("mode") != "anonymous_readonly":
            raise RuntimeError("Only anonymous_readonly OPC UA mode is implemented in this service slice")

        self._objects = {}
        self._variables = {}
        server_cfg = config.get("server") or {}
        # Клиентам объявляем endpoint с реальным IP машины (0.0.0.0 снаружи недостижим),
        # а сокет привязываем к сконфигурированному host (обычно 0.0.0.0 = все интерфейсы).
        endpoint = advertised_endpoint_from_config(config)
        bind_host = str(server_cfg.get("host") or "0.0.0.0").strip() or "0.0.0.0"
        bind_port = int(server_cfg.get("port") or 4840)
        signature = config_signature(config)

        server = Server()
        await server.init()
        server.set_endpoint(endpoint)
        try:
            # asyncua: отдельный адрес привязки сокета (иначе привязка идёт по host из endpoint)
            server.socket_address = (bind_host, bind_port)
        except Exception:
            pass
        try:
            # Подставлять в ответ discovery адрес, по которому клиент реально обратился
            server.set_match_discovery_client_ip(True)
        except Exception:
            pass
        server.set_server_name(server_cfg.get("server_name") or "KVT-C OPC UA Server")
        self._namespace_idx = await server.register_namespace(server_cfg.get("namespace_uri") or "urn:kvt:c:monitoring")
        await self._build_static_tree(server, config)

        async with server:
            _write_status({
                "state": "running",
                "enabled": True,
                "endpoint": endpoint,
                "namespace_uri": server_cfg.get("namespace_uri"),
                "namespace_index": self._namespace_idx,
                "exported_sensor_count": 0,
                "message": "OPC UA server started",
            })
            while not self._stop.is_set():
                latest_config = self._effective_config()
                if (config_signature(latest_config) != signature
                        or advertised_endpoint_from_config(latest_config) != endpoint):
                    # Изменился конфиг ИЛИ IP машины — пересоздаём endpoint/адресное пространство.
                    _write_status({
                        "state": "reloading",
                        "enabled": True,
                        "endpoint": endpoint,
                        "namespace_uri": server_cfg.get("namespace_uri"),
                        "exported_sensor_count": 0,
                        "message": "OPC UA config or IP changed; restarting address space",
                    })
                    break
                current = load_current_payload()
                exported = await self._publish_current(config, current)
                _write_status({
                    "state": "running",
                    "enabled": True,
                    "endpoint": endpoint,
                    "namespace_uri": server_cfg.get("namespace_uri"),
                    "namespace_index": self._namespace_idx,
                    "exported_sensor_count": exported,
                    "source_timestamp": current.get("timestamp"),
                    "message": "OPC UA server is publishing current sensor values",
                })
                interval_ms = int((config.get("publishing") or {}).get("update_interval_ms") or 1000)
                await self._sleep(max(0.25, interval_ms / 1000.0))

    async def _build_static_tree(self, server, config):
        idx = self._namespace_idx
        objects = server.nodes.objects
        root = await objects.add_object(ua.NodeId("KVT", idx), "KVT")
        system = await root.add_object(ua.NodeId("KVT.System", idx), "System")
        poll_ports = await root.add_object(ua.NodeId("KVT.PollPorts", idx), "PollPorts")
        sensors = await root.add_object(ua.NodeId("KVT.Sensors", idx), "Sensors")
        self._objects["root"] = root
        self._objects["system"] = system
        self._objects["poll_ports"] = poll_ports
        self._objects["sensors"] = sensors

        server_cfg = config.get("server") or {}
        await self._add_variable("system.server_name", system, "KVT.System.ServerName", "ServerName", server_cfg.get("server_name") or "", ua.VariantType.String)
        await self._add_variable("system.namespace_uri", system, "KVT.System.NamespaceUri", "NamespaceUri", server_cfg.get("namespace_uri") or "", ua.VariantType.String)
        await self._add_variable("system.last_update", system, "KVT.System.LastUpdate", "LastUpdate", "", ua.VariantType.String)
        await self._add_variable("system.source_timestamp", system, "KVT.System.SourceTimestamp", "SourceTimestamp", "", ua.VariantType.String)
        await self._add_variable("system.exported_sensor_count", system, "KVT.System.ExportedSensorCount", "ExportedSensorCount", 0, ua.VariantType.Int32)

    async def _add_variable(self, key, parent, node_id, browse_name, initial_value, variant_type):
        if key in self._variables:
            return self._variables[key]
        node = await parent.add_variable(ua.NodeId(node_id, self._namespace_idx), browse_name, initial_value, varianttype=variant_type)
        self._variables[key] = node
        return node

    async def _publish_current(self, config, current):
        sensors = selected_sensors(current, config)
        poll_ports = current.get("poll_ports") or []
        await self._write_value("system.last_update", utc_now(), ua.VariantType.String)
        await self._write_value("system.source_timestamp", current.get("timestamp") or "", ua.VariantType.String)
        await self._write_value("system.exported_sensor_count", len(sensors), ua.VariantType.Int32)

        for port in poll_ports:
            await self._ensure_poll_port(port)
            port_id = str(port.get("id") or "default")
            key_prefix = f"poll_port.{port_id}"
            await self._write_value(f"{key_prefix}.name", port.get("name") or port_id, ua.VariantType.String)
            await self._write_value(f"{key_prefix}.transport", port.get("transport") or "", ua.VariantType.String)
            await self._write_value(f"{key_prefix}.state", port.get("state") or ("running" if port.get("running") else "stopped"), ua.VariantType.String)
            await self._write_value(f"{key_prefix}.last_ping_ms", _nullable_float(port.get("last_ping_ms")), ua.VariantType.Double, port.get("last_ping_ms") is not None)
            await self._write_value(f"{key_prefix}.last_error", port.get("last_error") or port.get("last_network_error") or "", ua.VariantType.String)

        for sensor in sensors:
            await self._ensure_sensor(sensor, config)
            sid = int(sensor.get("id"))
            key_prefix = f"sensor.{sid}"
            fields = config.get("fields") or {}
            await self._write_value(f"{key_prefix}.name", sensor.get("name") or f"Sensor {sid}", ua.VariantType.String)
            await self._write_value(f"{key_prefix}.display_number", str(sensor.get("display_number") or sensor.get("local_number") or sid), ua.VariantType.String)
            if fields.get("temperature", True):
                value, good = _metric_value(sensor, "temperature")
                await self._write_value(f"{key_prefix}.temperature", _nullable_float(value), ua.VariantType.Double, good)
            if fields.get("humidity", True):
                value, good = _metric_value(sensor, "humidity")
                await self._write_value(f"{key_prefix}.humidity", _nullable_float(value), ua.VariantType.Double, good)
            if fields.get("combined_status", True):
                await self._write_value(f"{key_prefix}.combined_status", sensor.get("combined_status") or "unknown", ua.VariantType.String)
            if fields.get("timestamp", True):
                timestamp = (sensor.get("temperature") or {}).get("timestamp") or current.get("timestamp") or ""
                await self._write_value(f"{key_prefix}.timestamp", timestamp, ua.VariantType.String)
            if fields.get("poll_port_metadata", True):
                await self._write_value(f"{key_prefix}.poll_port_id", str(sensor.get("poll_port_id") or "default"), ua.VariantType.String)
                await self._write_value(f"{key_prefix}.poll_port_name", sensor.get("poll_port_name") or "", ua.VariantType.String)
                await self._write_value(f"{key_prefix}.transport", sensor.get("transport") or "", ua.VariantType.String)
            if fields.get("limits", True):
                for variable, group, limit_key in (
                    ("temp_min", "temp_limits", "min"),
                    ("temp_max", "temp_limits", "max"),
                    ("hum_min", "hum_limits", "min"),
                    ("hum_max", "hum_limits", "max"),
                ):
                    value, good = _limit_value(sensor, group, limit_key)
                    await self._write_value(f"{key_prefix}.{variable}", value, ua.VariantType.Double, good)
        return len(sensors)

    async def _ensure_poll_port(self, port):
        port_id = str(port.get("id") or "default")
        key = f"poll_port.{port_id}"
        if key in self._objects:
            return self._objects[key]
        parent = self._objects["poll_ports"]
        obj = await parent.add_object(ua.NodeId(poll_port_object_node_id(port_id), self._namespace_idx), _browse_name(f"PollPort_{port_id}"))
        self._objects[key] = obj
        await self._add_variable(f"{key}.name", obj, poll_port_node_id(port_id, "Name"), "Name", port.get("name") or port_id, ua.VariantType.String)
        await self._add_variable(f"{key}.transport", obj, poll_port_node_id(port_id, "Transport"), "Transport", port.get("transport") or "", ua.VariantType.String)
        await self._add_variable(f"{key}.state", obj, poll_port_node_id(port_id, "State"), "State", "", ua.VariantType.String)
        await self._add_variable(f"{key}.last_ping_ms", obj, poll_port_node_id(port_id, "LastPingMs"), "LastPingMs", 0.0, ua.VariantType.Double)
        await self._add_variable(f"{key}.last_error", obj, poll_port_node_id(port_id, "LastError"), "LastError", "", ua.VariantType.String)
        return obj

    async def _ensure_sensor(self, sensor, config):
        sid = int(sensor.get("id"))
        key = f"sensor.{sid}"
        if key in self._objects:
            return self._objects[key]
        parent = self._objects["sensors"]
        obj = await parent.add_object(ua.NodeId(sensor_object_node_id(sid), self._namespace_idx), f"Sensor_{sid}")
        self._objects[key] = obj
        fields = config.get("fields") or {}
        await self._add_variable(f"{key}.name", obj, sensor_node_id(sid, "Name"), "Name", sensor.get("name") or f"Sensor {sid}", ua.VariantType.String)
        await self._add_variable(f"{key}.display_number", obj, sensor_node_id(sid, "DisplayNumber"), "DisplayNumber", str(sensor.get("display_number") or sid), ua.VariantType.String)
        if fields.get("temperature", True):
            await self._add_variable(f"{key}.temperature", obj, sensor_node_id(sid, "Temperature"), "Temperature", 0.0, ua.VariantType.Double)
        if fields.get("humidity", True):
            await self._add_variable(f"{key}.humidity", obj, sensor_node_id(sid, "Humidity"), "Humidity", 0.0, ua.VariantType.Double)
        if fields.get("combined_status", True):
            await self._add_variable(f"{key}.combined_status", obj, sensor_node_id(sid, "CombinedStatus"), "CombinedStatus", "", ua.VariantType.String)
        if fields.get("timestamp", True):
            await self._add_variable(f"{key}.timestamp", obj, sensor_node_id(sid, "Timestamp"), "Timestamp", "", ua.VariantType.String)
        if fields.get("poll_port_metadata", True):
            await self._add_variable(f"{key}.poll_port_id", obj, sensor_node_id(sid, "PollPortId"), "PollPortId", "", ua.VariantType.String)
            await self._add_variable(f"{key}.poll_port_name", obj, sensor_node_id(sid, "PollPortName"), "PollPortName", "", ua.VariantType.String)
            await self._add_variable(f"{key}.transport", obj, sensor_node_id(sid, "Transport"), "Transport", "", ua.VariantType.String)
        if fields.get("limits", True):
            await self._add_variable(f"{key}.temp_min", obj, sensor_node_id(sid, "TempMin"), "TempMin", 0.0, ua.VariantType.Double)
            await self._add_variable(f"{key}.temp_max", obj, sensor_node_id(sid, "TempMax"), "TempMax", 0.0, ua.VariantType.Double)
            await self._add_variable(f"{key}.hum_min", obj, sensor_node_id(sid, "HumMin"), "HumMin", 0.0, ua.VariantType.Double)
            await self._add_variable(f"{key}.hum_max", obj, sensor_node_id(sid, "HumMax"), "HumMax", 0.0, ua.VariantType.Double)
        return obj

    async def _write_value(self, key, value, variant_type, good=True):
        node = self._variables.get(key)
        if node is None:
            return
        encoded_value = value
        if value is None and variant_type in (ua.VariantType.Double, ua.VariantType.Int32):
            good = False
            encoded_value = 0.0 if variant_type == ua.VariantType.Double else 0
        elif value is None and variant_type == ua.VariantType.String:
            good = False
            encoded_value = ""
        status = ua.StatusCode(ua.StatusCodes.Good if good else ua.StatusCodes.BadNoData)
        # ua.DataValue — frozen dataclass: качество и метку времени нужно задавать в
        # конструкторе. Присваивание data_value.StatusCode_ после создания бросает
        # FrozenInstanceError, из-за чего прежний код молча падал в fallback и писал
        # значение с качеством Good (поэтому у датчиков без данных было 0.0/Good).
        try:
            data_value = ua.DataValue(
                Value=ua.Variant(encoded_value, variant_type),
                StatusCode_=status,
                SourceTimestamp=datetime.now(timezone.utc),
            )
            await node.write_value(data_value)
        except Exception:
            await node.write_value(encoded_value, varianttype=variant_type)


def _nullable_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _browse_name(value):
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(value or "item"))
