# Tech Stack & Build

## Language & Runtime
- Python 3.8+
- No build step — run directly with Python

## Core Dependencies (requirements.txt)
- flask 2.0.3 — web framework
- pymodbus 2.5.3 — Modbus RTU communication
- pyserial 3.5 — serial port access
- sqlalchemy 1.4.46 — database ORM (SQLite / PostgreSQL)
- apscheduler 3.9.1 — scheduled tasks
- requests 2.28.1 — HTTP client
- jsonschema 4.17.3 — JSON validation
- Werkzeug 2.0.3, Jinja2 3.0.3, MarkupSafe 2.1.1 — Flask dependencies

## Frontend
- Server-rendered HTML via Jinja2 templates
- Plain CSS (no preprocessor or bundler)
- Vanilla JavaScript (no framework)

## Data Storage
- JSON files for runtime state (`current.json`, `modbus_log.json`, `archive.json`, `events.json`)
- JSON config files in `data/config/`
- SQLite or PostgreSQL for long-term archive (via SQLAlchemy)

## Common Commands

```bash
# Setup
python -m venv venv
venv\Scripts\activate        # Windows
source venv/bin/activate     # Linux/Mac
pip install -r requirements.txt

# Run subsystems
python -m visualizer.app     # Web UI on port 5000
python -m poller.app         # Modbus Poller on port 5001
python -m archiver.app       # Archive Manager on port 5002

# Run mock test server (for development without hardware)
pip install -r MocTestServer/requirements.txt
python MocTestServer/server/run.py

# Docker
docker-compose up -d
```

## Containerization
- Docker and docker-compose supported
- Targets ARM v7 and x86_64
