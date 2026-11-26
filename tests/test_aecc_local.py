"""AECC local interface: transport, field mapping (provisional signs) and runtime."""

import asyncio
import json
import unittest
from unittest.mock import patch

from energylib_under_test.energylib_core import aecc_local
from energylib_under_test.energylib_core.aecc_local import AeccLocalRuntime, decode, decode_ts
from energylib_under_test.energylib_core.model.tou import TouSchedule
from energylib_under_test.energylib_core.runtime import ControlError, PartialWrite
from energylib_under_test.energylib_core.transport.aecc_local import (
    AeccLocalClient,
    AeccLocalError,
    crc16,
    rtu_read_frame,
    rtu_registers,
    rtu_write_ack,
    rtu_write_frame,
)
from energylib_under_test.energylib_core.transport.modbus_tcp import EnergyLibError

from tools import aecc_local_simulator as simulator
from tools.aecc_local_simulator import registers, scenario, serve_client


def answer(**summary):
    return {"SSumInfoList": summary, "PlugInfoList": []}


class MappingTests(unittest.TestCase):
    def test_signs_follow_the_documented_assumption(self):
        # The convention is provisional; a device must confirm it.
        self.assertFalse(aecc_local.POWER_DIRECTION_VERIFIED)
        v = decode(
            answer(
                TotalPVPower=3000,
                TotalBatteryOutputPower=-1500,
                MeterTotalActivePower=-800,
                AverageBatteryAverageSOC=64,
                TotalBackUpPower=120,
            )
        )
        self.assertEqual(v["solar_power"], 3.0)
        self.assertEqual(v["battery_power"], -1.5)  # charging
        self.assertEqual(v["grid_power"], -0.8)  # selling
        self.assertAlmostEqual(v["load_power"], 0.7)  # 3.0 - 1.5 - 0.8
        self.assertEqual(v["battery_total_soc"], 64)
        self.assertEqual(v["backup_load_power"], 0.12)

    def test_grid_output_is_used_without_a_meter(self):
        v = decode(answer(TotalPVPower=1000, TotalBatteryOutputPower=0, TotalGridOutputPower=400))
        self.assertEqual(v["grid_power"], -0.4)  # output to the grid = selling
        self.assertAlmostEqual(v["load_power"], 0.6)

    def test_missing_or_invalid_values_are_unknown_not_zero(self):
        v = decode(answer(TotalPVPower="n/a", AverageBatteryAverageSOC=140))
        self.assertIsNone(v["solar_power"])
        self.assertIsNone(v["battery_total_soc"])
        self.assertIsNone(v["load_power"])  # never derived from a missing part
        self.assertTrue(all(value is None for value in decode({}).values()))
        self.assertIsNone(decode({"SSumInfoList": "broken"})["grid_power"])

    def test_separate_charge_power_is_subtracted(self):
        # The document lists battery output and battery charge as two fields.
        v = decode(
            answer(
                TotalPVPower=3000,
                TotalBatteryOutputPower=0,
                TotalChargePower=1200,
                MeterTotalActivePower=-500,
            )
        )
        self.assertEqual(v["battery_power"], -1.2)
        self.assertAlmostEqual(v["load_power"], 1.3)
        # A signed output is not reduced a second time.
        v = decode(answer(TotalBatteryOutputPower=-900, TotalChargePower=900))
        self.assertEqual(v["battery_power"], -0.9)

    def test_shenggerui_registers(self):
        regs = registers(0)
        blocks = {
            start: [regs.get(start + i, 0) for i in range(count)]
            for start, count in aecc_local.TS_LIVE_BLOCKS + aecc_local.TS_ENERGY_BLOCKS
        }
        v = decode_ts(blocks)
        s = scenario(0)["SSumInfoList"]
        self.assertEqual(v["battery_voltage"], 53.2)
        self.assertLess(v["battery_current"], 0)  # current > 0 discharging: this one charges
        self.assertLess(v["battery_power"], 0)
        self.assertAlmostEqual(v["battery_power"], -s["TotalChargePower"] / 1000, delta=0.01)
        self.assertEqual(v["solar_power"], s["TotalPVPower"] / 1000)
        self.assertEqual(v["grid_power"], s["MeterTotalActivePower"] / 1000)  # CT > 0 buying
        # The sources balance the measured load (AC OUT + CT normal load).
        self.assertAlmostEqual(
            v["solar_power"] + v["battery_power"] + v["grid_power"], v["load_power"], delta=0.01
        )
        self.assertEqual(v["load_power"], v["backup_load_power"])
        self.assertEqual(v["battery_total_soc"], 64)
        self.assertEqual(v["running_state"], "on_grid")  # inverter state with grid voltage
        self.assertNotIn("active_alarms", v)  # alarms come from the tracker only
        self.assertEqual(v["grid_frequency"], 50.0)
        self.assertGreater(v["solar_energy"], 2310)  # u32, low word first, 0.1 kWh
        self.assertEqual(v["grid_import_energy"], 230.8)
        self.assertEqual(v["grid_export_energy"], 95.4)
        # A block that failed leaves its values unknown, never zero.
        blocks[0xCB52] = None
        blocks[0x7584] = None
        v = decode_ts(blocks)
        self.assertIsNone(v["solar_energy"])
        self.assertIsNone(v["grid_power"])
        self.assertEqual(v["grid_import_energy"], 230.8)
        self.assertEqual(v["battery_voltage"], 53.2)

    def test_shenggerui_states_and_alarms(self):
        def state(startup, volts=2300, alarms=(0,) * 8):
            blocks = {0x756A: [volts, 0, 5000], 0x7594: [startup, 0], 0x75A5: list(alarms)}
            return decode_ts(blocks)

        self.assertEqual(state(1)["running_state"], "standby")
        self.assertEqual(state(2)["running_state"], "on_grid")
        self.assertEqual(state(3)["running_state"], "on_grid")
        self.assertEqual(state(3, volts=0)["running_state"], "off_grid")
        self.assertEqual(state(0)["running_state"], "other")  # initialising
        self.assertIsNone(decode_ts({})["running_state"])

    def test_rtu_frames(self):
        # Slave 1, read 3 registers from 0xFE06. The document's request example
        # shows "56 67" as CRC, which is not the Modbus CRC of these bytes; its
        # answer example ("... 2F CE") and the classic frame below are.
        self.assertEqual(rtu_read_frame(1, 0xFE06, 3).hex(" ").upper(), "01 03 FE 06 00 03 D4 22")
        self.assertEqual(rtu_read_frame(1, 0, 10).hex(" ").upper(), "01 03 00 00 00 0A C5 CD")
        answer_example = bytes.fromhex("01 03 06 00 01 02 03 04 05 2F CE")
        self.assertEqual(rtu_registers(answer_example, 1, 3), [1, 0x0203, 0x0405])
        body = answer_example[:-2]
        with self.assertRaises(AeccLocalError):
            rtu_registers(body + b"\x00\x00", 1, 3)  # bad CRC
        other = bytes.fromhex("02 03 02 00 01")
        with self.assertRaises(AeccLocalError):
            rtu_registers(other + crc16(other), 1, 1)  # another slave
        exception = bytes.fromhex("01 83 02")
        with self.assertRaisesRegex(AeccLocalError, "exception 2"):
            rtu_registers(exception + crc16(exception), 1, 1)

    def test_summary_as_a_list_is_accepted(self):
        v = decode({"SSumInfoList": [{"TotalPVPower": "2500"}]})
        self.assertEqual(v["solar_power"], 2.5)

    def test_simulator_scenario_balances(self):
        v = decode(scenario(0))
        self.assertGreater(v["solar_power"], 0)
        self.assertLess(v["battery_power"], 0)
        self.assertGreater(v["load_power"], 0)


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = await asyncio.start_server(serve_client, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()

    async def test_reads_the_summary_and_counts_requests(self):
        client = AeccLocalClient("127.0.0.1", self.port)
        try:
            first = await client.energy_parameters()
            second = await client.energy_parameters()
        finally:
            await client.close()
        self.assertIn("SSumInfoList", first)
        self.assertEqual((first["SerialNumber"], second["SerialNumber"]), (1, 2))
        self.assertEqual(client.requests, 2)

    async def test_pass_through_and_control_parameters(self):
        client = AeccLocalClient("127.0.0.1", self.port)
        try:
            values = await client.read_registers(1, 30000, 3)
            controls = await client.control_parameters(
                ["ScheduledModeEnabled", "DeviceGridInputPowerLimit"]
            )
            with self.assertRaises(AeccLocalError):
                await client.read_registers(7, 30000, 3)  # nobody at slave 7
        finally:
            await client.close()
        self.assertEqual(values[0], 532)
        self.assertEqual(
            controls, {"ScheduledModeEnabled": "0", "DeviceGridInputPowerLimit": "800"}
        )

    async def test_an_answer_to_an_earlier_request_is_skipped(self):
        async def late(reader, writer):
            line = await reader.readline()
            serial = json.loads(line)["SerialNumber"]
            # A late answer to an earlier request arrives first, in the same packet.
            writer.write(
                json.dumps(
                    {"SerialNumber": serial - 1, "SSumInfoList": {"TotalPVPower": 1}}
                ).encode()
                + json.dumps({"SerialNumber": serial, "SSumInfoList": {"TotalPVPower": 2}}).encode()
            )
            await writer.drain()
            await reader.readline()

        server = await asyncio.start_server(late, "127.0.0.1", 0)
        client = AeccLocalClient("127.0.0.1", server.sockets[0].getsockname()[1])
        try:
            value = await client.energy_parameters()
        finally:
            await client.close()
            server.close()
        self.assertEqual(value["SSumInfoList"]["TotalPVPower"], 2)
        self.assertEqual(client.filtered_push_frames, 1)

    async def test_answer_split_across_reads_and_without_newline(self):
        body = json.dumps(answer(TotalPVPower=1234, Name="光伏")).encode()

        async def slow(reader, writer):
            await reader.readline()
            for i in range(0, len(body), 7):  # splits inside the multi-byte characters
                writer.write(body[i : i + 7])
                await writer.drain()
                await asyncio.sleep(0)
            await asyncio.sleep(0.2)
            writer.close()

        server = await asyncio.start_server(slow, "127.0.0.1", 0)
        client = AeccLocalClient("127.0.0.1", server.sockets[0].getsockname()[1])
        try:
            value = await client.energy_parameters()
        finally:
            await client.close()
            server.close()
        self.assertEqual(value["SSumInfoList"]["TotalPVPower"], 1234)

    async def test_failures_are_energylib_errors_and_reconnect_next_time(self):
        self.assertTrue(issubclass(AeccLocalError, EnergyLibError))
        client = AeccLocalClient("127.0.0.1", 1, timeout=0.5)
        with self.assertRaises(AeccLocalError):
            await client.energy_parameters()

        async def garbage(reader, writer):
            await reader.readline()
            writer.write(b"[1, 2]")
            await writer.drain()

        server = await asyncio.start_server(garbage, "127.0.0.1", 0)
        client = AeccLocalClient("127.0.0.1", server.sockets[0].getsockname()[1], timeout=0.5)
        try:
            with self.assertRaises(AeccLocalError):
                await client.energy_parameters()
            self.assertFalse(client.connected)  # dropped; the next request reconnects
        finally:
            await client.close()
            server.close()

    async def test_closed_connection_before_answer(self):
        async def hang_up(reader, writer):
            await reader.readline()
            writer.close()

        server = await asyncio.start_server(hang_up, "127.0.0.1", 0)
        client = AeccLocalClient("127.0.0.1", server.sockets[0].getsockname()[1], timeout=0.5)
        try:
            with self.assertRaises(AeccLocalError):
                await client.energy_parameters()
        finally:
            await client.close()
            server.close()


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = await asyncio.start_server(serve_client, "127.0.0.1", 0)
        port = self.server.sockets[0].getsockname()[1]
        self.runtime = AeccLocalRuntime(AeccLocalClient("127.0.0.1", port), "AECCSIM0001", 34)

    async def asyncTearDown(self):
        await self.runtime.close()
        self.server.close()
        await self.server.wait_closed()

    async def test_read_only_runtime_with_standard_keys(self):
        await self.runtime.initialize("AECCSIM0001")
        keys = [p.key for p in self.runtime.points()]
        self.assertEqual(
            keys,
            [
                "solar_power",
                "battery_power",
                "grid_power",
                "load_power",
                "battery_total_soc",
                "backup_load_power",
            ],
        )
        data = await self.runtime.refresh(set(keys))
        self.assertEqual(set(data), set(keys))
        self.assertGreater(data["solar_power"], 0)
        self.assertEqual(self.runtime.unique_id, "aecc_AECCSIM0001")
        self.assertEqual(self.runtime.device_name, "EnergyLIB 0001")
        self.assertEqual(self.runtime.profile.manufacturer, "EnergyLIB")
        self.assertFalse(self.runtime.allow_control)
        self.assertFalse(self.runtime.profile.controls_supported)
        self.assertFalse(self.runtime.profile.power_direction_verified)
        self.assertEqual(self.runtime.profile.control_keys, frozenset())
        with self.runtime.prioritize_control():
            pass
        for call in (
            self.runtime.set_control("work_mode", "backup"),
            self.runtime.set_schedule({"periods": []}),
            self.runtime.set_battery_power(1, 60),
            self.runtime.stop_remote(),
        ):
            with self.assertRaises(ControlError):
                await call
        # The coordinator's state store works with the empty alarm and draft objects.
        self.assertEqual(self.runtime.alarms.export()["active"], [])
        self.runtime.alarms.restore(None)
        self.runtime.draft.restore(self.runtime.draft.export())

    async def test_shenggerui_inverter_reads_everything_from_registers(self):
        port = self.server.sockets[0].getsockname()[1]
        runtime = AeccLocalRuntime(AeccLocalClient("127.0.0.1", port), "AECCSIM0001", 131)
        try:
            await runtime.initialize("AECCSIM0001")
            self.assertTrue(runtime.registers)
            self.assertEqual(runtime.identity.inverter_code, 54)
            keys = [p.key for p in runtime.points()]
            self.assertEqual(keys, list(aecc_local.TS_KEYS) + list(aecc_local.ALARM_KEYS))
            self.assertNotIn("tou_schedule", keys)  # only with controls allowed
            requests = runtime.client.requests
            data = await runtime.refresh(set(keys))
            live, energy = len(aecc_local.TS_LIVE_BLOCKS), len(aecc_local.TS_ENERGY_BLOCKS)
            # Registers, the EMS mode block, one summary to look for accessories (none
            # here) and the control parameters.
            self.assertEqual(runtime.client.requests - requests, live + energy + 3)
            self.assertEqual(runtime.last_frame_count, live + energy + 3)
            self.assertEqual(data["work_mode"], "other")
            self.assertGreater(data["solar_energy"], 2000)
            self.assertEqual(data["battery_voltage"], 53.2)
            self.assertEqual(data["running_state"], "on_grid")
            self.assertLess(data["battery_power"], 0)
            diagnostics = runtime.diagnostics()
            self.assertIsNone(diagnostics["register_error"])
            self.assertEqual(diagnostics["inverter_code"], 54)
            self.assertEqual(diagnostics["control_parameters"]["DeviceGridInputPowerLimit"], "800")
            # The live block every poll, the counters once a minute (kept meanwhile).
            data = await runtime.refresh(set(keys))
            self.assertEqual(runtime.last_frame_count, live)
            self.assertGreater(data["solar_energy"], 2000)
        finally:
            await runtime.close()

    async def test_without_pass_through_only_the_summary_is_used(self):
        async def summary_only(reader, writer):
            while line := await reader.readline():
                request = json.loads(line)
                if request.get("Get") == "EnergyParameter":
                    body = answer(TotalPVPower=500)
                else:
                    body = {"Result": "unsupported"}
                writer.write(json.dumps({**body, "SerialNumber": request["SerialNumber"]}).encode())
                await writer.drain()

        server = await asyncio.start_server(summary_only, "127.0.0.1", 0)
        runtime = AeccLocalRuntime(
            AeccLocalClient("127.0.0.1", server.sockets[0].getsockname()[1]), "X", 131
        )
        try:
            await runtime.initialize("X")
            self.assertFalse(runtime.registers)
            self.assertEqual([p.key for p in runtime.points()], list(aecc_local.KEYS))
            data = await runtime.refresh({"solar_power"})
            self.assertEqual(data["solar_power"], 0.5)
            self.assertIsNone(runtime.diagnostics()["control_parameters"])
        finally:
            await runtime.close()
            server.close()

    async def test_a_device_without_summary_is_refused(self):
        async def other(reader, writer):
            await reader.readline()
            writer.write(b'{"Result": "unsupported"}')
            await writer.drain()

        server = await asyncio.start_server(other, "127.0.0.1", 0)
        runtime = AeccLocalRuntime(
            AeccLocalClient("127.0.0.1", server.sockets[0].getsockname()[1]), "X"
        )
        try:
            with self.assertRaises(EnergyLibError):
                await runtime.initialize("X")
        finally:
            await runtime.close()
            server.close()


if __name__ == "__main__":
    unittest.main()


class ShenggeruiControlTests(unittest.IsolatedAsyncioTestCase):
    """Controls through the pass-through, against the simulator's register model."""

    async def asyncSetUp(self):
        self.saved = (
            dict(simulator.SETTINGS),
            dict(simulator.STATE),
            list(simulator.STATE["alarms"]),
        )
        simulator.STATE["force_until"] = None
        self.server = await asyncio.start_server(serve_client, "127.0.0.1", 0)
        port = self.server.sockets[0].getsockname()[1]
        self.runtime = AeccLocalRuntime(AeccLocalClient("127.0.0.1", port), "AECCSIM0001", 131)
        await self.runtime.initialize("AECCSIM0001")
        self.runtime.allow_control = True
        self.keys = {p.key for p in self.runtime.points()}
        await self.runtime.refresh(self.keys, force=True)

    async def asyncTearDown(self):
        await self.runtime.close()
        self.server.close()
        await self.server.wait_closed()
        simulator.SETTINGS.clear()
        simulator.SETTINGS.update(self.saved[0])
        simulator.STATE.update(self.saved[1])
        simulator.STATE["alarms"] = self.saved[2]

    def test_frames(self):
        single = rtu_write_frame(1, 0xA09E, [20])
        self.assertEqual(single[:6], bytes.fromhex("01 06 A0 9E 00 14"))
        rtu_write_ack(single, single)  # 06 echoes the request
        multiple = rtu_write_frame(1, 0xA0A0, [1, 0x0100])
        self.assertEqual(multiple[:9].hex(" ").upper(), "01 10 A0 A0 00 02 04 00 01")
        echo = multiple[:6]
        from energylib_under_test.energylib_core.transport.aecc_local import crc16

        rtu_write_ack(echo + crc16(echo), multiple)
        refused = bytes.fromhex("01 86 03")
        with self.assertRaisesRegex(AeccLocalError, "exception 3"):
            rtu_write_ack(refused + crc16(refused), single)

    def test_plan_encoding(self):
        plan = TouSchedule.parse(
            {
                "periods": [
                    {
                        "type": "charge",
                        "start": "22:00",
                        "end": "06:00",
                        "power": 0,
                    },  # across midnight
                    {"type": "discharge", "start": "18:00", "end": "21:30", "power": 0},
                ]
            },
            aecc_local.TS_TOU_LIMITS,
        )
        words = aecc_local.encode_tou(plan)
        self.assertEqual(words[:3], [1, 0x1600, 0x0600])
        self.assertEqual(words[7:10], [1, 0x1200, 0x151E])
        decoded, enables = aecc_local.decode_tou(words)
        self.assertEqual(decoded.periods, plan.periods)
        self.assertEqual(enables, {"charge": True, "discharge": True})
        with self.assertRaises(ValueError):  # periods have no power on this model
            TouSchedule.parse(
                {"periods": [{"type": "charge", "start": "01:00", "end": "02:00", "power": 3}]},
                aecc_local.TS_TOU_LIMITS,
            )

    async def test_controls_are_read(self):
        r = self.runtime
        self.assertTrue(r.profile.controls_supported)
        self.assertTrue(r.profile.remote_supported)  # forced charge/discharge 0xA109-0xA10C
        self.assertEqual(
            r.controls,
            {
                "min_soc": 5,
                "backup_soc": 10,
                "grid_charge": True,
                "inverter_power": True,
                "work_mode": "other",  # 0xA034 = 0: the earlier feed-in behaviour
                "grid_import_limit": 1.0,
                "remote_control": 0,
            },
        )
        self.assertFalse(r.attributes["work_mode"]["hybrid_supply"])
        self.assertTrue({"remote_status", "remote_end_time", "work_mode"} <= self.keys)
        self.assertIn("tou_schedule", self.keys)
        attributes = r.attributes["tou_schedule"]
        self.assertEqual(
            attributes["periods"],
            [{"type": "charge", "start": "01:00", "end": "05:00", "power": 0.0}],
        )
        self.assertFalse(attributes["power_supported"])
        self.assertEqual(attributes["max_per_type"], 3)
        from datetime import datetime

        self.assertEqual(r.schedule_status(datetime(2026, 11, 19, 2, 0)), "inactive")  # enable is 0

    async def test_soc_and_grid_charge_are_written_and_read_back(self):
        r = self.runtime
        await r.set_control("backup_soc", 30)
        await r.set_control("min_soc", 15)
        self.assertEqual((simulator.SETTINGS[0xA09E], simulator.SETTINGS[0xA09C]), (30, 15))
        self.assertEqual(
            (r.controls["backup_soc"], r.controls["min_soc"], r.last_command), (30, 15, "confirmed")
        )
        with self.assertRaisesRegex(ControlError, "Minimum SOC"):
            await r.set_control("min_soc", 40)  # above the backup SOC: refused before writing
        with self.assertRaises(ControlError):
            await r.set_control("backup_soc", 101)
        await r.set_control("grid_charge", False)
        self.assertEqual(simulator.SETTINGS[0xA10D], 0)  # V2.7.1.1 grid charging switch
        self.assertEqual(simulator.SETTINGS[0xA02E], 2)  # the charge source is left alone
        self.assertFalse(r.controls["grid_charge"])
        await r.set_control("grid_charge", True)
        self.assertEqual(simulator.SETTINGS[0xA10D], 1)
        await r.set_control("grid_import_limit", 2.5)
        self.assertEqual(simulator.SETTINGS[0xA108], 2500)
        self.assertEqual(r.controls["grid_import_limit"], 2.5)
        with self.assertRaises(ControlError):
            await r.set_control("grid_import_limit", 70)

    async def test_ems_mode_sets_hybrid_supply_then_the_mode(self):
        r = self.runtime
        from energylib_under_test.energylib_core.model.points import CONTROLS

        mode = next(c for c in CONTROLS if c.key == "work_mode")
        self.assertEqual(
            r.control_options(mode),
            ("self_consumption", "time_of_use", "backup", "feed_in_priority"),
        )
        await r.set_control("work_mode", "backup")
        self.assertEqual((simulator.SETTINGS[0xA028], simulator.SETTINGS[0xA034]), (3, 13))
        self.assertEqual((r.controls["work_mode"], r.values["work_mode"]), ("backup", "backup"))
        self.assertTrue(r.attributes["work_mode"]["hybrid_supply"])
        await r.set_control("work_mode", "feed_in_priority")
        self.assertEqual(simulator.SETTINGS[0xA034], 12)
        with self.assertRaises(ControlError):
            await r.set_control("work_mode", "peak_shaving")
        # The plan only runs in time-of-use; saving it with activate switches to it.
        await r.set_schedule(
            {"periods": [{"type": "charge", "start": "01:00", "end": "05:00", "power": 0}]},
            activate=True,
        )
        self.assertEqual(simulator.SETTINGS[0xA034], 11)
        data = await r.refresh(self.keys, force=True)
        self.assertEqual(data["work_mode"], "time_of_use")

    async def test_forced_charge_and_discharge_are_timed_by_the_inverter(self):
        r = self.runtime
        await r.set_battery_power(-2.5, 600)  # charge 2.5 kW for 10 minutes
        self.assertEqual(
            (simulator.SETTINGS[0xA109], simulator.SETTINGS[0xA10A], simulator.SETTINGS[0xA10B]),
            (0x10000 - 2500, 600, 1),
        )
        self.assertEqual((r.remote_status, r.controls["remote_control"]), ("charging", 1))
        self.assertIsNotNone(r.remote_end_time)
        self.assertEqual(r.attributes["remote_status"]["target_power_kw"], -2.5)
        data = await r.refresh(self.keys, force=True)
        self.assertLess(data["battery_power"], -2)  # the simulated battery follows
        # The mode cannot change while it runs; a new task replaces the running one.
        with self.assertRaisesRegex(ControlError, "Stop remote"):
            await r.set_control("work_mode", "backup")
        await r.set_battery_power(1.0, 300)
        self.assertEqual((simulator.SETTINGS[0xA109], simulator.SETTINGS[0xA10A]), (1000, 300))
        self.assertEqual(r.remote_status, "discharging")
        await r.stop_remote()
        self.assertEqual((simulator.SETTINGS[0xA10B], r.remote_status), (0, "stopped"))
        self.assertIsNone(r.remote_deadline)
        for power, seconds in ((40, 60), (1, 64801), (1, 0), (1, 1.5)):
            with self.subTest(power=power, seconds=seconds), self.assertRaises(ControlError):
                await r.set_battery_power(power, seconds)

    async def test_forced_task_that_ends_on_the_device_is_completed(self):
        r = self.runtime
        await r.set_battery_power(-1, 60)
        simulator.STATE["force_until"] = 0  # the inverter's timer ran out
        await r.refresh(self.keys, force=True)
        self.assertEqual((r.remote_status, r.controls["remote_control"]), ("completed", 0))

    async def test_a_refused_write_is_reported(self):
        simulator.RANGES[0xA09E] = (0, 20)  # the device refuses what our check allows
        try:
            with self.assertRaises(PartialWrite):
                await self.runtime.set_control("backup_soc", 50)
            self.assertEqual(self.runtime.last_command, "uncertain")
            self.assertEqual(simulator.SETTINGS[0xA09E], 10)
        finally:
            simulator.RANGES[0xA09E] = (0, 100)

    async def test_power_off_and_on_are_confirmed_from_the_state(self):
        r = self.runtime
        with patch.object(aecc_local, "POWER_POLL_S", 0):
            await r.set_control("inverter_power", False)
            self.assertFalse(simulator.STATE["on"])
            self.assertFalse(r.controls["inverter_power"])
            await r.set_control("inverter_power", True)
        self.assertTrue(simulator.STATE["on"])
        self.assertEqual(r.last_command, "confirmed")

    async def test_power_not_confirmed(self):
        original = simulator.write
        with (
            patch.object(simulator, "write", lambda address, values: None),
            patch.object(aecc_local, "POWER_POLL_S", 0),
            patch.object(aecc_local, "POWER_CONFIRM_S", 0.05),
        ):
            with self.assertRaisesRegex(PartialWrite, "not confirmed"):
                await self.runtime.set_control("inverter_power", False)
        self.assertIs(simulator.write, original)
        self.assertEqual(self.runtime.last_command, "uncertain")

    async def test_plan_is_written_once_and_read_back(self):
        r = self.runtime
        await r.set_schedule(
            {
                "periods": [
                    {"type": "discharge", "start": "18:00", "end": "21:00", "power": 0},
                    {"type": "charge", "start": "02:00", "end": "05:00", "power": 0},
                ],
                "weekdays": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            }
        )
        self.assertEqual(simulator.SETTINGS[0xA0A0], 1)
        self.assertEqual(simulator.SETTINGS[0xA0A7], 1)
        self.assertEqual((simulator.SETTINGS[0xA0A8], simulator.SETTINGS[0xA0A9]), (0x1200, 0x1500))
        from datetime import datetime

        self.assertEqual(r.schedule_status(datetime(2026, 11, 19, 19, 0)), "discharge")
        self.assertEqual(r.schedule_status(datetime(2026, 11, 19, 12, 0)), "outside_period")
        for bad in (
            {"periods": [], "weekdays": ["mon"]},
            {"periods": [], "outside_mode": "backup"},
            {"periods": [{"type": "charge", "start": "01:00", "end": "02:00", "power": 2}]},
        ):
            with self.subTest(bad=bad), self.assertRaises(ControlError):
                await r.set_schedule(bad)
        await r.set_schedule({"periods": []})  # clearing switches both kinds off
        self.assertEqual((simulator.SETTINGS[0xA0A0], simulator.SETTINGS[0xA0A7]), (0, 0))
        self.assertEqual(r.schedule_status(datetime(2026, 11, 19, 19, 0)), "inactive")

    async def test_alarms_have_names_and_levels(self):
        simulator.STATE["alarms"] = [
            0x0818,
            0x0908,
            0x0002,
            0,
            0,
            0,
            0,
            0,
        ]  # the bench's F04/F05/F12/F20/F25/F28/F34
        data = await self.runtime.refresh(self.keys)
        self.assertEqual(data["alarm_status"], "abnormal")
        alarms = self.runtime.attributes["alarm_status"]["alarms"]
        names = {a["fault_code"]: a["name"] for a in alarms}
        self.assertEqual(names["F05"], "电池未接")
        self.assertEqual(names["F28"], "EEPROM故障")
        self.assertEqual(len(alarms), 7)
        self.assertEqual(
            (data["fault_count"], data["warning_count"]), (5, 2)
        )  # F05 and F34 are warnings
        self.assertTrue(self.runtime.attributes["alarm_status"]["battery_disconnected"])

    async def test_read_only_without_permission(self):
        self.runtime.allow_control = False
        self.assertNotIn("tou_schedule", {p.key for p in self.runtime.points()})
        for call in (
            self.runtime.set_control("min_soc", 10),
            self.runtime.set_schedule({"periods": []}),
        ):
            with self.assertRaises(ControlError):
                await call
        self.assertEqual(simulator.SETTINGS[0xA09C], 5)

    async def test_register_error_reflects_this_poll(self):
        real = self.runtime.client.read_registers

        async def grid_ct_fails(slave, address, count):
            if address == 0x7584:
                raise AeccLocalError("No valid answer to DataTransmission")
            return await real(slave, address, count)

        with patch.object(self.runtime.client, "read_registers", grid_ct_fails):
            data = await self.runtime.refresh(self.keys, force=True)  # energy and controls too
        self.assertIsNone(data["grid_power"])
        self.assertEqual(self.runtime.register_error, "No valid answer to DataTransmission")
        await self.runtime.refresh(self.keys)
        self.assertIsNone(self.runtime.register_error)  # every read of this poll succeeded

    async def test_polls_go_on_while_power_is_confirmed(self):
        polled = []

        async def poll_during_confirm():
            await asyncio.sleep(0.01)
            await asyncio.wait_for(self.runtime.refresh(self.keys), 1)
            polled.append(True)

        with patch.object(aecc_local, "POWER_POLL_S", 0.05):
            await asyncio.gather(
                self.runtime.set_control("inverter_power", False), poll_during_confirm()
            )
        self.assertEqual(polled, [True])
        self.assertFalse(self.runtime.controls["inverter_power"])


class AccessoryTests(unittest.IsolatedAsyncioTestCase):
    """Chargers, plugs and water heaters from the EMS summary (read only)."""

    async def asyncSetUp(self):
        simulator.ACCESSORIES = True
        self.server = await asyncio.start_server(serve_client, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        simulator.ACCESSORIES = False
        self.server.close()
        await self.server.wait_closed()

    def test_charger_state_is_the_most_active_connector(self):
        def charger(c1, c2, p1=0, p2=0):
            return {
                "Connector1Status": c1,
                "Connector2Status": c2,
                "Connector1Power": p1,
                "Connector2Power": p2,
                "ConnectorElectricity": 1.5,
            }

        for connectors, state in (
            ((0, 0), "off"),
            ((1, 0), "preparing"),
            ((3, 1), "preparing"),
            ((3, 0), "finished"),
            ((2, 3), "charging"),
            ((9, 0), "other"),
        ):
            values, _ = aecc_local.decode_chargers({"ChargerInfoList": [charger(*connectors)]})
            self.assertEqual(values["ev_charger_status"], state, connectors)
        values, details = aecc_local.decode_chargers(
            {"ChargerInfoList": [charger(2, 2, 3000, 4000), charger(2, 0, 7000)]}
        )
        self.assertEqual(values["ev_charge_power"], 14.0)  # summed, W -> kW
        self.assertEqual(values["ev_connector_energy"], 3.0)
        self.assertEqual(len(details["chargers"]), 2)
        self.assertEqual(aecc_local.decode_chargers({"ChargerInfoList": []}), ({}, None))
        values, _ = aecc_local.decode_chargers({"ChargerInfoList": [{"Connector1Status": 2}]})
        self.assertIsNone(values["ev_charge_power"])  # missing is unknown, never zero

    def test_plug_and_heater_units(self):
        items = aecc_local.decode_accessories(scenario())
        plug = items[("plug", "NKPG1DDC40")]
        self.assertEqual(
            {k: plug["values"][k] for k in ("plug_on", "plug_power", "plug_voltage")},
            {"plug_on": True, "plug_power": 120, "plug_voltage": 230.1},  # 0.1 V
        )
        self.assertEqual(plug["attributes"]["rated_power_w"], 2000)
        heater = items[("heater", "SXDI7A47B8")]["values"]
        self.assertEqual(
            (heater["heater_temperature"], heater["heater_temperature_max"]), (45.2, 60)
        )
        self.assertTrue(heater["heater_on"])

    async def test_summary_device_gets_charger_points_and_accessories(self):
        runtime = AeccLocalRuntime(AeccLocalClient("127.0.0.1", self.port), "AECCSIM0001", 34)
        try:
            await runtime.initialize("AECCSIM0001")
            keys = [p.key for p in runtime.points()]
            self.assertEqual(keys[-3:], list(aecc_local.CHARGER_KEYS))
            self.assertEqual(
                runtime.accessory_list(), [("heater", "SXDI7A47B8"), ("plug", "NKPG1DDC40")]
            )
            data = await runtime.refresh(set(keys))
            self.assertEqual(data["ev_charge_power"], 7.0)
            self.assertEqual(data["ev_charger_status"], "charging")
            self.assertEqual(
                runtime.attributes["ev_charger_status"]["chargers"][0]["serial"], "SXDI78C594"
            )
            self.assertFalse(runtime.capabilities_changed)
            simulator.ACCESSORIES = False  # the EMS no longer reports them
            await runtime.refresh(set(keys))
            self.assertTrue(runtime.capabilities_changed)
            self.assertEqual(runtime.accessory_list(), [])
        finally:
            await runtime.close()

    async def test_register_device_reads_the_summary_only_for_accessories(self):
        runtime = AeccLocalRuntime(AeccLocalClient("127.0.0.1", self.port), "AECCSIM0001", 131)
        try:
            await runtime.initialize("AECCSIM0001")
            self.assertTrue(runtime.registers)
            keys = {p.key for p in runtime.points()}
            await runtime.refresh(keys)
            data = await runtime.refresh(keys)
            # Live blocks plus the summary every poll while the EMS reports accessories.
            self.assertEqual(runtime.last_frame_count, len(aecc_local.TS_LIVE_BLOCKS) + 1)
            self.assertEqual(data["ev_charge_power"], 7.0)
            self.assertEqual(
                runtime.accessories[("plug", "NKPG1DDC40")]["values"]["plug_power"], 120
            )
            real = runtime.client.energy_parameters

            async def fails():
                raise AeccLocalError("No answer")

            runtime.client.energy_parameters = fails
            data = await runtime.refresh(keys)
            self.assertIsNone(data["ev_charge_power"])  # unknown, not the last value
            self.assertEqual(runtime.accessories[("plug", "NKPG1DDC40")]["values"], {})
            runtime.client.energy_parameters = real
        finally:
            await runtime.close()
