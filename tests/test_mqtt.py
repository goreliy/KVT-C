import io
import json
import os
import tempfile
import unittest
import zipfile

from mqtt_bridge import service as mqtt_service
from mqtt_bridge.service import (
    build_sensor_payloads,
    build_snapshot_payload,
    load_inbound_payload,
    mqtt_topic_map,
    normalize_base_topic,
    sensor_topic,
    store_inbound_sensor,
)
from shared.config_bundle import REQUIRED_CONFIG_FILES, export_config_bundle, import_config_bundle
from shared.config_manager import default_mqtt_config, validated_mqtt_config_patch


class MqttConfigTests(unittest.TestCase):
    def test_defaults_match_public_interface(self):
        config, errors = validated_mqtt_config_patch({})
        self.assertEqual(errors, [])
        self.assertFalse(config["autostart"])
        self.assertEqual(config["broker"]["host"], "0.0.0.0")
        self.assertEqual(config["broker"]["port"], 1883)
        self.assertEqual(config["topics"]["base"], "kvt-c")
        self.assertTrue(config["publishing"]["enabled"])
        self.assertTrue(config["publishing"]["retain"])
        self.assertEqual(config["publishing"]["interval_ms"], 1000)
        self.assertTrue(config["receiving"]["enabled"])

    def test_legacy_enabled_config_autostarts(self):
        config, errors = validated_mqtt_config_patch({}, {"enabled": True})
        self.assertEqual(errors, [])
        self.assertTrue(config["autostart"])

    def test_validation_rejects_bad_port_qos_and_topic(self):
        _config, errors = validated_mqtt_config_patch({
            "broker": {"port": 70000},
            "topics": {"base": "bad/#"},
            "publishing": {"qos": 9},
            "receiving": {"qos": -1},
        })
        self.assertGreaterEqual(len(errors), 4)

    def test_topic_normalization_and_map(self):
        config, errors = validated_mqtt_config_patch({"topics": {"base": "/kvt-c//line-a/"}})
        self.assertEqual(errors, [])
        self.assertEqual(normalize_base_topic(config), "kvt-c/line-a")
        topics = mqtt_topic_map(config)
        self.assertEqual(topics["status"], "kvt-c/line-a/status")
        self.assertEqual(topics["current"], "kvt-c/line-a/current")
        self.assertEqual(topics["inbound_sensor_filter"], "kvt-c/line-a/inbound/sensors/+")
        self.assertEqual(sensor_topic(config, 7), "kvt-c/line-a/sensors/7")


class MqttPayloadTests(unittest.TestCase):
    def test_snapshot_and_per_sensor_payloads_preserve_current_shape(self):
        current = {
            "timestamp": "2026-07-03T10:00:00Z",
            "sensors": [
                {"id": 1, "temperature": {"value": 22.1}, "humidity": {"value": 45.2}},
                {"id": 2, "temperature": {"value": None}, "humidity": {"value": None}},
            ],
        }
        self.assertEqual(build_snapshot_payload(current), current)
        sensors = build_sensor_payloads(current)
        self.assertEqual(sensors[1]["temperature"]["value"], 22.1)
        self.assertEqual(sensors[2]["humidity"]["value"], None)

    def test_inbound_sensor_store_is_separate_from_current_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            original_path = mqtt_service.INBOUND_PATH
            mqtt_service.INBOUND_PATH = os.path.join(tmp, "mqtt_inbound.json")
            self.addCleanup(setattr, mqtt_service, "INBOUND_PATH", original_path)

            entry = store_inbound_sensor(3, {
                "temperature": {"value": 21.5, "status": "normal"},
                "humidity": {"value": 44.0, "status": "normal"},
                "timestamp": "2026-07-03T10:01:00Z",
            }, "kvt-c/inbound/sensors/3")

            self.assertEqual(entry["id"], 3)
            payload = load_inbound_payload()
            self.assertIn("3", payload["sensors"])
            self.assertEqual(payload["sensors"]["3"]["temperature"]["value"], 21.5)


class MqttConfigBundleTests(unittest.TestCase):
    def _write_minimal_config_tree(self, root):
        config_dir = os.path.join(root, "data", "config")
        os.makedirs(config_dir, exist_ok=True)
        os.makedirs(os.path.join(root, "visualizer", "static", "floorplans"), exist_ok=True)
        for name in REQUIRED_CONFIG_FILES:
            with open(os.path.join(config_dir, name), "w", encoding="utf-8") as handle:
                json.dump({}, handle)
        with open(os.path.join(config_dir, "mqtt_config.json"), "w", encoding="utf-8") as handle:
            json.dump(default_mqtt_config(), handle)

    def _old_bundle_without_mqtt(self):
        buffer = io.BytesIO()
        manifest = {
            "format": "kvt-config-bundle",
            "version": 1,
            "created_at": "2026-07-03T00:00:00",
            "source": {"app": "KVT-C"},
            "restore_scope": {"config": True, "floorplan_assets": True, "diagnostics": False},
            "config_files": sorted(REQUIRED_CONFIG_FILES),
            "floorplan_assets": [],
            "diagnostics": [],
        }
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
            for name in REQUIRED_CONFIG_FILES:
                archive.writestr(f"config/{name}", "{}")
        return buffer.getvalue()

    def test_old_bundle_without_mqtt_gets_default_config(self):
        with tempfile.TemporaryDirectory() as root:
            self._write_minimal_config_tree(root)
            result = import_config_bundle(self._old_bundle_without_mqtt(), root_dir=root, create_backup=False)
            self.assertIn("mqtt_config.json", result["imported_config_files"])
            mqtt_path = os.path.join(root, "data", "config", "mqtt_config.json")
            with open(mqtt_path, "r", encoding="utf-8-sig") as handle:
                imported = json.load(handle)
            self.assertEqual(imported["topics"]["base"], "kvt-c")
            self.assertFalse(imported["autostart"])

    def test_password_file_is_not_exported(self):
        with tempfile.TemporaryDirectory() as root:
            self._write_minimal_config_tree(root)
            with open(os.path.join(root, "data", "config", "mqtt_password.key"), "w", encoding="utf-8") as handle:
                handle.write("secret")
            archive_bytes, _filename, manifest = export_config_bundle(root_dir=root, include_diagnostics=True)
            self.assertIn("mqtt_config.json", manifest["config_files"])
            with zipfile.ZipFile(io.BytesIO(archive_bytes), "r") as archive:
                names = archive.namelist()
            self.assertIn("config/mqtt_config.json", names)
            self.assertNotIn("config/mqtt_password.key", names)


if __name__ == "__main__":
    unittest.main()
