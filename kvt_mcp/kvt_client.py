"""
Клиент для работы с API компонентов KVT-C.
"""
import json
from typing import Any, Dict, List, Optional
from datetime import datetime
import requests
from requests.exceptions import RequestException, Timeout, ConnectionError

from .config import KVTConfig


class KVTClientError(Exception):
    """Базовое исключение для ошибок клиента"""
    pass


class KVTClient:
    """Клиент для взаимодействия с компонентами KVT-C"""
    
    def __init__(self, config: KVTConfig):
        self.config = config
        self.session = requests.Session()
        self.session.timeout = config.request_timeout
    
    def _request(
        self, 
        method: str, 
        url: str, 
        **kwargs
    ) -> Dict[str, Any]:
        """Выполнить HTTP запрос"""
        try:
            response = self.session.request(
                method,
                url,
                timeout=self.config.request_timeout,
                **kwargs
            )
            response.raise_for_status()
            return response.json() if response.text else {}
        except ConnectionError as e:
            raise KVTClientError(f"Ошибка подключения: {e}")
        except Timeout as e:
            raise KVTClientError(f"Таймаут подключения: {e}")
        except RequestException as e:
            raise KVTClientError(f"Ошибка запроса: {e}")
        except json.JSONDecodeError as e:
            raise KVTClientError(f"Ошибка парсинга JSON: {e}")
    
    # ===== Visualizer API =====
    
    def get_system_config(self) -> Dict[str, Any]:
        """Получить конфигурацию системы"""
        return self._request("GET", f"{self.config.visualizer_url}/api/config/system")
    
    def get_current_data(self) -> Dict[str, Any]:
        """Получить текущие значения всех датчиков"""
        return self._request("GET", f"{self.config.visualizer_url}/api/current")
    
    def get_sensor(self, sensor_name: str) -> Dict[str, Any]:
        """Получить информацию о конкретном датчике"""
        return self._request("GET", f"{self.config.visualizer_url}/api/sensor/{sensor_name}")
    
    def update_sensor(self, sensor_name: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Обновить конфигурацию датчика"""
        return self._request(
            "PUT",
            f"{self.config.visualizer_url}/api/sensor/{sensor_name}",
            json=data
        )
    
    def get_status(self) -> Dict[str, Any]:
        """Получить статус системы"""
        return self._request("GET", f"{self.config.visualizer_url}/api/status")
    
    # ===== Poller API =====
    
    def get_poller_status(self) -> Dict[str, Any]:
        """Получить статус poller"""
        return self._request("GET", f"{self.config.poller_url}/api/status")
    
    def get_modbus_log(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Получить лог Modbus обменов"""
        return self._request(
            "GET",
            f"{self.config.poller_url}/api/modbus/log",
            params={"limit": limit}
        )
    
    def get_poller_stats(self) -> Dict[str, Any]:
        """Получить статистику poller"""
        return self._request("GET", f"{self.config.poller_url}/api/stats")
    
    # ===== Archiver API =====
    
    def get_archive_status(self) -> Dict[str, Any]:
        """Получить статус archiver"""
        return self._request("GET", f"{self.config.archiver_url}/api/status")
    
    def get_archive_data(
        self,
        sensor_name: str,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """
        Получить исторические данные датчика
        
        Args:
            sensor_name: Имя датчика
            start_time: Начальное время (ISO 8601)
            end_time: Конечное время (ISO 8601)
            limit: Максимум записей
        """
        params = {"sensor": sensor_name, "limit": limit}
        if start_time:
            params["start"] = start_time
        if end_time:
            params["end"] = end_time
        
        return self._request(
            "GET",
            f"{self.config.archiver_url}/api/archive",
            params=params
        )
    
    def get_daily_archive(
        self,
        sensor_name: str,
        date: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Получить дневной архив датчика"""
        params = {"sensor": sensor_name}
        if date:
            params["date"] = date
        
        return self._request(
            "GET",
            f"{self.config.archiver_url}/api/archive/daily",
            params=params
        )
    
    def get_availability(self) -> Dict[str, Any]:
        """Получить информацию о доступности датчиков"""
        return self._request("GET", f"{self.config.archiver_url}/api/availability")
    
    # ===== Multi-component queries =====
    
    def get_system_health(self) -> Dict[str, Any]:
        """Получить общее состояние здоровья системы"""
        health = {
            "timestamp": datetime.utcnow().isoformat(),
            "components": {}
        }
        
        try:
            health["components"]["visualizer"] = {
                "status": "ok",
                "data": self.get_status()
            }
        except KVTClientError as e:
            health["components"]["visualizer"] = {"status": "error", "error": str(e)}
        
        try:
            health["components"]["poller"] = {
                "status": "ok",
                "data": self.get_poller_status()
            }
        except KVTClientError as e:
            health["components"]["poller"] = {"status": "error", "error": str(e)}
        
        try:
            health["components"]["archiver"] = {
                "status": "ok",
                "data": self.get_archive_status()
            }
        except KVTClientError as e:
            health["components"]["archiver"] = {"status": "error", "error": str(e)}
        
        return health
    
    def get_all_sensors_current(self) -> Dict[str, Dict[str, Any]]:
        """Получить текущие значения всех датчиков с метаданными"""
        return self.get_current_data()
    
    def search_sensors_by_name(self, name_pattern: str) -> List[Dict[str, Any]]:
        """Найти датчики по названию (частичное совпадение)"""
        try:
            config = self.get_system_config()
            sensors = config.get("sensors", [])
            pattern_lower = name_pattern.lower()
            return [
                s for s in sensors 
                if pattern_lower in s.get("name", "").lower()
            ]
        except KVTClientError:
            return []
