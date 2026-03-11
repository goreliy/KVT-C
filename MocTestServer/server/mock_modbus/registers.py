"""
Виртуальные Modbus регистры
"""

import threading
from typing import Dict

from ..utils import UINT16_MAX, INT16_MAX, INT16_OVERFLOW


class VirtualRegisters:
    """Управление виртуальными Modbus регистрами"""

    def __init__(self, value_base: int = 30000, status_base: int = 40000, sensor_count: int = 10):
        self.value_base = value_base
        self.status_base = status_base
        self.sensor_count = sensor_count
        self._registers: Dict[int, int] = {}
        self._lock = threading.Lock()
        self._init_registers()

    def _sensor_addrs(self, sensor_id: int):
        """Вычислить адреса регистров для датчика"""
        idx = (sensor_id - 1) * 2
        return (
            self.value_base + idx,       # temp value
            self.value_base + idx + 1,   # hum value
            self.status_base + idx,      # temp status
            self.status_base + idx + 1,  # hum status
        )

    def _init_registers(self):
        for sensor_id in range(1, self.sensor_count + 1):
            t_addr, h_addr, ts_addr, hs_addr = self._sensor_addrs(sensor_id)
            self._registers[t_addr] = 220   # 22.0°C
            self._registers[h_addr] = 450   # 45.0%
            self._registers[ts_addr] = 0    # OK
            self._registers[hs_addr] = 0    # OK

    def get_register(self, address: int) -> int:
        with self._lock:
            return self._registers.get(address, 0)

    def set_register(self, address: int, value: int):
        with self._lock:
            self._registers[address] = value & UINT16_MAX

    def get_registers(self, start_address: int, count: int) -> list:
        return [self.get_register(start_address + i) for i in range(count)]

    def set_registers(self, start_address: int, values: list):
        for i, value in enumerate(values):
            self.set_register(start_address + i, value)

    def set_sensor_values(self, sensor_id: int, temperature: float, humidity: float,
                          temp_status: int = 0, hum_status: int = 0):
        """Установить значения датчика"""
        t_addr, h_addr, ts_addr, hs_addr = self._sensor_addrs(sensor_id)
        temp_raw = int(temperature * 10) & UINT16_MAX
        hum_raw = int(humidity * 10) & UINT16_MAX

        with self._lock:
            self._registers[t_addr] = temp_raw
            self._registers[h_addr] = hum_raw
            self._registers[ts_addr] = temp_status
            self._registers[hs_addr] = hum_status

    def get_sensor_values(self, sensor_id: int) -> Dict:
        """Получить значения датчика"""
        t_addr, h_addr, ts_addr, hs_addr = self._sensor_addrs(sensor_id)

        with self._lock:
            temp_raw = self._registers.get(t_addr, 0)
            hum_raw = self._registers.get(h_addr, 0)
            temp_status = self._registers.get(ts_addr, 0)
            hum_status = self._registers.get(hs_addr, 0)

        # Обработка знака для температуры
        temp_signed = temp_raw - INT16_OVERFLOW if temp_raw > INT16_MAX else temp_raw

        return {
            "temperature": {
                "value": temp_signed / 10.0,
                "raw": temp_raw,
                "address": t_addr,
                "status": temp_status
            },
            "humidity": {
                "value": hum_raw / 10.0,
                "raw": hum_raw,
                "address": h_addr,
                "status": hum_status
            }
        }

    def get_all_values(self) -> Dict:
        return {sid: self.get_sensor_values(sid) for sid in range(1, self.sensor_count + 1)}
