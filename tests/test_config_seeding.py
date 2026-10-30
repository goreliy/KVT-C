"""First-run templates must never replace a site's existing configuration."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from shared.paths import SEEDED_CONFIGS, bundled_resource, seed_default_configs


class ConfigSeedingTests(unittest.TestCase):
    def test_clean_install_has_all_valid_templates(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, KVT_HOME=root):
            self.assertEqual(set(seed_default_configs()), set(SEEDED_CONFIGS))
            for name in SEEDED_CONFIGS:
                config = json.loads((Path(root) / 'data' / 'config' / name).read_text(encoding='utf-8'))
                self.assertIsInstance(config, dict)

    def test_existing_site_config_and_secrets_survive_reseeding(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, KVT_HOME=root):
            folder = Path(root) / 'data' / 'config'
            folder.mkdir(parents=True)
            existing = {'system_config.json': b'{"system":{"name":"SITE-42"},"sensors":[{"id":42}]}',
                        'notifications.json': b'{"email":{"smtp_server":"site.example"}}',
                        'notification_secrets.key': b'local-secret',
                        'poller_config.json': b''}
            for name, value in existing.items():
                (folder / name).write_bytes(value)
            copied = seed_default_configs()
            self.assertNotIn('system_config.json', copied)
            for name, value in existing.items():
                self.assertEqual((folder / name).read_bytes(), value)
            self.assertEqual(seed_default_configs(), [])

    def test_templates_do_not_contain_site_sensors_or_mail_recipients(self):
        folder = Path(bundled_resource('default_config'))
        system = json.loads((folder / 'system_config.json').read_text(encoding='utf-8'))
        notifications = json.loads((folder / 'notifications.json').read_text(encoding='utf-8'))
        poller = json.loads((folder / 'poller_config.json').read_text(encoding='utf-8'))
        self.assertEqual(system['sensors'], [])
        self.assertFalse(notifications['email']['enabled'])
        self.assertEqual(notifications['email']['recipients'], [])
        self.assertFalse(poller['auto_start'])
        self.assertEqual(poller['poll_ports'], [])


if __name__ == '__main__':
    unittest.main()
