# Requirements Document

## Introduction

Система КВТ (Комплект контроля температуры и влажности) предназначена для автоматического измерения, архивирования и визуализации данных температуры и влажности с датчиков С2000-ВТ/С2000-ВТИ (Болид) через преобразователь С2000-ПП по протоколу Modbus RTU. Система состоит из четырёх независимых подсистем: Modbus Poller, Archive Manager, Web Visualizer и Telegram Bot.

## Glossary

- **КВТ (KVT_System)**: Комплект контроля температуры и влажности — полная система мониторинга
- **Modbus_Poller**: Подсистема опроса датчиков по протоколу Modbus RTU через RS-485
- **Archive_Manager**: Подсистема архивирования, компрессии и хранения измерений
- **Web_Visualizer**: Подсистема веб-интерфейса для визуализации данных и управления настройками
- **Telegram_Bot**: Подсистема Telegram-бота для уведомлений, команд и регулярных отчётов
- **С2000-ВТ**: Датчик температуры и влажности производства Болид
- **С2000-ПП**: Преобразователь интерфейсов RS-485 / Modbus RTU производства Болид
- **current.json**: Файл с текущими значениями датчиков, обновляемый Modbus_Poller
- **archive.json**: Файл JSON-архива измерений
- **system_config.json**: Главный конфигурационный файл системы с описанием датчиков и настроек
- **Компрессия данных**: Алгоритм схлопывания последовательных одинаковых значений для экономии места
- **Квитирование**: Подтверждение оператором факта ознакомления с событием или тревогой
- **Мнемосхема**: Главный экран Web_Visualizer с плашками датчиков на фоновой подложке
- **Плашка датчика**: Визуальный элемент на мнемосхеме, отображающий текущие значения и статус датчика
- **План помещения**: Страница с размещением маркеров датчиков на загруженном изображении/SVG плана
- **Превышение границ (violation)**: Событие выхода измеренного значения за установленные пределы
- **Report_Generator**: Подсистема автоматической генерации и сохранения отчётов по расписанию (PDF/HTML/CSV) на диск
- **OPC_UA_Server**: Подсистема OPC UA 2.0, предоставляющая текущие и архивные данные внешним клиентам по протоколу OPC UA

## Requirements

### Requirement 1: Опрос датчиков по Modbus RTU

**User Story:** As an operator, I want the system to automatically poll temperature and humidity sensors via Modbus RTU, so that I always have up-to-date measurement data.

#### Acceptance Criteria

1. WHEN the operator starts the Modbus_Poller, THE Modbus_Poller SHALL begin cyclic polling of all configured sensors using Modbus function 0x04 (Read Input Registers) with a configurable period from 100 ms to 60000 ms.
2. WHILE the Modbus_Poller is running, THE Modbus_Poller SHALL read value registers at base address 30000+N (even address for temperature, odd for humidity) and status registers at base address 40000+N for each configured sensor.
3. WHEN the Modbus_Poller receives a valid response from a sensor, THE Modbus_Poller SHALL convert raw 16-bit register values to physical units (temperature = raw / 10 °C, humidity = raw / 10 %) and write the result to current.json within 50 ms.
4. IF a sensor does not respond within the configured timeout (100–5000 ms), THEN THE Modbus_Poller SHALL mark the sensor status as "timeout" and retry up to the configured retry count (default 3) before marking the sensor as "offline".
5. WHILE the Modbus_Poller is running, THE Modbus_Poller SHALL log each Modbus TX/RX frame with timestamp, direction, raw hex data, and parsed description to modbus_log.json, maintaining a maximum of 1000 entries in a circular buffer.

### Requirement 2: Настройка параметров подключения Modbus

**User Story:** As an operator, I want to configure Modbus connection parameters (COM port, baud rate, parity, timeout), so that the system works with my specific hardware setup.

#### Acceptance Criteria

1. THE Modbus_Poller SHALL support configuration of COM port, baud rate (1200–115200), data bits (7 or 8), parity (None, Even, Odd), stop bits (1 or 2), poll period (100–60000 ms), and timeout (100–5000 ms) via poller_config.json.
2. WHEN the operator changes Modbus connection parameters via the REST API (POST /api/poller/config), THE Modbus_Poller SHALL apply the new parameters and restart the polling cycle within 2 seconds.
3. WHEN the operator requests available COM ports (GET /api/poller/ports), THE Modbus_Poller SHALL return a list of all detected serial ports on the host system.

### Requirement 3: Архивирование данных

**User Story:** As an operator, I want measurement data to be automatically archived, so that I can review historical trends and generate reports.

#### Acceptance Criteria

1. WHILE the Archive_Manager is running, THE Archive_Manager SHALL periodically read current.json and store measurements in the configured storage backend (SQLite, PostgreSQL, or JSON file).
2. THE Archive_Manager SHALL support three data collection modes: periodic (timer-based), watch (file-change-based), and combined (file-change with minimum/maximum interval constraints).
3. WHEN compression is enabled, THE Archive_Manager SHALL collapse consecutive measurements with identical values (within configured tolerance: 0.1 °C for temperature, 0.5 % for humidity) into a single record with start timestamp, end timestamp, duration, and sample count.
4. WHILE the archive storage exceeds the configured maximum size, THE Archive_Manager SHALL apply retention policies: raw data for 24 hours, 1-minute aggregation for 1–7 days, 5-minute aggregation for 7–30 days, 1-hour aggregation for 30–365 days, and 1-day aggregation beyond 1 year.
5. IF the free disk space falls below the configured minimum (default 200 MB), THEN THE Archive_Manager SHALL delete the oldest archived data until sufficient space is recovered.

### Requirement 4: Управление датчиками

**User Story:** As an operator, I want to add, edit, and remove sensors through the web interface, so that I can manage the monitoring configuration without editing files manually.

#### Acceptance Criteria

1. WHEN the operator submits a new sensor via POST /api/sensors, THE Web_Visualizer SHALL validate the sensor data (unique ID, unique Modbus addresses, valid slave ID 1–247, min < max for limits) and add the sensor to system_config.json.
2. WHEN the operator updates a sensor via PUT /api/sensors/{id}, THE Web_Visualizer SHALL validate the changes and update the sensor record in system_config.json.
3. WHEN the operator deletes a sensor via DELETE /api/sensors/{id}, THE Web_Visualizer SHALL remove the sensor from system_config.json.
4. WHEN any sensor configuration change is saved, THE Web_Visualizer SHALL increment the config_version, record the change in update_history, and create a backup copy in data/config/backups/.
5. THE KVT_System SHALL support up to 256 sensors on a single С2000-ПП, each occupying 2 Modbus addresses (temperature + humidity).

### Requirement 5: Мнемосхема (главный экран)

**User Story:** As an operator, I want to see all sensors on a visual dashboard with real-time values and status indicators, so that I can quickly assess the state of the monitored environment.

#### Acceptance Criteria

1. THE Web_Visualizer SHALL display a main screen (mnemonic diagram) with draggable sensor tiles showing sensor name, current temperature, current humidity, combined status, and Modbus addresses.
2. WHEN the operator drags a sensor tile to a new position, THE Web_Visualizer SHALL save the new coordinates to layout.json.
3. THE Web_Visualizer SHALL apply color coding to sensor tiles based on combined_status: green for "normal", blue for "guarded", yellow/orange for "warning_*", red for "alarm", and grey for "no_connection".
4. WHEN the operator uploads a background image, THE Web_Visualizer SHALL display the image as the mnemonic diagram backdrop and save the reference in layout.json.
5. WHILE the main screen is open, THE Web_Visualizer SHALL refresh sensor data from current.json at a regular interval to display near-real-time values.

### Requirement 6: Детальный просмотр датчика

**User Story:** As an operator, I want to view detailed historical charts for a specific sensor, so that I can analyze temperature and humidity trends over time.

#### Acceptance Criteria

1. WHEN the operator navigates to /sensor/{id}, THE Web_Visualizer SHALL display temperature and humidity charts for the selected sensor.
2. THE Web_Visualizer SHALL support chart time scales: 1 hour, 6 hours, 24 hours, 7 days, 30 days, and custom date range.
3. WHEN the operator views a sensor detail page, THE Web_Visualizer SHALL display the sensor event log, current threshold settings, and a button to acknowledge active alarms.

### Requirement 7: План помещения

**User Story:** As an operator, I want to place sensors on a floor plan image, so that I can see their physical location and current readings in context.

#### Acceptance Criteria

1. THE Web_Visualizer SHALL provide a "Floor Plan" page (/floorplan) supporting multiple plans with a parent-child hierarchy (sub-plans).
2. WHEN the operator creates a new plan, THE Web_Visualizer SHALL generate a unique plan ID and store the plan metadata in floorplan_config.json.
3. WHEN the operator uploads a background image (PNG, JPG, BMP, WebP, or SVG), THE Web_Visualizer SHALL save the file to static/floorplans/ and associate it with the plan.
4. WHEN the operator drags a sensor marker onto the plan, THE Web_Visualizer SHALL save the sensor position (as percentage of canvas dimensions) in floorplan_config.json.
5. WHILE the floor plan page is open, THE Web_Visualizer SHALL display current temperature, humidity, and status color on each placed sensor marker, updated in near-real-time.
6. WHEN the operator deletes a plan, THE Web_Visualizer SHALL cascade-delete all child sub-plans and their associated data from floorplan_config.json.

### Requirement 8: Настройки оформления и темы

**User Story:** As an operator, I want to customize the application appearance (theme, colors, title), so that the interface matches my organization's preferences.

#### Acceptance Criteria

1. THE Web_Visualizer SHALL support two themes: dark (default) and light, switchable via a button in the navigation bar with immediate visual effect.
2. WHEN the operator changes theme colors on the /settings/appearance page, THE Web_Visualizer SHALL save the updated colors to theme_config.json for the selected theme independently.
3. THE Web_Visualizer SHALL allow the operator to set a custom application title (1–50 characters) displayed in the navigation bar and browser tab title.
4. WHEN the operator clicks "Reset to defaults" for a theme, THE Web_Visualizer SHALL restore all color values for that theme to the predefined defaults.

### Requirement 9: Уведомления по Email

**User Story:** As an operator, I want to receive email notifications when sensor values exceed thresholds, so that I can respond to abnormal conditions promptly.

#### Acceptance Criteria

1. WHEN a sensor value crosses a configured warning or alarm threshold, THE Web_Visualizer SHALL send an email notification to all configured recipients via the SMTP server defined in notifications.json.
2. THE Web_Visualizer SHALL support per-sensor notification settings (enable/disable email on warning, email on alarm) stored in system_config.json.
3. WHERE daily email reports are enabled, THE Web_Visualizer SHALL send a summary report at the configured time (default 08:00) containing temperature/humidity ranges and violation counts for each sensor.

### Requirement 10: Telegram Bot — уведомления и команды

**User Story:** As an operator, I want to receive Telegram notifications and query sensor data via bot commands, so that I can monitor the system remotely from my phone.

#### Acceptance Criteria

1. WHEN a sensor value crosses a configured warning or alarm threshold, THE Telegram_Bot SHALL send a notification message to all configured chat IDs, including sensor name, parameter, value, threshold, violation type, and timestamp.
2. WHEN a violation ends (value returns to normal), THE Telegram_Bot SHALL send a resolution message including duration and peak value.
3. WHEN the operator sends the /status command, THE Telegram_Bot SHALL reply with current values and statuses of all sensors.
4. WHEN the operator sends the /chart {sensor} {period} command, THE Telegram_Bot SHALL generate a PNG chart of temperature and humidity using matplotlib and send it as an image.
5. WHEN the operator sends the /mute {minutes} command, THE Telegram_Bot SHALL suppress all notifications for the specified duration, and the /unmute command SHALL resume notifications immediately.

### Requirement 11: Telegram Bot — регулярные отчёты

**User Story:** As an operator, I want to receive periodic summary reports via Telegram with charts, so that I stay informed about system trends without manual checks.

#### Acceptance Criteria

1. THE Telegram_Bot SHALL support four report schedules: hourly, daily, weekly, and monthly, each independently configurable (enabled/disabled, time, day of week/month) via notifications.json.
2. WHEN a scheduled report triggers, THE Telegram_Bot SHALL generate a text summary (temperature/humidity ranges, violation counts per sensor) and PNG charts for the report period, then send them to all configured chat IDs.
3. WHEN the operator sends the /schedule command, THE Telegram_Bot SHALL display the current report schedule configuration.
4. WHEN the operator sends /schedule {type} {on|off}, THE Telegram_Bot SHALL enable or disable the specified report type and persist the change to notifications.json.

### Requirement 12: Журналы и экспорт данных

**User Story:** As an operator, I want to view event logs, temperature journals, and violation records, and export data in CSV/JSON, so that I can perform analysis and generate compliance reports.

#### Acceptance Criteria

1. THE Web_Visualizer SHALL provide an events page (/events) displaying alarm and warning events with filtering by sensor, event type, and date range, and support acknowledgment of individual events.
2. THE Web_Visualizer SHALL provide a temperature journal page (/journal/temperatures) showing aggregated min/max/avg temperature and humidity per sensor for selectable periods (hour, day, week).
3. THE Web_Visualizer SHALL provide a violations journal page (/journal/violations) showing threshold violation records with start time, end time, duration, peak value, and acknowledgment status.
4. WHEN the operator requests data export (GET /api/archive/export), THE Archive_Manager SHALL generate a downloadable file in CSV or JSON format for the specified sensor(s) and date range.

### Requirement 13: Конфигурация и версионность

**User Story:** As an administrator, I want the system to version all configuration changes and maintain backups, so that I can audit changes and restore previous configurations if needed.

#### Acceptance Criteria

1. WHEN any configuration change is saved, THE KVT_System SHALL increment config_version, record the change description and timestamp in update_history, and create a backup file in data/config/backups/.
2. WHEN the administrator requests configuration history (GET /api/config/history), THE Web_Visualizer SHALL return the list of all recorded configuration changes with version, timestamp, and description.
3. WHEN the administrator requests a restore (POST /api/config/restore/{version}), THE Web_Visualizer SHALL replace the current system_config.json with the specified backup version and record the restore action in update_history.
4. THE KVT_System SHALL validate all configuration changes against defined rules (unique sensor IDs, unique Modbus addresses, valid ranges) before persisting them.

### Requirement 14: Автоматическая генерация и сохранение отчётов

**User Story:** As an operator, I want the system to automatically generate and save reports to disk on a configurable schedule, so that I have a local archive of reports for compliance and auditing without relying on Telegram or email.

#### Acceptance Criteria

1. THE Report_Generator SHALL support configurable report schedules: hourly, daily, weekly, and monthly, each independently enabled/disabled with configurable time and day parameters via report_config.json.
2. WHEN a scheduled report triggers, THE Report_Generator SHALL generate a report file containing a text summary (temperature/humidity ranges per sensor, violation counts, duration statistics) and embedded PNG charts for the report period.
3. THE Report_Generator SHALL support output formats: PDF, HTML, and CSV, configurable per schedule entry in report_config.json.
4. THE Report_Generator SHALL save generated reports to a configurable directory (default: data/reports/) with a naming convention including report type, period, and timestamp (e.g., daily_2026-01-14_080000.pdf).
5. WHILE the report storage directory exceeds the configured maximum size or file count, THE Report_Generator SHALL delete the oldest report files according to the configured retention policy (default: keep reports for 365 days).
6. WHEN the operator requests a manual report generation via the Web_Visualizer (/settings/reports) or REST API (POST /api/reports/generate), THE Report_Generator SHALL immediately generate a report for the specified period and format.

### Requirement 15: OPC UA сервер

**User Story:** As a system integrator, I want the KVT system to expose current and historical sensor data via OPC UA 2.0, so that external SCADA systems and other OPC UA clients can consume the data.

#### Acceptance Criteria

1. THE OPC_UA_Server SHALL expose an OPC UA address space containing a node for each configured sensor with variables for current temperature, current humidity, combined status, and last update timestamp.
2. WHILE the OPC_UA_Server is running, THE OPC_UA_Server SHALL update sensor variable values from current.json within 2 seconds of a new measurement.
3. THE OPC_UA_Server SHALL support OPC UA Historical Access (HA) interface, allowing clients to read archived temperature and humidity data for a specified sensor and time range from the Archive_Manager storage.
4. THE OPC_UA_Server SHALL support configurable endpoint URL, port (default: 4840), security policy (None, Basic256Sha256), and authentication mode (Anonymous, Username/Password) via opcua_config.json.
5. WHEN a new sensor is added or removed from system_config.json, THE OPC_UA_Server SHALL dynamically update the OPC UA address space to reflect the change within 5 seconds.

### Requirement 16: Docker-контейнеризация

**User Story:** As a system administrator, I want to deploy the KVT system using Docker containers, so that installation and updates are simple and reproducible.

#### Acceptance Criteria

1. THE KVT_System SHALL provide a Dockerfile and docker-compose.yml that build and run all four subsystems (Modbus_Poller, Archive_Manager, Web_Visualizer, Telegram_Bot) as separate containers.
2. THE KVT_System SHALL mount the data/ directory as a Docker volume shared between all containers for configuration and data exchange.
3. WHILE running in Docker, THE Modbus_Poller container SHALL have access to host serial ports (via privileged mode or device mapping) for RS-485 communication.
