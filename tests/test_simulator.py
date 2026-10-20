"""Check physical consistency and read-only behavior of the development peer."""

import asyncio
import json
import struct
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from energylib_under_test.protocol.device import EnergyLibDevice
from energylib_under_test.protocol.modbus import ModbusTcpClient

from tools.inverter_simulator import (
    DEVICE_SIGN_FLIPPED,
    SERIAL,
    InverterModel,
    InverterServer,
    atomic_json,
)


class ModelTests(unittest.TestCase):
    def test_day_night_conservation_and_monotonic_energy(self):
        model = InverterModel()
        now = datetime(2026, 10, 19, tzinfo=timezone(timedelta(hours=8)))
        previous = dict(model.totals)
        for index in range(0, 24 * 60):
            values = model.step(now + timedelta(minutes=index), 5)
            self.assertAlmostEqual(
                values["solar_power"] + values["grid_power"] + values["battery_power"],
                values["load_power"],
            )
            self.assertTrue(10 <= values["battery_soc"] <= 95)
            for key, total in model.totals.items():
                self.assertGreaterEqual(total, previous[key])
            previous = dict(model.totals)

    def test_persistence_and_no_offline_backfill(self):
        model = InverterModel()
        now = datetime(2026, 10, 19, 12, tzinfo=timezone(timedelta(hours=8)))
        model.step(now, 5)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            atomic_json(path, model.state())
            restored = InverterModel(json.loads(path.read_text()))
        restored.step(now + timedelta(days=2), 0)
        self.assertEqual(restored.totals, model.totals)
        self.assertEqual(restored.soc, model.soc)

    def test_distinct_days_and_invalid_saved_state(self):
        model = InverterModel()
        day = datetime(2026, 10, 19, 12, tzinfo=timezone(timedelta(hours=8)))
        first = model.step(day, 0)["solar_power"]
        second = model.step(day + timedelta(days=1), 0)["solar_power"]
        self.assertNotEqual(first, second)
        state = model.state()
        state["battery_soc"] = float("nan")
        with self.assertRaises(ValueError):
            InverterModel(state)


class SimulatorTcpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.model = InverterModel()
        self.model.step(datetime.now(timezone.utc), 0)
        self.peer = InverterServer(self.model, {"127.0.0.1"})
        self.server = await asyncio.start_server(self.peer.handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()
        await self.peer.close()

    async def test_full_identity_and_all_11_points_with_real_client(self):
        device = EnergyLibDevice(ModbusTcpClient("127.0.0.1", self.port, 1))
        try:
            identity = await device.identify()
            snapshot = await device.read_snapshot(identity.serial)
            self.assertEqual(identity.serial, SERIAL)
            self.assertEqual(len(snapshot), 11)
            self.assertNotIn(None, snapshot.values())
            for key, value in snapshot.items():
                # The raw v0.1 reader shows the device sign (Sinexcel convention).
                sign = -1 if key in DEVICE_SIGN_FLIPPED else 1
                self.assertAlmostEqual(value, sign * self.model.values[key], places=3)
        finally:
            await device.close()

    async def test_write_is_rejected_and_values_are_unchanged(self):
        before = dict(self.model.values)
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        pdu = b"\x10\x12\x12\x00\x02\x04\x00\x00\x00\x00"
        try:
            writer.write(struct.pack(">HHHB", 1, 0, len(pdu) + 1, 1) + pdu)
            await writer.drain()
            response = await reader.readexactly(9)
            self.assertEqual(response[-2:], b"\x90\x01")
            self.assertEqual(self.model.values, before)
        finally:
            writer.close()
            await writer.wait_closed()
