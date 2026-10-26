"""Software Sinexcel V001B031 inverter: same registers and function codes as the
protocol, so HA adds it exactly like a real device (one EnergyLIB profile).

Example: python -m tools.sinexcel_simulator --bind 192.0.2.10 --port 15021 \
             --allow-peer 192.0.2.20 --enable-writes --mdns
--mdns advertises _energylib._tcp.local. on --bind (a concrete LAN address).
Writes are off unless --enable-writes. Never run against or proxy physical hardware.

Where the protocol is silent the simulator mirrors the integration's stated
assumptions (heartbeat via 0xC068, LSB-first running-state bits, TOU dates as
local-as-UTC U32 stamps) so mismatches surface during firmware integration.
"""

import argparse
import asyncio
import contextlib
import importlib
import json
import math
import struct
import sys
import time
import types
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .discovery_advertisement import service_info, txt_properties
from .inverter_simulator import InverterModel, atomic_json

_package = types.ModuleType("energylib_bench")
_package.__path__ = [str(Path(__file__).resolve().parents[1] / "custom_components" / "energylib")]
sys.modules.setdefault(_package.__name__, _package)
_sx = importlib.import_module("energylib_bench.energylib_core.profiles.sinexcel_v2")
_tou = importlib.import_module("energylib_bench.energylib_core.model.tou")

CONTROL_ADDRESSES = _sx.CONTROL_ADDRESSES
ADVANCED_CONTROL_ADDRESSES = _sx.ADVANCED_CONTROL_ADDRESSES
BATTERY_2_ADDRESSES = _sx.BATTERY_2_ADDRESSES
SCHEDULE_BLOCKS = _sx.SCHEDULE_BLOCKS
HEARTBEAT, HEARTBEAT_TIMEOUT, ESCAPE_MODE = _sx.HEARTBEAT, _sx.HEARTBEAT_TIMEOUT, _sx.ESCAPE_MODE
PARAMETER_BLOCKS = {
    **{a: 2 for a in CONTROL_ADDRESSES.values()},
    **{a: 2 for a in ADVANCED_CONTROL_ADDRESSES.values()},
    **{a: 2 for a in BATTERY_2_ADDRESSES.values()},
    0x3002: 2,
    0x314C: 2,
    0x30B8: 2,
    0x314E: 2,
    **dict(SCHEDULE_BLOCKS),
}


@dataclass(frozen=True)
class BenchConfig:
    serial: str = "ELIB260002"
    machine_type: int = 0
    pv_count: int = 2
    batteries: int = 1
    bms: bool = True
    port_use: int = 0
    max_registers: int = 60
    writes: bool = False
    rated_kw: float = 5.0  # Served at 0x2008 as U16 value * 100 (V001B031).

    def __post_init__(self):
        if not self.serial or len(self.serial) > 24:
            raise ValueError("Serial must be 1-24 ASCII bytes")
        if not all(0x21 <= ord(c) <= 0x7E for c in self.serial):
            raise ValueError("Serial must be printable ASCII without spaces")
        if self.machine_type not in (0, 1, 2) or not 0 <= self.pv_count <= 10:
            raise ValueError("Invalid capabilities")
        if self.batteries not in (1, 2) or not 48 <= self.max_registers <= 125:
            raise ValueError("Invalid battery count/register limit")
        if not 0 < self.rated_kw <= 655:
            raise ValueError("Invalid rated power")


def tou_limits(config):
    return _tou.TouLimits(max_power_kw=min(config.rated_kw, _sx.PROTOCOL_MAX_KW))


class BenchModel:
    def __init__(self, config=BenchConfig(), *, clock=time.monotonic, state=None):
        self.config, self.clock = config, clock
        self.base = InverterModel(state)
        self.registers = {}
        self.bits = {u: {a: False for a in range(0x1000, 0x1112)} for u in (1, 2)}
        self.bits[1][0x1001] = self.bits[1][0x1002] = True  # on-grid enum 3
        self.last_heartbeat = None
        self.last_now = None
        self.schedule_error = False
        for key, address in CONTROL_ADDRESSES.items():
            self.set_float(address, {"backup_soc": 20, "min_soc": 10}.get(key, 0))
        for address, value in (
            (0x314C, 20),
            (0x30B8, 10),
            # Protocol defaults: max SOC 100 %, three-phase max charge/discharge 25 A.
            (0x31B6, 100),
            (0x31B8, 100),
            (0x3040, 25),
            (0x3042, 25),
            (0x30B2, 25),
            (0x30B4, 25),
            (0x3002, 0),
            (0x314E, 0),
            (HEARTBEAT, 0),  # 0 = Dead until an EMS writes 1 (Alive)
            (HEARTBEAT_TIMEOUT, 60),
            (ESCAPE_MODE, 1),
        ):
            self.set_float(address, value)
        for (address, _), words in zip(
            SCHEDULE_BLOCKS, _sx.encode_schedule(_tou.TouSchedule(), datetime.now().date())
        ):
            self.registers.update({address + i: w for i, w in enumerate(words)})
        if state is not None and "bench_parameters" in state:
            saved = state["bench_parameters"]
            writable = PARAMETER_BLOCKS
            for text, words in saved.items():
                address = int(text)
                if (
                    address not in writable
                    or len(words) != writable[address]
                    or any(type(w) is not int or not 0 <= w <= 65535 for w in words)
                ):
                    raise ValueError("Invalid persisted bench parameters")
                self.registers.update({address + i: w for i, w in enumerate(words)})
            self.schedule()  # Reject damaged plans before serving any connection.
            # A restarted bench never resumes a previous remote lease or setpoint.
            self.set_float(0xC646, 0)
            self.set_float(0xC648, 0)

    def set_float(self, address, value):
        self.registers.update({address + i: w for i, w in enumerate(_sx.floats([value]))})

    def get_float(self, address):
        return struct.unpack(
            ">f", struct.pack(">HH", self.registers[address], self.registers[address + 1])
        )[0]

    def schedule(self):
        blocks = [[self.registers[a + i] for i in range(n)] for a, n in SCHEDULE_BLOCKS]
        return _sx.decode_schedule(blocks, self.get_float(0x314E), tou_limits(self.config))

    def schedule_in_effect(self, today: date):
        """0xC0B6/0xC0B8: U32 local-date stamps bounding the TOU plan."""
        words = [self.registers[0xC0B4 + i] for i in range(6)]
        start, end = words[2] << 16 | words[3], words[4] << 16 | words[5]
        return start <= _sx.local_date_stamp(today) <= end

    def tick(self, now, seconds):
        self.last_now = now
        seconds = max(0, min(seconds, 5))
        if self.get_float(0xC646) and (
            self.last_heartbeat is None
            or self.clock() - self.last_heartbeat >= self.get_float(HEARTBEAT_TIMEOUT)
        ):
            self.set_float(HEARTBEAT, 0)  # Dead
            escape = self.get_float(ESCAPE_MODE)
            if escape in (1, 2):  # 1: self-consumption, 2: stop; 0 keeps running
                self.set_float(0xC646, 0)
                if escape == 1:
                    self.set_float(0x300C, 0)
                else:
                    self.set_float(0xC648, 0)
        values = self.base.step(now, 0)
        solar, load = values["solar_power"], values["load_power"]
        battery = values["battery_power"]
        if self.get_float(0xC646):
            battery = self.get_float(0xC648)
        elif self.get_float(0x300C) == 1:
            try:
                schedule = self.schedule()
                active = schedule.active(now) if self.schedule_in_effect(now.date()) else None
                if active:
                    battery = active.power * (-1 if active.type == "charge" else 1)
                else:
                    battery = self.outside_power(schedule.outside_mode, battery)
                self.schedule_error = False
            except ValueError:
                battery = 0
                self.schedule_error = True
        elif self.get_float(0x300C) in (3, 5, 6):
            # Explicit bench policy: backup/PV-charge do not discharge; feed-in
            # priority leaves the battery idle. These are not hardware claims.
            battery = self.outside_power(
                {3: "backup", 5: "pv_charge_priority", 6: "feed_in_priority"}[
                    self.get_float(0x300C)
                ],
                battery,
            )
        if battery < 0 and not self.get_float(0x322A):
            battery = max(battery, -max(solar - load, 0))
        dt = seconds / 3600
        if dt:
            battery = min(
                battery,
                max(0, self.base.soc - max(10, self.get_float(0x3046))) / 100 * 10 * 0.95 / dt,
            )
            battery = max(battery, -(95 - self.base.soc) / 100 * 10 / (0.95 * dt))
        # Curtail against the achievable battery power, including full/empty SOC.
        if self.get_float(0x3002):
            export = self.get_float(0x3004)
            battery = min(battery, load + export)
            solar = min(solar, max(0, load - battery + export))
        grid = load - solar - battery
        self.base.soc += (-battery * 0.95 if battery < 0 else -battery / 0.95) * dt / 10 * 100
        powers = [max(grid, 0), max(-grid, 0), solar, max(-battery, 0), max(battery, 0), load]
        for key, power in zip(self.base.totals, powers):
            self.base.totals[key] += power * dt
        self.base.values = {
            **self.base.totals,
            "battery_soc": self.base.soc,
            "solar_power": solar,
            "load_power": load,
            "battery_power": battery,
            "grid_power": grid,
        }
        old = self.base.registers()
        old[0x2006] = self.config.machine_type
        old[0x2007] = old[0x2008] = round(self.config.rated_kw * 100)  # U16, kVA * 100
        for i, word in enumerate(
            struct.unpack(">12H", self.config.serial.encode().ljust(24, b"\0"))
        ):
            old[0xC102 + i] = word
        self.registers.update(old)
        extras = {
            0xC644: self.base.soc,
            0xC05E: self.config.pv_count,
            0x3036: self.config.batteries - 1,
            0xC264: self.config.port_use,
            0xB000: int(self.config.bms),
            0xB108: 97,
            # Battery cabinet 1 BMS view (engineering units, as the integration assumes).
            0xB10A: 3.331,
            0xB10C: 3.302,
            0xB112: 27.5,
            0xB114: 24.0,
            0xB11A: 50.0,
            0xB11C: 60.0,
            0xB15C: 10.24,
            0xB15E: round(10.24 * self.base.soc / 100, 3),
            # Sinexcel convention on the wire: battery + = charging, grid + = feed-in.
            0x1272: -battery,
            0x1276: -grid,
            0x121C: self.base.soc,
            0x121A: -battery / 2,
            0x1000: 230,
            0x1002: 231,
            0x1004: 229,
            0x1012: 50,
            0x120C: 51.2,
            0x120E: -battery * 1000 / 51.2,
            0x1216: 51.0,  # battery 2 voltage
            0x1218: -battery / 2 * 1000 / 51.0,  # battery 2 current, device sign
            0x1278: load * 0.3,
            0x128C: 0,
            0x12A4: 0,
            **{0x10DE + i * 2: 35 + i for i in range(5)},
        }
        for i, address in enumerate((0x1204, 0x120A, 0x1280, 0x1286), 1):
            extras[address] = (
                solar / max(1, self.config.pv_count) if i <= self.config.pv_count else 0
            )
        for address, value in extras.items():
            self.set_float(address, value)

    @staticmethod
    def outside_power(mode, battery):
        """Use the same explicit bench policy inside and outside a TOU period."""
        if mode in ("backup", "pv_charge_priority"):
            return min(battery, 0)
        if mode == "feed_in_priority":
            return 0
        return battery

    def state(self):
        return {
            **self.base.state(),
            "bench_parameters": {
                str(a): [self.registers[a + i] for i in range(n)]
                for a, n in PARAMETER_BLOCKS.items()
            },
        }

    def apply_write(self, address, words):
        if not self.config.writes:
            return 1
        allowed = {
            **PARAMETER_BLOCKS,
            HEARTBEAT: 2,
            HEARTBEAT_TIMEOUT: 2,
            ESCAPE_MODE: 2,
        }
        if allowed.get(address) != len(words):
            return 2
        if len(words) == 2:
            value = struct.unpack(">f", struct.pack(">2H", *words))[0]
            if not math.isfinite(value):
                return 3
            if address == HEARTBEAT and value != 1:
                return 3  # Only "Alive" (1) is a meaningful EMS heartbeat.
            # Real V001B031 (2026-10-26): the manual-control power is writable only
            # while manual control is enabled.
            if address == 0xC648 and not self.get_float(0xC646):
                return 3
        self.registers.update({address + i: w for i, w in enumerate(words)})
        if address in (HEARTBEAT, 0xC646):
            self.last_heartbeat = self.clock()
        if address == HEARTBEAT:
            self.set_float(HEARTBEAT, 0)  # The device consumes the Alive flag at once.
        if address == 0xC646 and self.get_float(0xC646):
            self.set_float(0x300C, 4)  # Enabling switches to manual mode, kept after exit.
        return 0


class BenchServer:
    def __init__(self, model, allowed_peers, *, fault="normal", fail_write_index=0):
        self.model, self.allowed_peers = model, allowed_peers
        self.fault, self.fail_write_index = fault, fail_write_index
        self.requests = self.write_count = 0
        self.read_log = deque(maxlen=100)
        self.writers = set()

    async def close(self):
        for writer in list(self.writers):
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()

    async def handle(self, reader, writer):
        peer = writer.get_extra_info("peername")
        if not peer or peer[0] not in self.allowed_peers:
            writer.close()
            return
        self.writers.add(writer)
        try:
            while True:
                async with asyncio.timeout(120):
                    tid, proto, length, unit = struct.unpack(">HHHB", await reader.readexactly(7))
                    if proto or not 2 <= length <= 254:
                        return
                    pdu = await reader.readexactly(length - 1)
                self.requests += 1
                fc = pdu[0]
                error, response = 0, None
                if unit not in (1, 2) or (unit == 2 and self.fault == "unit2_offline"):
                    error = 11
                elif fc in (2, 3, 4) and len(pdu) == 5:
                    address, count = struct.unpack(">HH", pdu[1:])
                    self.read_log.append((fc, unit, address, count))
                    if fc == 2:
                        bits = self.model.bits[unit]
                        if not 1 <= count <= 2000:
                            error = 3
                        elif self.fault == "fc02_even_address" and address % 2:
                            error = 3
                        elif any(address + i not in bits for i in range(count)):
                            error = 2
                        else:
                            data = bytearray((count + 7) // 8)
                            for i in range(count):
                                data[i // 8] |= int(bits[address + i]) << (i % 8)
                            response = bytes([fc, len(data)]) + data
                    elif (
                        self.fault == "flow_single_only"
                        and fc == 3
                        and address == 0x1270
                        and count == 8
                    ):
                        error = 3
                    elif not 1 <= count <= self.model.config.max_registers:
                        error = 3
                    elif unit != 1 or any(
                        address + i not in self.model.registers for i in range(count)
                    ):
                        error = 2
                    else:
                        response = bytes([fc, count * 2]) + struct.pack(
                            f">{count}H", *[self.model.registers[address + i] for i in range(count)]
                        )
                elif fc == 16 and len(pdu) >= 6:
                    address, count, size = struct.unpack(">HHB", pdu[1:6])
                    self.write_count += 1
                    if self.fault == "disconnect" or self.write_count == self.fail_write_index:
                        return
                    if not 1 <= count <= 123 or size != count * 2 or len(pdu) != size + 6:
                        error = 3
                    elif self.fault == "reject_write" or unit != 1:
                        error = 1
                    elif self.fault == "mismatch":
                        response = pdu[:5]
                    else:
                        error = self.model.apply_write(
                            address, list(struct.unpack(f">{count}H", pdu[6:]))
                        )
                        response = pdu[:5]
                else:
                    error = 1
                if error:
                    response = bytes([fc | 0x80, error])
                reply = struct.pack(">HHHB", tid, 0, len(response) + 1, unit) + response
                if self.fault == "swapped_length":
                    reply = reply[:4] + reply[4:6][::-1] + reply[6:]
                if self.fault == "short_frame":
                    writer.write(reply[:-1])
                    await writer.drain()
                    return
                writer.write(reply)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, TimeoutError):
            pass
        finally:
            self.writers.discard(writer)
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()


def mdns_properties(logger_sn):
    """The logger identity is independent of the Modbus inverter serial."""
    return txt_properties(logger_sn)


def mdns_service_info(logger_sn, bind, port):
    return service_info(logger_sn, bind, port)


async def run(args):
    info = (
        mdns_service_info(args.logger_sn or f"LOGGER-{args.serial}", args.bind, args.port)
        if args.mdns
        else None
    )
    config = BenchConfig(
        args.serial,
        args.machine_type,
        args.pv_count,
        args.batteries,
        not args.no_bms,
        args.port_use,
        args.max_registers,
        args.enable_writes,
        args.rated_kw,
    )
    state = json.loads(args.state.read_text()) if args.state.exists() else None
    model = BenchModel(config, state=state)
    zone = timezone(timedelta(hours=8))
    model.tick(datetime.now(zone), 0)
    peer = BenchServer(
        model,
        {args.bind, args.allow_peer, "127.0.0.1"},
        fault=args.fault,
        fail_write_index=args.fail_write_index,
    )
    server = await asyncio.start_server(peer.handle, args.bind, args.port)
    zc = None
    if info is not None:
        from zeroconf import IPVersion
        from zeroconf.asyncio import AsyncZeroconf

        zc = AsyncZeroconf(interfaces=[args.bind], ip_version=IPVersion.V4Only)
        await zc.async_register_service(info, allow_name_change=True)
        print(f"mDNS registered {info.name} on {args.bind}:{args.port}", flush=True)
    previous = time.monotonic()
    try:
        while True:
            await asyncio.sleep(1)
            current = time.monotonic()
            model.tick(datetime.now(zone), current - previous)
            previous = current
            atomic_json(args.state, model.state())
    finally:
        if zc is not None:
            await zc.async_unregister_service(info)
            await zc.async_close()
        server.close()
        await peer.close()
        await server.wait_closed()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", required=True)
    parser.add_argument("--port", type=int, default=15021)
    parser.add_argument("--allow-peer", required=True)
    parser.add_argument("--serial", default="ELIB260002")
    parser.add_argument("--logger-sn", help="Logger identity, independent of --serial")
    parser.add_argument("--rated-kw", type=float, default=5.0, help="Served at 0x2008")
    parser.add_argument("--machine-type", type=int, choices=(0, 1, 2), default=0)
    parser.add_argument("--pv-count", type=int, default=2)
    parser.add_argument("--batteries", type=int, choices=(1, 2), default=1)
    parser.add_argument("--port-use", type=int, choices=(0, 1, 2, 4), default=0)
    parser.add_argument("--no-bms", action="store_true")
    parser.add_argument("--max-registers", type=int, default=60)
    parser.add_argument("--enable-writes", action="store_true")
    parser.add_argument(
        "--mdns", action="store_true", help="Advertise _energylib._tcp.local. on --bind"
    )
    parser.add_argument(
        "--fault",
        choices=(
            "normal",
            "unit2_offline",
            "reject_write",
            "mismatch",
            "disconnect",
            "short_frame",
            "flow_single_only",
            "fc02_even_address",
            "swapped_length",
        ),
        default="normal",
    )
    parser.add_argument("--fail-write-index", type=int, default=0)
    parser.add_argument("--state", type=Path, default=Path("local_data/sinexcel-sim-state.json"))
    asyncio.run(run(parser.parse_args()))
