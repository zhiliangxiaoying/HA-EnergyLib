"""End-to-end TCP bench tests for profiles, polling, controls and failure states."""

import asyncio
import time
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone

from energylib_under_test.energylib_core.legacy import IdentityMismatch
from energylib_under_test.energylib_core.model.points import CONTROLS, CORE
from energylib_under_test.energylib_core.model.tou import TouLimits, TouSchedule
from energylib_under_test.energylib_core.planner import plan_reads
from energylib_under_test.energylib_core.profiles.sinexcel_v2 import Mapping
from energylib_under_test.energylib_core.runtime import ControlError, DeviceRuntime, PartialWrite
from energylib_under_test.energylib_core.transport.modbus_tcp import (
    DeviceException,
    EnergyLibError,
    ModbusTcpClient,
)

from tools.sinexcel_simulator import BenchConfig, BenchModel, BenchServer


def period(kind="charge", start="01:00", end="06:00", power=2):
    return dict(type=kind, start=start, end=end, power=power)


class TouTests(unittest.TestCase):
    def test_validity_roundtrip_and_calendar_boundaries(self):
        data = {
            "periods": [period(start="10:00", end="12:00")],
            "valid_from": "2098-01-01T12:34:56+00:00",
            "valid_until": "2098-01-03T23:45:00+00:00",
        }
        schedule = TouSchedule.parse(data, TouLimits(max_power_kw=5))
        self.assertEqual(schedule.as_dict()["valid_from"], data["valid_from"])
        self.assertEqual(schedule.as_dict()["valid_until"], data["valid_until"])
        self.assertEqual(TouSchedule.parse(schedule.as_dict(), TouLimits(max_power_kw=5)), schedule)
        for day, active in (
            (date(2097, 12, 31), False),
            (date(2098, 1, 1), False),
            (date(2098, 1, 2), True),
            (date(2098, 1, 3), True),
            (date(2098, 1, 4), False),
        ):
            self.assertEqual(
                schedule.active(datetime.combine(day, datetime.min.time()).replace(hour=11))
                is not None,
                active,
            )

    def test_invalid_validity_is_rejected(self):
        for bounds in (
            {"valid_from": "2098-01-01"},
            {"valid_from": 42},
            {"valid_from": "2098-01-03T00:00:00+00:00", "valid_until": "2098-01-01T00:00:00+00:00"},
        ):
            with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                TouSchedule.parse({"periods": [], **bounds}, TouLimits(max_power_kw=5))

    def test_roundtrip_sort_and_preserve_defaults(self):
        parsed = TouSchedule.parse(
            {"periods": [period("discharge", "18:00", "22:00"), period()]},
            TouLimits(max_power_kw=5.0),
        )
        self.assertEqual(parsed.periods[0].start, "01:00")
        self.assertNotIn("outside_mode", parsed.as_dict())

    def test_reject_overlap_invalid_time_and_limits(self):
        for items in (
            [period(), period("discharge", "05:00", "07:00")],
            [period(end="24:00")],
            [period(start="01:00", end="01:00")],
            [period(power=float("nan"))],
            [period(power=True)],
            [period(power=6)],
            [period(start="23:00", end="06:00")],
            [period(start="1:00")],
        ):
            with self.subTest(items=items), self.assertRaises(ValueError):
                TouSchedule.parse({"periods": items}, TouLimits(max_power_kw=5.0))

    def test_sunday_cross_midnight_and_weekday_overlap(self):
        schedule = TouSchedule.parse(
            {"periods": [period(start="22:00", end="06:00")], "weekdays": ["sun"]},
            TouLimits(max_power_kw=5.0, supports_cross_midnight=True),
        )
        self.assertIsNotNone(schedule.active(datetime(2026, 10, 19, 5)))  # Monday after Sunday
        self.assertIsNone(schedule.active(datetime(2026, 10, 20, 5)))
        with self.assertRaises(ValueError):
            TouSchedule.parse(
                {
                    "periods": [
                        period(start="22:00", end="06:00"),
                        period("discharge", "05:00", "07:00"),
                    ]
                },
                TouLimits(max_power_kw=5.0, supports_cross_midnight=True),
            )

    def test_power_limit_comes_from_profile(self):
        data = {"periods": [period(power=8)]}
        self.assertEqual(TouSchedule.parse(data, TouLimits(max_power_kw=10.0)).periods[0].power, 8)
        for limits in (TouLimits(max_power_kw=5.0), TouLimits()):
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                TouSchedule.parse(data, limits)

    def test_slot_limit_and_empty_weekdays(self):
        for data in (
            {"periods": [], "weekdays": []},
            {"periods": [period(start=f"{i:02}:00", end=f"{i:02}:30") for i in range(7)]},
        ):
            with self.assertRaises(ValueError):
                TouSchedule.parse(data, TouLimits(max_power_kw=5.0))

    def test_read_planner_separates_units_functions_and_holes(self):
        maps = [
            Mapping("a", 10),
            Mapping("b", 12),
            Mapping("c", 16),
            Mapping("d", 10, unit=2),
            Mapping("e", 10, function=4),
        ]
        blocks = plan_reads(maps, register_limit=10)
        self.assertEqual(len(blocks), 4)
        self.assertEqual(blocks[0].count, 4)
        self.assertEqual(len(plan_reads(maps, register_limit=2)), 5)


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_unowned_remote_session_is_not_reported_idle_or_resumed(self):
        self.runtime.allow_control = True
        self.model.set_float(0xC646, 1)
        values = await self.runtime.refresh({"remote_status"})
        self.assertEqual(values["remote_status"], "unconfirmed")
        self.assertIsNone(self.runtime.remote_deadline)
        self.assertIsNone(self.runtime._lease_task)
        self.assertEqual(self.peer.write_count, 0)

    async def test_remote_drafts_do_not_write_or_mutate_running_snapshot(self):
        self.runtime.allow_control = True
        self.runtime.draft.set("remote_target_power", 2, 5)
        self.runtime.draft.set("remote_duration", 1, 5)
        self.assertEqual(self.peer.write_count, 0)
        power, seconds = self.runtime.draft.snapshot("charge", 5)
        await self.runtime.set_battery_power(power, seconds)
        writes = self.peer.write_count
        self.runtime.draft.set("remote_target_power", 3, 5)
        self.runtime.draft.set("remote_duration", 1440, 5)
        self.assertEqual(self.peer.write_count, writes)
        self.assertEqual(self.runtime.remote_target, -2)
        self.assertEqual(self.runtime.remote_deadline, self.now + 60)
        self.assertEqual(self.runtime.remote_status, "charging")
        await self.runtime.stop_remote()
        self.assertEqual(self.runtime.remote_status, "stopped")
        self.assertIsNone(self.runtime.remote_end_time)

    async def test_expiration_publishes_status_without_polling(self):
        self.runtime.allow_control = True
        self.runtime._clock = time.monotonic
        changes = []
        self.runtime.state_changed = lambda: changes.append(self.runtime.remote_status)
        await self.runtime.set_battery_power(1, 1)
        task = self.runtime._lease_task
        await asyncio.wait_for(asyncio.shield(task), 2)
        self.assertEqual(self.model.get_float(0xC646), 0)
        self.assertEqual(changes, ["discharging", "completed"])
        self.assertIsNone(self.runtime.remote_end_time)

    async def test_one_failed_poll_does_not_leave_a_running_task_unconfirmed(self):
        self.runtime.allow_control = True
        await self.runtime.set_battery_power(1, 300)
        self.assertEqual(self.runtime.remote_status, "discharging")
        self.peer.fault = "short_frame"
        with self.assertRaises(EnergyLibError):
            await self.runtime.refresh({"remote_status"}, force=True)
        self.assertEqual(self.runtime.remote_status, "unconfirmed")
        self.peer.fault = "normal"
        values = await self.runtime.refresh({"remote_status"}, force=True)
        # The device confirms the session again: the task is shown as running.
        self.assertEqual(values["remote_status"], "discharging")
        await self.runtime.stop_remote()

    async def test_confirmed_exit_after_a_failed_poll_reports_completed(self):
        self.runtime.allow_control = True
        self.runtime._clock = time.monotonic
        await self.runtime.set_battery_power(1, 1)
        task = self.runtime._lease_task
        self.peer.fault = "short_frame"
        with self.assertRaises(EnergyLibError):
            await self.runtime.refresh({"remote_status"}, force=True)
        self.peer.fault = "normal"
        await asyncio.wait_for(asyncio.shield(task), 3)
        self.assertEqual(self.model.get_float(0xC646), 0)
        self.assertEqual(self.runtime.remote_status, "completed")
        self.assertEqual(self.runtime.last_command, "confirmed")

    async def test_uncertain_remote_write_never_claims_running_or_stopped(self):
        self.runtime.allow_control = True
        self.peer.fault = "mismatch"
        with self.assertRaises(PartialWrite):
            await self.runtime.set_battery_power(1, 60)
        self.assertEqual(self.runtime.remote_status, "unconfirmed")
        self.assertIsNone(self.runtime.remote_deadline)
        self.peer.fault = "normal"
        await self.runtime.stop_remote()
        await self.runtime.set_battery_power(0, 300)
        self.assertEqual(self.runtime.remote_status, "holding")
        self.assertEqual(self.runtime.remote_deadline, self.now + 300)

    async def test_alarm_summaries_read_without_original_alarm_entity(self):
        self.model.bits[1][0x1054] = True
        self.model.bits[2][0x1048] = True
        values = await self.runtime.refresh({"fault_count", "warning_count", "alarm_status"})
        self.assertEqual(values, {"fault_count": 1, "warning_count": 1, "alarm_status": "abnormal"})
        before = self.client.requests
        await self.runtime.refresh({"fault_count", "warning_count", "alarm_status"})
        # Identity remains checked, but alarm blocks are shared and not read per summary.
        self.assertLess(self.client.requests - before, 3)

    async def test_partial_alarm_source_retains_previous_confirmed_fault(self):
        self.model.bits[2][0x1054] = True
        await self.runtime.refresh({"active_alarms"})
        self.peer.fault = "unit2_offline"
        values = await self.runtime.refresh(
            {"any_fault", "fault_count", "alarm_status"}, force=True
        )
        self.assertTrue(values["any_fault"])
        self.assertIsNone(values["fault_count"])
        self.assertEqual(values["alarm_status"], "incomplete")
        self.assertFalse(self.runtime.alarms.events)

    async def asyncSetUp(self):
        self.now = 1000.0
        self.model = BenchModel(
            BenchConfig(batteries=2, pv_count=4, writes=True), clock=lambda: self.now
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
        self.peer.fault = "normal"
        self.peer.fail_write_index = 0
        await self.runtime.close()
        self.server.close()
        await self.server.wait_closed()
        await self.peer.close()

    async def test_fourteen_core_points_capabilities_and_no_holes(self):
        values = await self.runtime.refresh({p.key for p in CORE})
        self.assertEqual(len(values), 14)
        self.assertTrue(all(v is not None for v in values.values()))
        self.assertEqual(values["running_state"], "on_grid")
        keys = {p.key for p in self.runtime.points()}
        self.assertIn("pv4_power", keys)
        self.assertIn("battery_2_soc", keys)
        # +1 since the grid bit 0x1014 became an alarm source: it cannot share the
        # 0x1048 read (0x1015–0x1047 are status bits, not alarms).
        self.assertLess(self.runtime.last_frame_count, 19)

    async def test_battery_2_voltage_and_current_follow_battery_2(self):
        keys = {"battery_2_voltage", "battery_2_current", "battery_2_power"}
        self.assertLessEqual(keys, {p.key for p in self.runtime.points()})
        values = await self.runtime.refresh(keys, force=True)
        self.assertAlmostEqual(values["battery_2_voltage"], 51.0, places=2)
        # Same standard sign as battery 2 power (+ = discharge, - = charge).
        self.assertGreater(values["battery_2_current"] * values["battery_2_power"], 0)
        self.model.set_float(0x3036, 0)  # single battery
        await self.runtime.initialize()
        self.assertFalse(keys & {p.key for p in self.runtime.points()})

    async def test_grid_bit_is_an_alarm_source_and_diagnostics_are_gone(self):
        sources = self.runtime.profile.mappings["active_alarms"]
        self.assertIn(
            (2, 1, 0x1014, 1), {(m.function, m.unit, m.address, m.count) for m in sources}
        )
        keys = {p.key for p in self.runtime.points()}
        self.assertFalse({"grid_fault", "bms_comm_fault"} & keys)
        self.model.bits[1][0x1014] = True
        values = await self.runtime.refresh({"alarm_status"}, force=True)
        self.assertEqual(values["alarm_status"], "abnormal")
        (alarm,) = self.runtime.attributes["alarm_status"]["alarms"]
        self.assertEqual(
            (alarm["name"], alarm["severity"], alarm["code"]), ("电网异常", "warning", "1014")
        )

    async def test_unsupported_grid_bit_does_not_leave_alarms_incomplete(self):
        del self.model.bits[1][0x1014]
        await self.runtime.initialize()
        sources = self.runtime.profile.mappings["active_alarms"]
        self.assertNotIn(0x1014, {m.address for m in sources})
        values = await self.runtime.refresh({"alarm_status"}, force=True)
        self.assertEqual(values["alarm_status"], "normal")

    async def test_reconnect_limits_are_checked_before_every_command(self):
        self.runtime.allow_control = True
        self.model.config = replace(self.model.config, rated_kw=3)
        self.model.tick(datetime(2026, 10, 20, 11), 0)
        for action in (
            lambda: self.runtime.set_battery_power(4, 30),
            lambda: self.runtime.set_control("export_limit", 4),
            lambda: self.runtime.set_schedule({"periods": [period(power=4)]}),
        ):
            self.runtime.profile.max_power_kw = 5
            await self.client.close()
            before = self.peer.write_count
            with self.assertRaises((ControlError, ValueError)):
                await action()
            self.assertEqual(self.runtime.profile.max_power_kw, 3)
            self.assertEqual(self.peer.write_count, before)

    async def test_reconnect_can_revoke_control_capabilities_before_writing(self):
        self.runtime.allow_control = True
        self.model.registers.pop(0xC646)
        self.model.registers.pop(0xC647)
        await self.client.close()
        before = self.peer.write_count
        with self.assertRaises(ControlError):
            await self.runtime.set_control("grid_charge", True)
        self.assertFalse(self.runtime.profile.controls_supported)
        self.assertEqual(self.peer.write_count, before)

    async def test_queued_control_uses_limits_at_lock_acquisition(self):
        self.runtime.allow_control = True
        await self.runtime._lock.acquire()
        try:
            command = asyncio.create_task(self.runtime.set_control("export_limit", 4))
            await asyncio.sleep(0)
            self.runtime.profile.max_power_kw = 3
        finally:
            self.runtime._lock.release()
        with self.assertRaises(ControlError):
            await command
        self.assertEqual(self.peer.write_count, 0)

    async def test_close_rejects_queued_commands_and_future_reads(self):
        self.runtime.allow_control = True
        await self.runtime.set_battery_power(1, 300)
        await asyncio.sleep(0)
        await self.runtime._lock.acquire()
        try:
            closing = asyncio.create_task(self.runtime.close())
            await asyncio.sleep(0)
            command = asyncio.create_task(self.runtime.set_battery_power(2, 300))
            await asyncio.sleep(0)
        finally:
            self.runtime._lock.release()
        await closing
        with self.assertRaises(ControlError):
            await command
        self.assertEqual(self.model.get_float(0xC646), 0)
        self.assertIsNone(self.runtime._lease_task)
        self.assertIsNone(self.runtime.remote_deadline)
        requests = self.client.requests
        for action in (
            lambda: self.runtime.refresh({"solar_power"}),
            lambda: self.runtime.initialize(),
            lambda: self.runtime.set_control("grid_charge", True),
            lambda: self.runtime.set_schedule({"periods": []}),
        ):
            with self.assertRaises(ControlError):
                await action()
        await self.runtime.close()  # Closing again is harmless.
        self.assertEqual(self.client.requests, requests)

    async def test_future_expired_and_non_midnight_validity_are_preserved(self):
        from energylib_under_test.energylib_core.profiles.sinexcel_v2 import u32_words

        self.runtime.allow_control = True
        await self.runtime.set_schedule(
            {"periods": [period(start="10:00", end="12:00")]}, activate=True
        )
        for first, last in (
            (
                datetime(2098, 1, 1, 12, 34, 56, tzinfo=timezone.utc),
                datetime(2098, 12, 31, tzinfo=timezone.utc),
            ),
            (datetime(2000, 1, 1, tzinfo=timezone.utc), datetime(2001, 1, 1, tzinfo=timezone.utc)),
        ):
            expected = u32_words(int(first.timestamp())) + u32_words(int(last.timestamp()))
            self.model.registers.update({0xC0B6 + i: word for i, word in enumerate(expected)})
            await self.runtime.refresh({"tou_schedule"}, force=True)
            self.assertEqual(
                self.runtime.schedule_status(datetime(2026, 10, 20, 11)), "outside_period"
            )
            self.assertEqual(self.runtime.schedule.valid_from, first)
            self.assertEqual(self.runtime.schedule.valid_until, last)
            # An old form draft must not overwrite bounds changed from the App.
            data = {
                "periods": [period(start="10:00", end="12:00")],
                "outside_mode": "backup",
                "valid_from": "2020-01-01T00:00:00+00:00",
            }
            await self.runtime.set_schedule(data)
            self.assertEqual([self.model.registers[0xC0B6 + i] for i in range(4)], expected)
            await self.runtime.set_schedule({"periods": []})
            self.assertEqual([self.model.registers[0xC0B6 + i] for i in range(4)], expected)

    async def test_schedule_readback_checks_validity_as_well_as_periods(self):
        self.runtime.allow_control = True
        original = self.runtime.profile.write_schedule

        async def changed_dates(client, schedule):
            await original(client, schedule)
            self.model.registers[0xC0B7] += 1

        self.runtime.profile.write_schedule = changed_dates
        with self.assertRaises(PartialWrite):
            await self.runtime.set_schedule({"periods": [period()]}, activate=True)
        self.assertEqual(self.model.get_float(0x300C), 0)
        self.assertEqual(self.runtime.last_command, "uncertain")

    async def test_disabled_points_are_not_polled_and_slow_tier_is_cached(self):
        await self.runtime.refresh({"solar_energy", "solar_power"})
        first = self.runtime.last_frame_count
        self.now += 30
        await self.runtime.refresh({"solar_energy", "solar_power"})
        self.assertLess(self.runtime.last_frame_count, first)
        self.assertNotIn("pv1_power", self.runtime.values)
        self.now += 30
        await self.runtime.refresh({"solar_energy", "solar_power"})
        self.assertGreater(self.runtime.last_frame_count, 3)

    async def test_partial_alarm_source_never_becomes_zero(self):
        self.peer.fault = "unit2_offline"
        self.model.bits[1][0x1048] = True
        values = await self.runtime.refresh({"active_alarms"})
        self.assertIsNone(values["active_alarms"])
        attributes = self.runtime.attributes["active_alarms"]
        self.assertFalse(attributes["complete"])
        self.assertEqual([(a["source"], a["code"]) for a in attributes["alarms"]], [(1, "1048")])
        self.assertEqual(attributes["alarms"][0]["severity"], "warning")

    async def test_changed_serial_refuses_all_writes(self):
        self.runtime.allow_control = True
        self.model.registers[0xC104] = 0x4F54
        with self.assertRaises(IdentityMismatch):
            await self.runtime.set_control("grid_charge", True)
        self.assertEqual(self.peer.write_count, 0)

    async def test_control_gate_numeric_boundaries_and_two_battery_readback(self):
        with self.assertRaises(ControlError):
            await self.runtime.set_control("grid_charge", True)
        self.runtime.allow_control = True
        for key, value in (
            ("backup_soc", -1),
            ("backup_soc", float("inf")),
            ("backup_soc", True),
            ("min_soc", 30),
            ("work_mode", "remote"),
        ):
            with self.subTest(key=key, value=value), self.assertRaises(ControlError):
                await self.runtime.set_control(key, value)
        self.assertEqual(self.peer.write_count, 0)
        await self.runtime.set_control("backup_soc", 35)
        self.assertEqual(self.model.get_float(0x314A), 35)
        self.assertEqual(self.model.get_float(0x314C), 35)
        self.assertEqual(self.runtime.controls["backup_soc"], 35)

    async def test_existing_soc_conflict_only_blocks_soc_writes(self):
        self.runtime.allow_control = True
        # Device already has min SOC above backup SOC, e.g. changed from the App.
        for address, value in ((0x3046, 50), (0x30B8, 50), (0x314A, 30), (0x314C, 30)):
            self.model.set_float(address, value)
        await self.runtime.set_control("grid_charge", True)
        self.assertEqual(self.runtime.controls["grid_charge"], 1)
        await self.runtime.set_control("export_limit", 2)
        self.assertEqual(self.runtime.controls["export_limit"], 2)
        before = self.peer.write_count
        with self.assertRaises(ControlError):
            await self.runtime.set_control("backup_soc", 40)
        self.assertEqual(self.peer.write_count, before)
        # Raising backup above minimum resolves the conflict and is accepted.
        await self.runtime.set_control("backup_soc", 60)
        self.assertEqual(self.runtime.controls["backup_soc"], 60)

    async def test_power_controls_follow_profile_rating(self):
        self.runtime.allow_control = True
        self.runtime.profile.max_power_kw = 3.0
        before = self.peer.write_count
        for key, value in (("export_limit", 4), ("export_limit", -1)):
            with self.subTest(key=key, value=value), self.assertRaises(ControlError):
                await self.runtime.set_control(key, value)
        with self.assertRaises(ControlError):
            await self.runtime.set_battery_power(-3.5, 60)
        self.assertEqual(self.peer.write_count, before)
        await self.runtime.set_control("export_limit", 2.5)
        self.assertEqual(self.runtime.controls["export_limit"], 2.5)
        self.runtime.profile.max_power_kw = None
        with self.assertRaises(ControlError):
            await self.runtime.set_control("export_limit", 1)
        with self.assertRaises(ControlError):
            await self.runtime.set_battery_power(1, 60)

    async def test_work_mode_options_follow_profile_codes(self):
        self.runtime.allow_control = True
        mode = next(c for c in CONTROLS if c.key == "work_mode")
        self.assertIn("backup", self.runtime.control_options(mode))
        # A profile that cannot encode "backup" must neither offer nor write it.
        self.runtime.profile.work_mode_codes = {0: "self_consumption", 1: "time_of_use"}
        self.assertEqual(self.runtime.control_options(mode), ("self_consumption", "time_of_use"))
        before = self.peer.write_count
        with self.assertRaises(ControlError):
            await self.runtime.set_control("work_mode", "backup")
        self.assertEqual(self.peer.write_count, before)

    async def test_remote_lease_uses_host_task_factory(self):
        self.runtime.allow_control = True
        created = []

        def factory(coro):
            task = asyncio.create_task(coro)
            created.append(task)
            return task

        self.runtime._task_factory = factory
        await self.runtime.set_battery_power(1, 60)
        self.assertEqual(len(created), 1)
        await self.runtime.stop_remote()
        self.assertTrue(created[0].done())

    async def test_device_name_is_distinct_per_serial(self):
        serial = self.runtime.identity.serial
        self.assertEqual(self.runtime.device_name, f"EnergyLIB {serial[-4:]}")

    async def test_write_mismatch_not_reported_as_success(self):
        self.runtime.allow_control = True
        self.peer.fault = "mismatch"
        with self.assertRaises(PartialWrite):
            await self.runtime.set_control("grid_charge", True)
        self.assertEqual(self.runtime.last_command, "uncertain")
        self.assertEqual(self.runtime.controls["grid_charge"], 0)

    async def test_second_battery_failure_reports_partial_result(self):
        self.runtime.allow_control = True
        self.peer.fail_write_index = 2
        with self.assertRaises(PartialWrite):
            await self.runtime.set_control("backup_soc", 40)
        self.assertEqual(self.model.get_float(0x314A), 40)
        self.assertEqual(self.model.get_float(0x314C), 20)
        self.assertEqual(self.runtime.controls["backup_soc_2"], 20)

    async def test_second_battery_soc_constraints_checked_before_writes(self):
        self.runtime.allow_control = True
        self.model.set_float(0x30B8, 50)
        self.model.set_float(0x314C, 60)
        before = self.peer.write_count
        with self.assertRaises(ControlError):
            await self.runtime.set_control("backup_soc", 40)
        self.assertEqual(self.peer.write_count, before)

    async def test_schedule_roundtrip_clear_and_mode_preservation(self):
        self.runtime.allow_control = True
        await self.runtime.set_control("work_mode", "backup")
        await self.runtime.set_schedule({"periods": [period(power=0.1)]})
        self.assertEqual(self.model.get_float(0x300C), 3)
        self.assertAlmostEqual(self.runtime.schedule.periods[0].power, 0.1)
        await self.runtime.set_schedule({"periods": [period()], "weekdays": ["sun"]}, activate=True)
        self.assertEqual(self.model.get_float(0x300C), 1)
        await self.runtime.set_schedule({"periods": []})
        self.assertEqual(self.model.get_float(0x300C), 1)
        self.assertEqual(self.model.schedule().periods, ())

    async def test_partial_schedule_never_reactivates(self):
        self.runtime.allow_control = True
        await self.runtime.set_control("work_mode", "time_of_use")
        self.peer.fail_write_index = self.peer.write_count + 3
        with self.assertRaises(PartialWrite):
            await self.runtime.set_schedule({"periods": [period()]}, activate=True)
        self.assertEqual(self.model.get_float(0x300C), 0)
        self.assertIsNone(self.runtime.schedule)

    async def test_remote_finite_duration_and_tou_mutex(self):
        self.runtime.allow_control = True
        for power, duration in ((1, 0), (1, float("inf")), (6, 10), (True, 10)):
            with self.assertRaises(ControlError):
                await self.runtime.set_battery_power(power, duration)
        await self.runtime.set_battery_power(-2, 1)
        self.assertEqual(self.model.get_float(0xC646), 1)
        with self.assertRaises(ControlError):
            await self.runtime.set_schedule({"periods": []})
        self.now += 2
        await asyncio.sleep(1.05)
        self.assertEqual(self.model.get_float(0xC646), 0)
        self.assertIsNone(self.runtime.remote_deadline)

    async def test_watchdog_independent_of_ha_and_no_automatic_resume(self):
        self.runtime.allow_control = True
        await self.runtime.set_battery_power(2, 300)
        self.now += 61
        self.model.tick(datetime(2026, 10, 19, 12), 1)
        self.assertEqual(self.model.get_float(0xC646), 0)
        other = DeviceRuntime(self.client, "sinexcel_v2")
        await other.initialize()
        self.assertIsNone(other.remote_deadline)
        self.assertFalse(other.allow_control)

    async def test_renewal_queued_at_expiry_keeps_the_new_lease(self):
        self.runtime.allow_control = True
        await self.runtime.set_battery_power(1, 1)
        old_task = self.runtime._lease_task
        await asyncio.sleep(0)
        await self.runtime._lock.acquire()
        try:
            self.now += 2
            # Queue the expiring worker before a renewal of the same device.
            await asyncio.sleep(1.05)
            renewal = asyncio.create_task(self.runtime.set_battery_power(2, 300))
            await asyncio.sleep(0)
        finally:
            self.runtime._lock.release()
        await renewal
        await asyncio.gather(old_task, return_exceptions=True)
        self.assertEqual(self.model.get_float(0xC646), 1)
        self.assertEqual(self.model.get_float(0xC648), 2)
        self.assertEqual(self.runtime.remote_deadline, self.now + 300)
        self.assertFalse(self.runtime._lease_task.done())
        self.assertEqual(self.runtime.last_command, "confirmed")

    async def test_stop_then_concurrent_start_retains_the_new_worker(self):
        self.runtime.allow_control = True
        await self.runtime.set_battery_power(1, 300)
        old_task = self.runtime._lease_task
        await asyncio.sleep(0)
        await asyncio.gather(self.runtime.stop_remote(), self.runtime.set_battery_power(2, 300))
        await asyncio.gather(old_task, return_exceptions=True)
        self.assertEqual(self.model.get_float(0xC646), 1)
        self.assertEqual(self.runtime.remote_deadline, self.now + 300)
        self.assertFalse(self.runtime._lease_task.done())

    async def test_framing_fc04_and_non_byte_aligned_fc02(self):
        self.model.bits[2][0x1048 + 8] = True
        bits = await self.client.read_discrete_inputs(0x1048, 9, unit_id=2)
        self.assertEqual(bits, [False] * 8 + [True])
        self.assertEqual(await self.client.read_input_registers(0x2000, 1), [0x641])

    async def test_sinexcel_power_signs_become_standard(self):
        self.runtime.allow_control = True
        await self.runtime.set_battery_power(-2, 300)  # 0xC648: negative = charge
        self.model.tick(datetime(2026, 10, 19, 12), 1)
        raw = self.model.get_float(0x1272)
        self.assertGreater(raw, 0)  # device: + = charging
        values = await self.runtime.refresh(
            {"battery_power", "grid_power", "solar_power", "load_power"}, force=True
        )
        self.assertAlmostEqual(values["battery_power"], -raw, places=4)  # standard: - = charge
        self.assertAlmostEqual(values["grid_power"], -self.model.get_float(0x1276), places=4)
        self.assertAlmostEqual(
            values["load_power"],
            values["solar_power"] + values["battery_power"] + values["grid_power"],
            places=3,
        )
        self.assertTrue(self.runtime.profile.power_direction_verified)

    async def test_device_without_control_registers_gets_no_controls(self):
        for address in (0xC646, 0xC647):  # e.g. firmware without EMS remote control
            self.model.registers.pop(address)
        await self.client.close()  # capabilities are re-probed after reconnect
        await self.runtime.refresh({"solar_power"})
        self.assertFalse(self.runtime.profile.controls_supported)
        self.assertNotIn("ctrl_work_mode", self.runtime.profile.capabilities)
        self.runtime.allow_control = True
        before = self.peer.write_count
        with self.assertRaises(ControlError):
            await self.runtime.set_control("grid_charge", True)
        self.assertEqual(self.peer.write_count, before)

    async def test_rated_power_comes_from_protocol_register(self):
        self.assertEqual(self.runtime.profile.max_power_kw, 5.0)  # 0x2008 = 500
        self.assertEqual(self.runtime.profile.tou_limits.max_power_kw, 5.0)
        self.model.config = replace(self.model.config, rated_kw=80)
        self.model.tick(datetime(2026, 10, 19, 12), 0)
        await self.client.close()
        await self.runtime.refresh({"solar_power"})
        self.assertEqual(self.runtime.profile.max_power_kw, 50.0)  # capped by protocol ranges

    async def test_heartbeat_uses_protocol_register_and_watchdog_escape(self):
        self.runtime.allow_control = True
        await self.runtime.set_battery_power(-1, 300)
        self.assertEqual(self.model.get_float(0xC068), 1)  # EMS Alive
        self.assertEqual(self.model.get_float(0xC06C), 1)  # escape: self-consumption
        self.now += 61
        self.model.tick(datetime(2026, 10, 19, 12), 1)
        self.assertEqual(self.model.get_float(0xC068), 0)  # Dead after timeout
        self.assertEqual(self.model.get_float(0xC646), 0)

    async def test_tou_dates_are_protocol_timestamps(self):
        self.runtime.allow_control = True
        await self.runtime.set_schedule({"periods": [period()]})
        words = [self.model.registers[0xC0B4 + i] for i in range(6)]
        start, end = words[2] << 16 | words[3], words[4] << 16 | words[5]
        today = datetime.now().date()
        self.assertEqual(
            start,
            int(datetime(today.year, today.month, today.day, tzinfo=timezone.utc).timestamp()),
        )
        self.assertGreater(end, start + 365 * 86400 * 50)  # long-lived, not "today only"
        self.assertTrue(self.model.schedule_in_effect(today))

    async def test_legacy_soc_not_repurposed_as_total_and_single_only_fallback(self):
        self.model.set_float(0x1212, 40)
        self.model.set_float(0xC644, 70)
        values = await self.runtime.refresh({"battery_soc", "battery_total_soc"})
        self.assertEqual((values["battery_soc"], values["battery_total_soc"]), (40, 70))
        self.model.registers.pop(0xC644)
        self.model.registers.pop(0xC645)
        self.now += 30
        values = await self.runtime.refresh({"battery_total_soc"})
        self.assertIsNone(values["battery_total_soc"])
        self.model.set_float(0x3036, 0)
        await self.client.close()  # Capability probes are refreshed after reconnect.
        values = await self.runtime.refresh({"battery_total_soc"}, force=True)
        self.assertEqual(values["battery_total_soc"], 40)

    async def test_read_only_server_rejects_writes(self):
        from dataclasses import replace

        self.model.config = replace(self.model.config, writes=False)
        with self.assertRaises(DeviceException):
            await self.client.write_registers(0x322A, [0x3F80, 0])

    async def test_capability_matrix_shrinks_after_reconnect(self):
        from dataclasses import replace

        self.model.config = replace(
            self.model.config, machine_type=1, pv_count=1, batteries=1, bms=False
        )
        self.model.tick(datetime(2026, 10, 19, 12), 0)
        await self.client.close()
        await self.runtime.refresh({"solar_power"})
        keys = {p.key for p in self.runtime.points()}
        self.assertIn("pv1_power", keys)
        for absent in (
            "pv2_power",
            "grid_voltage_b",
            "grid_voltage_c",
            "battery_2_soc",
            "battery_soh",
        ):
            self.assertNotIn(absent, keys)

    async def test_rejected_and_timed_out_writes_never_get_retried(self):
        self.runtime.allow_control = True
        for fault in ("reject_write", "disconnect"):
            self.peer.fault = fault
            before = self.peer.write_count
            with self.subTest(fault=fault), self.assertRaises(PartialWrite):
                await self.runtime.set_control("grid_charge", True)
            self.assertEqual(self.peer.write_count - before, 1)

    async def test_concurrent_read_and_write_share_device_lock(self):
        self.runtime.allow_control = True
        await asyncio.gather(
            self.runtime.refresh({"solar_power"}), self.runtime.set_control("grid_charge", True)
        )
        self.assertEqual(self.runtime.controls["grid_charge"], 1)
        self.assertEqual(self.runtime.last_command, "confirmed")

    async def test_missing_control_readback_blocks_mutation(self):
        self.runtime.allow_control = True
        self.model.set_float(0xC646, float("nan"))
        before = self.peer.write_count
        from energylib_under_test.energylib_core.transport.modbus_tcp import InvalidResponse

        with self.assertRaises(InvalidResponse):
            await self.runtime.set_battery_power(1, 10)
        self.assertEqual(self.peer.write_count, before)
