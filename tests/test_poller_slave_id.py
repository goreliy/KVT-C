import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from poller.poller_service import PollPortWorker


class PollerSlaveIdTests(unittest.TestCase):
    def _worker(self):
        return PollPortWorker(
            manager=None,
            port_config={
                "id": "line-a",
                "name": "Line A",
                "transport": "serial",
                "com_port": "COM1",
                "device_slave_id": 16,
                "baudrate": 9600,
                "bytesize": 8,
                "parity": "N",
                "stopbits": 1,
                "timeout_ms": 500,
            },
            global_config={
                "value_register_base": 30000,
                "status_register_base": 40000,
                "poll_period_ms": 1000,
                "retry_count": 3,
            },
        )

    def _sensor(self):
        return {
            "id": 7,
            "name": "Sensor 7",
            "modbus_slave_id": 42,
            "modbus_addr_temp": 11,
            "modbus_addr_hum": 12,
            "temp_limits": {"min": -40, "max": 85, "warning_delta": 3, "alarm_delta": 5},
            "hum_limits": {"min": 0, "max": 100, "warning_delta": 5, "alarm_delta": 10},
            "guarded": True,
        }

    def test_poll_sensor_uses_sensor_slave_id_not_port_default(self):
        worker = self._worker()
        seen_slave_ids = []

        def fake_read(**kwargs):
            seen_slave_ids.append(kwargs["slave_id"])
            if kwargs["start_addr"] >= 40000:
                return [0, 0]
            return [0x1900, 0x3200]

        worker._read_registers_logged = fake_read

        payload = worker._poll_sensor(self._sensor(), "2026-08-14T12:00:00")

        self.assertEqual(seen_slave_ids, [42, 42])
        self.assertEqual(payload["modbus_slave_id"], 42)
        self.assertEqual(payload["temperature"]["value"], 25.0)
        self.assertEqual(payload["humidity"]["value"], 50.0)

    def test_offline_sensor_reports_sensor_slave_id_not_port_default(self):
        payload = self._worker()._offline_sensor(self._sensor(), "2026-08-14T12:00:00")

        self.assertEqual(payload["modbus_slave_id"], 42)


if __name__ == "__main__":
    unittest.main()

