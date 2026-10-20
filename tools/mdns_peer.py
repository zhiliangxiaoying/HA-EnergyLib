"""Discovery test peer: v0.1 read-only registers with configurable SN, port and TXT.

Reuses the inverter_simulator model and server unchanged, so the showcase
process keeps its contract. Each instance can advertise its own identity, or
deliberately wrong metadata, to exercise HA's zeroconf flow:

    python -m tools.mdns_peer --serial ELIB260101 --bind 192.0.2.10 --port 15030 \
        --allow-peer 192.0.2.20 [--txt-id OTHER | --no-txt-id] [--auth token]

Development peer only; never evidence of real device compatibility.
"""

import argparse
import asyncio
import json
import logging
import socket
import struct
import time
from datetime import datetime
from pathlib import Path

from tools.inverter_simulator import (
    LOCAL_TIMEZONE,
    SERVICE,
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


def txt_properties(serial, *, txt_id=None, omit_id=False, auth="none", transport="modbus_tcp"):
    """TXT record per docs/DISCOVERY.md; overrides allow negative tests."""
    properties = {"transport": transport, "api_ver": "1", "auth": auth}
    if not omit_id:
        properties["id"] = txt_id or serial
    return properties


async def run(args):
    from zeroconf import IPVersion, ServiceInfo
    from zeroconf.asyncio import AsyncZeroconf

    state = None
    if args.state is not None and args.state.exists():
        state = json.loads(args.state.read_text(encoding="utf-8"))
    model = PeerModel(args.serial, state)
    model.step(datetime.now(LOCAL_TIMEZONE), 0)
    peer = InverterServer(model, {args.allow_peer, args.bind, "127.0.0.1"})
    server = await asyncio.start_server(peer.handle, args.bind, args.port)
    zc = AsyncZeroconf(interfaces=[args.bind], ip_version=IPVersion.V4Only)
    properties = txt_properties(
        args.serial,
        txt_id=args.txt_id,
        omit_id=args.no_txt_id,
        auth=args.auth,
        transport=args.transport,
    )
    info = ServiceInfo(
        SERVICE,
        f"EnergyLIB-{args.serial}.{SERVICE}",
        addresses=[socket.inet_aton(args.bind)],
        port=args.port,
        properties=properties,
        server=f"energylib-{args.serial.lower()}.local.",
    )
    registered = False
    try:
        await zc.async_register_service(info)
        registered = True
        LOG.info("Registered %s on %s:%s TXT=%s", info.name, args.bind, args.port, properties)
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
        await server.wait_closed()
        await peer.close()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--bind", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--allow-peer", required=True)
    identity = parser.add_mutually_exclusive_group()
    identity.add_argument("--txt-id", help="Advertise a different id (identity-mismatch test)")
    identity.add_argument("--no-txt-id", action="store_true", help="Omit TXT id")
    parser.add_argument("--auth", default="none", help="Non-'none' tests rejection")
    parser.add_argument("--transport", default="modbus_tcp", help="Other values test rejection")
    parser.add_argument(
        "--state", type=Path, help="Persist energy counters/SOC so restarts do not reset them"
    )
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    return args


if __name__ == "__main__":
    options = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(run(options))
    except KeyboardInterrupt:
        pass
