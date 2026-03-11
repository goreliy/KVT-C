"""
Генератор значений для Modbus регистров
"""

import threading
import time
from typing import Dict, Any, Optional

from ..scenarios import get_scenario
from ..scenarios.base import BaseScenario
from ..utils import merge_config
from .registers import VirtualRegisters

# Лимиты по умолчанию для Modbus-генератора
_DEFAULT_LIMITS = {
    'temp_min': -10.0, 'temp_max': 40.0,
    'temp_warning_delta': 3.0, 'temp_alarm_delta': 5.0,
    'hum_min': 20.0, 'hum_max': 80.0,
    'hum_warning_delta': 5.0, 'hum_alarm_delta': 10.0,
}


class RegisterGenerator:
    """Генератор значений для виртуальных регистров"""

    def __init__(self, registers: VirtualRegisters, config: Dict[str, Any] = None):
        self.registers = registers
        self.config = config or self._default_config()
        self._scenario: Optional[BaseScenario] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._init_scenario()

    @staticmethod
    def _default_config() -> Dict[str, Any]:
        return {
            "update_interval_ms": 1000,
            "scenario": "normal",
            "values": {
                "temperature": {"min": -40.0, "max": 85.0, "base": 22.0, "variation": 2.0},
                "humidity": {"min": 0.0, "max": 100.0, "base": 45.0, "variation": 5.0}
            },
            "errors": {
                "error_rate": 0.0, "timeout_rate": 0.0,
                "crc_error_rate": 0.0, "offline_sensors": []
            },
            "per_sensor_overrides": {}
        }

    def _init_scenario(self):
        temp_cfg = self.config["values"]["temperature"]
        hum_cfg = self.config["values"]["humidity"]
        self._scenario = get_scenario(
            self.config.get("scenario", "normal"),
            temp_base=temp_cfg["base"], temp_variation=temp_cfg["variation"],
            temp_min=temp_cfg["min"], temp_max=temp_cfg["max"],
            hum_base=hum_cfg["base"], hum_variation=hum_cfg["variation"],
            hum_min=hum_cfg["min"], hum_max=hum_cfg["max"],
            offline_sensors=self.config["errors"].get("offline_sensors", [])
        )

    def _update_registers(self):
        for sensor_id in range(1, self.registers.sensor_count + 1):
            if sensor_id in self.config["errors"].get("offline_sensors", []):
                self.registers.set_sensor_values(sensor_id, 0, 0, 1, 1)
                continue

            value = self._scenario.get_value(sensor_id, _DEFAULT_LIMITS)
            temp_status = 0 if value.temp_status == "normal" else 1
            hum_status = 0 if value.hum_status == "normal" else 1
            if value.combined_status == "offline":
                temp_status = hum_status = 1

            self.registers.set_sensor_values(
                sensor_id, value.temperature, value.humidity, temp_status, hum_status
            )

    def _generation_loop(self):
        interval = self.config["update_interval_ms"] / 1000.0
        while self._running:
            self._update_registers()
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

    def set_scenario(self, scenario_name: str):
        self.config["scenario"] = scenario_name
        self._init_scenario()

    def update_config(self, new_config: Dict[str, Any]):
        was_running = self._running
        if was_running:
            self.stop()
        merge_config(self.config, new_config)
        self._init_scenario()
        if was_running:
            self.start()
