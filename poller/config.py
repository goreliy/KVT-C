import os
from typing import Dict, Any
from shared.config_manager import load_poller_config, save_poller_config


DEFAULT_POLLER_CONFIG: Dict[str, Any] = {
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


def data_dir() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "data")
