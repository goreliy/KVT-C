import time
import socket
from dataclasses import dataclass
from typing import List

import serial


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def hex_bytes(payload: bytes) -> str:
    return " ".join(f"{byte:02X}" for byte in payload)


@dataclass
class ModbusExchange:
    registers: List[int]
    tx_frame: bytes
    rx_frame: bytes
    response_time_ms: float

    @property
    def tx_hex(self) -> str:
        return hex_bytes(self.tx_frame)

    @property
    def rx_hex(self) -> str:
        return hex_bytes(self.rx_frame)


class ModbusError(RuntimeError):
    def __init__(self, message: str, tx_frame: bytes = b"", rx_frame: bytes = b"", response_time_ms=None):
        super().__init__(message)
        self.tx_frame = tx_frame
        self.rx_frame = rx_frame
        self.response_time_ms = response_time_ms

    @property
    def tx_hex(self) -> str:
        return hex_bytes(self.tx_frame)

    @property
    def rx_hex(self) -> str:
        return hex_bytes(self.rx_frame)


class ModbusClient:
    def __init__(self, config):
        self.config = config
        self.client = None
        self.transport = str(self.config.get("transport", "serial")).lower()

    def connect(self) -> bool:
        self.close()
        self.transport = str(self.config.get("transport", "serial")).lower()
        if self.transport == "udp":
            try:
                self.client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.client.settimeout(float(self.config["timeout_ms"]) / 1000.0)
                return True
            except OSError:
                self.client = None
                return False

        try:
            self.client = serial.Serial(
                port=self.config["com_port"],
                baudrate=int(self.config["baudrate"]),
                bytesize=int(self.config["bytesize"]),
                parity=str(self.config["parity"]),
                stopbits=int(self.config["stopbits"]),
                timeout=float(self.config["timeout_ms"]) / 1000.0,
                write_timeout=float(self.config["timeout_ms"]) / 1000.0,
            )
            return bool(self.client.is_open)
        except serial.SerialException:
            self.client = None
            return False

    def close(self):
        if self.client:
            try:
                self.client.close()
            except Exception:
                pass
        self.client = None

    def read_holding_registers_raw(self, slave_id: int, start_addr: int, count: int) -> ModbusExchange:
        return self._read_registers(function=0x03, slave_id=slave_id, start_addr=start_addr, count=count)

    def read_holding_registers(self, slave_id: int, start_addr: int, count: int) -> List[int]:
        return self.read_holding_registers_raw(slave_id, start_addr, count).registers

    def read_input_registers(self, slave_id: int, start_addr: int, count: int) -> List[int]:
        return self._read_registers(function=0x04, slave_id=slave_id, start_addr=start_addr, count=count).registers

    def _read_registers(self, function: int, slave_id: int, start_addr: int, count: int) -> ModbusExchange:
        if not self.client:
            raise ModbusError("Modbus client not connected")
        if not (1 <= slave_id <= 247):
            raise ModbusError(f"Invalid slave id: {slave_id}")
        if not (0 <= start_addr <= 0xFFFF):
            raise ModbusError(f"Invalid register address: {start_addr}")
        if not (1 <= count <= 125):
            raise ModbusError(f"Invalid register count: {count}")

        request = bytes([
            slave_id,
            function,
            (start_addr >> 8) & 0xFF,
            start_addr & 0xFF,
            (count >> 8) & 0xFF,
            count & 0xFF,
        ])
        crc = crc16_modbus(request)
        tx_frame = request + bytes([crc & 0xFF, (crc >> 8) & 0xFF])

        try:
            if self.transport == "serial":
                self.client.reset_input_buffer()
        except Exception:
            pass

        started = time.perf_counter()
        try:
            if self.transport == "udp":
                host = str(self.config.get("udp_host", "127.0.0.1")).strip()
                port = int(self.config.get("udp_port", 502))
                self.client.sendto(tx_frame, (host, port))
                rx_frame, _ = self.client.recvfrom(2048)
                elapsed = round((time.perf_counter() - started) * 1000, 2)
            else:
                self.client.write(tx_frame)
                self.client.flush()
                header = self.client.read(3)
                if len(header) < 3:
                    elapsed = round((time.perf_counter() - started) * 1000, 2)
                    raise ModbusError(
                        f"No response: slave={slave_id}, function={function}, addr={start_addr}, count={count}",
                        tx_frame=tx_frame,
                        rx_frame=header,
                        response_time_ms=elapsed,
                    )

                if header[1] & 0x80:
                    tail = self.client.read(2)
                    rx_frame = header + tail
                    elapsed = round((time.perf_counter() - started) * 1000, 2)
                    raise ModbusError(
                        f"Modbus exception: slave={slave_id}, function={function}, code={header[2]}",
                        tx_frame=tx_frame,
                        rx_frame=rx_frame,
                        response_time_ms=elapsed,
                    )

                byte_count = header[2]
                tail = self.client.read(byte_count + 2)
                rx_frame = header + tail
                elapsed = round((time.perf_counter() - started) * 1000, 2)

            if len(rx_frame) < 5:
                raise ModbusError(
                    f"Incomplete response: got={len(rx_frame)}, expected>=5",
                    tx_frame=tx_frame,
                    rx_frame=rx_frame,
                    response_time_ms=elapsed,
                )

            if rx_frame[1] & 0x80:
                raise ModbusError(
                    f"Modbus exception: slave={slave_id}, function={function}, code={rx_frame[2]}",
                    tx_frame=tx_frame,
                    rx_frame=rx_frame,
                    response_time_ms=elapsed,
                )

            byte_count = rx_frame[2]
            expected_len = 3 + byte_count + 2
            if len(rx_frame) != expected_len:
                raise ModbusError(
                    f"Incomplete response: got={len(rx_frame)}, expected={expected_len}",
                    tx_frame=tx_frame,
                    rx_frame=rx_frame,
                    response_time_ms=elapsed,
                )
            if rx_frame[0] != slave_id or rx_frame[1] != function:
                raise ModbusError(
                    f"Unexpected response header: {hex_bytes(rx_frame[:2])}",
                    tx_frame=tx_frame,
                    rx_frame=rx_frame,
                    response_time_ms=elapsed,
                )
            crc_received = rx_frame[-2] | (rx_frame[-1] << 8)
            crc_calculated = crc16_modbus(rx_frame[:-2])
            if crc_received != crc_calculated:
                raise ModbusError(
                    f"CRC error: got={crc_received:04X}, expected={crc_calculated:04X}",
                    tx_frame=tx_frame,
                    rx_frame=rx_frame,
                    response_time_ms=elapsed,
                )
            if byte_count != count * 2:
                raise ModbusError(
                    f"Unexpected byte count: got={byte_count}, expected={count * 2}",
                    tx_frame=tx_frame,
                    rx_frame=rx_frame,
                    response_time_ms=elapsed,
                )

            registers = []
            payload = rx_frame[3:-2]
            for index in range(0, len(payload), 2):
                registers.append((payload[index] << 8) | payload[index + 1])
            return ModbusExchange(
                registers=registers,
                tx_frame=tx_frame,
                rx_frame=rx_frame,
                response_time_ms=elapsed,
            )
        except (serial.SerialException, socket.timeout, OSError) as exc:
            elapsed = round((time.perf_counter() - started) * 1000, 2)
            raise ModbusError(str(exc), tx_frame=tx_frame, response_time_ms=elapsed) from exc
