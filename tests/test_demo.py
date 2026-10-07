import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

import requests

REPO = Path(__file__).resolve().parents[1]


class DemoIsolationTests(unittest.TestCase):
    def test_real_pages_routing_repeat_and_isolation(self):
        with tempfile.TemporaryDirectory(prefix='kvt-demo-test-') as directory:
            root = Path(directory)
            env = {**os.environ, 'KVT_HOME': directory, 'PYTHONDONTWRITEBYTECODE': '1'}
            env.pop('KVT_DEMO_TOKEN', None)
            with (root / 'host.log').open('wb') as log:
                host = subprocess.Popen([sys.executable, '-B', str(REPO / 'tests' / 'demo_host.py')], env=env, cwd=REPO,
                                        stdout=log, stderr=log, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            client = requests.Session()
            client.trust_env = False
            base = None
            try:
                deadline = time.monotonic() + 20
                while not (root / 'host-ready.json').exists() and host.poll() is None and time.monotonic() < deadline:
                    time.sleep(.1)
                self.assertTrue((root / 'host-ready.json').exists(), (root / 'host.log').read_text())
                base = 'http://127.0.0.1:' + str(json.loads((root / 'host-ready.json').read_text())['port'])
                production = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (root / 'data' / 'config').iterdir() if p.is_file()}
                self.assertEqual(client.get(base + '/help').status_code, 200)
                launch = client.post(base + '/demo/start')
                self.assertEqual(launch.status_code, 200, launch.text)
                prefix = launch.json()['url'].split('?')[0].rstrip('/')
                project_id = prefix.rsplit('/', 1)[1]
                project = root / 'data' / 'demo_projects' / project_id
                def get(path):
                    return client.get(base + prefix + path)
                def post(path, payload):
                    return client.post(base + prefix + path, json=payload)
                # Another browser cannot reach the project or its internal loopback server.
                stranger = requests.Session()
                stranger.trust_env = False
                self.assertEqual(stranger.get(base + prefix + '/api/config').status_code, 404)
                port = json.loads((project / 'ready.json').read_text())['port']
                self.assertEqual(stranger.get(f'http://127.0.0.1:{port}/api/config').status_code, 403)
                stranger.close()
                for path in ['/', '/settings/', '/settings/poller', '/settings/sensors', '/floorplan/', '/settings/archive',
                             '/settings/notifications', '/settings/opcua', '/settings/mqtt', '/settings/reports',
                             '/settings/network', '/settings/system', '/settings/appearance', '/settings/config-transfer',
                             '/events', '/journal/temperatures', '/journal/violations', '/logbook', '/export', '/help']:
                    response = get(path)
                    self.assertEqual(response.status_code, 200, path + ': ' + response.text[:300])
                    self.assertIn('KVT_DEMO', response.text)
                    self.assertIn(prefix + '/static/js/demo.js', response.text)
                self.assertTrue(get('/api/current').json()['demo'])
                self.assertEqual(post('/_demo/move', {'action': 'next'}).json()['step'], 1)
                before = get('/api/config').json()
                updated = json.loads(json.dumps(before))
                updated['system']['name'] = 'Изменённое демо'
                self.assertEqual(post('/api/config', updated).status_code, 200)
                self.assertIn(1, get('/_demo/state').json()['completed'])
                self.assertEqual(post('/_demo/move', {'action': 'next'}).json()['step'], 2)
                self.assertEqual(post('/_demo/move', {'action': 'next'}).status_code, 400)
                self.assertEqual(post('/_demo/move', {'action': 'back'}).json()['step'], 1)
                self.assertEqual(get('/api/config').json()['system']['name'], 'Изменённое демо')
                repeated = post('/_demo/move', {'action': 'repeat'}).json()
                self.assertNotIn(1, repeated['completed'])
                self.assertEqual(get('/api/config').json()['system']['name'], before['system']['name'])
                self.assertEqual(post('/_demo/move', {'action': 'goto', 'step': 15}).status_code, 400)
                # Mail and real devices are intercepted; configured paths cannot escape.
                self.assertTrue(post('/api/notifications/test', {'channel': 'email'}).json()['demo'])
                self.assertTrue(post('/api/mockserver/start', {}).json()['demo'])
                archive = post('/api/archive/config', {'storage': {'sqlite': {'enabled': True, 'path': '../outside.db'}},
                                                      'data_collection': {'source_file': '../outside.json'}})
                self.assertEqual(archive.status_code, 200, archive.text)
                self.assertEqual(archive.json()['storage']['sqlite']['path'], './data/archive.db')
                self.assertEqual(client.post(base + prefix + '/api/config/bundle/import').status_code, 403)
                self.assertEqual(post('/_demo/alarm', {}).status_code, 200)
                self.assertTrue(get('/api/archive/events').json())
                self.assertEqual(client.post(base + '/demo/start', headers={'Origin': 'https://other.example'}).status_code, 403)
                self.assertEqual(production, {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (root / 'data' / 'config').iterdir() if p.is_file()})
                self.assertEqual(client.post(base + '/demo/end').status_code, 200)
                self.assertEqual(get('/api/config').status_code, 404)
            finally:
                if base:
                    client.post(base + '/demo/end')
                host.terminate()
                host.wait(timeout=10)
                client.close()
