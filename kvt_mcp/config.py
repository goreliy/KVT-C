"""
Конфигурация MCP сервера для подключения к компонентам KVT-C.
"""
import os
from dataclasses import dataclass
from typing import Optional

@dataclass
class KVTConfig:
    """Конфигурация подключения к компонентам KVT-C"""
    
    # Основной адрес системы KVT-C
    kvt_host: str = "127.0.0.1"
    
    # Адреса IP и порты компонентов (переопределяют kvt_host если указаны)
    visualizer_host: str = "127.0.0.1"
    visualizer_port: int = 5000
    
    poller_host: str = "127.0.0.1"
    poller_port: int = 5001
    
    archiver_host: str = "127.0.0.1"
    archiver_port: int = 5002
    
    opcua_host: str = "127.0.0.1"
    opcua_port: int = 4840
    
    mqtt_host: str = "127.0.0.1"
    mqtt_port: int = 1883
    mqtt_username: Optional[str] = None
    mqtt_password: Optional[str] = None
    
    # Таймауты
    request_timeout: int = 10  # секунд
    connection_timeout: int = 5  # секунд
    
    # Пути к данным
    data_dir: str = "./data"
    config_dir: str = "./data/config"
    
    @classmethod
    def from_env(cls) -> "KVTConfig":
        """Загрузить конфигурацию из переменных окружения"""
        # Получить основной IP адрес KVT-C (используется как fallback для компонентов)
        default_host = os.getenv("KVT_HOST", "127.0.0.1")
        
        return cls(
            kvt_host=default_host,
            visualizer_host=os.getenv("KVT_VISUALIZER_HOST", default_host),
            visualizer_port=int(os.getenv("KVT_VISUALIZER_PORT", "5000")),
            poller_host=os.getenv("KVT_POLLER_HOST", default_host),
            poller_port=int(os.getenv("KVT_POLLER_PORT", "5001")),
            archiver_host=os.getenv("KVT_ARCHIVER_HOST", default_host),
            archiver_port=int(os.getenv("KVT_ARCHIVER_PORT", "5002")),
            opcua_host=os.getenv("KVT_OPCUA_HOST", default_host),
            opcua_port=int(os.getenv("KVT_OPCUA_PORT", "4840")),
            mqtt_host=os.getenv("KVT_MQTT_HOST", default_host),
            mqtt_port=int(os.getenv("KVT_MQTT_PORT", "1883")),
            mqtt_username=os.getenv("KVT_MQTT_USERNAME"),
            mqtt_password=os.getenv("KVT_MQTT_PASSWORD"),
            request_timeout=int(os.getenv("KVT_REQUEST_TIMEOUT", "10")),
            connection_timeout=int(os.getenv("KVT_CONNECTION_TIMEOUT", "5")),
            data_dir=os.getenv("KVT_DATA_DIR", "./data"),
            config_dir=os.getenv("KVT_CONFIG_DIR", "./data/config"),
        )
    
    @property
    def visualizer_url(self) -> str:
        return f"http://{self.visualizer_host}:{self.visualizer_port}"
    
    @property
    def poller_url(self) -> str:
        return f"http://{self.poller_host}:{self.poller_port}"
    
    @property
    def archiver_url(self) -> str:
        return f"http://{self.archiver_host}:{self.archiver_port}"
    
    @property
    def opcua_url(self) -> str:
        return f"opc.tcp://{self.opcua_host}:{self.opcua_port}"


# Глобальный экземпляр конфигурации
config = KVTConfig.from_env()
