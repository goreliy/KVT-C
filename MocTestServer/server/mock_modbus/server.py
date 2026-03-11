"""
Modbus RTU Server - эмуляция Modbus RTU устройств через TCP
"""

import logging
import threading
import random
import time
from datetime import datetime
from typing import Dict, Any, Optional, List
from collections import deque

"""from pymodbus.server import StartTcpServer, ServerStop"""
from pymodbus.server.sync import StartTcpServer, ServerStop
from pymodbus.datastore import ModbusSlaveContext, ModbusServerContext
from pymodbus.datastore.store import ModbusSequentialDataBlock

from .registers import VirtualRegisters
from .generator import RegisterGenerator
from ..utils import merge_config

logger = logging.getLogger(__name__)


class ModbusRequestLog:
    """Лог Modbus запросов и ответов"""

    def __init__(self, max_entries: int = 1000):
        self.max_entries = max_entries
        self._entries: deque = deque(maxlen=max_entries)
        self._lock = threading.Lock()
        self._pending_requests: Dict[str, float] = {}

    def _fake_crc(self) -> str:
        return f"{random.randint(0, 255):02X} {random.randint(0, 255):02X}"

    def _describe_register_type(self, address: int):
        """Определить тип регистра и номер датчика по адресу"""
        if address >= 40000:
            return "статусов", ((address - 40000) // 2) + 1
        if address >= 30000:
            return "значений", ((address - 30000) // 2) + 1
        return "регистров", (address // 2) + 1

    def log_request(self, slave_id: int, function: int, address: int, count: int) -> str:
        request_id = f"{time.time_ns()}"
        reg_type, sensor_num = self._describe_register_type(address)

        raw_hex = (f"{slave_id:02X} {function:02X} "
                   f"{(address >> 8) & 0xFF:02X} {address & 0xFF:02X} "
                   f"{(count >> 8) & 0xFF:02X} {count & 0xFF:02X} "
                   f"{self._fake_crc()}")

        entry = {
            "request_id": request_id,
            "timestamp": datetime.now().isoformat(),
            "direction": "TX",
            "raw_hex": raw_hex,
            "parsed": {
                "slave_id": slave_id, "function": function,
                "start_addr": address, "quantity": count,
                "description": f"Запрос {reg_type} датчика {sensor_num}"
            },
            "response_time_ms": None
        }

        with self._lock:
            self._entries.append(entry)
            self._pending_requests[request_id] = time.perf_counter()

        return request_id

    def log_response(self, request_id: str, slave_id: int, function: int,
                     values: List[int], address: int):
        response_time_ms = self._pop_pending(request_id)

        byte_count = len(values) * 2
        values_hex = " ".join(f"{(v >> 8) & 0xFF:02X} {v & 0xFF:02X}" for v in values)
        raw_hex = (f"{slave_id:02X} {function:02X} {byte_count:02X} "
                   f"{values_hex} {self._fake_crc()}")

        description = self._describe_response(address, values)

        entry = {
            "timestamp": datetime.now().isoformat(),
            "direction": "RX",
            "raw_hex": raw_hex,
            "parsed": {
                "slave_id": slave_id, "function": function,
                "byte_count": byte_count, "values": values,
                "description": description
            },
            "response_time_ms": response_time_ms
        }

        with self._lock:
            self._entries.append(entry)

    def _describe_response(self, address: int, values: List[int]) -> str:
        if address >= 40000:
            statuses = ["OK" if v == 0 else f"ERR:{v}" for v in values]
            return f"Ответ: статусы [{', '.join(statuses)}]"
        if address >= 30000:
            parts = []
            for i in range(0, len(values), 2):
                temp_raw = values[i] if values[i] < 32768 else values[i] - 65536
                parts.append(f"T={temp_raw / 10.0}°C")
                if i + 1 < len(values):
                    parts.append(f"H={values[i + 1] / 10.0}%")
            return f"Ответ: {', '.join(parts)}"
        return f"Ответ: {values}"

    def log_error(self, request_id: str, slave_id: int, error_type: str, description: str):
        response_time_ms = self._pop_pending(request_id)
        entry = {
            "timestamp": datetime.now().isoformat(),
            "direction": "RX",
            "raw_hex": None,
            "parsed": {"slave_id": slave_id, "error": error_type, "description": description},
            "response_time_ms": response_time_ms
        }
        with self._lock:
            self._entries.append(entry)

    def _pop_pending(self, request_id: str) -> Optional[float]:
        with self._lock:
            start_time = self._pending_requests.pop(request_id, None)
        if start_time is not None:
            return round((time.perf_counter() - start_time) * 1000, 2)
        return None

    def get_entries(self, limit: int = 100) -> List[Dict]:
        with self._lock:
            entries = list(self._entries)
        return entries[-limit:]

    def get_statistics(self) -> Dict:
        with self._lock:
            entries = list(self._entries)

        tx_count = rx_count = error_count = 0
        response_times = []
        for e in entries:
            if e["direction"] == "TX":
                tx_count += 1
            else:
                rx_count += 1
                if e.get("parsed", {}).get("error"):
                    error_count += 1
            if e["response_time_ms"] is not None:
                response_times.append(e["response_time_ms"])

        return {
            "total_entries": len(entries),
            "tx_count": tx_count, "rx_count": rx_count,
            "error_count": error_count,
            "avg_response_time_ms": round(sum(response_times) / len(response_times), 2) if response_times else 0,
            "min_response_time_ms": min(response_times) if response_times else 0,
            "max_response_time_ms": max(response_times) if response_times else 0
        }

    def clear(self):
        with self._lock:
            self._entries.clear()
            self._pending_requests.clear()


class LoggingDataBlock(ModbusSequentialDataBlock):
    """Кастомный блок данных с логированием запросов"""

    def __init__(self, registers: VirtualRegisters, base_address: int,
                 request_log: ModbusRequestLog, unit_id: int, is_status: bool = False):
        self.virtual_registers = registers
        self.base_address = base_address
        self.request_log = request_log
        self.unit_id = unit_id
        self.is_status = is_status
        super().__init__(0, [0] * 65536)

    def getValues(self, address, count=1):
        actual_address = self.base_address + address
        func_code = 3 if self.is_status else 4

        request_id = self.request_log.log_request(
            slave_id=self.unit_id, function=func_code,
            address=actual_address, count=count
        )

        # Имитация задержки ответа (5-30 мс)
        time.sleep(random.uniform(5, 30) / 1000.0)

        result = [self.virtual_registers.get_register(actual_address + i) for i in range(count)]

        self.request_log.log_response(
            request_id=request_id, slave_id=self.unit_id,
            function=func_code, values=result, address=actual_address
        )
        return result

    def setValues(self, address, values):
        for i, val in enumerate(values):
            self.virtual_registers.set_register(self.base_address + address + i, val)


class ModbusServer:
    """Modbus RTU Server (эмулирует через TCP)"""

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or self._default_config()
        self._registers: Optional[VirtualRegisters] = None
        self._generator: Optional[RegisterGenerator] = None
        self._request_log: Optional[ModbusRequestLog] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._server_context = None
        self._init_components()

    @staticmethod
    def _default_config() -> Dict[str, Any]:
        return {
            "server": {"port": 5020, "unit_id": 16, "enabled": True},
            "sensors": {"count": 10, "value_register_base": 30000, "status_register_base": 40000},
            "generation": {"update_interval_ms": 1000, "scenario": "normal"},
            "values": {
                "temperature": {"min": -40.0, "max": 85.0, "base": 22.0, "variation": 2.0},
                "humidity": {"min": 0.0, "max": 100.0, "base": 45.0, "variation": 5.0}
            },
            "errors": {
                "error_rate": 0.0, "timeout_rate": 0.0,
                "crc_error_rate": 0.0, "offline_sensors": []
            },
            "log": {"max_entries": 1000},
            "per_sensor_overrides": {}
        }

    def _init_components(self):
        sensors_cfg = self.config["sensors"]
        log_cfg = self.config.get("log", {})

        self._request_log = ModbusRequestLog(max_entries=log_cfg.get("max_entries", 1000))
        self._registers = VirtualRegisters(
            value_base=sensors_cfg["value_register_base"],
            status_base=sensors_cfg["status_register_base"],
            sensor_count=sensors_cfg["count"]
        )
        self._generator = RegisterGenerator(self._registers, {
            "update_interval_ms": self.config["generation"]["update_interval_ms"],
            "scenario": self.config["generation"]["scenario"],
            "values": self.config["values"],
            "errors": self.config["errors"],
            "per_sensor_overrides": self.config.get("per_sensor_overrides", {})
        })

    def _create_server_context(self):
        sensors_cfg = self.config["sensors"]
        unit_id = self.config["server"]["unit_id"]

        ir_block = LoggingDataBlock(
            self._registers, sensors_cfg["value_register_base"],
            self._request_log, unit_id, is_status=False
        )
        hr_block = LoggingDataBlock(
            self._registers, sensors_cfg["status_register_base"],
            self._request_log, unit_id, is_status=True
        )

        slave_context = ModbusSlaveContext(
            di=ModbusSequentialDataBlock(0, [0] * 100),
            co=ModbusSequentialDataBlock(0, [0] * 100),
            hr=hr_block, ir=ir_block
        )
        self._server_context = ModbusServerContext(
            slaves={unit_id: slave_context}, single=False
        )
        return self._server_context

    def _run_server(self):
        port = self.config["server"]["port"]
        logger.info(f"Starting Modbus TCP server on port {port}")
        try:
            context = self._create_server_context()
            StartTcpServer(context=context, address=("0.0.0.0", port))
        except Exception as e:
            logger.error(f"Modbus server error: {e}")
            self._running = False

    def start(self):
        if self._running:
            return
        self._running = True
        self._generator.start()
        self._thread = threading.Thread(target=self._run_server, daemon=True)
        self._thread.start()
        logger.info("Modbus server started")

    def stop(self):
        self._running = False
        self._generator.stop()
        try:
            ServerStop()
        except Exception:
            pass
        logger.info("Modbus server stopped")

    def get_status(self) -> Dict[str, Any]:
        log_stats = self._request_log.get_statistics() if self._request_log else {}
        return {
            "running": self._running,
            "port": self.config["server"]["port"],
            "unit_id": self.config["server"]["unit_id"],
            "sensor_count": self.config["sensors"]["count"],
            "scenario": self.config["generation"]["scenario"],
            "update_interval_ms": self.config["generation"]["update_interval_ms"],
            "log_statistics": log_stats
        }

    def get_registers(self) -> Dict:
        return self._registers.get_all_values()

    def get_request_log(self, limit: int = 100) -> Dict:
        if not self._request_log:
            return {"max_entries": 0, "entries": [], "statistics": {}}
        return {
            "max_entries": self._request_log.max_entries,
            "entries": self._request_log.get_entries(limit),
            "statistics": self._request_log.get_statistics()
        }

    def clear_request_log(self):
        if self._request_log:
            self._request_log.clear()

    def update_config(self, new_config: Dict[str, Any]):
        was_running = self._running
        if was_running:
            self.stop()
        merge_config(self.config, new_config)
        self._init_components()
        if was_running:
            self.start()

    def set_scenario(self, scenario_name: str):
        self.config["generation"]["scenario"] = scenario_name
        self._generator.set_scenario(scenario_name)

    def set_value(self, address: int, value: int):
        self._registers.set_register(address, value)
