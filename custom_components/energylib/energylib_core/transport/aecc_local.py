"""AECC local interface: newline-terminated JSON requests over plain TCP.

AECC's local interface document (EMS local interface, updated 2026-11-16): a
request such as `{"Get": "EnergyParameter", "SerialNumber": n,
"CommandSource": "Web"}` followed by a newline is answered by one JSON object
(not newline-terminated) echoing `SerialNumber`. No authentication; the
document's default port is TCP 8080 (the port is taken from the announcement).

Used here: `Get EnergyParameter` (system summary), `Get
EnergycontrolparametersList` (control parameters, read only) and `Set
DataTransmission` carrying a Modbus RTU read (function 0x03) to a device behind
the EMS, and writes (0x06 / 0x10) the caller reads back.
"""

import asyncio
import json

from .modbus_tcp import EnergyLibError

# A summary is a few kilobytes; stop reading long before memory is at risk.
MAX_RESPONSE_BYTES = 256 * 1024
# Answers to an earlier request (one that timed out) are skipped, a few at most.
MAX_STALE_ANSWERS = 3


class AeccLocalError(EnergyLibError):
    """The AECC device could not be reached or did not answer as expected."""


def crc16(data):
    """Modbus RTU CRC (polynomial 0xA001), low byte first on the wire."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return bytes((crc & 0xFF, crc >> 8))


def rtu_read_frame(slave, address, count):
    body = bytes((slave, 3, address >> 8, address & 0xFF, count >> 8, count & 0xFF))
    return body + crc16(body)


def rtu_registers(frame, slave, count):
    """The registers of a function 0x03 answer; AeccLocalError when it is not one."""
    if len(frame) < 5 or crc16(frame[:-2]) != frame[-2:]:
        raise AeccLocalError("Invalid RTU answer")
    if frame[0] != slave:
        raise AeccLocalError("RTU answer from another device")
    if frame[1] == 0x83:
        raise AeccLocalError(f"Device exception {frame[2]}")
    if frame[1] != 3 or frame[2] != 2 * count or len(frame) != 5 + 2 * count:
        raise AeccLocalError("Unexpected RTU answer")
    data = frame[3:-2]
    return [int.from_bytes(data[i : i + 2], "big") for i in range(0, len(data), 2)]


def rtu_write_frame(slave, address, values):
    """Function 0x06 for one register, 0x10 for several."""
    if len(values) == 1:
        body = bytes((slave, 6, address >> 8, address & 0xFF, values[0] >> 8, values[0] & 0xFF))
    else:
        count = len(values)
        data = b"".join(int(v).to_bytes(2, "big") for v in values)
        body = (
            bytes((slave, 0x10, address >> 8, address & 0xFF, count >> 8, count & 0xFF, 2 * count))
            + data
        )
    return body + crc16(body)


def rtu_write_ack(frame, request):
    """Check the device's echo of a write; AeccLocalError when it is not one."""
    if len(frame) < 5 or crc16(frame[:-2]) != frame[-2:]:
        raise AeccLocalError("Invalid RTU answer")
    if frame[0] != request[0]:
        raise AeccLocalError("RTU answer from another device")
    if frame[1] == request[1] | 0x80:
        raise AeccLocalError(f"Device exception {frame[2]}")
    # 06 echoes the whole request; 10 echoes slave, function, address and count.
    if frame[:-2] != request[:6] or len(frame) != 8:
        raise AeccLocalError("Unexpected RTU answer to a write")


def hex_bytes(frame):
    return " ".join(f"{b:02X}" for b in frame)


class AeccLocalClient:
    """One connection, one request at a time; reconnects on the next request."""

    def __init__(self, host, port, *, timeout=5.0):
        self.host, self.port, self.timeout = host, port, timeout
        self._reader = self._writer = None
        self._buffer = b""
        self._lock = asyncio.Lock()
        self._serial = 0
        # Counters the diagnostics read (shared shape with the Modbus client).
        self.requests = 0
        self.read_retries = 0
        self.filtered_push_frames = 0
        self.write_retries = 0
        self.write_readback_recoveries = 0

    @property
    def connected(self):
        return self._writer is not None and not self._writer.is_closing()

    async def _connect(self):
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), self.timeout
            )
        except (OSError, asyncio.TimeoutError) as err:
            raise AeccLocalError(f"Cannot connect to {self.host}:{self.port}") from err
        self._buffer = b""

    async def exchange(self, message):
        """Send one request (a dict without SerialNumber) and return its answer."""
        name = message.get("Get") or message.get("Set")
        async with self._lock:
            if not self.connected:
                await self._connect()
            self._serial += 1
            serial = self._serial
            payload = {**message, "SerialNumber": serial, "CommandSource": "Web"}
            self.requests += 1
            try:
                self._writer.write(json.dumps(payload).encode() + b"\n")
                await asyncio.wait_for(self._writer.drain(), self.timeout)
                for _ in range(MAX_STALE_ANSWERS + 1):
                    answer = await asyncio.wait_for(self._read_object(), self.timeout)
                    # An answer without a serial is taken as this one.
                    if answer.get("SerialNumber", serial) == serial:
                        return answer
                    self.filtered_push_frames += 1
                raise ValueError("Only answers to other requests")
            except (OSError, asyncio.TimeoutError, ValueError) as err:
                await self._close_locked()
                raise AeccLocalError(f"No valid answer to {name}") from err

    async def request(self, name):
        """Send one `Get` request and return the decoded JSON object."""
        return await self.exchange({"Get": name})

    async def _read_object(self):
        """Read until the bytes received so far form one complete JSON object."""
        decoder = json.JSONDecoder()
        while True:
            if self._buffer.strip():
                try:
                    text = self._buffer.decode("utf-8").lstrip()
                except UnicodeDecodeError:
                    text = None  # a multi-byte character split across reads
                if text is not None:
                    try:
                        value, end = decoder.raw_decode(text)
                    except json.JSONDecodeError:
                        pass  # incomplete: wait for more
                    else:
                        # Keep what follows (the start of a next answer).
                        self._buffer = text[end:].encode("utf-8")
                        if not isinstance(value, dict):
                            raise ValueError("Answer is not a JSON object")
                        return value
            chunk = await self._reader.read(4096)
            if not chunk:
                raise ValueError("Connection closed before a complete answer")
            self._buffer += chunk
            if len(self._buffer) > MAX_RESPONSE_BYTES:
                raise ValueError("Answer too long")

    async def energy_parameters(self):
        return await self.request("EnergyParameter")

    async def control_parameters(self, fields):
        """Current control parameters (normalised strings), read only."""
        answer = await self.exchange(
            {"Get": "EnergycontrolparametersList", "RegControlField": list(fields)}
        )
        values = answer.get("ControlInfoField")
        if not isinstance(values, dict):
            raise AeccLocalError("No control parameters in the answer")
        return values

    async def _pass_through(self, frame):
        answer = await self.exchange(
            {
                "Set": "DataTransmission",
                "SetCommand": {"FunctionCode": frame[1], "TransmittedData": hex_bytes(frame)},
            }
        )
        result = answer.get("CommandResponse")
        if not isinstance(result, dict) or result.get("ControlState") != "succeed":
            raise AeccLocalError("Pass-through failed")
        try:
            return bytes.fromhex(str(result.get("TransmittedData", "")))
        except ValueError as err:
            raise AeccLocalError("Invalid pass-through data") from err

    async def write_registers(self, slave, address, values):
        """Write holding registers of a device behind the EMS (06 or 10).

        The caller reads the registers back: an echo only says the frame arrived.
        """
        values = [int(v) for v in values]
        if not values or any(not 0 <= v <= 0xFFFF for v in values):
            raise AeccLocalError("Register values must be 0-65535")
        frame = rtu_write_frame(slave, address, values)
        # Never retried: a lost answer leaves the outcome unknown, read back instead.
        rtu_write_ack(await self._pass_through(frame), frame)

    async def read_registers(self, slave, address, count):
        """Read holding registers of a device behind the EMS (RTU pass-through)."""
        frame = rtu_read_frame(slave, address, count)
        return rtu_registers(await self._pass_through(frame), slave, count)

    async def _close_locked(self):
        writer, self._reader, self._writer = self._writer, None, None
        self._buffer = b""
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def close(self):
        async with self._lock:
            await self._close_locked()
