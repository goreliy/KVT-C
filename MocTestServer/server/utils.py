"""
Общие утилиты Mock Test Server
"""

from typing import Dict, Any

# Константы для 16-битных Modbus регистров
UINT16_MAX = 0xFFFF
INT16_MAX = 32767
INT16_OVERFLOW = 65536


def merge_config(base: Dict[str, Any], update: Dict[str, Any]) -> None:
    """Рекурсивное слияние конфигураций (мутирует base)"""
    for key, value in update.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            merge_config(base[key], value)
        else:
            base[key] = value


def to_raw_int16(value: float, scale: float = 10.0) -> int:
    """Конвертация float в raw 16-bit значение для Modbus регистра"""
    return int(value * scale) & UINT16_MAX


def from_raw_int16(raw: int, scale: float = 10.0, signed: bool = False) -> float:
    """Конвертация raw 16-bit значения обратно в float"""
    if signed and raw > INT16_MAX:
        raw = raw - INT16_OVERFLOW
    return raw / scale
