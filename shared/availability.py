import os
import platform
import subprocess
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from shared.config_manager import atomic_save_json, load_runtime_json


DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
AVAILABILITY_DAILY_PATH = os.path.join(DATA_DIR, "availability_daily.json")
MAX_NETWORK_HISTORY = 1440
_LOCK = threading.RLock()


def _today() -> str:
    return datetime.now().date().isoformat()


def _default_payload(day: Optional[str] = None) -> Dict[str, Any]:
    return {
        "date": day or _today(),
        "updated_at": None,
        "sensors": {},
        "ports": {},
    }


def _day_payload(payload: Dict[str, Any], day: Optional[str] = None) -> Dict[str, Any]:
    current_day = day or _today()
    if not isinstance(payload, dict) or payload.get("date") != current_day:
        return _default_payload(current_day)
    payload.setdefault("sensors", {})
    payload.setdefault("ports", {})
    return payload


def load_daily_availability(day: Optional[str] = None) -> Dict[str, Any]:
    payload = load_runtime_json(AVAILABILITY_DAILY_PATH, default=_default_payload(day))
    return _day_payload(payload, day)


def is_ethernet_port(port: Dict[str, Any]) -> bool:
    return str(port.get("transport", "")).lower() in ("udp", "udp_rtu", "udp_c2000pp")


def ping_port_host(port: Dict[str, Any], timeout_ms: int = 1000, now_iso: Optional[str] = None) -> Dict[str, Any]:
    host = str(port.get("remote_host") or port.get("udp_host") or "").strip()
    timestamp = now_iso or datetime.now().isoformat()
    if not host:
        return {"timestamp": timestamp, "reachable": False, "ping_ms": None, "error": "remote_host is empty"}

    timeout_ms = max(100, min(10000, int(timeout_ms or 1000)))
    if platform.system().lower().startswith("win"):
        cmd = ["ping", "-n", "1", "-w", str(timeout_ms), host]
    else:
        timeout_s = max(1, int((timeout_ms + 999) / 1000))
        cmd = ["ping", "-c", "1", "-W", str(timeout_s), host]

    try:
        started = datetime.now()
        kwargs = {
            "capture_output": True,
            "text": True,
            "timeout": max(1.0, timeout_ms / 1000.0 + 1.0),
        }
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(cmd, **kwargs)
        elapsed_ms = round((datetime.now() - started).total_seconds() * 1000, 1)
        return {
            "timestamp": timestamp,
            "reachable": result.returncode == 0,
            "ping_ms": elapsed_ms if result.returncode == 0 else None,
            "error": None if result.returncode == 0 else (result.stderr or result.stdout or "").strip()[-300:],
        }
    except Exception as exc:
        return {"timestamp": timestamp, "reachable": False, "ping_ms": None, "error": str(exc)}


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _pct(part: int, total: int) -> Optional[float]:
    if total <= 0:
        return None
    return round(part * 100.0 / total, 1)


def _valid_sensor_sample(sensor: Dict[str, Any]) -> bool:
    temp = sensor.get("temperature") or {}
    hum = sensor.get("humidity") or {}
    if str(sensor.get("combined_status", "")).lower() == "no_connection":
        return False
    return temp.get("value") is not None or hum.get("value") is not None


def _sensor_sample_key(sensor: Dict[str, Any], fallback_ts: str) -> str:
    temp = sensor.get("temperature") or {}
    hum = sensor.get("humidity") or {}
    return str(temp.get("timestamp") or hum.get("timestamp") or fallback_ts)


def _seconds_since(timestamp: Optional[str]) -> Optional[float]:
    if not timestamp:
        return None
    try:
        return (datetime.now() - datetime.fromisoformat(str(timestamp))).total_seconds()
    except (TypeError, ValueError):
        return None


def update_daily_availability(
    port_config: Dict[str, Any],
    port_status: Dict[str, Any],
    sensors: List[Dict[str, Any]],
    network_check: Optional[Dict[str, Any]] = None,
    now_iso: Optional[str] = None,
) -> Dict[str, Any]:
    now_iso = now_iso or datetime.now().isoformat()
    day = now_iso[:10]
    with _LOCK:
        payload = load_daily_availability(day)
        payload["updated_at"] = now_iso

        port_id = str(port_config.get("id") or port_status.get("id") or "default")
        port_entry = payload["ports"].setdefault(port_id, {
            "id": port_id,
            "poll_cycles": 0,
            "poll_ok_cycles": 0,
            "poll_failed_cycles": 0,
            "network_checks": 0,
            "network_ok_checks": 0,
            "network_history": [],
            "_last_stats": {},
        })
        port_entry.update({
            "id": port_id,
            "name": port_config.get("name") or port_status.get("name") or port_id,
            "transport": port_config.get("transport") or port_status.get("transport"),
            "remote_host": port_config.get("remote_host") or port_config.get("udp_host"),
            "remote_port": port_config.get("remote_port") or port_config.get("udp_port"),
            "updated_at": now_iso,
            "state": port_status.get("state"),
        })

        last_stats = port_entry.setdefault("_last_stats", {})
        stat_pairs = (
            ("total_polls", "poll_cycles"),
            ("successful_polls", "poll_ok_cycles"),
            ("failed_polls", "poll_failed_cycles"),
        )
        for source_key, target_key in stat_pairs:
            current_value = _as_int(port_status.get(source_key))
            previous_value = _as_int(last_stats.get(source_key))
            delta = current_value - previous_value if current_value >= previous_value else current_value
            if delta > 0:
                port_entry[target_key] = _as_int(port_entry.get(target_key)) + delta
            last_stats[source_key] = current_value

        port_entry["poll_availability_percent"] = _pct(
            _as_int(port_entry.get("poll_ok_cycles")),
            _as_int(port_entry.get("poll_cycles")),
        )

        if network_check is not None:
            reachable = bool(network_check.get("reachable"))
            port_entry["network_checks"] = _as_int(port_entry.get("network_checks")) + 1
            if reachable:
                port_entry["network_ok_checks"] = _as_int(port_entry.get("network_ok_checks")) + 1
            port_entry["network_availability_percent"] = _pct(
                _as_int(port_entry.get("network_ok_checks")),
                _as_int(port_entry.get("network_checks")),
            )
            port_entry["last_network_check_at"] = network_check.get("timestamp") or now_iso
            port_entry["last_network_available"] = reachable
            port_entry["last_ping_ms"] = network_check.get("ping_ms")
            port_entry["last_network_error"] = network_check.get("error")
            history = port_entry.setdefault("network_history", [])
            history.append({
                "timestamp": network_check.get("timestamp") or now_iso,
                "available": reachable,
                "ping_ms": network_check.get("ping_ms"),
            })
            if len(history) > MAX_NETWORK_HISTORY:
                del history[:-MAX_NETWORK_HISTORY]

        for sensor in sensors or []:
            sensor_id = str(sensor.get("id"))
            sample_key = _sensor_sample_key(sensor, now_iso)
            sensor_entry = payload["sensors"].setdefault(sensor_id, {
                "id": sensor.get("id"),
                "samples_today": 0,
                "received_today": 0,
                "failed_today": 0,
            })
            sensor_entry.update({
                "id": sensor.get("id"),
                "name": sensor.get("name"),
                "poll_port_id": sensor.get("poll_port_id") or port_id,
                "poll_port_name": sensor.get("poll_port_name") or port_entry.get("name"),
                "last_seen_at": now_iso,
                "last_status": sensor.get("combined_status"),
            })
            if sample_key != sensor_entry.get("_last_sample_key"):
                sensor_entry["_last_sample_key"] = sample_key
                sensor_entry["samples_today"] = _as_int(sensor_entry.get("samples_today")) + 1
                if _valid_sensor_sample(sensor):
                    sensor_entry["received_today"] = _as_int(sensor_entry.get("received_today")) + 1
                    sensor_entry["last_success_at"] = (
                        (sensor.get("temperature") or {}).get("timestamp")
                        or (sensor.get("humidity") or {}).get("timestamp")
                        or now_iso
                    )
                else:
                    sensor_entry["failed_today"] = _as_int(sensor_entry.get("failed_today")) + 1
                sensor_entry["availability_percent"] = _pct(
                    _as_int(sensor_entry.get("received_today")),
                    _as_int(sensor_entry.get("samples_today")),
                )

        atomic_save_json(AVAILABILITY_DAILY_PATH, payload)
        return payload


def sync_daily_availability_from_current(
    current: Dict[str, Any],
    poller_config: Dict[str, Any],
    ping_ethernet: bool = False,
    ping_stale_after_s: float = 30.0,
) -> Dict[str, Any]:
    now_iso = str(current.get("timestamp") or datetime.now().isoformat())
    ports_by_id = {
        str(port.get("id") or "default"): dict(port)
        for port in (poller_config.get("poll_ports") or [])
    }
    statuses_by_id = {
        str(port.get("id") or "default"): dict(port)
        for port in (current.get("poll_ports") or [])
    }
    sensors_by_port: Dict[str, List[Dict[str, Any]]] = {}
    for sensor in current.get("sensors") or []:
        port_id = str(sensor.get("poll_port_id") or "default")
        sensors_by_port.setdefault(port_id, []).append(sensor)

    all_port_ids = set(ports_by_id) | set(statuses_by_id) | set(sensors_by_port)
    payload = load_daily_availability(now_iso[:10])
    for port_id in sorted(all_port_ids):
        port_config = ports_by_id.get(port_id) or statuses_by_id.get(port_id) or {"id": port_id}
        port_status = statuses_by_id.get(port_id) or port_config
        payload = update_daily_availability(
            port_config,
            port_status,
            sensors_by_port.get(port_id, []),
            now_iso=now_iso,
        )

    if ping_ethernet:
        payload = load_daily_availability(now_iso[:10])
        for port_id in sorted(set(ports_by_id) | set(statuses_by_id)):
            port_config = ports_by_id.get(port_id) or statuses_by_id.get(port_id) or {"id": port_id}
            if not is_ethernet_port(port_config):
                continue
            entry = (payload.get("ports") or {}).get(port_id, {})
            age = _seconds_since(entry.get("last_network_check_at"))
            if age is not None and age < ping_stale_after_s:
                continue
            timeout_ms = min(1500, max(300, int(port_config.get("timeout_ms") or poller_config.get("timeout_ms", 500))))
            network_check = ping_port_host(port_config, timeout_ms=timeout_ms)
            payload = update_daily_availability(
                port_config,
                statuses_by_id.get(port_id) or port_config,
                [],
                network_check=network_check,
            )

    return load_daily_availability(now_iso[:10])
