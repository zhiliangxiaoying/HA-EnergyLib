"""EV charger behind a Sinexcel inverter: probing, readings, alarms and settings."""

import asyncio
import unittest
from datetime import datetime, timezone

from energylib_under_test.energylib_core.legacy import decode_u32
from energylib_under_test.energylib_core.model.points import CONTROLS, MEASUREMENTS
from energylib_under_test.energylib_core.profiles import sinexcel_v2 as sx
from energylib_under_test.energylib_core.runtime import ControlError, DeviceRuntime
from energylib_under_test.energylib_core.transport.modbus_tcp import (
    InvalidResponse,
    ModbusTcpClient,
)

from tools.sinexcel_simulator import BenchConfig, BenchModel, BenchServer

EV_READINGS = {
    "ev_charge_power",
    "ev_charger_status",
    "ev_charger_energy",
    "ev_session_energy",
    "ev_session_duration",
    "ev_charger_meter_online",
    "ev_charger_voltage_a",
    "ev_charger_current_a",
}
EV_CONTROLS = {c.key for c in CONTROLS if c.key.startswith("ev_")}


class DecodeTests(unittest.TestCase):
    def test_u32_is_high_word_first(self):
        self.assertEqual(decode_u32([0x0001, 0x0002]), 65538.0)
        with self.assertRaises(InvalidResponse):
            decode_u32([1])

    def test_plug_state_is_lsb_first_and_charging_wins(self):
        profile = sx.SinexcelProfile()

        def status(plug, charging=False):
            bits = [bool(plug >> i & 1) for i in range(3)] + [False] * 8 + [charging]
            return profile.decode_enum("ev_charger_status", bits)

        self.assertEqual(status(0), "idle")
        self.assertEqual(status(1), "waiting_for_vehicle")
        self.assertEqual(status(2), "connected")
        self.assertEqual(status(5), "other")
        self.assertEqual(status(2, charging=True), "charging")

    def test_charge_mode_codes(self):
        profile = sx.SinexcelProfile()
        self.assertEqual(profile.decode_enum("ev_charge_mode", 2.0), "smart")
        self.assertIsNone(profile.decode_enum("ev_charge_mode", 7.0))
        self.assertIsNone(profile.decode_enum("ev_charge_mode", 1.5))
        self.assertEqual(profile.control_values("ev_charge_mode", "green"), 3.0)
        with self.assertRaises(InvalidResponse):
            profile.control_values("ev_charge_mode", "turbo")

    def test_main_entities_on_by_default_others_off(self):
        self.assertTrue(MEASUREMENTS["ev_charge_power"].enabled)
        self.assertTrue(MEASUREMENTS["ev_charger_status"].enabled)
        for key in EV_READINGS - {"ev_charge_power", "ev_charger_status"}:
            self.assertFalse(MEASUREMENTS[key].enabled, key)
        controls = {c.key: c for c in CONTROLS}
        self.assertFalse(controls["ev_charger_on"].advanced)
        self.assertFalse(controls["ev_charge_current"].advanced)
        for key in EV_CONTROLS - {"ev_charger_on", "ev_charge_current"}:
            self.assertTrue(controls[key].advanced, key)


class BenchCase(unittest.IsolatedAsyncioTestCase):
    charger = True

    async def asyncSetUp(self):
        self.now = 1000.0
        self.model = BenchModel(
            BenchConfig(writes=True, charger=self.charger), clock=lambda: self.now
        )
        self.model.tick(datetime(2026, 10, 19, 12, tzinfo=timezone.utc), 0)
        self.peer = BenchServer(self.model, {"127.0.0.1"})
        self.server = await asyncio.start_server(self.peer.handle, "127.0.0.1", 0)
        self.client = ModbusTcpClient(
            "127.0.0.1", self.server.sockets[0].getsockname()[1], 1, timeout=0.2
        )
        self.runtime = DeviceRuntime(self.client, "sinexcel_v2", clock=lambda: self.now)
        await self.runtime.initialize()

    async def asyncTearDown(self):
        await self.runtime.close()
        self.server.close()
        await self.server.wait_closed()
        await self.peer.close()

    def keys(self):
        return {p.key for p in self.runtime.points()}

    async def reprobe(self):
        self.client.generation += 1
        await self.runtime.refresh(set(), force=True)


class NoChargerTests(BenchCase):
    charger = False

    async def test_no_charger_entities_or_controls(self):
        self.runtime.allow_control = True
        self.assertFalse(self.keys() & EV_READINGS)
        self.assertFalse(self.runtime.profile.control_keys & EV_CONTROLS)
        self.assertNotIn("charger", self.runtime.profile.capabilities)


class ChargerReadTests(BenchCase):
    async def test_capabilities_and_points(self):
        caps = self.runtime.profile.capabilities
        self.assertTrue({"charger", "charger_status", "charger_alarms"} <= caps)
        self.assertNotIn("charger_three_phase", caps)
        self.assertTrue(EV_READINGS <= self.keys())
        self.assertNotIn("ev_charger_voltage_b", self.keys())  # single-phase charger

    async def test_readings(self):
        values = await self.runtime.refresh(EV_READINGS)
        self.assertAlmostEqual(values["ev_charge_power"], 230 * 16 / 1000, places=3)  # W -> kW
        self.assertEqual(values["ev_charger_status"], "charging")
        self.assertIs(values["ev_charger_meter_online"], True)
        self.assertAlmostEqual(values["ev_charger_voltage_a"], 230, places=3)
        self.assertAlmostEqual(values["ev_charger_current_a"], 16, places=3)
        self.assertEqual(values["ev_session_duration"], 0)
        self.model.tick_charger(3600)
        self.now += 60
        values = await self.runtime.refresh(EV_READINGS)
        self.assertEqual(values["ev_session_duration"], 3600)
        self.assertAlmostEqual(values["ev_session_energy"], 3.68, places=2)

    async def test_status_follows_plug_and_charging_bits(self):
        self.model.set_u32(0x392A, 0)  # charger off: the car stays connected
        self.model.tick_charger(0)
        self.now += 60
        values = await self.runtime.refresh({"ev_charger_status", "ev_charge_power"})
        self.assertEqual(values["ev_charger_status"], "connected")
        self.assertEqual(values["ev_charge_power"], 0)
        self.model.bits[1][0x2030], self.model.bits[1][0x2031] = True, False
        self.now += 60
        values = await self.runtime.refresh({"ev_charger_status"})
        self.assertEqual(values["ev_charger_status"], "waiting_for_vehicle")

    async def test_charger_alarms_join_the_alarm_summary(self):
        values = await self.runtime.refresh({"alarm_status"})
        self.assertEqual(values["alarm_status"], "normal")
        self.model.bits[1][0x2001] = True  # emergency stop
        self.now += 60
        values = await self.runtime.refresh({"alarm_status", "fault_count"})
        self.assertEqual(values["alarm_status"], "abnormal")
        self.assertEqual(values["fault_count"], 1)
        alarm = self.runtime.attributes["alarm_status"]["alarms"][0]
        self.assertEqual(
            (alarm["code"], alarm["name"], alarm["severity"]), ("2001", "充电桩急停保护", "fault")
        )
        self.model.bits[1][0x2001] = False
        self.model.bits[1][0x2008] = True  # reserved bit: reported, never guessed
        self.now += 60
        values = await self.runtime.refresh({"alarm_status"})
        self.assertEqual(values["alarm_status"], "unrecognized")

    async def test_three_phase_charger_adds_phase_b_and_c(self):
        self.model.set_u32(0x398A, 3)
        await self.reprobe()
        self.assertIn("charger_three_phase", self.runtime.profile.capabilities)
        self.assertTrue({"ev_charger_voltage_c", "ev_charger_current_b"} <= self.keys())

    async def test_disabled_in_inverter_means_no_charger(self):
        self.model.set_float(0x31BE, 0)
        self.now += 600
        await self.runtime.refresh({"solar_power"})
        self.assertTrue(self.runtime.capabilities_changed)
        self.assertFalse(self.keys() & EV_READINGS)


class ChargerControlTests(BenchCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.runtime.allow_control = True
        await self.runtime.refresh(set(), force=True)

    async def test_all_settings_offered_and_read(self):
        self.assertEqual(self.runtime.profile.control_keys & EV_CONTROLS, EV_CONTROLS)
        controls = self.runtime.controls
        self.assertEqual(controls["ev_charger_on"], 1)
        self.assertEqual(controls["ev_charge_current"], 16)
        self.assertEqual(controls["ev_charge_mode"], "eco")
        self.assertEqual(controls["ev_plug_and_charge"], 1)
        self.assertEqual(controls["ev_grid_max_power"], 0)

    async def test_float_and_u32_writes_with_read_back(self):
        await self.runtime.set_control("ev_charge_current", 10)
        self.assertEqual(self.model.get_float(0x3924), 10)
        self.assertEqual(self.runtime.controls["ev_charge_current"], 10)
        await self.runtime.set_control("ev_charger_on", False)
        self.assertEqual(self.model.get_u32(0x392A), 0)  # U32, not a float 0.0
        self.assertEqual(self.runtime.controls["ev_charger_on"], 0)
        await self.runtime.set_control("ev_green_priority", True)
        self.assertEqual(self.model.get_u32(0x39BC), 1)
        await self.runtime.set_control("ev_charge_mode", "smart")
        self.assertEqual(self.model.get_float(0x31C0), 2)
        self.assertEqual(self.runtime.controls["ev_charge_mode"], "smart")

    async def test_out_of_range_is_rejected_without_a_write(self):
        writes = self.peer.write_count
        for key, value in (
            ("ev_charge_current", 33),
            ("ev_charge_current", 5),
            ("ev_grid_max_power", 1000.1),
            ("ev_charge_mode", "turbo"),
        ):
            with self.subTest(key=key, value=value), self.assertRaises(ControlError):
                await self.runtime.set_control(key, value)
        self.assertEqual(self.peer.write_count, writes)

    async def test_unknown_device_value_hides_only_that_setting(self):
        self.model.set_float(0x31C0, 7)  # a mode this profile has no name for
        self.model.set_u32(0x3992, 2)  # not a switch state
        self.now += 60
        await self.runtime.refresh(set())
        self.assertNotIn("ev_charge_mode", self.runtime.controls)
        self.assertNotIn("ev_plug_and_charge", self.runtime.controls)
        self.assertEqual(self.runtime.controls["ev_charge_current"], 16)
        self.assertIn("work_mode", self.runtime.controls)  # inverter controls unaffected

    async def test_charger_writes_are_single_send(self):
        self.assertFalse({a for a, _ in sx.CHARGER_CONTROLS.values()} & sx.RETRY_SAFE_ADDRESSES)


if __name__ == "__main__":
    unittest.main()
