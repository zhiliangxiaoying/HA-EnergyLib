"""Discovery test peer: identity injection, TXT variants and CLI validation."""

import asyncio
import contextlib
import io
import unittest
from datetime import datetime, timezone

from energylib_under_test.protocol.device import EnergyLibDevice
from energylib_under_test.protocol.discovery import parse_discovery
from energylib_under_test.protocol.modbus import ModbusTcpClient

from tools.inverter_simulator import SERIAL, InverterServer
from tools.mdns_peer import PeerModel, parse_args, txt_properties


class PeerIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_custom_serial_is_read_over_modbus(self):
        model = PeerModel("ELIB260199")
        model.step(datetime.now(timezone.utc), 0)
        peer = InverterServer(model, {"127.0.0.1"})
        server = await asyncio.start_server(peer.handle, "127.0.0.1", 0)
        device = EnergyLibDevice(
            ModbusTcpClient("127.0.0.1", server.sockets[0].getsockname()[1], 1)
        )
        try:
            identity = await device.identify()
            snapshot = await device.read_snapshot(identity.serial)
            self.assertEqual(identity.serial, "ELIB260199")
            self.assertNotEqual(identity.serial, SERIAL)
            self.assertNotIn(None, snapshot.values())
        finally:
            await device.close()
            server.close()
            await server.wait_closed()
            await peer.close()

    def test_invalid_serial_rejected(self):
        for serial in ("", "HAS SPACE", "X" * 25, "非ASCII"):
            with self.subTest(serial=serial), self.assertRaises(ValueError):
                PeerModel(serial)


class TxtTests(unittest.TestCase):
    def test_default_txt_is_accepted_by_integration(self):
        props = txt_properties("ELIB260101")
        self.assertEqual(props["id"], "ELIB260101")
        self.assertEqual(parse_discovery("192.0.2.10", 15030, props).advertised_id, "ELIB260101")

    def test_negative_variants(self):
        mismatch = txt_properties("ELIB260103", txt_id="ELIB999999")
        self.assertEqual(parse_discovery("192.0.2.10", 1, mismatch).advertised_id, "ELIB999999")
        self.assertNotIn("id", txt_properties("ELIB260105", omit_id=True))
        for props in (
            txt_properties("ELIB260104", auth="token"),
            txt_properties("ELIB260106", transport="http"),
        ):
            with self.subTest(props=props), self.assertRaises(ValueError):
                parse_discovery("192.0.2.10", 15030, props)


class CliTests(unittest.TestCase):
    def test_arguments(self):
        base = ["--serial", "ELIB1", "--bind", "192.0.2.10", "--allow-peer", "192.0.2.20"]
        self.assertEqual(parse_args(base + ["--port", "15030"]).port, 15030)
        for extra in (["--port", "0"], ["--port", "1", "--txt-id", "X", "--no-txt-id"]):
            with (
                self.subTest(extra=extra),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                parse_args(base + extra)
