"""Archive Manager data service.

The service stores snapshots from data/current.json, keeps a daily file view for
warehouse reports, and exposes query helpers used by both the standalone
Archive Manager Flask app and the main visualizer API.
"""
import csv
import os
import shutil
import sqlite3
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path

from shared.config_manager import (
    atomic_save_json,
    get_sensor_by_id,
    load_archive_config,
    load_runtime_json,
    load_system_config,
    save_archive_config,
)


from shared.paths import app_root as _app_root

ROOT_DIR = Path(_app_root())
DATA_DIR = ROOT_DIR / "data"
CONFIG_DIR = DATA_DIR / "config"


def _now_iso():
    return datetime.now().isoformat(timespec="seconds")


def _parse_dt(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _date_text(dt):
    if isinstance(dt, datetime):
        return dt.date().isoformat()
    parsed = _parse_dt(dt)
    return parsed.date().isoformat() if parsed else None


def _as_float(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _round_or_none(value, digits=2):
    value = _as_float(value)
    return round(value, digits) if value is not None else None


def _rel_path(root_dir, path_value, default_name):
    raw = str(path_value or "").strip() or f"./data/{default_name}"
    path = Path(raw)
    if not path.is_absolute():
        path = Path(root_dir) / raw
    return path


def _default_archive():
    return {
        "version": "1.0",
        "created_at": _now_iso(),
        "last_updated": None,
        "compression_enabled": True,
        "sensors": {},
    }


def _event_is_violation(event):
    value = str(event.get("event_type") or event.get("type") or "").lower()
    return "warning" in value or "alarm" in value or "violation" in value


def _violation_parameter(event_type):
    event_type = str(event_type or "").lower()
    if "hum" in event_type or "humidity" in event_type:
        return "humidity"
    return "temperature"


def _violation_direction(event_type):
    event_type = str(event_type or "").lower()
    return "low" if "low" in event_type else "high"


def _status_level(event_type):
    event_type = str(event_type or "").lower()
    return "alarm" if "alarm" in event_type else "warning"


class ArchiveService:
    """File-first Archive Manager with optional SQLite mirror."""

    def __init__(self, root_dir=None):
        self.root_dir = Path(root_dir or ROOT_DIR)
        self.data_dir = self.root_dir / "data"
        self._lock = threading.RLock()
        self._thread = None
        self._stop_event = threading.Event()
        self._last_capture_at = None
        self._last_capture_error = None

    def config(self):
        return load_archive_config()

    def save_config(self, patch):
        current = self.config()
        next_config = self._merge_dict(current, patch or {})
        self._validate_config(next_config)
        save_archive_config(next_config)
        return next_config

    def archive_path(self, config=None):
        config = config or self.config()
        json_cfg = (config.get("storage") or {}).get("json_file") or {}
        return _rel_path(self.root_dir, json_cfg.get("path"), "archive.json")

    def sqlite_path(self, config=None):
        config = config or self.config()
        sqlite_cfg = (config.get("storage") or {}).get("sqlite") or {}
        return _rel_path(self.root_dir, sqlite_cfg.get("path"), "archive.db")

    def daily_path(self):
        return self.data_dir / "archive_daily.json"

    def events_path(self):
        return self.data_dir / "events.json"

    def current_path(self, config=None):
        config = config or self.config()
        data_collection = config.get("data_collection") or {}
        return _rel_path(self.root_dir, data_collection.get("source_file"), "current.json")

    def status(self):
        config = self.config()
        archive_path = self.archive_path(config)
        sqlite_path = self.sqlite_path(config)
        daily_path = self.daily_path()
        archive = self.load_archive()
        measurement_count = sum(
            len((sensor_data or {}).get("measurements") or []) + len((sensor_data or {}).get("data") or [])
            for sensor_data in (archive.get("sensors") or {}).values()
        )
        daily = self.load_daily_view()
        disk = shutil.disk_usage(str(self.data_dir))
        return {
            "status": "running" if self.running else "stopped",
            "source_file": str(self.current_path(config)),
            "archive_file": str(archive_path),
            "sqlite_file": str(sqlite_path),
            "daily_file": str(daily_path),
            "json_size_bytes": archive_path.stat().st_size if archive_path.exists() else 0,
            "sqlite_size_bytes": sqlite_path.stat().st_size if sqlite_path.exists() else 0,
            "sensor_count": len(archive.get("sensors") or {}),
            "measurement_count": measurement_count,
            "daily_days": len(daily.get("days") or {}),
            "last_updated": archive.get("last_updated") or archive.get("timestamp"),
            "last_capture_at": self._last_capture_at,
            "last_capture_error": self._last_capture_error,
            "disk_free_mb": round(disk.free / 1024 / 1024, 1),
            "disk_total_mb": round(disk.total / 1024 / 1024, 1),
            "config": config,
        }

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.running:
            return self.status()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="ArchiveManager", daemon=True)
        self._thread.start()
        return self.status()

    def stop(self):
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        return self.status()

    def _run_loop(self):
        while not self._stop_event.is_set():
            config = self.config()
            interval = self._next_interval_seconds(config)
            try:
                self.capture_current()
            except Exception as exc:
                self._last_capture_error = str(exc)
            self._stop_event.wait(interval)

    def _next_interval_seconds(self, config):
        collection = config.get("data_collection") or {}
        mode = collection.get("mode") or "combined"
        if mode == "periodic":
            ms = ((collection.get("periodic") or {}).get("interval_ms") or 1000)
        elif mode == "watch":
            ms = ((collection.get("watch") or {}).get("debounce_ms") or 1000)
        else:
            ms = ((collection.get("combined") or {}).get("max_interval_ms") or 5000)
        try:
            return max(0.1, min(60.0, int(ms) / 1000.0))
        except (TypeError, ValueError):
            return 5.0

    def load_archive(self):
        path = self.archive_path()
        archive = load_runtime_json(str(path), default=_default_archive())
        if not isinstance(archive, dict):
            archive = _default_archive()
        archive.setdefault("version", "1.0")
        archive.setdefault("created_at", _now_iso())
        archive.setdefault("sensors", {})
        return archive

    def save_archive(self, archive):
        atomic_save_json(str(self.archive_path()), archive)

    def capture_current(self):
        """Read current.json and append one compressed archive sample per sensor."""
        with self._lock:
            config = self.config()
            current = load_runtime_json(str(self.current_path(config)), default={})
            if not current:
                self._last_capture_error = "current.json is empty or unavailable"
                return {"captured": 0, "error": self._last_capture_error}

            archive = self.load_archive()
            archive["compression_enabled"] = bool((config.get("compression") or {}).get("enabled", True))
            capture_ts = _parse_dt(current.get("timestamp")) or datetime.now()
            sensors = current.get("sensors") or []
            by_config = {int(s.get("id")): s for s in load_system_config().get("sensors", []) if s.get("id") is not None}

            captured = 0
            events_created = []
            for sensor in sensors:
                sid = self._sensor_id(sensor)
                if sid is None:
                    continue
                temp = _as_float((sensor.get("temperature") or {}).get("value"))
                hum = _as_float((sensor.get("humidity") or {}).get("value"))
                status = sensor.get("combined_status") or (sensor.get("temperature") or {}).get("status") or "unknown"
                if temp is None and hum is None:
                    continue

                sample_ts = (
                    _parse_dt((sensor.get("temperature") or {}).get("timestamp"))
                    or _parse_dt((sensor.get("humidity") or {}).get("timestamp"))
                    or capture_ts
                )
                sdata = archive.setdefault("sensors", {}).setdefault(str(sid), {
                    "name": sensor.get("name") or f"Датчик {sid}",
                    "measurements": [],
                    "events": [],
                })
                sdata["name"] = sensor.get("name") or sdata.get("name") or f"Датчик {sid}"
                sdata.setdefault("measurements", [])
                appended = self._append_measurement(sdata, sample_ts, temp, hum, status, config)
                captured += 1 if appended else 0

                config_sensor = by_config.get(sid) or {}
                event = self._event_from_limits(sensor, config_sensor, sample_ts)
                if event:
                    events_created.append(event)

            archive["last_updated"] = _now_iso()
            self._apply_retention(archive, config)
            self.save_archive(archive)
            self._mirror_sqlite(archive, config)
            self._append_events(events_created)
            daily = self.generate_daily_view(archive)
            self._last_capture_at = archive["last_updated"]
            self._last_capture_error = None
            return {
                "captured": captured,
                "events_created": len(events_created),
                "last_updated": archive["last_updated"],
                "daily_days": len(daily.get("days") or {}),
            }

    def _sensor_id(self, sensor):
        try:
            return int(sensor.get("id"))
        except (TypeError, ValueError):
            return None

    def _append_measurement(self, sdata, sample_ts, temp, hum, status, config):
        measurements = sdata.setdefault("measurements", [])
        compression = config.get("compression") or {}
        if compression.get("enabled", True) and measurements:
            last = measurements[-1]
            same_status = str(last.get("s")) == str(status)
            same_temp = self._same_value(last.get("t"), temp, compression.get("tolerance_temp", 0.1))
            same_hum = self._same_value(last.get("h"), hum, compression.get("tolerance_hum", 0.5))
            if same_status and same_temp and same_hum:
                start = _parse_dt(last.get("ts")) or sample_ts
                last["te"] = sample_ts.isoformat()
                last["d"] = max(0, int((sample_ts - start).total_seconds()))
                last["n"] = int(last.get("n") or 1) + 1
                return False

        measurements.append({
            "ts": sample_ts.isoformat(),
            "te": sample_ts.isoformat(),
            "d": 0,
            "n": 1,
            "t": _round_or_none(temp, 3),
            "h": _round_or_none(hum, 3),
            "s": status,
        })
        return True

    def _same_value(self, a, b, tolerance):
        if a is None and b is None:
            return True
        if a is None or b is None:
            return False
        try:
            return abs(float(a) - float(b)) <= float(tolerance)
        except (TypeError, ValueError):
            return False

    def _event_from_limits(self, sensor, config_sensor, sample_ts):
        if not config_sensor:
            return None
        checks = [
            ("temperature", (sensor.get("temperature") or {}).get("value"), config_sensor.get("temp_limits") or {}),
            ("humidity", (sensor.get("humidity") or {}).get("value"), config_sensor.get("hum_limits") or {}),
        ]
        for parameter, value, limits in checks:
            value = _as_float(value)
            if value is None:
                continue
            min_value = _as_float(limits.get("min"))
            max_value = _as_float(limits.get("max"))
            warning_delta = _as_float(limits.get("warning_delta")) or 0
            alarm_delta = _as_float(limits.get("alarm_delta")) or 0
            event_type = None
            threshold = None
            if max_value is not None and value > max_value + alarm_delta:
                event_type = f"alarm_high_{'hum' if parameter == 'humidity' else 'temp'}"
                threshold = max_value + alarm_delta
            elif min_value is not None and value < min_value - alarm_delta:
                event_type = f"alarm_low_{'hum' if parameter == 'humidity' else 'temp'}"
                threshold = min_value - alarm_delta
            elif max_value is not None and value > max_value + warning_delta:
                event_type = f"warning_high_{'hum' if parameter == 'humidity' else 'temp'}"
                threshold = max_value + warning_delta
            elif min_value is not None and value < min_value - warning_delta:
                event_type = f"warning_low_{'hum' if parameter == 'humidity' else 'temp'}"
                threshold = min_value - warning_delta
            if event_type:
                sid = int(config_sensor.get("id"))
                return {
                    "timestamp": sample_ts.isoformat(),
                    "sensor_id": sid,
                    "sensor_name": config_sensor.get("name") or sensor.get("name") or f"Датчик {sid}",
                    "event_type": event_type,
                    "priority": "high" if event_type.startswith("alarm") else "medium",
                    "value": round(value, 2),
                    "threshold": round(threshold, 2) if threshold is not None else None,
                    "message": self._event_message(config_sensor, parameter, event_type, value),
                    "acknowledged": False,
                }
        return None

    def _event_message(self, sensor, parameter, event_type, value):
        name = sensor.get("name") or f"Датчик {sensor.get('id')}"
        label = "влажность" if parameter == "humidity" else "температура"
        direction = "ниже границы" if "low" in event_type else "выше границы"
        unit = "%" if parameter == "humidity" else "°C"
        return f"{name}: {label} {direction} ({value:.2f}{unit})"

    def _append_events(self, events_created):
        if not events_created:
            return
        path = self.events_path()
        payload = load_runtime_json(str(path), default={"events": []})
        events = payload.setdefault("events", [])
        next_id = max([int(e.get("id") or 0) for e in events] or [0]) + 1
        for event in events_created:
            event["id"] = next_id
            next_id += 1
            events.insert(0, event)
        payload["timestamp"] = _now_iso()
        payload["total"] = len(events)
        atomic_save_json(str(path), payload)

    def _apply_retention(self, archive, config):
        retention = config.get("retention") or {}
        try:
            max_days = int(retention.get("max_days") or 365)
        except (TypeError, ValueError):
            max_days = 365
        if max_days <= 0:
            return
        cutoff = datetime.now() - timedelta(days=max_days)
        for sdata in (archive.get("sensors") or {}).values():
            if not isinstance(sdata, dict):
                continue
            sdata["measurements"] = [
                m for m in (sdata.get("measurements") or [])
                if (_parse_dt(m.get("te") or m.get("ts")) or datetime.max) >= cutoff
            ]
            if "data" in sdata:
                sdata["data"] = [
                    p for p in (sdata.get("data") or [])
                    if (_parse_dt(p.get("timestamp")) or datetime.max) >= cutoff
                ]
                sdata["data_count"] = len(sdata["data"])

    def _mirror_sqlite(self, archive, config):
        sqlite_cfg = (config.get("storage") or {}).get("sqlite") or {}
        if not sqlite_cfg.get("enabled", False):
            return
        path = self.sqlite_path(config)
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(path)) as conn:
            self._ensure_sqlite(conn)
            conn.execute("DELETE FROM sensors")
            conn.execute("DELETE FROM measurements")
            for sid_text, sdata in (archive.get("sensors") or {}).items():
                try:
                    sid = int(sid_text)
                except (TypeError, ValueError):
                    continue
                conn.execute("INSERT OR REPLACE INTO sensors(id, name) VALUES (?, ?)", (sid, sdata.get("name") or f"Датчик {sid}"))
                for row in self.iter_measurements(archive, sensor_id=sid):
                    conn.execute(
                        """
                        INSERT INTO measurements(
                            sensor_id, timestamp_start, timestamp_end, duration_seconds,
                            sample_count, temperature, humidity, temp_status, hum_status, combined_status
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            sid,
                            row["timestamp_start"].isoformat(),
                            row["timestamp_end"].isoformat(),
                            row.get("duration_seconds") or 0,
                            row.get("sample_count") or 1,
                            row.get("t_avg"),
                            row.get("h_avg"),
                            row.get("status"),
                            row.get("status"),
                            row.get("status"),
                        ),
                    )
            total = sum(
                len((sdata or {}).get("measurements") or []) + len((sdata or {}).get("data") or [])
                for sdata in (archive.get("sensors") or {}).values()
            )
            size = path.stat().st_size if path.exists() else 0
            conn.execute(
                "INSERT INTO archive_stats(total_records, disk_usage_bytes, compression_ratio) VALUES (?, ?, ?)",
                (total, size, 1.0),
            )
            conn.commit()

    def _ensure_sqlite(self, conn):
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sensors (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                modbus_slave_id INTEGER,
                modbus_addr_temp INTEGER,
                modbus_addr_hum INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS measurements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sensor_id INTEGER REFERENCES sensors(id),
                timestamp_start TIMESTAMP NOT NULL,
                timestamp_end TIMESTAMP NOT NULL,
                duration_seconds INTEGER,
                sample_count INTEGER DEFAULT 1,
                temperature REAL,
                humidity REAL,
                temp_status TEXT,
                hum_status TEXT,
                combined_status TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_sensor_time ON measurements(sensor_id, timestamp_start);
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sensor_id INTEGER REFERENCES sensors(id),
                timestamp TIMESTAMP NOT NULL,
                event_type TEXT NOT NULL,
                value REAL,
                threshold REAL,
                acknowledged BOOLEAN DEFAULT FALSE,
                acknowledged_at TIMESTAMP,
                acknowledged_by TEXT,
                comment TEXT
            );
            CREATE TABLE IF NOT EXISTS temperature_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sensor_id INTEGER REFERENCES sensors(id),
                period_type TEXT NOT NULL,
                period_start TIMESTAMP NOT NULL,
                period_end TIMESTAMP NOT NULL,
                temp_min REAL,
                temp_max REAL,
                temp_avg REAL,
                hum_min REAL,
                hum_max REAL,
                hum_avg REAL,
                sample_count INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_temp_log_sensor_period ON temperature_log(sensor_id, period_type, period_start);
            CREATE TABLE IF NOT EXISTS threshold_violations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sensor_id INTEGER REFERENCES sensors(id),
                started_at TIMESTAMP NOT NULL,
                ended_at TIMESTAMP,
                duration_seconds INTEGER,
                parameter TEXT NOT NULL,
                violation_type TEXT NOT NULL,
                value_at_start REAL,
                value_peak REAL,
                threshold REAL,
                acknowledged BOOLEAN DEFAULT FALSE,
                acknowledged_at TIMESTAMP,
                acknowledged_by TEXT,
                comment TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_violations_sensor ON threshold_violations(sensor_id, started_at);
            CREATE TABLE IF NOT EXISTS archive_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                total_records INTEGER,
                disk_usage_bytes INTEGER,
                compression_ratio REAL
            );
            """
        )

    def iter_measurements(self, archive=None, sensor_id=None, date_from=None, date_to=None):
        archive = archive or self.load_archive()
        dt_from = _parse_dt(date_from)
        dt_to = _parse_dt(date_to)
        for sid_text, sdata in (archive.get("sensors") or {}).items():
            try:
                sid = int(sid_text)
            except (TypeError, ValueError):
                continue
            if sensor_id is not None and int(sensor_id) != sid:
                continue
            name = sdata.get("name") or f"Датчик {sid}"
            for item in sdata.get("measurements") or []:
                ts = _parse_dt(item.get("ts"))
                te = _parse_dt(item.get("te") or item.get("ts")) or ts
                if ts is None or not self._in_range(ts, dt_from, dt_to):
                    continue
                yield {
                    "sensor_id": sid,
                    "sensor_name": name,
                    "timestamp_start": ts,
                    "timestamp_end": te,
                    "duration_seconds": int(item.get("d") or max(0, (te - ts).total_seconds())),
                    "sample_count": int(item.get("n") or 1),
                    "t_min": _as_float(item.get("t")),
                    "t_max": _as_float(item.get("t")),
                    "t_avg": _as_float(item.get("t")),
                    "h_min": _as_float(item.get("h")),
                    "h_max": _as_float(item.get("h")),
                    "h_avg": _as_float(item.get("h")),
                    "status": item.get("s"),
                    "source": "measurements",
                }
            for item in sdata.get("data") or []:
                ts = _parse_dt(item.get("timestamp"))
                if ts is None or not self._in_range(ts, dt_from, dt_to):
                    continue
                temp = item.get("temperature") or {}
                hum = item.get("humidity") or {}
                sample_count = int(item.get("sample_count") or 1)
                yield {
                    "sensor_id": sid,
                    "sensor_name": name,
                    "timestamp_start": ts,
                    "timestamp_end": ts,
                    "duration_seconds": 0,
                    "sample_count": sample_count,
                    "t_min": _as_float(temp.get("min", temp.get("avg"))),
                    "t_max": _as_float(temp.get("max", temp.get("avg"))),
                    "t_avg": _as_float(temp.get("avg", temp.get("value"))),
                    "h_min": _as_float(hum.get("min", hum.get("avg"))),
                    "h_max": _as_float(hum.get("max", hum.get("avg"))),
                    "h_avg": _as_float(hum.get("avg", hum.get("value"))),
                    "status": item.get("status"),
                    "source": "data",
                }

    def _in_range(self, ts, dt_from, dt_to):
        if dt_from is not None and ts < dt_from:
            return False
        if dt_to is not None and ts > dt_to:
            return False
        return True

    def generate_daily_view(self, archive=None):
        archive = archive or self.load_archive()
        buckets = {}
        for row in self.iter_measurements(archive):
            day = row["timestamp_start"].date().isoformat()
            key = (day, row["sensor_id"])
            bucket = buckets.setdefault(key, {
                "date": day,
                "sensor_id": row["sensor_id"],
                "sensor_name": row["sensor_name"],
                "t_values": [],
                "h_values": [],
                "t_min_values": [],
                "t_max_values": [],
                "h_min_values": [],
                "h_max_values": [],
                "sample_count": 0,
            })
            count = max(1, int(row.get("sample_count") or 1))
            if row.get("t_avg") is not None:
                bucket["t_values"].append((row["t_avg"], count))
            if row.get("h_avg") is not None:
                bucket["h_values"].append((row["h_avg"], count))
            if row.get("t_min") is not None:
                bucket["t_min_values"].append(row["t_min"])
            if row.get("t_max") is not None:
                bucket["t_max_values"].append(row["t_max"])
            if row.get("h_min") is not None:
                bucket["h_min_values"].append(row["h_min"])
            if row.get("h_max") is not None:
                bucket["h_max_values"].append(row["h_max"])
            bucket["sample_count"] += count

        violations_by_key = self._violation_counts()
        days = {}
        by_sensor = {}
        for (day, sid), bucket in sorted(buckets.items()):
            daily_row = {
                "date": day,
                "sensor_id": sid,
                "sensor_name": bucket["sensor_name"],
                "t_min": self._min_or_none(bucket["t_min_values"]),
                "t_max": self._max_or_none(bucket["t_max_values"]),
                "t_avg": self._weighted_avg(bucket["t_values"]),
                "h_min": self._min_or_none(bucket["h_min_values"]),
                "h_max": self._max_or_none(bucket["h_max_values"]),
                "h_avg": self._weighted_avg(bucket["h_values"]),
                "sample_count": bucket["sample_count"],
                "violations": violations_by_key.get((day, sid), 0),
            }
            days.setdefault(day, {"date": day, "sensors": {}})["sensors"][str(sid)] = daily_row
            by_sensor.setdefault(str(sid), {"sensor_id": sid, "sensor_name": bucket["sensor_name"], "days": {}})["days"][day] = daily_row

        payload = {
            "version": "1.0",
            "updated_at": _now_iso(),
            "source": "archive.json",
            "days": days,
            "sensors": by_sensor,
        }
        atomic_save_json(str(self.daily_path()), payload)
        return payload

    def load_daily_view(self):
        payload = load_runtime_json(str(self.daily_path()), default={})
        if payload.get("days"):
            return payload
        return self.generate_daily_view()

    def daily_row_map(self):
        daily = self.load_daily_view()
        result = {}
        for day, day_data in (daily.get("days") or {}).items():
            result[day] = day_data.get("sensors") or {}
        return result

    def _violation_counts(self):
        payload = load_runtime_json(str(self.events_path()), default={"events": []})
        result = defaultdict(int)
        for event in payload.get("events") or []:
            if not _event_is_violation(event):
                continue
            day = _date_text(event.get("timestamp"))
            try:
                sid = int(event.get("sensor_id"))
            except (TypeError, ValueError):
                continue
            if day:
                result[(day, sid)] += 1
        return result

    def _weighted_avg(self, values):
        clean = [(float(value), max(1, int(count or 1))) for value, count in values if value is not None]
        total = sum(count for _value, count in clean)
        if not clean or total <= 0:
            return None
        return round(sum(value * count for value, count in clean) / total, 2)

    def _min_or_none(self, values):
        values = [v for v in values if v is not None]
        return round(min(values), 2) if values else None

    def _max_or_none(self, values):
        values = [v for v in values if v is not None]
        return round(max(values), 2) if values else None

    def query(self, sensor_id=None, date_from=None, date_to=None, resolution="raw"):
        resolution = resolution or "raw"
        if resolution == "auto":
            resolution = self._auto_resolution(date_from, date_to)
        rows = list(self.iter_measurements(sensor_id=sensor_id, date_from=date_from, date_to=date_to))
        if resolution == "raw":
            data = [self._row_to_payload(row) for row in rows]
        else:
            data = self._aggregate_rows(rows, resolution)
        return {
            "sensor_id": int(sensor_id) if sensor_id else None,
            "from": date_from,
            "to": date_to,
            "resolution": resolution,
            "total": len(data),
            "data": data,
        }

    def _auto_resolution(self, date_from, date_to):
        start = _parse_dt(date_from)
        end = _parse_dt(date_to) or datetime.now()
        if not start:
            return "hour"
        days = (end - start).total_seconds() / 86400
        if days <= 1:
            return "raw"
        if days <= 7:
            return "hour"
        return "day"

    def temperature_log(self, sensor_id=None, period_type="day", date_from=None, date_to=None):
        rows = list(self.iter_measurements(sensor_id=sensor_id, date_from=date_from, date_to=date_to))
        data = self._aggregate_rows(rows, period_type)
        sensor_name = None
        if sensor_id:
            sensor = get_sensor_by_id(int(sensor_id))
            sensor_name = (sensor or {}).get("name")
        return {
            "sensor_id": int(sensor_id) if sensor_id else None,
            "sensor_name": sensor_name,
            "period_type": period_type,
            "total": len(data),
            "data": data,
        }

    def _aggregate_rows(self, rows, resolution):
        buckets = {}
        for row in rows:
            bucket_start, bucket_end, label = self._bucket(row["timestamp_start"], resolution)
            key = (row["sensor_id"], label)
            bucket = buckets.setdefault(key, {
                "sensor_id": row["sensor_id"],
                "sensor_name": row["sensor_name"],
                "period": label,
                "period_start": bucket_start,
                "period_end": bucket_end,
                "t_values": [],
                "h_values": [],
                "t_min_values": [],
                "t_max_values": [],
                "h_min_values": [],
                "h_max_values": [],
                "sample_count": 0,
            })
            count = max(1, int(row.get("sample_count") or 1))
            if row.get("t_avg") is not None:
                bucket["t_values"].append((row["t_avg"], count))
            if row.get("h_avg") is not None:
                bucket["h_values"].append((row["h_avg"], count))
            if row.get("t_min") is not None:
                bucket["t_min_values"].append(row["t_min"])
            if row.get("t_max") is not None:
                bucket["t_max_values"].append(row["t_max"])
            if row.get("h_min") is not None:
                bucket["h_min_values"].append(row["h_min"])
            if row.get("h_max") is not None:
                bucket["h_max_values"].append(row["h_max"])
            bucket["sample_count"] += count

        result = []
        for bucket in sorted(buckets.values(), key=lambda item: (item["period_start"], item["sensor_id"])):
            result.append({
                "sensor_id": bucket["sensor_id"],
                "sensor_name": bucket["sensor_name"],
                "period": bucket["period"],
                "period_start": bucket["period_start"].isoformat(),
                "period_end": bucket["period_end"].isoformat(),
                "temp_min": self._min_or_none(bucket["t_min_values"]),
                "temp_max": self._max_or_none(bucket["t_max_values"]),
                "temp_avg": self._weighted_avg(bucket["t_values"]),
                "hum_min": self._min_or_none(bucket["h_min_values"]),
                "hum_max": self._max_or_none(bucket["h_max_values"]),
                "hum_avg": self._weighted_avg(bucket["h_values"]),
                "sample_count": bucket["sample_count"],
            })
        return result

    def _bucket(self, ts, resolution):
        if resolution == "minute":
            start = ts.replace(second=0, microsecond=0)
            end = start + timedelta(minutes=1) - timedelta(seconds=1)
            label = start.strftime("%Y-%m-%d %H:%M")
        elif resolution == "hour":
            start = ts.replace(minute=0, second=0, microsecond=0)
            end = start + timedelta(hours=1) - timedelta(seconds=1)
            label = start.strftime("%Y-%m-%d %H:00")
        elif resolution == "week":
            start = (ts - timedelta(days=ts.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=7) - timedelta(seconds=1)
            iso = ts.isocalendar()
            label = f"{iso[0]}-W{iso[1]:02d}"
        else:
            start = ts.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1) - timedelta(seconds=1)
            label = start.date().isoformat()
        return start, end, label

    def _row_to_payload(self, row):
        return {
            "sensor_id": row["sensor_id"],
            "sensor_name": row["sensor_name"],
            "timestamp_start": row["timestamp_start"].isoformat(),
            "timestamp_end": row["timestamp_end"].isoformat(),
            "duration_seconds": row.get("duration_seconds") or 0,
            "sample_count": row.get("sample_count") or 1,
            "temperature": {
                "min": row.get("t_min"),
                "max": row.get("t_max"),
                "avg": row.get("t_avg"),
            },
            "humidity": {
                "min": row.get("h_min"),
                "max": row.get("h_max"),
                "avg": row.get("h_avg"),
            },
            "status": row.get("status"),
        }

    def events(self, sensor_id=None, event_type=None, date_from=None, date_to=None, limit=200):
        payload = load_runtime_json(str(self.events_path()), default={"events": []})
        events = list(payload.get("events") or [])
        events = self._filter_events(events, sensor_id, event_type, date_from, date_to)
        events.sort(key=lambda item: item.get("timestamp") or "", reverse=True)
        return {"total": len(events), "events": events[:max(1, int(limit or 200))]}

    def violations(self, sensor_id=None, date_from=None, date_to=None, status="all", limit=200):
        events = self.events(sensor_id=sensor_id, date_from=date_from, date_to=date_to, limit=100000).get("events") or []
        violations = []
        for event in events:
            if not _event_is_violation(event):
                continue
            acknowledged = bool(event.get("acknowledged"))
            if status == "unacknowledged" and acknowledged:
                continue
            if status == "open" and event.get("ended_at"):
                continue
            if status == "closed" and not event.get("ended_at"):
                continue
            event_type = event.get("event_type")
            level = _status_level(event_type)
            direction = _violation_direction(event_type)
            started_at = event.get("started_at") or event.get("timestamp")
            ended_at = event.get("ended_at")
            started = _parse_dt(started_at)
            ended = _parse_dt(ended_at)
            duration = int((ended - started).total_seconds()) if started and ended else event.get("duration_seconds")
            violations.append({
                "id": event.get("id"),
                "sensor_id": event.get("sensor_id"),
                "sensor_name": event.get("sensor_name") or self._sensor_name(event.get("sensor_id")),
                "started_at": started_at,
                "ended_at": ended_at,
                "duration_seconds": duration,
                "parameter": _violation_parameter(event_type),
                "violation_type": f"{level}_{direction}",
                "value_at_start": event.get("value"),
                "value_peak": event.get("value_peak", event.get("value")),
                "threshold": event.get("threshold"),
                "acknowledged": acknowledged,
                "acknowledged_at": event.get("acknowledged_at"),
                "acknowledged_by": event.get("acknowledged_by"),
                "message": event.get("message"),
            })
        return {"total": len(violations), "violations": violations[:max(1, int(limit or 200))]}

    def _filter_events(self, events, sensor_id, event_type, date_from, date_to):
        dt_from = _parse_dt(date_from)
        dt_to = _parse_dt(date_to)
        result = []
        for event in events:
            if sensor_id is not None:
                try:
                    if int(event.get("sensor_id")) != int(sensor_id):
                        continue
                except (TypeError, ValueError):
                    continue
            if event_type and str(event_type) not in str(event.get("event_type") or ""):
                continue
            ts = _parse_dt(event.get("timestamp") or event.get("started_at"))
            if ts and not self._in_range(ts, dt_from, dt_to):
                continue
            result.append(event)
        return result

    def acknowledge_event(self, event_id, operator="operator", comment=""):
        payload = load_runtime_json(str(self.events_path()), default={"events": []})
        for event in payload.get("events") or []:
            if int(event.get("id") or -1) == int(event_id):
                event["acknowledged"] = True
                event["acknowledged_at"] = _now_iso()
                event["acknowledged_by"] = operator or "operator"
                if comment:
                    event["comment"] = comment
                payload["timestamp"] = _now_iso()
                atomic_save_json(str(self.events_path()), payload)
                return event
        return None

    def cleanup(self):
        with self._lock:
            config = self.config()
            archive = self.load_archive()
            self._apply_retention(archive, config)
            archive["last_updated"] = _now_iso()
            self.save_archive(archive)
            daily = self.generate_daily_view(archive)
            return {
                "status": "ok",
                "last_updated": archive["last_updated"],
                "daily_days": len(daily.get("days") or {}),
            }

    def export(self, sensor_id=None, date_from=None, date_to=None, fmt="json", resolution="raw"):
        payload = self.query(sensor_id=sensor_id, date_from=date_from, date_to=date_to, resolution=resolution)
        if fmt == "csv":
            handle = StringIO()
            writer = csv.writer(handle, delimiter=";")
            writer.writerow([
                "sensor_id", "sensor_name", "period", "timestamp_start", "timestamp_end",
                "temp_min", "temp_max", "temp_avg", "hum_min", "hum_max", "hum_avg", "sample_count", "status",
            ])
            for item in payload.get("data") or []:
                temp = item.get("temperature") or {}
                hum = item.get("humidity") or {}
                writer.writerow([
                    item.get("sensor_id"),
                    item.get("sensor_name"),
                    item.get("period"),
                    item.get("timestamp_start") or item.get("period_start"),
                    item.get("timestamp_end") or item.get("period_end"),
                    temp.get("min", item.get("temp_min")),
                    temp.get("max", item.get("temp_max")),
                    temp.get("avg", item.get("temp_avg")),
                    hum.get("min", item.get("hum_min")),
                    hum.get("max", item.get("hum_max")),
                    hum.get("avg", item.get("hum_avg")),
                    item.get("sample_count"),
                    item.get("status"),
                ])
            return handle.getvalue()
        return payload

    def _sensor_name(self, sensor_id):
        try:
            sensor = get_sensor_by_id(int(sensor_id))
        except (TypeError, ValueError):
            sensor = None
        return (sensor or {}).get("name") or f"Датчик {sensor_id}"

    def _merge_dict(self, base, patch):
        result = dict(base or {})
        for key, value in (patch or {}).items():
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = self._merge_dict(result[key], value)
            else:
                result[key] = value
        return result

    def _validate_config(self, config):
        collection = config.setdefault("data_collection", {})
        if collection.get("mode") not in ("periodic", "watch", "combined"):
            raise ValueError("mode должен быть periodic, watch или combined")
        storage = config.setdefault("storage", {})
        storage.setdefault("json_file", {}).setdefault("path", "./data/archive.json")
        storage.setdefault("sqlite", {}).setdefault("path", "./data/archive.db")
        retention = config.setdefault("retention", {})
        if int(retention.get("max_days") or 365) < 1:
            raise ValueError("retention.max_days должен быть больше 0")
