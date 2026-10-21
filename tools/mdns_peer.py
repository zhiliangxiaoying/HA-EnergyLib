"""Discovery test peer: v0.1 read-only registers with configurable SN, port and TXT.

Reuses the inverter_simulator model and server unchanged, so the showcase
process keeps its contract. Each instance can advertise its own identity, or
deliberately wrong metadata, to exercise HA's zeroconf flow:

    python -m tools.mdns_peer --serial ELIB260101 --bind 192.0.2.10 --port 15030 \
        --allow-peer 192.0.2.20 --logger-sn LOGGER-001 [--omit-field sn] [--auth token]

Development peer only; never evidence of real device compatibility.
"""

import argparse
import asyncio
import json
import logging
import struct
import time
from datetime import datetime
from pathlib import Path

from tools.discovery_advertisement import service_info, txt_properties
from tools.inverter_simulator import (
    LOCAL_TIMEZONE,
    InverterModel,
    InverterServer,
    atomic_json,
)

LOG = logging.getLogger(__name__)


class PeerModel(InverterModel):
    """Same physics as the showcase model, but reports its own serial number."""

    def __init__(self, serial: str, state: dict | None = None) -> None:
        if not serial or len(serial) > 24 or not all(0x21 <= ord(c) <= 0x7E for c in serial):
            raise ValueError("Serial must be 1-24 printable ASCII characters without spaces")
        super().__init__(state)
        self.serial = serial

    def registers(self) -> dict[int, int]:
        words = super().registers()
        padded = self.serial.encode("ascii").ljust(24, b"\0")
        for i, word in enumerate(struct.unpack(">12H", padded)):
            words[0xC102 + i] = word
        return words


async def run(args):
    from zeroconf import IPVersion
    from zeroconf.asyncio import AsyncZeroconf

    state = None
    if args.state is not None and args.state.exists():
        state = json.loads(args.state.read_text(encoding="utf-8"))
    model = PeerModel(args.serial, state)
    model.step(datetime.now(LOCAL_TIMEZONE), 0)
    peer = InverterServer(model, {args.allow_peer, args.bind, "127.0.0.1"}, unit_id=args.unit_id)
    server = await asyncio.start_server(peer.handle, args.bind, args.port)
    zc = AsyncZeroconf(interfaces=[args.bind], ip_version=IPVersion.V4Only)
    logger_sn = args.logger_sn or f"LOGGER-{args.serial}"
    properties = txt_properties(
        logger_sn,
        unit_id=None if args.no_txt_unit_id else args.unit_id,
        omit=args.omit_field,
        overrides=dict(item.split("=", 1) for item in args.txt),
        auth=args.auth,
        transport=args.transport,
        api_ver=args.api_ver,
    )
    info = service_info(logger_sn, args.bind, args.port, properties=properties)
    registered = False
    try:
        await zc.async_register_service(info, allow_name_change=True)
        registered = True
        LOG.info("Registered test peer on %s:%s", args.bind, args.port)
        previous = time.monotonic()
        tick = 0
        while True:
            await asyncio.sleep(1)
            current = time.monotonic()
            model.step(datetime.now(LOCAL_TIMEZONE), current - previous)
            previous = current
            tick += 1
            if args.state is not None and tick % 5 == 0:
                atomic_json(args.state, model.state())
    finally:
        if args.state is not None:
            atomic_json(args.state, model.state())
        if registered:
            await zc.async_unregister_service(info)
        await zc.async_close()
        server.close()
        await peer.close()
        await server.wait_closed()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--bind", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--allow-peer", required=True)
    parser.add_argument("--logger-sn", help="Independent logger SN; default LOGGER-<serial>")
    parser.add_argument("--unit-id", type=int, default=1)
    parser.add_argument("--no-txt-unit-id", action="store_true")
    parser.add_argument("--omit-field", action="append", default=[])
    parser.add_argument(
        "--txt", action="append", default=[], help="Override a TXT key=value for fault tests"
    )
    parser.add_argument("--api-ver", default="1")
    parser.add_argument("--auth", default="none", help="Non-'none' tests rejection")
    parser.add_argument("--transport", default="modbus_tcp", help="Other values test rejection")
    parser.add_argument(
        "--state", type=Path, help="Persist energy counters/SOC so restarts do not reset them"
    )
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if not 1 <= args.unit_id <= 255 or any("=" not in item for item in args.txt):
        parser.error("unit ID must be 1-255 and TXT overrides must be key=value")
    return args


if __name__ == "__main__":
    options = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(run(options))
    except KeyboardInterrupt:
        pass
