"""
Генератор current.json - эмуляция выхода Modbus Poller
"""

import json
import os
import random
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

from ..scenarios import get_scenario
from ..scenarios.base import BaseScenario
from ..utils import merge_config

# Базовая директория проекта (KVT-C)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DATA_DIR = os.path.join(BASE_DIR, 'data')


class CurrentGenerator:
    """Генератор файла current.json"""

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or self._default_config()
        self._scenario: Optional[BaseScenario] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._poll_count = 0
        self._successful_polls = 0
        self._failed_polls = 0
        self._last_error: Optional[str] = None
        self._current_data: Dict[str, Any] = {}
        self._log_entries: List[Dict] = []
        self._init_scenario()

    @staticmethod
    def _default_config() -> Dict[str, Any]:
        return {
            "output": {
                "current_path": "../data/current.json",
                "log_path": "../data/modbus_log.json",
                "generate_log": True,
                "log_max_entries": 1000
            },
            "generation": {"enabled": True, "interval_ms": 1000, "scenario": "normal"},
            "sensors": {
                "count": 10, "name_prefix": "ХРАН. №",
                "modbus_slave_id": 16, "start_modbus_addr": 1,
                "value_register_base": 30000, "status_register_base": 40000
            },
            "values": {
                "temperature": {"min": -40.0, "max": 85.0, "base": 22.0, "variation": 2.0},
                "humidity": {"min": 0.0, "max": 100.0, "base": 45.0, "variation": 5.0}
            },
            "limits": {
                "temperature": {"min": -10.0, "max": 40.0, "warning_delta": 3.0, "alarm_delta": 5.0},
                "humidity": {"min": 20.0, "max": 80.0, "warning_delta": 5.0, "alarm_delta": 10.0}
            },
            "errors": {"error_rate": 0.0, "offline_sensors": []},
            "per_sensor_overrides": {}
        }

    def _init_scenario(self):
        temp_cfg = self.config["values"]["temperature"]
        hum_cfg = self.config["values"]["humidity"]
        self._scenario = get_scenario(
            self.config["generation"]["scenario"],
            temp_base=temp_cfg["base"], temp_variation=temp_cfg["variation"],
            temp_min=temp_cfg["min"], temp_max=temp_cfg["max"],
            hum_base=hum_cfg["base"], hum_variation=hum_cfg["variation"],
            hum_min=hum_cfg["min"], hum_max=hum_cfg["max"],
            offline_sensors=self.config["errors"].get("offline_sensors", [])
        )

    def _get_limits_dict(self) -> Dict:
        tl = self.config["limits"]["temperature"]
        hl = self.config["limits"]["humidity"]
        return {
            'temp_min': tl["min"], 'temp_max': tl["max"],
            'temp_warning_delta': tl["warning_delta"], 'temp_alarm_delta': tl["alarm_delta"],
            'hum_min': hl["min"], 'hum_max': hl["max"],
            'hum_warning_delta': hl["warning_delta"], 'hum_alarm_delta': hl["alarm_delta"],
        }

    # ── Генерация данных датчика ──

    def _generate_sensor_data(self, sensor_id: int) -> Dict[str, Any]:
        cfg = self.config["sensors"]
        now = datetime.now()
        base_addr = cfg["start_modbus_addr"] + (sensor_id - 1) * 2

        base_info = {
            "id": sensor_id,
            "name": f"{cfg['name_prefix']} {sensor_id}",
            "modbus_slave_id": cfg["modbus_slave_id"],
            "modbus_addr_temp": base_addr,
            "modbus_addr_hum": base_addr + 1,
        }

        if sensor_id in self.config["errors"].get("offline_sensors", []):
            return {**base_info, **self._offline_sensor_data(now)}

        value = self._scenario.get_value(sensor_id, self._get_limits_dict())
        return {
            **base_info,
            "temperature": {
                "value": value.temperature, "raw": int(value.temperature * 10),
                "status": value.temp_status, "modbus_status": 0,
                "timestamp": now.isoformat()
            },
            "humidity": {
                "value": value.humidity, "raw": int(value.humidity * 10),
                "status": value.hum_status, "modbus_status": 0,
                "timestamp": now.isoformat()
            },
            "combined_status": value.combined_status
        }

    @staticmethod
    def _offline_sensor_data(now: datetime) -> Dict:
        offline_param = {
            "value": None, "raw": None, "status": "offline",
            "modbus_status": 1, "timestamp": now.isoformat()
        }
        return {
            "temperature": offline_param.copy(),
            "humidity": offline_param.copy(),
            "combined_status": "offline"
        }

    # ── Генерация current.json ──

    def generate_current_json(self) -> Dict[str, Any]:
        sensor_count = self.config["sensors"]["count"]
        sensors = [self._generate_sensor_data(sid) for sid in range(1, sensor_count + 1)]

        self._poll_count += 1
        self._successful_polls += 1

        data = {
            "timestamp": datetime.now().isoformat(),
            "poll_period_ms": self.config["generation"]["interval_ms"],
            "com_port": "MOCK", "baudrate": 9600,
            "sensors": sensors,
            "statistics": {
                "total_polls": self._poll_count,
                "successful_polls": self._successful_polls,
                "failed_polls": self._failed_polls,
                "last_error": self._last_error
            },
            "_mock": {
                "generator": "mock_current_generator",
                "scenario": self.config["generation"]["scenario"],
                "version": "1.0"
            }
        }
        self._current_data = data
        return data

    # ── Генерация лога Modbus ──

    @staticmethod
    def _fake_crc() -> str:
        return f"{random.randint(0, 255):02X} {random.randint(0, 255):02X}"

    def _make_log_tx(self, slave_id: int, start_addr: int, quantity: int,
                     sensor_id: int, desc_type: str, timestamp: datetime) -> Dict:
        raw = (f"{slave_id:02X} 04 "
               f"{(start_addr >> 8):02X} {(start_addr & 0xFF):02X} "
               f"00 {quantity:02X} {self._fake_crc()}")
        return {
            "timestamp": timestamp.isoformat(), "direction": "TX",
            "raw_hex": raw,
            "parsed": {
                "slave_id": slave_id, "function": 4,
                "start_addr": start_addr, "quantity": quantity,
                "description": f"Запрос {desc_type} датчика {sensor_id}"
            },
            "response_time_ms": None
        }

    def _make_log_rx_timeout(self, sensor_id: int, desc_type: str,
                             timestamp: datetime, response_time_ms: float) -> Dict:
        return {
            "timestamp": timestamp.isoformat(), "direction": "RX",
            "raw_hex": None,
            "parsed": {"error": "timeout", "description": f"Таймаут ответа {desc_type} от датчика {sensor_id}"},
            "response_time_ms": response_time_ms
        }

    def _make_log_rx_values(self, slave_id: int, values: List[int], description: str,
                            timestamp: datetime, response_time_ms: float) -> Dict:
        byte_count = len(values) * 2
        values_hex = " ".join(f"{(v >> 8) & 0xFF:02X} {v & 0xFF:02X}" for v in values)
        raw = f"{slave_id:02X} 04 {byte_count:02X} {values_hex} {self._fake_crc()}"
        return {
            "timestamp": timestamp.isoformat(), "direction": "RX",
            "raw_hex": raw,
            "parsed": {
                "slave_id": slave_id, "function": 4,
                "byte_count": byte_count, "values": values,
                "description": description
            },
            "response_time_ms": response_time_ms
        }

    def _generate_log_entries(self, sensor_data: Dict) -> List[Dict]:
        """Генерация записей лога Modbus (TX/RX) для одного датчика"""
        entries = []
        now = datetime.now()
        slave_id = sensor_data["modbus_slave_id"]
        sid = sensor_data["id"]
        is_offline = sensor_data["combined_status"] == "offline"

        value_base = self.config["sensors"].get("value_register_base", 30000)
        status_base = self.config["sensors"].get("status_register_base", 40000)
        addr_val = value_base + (sid - 1) * 2
        addr_stat = status_base + (sid - 1) * 2

        resp_ms = round(random.uniform(5, 30), 2)

        # TX/RX для значений
        tx_time = now
        entries.append(self._make_log_tx(slave_id, addr_val, 2, sid, "значений", tx_time))

        rx_time = tx_time + timedelta(milliseconds=random.randint(5, 25))
        if is_offline:
            entries.append(self._make_log_rx_timeout(sid, "", rx_time, resp_ms))
        else:
            temp_raw = sensor_data["temperature"]["raw"] or 0
            hum_raw = sensor_data["humidity"]["raw"] or 0
            desc = f"Ответ: T={sensor_data['temperature']['value']}°C, H={sensor_data['humidity']['value']}%"
            entries.append(self._make_log_rx_values(slave_id, [temp_raw, hum_raw], desc, rx_time, resp_ms))

        # TX/RX для статусов
        resp_ms_2 = round(random.uniform(5, 30), 2)
        tx_stat_time = rx_time + timedelta(milliseconds=random.randint(10, 30))
        entries.append(self._make_log_tx(slave_id, addr_stat, 2, sid, "статусов", tx_stat_time))

        rx_stat_time = tx_stat_time + timedelta(milliseconds=random.randint(5, 25))
        if is_offline:
            entries.append(self._make_log_rx_timeout(sid, "статусов", rx_stat_time, resp_ms_2))
        else:
            ts = sensor_data["temperature"]["modbus_status"]
            hs = sensor_data["humidity"]["modbus_status"]
            status_desc = "OK" if ts == 0 and hs == 0 else f"T:{ts}, H:{hs}"
            entries.append(self._make_log_rx_values(
                slave_id, [ts, hs], f"Ответ: статусы {status_desc}", rx_stat_time, resp_ms_2
            ))

        return entries

    # ── Запись файлов ──

    def _write_files(self, data: Dict):
        output_cfg = self.config["output"]
        os.makedirs(DATA_DIR, exist_ok=True)

        current_path = os.path.join(DATA_DIR, 'current.json')
        with open(current_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        if output_cfg["generate_log"]:
            for sensor in data["sensors"]:
                self._log_entries.extend(self._generate_log_entries(sensor))

            max_entries = output_cfg["log_max_entries"]
            if len(self._log_entries) > max_entries:
                self._log_entries = self._log_entries[-max_entries:]

            log_path = os.path.join(DATA_DIR, 'modbus_log.json')
            with open(log_path, 'w', encoding='utf-8') as f:
                json.dump({"max_entries": max_entries, "entries": self._log_entries},
                          f, ensure_ascii=False, indent=2)

    # ── Управление генерацией ──

    def _generation_loop(self):
        interval = self.config["generation"]["interval_ms"] / 1000.0
        while self._running:
            try:
                data = self.generate_current_json()
                self._write_files(data)
            except Exception as e:
                self._failed_polls += 1
                self._last_error = str(e)
            time.sleep(interval)

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._generation_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def generate_once(self) -> Dict[str, Any]:
        data = self.generate_current_json()
        self._write_files(data)
        return data

    def get_preview(self) -> Dict[str, Any]:
        return self.generate_current_json()

    def get_status(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "scenario": self.config["generation"]["scenario"],
            "interval_ms": self.config["generation"]["interval_ms"],
            "sensor_count": self.config["sensors"]["count"],
            "output_path": DATA_DIR,
            "current_file": os.path.join(DATA_DIR, 'current.json'),
            "log_file": os.path.join(DATA_DIR, 'modbus_log.json'),
            "statistics": {
                "total_polls": self._poll_count,
                "successful_polls": self._successful_polls,
                "failed_polls": self._failed_polls,
                "last_error": self._last_error
            }
        }

    def update_config(self, new_config: Dict[str, Any]):
        was_running = self._running
        if was_running:
            self.stop()
        merge_config(self.config, new_config)
        self._init_scenario()
        if was_running:
            self.start()

    def set_scenario(self, scenario_name: str):
        self.config["generation"]["scenario"] = scenario_name
        self._init_scenario()

    def set_sensor_value(self, sensor_id: int, temperature: float = None, humidity: float = None):
        overrides = self.config.setdefault("per_sensor_overrides", {})
        sensor_ovr = overrides.setdefault(sensor_id, {})
        if temperature is not None:
            sensor_ovr["temp_base"] = temperature
            sensor_ovr["temp_variation"] = 0.1
        if humidity is not None:
            sensor_ovr["hum_base"] = humidity
            sensor_ovr["hum_variation"] = 0.1
