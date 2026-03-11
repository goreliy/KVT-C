"""
Сценарий Sine - синусоидальные колебания
"""

import math
import random
from typing import Dict

from .base import BaseScenario


class SineScenario(BaseScenario):
    """Синусоидальные колебания температуры"""

    description = "Периодические синусоидальные колебания температуры"

    def __init__(self, period: int = 60, amplitude: float = 5.0, **kwargs):
        super().__init__(**kwargs)
        self.period = period
        self.amplitude = amplitude

    def _generate_raw(self, sensor_id: int, limits: Dict[str, float]) -> tuple:
        offset = self._sensor_offset(sensor_id)
        phase_shift = (sensor_id - 1) * (2 * math.pi / 10)
        sine_value = math.sin(2 * math.pi * self._iteration / self.period + phase_shift)

        temp = self.temp_base + offset + self.amplitude * sine_value
        temp += random.uniform(-self.temp_variation * 0.3, self.temp_variation * 0.3)

        hum = self.hum_base - (self.amplitude * 2) * sine_value
        hum += random.uniform(-self.hum_variation * 0.5, self.hum_variation * 0.5)

        self._iteration += 1
        return temp, hum
