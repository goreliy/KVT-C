import socket
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List

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


def _int_value(value, default=0) -> int:
    try:
        return int(str(value), 0)
    except (TypeError, ValueError):
        return default


@dataclass
class ModbusExchange:
    registers: List[int]
    tx_frame: bytes
    rx_frame: bytes
    response_time_ms: float
    tx_transport_frame: bytes = b""
    rx_transport_frame: bytes = b""
    transport_meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def tx_hex(self) -> str:
        return hex_bytes(self.tx_frame)

    @property
    def rx_hex(self) -> str:
        return hex_bytes(self.rx_frame)

    @property
    def tx_transport_hex(self) -> str:
        return hex_bytes(self.tx_transport_frame or self.tx_frame)

    @property
    def rx_transport_hex(self) -> str:
        return hex_bytes(self.rx_transport_frame or self.rx_frame)


class ModbusError(RuntimeError):
    def __init__(
        self,
        message: str,
        tx_frame: bytes = b"",
        rx_frame: bytes = b"",
        response_time_ms=None,
        tx_transport_frame: bytes = b"",
        rx_transport_frame: bytes = b"",
        transport_meta: Dict[str, Any] = None,
    ):
        super().__init__(message)
        self.tx_frame = tx_frame
        self.rx_frame = rx_frame
        self.response_time_ms = response_time_ms
        self.tx_transport_frame = tx_transport_frame
        self.rx_transport_frame = rx_transport_frame
        self.transport_meta = transport_meta or {}

    @property
    def tx_hex(self) -> str:
        return hex_bytes(self.tx_frame)

    @property
    def rx_hex(self) -> str:
        return hex_bytes(self.rx_frame)

    @property
    def tx_transport_hex(self) -> str:
        return hex_bytes(self.tx_transport_frame or self.tx_frame)

    @property
    def rx_transport_hex(self) -> str:
        return hex_bytes(self.rx_transport_frame or self.rx_frame)


class ModbusClient:
    def __init__(self, config):
        self.config = config
        self.client = None
        self.transport = str(self.config.get("transport", "serial")).lower()
        if self.transport == "udp":
            self.transport = "udp_rtu"
        self._seq = _int_value(self.config.get("seq_current", self.config.get("seq_start", "0x21D1")), 0x21D1)

    def connect(self) -> bool:
        self.close()
        self.transport = str(self.config.get("transport", "serial")).lower()
        if self.transport == "udp":
            self.transport = "udp_rtu"
        if self.transport in ("udp_rtu", "udp_c2000pp"):
            try:
                self.client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.client.settimeout(float(self.config["timeout_ms"]) / 1000.0)
                local_host = str(self.config.get("local_host", "")).strip()
                local_port = int(self.config.get("local_port", 0) or 0)
                if local_host or local_port:
                    self.client.bind((local_host or "0.0.0.0", local_port))
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

    def current_seq(self) -> int:
        return self._seq & 0xFFFF

    def read_holding_registers_raw(self, slave_id: int, start_addr: int, count: int) -> ModbusExchange:
        return self._read_registers(function=0x03, slave_id=slave_id, start_addr=start_addr, count=count)

    def read_holding_registers(self, slave_id: int, start_addr: int, count: int) -> List[int]:
        return self.read_holding_registers_raw(slave_id, start_addr, count).registers

    def read_input_registers(self, slave_id: int, start_addr: int, count: int) -> List[int]:
        return self._read_registers(function=0x04, slave_id=slave_id, start_addr=start_addr, count=count).registers

    def _wrap_c2000pp(self, frame: bytes, seq: int) -> bytes:
        if len(frame) > 255:
            raise ModbusError(f"RTU frame too long for UDP wrapper: {len(frame)} bytes", tx_frame=frame)
        prefix = _int_value(self.config.get("prefix", "0x10"), 0x10) & 0xFF
        channel = _int_value(self.config.get("channel", "0x10"), 0x10) & 0xFF
        return bytes([prefix, len(frame), (seq >> 8) & 0xFF, seq & 0xFF, channel]) + frame

    def _unwrap_c2000pp(self, datagram: bytes, tx_frame: bytes, tx_transport_frame: bytes, elapsed: float, transport_meta: Dict[str, Any]) -> bytes:
        prefix = _int_value(self.config.get("prefix", "0x10"), 0x10) & 0xFF
        channel = _int_value(self.config.get("channel", "0x10"), 0x10) & 0xFF
        if len(datagram) < 5:
            raise ModbusError(
                "invalid_udp_wrapper: datagram shorter than 5 byte header",
                tx_frame=tx_frame,
                rx_frame=datagram,
                tx_transport_frame=tx_transport_frame,
                rx_transport_frame=datagram,
                response_time_ms=elapsed,
                transport_meta={**transport_meta, "error": "invalid_udp_wrapper"},
            )
        if datagram[0] != prefix or datagram[4] != channel:
            raise ModbusError(
                f"invalid_udp_wrapper: prefix/channel mismatch ({hex_bytes(datagram[:5])})",
                tx_frame=tx_frame,
                rx_frame=datagram[5:],
                tx_transport_frame=tx_transport_frame,
                rx_transport_frame=datagram,
                response_time_ms=elapsed,
                transport_meta={**transport_meta, "error": "invalid_udp_wrapper"},
            )
        received_len = datagram[1]
        received_seq = (datagram[2] << 8) | datagram[3]
        transport_meta["rx_seq"] = f"{received_seq:04X}"
        payload = datagram[5:]
        if received_len != len(payload):
            raise ModbusError(
                f"invalid_udp_wrapper: LEN={received_len}, payload={len(payload)}",
                tx_frame=tx_frame,
                rx_frame=payload,
                tx_transport_frame=tx_transport_frame,
                rx_transport_frame=datagram,
                response_time_ms=elapsed,
                transport_meta={**transport_meta, "error": "invalid_udp_wrapper"},
            )
        return payload

    def _drain_udp_socket(self):
        if self.transport not in ("udp_rtu", "udp_c2000pp") or not self.client:
            return 0
        drained = 0
        original_timeout = self.client.gettimeout()
        try:
            self.client.settimeout(0.0)
            while True:
                try:
                    self.client.recvfrom(2048)
                    drained += 1
                except (BlockingIOError, socket.timeout):
                    break
                except OSError:
                    break
        finally:
            try:
                self.client.settimeout(original_timeout)
            except OSError:
                pass
        return drained

    def _exchange_frame(self, tx_frame: bytes):
        transport_meta: Dict[str, Any] = {"transport": self.transport}
        tx_transport_frame = tx_frame
        started = time.perf_counter()
        if self.transport in ("udp_rtu", "udp_c2000pp"):
            host = str(self.config.get("remote_host") or self.config.get("udp_host") or "").strip()
            port = int(self.config.get("remote_port") or self.config.get("udp_port", 502))
            drained = self._drain_udp_socket()
            if drained:
                transport_meta["drained_datagrams"] = drained
            if self.transport == "udp_c2000pp":
                seq = self._seq & 0xFFFF
                tx_transport_frame = self._wrap_c2000pp(tx_frame, seq)
                transport_meta["tx_seq"] = f"{seq:04X}"
                self._seq = (self._seq + 1) & 0xFFFF
            self.client.sendto(tx_transport_frame, (host, port))
            rx_transport_frame, remote = self.client.recvfrom(2048)
            elapsed = round((time.perf_counter() - started) * 1000, 2)
            transport_meta["remote"] = f"{remote[0]}:{remote[1]}"
            if self.transport == "udp_c2000pp":
                rx_frame = self._unwrap_c2000pp(rx_transport_frame, tx_frame, tx_transport_frame, elapsed, transport_meta)
            else:
                rx_frame = rx_transport_frame
            return rx_frame, elapsed, tx_transport_frame, rx_transport_frame, transport_meta

        try:
            self.client.reset_input_buffer()
        except Exception:
            pass
        self.client.write(tx_frame)
        self.client.flush()
        header = self.client.read(3)
        if len(header) < 3:
            elapsed = round((time.perf_counter() - started) * 1000, 2)
            raise ModbusError("No response", tx_frame=tx_frame, rx_frame=header, response_time_ms=elapsed)
        if header[1] & 0x80:
            tail = self.client.read(2)
            rx_frame = header + tail
            elapsed = round((time.perf_counter() - started) * 1000, 2)
            raise ModbusError(
                f"Modbus exception: function={header[1]}, code={header[2]}",
                tx_frame=tx_frame,
                rx_frame=rx_frame,
                response_time_ms=elapsed,
            )
        byte_count = header[2]
        tail = self.client.read(byte_count + 2)
        rx_frame = header + tail
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        return rx_frame, elapsed, tx_transport_frame, rx_frame, transport_meta

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
        started = time.perf_counter()

        try:
            rx_frame, elapsed, tx_transport_frame, rx_transport_frame, transport_meta = self._exchange_frame(tx_frame)

            if len(rx_frame) < 5:
                raise ModbusError(
                    f"Incomplete response: got={len(rx_frame)}, expected>=5",
                    tx_frame=tx_frame,
                    rx_frame=rx_frame,
                    tx_transport_frame=tx_transport_frame,
                    rx_transport_frame=rx_transport_frame,
                    response_time_ms=elapsed,
                    transport_meta=transport_meta,
                )

            if rx_frame[1] & 0x80:
                raise ModbusError(
                    f"Modbus exception: slave={slave_id}, function={function}, code={rx_frame[2]}",
                    tx_frame=tx_frame,
                    rx_frame=rx_frame,
                    tx_transport_frame=tx_transport_frame,
                    rx_transport_frame=rx_transport_frame,
                    response_time_ms=elapsed,
                    transport_meta=transport_meta,
                )

            byte_count = rx_frame[2]
            expected_len = 3 + byte_count + 2
            if len(rx_frame) != expected_len:
                raise ModbusError(
                    f"Incomplete response: got={len(rx_frame)}, expected={expected_len}",
                    tx_frame=tx_frame,
                    rx_frame=rx_frame,
                    tx_transport_frame=tx_transport_frame,
                    rx_transport_frame=rx_transport_frame,
                    response_time_ms=elapsed,
                    transport_meta=transport_meta,
                )
            if rx_frame[0] != slave_id or rx_frame[1] != function:
                raise ModbusError(
                    f"Unexpected response header: {hex_bytes(rx_frame[:2])}",
                    tx_frame=tx_frame,
                    rx_frame=rx_frame,
                    tx_transport_frame=tx_transport_frame,
                    rx_transport_frame=rx_transport_frame,
                    response_time_ms=elapsed,
                    transport_meta=transport_meta,
                )
            crc_received = rx_frame[-2] | (rx_frame[-1] << 8)
            crc_calculated = crc16_modbus(rx_frame[:-2])
            if crc_received != crc_calculated:
                raise ModbusError(
                    f"CRC error: got={crc_received:04X}, expected={crc_calculated:04X}",
                    tx_frame=tx_frame,
                    rx_frame=rx_frame,
                    tx_transport_frame=tx_transport_frame,
                    rx_transport_frame=rx_transport_frame,
                    response_time_ms=elapsed,
                    transport_meta=transport_meta,
                )
            if byte_count != count * 2:
                raise ModbusError(
                    f"Unexpected byte count: got={byte_count}, expected={count * 2}",
                    tx_frame=tx_frame,
                    rx_frame=rx_frame,
                    tx_transport_frame=tx_transport_frame,
                    rx_transport_frame=rx_transport_frame,
                    response_time_ms=elapsed,
                    transport_meta=transport_meta,
                )

            payload = rx_frame[3:-2]
            registers = [int.from_bytes(payload[index:index + 2], "big") for index in range(0, len(payload), 2)]
            return ModbusExchange(
                registers=registers,
                tx_frame=tx_frame,
                rx_frame=rx_frame,
                response_time_ms=elapsed,
                tx_transport_frame=tx_transport_frame,
                rx_transport_frame=rx_transport_frame,
                transport_meta=transport_meta,
            )
        except (serial.SerialException, socket.timeout, OSError) as exc:
            elapsed = round((time.perf_counter() - started) * 1000, 2)
            raise ModbusError(str(exc), tx_frame=tx_frame, response_time_ms=elapsed) from exc
