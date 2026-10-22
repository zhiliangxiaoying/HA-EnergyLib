"""Strict MBAP framing, profile-selected push filtering and verified retries."""

import asyncio
import contextlib
import struct

READ_RETRY_DELAYS = (0.2, 0.4, 0.8)  # Three retries after the initial read.
MAX_PUSH_FRAMES = 8


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
        self.read_retries = 0
        self.unsolicited_filter = None  # Selected explicitly by the device profile.
        self.filtered_push_frames = 0
        self.verify_write_identity = None
        self.authorize_write_retry = None
        self.write_retries = 0
        self.write_readback_recoveries = 0
        self._write_lock = asyncio.Lock()
        # DeviceRuntime pins writes to its last identity-verified connection.
        self.write_generation: int | None = None

    @property
    def connected(self) -> bool:
        """An open connection; the next request will not reconnect."""
        return self._writer is not None and not self._writer.is_closing()

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
        data = await self._request(
            struct.pack(">BHH", function, address, count), unit_id, read_size=count * 2
        )
        return list(struct.unpack(f">{count}H", data[2:]))

    async def read_discrete_inputs(self, address, count, *, unit_id=None):
        if not 0 <= address < 65536 or not 1 <= count <= 2000 or address + count > 65536:
            raise ValueError("Invalid bit range")
        size = (count + 7) // 8
        data = await self._request(struct.pack(">BHH", 2, address, count), unit_id, read_size=size)
        return [bool(data[2 + i // 8] & (1 << (i % 8))) for i in range(count)]

    async def write_registers(self, address, words, *, unit_id=None, retry_safe=False):
        if not 0 <= address < 65536 or not 1 <= len(words) <= 123 or address + len(words) > 65536:
            raise ValueError("Invalid write range")
        if any(type(w) is not int or not 0 <= w <= 65535 for w in words):
            raise ValueError("Invalid word")
        if type(retry_safe) is not bool:
            raise ValueError("Invalid write retry policy")
        words = tuple(words)  # The target cannot change between attempts.
        pdu = struct.pack(">BHHB", 16, address, len(words), len(words) * 2)
        async with self._write_lock:
            for attempt in range(len(READ_RETRY_DELAYS) + 1):
                try:
                    await self._request(pdu + struct.pack(f">{len(words)}H", *words), unit_id)
                    return
                except (CannotConnect, InvalidResponse):
                    if not retry_safe or self.verify_write_identity is None:
                        raise
                    # A lost acknowledgement is not proof that the write failed.
                    # Any failed identity/read-back check aborts without resending.
                    await self.verify_write_identity()
                    actual = await self.read_holding_registers(address, len(words), unit_id=unit_id)
                    await self.verify_write_identity()
                    if actual == list(words):
                        self.write_readback_recoveries += 1
                        return
                    if attempt == len(READ_RETRY_DELAYS):
                        raise
                    await asyncio.sleep(READ_RETRY_DELAYS[attempt])
                    if self.authorize_write_retry is not None:
                        await self.authorize_write_retry(address, words)
                    self.write_retries += 1

    async def _request(self, pdu, unit_id=None, *, read_size=None):
        unit_id = self.unit_id if unit_id is None else unit_id
        if type(unit_id) is not int or not 1 <= unit_id <= 255:
            raise ValueError("Invalid unit ID")
        async with self._lock:
            # Keep all attempts and byte-count validation under the same lock.
            # A queued request must not consume bytes from a failed transaction.
            for attempt in range(len(READ_RETRY_DELAYS) + 1):
                try:
                    response = await self._request_once(pdu, unit_id)
                    if read_size is not None and (
                        len(response) != read_size + 2 or response[1] != read_size
                    ):
                        raise InvalidResponse("Invalid read byte count")
                    return response
                except asyncio.CancelledError:
                    await self._disconnect()
                    raise
                except (CannotConnect, InvalidResponse):
                    await self._disconnect()
                    if read_size is None or attempt == len(READ_RETRY_DELAYS):
                        raise
                    self.read_retries += 1
                    await asyncio.sleep(READ_RETRY_DELAYS[attempt])

    async def _request_once(self, pdu, unit_id):
        try:
            async with asyncio.timeout(self.timeout):
                if pdu[0] == 16 and self.write_generation is not None:
                    if (
                        self.write_generation != self.generation
                        or self._writer is None
                        or self._writer.is_closing()
                    ):
                        raise CannotConnect("Connection changed since identity verification")
                if self._writer is None or self._writer.is_closing():
                    self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
                    self.generation += 1
                self._transaction = (self._transaction + 1) & 0xFFFF
                transaction = self._transaction
                self._writer.write(
                    struct.pack(">HHHB", transaction, 0, len(pdu) + 1, unit_id) + pdu
                )
                self.requests += 1
                await self._writer.drain()
                response = await self._read_response(transaction, unit_id)
                if response[0] == (pdu[0] | 0x80):
                    if len(response) != 2:
                        raise InvalidResponse("Invalid exception length")
                    raise DeviceException(response[1])
                if response[0] != pdu[0]:
                    raise InvalidResponse("Invalid response function")
                if pdu[0] == 16 and response != pdu[:5]:
                    raise InvalidResponse("Write acknowledgement mismatch; outcome unknown")
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

    async def _read_response(self, transaction, unit_id):
        skipped = 0
        while True:
            header = await self._reader.readexactly(7)
            tid, protocol, length, unit = struct.unpack(">HHHB", header)
            # A valid expected Modbus header always takes precedence.
            if tid == transaction and protocol == 0 and unit == unit_id and 2 <= length <= 254:
                return await self._reader.readexactly(length - 1)
            if self.unsolicited_filter is not None and await self.unsolicited_filter(
                header, self._reader
            ):
                self.filtered_push_frames += 1
                skipped += 1
                if skipped > MAX_PUSH_FRAMES:
                    raise InvalidResponse("Too many unsolicited frames")
                continue
            raise InvalidResponse("Invalid MBAP header")
