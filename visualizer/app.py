"""
Flask-приложение Web Visualizer (Подсистема 3).
Порт: 5000
"""
import argparse
import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.python_compat import patch_legacy_werkzeug_ast
patch_legacy_werkzeug_ast()

from flask import Flask
from shared.config_manager import load_theme_config


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRET_PATH = os.path.join(ROOT_DIR, 'data', 'config', 'flask_secret.key')


def _load_secret_key():
    env_secret = os.environ.get('KVT_SECRET_KEY')
    if env_secret:
        return env_secret
    os.makedirs(os.path.dirname(SECRET_PATH), exist_ok=True)
    try:
        with open(SECRET_PATH, 'r', encoding='utf-8') as handle:
            secret = handle.read().strip()
            if secret:
                return secret
    except FileNotFoundError:
        pass
    secret = secrets.token_urlsafe(48)
    with open(SECRET_PATH, 'w', encoding='utf-8') as handle:
        handle.write(secret)
    return secret


def create_app():
    app = Flask(__name__,
                template_folder='templates',
                static_folder='static')
    app.secret_key = _load_secret_key()

    @app.context_processor
    def inject_theme():
        theme_cfg = load_theme_config()
        active_theme = theme_cfg.get('theme', 'dark')
        colors = theme_cfg.get('colors', {}).get(active_theme, {})
        return {
            'theme_config': theme_cfg,
            'active_theme': active_theme,
            'theme_colors': colors,
            'app_title': theme_cfg.get('app_title', 'КВТ Мониторинг')
        }

    from visualizer.routes.main import main_bp
    from visualizer.routes.settings import settings_bp
    from visualizer.routes.api import api_bp
    from visualizer.routes.floorplan import floorplan_bp
    from visualizer.routes.journal import journal_bp
    from visualizer.routes.export import export_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(settings_bp, url_prefix='/settings')
    app.register_blueprint(api_bp, url_prefix='/api')
    app.register_blueprint(floorplan_bp, url_prefix='/floorplan')
    app.register_blueprint(journal_bp)
    app.register_blueprint(export_bp)

    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='KVT Web Visualizer')
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=5000)
    parser.add_argument('--debug', action='store_true')
    args = parser.parse_args()

    app = create_app()
    app.run(host=args.host, port=args.port, debug=args.debug)
