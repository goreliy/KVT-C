"""
Сценарии Drift - плавное изменение значений
"""

import random
from typing import Dict

from .base import BaseScenario


class _DriftScenario(BaseScenario):
    """Базовый drift-сценарий с настраиваемым направлением"""

    def __init__(self, drift_rate: float = 0.1, direction: float = 1.0, **kwargs):
        super().__init__(**kwargs)
        self.drift_rate = drift_rate
        self._direction = direction
        self._current_offset = 0.0

    def _generate_raw(self, sensor_id: int, limits: Dict[str, float]) -> tuple:
        offset = self._sensor_offset(sensor_id)
        self._current_offset += self.drift_rate * self._direction

        temp = self.temp_base + offset + self._current_offset
        temp += random.uniform(-self.temp_variation * 0.5, self.temp_variation * 0.5)

        hum = self.hum_base - self._current_offset * 0.5
        hum += random.uniform(-self.hum_variation, self.hum_variation)
        return temp, hum


class DriftUpScenario(_DriftScenario):
    """Плавное повышение температуры"""
    description = "Температура плавно растёт (имитация нагрева)"

    def __init__(self, drift_rate: float = 0.1, **kwargs):
        super().__init__(drift_rate=drift_rate, direction=1.0, **kwargs)


class DriftDownScenario(_DriftScenario):
    """Плавное понижение температуры"""
    description = "Температура плавно падает (имитация охлаждения)"

    def __init__(self, drift_rate: float = 0.1, **kwargs):
        super().__init__(drift_rate=drift_rate, direction=-1.0, **kwargs)
