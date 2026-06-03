import os
from typing import Dict, Any
from shared.config_manager import load_poller_config, save_poller_config


DEFAULT_POLLER_CONFIG: Dict[str, Any] = {
    "transport": "serial",
    "use_mock_server": False,
    "mock_server_url": "http://127.0.0.1:8000",
    "mock_server_host": "127.0.0.1",
    "mock_server_port": 8000,
    "com_port": "COM8",
    "device_slave_id": 16,
    "baudrate": 9600,
    "bytesize": 8,
    "parity": "N",
    "stopbits": 1,
    "udp_host": "127.0.0.1",
    "udp_port": 502,
    "timeout_ms": 500,
    "poll_period_ms": 1000,
    "retry_count": 3,
    "value_register_base": 30000,
    "status_register_base": 40000,
    "log_max_entries": 1000,
    "auto_start": True,
}


def normalized_poller_config() -> Dict[str, Any]:
    cfg = load_poller_config()
    merged = {**DEFAULT_POLLER_CONFIG, **cfg}
    if merged != cfg:
        save_poller_config(merged)
    return merged


def _as_int(config: Dict[str, Any], key: str, errors, min_value=None, max_value=None):
    try:
        value = int(config[key])
    except (TypeError, ValueError):
        errors.append(f"{key} должен быть числом")
        return
    if min_value is not None and value < min_value:
        errors.append(f"{key} должен быть >= {min_value}")
    if max_value is not None and value > max_value:
        errors.append(f"{key} должен быть <= {max_value}")
    config[key] = value


def validated_poller_config_patch(patch: Dict[str, Any], base: Dict[str, Any] = None):
    allowed = set(DEFAULT_POLLER_CONFIG)
    unknown = sorted(set(patch) - allowed)
    if unknown:
        return None, [f"Неизвестные поля: {', '.join(unknown)}"]

    merged = {**(base or normalized_poller_config()), **patch}
    errors = []

    transport = str(merged.get("transport", "serial")).lower()
    if transport not in ("serial", "udp"):
        errors.append("transport должен быть serial или udp")
    merged["transport"] = transport

    for key, min_value, max_value in (
        ("device_slave_id", 0, 247),
        ("baudrate", 1, None),
        ("bytesize", 5, 8),
        ("stopbits", 1, 2),
        ("udp_port", 1, 65535),
        ("timeout_ms", 50, 10000),
        ("poll_period_ms", 100, 60000),
        ("retry_count", 0, 10),
        ("value_register_base", 0, 65535),
        ("status_register_base", 0, 65535),
        ("log_max_entries", 1, 50000),
        ("mock_server_port", 1, 65535),
    ):
        _as_int(merged, key, errors, min_value, max_value)

    merged["parity"] = str(merged.get("parity", "N")).upper()
    if merged["parity"] not in ("N", "E", "O", "M", "S"):
        errors.append("parity должен быть N, E, O, M или S")

    if transport == "serial" and not str(merged.get("com_port", "")).strip():
        errors.append("com_port обязателен для serial")
    if transport == "udp" and not str(merged.get("udp_host", "")).strip():
        errors.append("udp_host обязателен для UDP")

    merged["com_port"] = str(merged.get("com_port", "")).strip()
    merged["udp_host"] = str(merged.get("udp_host", "")).strip()
    merged["mock_server_host"] = str(merged.get("mock_server_host", "127.0.0.1")).strip()
    merged["mock_server_url"] = str(merged.get("mock_server_url", "http://127.0.0.1:8000")).strip()
    merged["use_mock_server"] = bool(merged.get("use_mock_server", False))
    merged["auto_start"] = bool(merged.get("auto_start", True))

    return (None, errors) if errors else (merged, [])


def data_dir() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "data")
