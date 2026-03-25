# Project Structure

## Architecture
Three independent subsystems communicating via shared JSON files:

```
Modbus Poller (5001) → current.json / modbus_log.json
                              ↓
Archive Manager (5002) → archive.db / archive.json
                              ↓
Web Visualizer (5000) → browser
```

## Directory Layout

```
├── shared/                     # Shared Python modules used by all subsystems
│   └── config_manager.py       # Central config read/write, sensor CRUD, validation
│
├── visualizer/                 # Subsystem 3: Flask web UI (port 5000)
│   ├── app.py                  # Flask app factory (create_app)
│   ├── routes/                 # Blueprint modules: main, settings, api, floorplan
│   ├── templates/              # Jinja2 templates (base.html + pages)
│   │   └── settings/           # Settings sub-pages (sensors, poller, archive, etc.)
│   └── static/                 # CSS, floor plan images
│
├── data/                       # Runtime data (JSON state files)
│   ├── current.json            # Latest sensor readings from poller
│   ├── modbus_log.json         # Modbus TX/RX packet log (ring buffer, max 1000)
│   ├── events.json             # Alarm/warning event journal
│   ├── archive.json            # Compressed historical measurements
│   └── config/                 # All configuration files
│       ├── system_config.json  # Sensor definitions, system metadata
│       ├── poller_config.json  # COM port, baud rate, poll period
│       ├── archive_config.json # Storage backends, compression, retention
│       ├── layout.json         # Dashboard sensor tile positions
│       ├── theme_config.json   # Dark/light theme colors, app title
│       ├── floorplan_config.json # Floor plan layouts with sensor placement
│       ├── notifications.json  # Email/Telegram notification settings
│       └── backups/            # Auto-generated config backups (versioned)
│
├── MocTestServer/              # Mock server for development without real hardware
│   └── server/                 # Flask app simulating Modbus poller + archive data
│
├── requirements.txt            # Production Python dependencies
└── Общее ТЗ на систему КВТ С.md  # Full technical specification (Russian)
```

## Key Patterns
- Flask app factory pattern (`create_app()` in `visualizer/app.py`)
- Blueprints for route organization: `main_bp`, `settings_bp`, `api_bp`, `floorplan_bp`
- Centralized config management through `shared/config_manager.py` — all config load/save goes through here
- Config versioning with automatic backup on every save
- Sensor CRUD with validation in `config_manager.py`
- Theme injection via Flask `context_processor` (available in all templates)
- JSON files as the inter-process communication layer between subsystems
- `sys.path.insert(0, ...)` used in app.py to resolve `shared` module imports
