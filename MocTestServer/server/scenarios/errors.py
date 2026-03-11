"""
Сценарии ошибок - имитация различных сбоев
"""

import random
from typing import Dict

from .base import BaseScenario, SensorValue, OFFLINE_VALUE


class OfflineScenario(BaseScenario):
    """Все датчики недоступны"""

    description = "Все датчики недоступны (имитация потери связи)"

    def _generate_raw(self, sensor_id: int, limits: Dict[str, float]) -> tuple:
        return 0.0, 0.0

    def get_value(self, sensor_id: int, limits: Dict[str, float]) -> SensorValue:
        return OFFLINE_VALUE


class _RandomFailureScenario(BaseScenario):
    """Базовый сценарий со случайными сбоями"""

    def __init__(self, failure_rate: float = 0.2, error_type: str = "timeout", **kwargs):
        super().__init__(**kwargs)
        self.failure_rate = failure_rate
        self.error_type = error_type

    def _generate_raw(self, sensor_id: int, limits: Dict[str, float]) -> tuple:
        offset = self._sensor_offset(sensor_id)
        temp = self.temp_base + offset + random.uniform(-self.temp_variation, self.temp_variation)
        hum = self.hum_base + random.uniform(-self.hum_variation, self.hum_variation)
        return temp, hum

    def get_value(self, sensor_id: int, limits: Dict[str, float]) -> SensorValue:
        if random.random() < self.failure_rate:
            return SensorValue(
                temperature=0.0, humidity=0.0,
                temp_status="offline", hum_status="offline",
                combined_status="offline", modbus_error=self.error_type
            )
        return super().get_value(sensor_id, limits)


class IntermittentScenario(_RandomFailureScenario):
    """Периодические сбои связи"""
    description = "Периодические сбои связи с датчиками"

    def __init__(self, failure_rate: float = 0.2, **kwargs):
        super().__init__(failure_rate=failure_rate, error_type="timeout", **kwargs)


class TimeoutScenario(_RandomFailureScenario):
    """Медленные ответы с таймаутами"""
    description = "Медленные ответы от датчиков с частыми таймаутами"

    def __init__(self, timeout_rate: float = 0.3, **kwargs):
        super().__init__(failure_rate=timeout_rate, error_type="timeout", **kwargs)


class CRCErrorScenario(_RandomFailureScenario):
    """Ошибки CRC"""
    description = "Частые ошибки контрольной суммы CRC"

    def __init__(self, crc_error_rate: float = 0.15, **kwargs):
        super().__init__(failure_rate=crc_error_rate, error_type="crc_error", **kwargs)


class PartialOfflineScenario(BaseScenario):
    """Часть датчиков недоступна"""

    description = "Некоторые датчики недоступны (частичная потеря связи)"

    def __init__(self, offline_probability: float = 0.3, **kwargs):
        super().__init__(**kwargs)
        self._offline_sensors = {
            i for i in range(1, 11) if random.random() < offline_probability
        }

    def _generate_raw(self, sensor_id: int, limits: Dict[str, float]) -> tuple:
        offset = self._sensor_offset(sensor_id)
        temp = self.temp_base + offset + random.uniform(-self.temp_variation, self.temp_variation)
        hum = self.hum_base + random.uniform(-self.hum_variation, self.hum_variation)
        return temp, hum

    def get_value(self, sensor_id: int, limits: Dict[str, float]) -> SensorValue:
        if sensor_id in self._offline_sensors or sensor_id in self.offline_sensors:
            return OFFLINE_VALUE
        return super().get_value(sensor_id, limits)
