"""End-to-end TCP bench tests for profiles, polling, controls and failure states."""

import asyncio
import time
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone
from unittest.mock import patch

from energylib_under_test.energylib_core.legacy import IdentityMismatch
from energylib_under_test.energylib_core.model.points import CONTROLS, CORE
from energylib_under_test.energylib_core.model.tou import TouLimits, TouSchedule
from energylib_under_test.energylib_core.planner import plan_reads
from energylib_under_test.energylib_core.profiles.sinexcel_v2 import Mapping
from energylib_under_test.energylib_core.runtime import ControlError, DeviceRuntime, PartialWrite
from energylib_under_test.energylib_core.transport.modbus_tcp import (
    DeviceException,
    EnergyLibError,
    InvalidResponse,
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
    async def test_retry_rechecks_reduced_power_limit_before_resending(self):
        self.runtime.allow_control = True
        original = self.client._request_once
        failed = False

        async def reduced(pdu, unit):
            nonlocal failed
            if pdu[:3] == b"\x10\x30\x04" and not failed:
                failed = True
                self.model.config = replace(self.model.config, rated_kw=3)
                self.model.tick(datetime(2026, 10, 19, 12, tzinfo=timezone.utc), 0)
                raise InvalidResponse("Injected reconnect and lower device limit")
            return await original(pdu, unit)

        with patch.object(self.client, "_request_once", reduced):
            with self.assertRaises(PartialWrite):
                await self.runtime.set_control("export_limit", 4)
        self.assertEqual(self.runtime.profile.max_power_kw, 3)
        self.assertEqual(self.client.write_retries, 0)
        self.assertEqual(self.peer.write_count, 0)  # No oversized limit was sent.

    async def test_retry_rechecks_changed_soc_constraint_before_resending(self):
        self.runtime.allow_control = True
        original = self.client._request_once
        failed = False

        async def changed(pdu, unit):
            nonlocal failed
            if pdu[:3] == b"\x10\x31\x4a" and not failed:
                failed = True
                self.model.set_float(0x3046, 50)
                raise InvalidResponse("Injected change to minimum SOC")
            return await original(pdu, unit)

        with patch.object(self.client, "_request_once", changed):
            with self.assertRaises(PartialWrite):
                await self.runtime.set_control("backup_soc", 40)
        self.assertEqual(self.peer.write_count, 0)
        self.assertEqual(self.client.write_retries, 0)

    async def test_heartbeat_and_remote_enable_never_repeat_uncertain_writes(self):
        self.peer.fault = "disconnect"
        for address in (0xC068, 0xC646):
            await self.runtime._identity()
            before = self.peer.write_count
            with self.subTest(address=address), self.assertRaises(EnergyLibError):
                await self.runtime.profile.write_float(self.client, address, 1)
            self.assertEqual(self.peer.write_count - before, 1)
        self.assertEqual(self.client.write_retries, 0)

    async def test_poll_recovers_read_retry_and_rechecks_identity(self):
        original = self.client._request_once
        failed = False

        async def interrupted(pdu, unit):
            nonlocal failed
            if pdu == b"\x03\x12\x70\x00\x02" and not failed:
                failed = True
                raise InvalidResponse("Injected frame error")
            return await original(pdu, unit)

        with patch.object(self.client, "_request_once", interrupted):
            values = await self.runtime.refresh({"solar_power"})
        self.assertIsNotNone(values["solar_power"])
        self.assertEqual(self.client.read_retries, 1)
        self.assertEqual(self.client.write_generation, self.client.generation)
        self.assertEqual(self.peer.write_count, 0)

    async def test_changed_device_after_read_retry_is_not_published(self):
        original = self.client._request_once
        failed = False

        async def replaced(pdu, unit):
            nonlocal failed
            if pdu == b"\x03\x12\x70\x00\x02" and not failed:
                failed = True
                self.model.registers[0xC104] = 0x4F54
                raise InvalidResponse("Injected endpoint replacement")
            return await original(pdu, unit)

        with patch.object(self.client, "_request_once", replaced):
            with self.assertRaises(IdentityMismatch):
                await self.runtime.refresh({"solar_power"})
        self.assertNotIn("solar_power", self.runtime.values)
        self.assertEqual(self.peer.write_count, 0)

    async def test_reconnect_rechecks_identity_before_parameter_write(self):
        self.runtime.allow_control = True
        await self.client.close()
        original = self.client._request_once
        calls = []

        async def record(pdu, unit):
            calls.append((pdu[0], int.from_bytes(pdu[1:3], "big")))
            return await original(pdu, unit)

        with patch.object(self.client, "_request_once", record):
            await self.runtime.set_control("work_mode", "backup")
        self.assertLess(calls.index((3, 0xC102)), calls.index((16, 0x300C)))
        self.assertEqual(self.peer.write_count, 1)
        self.assertEqual(self.client.write_generation, self.client.generation)
        self.assertEqual(self.runtime.last_command, "confirmed")

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

    async def test_rejected_remote_start_returns_to_idle_and_restores_watchdog(self):
        self.runtime.allow_control = True
        self.model.set_float(0xC06A, 0)
        self.model.set_float(0xC06C, 0)
        original = self.model.apply_write

        def refuse_power(address, words):
            # Like the real device without a battery: 0xC648 reads back 0.
            if address == 0xC648:
                return None
            return original(address, words)

        with patch.object(self.model, "apply_write", refuse_power):
            with self.assertRaises(ControlError) as caught:
                await self.runtime.set_battery_power(1, 60)
        self.assertNotIsInstance(caught.exception, PartialWrite)
        self.assertEqual(self.runtime.remote_status, "idle")
        self.assertIsNone(self.runtime.remote_deadline)
        self.assertEqual((self.model.get_float(0xC06A), self.model.get_float(0xC06C)), (0, 0))

    async def test_export_limit_can_be_switched_off_without_zero_export(self):
        self.runtime.allow_control = True
        await self.runtime.set_control("export_limit_enable", False)
        before = self.peer.write_count
        await self.runtime.set_control("export_limit", 5)
        self.assertEqual(self.peer.write_count - before, 1)
        self.assertEqual((self.model.get_float(0x3002), self.model.get_float(0x3004)), (0, 5))
        self.assertEqual(self.runtime.controls["export_limit_enable"], 0)
        await self.runtime.set_control("export_limit_enable", True)
        self.assertEqual((self.model.get_float(0x3002), self.model.get_float(0x3004)), (1, 5))
        self.assertEqual(self.runtime.controls["export_limit_enable"], 1)
        await self.runtime.set_control("export_limit_enable", False)
        # Unlimited again; the stored limit value is kept for the next enable.
        self.assertEqual((self.model.get_float(0x3002), self.model.get_float(0x3004)), (0, 5))
        self.assertEqual(self.runtime.controls["export_limit_enable"], 0)
        self.model.set_float(0x3002, 2)  # another limit mode set from the App
        await self.runtime.refresh(set(), force=True)
        self.assertEqual(self.runtime.controls["export_limit_enable"], 1)

    async def test_heartbeat_accepts_a_consumed_alive_flag_while_the_session_runs(self):
        self.runtime.allow_control = True
        original = self.model.apply_write

        def consume_alive(address, words):
            result = original(address, words)
            if address == 0xC068:
                self.model.set_float(0xC068, 0)
            return result

        with patch.object(self.model, "apply_write", consume_alive):
            await self.runtime.set_battery_power(1, 60)
        self.assertEqual(self.runtime.remote_status, "discharging")
        await self.runtime.stop_remote()

    async def test_uncertain_remote_write_never_claims_running_or_stopped(self):
        self.runtime.allow_control = True
        original = self.model.apply_write

        def lost_enable(address, words):
            result = original(address, words)
            if address == 0xC646:
                raise ConnectionError  # session may be active; outcome unknown
            return result

        self.peer.fault = "normal"
        with patch.object(self.model, "apply_write", lost_enable):
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
            expired = last.year < 2026
            self.assertEqual(
                self.runtime.schedule_status(datetime(2026, 10, 20, 11)),
                "expired" if expired else "outside_period",
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
            written = [self.model.registers[0xC0B6 + i] for i in range(4)]
            if expired:
                # An expired window would never run the saved plan: start today.
                start, end = self.runtime.profile.fresh_validity(datetime.now().date())
                self.assertEqual(
                    written, u32_words(int(start.timestamp())) + u32_words(int(end.timestamp()))
                )
                self.assertEqual(self.runtime.schedule.valid_until, end)
                # The device stores window ends as 23:59:59 (observed on hardware).
                self.assertEqual((end.hour, end.minute, end.second), (23, 59, 59))
                continue
            self.assertEqual(written, expected)
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
        self.now += 30
        await self.runtime.refresh({"solar_energy", "solar_power"})
        at_30 = self.runtime.last_frame_count
        self.assertNotIn("pv1_power", self.runtime.values)
        self.now += 30
        await self.runtime.refresh({"solar_energy", "solar_power"})
        # The 60 s energy point is read again only on its own tier.
        self.assertEqual(self.runtime.last_frame_count, at_30 + 1)

    async def test_flow_powers_poll_every_10_s_and_other_points_every_30_s(self):
        keys = {"solar_power", "grid_power", "grid_voltage_a"}
        await self.runtime.refresh(keys)
        full = self.runtime.last_frame_count
        self.now += 10
        await self.runtime.refresh(keys)
        fast = self.runtime.last_frame_count
        self.assertLess(fast, full)
        self.model.set_float(0x1270, 4.25)
        self.model.set_float(0x1000, 199.0)
        self.now += 10
        values = await self.runtime.refresh(keys)
        self.assertEqual(self.runtime.last_frame_count, fast)
        self.assertEqual(values["solar_power"], 4.25)
        self.assertNotEqual(values["grid_voltage_a"], 199.0)
        self.now += 10
        values = await self.runtime.refresh(keys)
        self.assertGreater(self.runtime.last_frame_count, fast)
        self.assertEqual(values["grid_voltage_a"], 199.0)

    async def test_four_flow_powers_share_one_response_during_fast_changes(self):
        keys = {"solar_power", "battery_power", "load_power", "grid_power"}
        await self.runtime.refresh(keys, force=True)
        self.now += 10
        for address, value in ((0x1270, 1.5), (0x1272, -0.3), (0x1274, 0.2), (0x1276, 1.6)):
            self.model.set_float(address, value)
        self.peer.read_log.clear()
        original = self.client._request_once

        async def changing(pdu, unit):
            response = await original(pdu, unit)
            if pdu[0] == 3:
                for address, value in ((0x1270, 3), (0x1272, 0), (0x1274, 0.1), (0x1276, 2.9)):
                    self.model.set_float(address, value)
            return response

        with patch.object(self.client, "_request_once", changing):
            values = await self.runtime.refresh(keys)
        self.assertEqual(list(self.peer.read_log), [(3, 1, 0x1270, 8)])
        self.assertAlmostEqual(values["solar_power"], 1.5)
        self.assertAlmostEqual(values["battery_power"], 0.3)
        self.assertAlmostEqual(values["load_power"], 0.2)
        self.assertAlmostEqual(values["grid_power"], -1.6)
        self.assertAlmostEqual(
            values["solar_power"]
            + values["battery_power"]
            + values["grid_power"]
            - values["load_power"],
            0,
            places=6,
        )

    async def test_unsupported_flow_block_falls_back_once_then_reprobes_on_reconnect(self):
        keys = {"solar_power", "battery_power", "load_power", "grid_power"}
        await self.runtime.refresh(keys, force=True)
        self.peer.fault = "flow_single_only"
        self.now += 10
        self.peer.read_log.clear()
        values = await self.runtime.refresh(keys)
        self.assertTrue(all(values[k] is not None for k in keys))
        self.assertEqual(
            list(self.peer.read_log),
            [(3, 1, 0x1270, 8)] + [(3, 1, a, 2) for a in range(0x1270, 0x1278, 2)],
        )
        self.now += 10
        self.peer.read_log.clear()
        await self.runtime.refresh(keys)
        self.assertEqual(list(self.peer.read_log), [(3, 1, a, 2) for a in range(0x1270, 0x1278, 2)])
        self.assertEqual(self.client.read_retries, 0)
        self.peer.fault = "normal"
        await self.client.close()
        self.peer.read_log.clear()
        await self.runtime.refresh(keys)
        self.assertIn((3, 1, 0x1270, 8), self.peer.read_log)

    async def test_malformed_flow_block_is_rejected_without_silent_single_point_fallback(self):
        keys = {"solar_power", "battery_power", "load_power", "grid_power"}
        await self.runtime.refresh(keys, force=True)
        previous = dict(self.runtime.values)
        self.now += 10
        self.peer.read_log.clear()
        self.peer.fault = "short_frame"
        with self.assertRaises(EnergyLibError):
            await self.runtime.refresh(keys)
        self.assertTrue(self.runtime.profile._flow_block_enabled)
        self.assertEqual(list(self.peer.read_log), [(3, 1, 0x1270, 8)] * 4)
        self.assertEqual(self.runtime.values["solar_power"], previous["solar_power"])

    async def test_unrelated_quantity_errors_do_not_enable_read_fallback(self):
        block = plan_reads([Mapping("voltage", 0x1000)])[0]
        self.assertFalse(self.runtime.profile.can_fallback_read(block, 3))
        self.assertTrue(self.runtime.profile._flow_block_enabled)

    async def test_a_tick_a_few_ms_early_still_reads_the_flow_powers(self):
        await self.runtime.refresh({"solar_power"})
        self.model.set_float(0x1270, 3.5)
        self.now += 9.995  # HA schedules from the previous start, not after the reads
        values = await self.runtime.refresh({"solar_power"})
        self.assertEqual(values["solar_power"], 3.5)

    async def test_identity_and_controls_follow_the_30_s_tier(self):
        self.runtime.allow_control = True
        keys = {"solar_power"}
        await self.runtime.refresh(keys, force=True)
        self.now += 10
        await self.runtime.refresh(keys)
        fast = self.runtime.last_frame_count
        self.assertEqual(fast, 1)  # power only: no identity, no control registers
        self.now += 20
        await self.runtime.refresh(keys)
        self.assertGreater(self.runtime.last_frame_count, fast + 2)

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
        await self.client.close()
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
            ("min_soc", 101),
            ("work_mode", "remote"),
        ):
            with self.subTest(key=key, value=value), self.assertRaises(ControlError):
                await self.runtime.set_control(key, value)
        self.assertEqual(self.peer.write_count, 0)
        await self.runtime.set_control("backup_soc", 35)
        self.assertEqual(self.model.get_float(0x314A), 35)
        self.assertEqual(self.model.get_float(0x314C), 35)
        self.assertEqual(self.runtime.controls["backup_soc"], 35)

    async def test_soc_conflict_is_rejected_from_cached_values_without_reads(self):
        self.runtime.allow_control = True
        # Device already has min SOC above backup SOC, e.g. changed from the App.
        for address, value in ((0x3046, 50), (0x30B8, 50), (0x314A, 30), (0x314C, 30)):
            self.model.set_float(address, value)
        # A conflict elsewhere must not block unrelated controls.
        await self.runtime.set_control("grid_charge", True)
        self.assertEqual(self.runtime.controls["grid_charge"], 1)
        await self.runtime.set_control("export_limit", 2)
        self.assertEqual(self.runtime.controls["export_limit"], 2)
        writes, requests = self.peer.write_count, self.client.requests
        with self.assertRaises(ControlError) as caught:
            await self.runtime.set_control("backup_soc", 40)
        self.assertNotIsInstance(caught.exception, PartialWrite)
        # Judged from the cached read-back: no write and no extra read.
        self.assertEqual((self.peer.write_count, self.client.requests), (writes, requests))
        self.assertEqual(self.runtime.controls["backup_soc"], 30)
        # Raising backup above minimum resolves the conflict and is accepted.
        await self.runtime.set_control("backup_soc", 60)
        self.assertEqual(self.runtime.controls["backup_soc"], 60)
        self.assertEqual(self.model.get_float(0x314C), 60)
        # Battery 2 is checked against its own cached minimum.
        self.model.set_float(0x30B8, 70)
        await self.runtime.refresh(set(), force=True)
        with self.assertRaises(ControlError):
            await self.runtime.set_control("backup_soc", 65)

    async def test_mode_change_checks_remote_session_from_cache_or_one_read(self):
        self.runtime.allow_control = True
        self.model.set_float(0xC646, 1)  # session started from the App
        self.runtime.controls = {}
        requests = self.client.requests
        with self.assertRaises(ControlError):
            await self.runtime.set_control("work_mode", "backup")
        self.assertEqual(self.client.requests - requests, 1)  # one read, no write
        self.assertEqual(self.model.get_float(0x300C), 0)
        self.model.set_float(0xC646, 0)
        await self.runtime.refresh(set(), force=True)
        requests = self.client.requests
        await self.runtime.set_control("work_mode", "backup")
        self.assertEqual(self.runtime.controls["work_mode"], "backup")
        # With a cached state the edit is just the write and its read-back.
        self.assertEqual(self.client.requests - requests, 2)

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
        apply = self.model.apply_write
        with patch.object(
            self.model, "apply_write", lambda a, w: 1 if a == 0x314C else apply(a, w)
        ):
            with self.assertRaises(PartialWrite):
                await self.runtime.set_control("backup_soc", 40)
        self.assertEqual(self.model.get_float(0x314A), 40)
        self.assertEqual(self.model.get_float(0x314C), 20)
        self.assertEqual(self.runtime.controls["backup_soc_2"], 20)

    async def test_transient_second_battery_disconnect_recovers_parameter_only(self):
        self.runtime.allow_control = True
        self.peer.fail_write_index = 2
        await self.runtime.set_control("backup_soc", 40)
        self.assertEqual(self.peer.write_count, 3)
        self.assertEqual(self.runtime.controls["backup_soc"], 40)
        self.assertEqual(self.runtime.controls["backup_soc_2"], 40)

    async def test_second_battery_soc_rejection_retains_actual_partial_result(self):
        self.runtime.allow_control = True
        self.model.set_float(0x30B8, 50)
        self.model.set_float(0x314C, 60)
        before = self.peer.write_count
        apply = self.model.apply_write
        with patch.object(
            self.model, "apply_write", lambda a, w: 3 if a == 0x314C else apply(a, w)
        ):
            with self.assertRaises(PartialWrite):
                await self.runtime.set_control("backup_soc", 40)
        self.assertEqual(self.peer.write_count, before + 2)
        self.assertEqual(self.runtime.last_command, "uncertain")
        self.assertEqual(self.runtime.controls["backup_soc"], 40)
        self.assertEqual(self.runtime.controls["backup_soc_2"], 60)

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

    async def test_refusals_are_not_retried_and_parameter_retries_are_bounded(self):
        self.runtime.allow_control = True
        for fault in ("reject_write", "disconnect"):
            self.peer.fault = fault
            before = self.peer.write_count
            with self.subTest(fault=fault), self.assertRaises(PartialWrite):
                await self.runtime.set_control("grid_charge", True)
            self.assertEqual(self.peer.write_count - before, 1 if fault == "reject_write" else 4)

    async def test_concurrent_read_and_write_share_device_lock(self):
        self.runtime.allow_control = True
        await asyncio.gather(
            self.runtime.refresh({"solar_power"}), self.runtime.set_control("grid_charge", True)
        )
        self.assertEqual(self.runtime.controls["grid_charge"], 1)
        self.assertEqual(self.runtime.last_command, "confirmed")

    async def test_basic_control_reads_only_dependencies_and_preserves_poll_deadline(self):
        self.runtime.allow_control = True
        await self.runtime.refresh({"work_mode", "solar_power"}, force=True)
        deadline = self.runtime._timestamps["controls"]
        for key, value, expected, frames in (
            ("export_limit", 2.3, {0x3004}, 2),
            ("grid_charge", True, {0x322A}, 2),
            ("backup_soc", 35, {0x314A, 0x314C}, 4),
            ("min_soc", 12, {0x3046, 0x30B8}, 4),
            ("work_mode", "backup", {0x300C}, 2),
        ):
            with self.subTest(key=key):
                self.peer.read_log.clear()
                before = self.client.requests
                await self.runtime.set_control(key, value)
                reads = {address for _, _, address, _ in self.peer.read_log}
                self.assertEqual(reads, expected)
                self.assertEqual(self.client.requests - before, frames)
                self.assertEqual(self.runtime.last_control_timing["requests"], frames)
                self.assertEqual(self.runtime._timestamps["controls"], deadline)
        self.assertEqual(self.runtime.values["work_mode"], "backup")

    async def test_basic_edits_start_with_write_then_exact_target_readback(self):
        self.runtime.allow_control = True
        for batteries in (1, 2):
            self.model.set_float(0x3036, int(batteries == 2))
            await self.runtime.initialize()
            await self.runtime.refresh(set(), force=True)
            for key, value, addresses in (
                ("backup_soc", 35, (0x314A, 0x314C)),
                ("min_soc", 12, (0x3046, 0x30B8)),
                ("work_mode", "backup", (0x300C,)),
                ("export_limit", 2.3, (0x3004,)),
                ("export_limit_enable", True, (0x3002,)),
                ("grid_charge", True, (0x322A,)),
            ):
                calls = []
                original = self.client._request_once

                async def record(pdu, unit):
                    calls.append((pdu[0], int.from_bytes(pdu[1:3], "big")))
                    return await original(pdu, unit)

                with self.subTest(batteries=batteries, key=key):
                    with patch.object(self.client, "_request_once", record):
                        await self.runtime.set_control(key, value)
                    self.assertEqual(
                        calls,
                        [(fc, address) for address in addresses[:batteries] for fc in (16, 3)],
                    )
                    self.assertEqual(
                        self.runtime.last_control_timing["requests"], len(addresses[:batteries]) * 2
                    )
                    if key == "export_limit":
                        self.assertAlmostEqual(self.runtime.controls[key], value, places=5)
                    else:
                        self.assertEqual(self.runtime.controls[key], value)

    async def test_waiting_control_preempts_poll_between_requests_and_resumes_due_points(self):
        self.runtime.allow_control = True
        await self.runtime.refresh({"solar_power", "load_power"}, force=True)
        previous = dict(self.runtime.values)
        self.now += 10
        started, release = asyncio.Event(), asyncio.Event()
        original = self.runtime._read
        calls = []

        async def held_read(mapping):
            calls.append(mapping.address)
            if len(calls) == 1:
                started.set()
                await release.wait()
            return await original(mapping)

        with patch.object(self.runtime, "_read", held_read):
            poll = asyncio.create_task(self.runtime.refresh({"solar_power", "load_power"}))
            await started.wait()
            command = asyncio.create_task(self.runtime.set_control("grid_charge", True))
            await asyncio.sleep(0)
            self.assertEqual(self.runtime._pending_controls, 1)
            release.set()
            snapshot = await poll
            await command
        self.assertEqual(len(calls), 1)
        self.assertEqual(snapshot, {k: previous[k] for k in snapshot})
        self.assertEqual(self.runtime._pending_controls, 0)
        self.peer.read_log.clear()
        await self.runtime.refresh({"solar_power", "load_power"})
        self.assertEqual({a for _, _, a, _ in self.peer.read_log}, {0x1270, 0x1274})

    async def test_cancelled_queued_control_does_not_leave_poll_preemption_active(self):
        self.runtime.allow_control = True
        await self.runtime._lock.acquire()
        command = asyncio.create_task(self.runtime.set_control("grid_charge", True))
        await asyncio.sleep(0)
        command.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await command
        self.runtime._lock.release()
        self.assertEqual(self.runtime._pending_controls, 0)
        self.assertEqual(self.peer.write_count, 0)
        self.assertIsNotNone((await self.runtime.refresh({"solar_power"}))["solar_power"])

    async def test_priority_reaches_internal_identity_alarm_control_and_schedule_reads(self):
        self.runtime.allow_control = True
        for enabled, target in (
            ({"solar_power"}, 0x2000),
            ({"alarm_status"}, 0x1048),
            (set(), 0x300C),
            ({"tou_schedule"}, 0xC014),
        ):
            with self.subTest(target=hex(target)):
                await self.runtime.refresh(enabled, force=True)
                self.now += 300
                old_controls = dict(self.runtime.controls)
                started, release = asyncio.Event(), asyncio.Event()
                original = self.client._request_once
                poll_reads = []

                async def held(pdu, unit):
                    address = int.from_bytes(pdu[1:3], "big")
                    if self.runtime._poll_task is asyncio.current_task():
                        poll_reads.append(address)
                        if address == target:
                            started.set()
                            await release.wait()
                    return await original(pdu, unit)

                with patch.object(self.client, "_request_once", held):
                    poll = asyncio.create_task(self.runtime.refresh(enabled))
                    await asyncio.wait_for(started.wait(), 2)
                    command = asyncio.create_task(self.runtime.set_control("grid_charge", True))
                    await asyncio.sleep(0)
                    release.set()
                    await poll
                    # No second background request follows the held exchange.
                    self.assertEqual(poll_reads[-1], target)
                    self.assertEqual(poll_reads.count(target), 1)
                    self.assertEqual(self.runtime.controls, old_controls)
                    await command
                self.assertEqual(self.runtime._pending_controls, 0)
                self.assertEqual(self.runtime._control_tasks, {})
                self.assertIsNone(self.runtime._poll_task)
                self.assertEqual(self.client.read_retries, 0)

    async def test_command_owned_recovery_poll_is_not_suppressed_by_its_priority(self):
        self.runtime.allow_control = True
        with self.runtime.prioritize_control():
            before = self.client.requests
            result = await self.runtime.refresh({"solar_power"}, force=True)
            self.assertGreater(self.client.requests, before)
            self.assertIsNotNone(result["solar_power"])
        self.assertEqual(self.runtime._pending_controls, 0)
        self.assertFalse(self.runtime._control_tasks)

    async def test_target_read_failure_after_write_is_not_reported_confirmed(self):
        self.runtime.allow_control = True
        await self.runtime.refresh({"work_mode"}, force=True)
        original = self.client.read_holding_registers
        failed = False

        async def missing_target(address, count, **kwargs):
            nonlocal failed
            if address == 0x322A and not failed:
                failed = True
                raise InvalidResponse("Injected write read-back failure")
            return await original(address, count, **kwargs)

        with patch.object(self.client, "read_holding_registers", missing_target):
            with self.assertRaises(PartialWrite):
                await self.runtime.set_control("grid_charge", True)
        self.assertEqual(self.runtime.last_command, "uncertain")
        # Recovery reads the actual device instead of inventing the requested value.
        self.assertEqual(self.runtime.controls["grid_charge"], 1)

    async def test_cancelled_after_write_does_not_keep_stale_confirmed_control_state(self):
        self.runtime.allow_control = True
        await self.runtime.refresh({"work_mode"}, force=True)
        self.assertEqual(self.runtime.controls["grid_charge"], 0)
        applied = asyncio.Event()
        original = self.client._request_once

        async def hold_ack(pdu, unit):
            response = await original(pdu, unit)
            if pdu[0] == 16:
                applied.set()
                await asyncio.Event().wait()
            return response

        with patch.object(self.client, "_request_once", hold_ack):
            command = asyncio.create_task(self.runtime.set_control("grid_charge", True))
            await asyncio.wait_for(applied.wait(), 2)
            command.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await command
        self.assertEqual(self.model.get_float(0x322A), 1)
        self.assertEqual(self.runtime.last_command, "uncertain")
        self.assertNotIn("grid_charge", self.runtime.controls)
        self.assertEqual(self.peer.write_count, 1)
        self.assertEqual(self.client.write_retries, 0)
        self.assertEqual(self.runtime._pending_controls, 0)
        self.assertFalse(self.runtime._control_tasks)
        await self.runtime.refresh({"work_mode"})
        self.assertEqual(self.runtime.controls["grid_charge"], 1)
        self.assertEqual(self.peer.write_count, 1)
        before = self.client.requests
        await self.runtime.set_control("grid_charge", False)
        self.assertEqual(self.client.requests - before, 2)

    async def test_mode_rejects_known_active_remote_without_any_requests(self):
        self.runtime.allow_control = True
        self.model.set_float(0xC646, 1)
        await self.runtime.refresh(set(), force=True)
        self.peer.read_log.clear()
        with self.assertRaises(ControlError):
            await self.runtime.set_control("work_mode", "backup")
        self.assertEqual(self.peer.write_count, 0)
        self.assertEqual(list(self.peer.read_log), [])

    async def test_missing_control_readback_blocks_mutation(self):
        self.runtime.allow_control = True
        self.model.set_float(0xC646, float("nan"))
        before = self.peer.write_count
        from energylib_under_test.energylib_core.transport.modbus_tcp import InvalidResponse

        with self.assertRaises(InvalidResponse):
            await self.runtime.set_battery_power(1, 10)
        self.assertEqual(self.peer.write_count, before)
