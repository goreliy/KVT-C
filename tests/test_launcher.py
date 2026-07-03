import json
import tempfile
import unittest
from pathlib import Path

import run_kvt


class LauncherAutostartTests(unittest.TestCase):
    def setUp(self):
        self._opcua_config = run_kvt.OPCUA_CONFIG
        self._mqtt_config = run_kvt.MQTT_CONFIG

    def tearDown(self):
        run_kvt.OPCUA_CONFIG = self._opcua_config
        run_kvt.MQTT_CONFIG = self._mqtt_config

    def _write_config(self, root, name, payload):
        path = Path(root) / name
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        return path

    def test_legacy_enabled_optional_service_autostarts(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_kvt.OPCUA_CONFIG = self._write_config(tmp, "opcua_config.json", {"enabled": True})
            self.assertTrue(run_kvt._service_autostart("opcua"))

    def test_explicit_autostart_false_overrides_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_kvt.MQTT_CONFIG = self._write_config(tmp, "mqtt_config.json", {
                "enabled": True,
                "autostart": False,
            })
            self.assertFalse(run_kvt._service_autostart("mqtt"))

    def test_missing_required_config_keeps_core_services_autostarted(self):
        self.assertTrue(run_kvt._service_autostart("poller"))


if __name__ == "__main__":
    unittest.main()
