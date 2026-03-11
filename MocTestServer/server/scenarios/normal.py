"""
Сценарий Normal - стабильные значения с небольшими колебаниями
"""

import random
from typing import Dict

from .base import BaseScenario


class NormalScenario(BaseScenario):
    """Нормальное поведение - стабильные значения"""

    description = "Стабильные значения с небольшими случайными колебаниями"

    def _generate_raw(self, sensor_id: int, limits: Dict[str, float]) -> tuple:
        offset = self._sensor_offset(sensor_id)
        temp = self.temp_base + offset + random.uniform(-self.temp_variation, self.temp_variation)
        hum = self.hum_base + random.uniform(-self.hum_variation, self.hum_variation)
        return temp, hum
