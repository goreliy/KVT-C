"""
Flask-приложение Web Visualizer (Подсистема 3).
Порт: 5000
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask

def create_app():
    app = Flask(__name__,
                template_folder='templates',
                static_folder='static')
    app.secret_key = 'kvt-secret-key-change-in-production'

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