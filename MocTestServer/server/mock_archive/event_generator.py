"""
Генератор событий, журнала превышений и журнала температур для Mock Archive Server
"""

import math
import random
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional


# Маппинг типов событий на генераторы значений и приоритеты
_EVENT_VALUE_RANGES = {
    "high_temp": (35, 45), "low_temp": (-15, -5),
    "high_hum": (75, 95), "low_hum": (5, 20),
}

_EVENT_MESSAGES = {
    "warning_high_temp": "Датчик {sid}: Высокая температура {val}°C",
    "warning_low_temp": "Датчик {sid}: Низкая температура {val}°C",
    "alarm_high_temp": "АВАРИЯ Датчик {sid}: Критически высокая температура {val}°C",
    "alarm_low_temp": "АВАРИЯ Датчик {sid}: Критически низкая температура {val}°C",
    "warning_high_hum": "Датчик {sid}: Высокая влажность {val}%",
    "warning_low_hum": "Датчик {sid}: Низкая влажность {val}%",
    "sensor_offline": "Датчик {sid}: Потеря связи",
    "sensor_online": "Датчик {sid}: Связь восстановлена",
}

# Типы превышений
_VIOLATION_TYPES = ["warning_high", "warning_low", "alarm_high", "alarm_low"]


class EventGenerator:
    """Генератор событий"""

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or self._default_config()
        self._events: List[Dict] = []
        self._event_id_counter = 0
        self._generate_events()

    @staticmethod
    def _default_config() -> Dict[str, Any]:
        return {
            "sensor_count": 10, "history_days": 30,
            "include_events": True, "event_frequency": 0.01,
            "event_types": [
                "warning_high_temp", "warning_low_temp",
                "alarm_high_temp", "alarm_low_temp",
                "warning_high_hum", "warning_low_hum",
                "sensor_offline", "sensor_online"
            ]
        }

    def _generate_events(self):
        if not self.config["include_events"]:
            return

        end_time = datetime.now()
        current_time = end_time - timedelta(days=self.config["history_days"])

        while current_time <= end_time:
            for sensor_id in range(1, self.config["sensor_count"] + 1):
                if random.random() < self.config["event_frequency"]:
                    self._create_event(sensor_id, current_time)
            current_time += timedelta(hours=1)

    def _generate_value(self, event_type: str) -> Optional[float]:
        for key, (lo, hi) in _EVENT_VALUE_RANGES.items():
            if key in event_type:
                return random.uniform(lo, hi)
        return None

    @staticmethod
    def _get_priority(event_type: str) -> str:
        if "alarm" in event_type:
            return "high"
        if "warning" in event_type:
            return "medium"
        return "low"

    def _create_event(self, sensor_id: int, timestamp: datetime,
                      event_type: str = None, value: float = None) -> Dict:
        self._event_id_counter += 1

        if event_type is None:
            event_type = random.choice(self.config["event_types"])
        if value is None:
            value = self._generate_value(event_type)

        priority = self._get_priority(event_type)
        is_acked = random.random() < 0.7

        msg_template = _EVENT_MESSAGES.get(event_type, "Датчик {sid}: {val}")
        message = msg_template.format(sid=sensor_id, val=round(value, 1) if value else "")

        event = {
            "id": self._event_id_counter,
            "timestamp": timestamp.isoformat(),
            "sensor_id": sensor_id,
            "event_type": event_type,
            "priority": priority,
            "value": round(value, 1) if value else None,
            "message": message,
            "acknowledged": is_acked,
            "acknowledged_by": "operator" if is_acked else None,
            "acknowledged_at": (timestamp + timedelta(minutes=random.randint(5, 60))).isoformat() if is_acked else None
        }
        self._events.append(event)
        return event

    def get_events(self, from_time: datetime = None, to_time: datetime = None,
                   sensor_id: int = None, event_type: str = None,
                   priority: str = None, acknowledged: bool = None,
                   limit: int = 100, offset: int = 0) -> Dict[str, Any]:
        filtered = self._events

        if from_time:
            filtered = [e for e in filtered if datetime.fromisoformat(e["timestamp"]) >= from_time]
        if to_time:
            filtered = [e for e in filtered if datetime.fromisoformat(e["timestamp"]) <= to_time]
        if sensor_id:
            filtered = [e for e in filtered if e["sensor_id"] == sensor_id]
        if event_type:
            filtered = [e for e in filtered if e["event_type"] == event_type]
        if priority:
            filtered = [e for e in filtered if e["priority"] == priority]
        if acknowledged is not None:
            filtered = [e for e in filtered if e["acknowledged"] == acknowledged]

        filtered.sort(key=lambda x: x["timestamp"], reverse=True)
        total = len(filtered)
        return {"events": filtered[offset:offset + limit], "total": total, "limit": limit, "offset": offset}

    def acknowledge_event(self, event_id: int, user: str = "operator") -> Optional[Dict]:
        for event in self._events:
            if event["id"] == event_id:
                event.update({
                    "acknowledged": True,
                    "acknowledged_by": user,
                    "acknowledged_at": datetime.now().isoformat()
                })
                return event
        return None

    def add_event(self, sensor_id: int, event_type: str, value: float = None) -> Dict:
        return self._create_event(sensor_id, datetime.now(), event_type, value)

    def get_status(self) -> Dict[str, Any]:
        unacknowledged = sum(1 for e in self._events if not e["acknowledged"])
        by_priority = {p: sum(1 for e in self._events if e["priority"] == p) for p in ("high", "medium", "low")}
        return {"total_events": len(self._events), "unacknowledged": unacknowledged, "by_priority": by_priority}

    def regenerate(self):
        self._events.clear()
        self._event_id_counter = 0
        self._generate_events()


class TemperatureLogGenerator:
    """Генератор журнала температур и влажности (агрегация по часам/дням/неделям)"""

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or self._default_config()
        self._log_data: Dict[int, List[Dict]] = {}
        self._generate_log()

    @staticmethod
    def _default_config() -> Dict[str, Any]:
        return {
            "sensor_count": 10, "history_days": 30,
            "values": {
                "temperature": {"base": 22.0, "variation": 3.0, "daily_amplitude": 2.0},
                "humidity": {"base": 45.0, "variation": 5.0, "daily_amplitude": 10.0}
            }
        }

    def _generate_log(self):
        """Генерация почасовых записей для каждого датчика"""
        cfg = self.config
        end_time = datetime.now().replace(minute=0, second=0, microsecond=0)
        start_time = end_time - timedelta(days=cfg["history_days"])
        temp_cfg = cfg["values"]["temperature"]
        hum_cfg = cfg["values"]["humidity"]

        for sensor_id in range(1, cfg["sensor_count"] + 1):
            records = []
            current = start_time
            sensor_offset = (sensor_id - 1) * 0.5

            while current < end_time:
                hour = current.hour + current.minute / 60.0
                daily_factor = math.sin((hour - 6) * math.pi / 12)

                temp_center = temp_cfg["base"] + sensor_offset + temp_cfg["daily_amplitude"] * daily_factor
                hum_center = hum_cfg["base"] - hum_cfg["daily_amplitude"] * daily_factor

                # Генерируем min/max/avg для часового периода (как будто 60 измерений)
                temp_samples = [temp_center + random.uniform(-temp_cfg["variation"], temp_cfg["variation"]) for _ in range(60)]
                hum_samples = [hum_center + random.uniform(-hum_cfg["variation"], hum_cfg["variation"]) for _ in range(60)]

                records.append({
                    "period_start": current.isoformat(),
                    "period_end": (current + timedelta(hours=1)).isoformat(),
                    "period_type": "hour",
                    "temp_min": round(min(temp_samples), 1),
                    "temp_max": round(max(temp_samples), 1),
                    "temp_avg": round(sum(temp_samples) / len(temp_samples), 1),
                    "hum_min": round(max(0, min(hum_samples)), 1),
                    "hum_max": round(min(100, max(hum_samples)), 1),
                    "hum_avg": round(sum(hum_samples) / len(hum_samples), 1),
                    "sample_count": 60
                })
                current += timedelta(hours=1)

            self._log_data[sensor_id] = records

    def query(self, sensor_id: int = None, period_type: str = "hour",
              from_time: datetime = None, to_time: datetime = None) -> Dict[str, Any]:
        """Запрос журнала температур с агрегацией"""
        if sensor_id and sensor_id not in self._log_data:
            return {"sensor_id": sensor_id, "period_type": period_type, "data": []}

        sensor_ids = [sensor_id] if sensor_id else list(self._log_data.keys())
        result_data = []

        for sid in sensor_ids:
            records = self._log_data.get(sid, [])

            # Фильтрация по времени
            if from_time:
                records = [r for r in records if datetime.fromisoformat(r["period_start"]) >= from_time]
            if to_time:
                records = [r for r in records if datetime.fromisoformat(r["period_start"]) <= to_time]

            if period_type == "hour":
                aggregated = records
            elif period_type == "day":
                aggregated = self._aggregate_to_period(records, "day")
            elif period_type == "week":
                aggregated = self._aggregate_to_period(records, "week")
            else:
                aggregated = records

            if sensor_id:
                return {
                    "sensor_id": sid,
                    "sensor_name": f"ХРАН. № {sid}",
                    "period_type": period_type,
                    "data": aggregated
                }
            else:
                result_data.append({
                    "sensor_id": sid,
                    "sensor_name": f"ХРАН. № {sid}",
                    "data": aggregated
                })

        return {"period_type": period_type, "sensors": result_data}

    @staticmethod
    def _aggregate_to_period(hourly_records: List[Dict], period: str) -> List[Dict]:
        """Агрегация почасовых записей в дни или недели"""
        if not hourly_records:
            return []

        buckets: Dict[str, List[Dict]] = {}
        for rec in hourly_records:
            dt = datetime.fromisoformat(rec["period_start"])
            if period == "day":
                key = dt.strftime("%Y-%m-%d")
            else:  # week
                # Начало недели (понедельник)
                week_start = dt - timedelta(days=dt.weekday())
                key = week_start.strftime("%Y-%m-%d")
            buckets.setdefault(key, []).append(rec)

        result = []
        for key, recs in sorted(buckets.items()):
            period_start = datetime.fromisoformat(recs[0]["period_start"])
            if period == "day":
                period_end = period_start.replace(hour=23, minute=59, second=59)
            else:
                period_end = period_start + timedelta(days=6, hours=23, minutes=59, seconds=59)

            total_samples = sum(r["sample_count"] for r in recs)
            result.append({
                "period_start": period_start.replace(hour=0, minute=0, second=0).isoformat(),
                "period_end": period_end.isoformat(),
                "period_type": period,
                "temp_min": round(min(r["temp_min"] for r in recs), 1),
                "temp_max": round(max(r["temp_max"] for r in recs), 1),
                "temp_avg": round(sum(r["temp_avg"] * r["sample_count"] for r in recs) / total_samples, 1),
                "hum_min": round(min(r["hum_min"] for r in recs), 1),
                "hum_max": round(max(r["hum_max"] for r in recs), 1),
                "hum_avg": round(sum(r["hum_avg"] * r["sample_count"] for r in recs) / total_samples, 1),
                "sample_count": total_samples
            })
        return result

    def get_status(self) -> Dict[str, Any]:
        total = sum(len(recs) for recs in self._log_data.values())
        return {"sensor_count": len(self._log_data), "total_records": total}

    def regenerate(self):
        self._log_data.clear()
        self._generate_log()


class ViolationGenerator:
    """Генератор журнала превышений границ"""

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or self._default_config()
        self._violations: List[Dict] = []
        self._violation_id_counter = 0
        self._generate_violations()

    @staticmethod
    def _default_config() -> Dict[str, Any]:
        return {
            "sensor_count": 10, "history_days": 30,
            "violation_frequency": 0.005,
            "thresholds": {
                "temperature": {"min": -10.0, "max": 40.0},
                "humidity": {"min": 20.0, "max": 80.0}
            }
        }

    def _generate_violations(self):
        """Генерация записей превышений за весь период истории"""
        cfg = self.config
        end_time = datetime.now()
        current_time = end_time - timedelta(days=cfg["history_days"])

        while current_time <= end_time:
            for sensor_id in range(1, cfg["sensor_count"] + 1):
                if random.random() < cfg["violation_frequency"]:
                    self._create_violation(sensor_id, current_time)
            current_time += timedelta(hours=1)

    def _create_violation(self, sensor_id: int, started_at: datetime,
                          parameter: str = None, violation_type: str = None) -> Dict:
        self._violation_id_counter += 1

        if parameter is None:
            parameter = random.choice(["temperature", "humidity"])
        if violation_type is None:
            violation_type = random.choice(_VIOLATION_TYPES)

        thresholds = self.config["thresholds"].get(parameter, {"min": 0, "max": 100})

        # Генерация значений в зависимости от типа превышения
        if "high" in violation_type:
            threshold = thresholds["max"]
            value_at_start = threshold + random.uniform(0.5, 3.0)
            value_peak = value_at_start + random.uniform(0.5, 5.0)
        else:
            threshold = thresholds["min"]
            value_at_start = threshold - random.uniform(0.5, 3.0)
            value_peak = value_at_start - random.uniform(0.5, 5.0)

        duration = random.randint(60, 3600)  # от 1 мин до 1 часа
        is_closed = random.random() < 0.85
        ended_at = started_at + timedelta(seconds=duration) if is_closed else None
        is_acked = is_closed and random.random() < 0.6

        unit = "°C" if parameter == "temperature" else "%"

        violation = {
            "id": self._violation_id_counter,
            "sensor_id": sensor_id,
            "sensor_name": f"ХРАН. № {sensor_id}",
            "started_at": started_at.isoformat(),
            "ended_at": ended_at.isoformat() if ended_at else None,
            "duration_seconds": duration if is_closed else None,
            "parameter": parameter,
            "violation_type": violation_type,
            "value_at_start": round(value_at_start, 1),
            "value_peak": round(value_peak, 1),
            "threshold": round(threshold, 1),
            "unit": unit,
            "acknowledged": is_acked,
            "acknowledged_at": (started_at + timedelta(minutes=random.randint(10, 120))).isoformat() if is_acked else None,
            "acknowledged_by": "operator" if is_acked else None,
            "comment": None
        }
        self._violations.append(violation)
        return violation

    def get_violations(self, sensor_id: int = None, from_time: datetime = None,
                       to_time: datetime = None, status: str = "all",
                       parameter: str = None, acknowledged: bool = None,
                       limit: int = 100, offset: int = 0) -> Dict[str, Any]:
        filtered = self._violations

        if sensor_id:
            filtered = [v for v in filtered if v["sensor_id"] == sensor_id]
        if from_time:
            filtered = [v for v in filtered if datetime.fromisoformat(v["started_at"]) >= from_time]
        if to_time:
            filtered = [v for v in filtered if datetime.fromisoformat(v["started_at"]) <= to_time]
        if parameter:
            filtered = [v for v in filtered if v["parameter"] == parameter]
        if acknowledged is not None:
            filtered = [v for v in filtered if v["acknowledged"] == acknowledged]

        # Фильтрация по статусу
        if status == "open":
            filtered = [v for v in filtered if v["ended_at"] is None]
        elif status == "closed":
            filtered = [v for v in filtered if v["ended_at"] is not None]
        elif status == "unacknowledged":
            filtered = [v for v in filtered if not v["acknowledged"]]

        filtered.sort(key=lambda x: x["started_at"], reverse=True)
        total = len(filtered)
        return {"violations": filtered[offset:offset + limit], "total": total, "limit": limit, "offset": offset}

    def acknowledge_violation(self, violation_id: int, user: str = "operator",
                              comment: str = None) -> Optional[Dict]:
        for v in self._violations:
            if v["id"] == violation_id:
                v.update({
                    "acknowledged": True,
                    "acknowledged_by": user,
                    "acknowledged_at": datetime.now().isoformat(),
                    "comment": comment
                })
                return v
        return None

    def get_status(self) -> Dict[str, Any]:
        total = len(self._violations)
        open_count = sum(1 for v in self._violations if v["ended_at"] is None)
        unacked = sum(1 for v in self._violations if not v["acknowledged"])
        by_param = {}
        for p in ("temperature", "humidity"):
            by_param[p] = sum(1 for v in self._violations if v["parameter"] == p)
        return {
            "total_violations": total,
            "open": open_count,
            "closed": total - open_count,
            "unacknowledged": unacked,
            "by_parameter": by_param
        }

    def regenerate(self):
        self._violations.clear()
        self._violation_id_counter = 0
        self._generate_violations()
