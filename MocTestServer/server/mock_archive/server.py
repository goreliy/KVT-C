"""
Mock Archive Server - эмуляция REST API Archive Manager
"""

import csv
import io
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

from .data_generator import HistoryGenerator
from .event_generator import EventGenerator, TemperatureLogGenerator, ViolationGenerator
from ..utils import merge_config

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DATA_DIR = os.path.join(BASE_DIR, 'data')

logger = logging.getLogger(__name__)


def _parse_iso_datetime(value: str) -> Optional[datetime]:
    """Безопасный парсинг ISO datetime строки"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00').replace('+00:00', ''))
    except (ValueError, AttributeError):
        return None


class ArchiveServer:
    """Mock Archive Server"""

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or self._default_config()
        self._history_gen: Optional[HistoryGenerator] = None
        self._event_gen: Optional[EventGenerator] = None
        self._temp_log_gen: Optional[TemperatureLogGenerator] = None
        self._violation_gen: Optional[ViolationGenerator] = None
        self._running = False
        self._save_thread: Optional[threading.Thread] = None
        self._archive_file = os.path.join(DATA_DIR, 'archive.json')
        self._events_file = os.path.join(DATA_DIR, 'events.json')
        self._init_components()

    @staticmethod
    def _default_config() -> Dict[str, Any]:
        return {
            "server": {"port": 6002, "enabled": True},
            "data": {"sensor_count": 10, "history_days": 30, "data_resolution_ms": 60000},
            "output": {"save_to_file": True, "save_interval_sec": 60, "save_aggregated": True},
            "generation": {"scenario": "normal", "compression_ratio": 0.3},
            "values": {
                "temperature": {"base": 22.0, "variation": 3.0, "daily_amplitude": 2.0},
                "humidity": {"base": 45.0, "variation": 5.0, "daily_amplitude": 10.0}
            },
            "events": {
                "include_events": True, "event_frequency": 0.01,
                "event_types": ["warning_high_temp", "warning_low_temp", "alarm_high_temp"]
            },
            "violations": {
                "violation_frequency": 0.005,
                "thresholds": {
                    "temperature": {"min": -10.0, "max": 40.0},
                    "humidity": {"min": 20.0, "max": 80.0}
                }
            },
            "gaps": {"enabled": False, "probability": 0.05, "max_duration_minutes": 30},
            "per_sensor_overrides": {}
        }

    def _init_components(self):
        data_cfg = self.config["data"]
        self._history_gen = HistoryGenerator({
            "sensor_count": data_cfg["sensor_count"],
            "history_days": data_cfg["history_days"],
            "data_resolution_ms": data_cfg["data_resolution_ms"],
            "scenario": self.config["generation"]["scenario"],
            "values": self.config["values"],
            "gaps": self.config["gaps"],
            "compression_ratio": self.config["generation"]["compression_ratio"]
        })
        self._event_gen = EventGenerator({
            "sensor_count": data_cfg["sensor_count"],
            "history_days": data_cfg["history_days"],
            **self.config["events"]
        })
        self._temp_log_gen = TemperatureLogGenerator({
            "sensor_count": data_cfg["sensor_count"],
            "history_days": data_cfg["history_days"],
            "values": self.config["values"]
        })
        violations_cfg = self.config.get("violations", {})
        self._violation_gen = ViolationGenerator({
            "sensor_count": data_cfg["sensor_count"],
            "history_days": data_cfg["history_days"],
            "violation_frequency": violations_cfg.get("violation_frequency", 0.005),
            "thresholds": violations_cfg.get("thresholds", {
                "temperature": {"min": -10.0, "max": 40.0},
                "humidity": {"min": 20.0, "max": 80.0}
            })
        })

    def _save_loop(self):
        interval = self.config.get("output", {}).get("save_interval_sec", 60)
        while self._running:
            time.sleep(interval)
            if self._running:
                self.save_to_file()

    def start(self):
        self._running = True
        if self.config.get("output", {}).get("save_to_file", True):
            self.save_to_file()
            self._save_thread = threading.Thread(target=self._save_loop, daemon=True)
            self._save_thread.start()

    def stop(self):
        self._running = False
        if self.config.get("output", {}).get("save_to_file", True):
            self.save_to_file()
        if self._save_thread:
            self._save_thread.join(timeout=2.0)
            self._save_thread = None

    def save_to_file(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        try:
            save_aggregated = self.config.get("output", {}).get("save_aggregated", True)
            resolution = "hour" if save_aggregated else "minute"
            sensor_count = self.config["data"]["sensor_count"]
            history_days = self.config["data"]["history_days"]
            from_time = datetime.now() - timedelta(days=history_days)
            to_time = datetime.now()

            sensors_data = {}
            for sid in range(1, sensor_count + 1):
                result = self._history_gen.query(sid, from_time, to_time, resolution)
                data_points = result.get("data", [])
                sensors_data[str(sid)] = {
                    "name": f"Датчик {sid}",
                    "data_count": len(data_points),
                    "data": data_points
                }

            with open(self._archive_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "timestamp": datetime.now().isoformat(),
                    "config": {"sensor_count": sensor_count, "history_days": history_days, "resolution": resolution},
                    "sensors": sensors_data
                }, f, ensure_ascii=False, indent=2)

            events_result = self._event_gen.get_events(limit=10000)
            with open(self._events_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "timestamp": datetime.now().isoformat(),
                    "total": events_result.get("total", 0),
                    "events": events_result.get("events", [])
                }, f, ensure_ascii=False, indent=2)

        except Exception as e:
            logger.error(f"Error saving archive: {e}")

    def get_status(self) -> Dict[str, Any]:
        archive_exists = os.path.exists(self._archive_file)
        events_exists = os.path.exists(self._events_file)

        return {
            "running": self._running,
            "port": self.config["server"]["port"],
            "data": self._history_gen.get_status(),
            "events": self._event_gen.get_status(),
            "temperature_log": self._temp_log_gen.get_status(),
            "violations": self._violation_gen.get_status(),
            "scenario": self.config["generation"]["scenario"],
            "files": {
                "archive_file": self._archive_file,
                "archive_exists": archive_exists,
                "archive_size_mb": round(os.path.getsize(self._archive_file) / 1024 / 1024, 2) if archive_exists else 0,
                "events_file": self._events_file,
                "events_exists": events_exists,
                "events_size_mb": round(os.path.getsize(self._events_file) / 1024 / 1024, 2) if events_exists else 0,
            }
        }

    def query(self, sensor_id: int, from_time: str, to_time: str,
              resolution: str = "minute") -> Dict[str, Any]:
        from_dt = _parse_iso_datetime(from_time) or (datetime.now() - timedelta(days=1))
        to_dt = _parse_iso_datetime(to_time) or datetime.now()
        return self._history_gen.query(sensor_id, from_dt, to_dt, resolution)

    def get_events(self, from_time: str = None, to_time: str = None,
                   sensor_id: int = None, event_type: str = None,
                   priority: str = None, acknowledged: bool = None,
                   limit: int = 100, offset: int = 0) -> Dict[str, Any]:
        return self._event_gen.get_events(
            from_time=_parse_iso_datetime(from_time),
            to_time=_parse_iso_datetime(to_time),
            sensor_id=sensor_id, event_type=event_type,
            priority=priority, acknowledged=acknowledged,
            limit=limit, offset=offset
        )

    def acknowledge_event(self, event_id: int, user: str = "operator") -> Optional[Dict]:
        return self._event_gen.acknowledge_event(event_id, user)

    def cleanup(self, days_to_keep: int = 7) -> Dict[str, Any]:
        return {"status": "ok", "message": f"Simulated cleanup: keeping last {days_to_keep} days", "deleted_records": 0}

    def export_data(self, sensor_id: int, from_time: str, to_time: str, format: str = "json") -> Any:
        data = self.query(sensor_id, from_time, to_time, "minute")
        if format == "csv":
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["timestamp", "temperature", "humidity", "status"])
            for point in data.get("data", []):
                writer.writerow([point.get("timestamp"), point.get("temperature"),
                                 point.get("humidity"), point.get("status")])
            return output.getvalue()
        return data

    def regenerate(self):
        self._history_gen.regenerate()
        self._event_gen.regenerate()
        self._temp_log_gen.regenerate()
        self._violation_gen.regenerate()
        if self.config.get("output", {}).get("save_to_file", True):
            self.save_to_file()

    def add_event(self, sensor_id: int, event_type: str, value: float = None) -> Dict:
        return self._event_gen.add_event(sensor_id, event_type, value)

    def get_temperature_log(self, sensor_id: int = None, period_type: str = "hour",
                            from_time: str = None, to_time: str = None) -> Dict[str, Any]:
        from_dt = _parse_iso_datetime(from_time) if from_time else None
        to_dt = _parse_iso_datetime(to_time) if to_time else None
        return self._temp_log_gen.query(sensor_id, period_type, from_dt, to_dt)

    def get_violations(self, sensor_id: int = None, from_time: str = None,
                       to_time: str = None, status: str = "all",
                       parameter: str = None, acknowledged: bool = None,
                       limit: int = 100, offset: int = 0) -> Dict[str, Any]:
        from_dt = _parse_iso_datetime(from_time) if from_time else None
        to_dt = _parse_iso_datetime(to_time) if to_time else None
        return self._violation_gen.get_violations(
            sensor_id=sensor_id, from_time=from_dt, to_time=to_dt,
            status=status, parameter=parameter, acknowledged=acknowledged,
            limit=limit, offset=offset
        )

    def acknowledge_violation(self, violation_id: int, user: str = "operator",
                              comment: str = None) -> Optional[Dict]:
        return self._violation_gen.acknowledge_violation(violation_id, user, comment)

    def set_sensor_history(self, sensor_id: int, data: list):
        self._history_gen._data_cache.setdefault(sensor_id, []).extend(data)

    def update_config(self, new_config: Dict[str, Any]):
        merge_config(self.config, new_config)
        self._init_components()
