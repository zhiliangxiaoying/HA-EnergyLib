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

    async def test_idle_connection_can_be_closed_before_waiting_for_server(self):
        peer = InverterServer(PeerModel("CLEANUP-TEST"), {"127.0.0.1"})
        server = await asyncio.start_server(peer.handle, "127.0.0.1", 0)
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", server.sockets[0].getsockname()[1]
        )
        try:
            await asyncio.sleep(0)
            server.close()
            await asyncio.wait_for(peer.close(), 1)
            await asyncio.wait_for(server.wait_closed(), 1)
            self.assertEqual(await asyncio.wait_for(reader.read(), 1), b"")
        finally:
            writer.close()
            await writer.wait_closed()


class TxtTests(unittest.TestCase):
    def test_default_txt_is_accepted_by_integration(self):
        props = txt_properties("ELIB260101")
        self.assertEqual(props["sn"], "ELIB260101")
        self.assertEqual(parse_discovery("192.0.2.10", 15030, props).logger_sn, "ELIB260101")

    def test_negative_variants(self):
        self.assertNotIn("id", txt_properties("LOGGER-001"))
        for props in (
            txt_properties("ELIB260105", omit=("sn",)),
            txt_properties("LOGGER-001", overrides={"unit_id": "256"}),
            txt_properties("LOGGER-001", api_ver="2"),
            txt_properties("ELIB260104", auth="token"),
            txt_properties("ELIB260106", transport="http"),
        ):
            with self.subTest(props=props), self.assertRaises(ValueError):
                parse_discovery("192.0.2.10", 15030, props)


class CliTests(unittest.TestCase):
    def test_arguments(self):
        base = ["--serial", "ELIB1", "--bind", "192.0.2.10", "--allow-peer", "192.0.2.20"]
        self.assertEqual(parse_args(base + ["--port", "15030"]).port, 15030)
        parsed = parse_args(
            base + ["--port", "15030", "--unit-id", "7", "--logger-sn", "LOGGER-001"]
        )
        self.assertEqual(parsed.unit_id, 7)
        self.assertEqual(parsed.logger_sn, "LOGGER-001")
        for extra in (
            ["--port", "0"],
            ["--port", "1", "--unit-id", "256"],
            ["--port", "1", "--txt", "bad"],
        ):
            with (
                self.subTest(extra=extra),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                parse_args(base + extra)
