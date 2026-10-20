"""Standard MBAP framing, serialized FC02/03/04/16 without write retries."""

import asyncio
import contextlib
import struct


class EnergyLibError(Exception):
    """Base protocol/transport error; never include private payloads."""


class CannotConnect(EnergyLibError):
    """Connection failed, timed out or closed mid-frame."""


class InvalidResponse(EnergyLibError):
    """The peer returned a malformed or mismatched frame."""


class DeviceException(EnergyLibError):
    """A well-formed Modbus exception response."""

    def __init__(self, code: int) -> None:
        self.code = code
        super().__init__(f"Modbus exception {code}")


class ModbusTcpClient:
    """Serialize requests on one connection and discard broken streams."""

    def __init__(self, host: str, port: int, unit_id: int, *, timeout: float = 5.0) -> None:
        if not host.strip() or not 1 <= port <= 65535 or not 1 <= unit_id <= 255:
            raise ValueError("Invalid connection parameters")
        if timeout <= 0:
            raise ValueError("Timeout must be positive")
        self.host = host
        self.port = port
        self.unit_id = unit_id
        self.timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._transaction = 0
        self._lock = asyncio.Lock()
        self.generation = 0
        self.requests = 0

    async def _disconnect(self) -> None:
        writer, self._writer = self._writer, None
        self._reader = None
        if writer is not None:
            writer.close()
            with contextlib.suppress(OSError, TimeoutError):
                async with asyncio.timeout(1):
                    await writer.wait_closed()

    async def close(self) -> None:
        """Close after an outstanding request completes."""
        async with self._lock:
            await self._disconnect()

    async def read_holding_registers(self, address: int, count: int, *, unit_id=None) -> list[int]:
        return await self.read_registers(3, address, count, unit_id=unit_id)

    async def read_input_registers(self, address, count, *, unit_id=None):
        return await self.read_registers(4, address, count, unit_id=unit_id)

    async def read_registers(
        self, function, address: int, count: int, *, unit_id=None
    ) -> list[int]:
        """Read FC03 and validate identifiers, length, function and byte count."""
        if function not in (3, 4) or not 0 <= address <= 65535 or not 1 <= count <= 125:
            raise ValueError("Invalid register range")
        if address + count > 65536:
            raise ValueError("Register range exceeds address space")
        data = await self._request(struct.pack(">BHH", function, address, count), unit_id)
        if len(data) != count * 2 + 2 or data[1] != count * 2:
            await self.close()
            raise InvalidResponse("Invalid register response")
        return list(struct.unpack(f">{count}H", data[2:]))

    async def read_discrete_inputs(self, address, count, *, unit_id=None):
        if not 0 <= address < 65536 or not 1 <= count <= 2000 or address + count > 65536:
            raise ValueError("Invalid bit range")
        data = await self._request(struct.pack(">BHH", 2, address, count), unit_id)
        size = (count + 7) // 8
        if len(data) != size + 2 or data[1] != size:
            await self.close()
            raise InvalidResponse("Invalid bit response")
        return [bool(data[2 + i // 8] & (1 << (i % 8))) for i in range(count)]

    async def write_registers(self, address, words, *, unit_id=None):
        if not 0 <= address < 65536 or not 1 <= len(words) <= 123 or address + len(words) > 65536:
            raise ValueError("Invalid write range")
        if any(type(w) is not int or not 0 <= w <= 65535 for w in words):
            raise ValueError("Invalid word")
        pdu = struct.pack(">BHHB", 16, address, len(words), len(words) * 2)
        data = await self._request(pdu + struct.pack(f">{len(words)}H", *words), unit_id)
        if data != pdu[:5]:
            await self.close()
            raise InvalidResponse("Write acknowledgement mismatch; outcome unknown")

    async def _request(self, pdu, unit_id=None):
        unit_id = self.unit_id if unit_id is None else unit_id
        if type(unit_id) is not int or not 1 <= unit_id <= 255:
            raise ValueError("Invalid unit ID")
        async with self._lock:
            try:
                async with asyncio.timeout(self.timeout):
                    if self._writer is None or self._writer.is_closing():
                        self._reader, self._writer = await asyncio.open_connection(
                            self.host, self.port
                        )
                        self.generation += 1
                    self._transaction = (self._transaction + 1) & 0xFFFF
                    transaction = self._transaction
                    self._writer.write(
                        struct.pack(">HHHB", transaction, 0, len(pdu) + 1, unit_id) + pdu
                    )
                    self.requests += 1
                    await self._writer.drain()
                    header = await self._reader.readexactly(7)
                    tid, protocol, length, unit = struct.unpack(">HHHB", header)
                    if (
                        tid != transaction
                        or protocol != 0
                        or unit != unit_id
                        or not 2 <= length <= 254
                    ):
                        raise InvalidResponse("Invalid MBAP header")
                    response = await self._reader.readexactly(length - 1)
                    if response[0] == (pdu[0] | 0x80):
                        if len(response) != 2:
                            raise InvalidResponse("Invalid exception length")
                        raise DeviceException(response[1])
                    if response[0] != pdu[0]:
                        raise InvalidResponse("Invalid response function")
                    return response
            except asyncio.CancelledError:
                await self._disconnect()
                raise
            except (TimeoutError, OSError, asyncio.IncompleteReadError) as err:
                await self._disconnect()
                raise CannotConnect("Modbus connection failed") from err
            except DeviceException:
                # A complete exception PDU preserves stream alignment.
                raise
            except EnergyLibError:
                await self._disconnect()
                raise
