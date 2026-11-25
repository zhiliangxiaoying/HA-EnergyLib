"""Local, read-only Modbus TCP inverter with mDNS advertisement.

This is a development peer, never evidence of real device compatibility. The model
uses positive grid import / positive battery discharge internally; registers carry
the Sinexcel convention (battery + = charging, grid + = feed-in), as on a device.
State is persisted without generating historical data while the PC is off.
"""

import argparse
import asyncio
import contextlib
import json
import logging
import math
import os
import struct
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

LOG = logging.getLogger(__name__)
SERVICE = "_energylib._tcp.local."
SERIAL = "ELIB260001"
LOCAL_TIMEZONE = timezone(timedelta(hours=8))
ENERGY_KEYS = (
    "grid_import_energy",
    "grid_export_energy",
    "solar_energy",
    "battery_charge_energy",
    "battery_discharge_energy",
    "load_energy",
)
FLOAT_ADDRESSES = {
    0x1212: "battery_soc",
    0x1240: "grid_import_energy",
    0x1242: "grid_export_energy",
    0x125C: "solar_energy",
    0x125E: "battery_charge_energy",
    0x1260: "battery_discharge_energy",
    0x1262: "load_energy",
    0x1270: "solar_power",
    0x1272: "battery_power",
    0x1274: "load_power",
    0x1276: "grid_power",
}
# Served with the Sinexcel sign (battery + = charging, grid + = feed-in).
DEVICE_SIGN_FLIPPED = {"battery_power", "grid_power"}


class InverterModel:
    """Integrate power and battery SOC with an instantaneous energy balance."""

    def __init__(self, state: dict | None = None) -> None:
        self.soc = 64.2
        self.totals = dict(zip(ENERGY_KEYS, [182.64, 104.25, 356.42, 84.10, 72.55, 423.16]))
        if state is not None:
            soc = float(state["battery_soc"])
            totals = {key: float(state["totals"][key]) for key in ENERGY_KEYS}
            if not math.isfinite(soc) or not 10 <= soc <= 95:
                raise ValueError("Invalid stored SOC")
            if any(not math.isfinite(value) or value < 0 for value in totals.values()):
                raise ValueError("Invalid stored energy counter")
            self.soc, self.totals = soc, totals
        self.values: dict[str, float] = {}

    def step(self, now: datetime, seconds: float) -> dict[str, float]:
        seconds = max(0.0, min(seconds, 5.0))
        hour = now.hour + now.minute / 60 + now.second / 3600
        day = now.toordinal()
        weather = 0.70 + 0.22 * math.sin(day * 1.73)
        daylight = max(0.0, math.sin(math.pi * (hour - 6) / 12)) if 6 <= hour <= 18 else 0
        cloud = 0.90 + 0.10 * math.sin(now.timestamp() / 43)
        solar = 6.5 * daylight**1.5 * weather * cloud
        load = 1.25 + 0.32 * math.sin(now.timestamp() / 31)
        load += 0.85 * math.exp(-(((hour - 19) / 2.0) ** 2))
        load += 0.45 * math.exp(-(((hour - 8) / 1.5) ** 2))
        surplus = solar - load
        dt_hours = seconds / 3600
        if surplus > 0 and self.soc < 95:
            battery = -min(surplus, 3.5)
            if dt_hours:
                battery = max(battery, -(95 - self.soc) / 100 * 10 / (0.95 * dt_hours))
        elif surplus < 0 and self.soc > 10:
            # Leave a visible grid contribution while supplying part of the load.
            battery = min(-surplus * 0.65, 3.5)
            if dt_hours:
                battery = min(battery, (self.soc - 10) / 100 * 10 * 0.95 / dt_hours)
        else:
            battery = 0.0
        grid = load - solar - battery
        self.soc += (-battery * 0.95 if battery < 0 else -battery / 0.95) * dt_hours / 10 * 100
        self.soc = min(95.0, max(10.0, self.soc))
        increments = [max(grid, 0), max(-grid, 0), solar, max(-battery, 0), max(battery, 0), load]
        for key, power in zip(ENERGY_KEYS, increments):
            self.totals[key] += power * dt_hours
        self.values = {
            **self.totals,
            "battery_soc": self.soc,
            "solar_power": solar,
            "battery_power": battery,
            "load_power": load,
            "grid_power": grid,
        }
        return dict(self.values)

    def state(self) -> dict:
        return {"battery_soc": self.soc, "totals": self.totals}

    def registers(self) -> dict[int, int]:
        words = {
            0x2000 + i: word
            for i, word in enumerate([0x0641, 0x0641, 0x0641, 0x0652, 1, 0, 0, 1000, 1000, 1])
        }
        for i, word in enumerate(struct.unpack(">12H", SERIAL.encode().ljust(24, b"\0"))):
            words[0xC102 + i] = word
        for address, key in FLOAT_ADDRESSES.items():
            value = self.values[key]
            if key in DEVICE_SIGN_FLIPPED:
                value = -value
            high, low = struct.unpack(">2H", struct.pack(">f", value))
            words[address], words[address + 1] = high, low
        return words


class InverterServer:
    def __init__(self, model: InverterModel, allowed_peers: set[str], unit_id: int = 1):
        self.model, self.allowed_peers, self.unit_id = model, allowed_peers, unit_id
        self.requests = 0
        self.connections = 0
        self.writers: set[asyncio.StreamWriter] = set()

    async def close(self):
        for writer in list(self.writers):
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername")
        if not peer or peer[0] not in self.allowed_peers:
            writer.close()
            return
        self.writers.add(writer)
        self.connections += 1
        try:
            while True:
                async with asyncio.timeout(120):
                    header = await reader.readexactly(7)
                    transaction, protocol, length, unit = struct.unpack(">HHHB", header)
                    if protocol != 0 or not 2 <= length <= 254:
                        return
                    pdu = await reader.readexactly(length - 1)
                self.requests += 1
                function = pdu[0]
                error = 0
                if unit != self.unit_id:
                    error = 0x0B
                elif function != 3:
                    error = 1
                elif len(pdu) != 5:
                    error = 3
                else:
                    address, count = struct.unpack(">HH", pdu[1:])
                    words = self.model.registers()
                    if not 1 <= count <= 125 or address + count > 65536:
                        error = 3
                    elif any(address + i not in words for i in range(count)):
                        error = 2
                if error:
                    response = bytes([function | 0x80, error])
                else:
                    response = bytes([3, count * 2]) + struct.pack(
                        f">{count}H", *(words[address + i] for i in range(count))
                    )
                writer.write(
                    struct.pack(">HHHB", transaction, 0, len(response) + 1, unit) + response
                )
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, TimeoutError):
            pass
        finally:
            self.writers.discard(writer)
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()


def atomic_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


async def run(args):
    from zeroconf import IPVersion
    from zeroconf.asyncio import AsyncZeroconf

    from tools.discovery_advertisement import service_info

    state = json.loads(args.state.read_text(encoding="utf-8")) if args.state.exists() else None
    model = InverterModel(state)
    model.step(datetime.now(LOCAL_TIMEZONE), 0)
    peer = InverterServer(model, {args.allow_peer, args.bind, "127.0.0.1"})
    server = await asyncio.start_server(peer.handle, args.bind, args.port)
    zc = AsyncZeroconf(interfaces=[args.bind], ip_version=IPVersion.V4Only)
    info = service_info(f"LOGGER-{SERIAL}", args.bind, args.port)
    registered = False
    try:
        await zc.async_register_service(info)
        registered = True
        LOG.info("Modbus TCP ready on %s:%s; mDNS registered", args.bind, args.port)
        previous = time.monotonic()
        tick = 0
        while True:
            await asyncio.sleep(1)
            current = time.monotonic()
            model.step(datetime.now(LOCAL_TIMEZONE), current - previous)
            previous = current
            tick += 1
            if tick % 5 == 0:
                atomic_json(args.state, model.state())
                atomic_json(
                    args.status,
                    {
                        "pid": os.getpid(),
                        "bind": args.bind,
                        "port": args.port,
                        "service": info.name,
                        "updated_at": datetime.now(LOCAL_TIMEZONE).isoformat(),
                        "requests": peer.requests,
                        "connections": peer.connections,
                        "values": model.values,
                    },
                )
    finally:
        atomic_json(args.state, model.state())
        if registered:
            await zc.async_unregister_service(info)
        await zc.async_close()
        server.close()
        await peer.close()
        await server.wait_closed()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", required=True)
    parser.add_argument("--port", type=int, default=15020)
    parser.add_argument("--allow-peer", required=True)
    parser.add_argument("--state", type=Path, default=Path("local_data/inverter-state.json"))
    parser.add_argument("--status", type=Path, default=Path("local_data/inverter-status.json"))
    options = parser.parse_args()
    if not 1 <= options.port <= 65535:
        parser.error("port must be between 1 and 65535")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(run(options))
    except KeyboardInterrupt:
        pass
