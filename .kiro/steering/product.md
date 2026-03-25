# КВТ — Temperature & Humidity Monitoring System

КВТ (Комплект контроля температуры и влажности) is a monitoring system for temperature and humidity using Bolid С2000-ВТ sensors over Modbus RTU (RS-485).

## Purpose
- Automatic polling of С2000-ВТ / С2000-ВТИ sensors via a С2000-ПП interface converter
- Real-time data visualization through a web dashboard with interactive floor plans
- Archiving with data compression (collapsing identical consecutive readings)
- Alerting via Email and Telegram when thresholds are exceeded
- Event journal with acknowledgement workflow

## Target Users
- Facility operators monitoring storage environments (warehouses, cold rooms)
- System runs on ARM v7 controllers or x86_64 PCs, containerized with Docker

## Primary Language
- The project documentation, UI, commit messages, and code comments are in Russian
- Variable names and code identifiers are in English
