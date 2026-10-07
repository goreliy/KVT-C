"""Source-host fixture shared by HTTP and browser integration checks."""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from visualizer.demo_app import seed_project
from shared.paths import app_root
seed_project(Path(app_root()))
from visualizer.app import create_app
from werkzeug.serving import make_server
from shared.config_manager import atomic_save_json

server = make_server('127.0.0.1', 0, create_app(), threaded=True)
atomic_save_json(str(Path(app_root()) / 'host-ready.json'), {'port': server.server_port})
server.serve_forever()
