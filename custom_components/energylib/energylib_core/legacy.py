"""Experimental V001B031 single-inverter read profile.

Addresses use the protocol's zero-based hexadecimal addresses as transmitted.
Register words and float32 values are big endian (ABCD). A float occupies two
16-bit Modbus registers. No parallel SN commands, writes or upgrades are exposed.
"""

import asyncio
import math
import struct
from dataclasses import dataclass

from .transport.modbus_tcp import DeviceException, InvalidResponse, ModbusTcpClient


class UnsupportedDevice(InvalidResponse):
    """The peer does not match the explicitly selected protocol profile."""


class IdentityMismatch(InvalidResponse):
    """The connected inverter is not the configured or advertised device."""


@dataclass(frozen=True)
class DeviceIdentity:
    """Identity read from the business interface, never from a display name."""

    serial: str
    machine_type: int
    protocol_version: str
    arm_version: str


@dataclass(frozen=True)
class Point:
    """One documented float32 measurement."""

    key: str
    address: int
    kind: str


POINTS = (
    Point("battery_soc", 0x1212, "soc"),
    Point("grid_import_energy", 0x1240, "energy"),
    Point("grid_export_energy", 0x1242, "energy"),
    Point("solar_energy", 0x125C, "energy"),
    Point("battery_charge_energy", 0x125E, "energy"),
    Point("battery_discharge_energy", 0x1260, "energy"),
    Point("load_energy", 0x1262, "energy"),
    Point("solar_power", 0x1270, "power"),
    Point("battery_power", 0x1272, "power"),
    Point("load_power", 0x1274, "power"),
    Point("grid_power", 0x1276, "power"),
)


def decode_float(words: list[int], *, kind: str) -> float | None:
    """Reject invalid numbers instead of silently substituting zero."""
    if len(words) != 2:
        raise InvalidResponse("A float32 requires two registers")
    value = struct.unpack(">f", struct.pack(">2H", *words))[0]
    if not math.isfinite(value):
        return None
    if kind == "energy" and value < 0:
        return None
    if kind == "soc" and not 0 <= value <= 100:
        return None
    return value


def decode_serial(words: list[int]) -> str:
    """Decode ASCII identity, removing only trailing NUL/space padding."""
    if len(words) != 12:
        raise UnsupportedDevice("Invalid identity length")
    raw = struct.pack(">12H", *words)
    # Observed hardware uses spaces; older peers use NULs. Normalize padding
    # without changing case or accepting whitespace inside an identity.
    content = raw.rstrip(b"\x00 ")
    try:
        serial = content.decode("ascii")
    except UnicodeDecodeError as err:
        raise UnsupportedDevice("Invalid identity encoding") from err
    if (
        not serial
        or serial != serial.strip()
        or not all(0x21 <= ord(char) <= 0x7E for char in serial)
    ):
        raise UnsupportedDevice("Invalid or empty identity")
    return serial


def decode_version(word: int) -> str:
    """The high 12 bits encode the main version, low 4 the branch."""
    return f"{word >> 4}.{word & 0xF}"


class EnergyLibDevice:
    """Own one single-inverter connection and take coordinated snapshots."""

    def __init__(self, client: ModbusTcpClient) -> None:
        self.client = client
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        async with self._lock:
            await self.client.close()

    async def _identify(self) -> DeviceIdentity:
        try:
            info = await self.client.read_holding_registers(0x2000, 10)
            serial = await self.client.read_holding_registers(0xC102, 12)
        except DeviceException as err:
            if err.code in (1, 2):
                raise UnsupportedDevice("Identity registers not supported") from err
            raise
        if info[0] in (0, 0xFFFF) or info[6] not in (0, 1, 2):
            raise UnsupportedDevice("Unrecognized protocol identity")
        return DeviceIdentity(
            serial=decode_serial(serial),
            machine_type=info[6],
            protocol_version=decode_version(info[0]),
            arm_version=decode_version(info[3]),
        )

    async def identify(self) -> DeviceIdentity:
        async with self._lock:
            return await self._identify()

    async def read_snapshot(self, expected_serial: str) -> dict[str, float | None]:
        """Check identity every cycle, including after reconnect/address reuse.

        Read each point independently to avoid undocumented holes and gateway
        block-size assumptions. Requests are sequential, never per HA entity.
        Illegal-function/address responses make only that point unavailable.
        """
        async with self._lock:
            identity = await self._identify()
            if identity.serial != expected_serial:
                raise IdentityMismatch("Connected device identity changed")
            generation = getattr(self.client, "generation", None)
            values: dict[str, float | None] = {}
            for point in POINTS:
                try:
                    words = await self.client.read_holding_registers(point.address, 2)
                except DeviceException as err:
                    if err.code not in (1, 2):
                        raise
                    values[point.key] = None
                else:
                    values[point.key] = decode_float(words, kind=point.kind)
            if getattr(self.client, "generation", None) != generation:
                if (await self._identify()).serial != expected_serial:
                    raise IdentityMismatch("Connected device identity changed")
            return values
