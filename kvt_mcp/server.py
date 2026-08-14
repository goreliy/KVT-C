#!/usr/bin/env python3
"""
MCP сервер для системы мониторинга KVT-C.
Предоставляет инструменты для работы с датчиками, архивом, конфигурацией и статусом.
"""
import json
from datetime import datetime
from typing import Any

from mcp.server import Server
from mcp.types import (
    Tool,
    TextContent,
    ToolResponse,
)
import mcp.server.stdio

from .config import KVTConfig
from .kvt_client import KVTClient, KVTClientError


def create_mcp_server() -> Server:
    """Создать и настроить MCP сервер"""
    server = Server("kvt-mcp")
    config = KVTConfig.from_env()
    client = KVTClient(config)
    
    # ===== Инструменты для работы с текущими данными =====
    
    @server.list_tools()
    async def list_tools() -> list[Tool]:
        """Получить список всех доступных инструментов"""
        return [
            Tool(
                name="get_current_data",
                description="Получить текущие значения всех датчиков системы KVT-C",
                inputSchema={
                    "type": "object",
                    "properties": {},
                },
            ),
            Tool(
                name="get_sensor_info",
                description="Получить информацию о конкретном датчике",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "sensor_name": {
                            "type": "string",
                            "description": "Имя датчика"
                        }
                    },
                    "required": ["sensor_name"],
                },
            ),
            Tool(
                name="get_archive_data",
                description="Получить исторические данные датчика из архива",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "sensor_name": {
                            "type": "string",
                            "description": "Имя датчика"
                        },
                        "start_time": {
                            "type": "string",
                            "description": "Начальное время (ISO 8601, опционально)"
                        },
                        "end_time": {
                            "type": "string",
                            "description": "Конечное время (ISO 8601, опционально)"
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Максимум записей (по умолчанию 1000)"
                        }
                    },
                    "required": ["sensor_name"],
                },
            ),
            Tool(
                name="get_daily_archive",
                description="Получить дневной архив датчика",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "sensor_name": {
                            "type": "string",
                            "description": "Имя датчика"
                        },
                        "date": {
                            "type": "string",
                            "description": "Дата в формате YYYY-MM-DD (опционально, по умолчанию сегодня)"
                        }
                    },
                    "required": ["sensor_name"],
                },
            ),
            Tool(
                name="search_sensors",
                description="Найти датчики по названию (частичное совпадение)",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name_pattern": {
                            "type": "string",
                            "description": "Часть названия датчика для поиска"
                        }
                    },
                    "required": ["name_pattern"],
                },
            ),
            Tool(
                name="get_system_config",
                description="Получить конфигурацию системы (датчики, параметры)",
                inputSchema={
                    "type": "object",
                    "properties": {},
                },
            ),
            Tool(
                name="get_system_health",
                description="Получить общее состояние здоровья системы и её компонентов",
                inputSchema={
                    "type": "object",
                    "properties": {},
                },
            ),
            Tool(
                name="get_poller_status",
                description="Получить статус поллера (опрос Modbus)",
                inputSchema={
                    "type": "object",
                    "properties": {},
                },
            ),
            Tool(
                name="get_modbus_log",
                description="Получить лог обменов по Modbus",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Количество последних записей (по умолчанию 100)"
                        }
                    },
                },
            ),
            Tool(
                name="get_archiver_status",
                description="Получить статус архивера",
                inputSchema={
                    "type": "object",
                    "properties": {},
                },
            ),
            Tool(
                name="get_sensor_availability",
                description="Получить информацию о доступности датчиков",
                inputSchema={
                    "type": "object",
                    "properties": {},
                },
            ),
            Tool(
                name="get_mcp_config",
                description="Получить текущую конфигурацию MCP сервера (IP адреса и порты)",
                inputSchema={
                    "type": "object",
                    "properties": {},
                },
            ),
        ]
    
    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> ToolResponse:
        """Выполнить инструмент"""
        try:
            result = None
            
            if name == "get_current_data":
                result = client.get_current_data()
            
            elif name == "get_sensor_info":
                sensor_name = arguments.get("sensor_name")
                if not sensor_name:
                    raise ValueError("sensor_name требуется")
                result = client.get_sensor(sensor_name)
            
            elif name == "get_archive_data":
                sensor_name = arguments.get("sensor_name")
                if not sensor_name:
                    raise ValueError("sensor_name требуется")
                result = client.get_archive_data(
                    sensor_name,
                    start_time=arguments.get("start_time"),
                    end_time=arguments.get("end_time"),
                    limit=arguments.get("limit", 1000)
                )
            
            elif name == "get_daily_archive":
                sensor_name = arguments.get("sensor_name")
                if not sensor_name:
                    raise ValueError("sensor_name требуется")
                result = client.get_daily_archive(
                    sensor_name,
                    date=arguments.get("date")
                )
            
            elif name == "search_sensors":
                name_pattern = arguments.get("name_pattern")
                if not name_pattern:
                    raise ValueError("name_pattern требуется")
                result = client.search_sensors_by_name(name_pattern)
            
            elif name == "get_system_config":
                result = client.get_system_config()
            
            elif name == "get_system_health":
                result = client.get_system_health()
            
            elif name == "get_poller_status":
                result = client.get_poller_status()
            
            elif name == "get_modbus_log":
                limit = arguments.get("limit", 100)
                result = client.get_modbus_log(limit=limit)
            
            elif name == "get_archiver_status":
                result = client.get_archive_status()
            
            elif name == "get_sensor_availability":
                result = client.get_availability()
            
            elif name == "get_mcp_config":
                result = {
                    "kvt": {
                        "host": config.kvt_host,
                        "description": "Основной IP адрес системы KVT-C"
                    },
                    "visualizer": {
                        "host": config.visualizer_host,
                        "port": config.visualizer_port,
                        "url": config.visualizer_url
                    },
                    "poller": {
                        "host": config.poller_host,
                        "port": config.poller_port,
                        "url": config.poller_url
                    },
                    "archiver": {
                        "host": config.archiver_host,
                        "port": config.archiver_port,
                        "url": config.archiver_url
                    },
                    "opcua": {
                        "host": config.opcua_host,
                        "port": config.opcua_port,
                        "url": config.opcua_url
                    },
                    "mqtt": {
                        "host": config.mqtt_host,
                        "port": config.mqtt_port,
                        "has_credentials": config.mqtt_username is not None
                    },
                    "timeouts": {
                        "request_timeout": config.request_timeout,
                        "connection_timeout": config.connection_timeout
                    }
                }
            
            else:
                raise ValueError(f"Неизвестный инструмент: {name}")
            
            return ToolResponse(
                content=[TextContent(
                    type="text",
                    text=json.dumps(result, ensure_ascii=False, indent=2, default=str)
                )],
                is_error=False,
            )
        
        except KVTClientError as e:
            return ToolResponse(
                content=[TextContent(
                    type="text",
                    text=f"Ошибка подключения к системе KVT: {e}"
                )],
                is_error=True,
            )
        except Exception as e:
            return ToolResponse(
                content=[TextContent(
                    type="text",
                    text=f"Ошибка: {e}"
                )],
                is_error=True,
            )
    
    return server


async def main():
    """Запустить MCP сервер"""
    server = create_mcp_server()
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, None)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
