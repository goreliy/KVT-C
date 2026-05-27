import json
import os
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any, Dict, List

from serial.tools import list_ports

from shared.config_manager import load_system_config
from .config import normalized_poller_config, data_dir
from .modbus_client import ModbusClient


def _signed_16(value: int) -> int:
    return value if value < 32768 else value - 65536


def _fixed_q8_8(value: int) -> float:
    return _signed_16(value) / 256.0


def _crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def _hex_bytes(payload: bytes) -> str:
    return " ".join(f"{b:02X}" for b in payload)


class PollerService:
    def __init__(self):
        self._config = normalized_poller_config()
        self._state = "stopped"
        self._last_error = None
        self._last_poll_at = None
        self._last_success_at = None
        self._running = False
        self._thread = None
        self._lock = threading.Lock()
        self._stats = {"total_polls": 0, "successful_polls": 0, "failed_polls": 0}
        self._log_entries: List[Dict[str, Any]] = []
        self._tx_queue = deque(maxlen=5000)
        self._rx_queue = deque(maxlen=5000)
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
            self._config = {**self._config, **config}
            from shared.config_manager import save_poller_config
            save_poller_config(self._config)
            self._modbus = ModbusClient(self._config)
            self._state = "running" if self._running else "stopped"

    def status(self) -> Dict[str, Any]:
        avg_response = [x["response_time_ms"] for x in self._log_entries if x.get("response_time_ms") is not None]
        return {
            "state": self._state,
            "running": self._running,
            "last_error": self._last_error,
            "last_poll_at": self._last_poll_at,
            "last_success_at": self._last_success_at,
            "current_sensor_count": len(self._enabled_sensors()),
            "avg_response_time_ms": round(sum(avg_response) / len(avg_response), 2) if avg_response else 0,
            **self._stats,
        }

    def health(self) -> Dict[str, Any]:
        return {"state": self._state, **self._stats, "last_error": self._last_error}

    def current_payload(self) -> Dict[str, Any]:
        path = os.path.join(data_dir(), "current.json")
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def log_payload(self, limit: int = 100) -> Dict[str, Any]:
        entries = self._log_entries[-max(1, int(limit)):]
        return {
            "max_entries": int(self._config["log_max_entries"]),
            "entries": entries,
            "tx_queue": list(self._tx_queue)[-max(1, int(limit)):],
            "rx_queue": list(self._rx_queue)[-max(1, int(limit)):],
        }

    @staticmethod
    def available_ports() -> List[Dict[str, Any]]:
        return [{"device": p.device, "description": p.description} for p in list_ports.comports()]

    def _enabled_sensors(self):
        sensors = load_system_config().get("sensors", [])
        return [s for s in sensors if s.get("enabled", True)]

    def _log(self, entry: Dict[str, Any]):
        self._log_entries.append(entry)
        if entry.get("direction") == "TX":
            self._tx_queue.append(entry)
        elif entry.get("direction") == "RX":
            self._rx_queue.append(entry)
        max_entries = int(self._config.get("log_max_entries", 1000))
        if len(self._log_entries) > max_entries:
            self._log_entries = self._log_entries[-max_entries:]
        self._write_json_atomic(os.path.join(data_dir(), "modbus_log.json"), {
            "max_entries": max_entries,
            "entries": self._log_entries
        })

    @staticmethod
    def _write_json_atomic(path: str, payload: Dict[str, Any]):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    def _sensor_status(self, sensor: Dict[str, Any], temp: float, hum: float, modbus_ok: bool) -> str:
        if not modbus_ok:
            return "no_connection"
        temp_lim = sensor.get("temp_limits", {})
        hum_lim = sensor.get("hum_limits", {})
        tmin = temp_lim.get("min", -40)
        tmax = temp_lim.get("max", 85)
        hmin = hum_lim.get("min", 0)
        hmax = hum_lim.get("max", 100)
        tw = temp_lim.get("warning_delta", 3)
        hw = hum_lim.get("warning_delta", 5)
        ta = temp_lim.get("alarm_delta", 5)
        ha = hum_lim.get("alarm_delta", 10)
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
        slave = configured_slave if configured_slave > 0 else int(sensor["modbus_slave_id"])
        addr_t = int(sensor["modbus_addr_temp"])
        addr_h = int(sensor["modbus_addr_hum"])
        val_base = int(self._config["value_register_base"])
        stat_base = int(self._config["status_register_base"])
        v_start = val_base + addr_t - 1
        s_start = stat_base + addr_t - 1
        tx_time = time.perf_counter()
        req_bytes = bytes([
            slave, 0x03,
            (v_start >> 8) & 0xFF, v_start & 0xFF,
            0x00, 0x02
        ])
        req_crc = _crc16_modbus(req_bytes)
        req_frame = req_bytes + bytes([req_crc & 0xFF, (req_crc >> 8) & 0xFF])
        self._log({
            "timestamp": now_iso,
            "direction": "TX",
            "raw_hex": _hex_bytes(req_frame),
            "parsed": {"slave_id": slave, "function": 3, "start_addr": v_start, "quantity": 2, "description": f"Запрос значений датчика {sensor['id']}"},
            "response_time_ms": None
        })
        values = self._modbus.read_holding_registers(slave, v_start, 2)
        req2_bytes = bytes([
            slave, 0x03,
            (s_start >> 8) & 0xFF, s_start & 0xFF,
            0x00, 0x02
        ])
        req2_crc = _crc16_modbus(req2_bytes)
        req2_frame = req2_bytes + bytes([req2_crc & 0xFF, (req2_crc >> 8) & 0xFF])
        self._log({
            "timestamp": datetime.now().isoformat(),
            "direction": "TX",
            "raw_hex": _hex_bytes(req2_frame),
            "parsed": {"slave_id": slave, "function": 3, "start_addr": s_start, "quantity": 2, "description": f"Запрос статусов датчика {sensor['id']}"},
            "response_time_ms": None
        })
        statuses = self._modbus.read_holding_registers(slave, s_start, 2)
        elapsed = round((time.perf_counter() - tx_time) * 1000, 2)
        resp_body = bytes([slave, 0x03, 0x04, (values[0] >> 8) & 0xFF, values[0] & 0xFF, (values[1] >> 8) & 0xFF, values[1] & 0xFF])
        resp_crc = _crc16_modbus(resp_body)
        resp_frame = resp_body + bytes([resp_crc & 0xFF, (resp_crc >> 8) & 0xFF])
        temp_val_log = _fixed_q8_8(values[0])
        hum_val_log = _fixed_q8_8(values[1])
        self._log({
            "timestamp": datetime.now().isoformat(),
            "direction": "RX",
            "raw_hex": _hex_bytes(resp_frame),
            "parsed": {"slave_id": slave, "function": 3, "byte_count": 4, "values": values, "description": f"Ответ: T={temp_val_log:.4f}°C, H={hum_val_log:.4f}%"},
            "response_time_ms": elapsed
        })
        resp2_body = bytes([slave, 0x03, 0x04, (statuses[0] >> 8) & 0xFF, statuses[0] & 0xFF, (statuses[1] >> 8) & 0xFF, statuses[1] & 0xFF])
        resp2_crc = _crc16_modbus(resp2_body)
        resp2_frame = resp2_body + bytes([resp2_crc & 0xFF, (resp2_crc >> 8) & 0xFF])
        self._log({
            "timestamp": datetime.now().isoformat(),
            "direction": "RX",
            "raw_hex": _hex_bytes(resp2_frame),
            "parsed": {"slave_id": slave, "function": 3, "byte_count": 4, "values": statuses, "description": f"Ответ статусов: {statuses[0]}, {statuses[1]}"},
            "response_time_ms": elapsed
        })
        temp_raw = _signed_16(values[0])
        hum_raw = _signed_16(values[1])
        temp_val = round(_fixed_q8_8(values[0]), 4)
        hum_val = round(_fixed_q8_8(values[1]), 4)
        combined = self._sensor_status(sensor, temp_val, hum_val, True)
        return {
            "id": sensor["id"],
            "name": sensor.get("name", f"Датчик {sensor['id']}"),
            "modbus_slave_id": slave,
            "modbus_addr_temp": addr_t,
            "modbus_addr_hum": addr_h,
            "temperature": {"value": temp_val, "raw": temp_raw, "status": "normal", "modbus_status": statuses[0], "timestamp": now_iso},
            "humidity": {"value": hum_val, "raw": hum_raw, "status": "normal", "modbus_status": statuses[1], "timestamp": now_iso},
            "combined_status": combined,
        }

    def _run(self):
        reconnect_pause = 5.0
        while True:
            with self._lock:
                if not self._running:
                    break
            if not self._modbus.connect():
                self._state = "error"
                self._last_error = f"Не удалось открыть порт {self._config['com_port']}"
                self._log({
                    "timestamp": datetime.now().isoformat(),
                    "direction": "RX",
                    "raw_hex": None,
                    "parsed": {
                        "slave_id": self._config.get("device_slave_id", 0),
                        "function": 4,
                        "description": self._last_error
                    },
                    "response_time_ms": None
                })
                time.sleep(reconnect_pause)
                continue
            self._state = "running"
            while True:
                with self._lock:
                    if not self._running:
                        break
                now_iso = datetime.now().isoformat()
                self._last_poll_at = now_iso
                self._stats["total_polls"] += 1
                sensors = self._enabled_sensors()
                out_sensors = []
                cycle_failed = False
                for sensor in sensors:
                    retries = int(self._config.get("retry_count", 3))
                    for attempt in range(retries + 1):
                        try:
                            out_sensors.append(self._poll_sensor(sensor, now_iso))
                            break
                        except Exception as ex:
                            err = str(ex)
                            self._log({
                                "timestamp": datetime.now().isoformat(),
                                "direction": "RX",
                                "raw_hex": None,
                                "parsed": {
                                    "slave_id": int(sensor["modbus_slave_id"]),
                                    "function": 3,
                                    "description": f"Ошибка обмена: {err}"
                                },
                                "response_time_ms": None
                            })
                            if attempt >= retries:
                                cycle_failed = True
                                self._last_error = err
                                out_sensors.append({
                                    "id": sensor["id"],
                                    "name": sensor.get("name", f"Датчик {sensor['id']}"),
                                    "modbus_slave_id": sensor["modbus_slave_id"],
                                    "modbus_addr_temp": sensor["modbus_addr_temp"],
                                    "modbus_addr_hum": sensor["modbus_addr_hum"],
                                    "temperature": {"value": None, "raw": None, "status": "offline", "modbus_status": 4, "timestamp": now_iso},
                                    "humidity": {"value": None, "raw": None, "status": "offline", "modbus_status": 4, "timestamp": now_iso},
                                    "combined_status": "no_connection",
                                })
                            else:
                                time.sleep(0.05)
                payload = {
                    "timestamp": now_iso,
                    "poll_period_ms": int(self._config["poll_period_ms"]),
                    "com_port": self._config["com_port"],
                    "baudrate": int(self._config["baudrate"]),
                    "sensors": out_sensors,
                    "stats": {
                        **self._stats,
                        "last_error": self._last_error
                    }
                }
                self._write_json_atomic(os.path.join(data_dir(), "current.json"), payload)
                if cycle_failed:
                    self._stats["failed_polls"] += 1
                    self._state = "error"
                else:
                    self._stats["successful_polls"] += 1
                    self._state = "running"
                    self._last_success_at = now_iso
                time.sleep(max(0.1, int(self._config["poll_period_ms"]) / 1000.0))
            self._modbus.close()

    def scan_devices(self, start_id: int = 1, end_id: int = 32, timeout_ms: int = 500) -> Dict[str, Any]:
        start_id = max(1, int(start_id))
        end_id = min(247, int(end_id))
        if end_id < start_id:
            start_id, end_id = end_id, start_id
        found = []
        status_base = int(self._config["status_register_base"])
        timeout_ms = max(50, min(10000, int(timeout_ms)))
        scan_cfg = {**self._config, "timeout_ms": timeout_ms}
        scan_client = ModbusClient(scan_cfg)
        if not scan_client.connect():
            message = f"Не удалось открыть порт {self._config['com_port']}"
            self._log({
                "timestamp": datetime.now().isoformat(),
                "direction": "RX",
                "raw_hex": None,
                "parsed": {"slave_id": 0, "function": 4, "description": f"Сканирование ПП: {message}"},
                "response_time_ms": None
            })
            return {"status": "error", "error": message, "found": []}
        try:
            for slave_id in range(start_id, end_id + 1):
                try:
                    req_bytes = bytes([
                        slave_id, 0x03,
                        (status_base >> 8) & 0xFF, status_base & 0xFF,
                        0x00, 0x01
                    ])
                    req_crc = _crc16_modbus(req_bytes)
                    req_frame = req_bytes + bytes([req_crc & 0xFF, (req_crc >> 8) & 0xFF])
                    self._log({
                        "timestamp": datetime.now().isoformat(),
                        "direction": "TX",
                        "raw_hex": _hex_bytes(req_frame),
                        "parsed": {"slave_id": slave_id, "function": 3, "start_addr": status_base, "quantity": 1, "description": "Сканирование ПП: запрос 40000"},
                        "response_time_ms": None
                    })
                    values = scan_client.read_holding_registers(slave_id, status_base, 1)
                    resp_body = bytes([slave_id, 0x03, 0x02, (values[0] >> 8) & 0xFF, values[0] & 0xFF])
                    resp_crc = _crc16_modbus(resp_body)
                    resp_frame = resp_body + bytes([resp_crc & 0xFF, (resp_crc >> 8) & 0xFF])
                    self._log({
                        "timestamp": datetime.now().isoformat(),
                        "direction": "RX",
                        "raw_hex": _hex_bytes(resp_frame),
                        "parsed": {"slave_id": slave_id, "function": 3, "byte_count": 2, "values": values, "description": "Сканирование ПП: есть ответ"},
                        "response_time_ms": None
                    })
                    found.append({
                        "slave_id": slave_id,
                        "status_raw": values[0]
                    })
                except Exception as ex:
                    self._log({
                        "timestamp": datetime.now().isoformat(),
                        "direction": "RX",
                        "raw_hex": None,
                        "parsed": {"slave_id": slave_id, "function": 3, "description": f"Сканирование ПП: нет ответа ({ex})"},
                        "response_time_ms": None
                    })
                    continue
        finally:
            scan_client.close()
        return {
            "status": "ok",
            "com_port": self._config["com_port"],
            "timeout_ms": timeout_ms,
            "probe_address": status_base,
            "range": {"start": start_id, "end": end_id},
            "found": found
        }
