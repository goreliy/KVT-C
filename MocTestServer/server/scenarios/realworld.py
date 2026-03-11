"""
Реальные сценарии - имитация реальных ситуаций
"""

import math
import random
from datetime import datetime
from typing import Dict

from .base import BaseScenario, SensorValue, OFFLINE_VALUE


class DailyCycleScenario(BaseScenario):
    """Суточный цикл температуры"""

    description = "Имитация суточного цикла температуры (день/ночь)"

    def __init__(self, day_temp: float = 25.0, night_temp: float = 18.0, **kwargs):
        super().__init__(**kwargs)
        self.day_temp = day_temp
        self.night_temp = night_temp

    def _generate_raw(self, sensor_id: int, limits: Dict[str, float]) -> tuple:
        now = datetime.now()
        hour = now.hour + now.minute / 60.0
        daily_factor = math.sin((hour - 8) * math.pi / 12)

        temp_amplitude = (self.day_temp - self.night_temp) / 2
        temp_center = (self.day_temp + self.night_temp) / 2

        temp = temp_center + temp_amplitude * daily_factor + self._sensor_offset(sensor_id, 0.2)
        temp += random.uniform(-1, 1)

        hum = self.hum_base - 10 * daily_factor
        hum += random.uniform(-self.hum_variation, self.hum_variation)
        return temp, hum


class HVACControlScenario(BaseScenario):
    """Имитация работы HVAC системы"""

    description = "Имитация работы системы кондиционирования (вкл/выкл)"

    def __init__(self, setpoint: float = 22.0, hysteresis: float = 1.0, **kwargs):
        super().__init__(**kwargs)
        self.setpoint = setpoint
        self.hysteresis = hysteresis
        self._hvac_on = False
        self._current_temp = setpoint

    def _generate_raw(self, sensor_id: int, limits: Dict[str, float]) -> tuple:
        if self._current_temp > self.setpoint + self.hysteresis:
            self._hvac_on = True
        elif self._current_temp < self.setpoint - self.hysteresis:
            self._hvac_on = False

        if self._hvac_on:
            self._current_temp -= random.uniform(0.1, 0.3)
        else:
            self._current_temp += random.uniform(0.05, 0.15)

        temp = self._current_temp + self._sensor_offset(sensor_id, 0.1) + random.uniform(-0.3, 0.3)
        hum = self.hum_base + (5 if self._hvac_on else -2)
        hum += random.uniform(-self.hum_variation * 0.5, self.hum_variation * 0.5)
        return temp, hum


class DoorOpenScenario(BaseScenario):
    """Имитация открытия двери"""

    description = "Периодическое открытие двери (резкие изменения)"

    def __init__(self, open_probability: float = 0.1, outside_temp: float = 35.0, **kwargs):
        super().__init__(**kwargs)
        self.open_probability = open_probability
        self.outside_temp = outside_temp
        self._door_open = False
        self._door_timer = 0

    def _generate_raw(self, sensor_id: int, limits: Dict[str, float]) -> tuple:
        if not self._door_open and random.random() < self.open_probability:
            self._door_open = True
            self._door_timer = random.randint(5, 15)

        if self._door_open:
            self._door_timer -= 1
            if self._door_timer <= 0:
                self._door_open = False

        if self._door_open:
            temp = self.temp_base + (self.outside_temp - self.temp_base) * 0.3
            temp += random.uniform(-2, 2)
            hum = self.hum_base + random.uniform(-10, 10)
        else:
            temp = self.temp_base + self._sensor_offset(sensor_id)
            temp += random.uniform(-self.temp_variation, self.temp_variation)
            hum = self.hum_base + random.uniform(-self.hum_variation, self.hum_variation)
        return temp, hum


class PowerOutageScenario(BaseScenario):
    """Имитация отключения питания"""

    description = "Отключение питания с последующим восстановлением"

    def __init__(self, outage_probability: float = 0.05, **kwargs):
        super().__init__(**kwargs)
        self.outage_probability = outage_probability
        self._power_off = False
        self._outage_timer = 0

    def _generate_raw(self, sensor_id: int, limits: Dict[str, float]) -> tuple:
        offset = self._sensor_offset(sensor_id)
        temp = self.temp_base + offset + random.uniform(-self.temp_variation, self.temp_variation)
        hum = self.hum_base + random.uniform(-self.hum_variation, self.hum_variation)
        return temp, hum

    def get_value(self, sensor_id: int, limits: Dict[str, float]) -> SensorValue:
        if not self._power_off and random.random() < self.outage_probability:
            self._power_off = True
            self._outage_timer = random.randint(10, 30)

        if self._power_off:
            self._outage_timer -= 1
            if self._outage_timer <= 0:
                self._power_off = False
            return SensorValue(
                temperature=0.0, humidity=0.0,
                temp_status="offline", hum_status="offline",
                combined_status="offline", modbus_error="no_power"
            )
        return super().get_value(sensor_id, limits)


class SensorFailureScenario(BaseScenario):
    """Имитация выхода датчика из строя"""

    description = "Постепенный выход датчиков из строя"

    def __init__(self, failure_rate: float = 0.01, **kwargs):
        super().__init__(**kwargs)
        self.failure_rate = failure_rate
        self._failed_sensors: set = set()

    def _generate_raw(self, sensor_id: int, limits: Dict[str, float]) -> tuple:
        offset = self._sensor_offset(sensor_id)
        temp = self.temp_base + offset + random.uniform(-self.temp_variation, self.temp_variation)
        hum = self.hum_base + random.uniform(-self.hum_variation, self.hum_variation)
        return temp, hum

    def get_value(self, sensor_id: int, limits: Dict[str, float]) -> SensorValue:
        if sensor_id not in self._failed_sensors:
            if random.random() < self.failure_rate:
                self._failed_sensors.add(sensor_id)

        if sensor_id in self._failed_sensors:
            if random.random() < 0.5:
                return SensorValue(
                    temperature=0.0, humidity=0.0,
                    temp_status="offline", hum_status="offline",
                    combined_status="offline", modbus_error="sensor_failure"
                )
            return SensorValue(
                temperature=round(random.uniform(-40, 85), 1),
                humidity=round(random.uniform(0, 100), 1),
                temp_status="alarm", hum_status="alarm",
                combined_status="alarm", modbus_error="invalid_data"
            )
        return super().get_value(sensor_id, limits)
