import os
from typing import Any, Dict

from shared.config_manager import load_poller_config, save_poller_config


DEFAULT_POLL_PORT: Dict[str, Any] = {
    "id": "default",
    "name": "COM8",
    "enabled": True,
    "transport": "serial",
    "com_port": "COM8",
    "device_slave_id": 16,
    "baudrate": 9600,
    "bytesize": 8,
    "parity": "N",
    "stopbits": 1,
    "remote_host": "",
    "remote_port": 502,
    "local_host": "",
    "local_port": 0,
    "timeout_ms": 500,
    # Пер-линейные параметры опроса. 0 = использовать общий параметр.
    "poll_period_ms": 0,
    "retry_count": -1,  # -1 = использовать общий retry_count
    # Период «медленного цикла»: датчик, не ответивший retry_count раз подряд,
    # опрашивается реже (раз в slow_poll_period_ms), но НИКОГДА не выпадает из опроса.
    "slow_poll_period_ms": 30000,
    "prefix": "0x10",
    "channel": "0x10",
    "seq_start": "0x21D1",
    "seq_persist": True,
}


DEFAULT_POLLER_CONFIG: Dict[str, Any] = {
    "transport": "serial",
    "use_mock_server": False,
    "mock_server_url": "http://0.0.0.0:8000",
    "mock_server_host": "0.0.0.0",
    "mock_server_port": 8000,
    "com_port": "COM8",
    "device_slave_id": 16,
    "baudrate": 9600,
    "bytesize": 8,
    "parity": "N",
    "stopbits": 1,
    "udp_host": "",
    "udp_port": 502,
    "timeout_ms": 500,
    "poll_period_ms": 1000,
    "retry_count": 3,
    "value_register_base": 30000,
    "status_register_base": 40000,
    "log_max_entries": 1000,
    "auto_start": True,
    "poll_ports": [DEFAULT_POLL_PORT],
}


def data_dir() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "data")


def _legacy_poll_port(config: Dict[str, Any]) -> Dict[str, Any]:
    transport = str(config.get("transport", "serial")).lower()
    if transport == "udp":
        transport = "udp_rtu"
    remote_host = config.get("remote_host") or config.get("udp_host", DEFAULT_POLL_PORT["remote_host"])
    port = {**DEFAULT_POLL_PORT}
    port.update({
        "id": "default",
        "name": str(config.get("com_port") if transport == "serial" else remote_host).strip() or "default",
        "enabled": True,
        "transport": transport,
        "com_port": config.get("com_port", DEFAULT_POLL_PORT["com_port"]),
        "device_slave_id": config.get("device_slave_id", DEFAULT_POLL_PORT["device_slave_id"]),
        "baudrate": config.get("baudrate", DEFAULT_POLL_PORT["baudrate"]),
        "bytesize": config.get("bytesize", DEFAULT_POLL_PORT["bytesize"]),
        "parity": config.get("parity", DEFAULT_POLL_PORT["parity"]),
        "stopbits": config.get("stopbits", DEFAULT_POLL_PORT["stopbits"]),
        "remote_host": remote_host,
        "remote_port": config.get("remote_port") or config.get("udp_port", DEFAULT_POLL_PORT["remote_port"]),
        "local_host": config.get("local_host", DEFAULT_POLL_PORT["local_host"]),
        "local_port": config.get("local_port", DEFAULT_POLL_PORT["local_port"]),
        "timeout_ms": config.get("timeout_ms", DEFAULT_POLL_PORT["timeout_ms"]),
    })
    return port


def _sync_legacy_fields(config: Dict[str, Any]) -> Dict[str, Any]:
    ports = config.get("poll_ports") or []
    primary = ports[0] if ports else _legacy_poll_port(config)
    transport = str(primary.get("transport", "serial")).lower()
    config["transport"] = "udp" if transport in ("udp_rtu", "udp_c2000pp") else "serial"
    config["com_port"] = primary.get("com_port", config.get("com_port", "COM8"))
    config["device_slave_id"] = primary.get("device_slave_id", config.get("device_slave_id", 16))
    config["baudrate"] = primary.get("baudrate", config.get("baudrate", 9600))
    config["bytesize"] = primary.get("bytesize", config.get("bytesize", 8))
    config["parity"] = primary.get("parity", config.get("parity", "N"))
    config["stopbits"] = primary.get("stopbits", config.get("stopbits", 1))
    config["udp_host"] = primary.get("remote_host", config.get("udp_host", ""))
    config["udp_port"] = primary.get("remote_port", config.get("udp_port", 502))
    return config


def normalized_poller_config() -> Dict[str, Any]:
    cfg = load_poller_config()
    merged = {**DEFAULT_POLLER_CONFIG, **cfg}
    if not isinstance(merged.get("poll_ports"), list) or not merged.get("poll_ports"):
        merged["poll_ports"] = [_legacy_poll_port(merged)]

    normalized_ports = []
    for index, port in enumerate(merged["poll_ports"]):
        normalized = {**DEFAULT_POLL_PORT, **(port or {})}
        normalized["id"] = str(normalized.get("id") or f"port_{index + 1}").strip()
        normalized["name"] = str(normalized.get("name") or normalized["id"]).strip()
        normalized["transport"] = str(normalized.get("transport", "serial")).lower()
        if normalized["transport"] == "udp":
            normalized["transport"] = "udp_rtu"
        normalized_ports.append(normalized)
    merged["poll_ports"] = normalized_ports
    merged = _sync_legacy_fields(merged)
    if merged != cfg:
        save_poller_config(merged)
    return merged


def _as_int(config: Dict[str, Any], key: str, errors, min_value=None, max_value=None):
    try:
        value = int(config[key])
    except (TypeError, ValueError, KeyError):
        errors.append(f"{key} должен быть числом")
        return
    if min_value is not None and value < min_value:
        errors.append(f"{key} должен быть >= {min_value}")
    if max_value is not None and value > max_value:
        errors.append(f"{key} должен быть <= {max_value}")
    config[key] = value


def _parse_hex_byte(value, key, errors):
    try:
        parsed = int(str(value), 0)
    except (TypeError, ValueError):
        errors.append(f"{key} должен быть байтом, например 0x10")
        return value
    if not (0 <= parsed <= 0xFF):
        errors.append(f"{key} должен быть в диапазоне 0..255")
    return f"0x{parsed:02X}"


def _parse_hex_word(value, key, errors):
    try:
        parsed = int(str(value), 0)
    except (TypeError, ValueError):
        errors.append(f"{key} должен быть словом, например 0x21D1")
        return value
    if not (0 <= parsed <= 0xFFFF):
        errors.append(f"{key} должен быть в диапазоне 0..65535")
    return f"0x{parsed:04X}"


def _normalize_com_port(value) -> str:
    """Нормализация имени последовательного порта. На Linux tty-имена без пути
    автоматически дополняются префиксом /dev/ (ttyUSB0 -> /dev/ttyUSB0);
    Windows-имена COMx и уже полные пути остаются как есть."""
    name = str(value or "").strip()
    if not name:
        return ""
    if name.upper().startswith("COM") or name.startswith("/") or name.startswith("\\\\"):
        return name
    if name.lower().startswith("tty"):
        return "/dev/" + name
    return name


def _validate_poll_port(port: Dict[str, Any], index: int, errors) -> Dict[str, Any]:
    item = {**DEFAULT_POLL_PORT, **(port or {})}
    prefix = f"poll_ports[{index}]"
    item["id"] = str(item.get("id", "")).strip()
    item["name"] = str(item.get("name", "")).strip()
    if not item["id"]:
        errors.append(f"{prefix}.id обязателен")
    if not item["name"]:
        errors.append(f"{prefix}.name обязателен")

    transport = str(item.get("transport", "serial")).lower()
    if transport == "udp":
        transport = "udp_rtu"
    if transport not in ("serial", "udp_rtu", "udp_c2000pp"):
        errors.append(f"{prefix}.transport должен быть serial, udp_rtu или udp_c2000pp")
    item["transport"] = transport
    item["enabled"] = bool(item.get("enabled", True))

    for key, min_value, max_value in (
        ("device_slave_id", 0, 247),
        ("timeout_ms", 50, 10000),
        ("slow_poll_period_ms", 1000, 3600000),
    ):
        _as_int(item, key, errors, min_value, max_value)

    # 0 = использовать общий poll_period_ms; иначе 100..60000
    _as_int(item, "poll_period_ms", errors, 0, 60000)
    if item.get("poll_period_ms") and item["poll_period_ms"] < 100:
        errors.append(f"{prefix}.poll_period_ms должен быть 0 (общий) или >= 100")
    # -1 = использовать общий retry_count; иначе 0..10
    _as_int(item, "retry_count", errors, -1, 10)

    if transport == "serial":
        item["com_port"] = _normalize_com_port(item.get("com_port"))
        if not item["com_port"]:
            errors.append(f"{prefix}.com_port обязателен для serial")
        for key, min_value, max_value in (
            ("baudrate", 1, None),
            ("bytesize", 5, 8),
            ("stopbits", 1, 2),
        ):
            _as_int(item, key, errors, min_value, max_value)
        item["parity"] = str(item.get("parity", "N")).upper()
        if item["parity"] not in ("N", "E", "O", "M", "S"):
            errors.append(f"{prefix}.parity должен быть N, E, O, M или S")
    else:
        item["remote_host"] = str(item.get("remote_host") or item.get("udp_host") or "").strip()
        if not item["remote_host"]:
            errors.append(f"{prefix}.remote_host обязателен для UDP")
        _as_int(item, "remote_port", errors, 1, 65535)
        item["local_host"] = str(item.get("local_host", "")).strip()
        _as_int(item, "local_port", errors, 0, 65535)
        if transport == "udp_c2000pp":
            item["prefix"] = _parse_hex_byte(item.get("prefix", "0x10"), f"{prefix}.prefix", errors)
            item["channel"] = _parse_hex_byte(item.get("channel", "0x10"), f"{prefix}.channel", errors)
            item["seq_start"] = _parse_hex_word(item.get("seq_start", "0x21D1"), f"{prefix}.seq_start", errors)
            item["seq_persist"] = bool(item.get("seq_persist", True))

    return item


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

    if not isinstance(merged.get("poll_ports"), list) or not merged.get("poll_ports"):
        merged["poll_ports"] = [_legacy_poll_port(merged)]

    ports = []
    ids = set()
    names = set()
    for index, port in enumerate(merged["poll_ports"]):
        item = _validate_poll_port(port, index, errors)
        if item["id"] in ids:
            errors.append(f"poll_ports id должен быть уникальным: {item['id']}")
        if item["name"] in names:
            errors.append(f"poll_ports name должен быть уникальным: {item['name']}")
        ids.add(item["id"])
        names.add(item["name"])
        ports.append(item)
    merged["poll_ports"] = ports

    merged["parity"] = str(merged.get("parity", "N")).upper()
    if merged["parity"] not in ("N", "E", "O", "M", "S"):
        errors.append("parity должен быть N, E, O, M или S")

    if transport == "serial" and not str(merged.get("com_port", "")).strip():
        errors.append("com_port обязателен для serial")
    if transport == "udp" and not str(merged.get("udp_host", "")).strip():
        errors.append("udp_host обязателен для UDP")

    merged["com_port"] = str(merged.get("com_port", "")).strip()
    merged["udp_host"] = str(merged.get("udp_host", "")).strip()
    merged["mock_server_host"] = str(merged.get("mock_server_host", "0.0.0.0")).strip()
    merged["mock_server_url"] = str(merged.get("mock_server_url", "http://0.0.0.0:8000")).strip()
    merged["use_mock_server"] = bool(merged.get("use_mock_server", False))
    merged["auto_start"] = bool(merged.get("auto_start", True))
    merged = _sync_legacy_fields(merged)

    return (None, errors) if errors else (merged, [])
