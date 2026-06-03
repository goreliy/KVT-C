import os
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any, Callable, Dict, List

from serial.tools import list_ports

from shared.config_manager import atomic_save_json, load_runtime_json, load_system_config, save_poller_config
from .config import data_dir, normalized_poller_config
from .modbus_client import ModbusClient, ModbusError


def _signed_16(value: int) -> int:
    return value if value < 32768 else value - 65536


def _fixed_q8_8(value: int) -> float:
    return _signed_16(value) / 256.0


class PollerService:
    def __init__(self):
        self._config = normalized_poller_config()
        self._state = "stopped"
        self._last_error = None
        self._last_poll_at = None
        self._last_success_at = None
        self._last_exchange_at = None
        self._running = False
        self._thread = None
        self._lock = threading.RLock()
        self._stats = {"total_polls": 0, "successful_polls": 0, "failed_polls": 0}
        self._log_entries: List[Dict[str, Any]] = []
        self._tx_queue = deque(maxlen=5000)
        self._rx_queue = deque(maxlen=5000)
        self._exchange_queue = deque(maxlen=5000)
        self._last_cycle_duration_ms = 0
        self._last_log_write_at = 0.0
        self._log_dirty = False
        self._pending_config = None
        self._modbus = ModbusClient(self._config)

    def start(self):
        with self._lock:
            if self._running:
                return
            self._running = True
            self._state = "running"
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def stop(self):
        with self._lock:
            self._running = False
            self._state = "stopped"
            self._modbus.close()

    def reload_sensors(self):
        return {"status": "ok", "sensors": len(self._enabled_sensors())}

    def apply_config(self, config: Dict[str, Any]):
        with self._lock:
            merged = {**self._config, **config}
            save_poller_config(merged)
            if self._running:
                self._pending_config = merged
                self._last_error = "Config saved; pending reconnect between poll cycles"
                return
            self._config = merged
            self._modbus.close()
            self._modbus = ModbusClient(self._config)
            self._state = "stopped"

    def status(self) -> Dict[str, Any]:
        with self._lock:
            response_times = [x["response_time_ms"] for x in self._log_entries if x.get("response_time_ms") is not None]
            return {
                "state": self._state,
                "running": self._running,
                "transport": self._config.get("transport", "serial"),
                "com_port": self._config.get("com_port"),
                "udp_host": self._config.get("udp_host"),
                "udp_port": self._config.get("udp_port"),
                "device_slave_id": self._config.get("device_slave_id"),
                "last_error": self._last_error,
                "last_poll_at": self._last_poll_at,
                "last_success_at": self._last_success_at,
                "last_exchange_at": self._last_exchange_at,
                "current_sensor_count": len(self._enabled_sensors()),
                "log_entries": len(self._log_entries),
                "tx_entries": len(self._tx_queue),
                "rx_entries": len(self._rx_queue),
                "exchange_entries": len(self._exchange_queue),
                "avg_response_time_ms": round(sum(response_times) / len(response_times), 2) if response_times else 0,
                "last_cycle_duration_ms": self._last_cycle_duration_ms,
                **self._stats,
            }

    def health(self) -> Dict[str, Any]:
        with self._lock:
            return {"state": self._state, **self._stats, "last_error": self._last_error}

    def current_payload(self) -> Dict[str, Any]:
        path = os.path.join(data_dir(), "current.json")
        return load_runtime_json(path, default={})

    def log_payload(self, limit: int = 100) -> Dict[str, Any]:
        limit = max(1, int(limit))
        with self._lock:
            return {
                "max_entries": int(self._config["log_max_entries"]),
                "entries": self._log_entries[-limit:],
                "tx_queue": list(self._tx_queue)[-limit:],
                "rx_queue": list(self._rx_queue)[-limit:],
                "exchange_queue": list(self._exchange_queue)[-limit:],
            }

    @staticmethod
    def available_ports() -> List[Dict[str, Any]]:
        return [{"device": port.device, "description": port.description} for port in list_ports.comports()]

    def scan_devices(self, start_id: int = 1, end_id: int = 32, timeout_ms: int = 500) -> Dict[str, Any]:
        with self._lock:
            if self._running:
                return {
                    "status": "error",
                    "error": "Scan is disabled while polling is running. Stop poller before scan.",
                    "found": [],
                }
            config = dict(self._config)

        start_id = max(1, int(start_id))
        end_id = min(247, int(end_id))
        if end_id < start_id:
            start_id, end_id = end_id, start_id

        timeout_ms = max(50, min(10000, int(timeout_ms)))
        status_base = int(config["status_register_base"])
        scan_client = ModbusClient({**config, "timeout_ms": timeout_ms})
        found = []

        if not scan_client.connect():
            if str(config.get("transport", "serial")).lower() == "udp":
                message = f"Cannot reach UDP endpoint {config.get('udp_host')}:{config.get('udp_port')}"
            else:
                message = f"Cannot open port {config['com_port']}"
            self._log_rx(None, {"slave_id": 0, "function": 3, "description": f"Scan: {message}"})
            self._flush_log_file(force=True)
            return {"status": "error", "error": message, "found": []}

        try:
            for slave_id in range(start_id, end_id + 1):
                try:
                    registers = self._read_registers_logged(
                        client=scan_client,
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
                            "register_group": "scan",
                        },
                    )
                    found.append({"slave_id": slave_id, "status_raw": registers[0]})
                except ModbusError:
                    continue
        finally:
            scan_client.close()

        return {
            "status": "ok",
            "transport": config.get("transport", "serial"),
            "com_port": config["com_port"],
            "udp_host": config.get("udp_host"),
            "udp_port": config.get("udp_port"),
            "timeout_ms": timeout_ms,
            "probe_address": status_base,
            "range": {"start": start_id, "end": end_id},
            "found": found,
        }

    def _enabled_sensors(self):
        sensors = load_system_config().get("sensors", [])
        enabled = []
        for config_index, sensor in enumerate(sensors, start=1):
            if sensor.get("enabled", True):
                enabled.append({
                    **sensor,
                    "_config_index": config_index,
                    "_config_path": f"system_config.json:sensors[{config_index - 1}]",
                })
        return enabled

    def _log_tx(self, raw_hex: str, parsed: Dict[str, Any], response_time_ms=None):
        self._log({"timestamp": datetime.now().isoformat(), "direction": "TX", "raw_hex": raw_hex, "parsed": parsed, "response_time_ms": response_time_ms})

    def _log_rx(self, raw_hex: str, parsed: Dict[str, Any], response_time_ms=None):
        self._log({"timestamp": datetime.now().isoformat(), "direction": "RX", "raw_hex": raw_hex, "parsed": parsed, "response_time_ms": response_time_ms})

    def _log(self, entry: Dict[str, Any]):
        with self._lock:
            self._last_exchange_at = entry.get("timestamp")
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

    def _log_exchange(self, exchange: Dict[str, Any]):
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
        modbus = client or self._modbus
        try:
            exchange = modbus.read_holding_registers_raw(slave_id, start_addr, count)
        except ModbusError as error:
            timestamp = datetime.now().isoformat()
            if error.tx_frame:
                self._log_tx(error.tx_hex, {"slave_id": slave_id, "function": 3, "start_addr": start_addr, "quantity": count, "description": tx_description})
            self._log_rx(
                error.rx_hex or None,
                {"slave_id": slave_id, "function": 3, "description": f"No valid response: {error}"},
                error.response_time_ms,
            )
            self._log_exchange({
                "timestamp": timestamp,
                "status": "error",
                "source": context,
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
        self._log_tx(exchange.tx_hex, {"slave_id": slave_id, "function": 3, "start_addr": start_addr, "quantity": count, "description": tx_description})
        self._log_rx(
            exchange.rx_hex,
            {"slave_id": slave_id, "function": 3, "byte_count": count * 2, "values": exchange.registers, "description": result_text},
            exchange.response_time_ms,
        )
        self._log_exchange({
            "timestamp": timestamp,
            "status": "ok",
            "source": context,
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

    def _poll_sensor(self, sensor: Dict[str, Any], now_iso: str):
        configured_slave = int(self._config.get("device_slave_id", 0) or 0)
        slave_id = configured_slave if configured_slave > 0 else int(sensor["modbus_slave_id"])
        addr_t = int(sensor["modbus_addr_temp"])
        addr_h = int(sensor["modbus_addr_hum"])
        value_start = int(self._config["value_register_base"]) + addr_t - 1
        status_start = int(self._config["status_register_base"]) + addr_t - 1
        sensor_index = sensor.get("_config_index")
        sensor_name = sensor.get("name", f"Sensor {sensor['id']}")
        context = {
            "kind": "sensor",
            "config_line": sensor_index,
            "config_path": sensor.get("_config_path"),
            "sensor_id": sensor["id"],
            "sensor_name": sensor_name,
            "temp_addr": addr_t,
            "hum_addr": addr_h,
        }

        values = self._read_registers_logged(
            slave_id=slave_id,
            start_addr=value_start,
            count=2,
            tx_description=f"Read values for sensor {sensor['id']}",
            rx_description=lambda regs: f"T={_fixed_q8_8(regs[0]):.4f} C, H={_fixed_q8_8(regs[1]):.4f}%",
            context={**context, "register_group": "values"},
        )
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
            "name": sensor.get("name", f"Sensor {sensor['id']}"),
            "modbus_slave_id": self._config.get("device_slave_id") or sensor["modbus_slave_id"],
            "modbus_addr_temp": sensor["modbus_addr_temp"],
            "modbus_addr_hum": sensor["modbus_addr_hum"],
            "temperature": {"value": None, "raw": None, "status": "offline", "modbus_status": 4, "timestamp": now_iso},
            "humidity": {"value": None, "raw": None, "status": "offline", "modbus_status": 4, "timestamp": now_iso},
            "combined_status": "no_connection",
        }

    def _run(self):
        reconnect_pause = 5.0
        while True:
            with self._lock:
                if not self._running:
                    break
                if self._pending_config is not None:
                    self._config = self._pending_config
                    self._pending_config = None
                    self._modbus.close()
                    self._modbus = ModbusClient(self._config)

            if not self._modbus.connect():
                self._state = "error"
                if str(self._config.get("transport", "serial")).lower() == "udp":
                    self._last_error = f"Cannot reach UDP endpoint {self._config.get('udp_host')}:{self._config.get('udp_port')}"
                else:
                    self._last_error = f"Cannot open port {self._config['com_port']}"
                self._log_rx(None, {"slave_id": self._config.get("device_slave_id", 0), "function": 3, "description": self._last_error})
                time.sleep(reconnect_pause)
                continue

            self._state = "running"
            while True:
                with self._lock:
                    if not self._running:
                        break
                    if self._pending_config is not None:
                        self._config = self._pending_config
                        self._pending_config = None
                        self._modbus.close()
                        self._modbus = ModbusClient(self._config)
                        break

                now_iso = datetime.now().isoformat()
                cycle_started = time.perf_counter()
                self._last_poll_at = now_iso
                self._stats["total_polls"] += 1
                out_sensors = []
                cycle_failed = False

                for sensor in self._enabled_sensors():
                    success = False
                    for attempt in range(int(self._config.get("retry_count", 3)) + 1):
                        try:
                            out_sensors.append(self._poll_sensor(sensor, now_iso))
                            success = True
                            break
                        except ModbusError as error:
                            self._last_error = str(error)
                            if attempt < int(self._config.get("retry_count", 3)):
                                time.sleep(0.05)
                        except Exception as error:
                            self._last_error = str(error)
                            self._log_rx(None, {"slave_id": self._config.get("device_slave_id", 0), "function": 3, "description": f"Poll error: {error}"})
                            if attempt < int(self._config.get("retry_count", 3)):
                                time.sleep(0.05)

                    if not success:
                        cycle_failed = True
                        out_sensors.append(self._offline_sensor(sensor, now_iso))

                payload = {
                    "timestamp": now_iso,
                    "transport": self._config.get("transport", "serial"),
                    "poll_period_ms": int(self._config["poll_period_ms"]),
                    "com_port": self._config["com_port"],
                    "udp_host": self._config.get("udp_host"),
                    "udp_port": self._config.get("udp_port"),
                    "baudrate": int(self._config["baudrate"]),
                    "sensors": out_sensors,
                    "stats": {**self._stats, "last_error": self._last_error},
                }
                self._last_cycle_duration_ms = round((time.perf_counter() - cycle_started) * 1000, 2)
                payload["stats"]["last_cycle_duration_ms"] = self._last_cycle_duration_ms
                atomic_save_json(os.path.join(data_dir(), "current.json"), payload)
                self._flush_log_file(force=True)

                if cycle_failed:
                    self._stats["failed_polls"] += 1
                    self._state = "error"
                else:
                    self._stats["successful_polls"] += 1
                    self._state = "running"
                    self._last_success_at = now_iso
                    self._last_error = None

                time.sleep(max(0.1, int(self._config["poll_period_ms"]) / 1000.0))

            self._modbus.close()
