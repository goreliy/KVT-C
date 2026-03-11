"""
Генератор исторических данных для Mock Archive Server
"""

import random
import math
from datetime import datetime, timedelta
from typing import Dict, List, Any

from ..utils import merge_config


class HistoryGenerator:
    """Генератор исторических данных"""

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or self._default_config()
        self._data_cache: Dict[int, List[Dict]] = {}
        self._generate_history()

    @staticmethod
    def _default_config() -> Dict[str, Any]:
        return {
            "sensor_count": 10, "history_days": 30,
            "data_resolution_ms": 60000, "scenario": "normal",
            "values": {
                "temperature": {"base": 22.0, "variation": 3.0, "daily_amplitude": 2.0},
                "humidity": {"base": 45.0, "variation": 5.0, "daily_amplitude": 10.0}
            },
            "gaps": {"enabled": False, "probability": 0.05, "max_duration_minutes": 30},
            "compression_ratio": 0.3
        }

    def _generate_history(self):
        cfg = self.config
        end_time = datetime.now()
        start_time = end_time - timedelta(days=cfg["history_days"])
        interval = timedelta(milliseconds=cfg["data_resolution_ms"])
        gaps_cfg = cfg["gaps"]
        temp_cfg = cfg["values"]["temperature"]
        hum_cfg = cfg["values"]["humidity"]

        for sensor_id in range(1, cfg["sensor_count"] + 1):
            points = []
            current_time = start_time
            sensor_offset = (sensor_id - 1) * 0.5

            while current_time <= end_time:
                if gaps_cfg["enabled"] and random.random() < gaps_cfg["probability"]:
                    current_time += timedelta(minutes=random.randint(1, gaps_cfg["max_duration_minutes"]))
                    continue

                hour = current_time.hour + current_time.minute / 60.0
                daily_factor = math.sin((hour - 6) * math.pi / 12)

                temp = (temp_cfg["base"] + sensor_offset
                        + temp_cfg["daily_amplitude"] * daily_factor
                        + random.uniform(-temp_cfg["variation"], temp_cfg["variation"]))
                hum = (hum_cfg["base"]
                       - hum_cfg["daily_amplitude"] * daily_factor
                       + random.uniform(-hum_cfg["variation"], hum_cfg["variation"]))

                points.append({
                    "timestamp": current_time.isoformat(),
                    "temperature": round(max(-40, min(85, temp)), 1),
                    "humidity": round(max(0, min(100, hum)), 1),
                    "status": "normal"
                })
                current_time += interval

            self._data_cache[sensor_id] = points

    def query(self, sensor_id: int, from_time: datetime, to_time: datetime,
              resolution: str = "minute") -> Dict[str, Any]:
        if sensor_id not in self._data_cache:
            return {"error": "Sensor not found", "data": []}

        filtered = [
            p for p in self._data_cache[sensor_id]
            if from_time <= datetime.fromisoformat(p["timestamp"]) <= to_time
        ]

        aggregators = {"hour": "%Y-%m-%dT%H:00:00", "day": "%Y-%m-%dT00:00:00"}
        fmt = aggregators.get(resolution)
        aggregated = self._aggregate(filtered, fmt) if fmt else filtered

        return {
            "sensor_id": sensor_id,
            "from": from_time.isoformat(), "to": to_time.isoformat(),
            "resolution": resolution, "data": aggregated,
            "_mock": {"generated": True, "scenario": self.config["scenario"]}
        }

    @staticmethod
    def _aggregate(data: List[Dict], time_format: str) -> List[Dict]:
        """Универсальная агрегация по временному формату"""
        if not data:
            return []

        buckets: Dict[str, List[Dict]] = {}
        for point in data:
            key = datetime.fromisoformat(point["timestamp"]).strftime(time_format)
            buckets.setdefault(key, []).append(point)

        result = []
        for key, points in sorted(buckets.items()):
            temps = [p["temperature"] for p in points]
            hums = [p["humidity"] for p in points]
            result.append({
                "timestamp": key,
                "temperature": {
                    "avg": round(sum(temps) / len(temps), 1),
                    "min": round(min(temps), 1), "max": round(max(temps), 1)
                },
                "humidity": {
                    "avg": round(sum(hums) / len(hums), 1),
                    "min": round(min(hums), 1), "max": round(max(hums), 1)
                },
                "status": "normal", "sample_count": len(points)
            })
        return result

    def get_status(self) -> Dict[str, Any]:
        total_records = sum(len(data) for data in self._data_cache.values())
        return {
            "sensor_count": len(self._data_cache),
            "total_records": total_records,
            "history_days": self.config["history_days"],
            "resolution_ms": self.config["data_resolution_ms"],
            "memory_usage_mb": round(total_records * 100 / 1024 / 1024, 2)
        }

    def regenerate(self):
        self._data_cache.clear()
        self._generate_history()

    def update_config(self, new_config: Dict[str, Any]):
        merge_config(self.config, new_config)
        self.regenerate()
