import os
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from serial.tools import list_ports

from shared.config_manager import atomic_save_json, load_runtime_json, load_system_config, save_poller_config
from .config import data_dir, normalized_poller_config
from .modbus_client import ModbusClient, ModbusError


def _signed_16(value: int) -> int:
    return value if value < 32768 else value - 65536


def _fixed_q8_8(value: int) -> float:
    return _signed_16(value) / 256.0


def _display_number(port: Dict[str, Any], sensor: Dict[str, Any], local_number: int = None) -> str:
    number = local_number or int(sensor.get("local_number") or sensor.get("id") or 0)
    return f"{port.get('name', port.get('id', 'PORT'))}-{number:03d}"


class PollPortWorker:
    def __init__(self, manager: "PollerService", port_config: Dict[str, Any], global_config: Dict[str, Any]):
        self.manager = manager
        self.port_config = dict(port_config)
        self.global_config = dict(global_config)
        self.port_id = str(self.port_config["id"])
        self.port_name = str(self.port_config.get("name") or self.port_id)
        self._stop = threading.Event()
        self._thread = None
        self._client = ModbusClient(self.port_config)
        self._lock = threading.RLock()
        self._state = "configured"
        self._last_error = None
        self._last_poll_at = None
        self._last_success_at = None
        self._last_cycle_duration_ms = 0
        self._stats = {
            "total_polls": 0,
            "successful_polls": 0,
            "failed_polls": 0,
            "skipped_status_reads": 0,
        }

    def start(self):
        with self._lock:
            if self.running:
                return
            self._stop.clear()
            self._state = "starting"
            self._thread = threading.Thread(target=self._run, name=f"poller-{self.port_id}", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        self._client.close()
        with self._lock:
            self._state = "stopped"

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    def status(self) -> Dict[str, Any]:
        with self._lock:
            payload = {
                **self.port_config,
                "state": self._state,
                "running": self.running,
                "last_error": self._last_error,
                "last_poll_at": self._last_poll_at,
                "last_success_at": self._last_success_at,
                "last_cycle_duration_ms": self._last_cycle_duration_ms,
                "seq_current": f"{self._client.current_seq():04X}" if self.port_config.get("transport") == "udp_c2000pp" else None,
                **self._stats,
            }
            return payload

    def _enabled_sensors(self) -> List[Dict[str, Any]]:
        sensors = load_system_config().get("sensors", [])
        enabled = []
        local_number = 0
        for config_index, sensor in enumerate(sensors, start=1):
            sensor_port_id = str(sensor.get("poll_port_id") or "default")
            if sensor_port_id != self.port_id or not sensor.get("enabled", True):
                continue
            local_number += 1
            sensor_number = int(sensor.get("local_number") or local_number)
            enabled.append({
                **sensor,
                "poll_port_id": self.port_id,
                "poll_port_name": self.port_name,
                "local_number": sensor_number,
                "display_number": sensor.get("display_number") or _display_number(self.port_config, sensor, sensor_number),
                "_config_index": config_index,
                "_config_path": f"system_config.json:sensors[{config_index - 1}]",
            })
        return enabled

    def _log_tx(self, raw_hex: str, parsed: Dict[str, Any], response_time_ms=None, transport_meta=None):
        self.manager.log_entry({
            "timestamp": datetime.now().isoformat(),
            "direction": "TX",
            "poll_port_id": self.port_id,
            "poll_port_name": self.port_name,
            "raw_hex": raw_hex,
            "parsed": parsed,
            "response_time_ms": response_time_ms,
            "transport": transport_meta or {},
        })

    def _log_rx(self, raw_hex: str, parsed: Dict[str, Any], response_time_ms=None, transport_meta=None):
        self.manager.log_entry({
            "timestamp": datetime.now().isoformat(),
            "direction": "RX",
            "poll_port_id": self.port_id,
            "poll_port_name": self.port_name,
            "raw_hex": raw_hex,
            "parsed": parsed,
            "response_time_ms": response_time_ms,
            "transport": transport_meta or {},
        })

    def _transport_log(self, exchange_or_error) -> Dict[str, Any]:
        meta = dict(getattr(exchange_or_error, "transport_meta", {}) or {})
        if self.port_config.get("transport") == "udp_c2000pp":
            meta["udp"] = {
                "tx_seq": meta.get("tx_seq"),
                "rx_seq": meta.get("rx_seq"),
                "wrapped_tx_hex": getattr(exchange_or_error, "tx_transport_hex", ""),
                "wrapped_rx_hex": getattr(exchange_or_error, "rx_transport_hex", ""),
                "payload_tx_hex": getattr(exchange_or_error, "tx_hex", ""),
                "payload_rx_hex": getattr(exchange_or_error, "rx_hex", ""),
                "remote": meta.get("remote"),
                "drained_datagrams": meta.get("drained_datagrams", 0),
            }
        return meta

    def _log_exchange(self, exchange: Dict[str, Any]):
        self.manager.log_exchange(exchange)

    def _log_status_skipped_after_values_failure(self, context: Dict[str, Any], slave_id: int, start_addr: int, count: int, error: ModbusError):
        timestamp = datetime.now().isoformat()
        self._stats["skipped_status_reads"] = self._stats.get("skipped_status_reads", 0) + 1
        self._log_exchange({
            "timestamp": timestamp,
            "status": "skipped",
            "source": {**context, "register_group": "status"},
            "poll_port_id": self.port_id,
            "poll_port_name": self.port_name,
            "slave_id": slave_id,
            "function": 3,
            "start_addr": start_addr,
            "quantity": count,
            "request": "Read statuses skipped after values failure",
            "tx_hex": None,
            "rx_hex": None,
            "response_time_ms": None,
            "reason": "values read failed",
            "error_type": type(error).__name__,
            "result": f"Status read skipped after values failure: {error}",
        })

    def _read_registers_logged(
        self,
        slave_id: int,
        start_addr: int,
        count: int,
        tx_description: str,
        rx_description: Callable[[List[int]], str],
        context: Dict[str, Any],
        client: ModbusClient = None,
    ) -> List[int]:
        modbus = client or self._client
        try:
            exchange = modbus.read_holding_registers_raw(slave_id, start_addr, count)
        except ModbusError as error:
            timestamp = datetime.now().isoformat()
            transport = self._transport_log(error)
            if error.tx_frame:
                self._log_tx(
                    error.tx_hex,
                    {"slave_id": slave_id, "function": 3, "start_addr": start_addr, "quantity": count, "description": tx_description},
                    transport_meta=transport,
                )
            self._log_rx(
                error.rx_hex or None,
                {"slave_id": slave_id, "function": 3, "description": f"No valid response: {error}"},
                error.response_time_ms,
                transport_meta=transport,
            )
            self._log_exchange({
                "timestamp": timestamp,
                "status": "error",
                "source": context,
                "poll_port_id": self.port_id,
                "poll_port_name": self.port_name,
                "transport": transport,
                "slave_id": slave_id,
                "function": 3,
                "start_addr": start_addr,
                "quantity": count,
                "request": tx_description,
                "tx_hex": error.tx_hex,
                "rx_hex": error.rx_hex or None,
                "response_time_ms": error.response_time_ms,
                "result": f"No valid response: {error}",
            })
            raise

        timestamp = datetime.now().isoformat()
        result_text = rx_description(exchange.registers)
        transport = self._transport_log(exchange)
        self._log_tx(
            exchange.tx_hex,
            {"slave_id": slave_id, "function": 3, "start_addr": start_addr, "quantity": count, "description": tx_description},
            transport_meta=transport,
        )
        self._log_rx(
            exchange.rx_hex,
            {"slave_id": slave_id, "function": 3, "byte_count": count * 2, "values": exchange.registers, "description": result_text},
            exchange.response_time_ms,
            transport_meta=transport,
        )
        self._log_exchange({
            "timestamp": timestamp,
            "status": "ok",
            "source": context,
            "poll_port_id": self.port_id,
            "poll_port_name": self.port_name,
            "transport": transport,
            "slave_id": slave_id,
            "function": 3,
            "start_addr": start_addr,
            "quantity": count,
            "request": tx_description,
            "tx_hex": exchange.tx_hex,
            "rx_hex": exchange.rx_hex,
            "response_time_ms": exchange.response_time_ms,
            "registers": exchange.registers,
            "result": result_text,
        })
        return exchange.registers

    def _sensor_status(self, sensor: Dict[str, Any], temp: float, hum: float, modbus_ok: bool) -> str:
        if not modbus_ok:
            return "no_connection"
        temp_limits = sensor.get("temp_limits", {})
        hum_limits = sensor.get("hum_limits", {})
        tmin = temp_limits.get("min", -40)
        tmax = temp_limits.get("max", 85)
        hmin = hum_limits.get("min", 0)
        hmax = hum_limits.get("max", 100)
        tw = temp_limits.get("warning_delta", 3)
        hw = hum_limits.get("warning_delta", 5)
        ta = temp_limits.get("alarm_delta", 5)
        ha = hum_limits.get("alarm_delta", 10)
        if temp < tmin - ta or temp > tmax + ta or hum < hmin - ha or hum > hmax + ha:
            return "alarm"
        if temp < tmin - tw:
            return "warning_low_temp"
        if temp > tmax + tw:
            return "warning_high_temp"
        if hum < hmin - hw:
            return "warning_low_hum"
        if hum > hmax + hw:
            return "warning_high_hum"
        return "guarded" if sensor.get("guarded", True) else "normal"

    def _poll_sensor(self, sensor: Dict[str, Any], now_iso: str, attempt: int = 0):
        configured_slave = int(self.port_config.get("device_slave_id", 0) or 0)
        slave_id = configured_slave if configured_slave > 0 else int(sensor["modbus_slave_id"])
        addr_t = int(sensor["modbus_addr_temp"])
        addr_h = int(sensor["modbus_addr_hum"])
        value_start = int(self.global_config["value_register_base"]) + addr_t - 1
        status_start = int(self.global_config["status_register_base"]) + addr_t - 1
        sensor_index = sensor.get("_config_index")
        sensor_name = sensor.get("name", f"Sensor {sensor['id']}")
        context = {
            "kind": "sensor",
            "config_line": sensor_index,
            "config_path": sensor.get("_config_path"),
            "sensor_id": sensor["id"],
            "sensor_name": sensor_name,
            "poll_port_id": self.port_id,
            "poll_port_name": self.port_name,
            "temp_addr": addr_t,
            "hum_addr": addr_h,
            "attempt": attempt,
        }

        try:
            values = self._read_registers_logged(
                slave_id=slave_id,
                start_addr=value_start,
                count=2,
                tx_description=f"Read values for sensor {sensor['id']}",
                rx_description=lambda regs: f"T={_fixed_q8_8(regs[0]):.4f} C, H={_fixed_q8_8(regs[1]):.4f}%",
                context={**context, "register_group": "values"},
            )
        except ModbusError as error:
            self._log_status_skipped_after_values_failure(context, slave_id, status_start, 2, error)
            raise

        statuses = self._read_registers_logged(
            slave_id=slave_id,
            start_addr=status_start,
            count=2,
            tx_description=f"Read statuses for sensor {sensor['id']}",
            rx_description=lambda regs: f"Status={regs[0]}, {regs[1]}",
            context={**context, "register_group": "status"},
        )

        temp_raw = _signed_16(values[0])
        hum_raw = _signed_16(values[1])
        temp_value = round(_fixed_q8_8(values[0]), 4)
        hum_value = round(_fixed_q8_8(values[1]), 4)
        combined = self._sensor_status(sensor, temp_value, hum_value, True)
        return {
            "id": sensor["id"],
            "poll_port_id": self.port_id,
            "poll_port_name": self.port_name,
            "transport": self.port_config.get("transport", "serial"),
            "local_number": sensor.get("local_number"),
            "display_number": sensor.get("display_number"),
            "name": sensor.get("name", f"Sensor {sensor['id']}"),
            "modbus_slave_id": slave_id,
            "modbus_addr_temp": addr_t,
            "modbus_addr_hum": addr_h,
            "temperature": {"value": temp_value, "raw": temp_raw, "status": "normal", "modbus_status": statuses[0], "timestamp": now_iso},
            "humidity": {"value": hum_value, "raw": hum_raw, "status": "normal", "modbus_status": statuses[1], "timestamp": now_iso},
            "combined_status": combined,
        }

    def _offline_sensor(self, sensor: Dict[str, Any], now_iso: str):
        return {
            "id": sensor["id"],
            "poll_port_id": self.port_id,
            "poll_port_name": self.port_name,
            "transport": self.port_config.get("transport", "serial"),
            "local_number": sensor.get("local_number"),
            "display_number": sensor.get("display_number"),
            "name": sensor.get("name", f"Sensor {sensor['id']}"),
            "modbus_slave_id": self.port_config.get("device_slave_id") or sensor["modbus_slave_id"],
            "modbus_addr_temp": sensor["modbus_addr_temp"],
            "modbus_addr_hum": sensor["modbus_addr_hum"],
            "temperature": {"value": None, "raw": None, "status": "offline", "modbus_status": 4, "timestamp": now_iso},
            "humidity": {"value": None, "raw": None, "status": "offline", "modbus_status": 4, "timestamp": now_iso},
            "combined_status": "no_connection",
        }

    def scan_devices(self, start_id: int = 1, end_id: int = 32, timeout_ms: int = 500) -> Dict[str, Any]:
        if self.running:
            return {"status": "error", "error": "Stop selected poll port before scan.", "found": []}
        config = {**self.port_config, "timeout_ms": max(50, min(10000, int(timeout_ms)))}
        client = ModbusClient(config)
        found = []
        status_base = int(self.global_config["status_register_base"])
        start_id = max(1, int(start_id))
        end_id = min(247, int(end_id))
        if end_id < start_id:
            start_id, end_id = end_id, start_id

        if not client.connect():
            return {"status": "error", "error": f"Cannot open {self.port_name}", "found": []}
        try:
            for slave_id in range(start_id, end_id + 1):
                try:
                    registers = self._read_registers_logged(
                        client=client,
                        slave_id=slave_id,
                        start_addr=status_base,
                        count=1,
                        tx_description="Scan PP: read 40000",
                        rx_description=lambda values: f"Scan PP: response status={values[0]}",
                        context={
                            "kind": "scan",
                            "config_line": None,
                            "config_path": None,
                            "sensor_id": None,
                            "sensor_name": f"Scan slave {slave_id}",
                            "poll_port_id": self.port_id,
                            "poll_port_name": self.port_name,
                            "register_group": "scan",
                        },
                    )
                    found.append({"slave_id": slave_id, "status_raw": registers[0]})
                except ModbusError:
                    continue
        finally:
            client.close()
        return {
            "status": "ok",
            "poll_port_id": self.port_id,
            "poll_port_name": self.port_name,
            "transport": self.port_config.get("transport"),
            "timeout_ms": timeout_ms,
            "probe_address": status_base,
            "range": {"start": start_id, "end": end_id},
            "found": found,
        }

    def _run(self):
        reconnect_pause = 5.0
        while not self._stop.is_set():
            if not self._client.connect():
                with self._lock:
                    self._state = "error"
                    self._last_error = f"Cannot open {self.port_name}"
                self.manager.update_port_snapshot(self.port_id, self.status(), [], self._stats)
                time.sleep(reconnect_pause)
                continue

            with self._lock:
                self._state = "running"
                self._last_error = None

            while not self._stop.is_set():
                now_iso = datetime.now().isoformat()
                cycle_started = time.perf_counter()
                with self._lock:
                    self._last_poll_at = now_iso
                    self._stats["total_polls"] += 1

                out_sensors = []
                cycle_failed = False
                for sensor in self._enabled_sensors():
                    success = False
                    last_sensor_error: Optional[Exception] = None
                    for attempt in range(int(self.global_config.get("retry_count", 3)) + 1):
                        try:
                            out_sensors.append(self._poll_sensor(sensor, now_iso, attempt=attempt))
                            success = True
                            break
                        except ModbusError as error:
                            last_sensor_error = error
                            with self._lock:
                                self._last_error = str(error)
                            if attempt < int(self.global_config.get("retry_count", 3)):
                                time.sleep(0.05)
                        except Exception as error:
                            last_sensor_error = error
                            with self._lock:
                                self._last_error = str(error)
                            self._log_rx(None, {"slave_id": self.port_config.get("device_slave_id", 0), "function": 3, "description": f"Poll error: {error}"})
                            if attempt < int(self.global_config.get("retry_count", 3)):
                                time.sleep(0.05)

                    if not success:
                        cycle_failed = True
                        if last_sensor_error is not None:
                            with self._lock:
                                self._last_error = str(last_sensor_error)
                        out_sensors.append(self._offline_sensor(sensor, now_iso))

                duration_ms = round((time.perf_counter() - cycle_started) * 1000, 2)
                with self._lock:
                    self._last_cycle_duration_ms = duration_ms
                    if cycle_failed:
                        self._stats["failed_polls"] += 1
                        self._state = "degraded"
                    else:
                        self._stats["successful_polls"] += 1
                        self._state = "running"
                        self._last_success_at = now_iso
                        self._last_error = None

                self.manager.update_port_snapshot(self.port_id, self.status(), out_sensors, self._stats)
                time.sleep(max(0.1, int(self.global_config["poll_period_ms"]) / 1000.0))

            self._client.close()


class PollerService:
    def __init__(self):
        self._config = normalized_poller_config()
        self._state = "stopped"
        self._running = False
        self._lock = threading.RLock()
        self._workers: Dict[str, PollPortWorker] = {}
        self._port_snapshots: Dict[str, Dict[str, Any]] = {}
        self._sensor_snapshots: Dict[str, List[Dict[str, Any]]] = {}
        self._stats = {"total_polls": 0, "successful_polls": 0, "failed_polls": 0, "skipped_status_reads": 0}
        self._log_entries: List[Dict[str, Any]] = []
        self._tx_queue = deque(maxlen=5000)
        self._rx_queue = deque(maxlen=5000)
        self._exchange_queue = deque(maxlen=5000)
        self._last_log_write_at = 0.0
        self._log_dirty = False

    def start(self):
        with self._lock:
            if self._running:
                return
            self._running = True
            self._state = "running"
            self._reconcile_workers_locked()

    def stop(self):
        with self._lock:
            workers = list(self._workers.values())
            self._workers = {}
            self._running = False
            self._state = "stopped"
        for worker in workers:
            worker.stop()
        self._write_current_file()

    def start_port(self, port_id: str):
        with self._lock:
            self._running = True
            self._state = "running"
            port = self._port_config(port_id)
            if not port:
                return {"status": "error", "error": "Poll port not found"}
            if not port.get("enabled", True):
                return {"status": "error", "error": "Poll port is disabled"}
            worker = self._workers.get(port_id)
            if worker and worker.running:
                return {"status": "ok", "message": "already running"}
            worker = PollPortWorker(self, port, self._config)
            self._workers[port_id] = worker
            worker.start()
            return {"status": "ok"}

    def stop_port(self, port_id: str):
        with self._lock:
            worker = self._workers.pop(port_id, None)
        if not worker:
            return {"status": "ok", "message": "already stopped"}
        worker.stop()
        self._write_current_file()
        return {"status": "ok"}

    def restart_port(self, port_id: str):
        self.stop_port(port_id)
        return self.start_port(port_id)

    def reload_sensors(self):
        return {"status": "ok", "sensors": len(self._enabled_sensors())}

    def apply_config(self, config: Dict[str, Any]):
        with self._lock:
            self._config = config
            save_poller_config(config)
            if self._running:
                self._reconcile_workers_locked()
            else:
                self._state = "stopped"

    def _port_config(self, port_id: str):
        for port in self._config.get("poll_ports", []):
            if str(port.get("id")) == str(port_id):
                return dict(port)
        return None

    def _reconcile_workers_locked(self):
        desired = {str(port["id"]): dict(port) for port in self._config.get("poll_ports", []) if port.get("enabled", True)}
        for port_id in list(self._workers):
            current = self._workers[port_id]
            if port_id not in desired or current.port_config != desired[port_id] or current.global_config != self._config:
                worker = self._workers.pop(port_id)
                worker.stop()
        for port_id, port in desired.items():
            if port_id not in self._workers:
                worker = PollPortWorker(self, port, self._config)
                self._workers[port_id] = worker
                worker.start()

    def status(self) -> Dict[str, Any]:
        with self._lock:
            ports = self.poll_ports()
            response_times = [x["response_time_ms"] for x in self._log_entries if x.get("response_time_ms") is not None]
            last_error = next((p.get("last_error") for p in ports if p.get("last_error")), None)
            last_poll_at = max([p.get("last_poll_at") for p in ports if p.get("last_poll_at")] or [None])
            last_success_at = max([p.get("last_success_at") for p in ports if p.get("last_success_at")] or [None])
            return {
                "state": self._state,
                "manager_state": self._state,
                "running": self._running,
                "last_error": last_error,
                "last_poll_at": last_poll_at,
                "last_success_at": last_success_at,
                "current_sensor_count": len(self._enabled_sensors()),
                "poll_ports": ports,
                "log_entries": len(self._log_entries),
                "tx_entries": len(self._tx_queue),
                "rx_entries": len(self._rx_queue),
                "exchange_entries": len(self._exchange_queue),
                "avg_response_time_ms": round(sum(response_times) / len(response_times), 2) if response_times else 0,
                **self._stats,
            }

    def health(self) -> Dict[str, Any]:
        with self._lock:
            return {"state": self._state, **self._stats, "ports": self.poll_ports()}

    def poll_ports(self) -> List[Dict[str, Any]]:
        ports = []
        for port in self._config.get("poll_ports", []):
            port_id = str(port["id"])
            worker = self._workers.get(port_id)
            if worker:
                ports.append(worker.status())
            else:
                ports.append({**port, "state": "disabled" if not port.get("enabled", True) else "stopped", "running": False})
        return ports

    def current_payload(self) -> Dict[str, Any]:
        path = os.path.join(data_dir(), "current.json")
        return load_runtime_json(path, default={})

    def log_payload(self, limit: int = 100, poll_port_id: str = None) -> Dict[str, Any]:
        limit = max(1, int(limit))
        with self._lock:
            def filt(items):
                if poll_port_id:
                    return [item for item in items if str(item.get("poll_port_id")) == str(poll_port_id)]
                return list(items)
            return {
                "max_entries": int(self._config["log_max_entries"]),
                "entries": filt(self._log_entries)[-limit:],
                "tx_queue": filt(self._tx_queue)[-limit:],
                "rx_queue": filt(self._rx_queue)[-limit:],
                "exchange_queue": filt(self._exchange_queue)[-limit:],
            }

    @staticmethod
    def available_ports() -> List[Dict[str, Any]]:
        return [{"device": port.device, "description": port.description} for port in list_ports.comports()]

    def scan_devices(self, poll_port_id: str = None, start_id: int = 1, end_id: int = 32, timeout_ms: int = 500) -> Dict[str, Any]:
        port_id = poll_port_id or (self._config.get("poll_ports") or [{"id": "default"}])[0]["id"]
        port = self._port_config(port_id)
        if not port:
            return {"status": "error", "error": "Poll port not found", "found": []}
        worker = self._workers.get(str(port_id)) or PollPortWorker(self, port, self._config)
        return worker.scan_devices(start_id=start_id, end_id=end_id, timeout_ms=timeout_ms)

    def _enabled_sensors(self):
        sensors = load_system_config().get("sensors", [])
        return [sensor for sensor in sensors if sensor.get("enabled", True)]

    def update_port_snapshot(self, port_id: str, port_status: Dict[str, Any], sensors: List[Dict[str, Any]], stats: Dict[str, Any]):
        with self._lock:
            self._port_snapshots[port_id] = port_status
            self._sensor_snapshots[port_id] = sensors
            self._stats = {
                "total_polls": sum(int(p.get("total_polls", 0)) for p in self._port_snapshots.values()),
                "successful_polls": sum(int(p.get("successful_polls", 0)) for p in self._port_snapshots.values()),
                "failed_polls": sum(int(p.get("failed_polls", 0)) for p in self._port_snapshots.values()),
                "skipped_status_reads": sum(int(p.get("skipped_status_reads", 0)) for p in self._port_snapshots.values()),
            }
            self._write_current_file_locked()

    def _write_current_file(self):
        with self._lock:
            self._write_current_file_locked()

    def _write_current_file_locked(self):
        sensors = []
        for port_id in sorted(self._sensor_snapshots):
            sensors.extend(self._sensor_snapshots[port_id])
        payload = {
            "timestamp": datetime.now().isoformat(),
            "poll_period_ms": int(self._config["poll_period_ms"]),
            "poll_ports": self.poll_ports(),
            "sensors": sensors,
            "stats": {
                **self._stats,
                "ports": {port_id: snapshot for port_id, snapshot in self._port_snapshots.items()},
            },
        }
        atomic_save_json(os.path.join(data_dir(), "current.json"), payload)

    def log_entry(self, entry: Dict[str, Any]):
        with self._lock:
            self._log_entries.append(entry)
            if entry.get("direction") == "TX":
                self._tx_queue.append(entry)
            elif entry.get("direction") == "RX":
                self._rx_queue.append(entry)
            max_entries = int(self._config.get("log_max_entries", 1000))
            if len(self._log_entries) > max_entries:
                self._log_entries = self._log_entries[-max_entries:]
            self._log_dirty = True
            self._flush_log_file()

    def log_exchange(self, exchange: Dict[str, Any]):
        with self._lock:
            self._exchange_queue.append(exchange)
            self._log_dirty = True
            self._flush_log_file()

    def _flush_log_file(self, force: bool = False):
        now = time.monotonic()
        if not self._log_dirty:
            return
        if not force and now - self._last_log_write_at < 1.0:
            return
        self._write_log_file()
        self._last_log_write_at = now
        self._log_dirty = False

    def _write_log_file(self):
        max_entries = int(self._config.get("log_max_entries", 1000))
        atomic_save_json(
            os.path.join(data_dir(), "modbus_log.json"),
            {
                "max_entries": max_entries,
                "entries": self._log_entries,
                "tx_queue": list(self._tx_queue),
                "rx_queue": list(self._rx_queue),
                "exchange_queue": list(self._exchange_queue),
            },
            indent=None,
        )
