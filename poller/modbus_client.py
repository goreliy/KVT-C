from typing import List
from pymodbus.client.sync import ModbusSerialClient


class ModbusClient:
    def __init__(self, config):
        self.config = config
        self.client = None

    def connect(self) -> bool:
        self.close()
        self.client = ModbusSerialClient(
            method="rtu",
            port=self.config["com_port"],
            baudrate=int(self.config["baudrate"]),
            bytesize=int(self.config["bytesize"]),
            parity=str(self.config["parity"]),
            stopbits=int(self.config["stopbits"]),
            timeout=float(self.config["timeout_ms"]) / 1000.0,
        )
        return bool(self.client.connect())

    def close(self):
        if self.client:
            try:
                self.client.close()
            except Exception:
                pass
        self.client = None

    def read_input_registers(self, slave_id: int, start_addr: int, count: int) -> List[int]:
        if not self.client:
            raise RuntimeError("Modbus client not connected")
        response = self.client.read_input_registers(address=start_addr, count=count, unit=slave_id)
        if not response or response.isError():
            raise RuntimeError(f"Modbus read error: slave={slave_id}, addr={start_addr}, count={count}")
        return list(response.registers)

    def read_holding_registers(self, slave_id: int, start_addr: int, count: int) -> List[int]:
        if not self.client:
            raise RuntimeError("Modbus client not connected")
        response = self.client.read_holding_registers(address=start_addr, count=count, unit=slave_id)
        if not response or response.isError():
            raise RuntimeError(f"Modbus read error: slave={slave_id}, addr={start_addr}, count={count}")
        return list(response.registers)
