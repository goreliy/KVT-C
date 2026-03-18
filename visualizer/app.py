"""
Flask-приложение Web Visualizer (Подсистема 3).
Порт: 5000
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask
from shared.config_manager import load_theme_config

def create_app():
    app = Flask(__name__,
                template_folder='templates',
                static_folder='static')
    app.secret_key = 'kvt-secret-key-change-in-production'

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

    app.register_blueprint(main_bp)
    app.register_blueprint(settings_bp, url_prefix='/settings')
    app.register_blueprint(api_bp, url_prefix='/api')

    return app


if __name__ == '__main__':
    app = create_app()
    app.run(host='0.0.0.0', port=5000, debug=True)