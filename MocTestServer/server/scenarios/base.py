"""
Базовый класс сценария генерации данных
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class SensorValue:
    """Значение датчика"""
    temperature: float
    humidity: float
    temp_status: str = "normal"
    hum_status: str = "normal"
    combined_status: str = "normal"
    modbus_error: Optional[str] = None


# Предустановленное значение для offline-датчиков
OFFLINE_VALUE = SensorValue(
    temperature=0.0, humidity=0.0,
    temp_status="offline", hum_status="offline",
    combined_status="offline", modbus_error="timeout"
)


class BaseScenario(ABC):
    """Базовый класс для всех сценариев"""

    description = "Base scenario"

    def __init__(
        self,
        temp_base: float = 22.0,
        temp_variation: float = 2.0,
        temp_min: float = -40.0,
        temp_max: float = 85.0,
        hum_base: float = 45.0,
        hum_variation: float = 5.0,
        hum_min: float = 0.0,
        hum_max: float = 100.0,
        offline_sensors: list = None,
        **kwargs
    ):
        self.temp_base = temp_base
        self.temp_variation = temp_variation
        self.temp_min = temp_min
        self.temp_max = temp_max
        self.hum_base = hum_base
        self.hum_variation = hum_variation
        self.hum_min = hum_min
        self.hum_max = hum_max
        self.offline_sensors = offline_sensors or []
        self._iteration = 0

    @abstractmethod
    def _generate_raw(self, sensor_id: int, limits: Dict[str, float]) -> tuple:
        """
        Генерация сырых значений температуры и влажности.

        Returns:
            (temperature: float, humidity: float)
        """
        pass

    def get_value(self, sensor_id: int, limits: Dict[str, float]) -> SensorValue:
        """Получить значение для датчика с автоматическим расчётом статусов."""
        if sensor_id in self.offline_sensors:
            return OFFLINE_VALUE

        temp, hum = self._generate_raw(sensor_id, limits)

        temp = round(self._clamp(temp, self.temp_min, self.temp_max), 1)
        hum = round(self._clamp(hum, self.hum_min, self.hum_max), 1)

        temp_status = self._calculate_status(
            temp,
            limits.get('temp_min', -10), limits.get('temp_max', 40),
            limits.get('temp_warning_delta', 3), limits.get('temp_alarm_delta', 5)
        )
        hum_status = self._calculate_status(
            hum,
            limits.get('hum_min', 20), limits.get('hum_max', 80),
            limits.get('hum_warning_delta', 5), limits.get('hum_alarm_delta', 10)
        )

        return SensorValue(
            temperature=temp,
            humidity=hum,
            temp_status=temp_status,
            hum_status=hum_status,
            combined_status=self._get_combined_status(temp_status, hum_status)
        )

    def _sensor_offset(self, sensor_id: int, factor: float = 0.3) -> float:
        """Смещение значения для конкретного датчика"""
        return (sensor_id - 1) * factor

    @staticmethod
    def _clamp(value: float, min_val: float, max_val: float) -> float:
        return max(min_val, min(max_val, value))

    @staticmethod
    def _calculate_status(value: float, limit_min: float, limit_max: float,
                          warning_delta: float, alarm_delta: float) -> str:
        if value < limit_min - alarm_delta or value > limit_max + alarm_delta:
            return "alarm"
        if value < limit_min - warning_delta or value > limit_max + warning_delta:
            return "warning"
        if value < limit_min or value > limit_max:
            return "warning"
        return "normal"

    @staticmethod
    def _get_combined_status(temp_status: str, hum_status: str) -> str:
        if "alarm" in (temp_status, hum_status):
            return "alarm"
        if "warning" in (temp_status, hum_status):
            return "warning"
        return "normal"

    def tick(self):
        self._iteration += 1
