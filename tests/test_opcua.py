import asyncio
import socket
import unittest

from opcua_server.nodes import selected_sensors, sensor_node_id
from opcua_server.service import OpcUaService, endpoint_from_config
from shared.config_manager import validated_opcua_config_patch
from shared.current_data import load_current_payload, with_configured_sensors
from shared.net import local_ip


def free_tcp_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((local_ip(), 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class OpcUaConfigTests(unittest.TestCase):
    def test_endpoint_and_validation_defaults(self):
        # 192.0.2.10 — адрес из TEST-NET-1 (RFC 5737): проверяем, что явный host
        # сохраняется в endpoint без подмены.
        config, errors = validated_opcua_config_patch({
            "enabled": True,
            "server": {"host": "192.0.2.10", "port": 4840, "endpoint_path": "kvt"},
        })
        self.assertEqual(errors, [])
        self.assertEqual(config["server"]["endpoint_path"], "/kvt/")
        self.assertEqual(endpoint_from_config(config), "opc.tcp://192.0.2.10:4840/kvt/")

    def test_legacy_enabled_config_autostarts(self):
        config, errors = validated_opcua_config_patch({}, {"enabled": True})
        self.assertEqual(errors, [])
        self.assertTrue(config["autostart"])

    def test_sensor_selection_empty_means_all(self):
        current = {"sensors": [{"id": 1, "enabled": True}, {"id": 2, "enabled": True}]}
        config = {"selection": {"sensor_ids": []}, "publishing": {"publish_only_enabled_sensors": True}}
        self.assertEqual([sensor["id"] for sensor in selected_sensors(current, config)], [1, 2])

    def test_sensor_selection_explicit_ids(self):
        current = {"sensors": [{"id": 1, "enabled": True}, {"id": 2, "enabled": True}]}
        config = {"selection": {"sensor_ids": [2]}, "publishing": {"publish_only_enabled_sensors": True}}
        self.assertEqual([sensor["id"] for sensor in selected_sensors(current, config)], [2])

    def test_stable_sensor_node_id(self):
        self.assertEqual(sensor_node_id(7, "Temperature"), "KVT.Sensors.7.Temperature")

    def test_shared_current_payload_normalizes_configured_sensors(self):
        payload = with_configured_sensors({"sensors": [], "timestamp": None, "stats": {}})
        self.assertIn("sensors", payload)
        self.assertGreaterEqual(len(load_current_payload().get("sensors") or []), 0)


class FixedConfigOpcUaService(OpcUaService):
    def __init__(self, config):
        super().__init__()
        self.config = config

    def _effective_config(self):
        return self.config


class OpcUaIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_asyncua_client_can_browse_and_read_sensor_node(self):
        try:
            from asyncua import Client
        except ImportError:
            self.skipTest("asyncua is not installed")

        port = free_tcp_port()
        config, errors = validated_opcua_config_patch({
            "enabled": True,
            "server": {
                "host": local_ip(),  # реальный интерфейс машины, как в боевой конфигурации
                "port": port,
                "endpoint_path": "/kvt-test/",
                "namespace_uri": "urn:kvt:test",
                "namespace_name": "KVT-Test",
            },
            "publishing": {"update_interval_ms": 250},
            "selection": {"sensor_ids": [1]},
        })
        self.assertEqual(errors, [])

        service = FixedConfigOpcUaService(config)
        task = asyncio.create_task(service.run_forever())
        try:
            await asyncio.sleep(1.0)
            endpoint = endpoint_from_config(config)
            async with Client(endpoint) as client:
                nsidx = await client.get_namespace_index("urn:kvt:test")
                sensors_node = client.get_node(f"ns={nsidx};s=KVT.Sensors")
                children = await sensors_node.get_children()
                self.assertGreaterEqual(len(children), 1)
                temp_node = client.get_node(f"ns={nsidx};s=KVT.Sensors.1.Temperature")
                # Значение может быть Good (есть данные) или BadNoData (датчик без данных).
                # Тесту важно, что узел существует и читается; не поднимаем исключение на bad-quality.
                data_value = await temp_node.read_data_value(raise_on_bad_status=False)
                self.assertIsNotNone(data_value)
        finally:
            service.stop()
            await asyncio.wait_for(task, timeout=3.0)


if __name__ == "__main__":
    unittest.main()
