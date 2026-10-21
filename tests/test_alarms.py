"""Alarm set transitions, per-source baselines and durable bounded occurrences."""

import unittest
from datetime import datetime, timedelta, timezone

from energylib_under_test.energylib_core.model.alarms import AlarmTracker
from energylib_under_test.energylib_core.model.points import RegisterMapping
from energylib_under_test.energylib_core.profiles.sinexcel_alarms import describe_alarm


class AlarmTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 21, tzinfo=timezone.utc)
        self.tracker = AlarmTracker(
            lambda fc, unit, addr: describe_alarm("sinexcel_v2", fc, unit, addr),
            utcnow=lambda: self.now,
        )
        self.maps = [RegisterMapping("alarms", 0x1048, 202, 2, u, "bits") for u in (1, 2)]

    def observe(self, unit, *addresses):
        self.now += timedelta(seconds=30)
        self.tracker.observe(self.maps[unit - 1], [0x1048 + i in addresses for i in range(202)])

    def test_dictionary_key_scope_and_counterintuitive_severity(self):
        self.assertEqual(describe_alarm("sinexcel_v2", 2, 1, 0x10A2)["severity"], "warning")
        for p, fc, unit in (("other", 2, 1), ("sinexcel_v2", 3, 1), ("sinexcel_v2", 2, 3)):
            self.assertEqual(describe_alarm(p, fc, unit, 0x1048)["severity"], "unknown")
        self.assertNotEqual(
            describe_alarm("sinexcel_v2", 2, 1, 0x1048)["alarm_id"],
            describe_alarm("sinexcel_v2", 2, 2, 0x1048)["alarm_id"],
        )
        for addr in range(0x1048, 0x1112):
            d = describe_alarm("sinexcel_v2", 2, 1, addr)
            self.assertIn(d["severity"], {"fault", "warning", "info"})
            self.assertTrue(48 <= d["evidence_page"] <= 61)

    def test_equal_count_replacement_and_repeated_snapshot(self):
        self.observe(1)
        self.observe(2)
        self.observe(1, 0x1054)
        self.observe(1, 0x1053)
        self.observe(1, 0x1053)
        self.assertEqual(
            [(e["code"], e["change"]) for e in self.tracker.events],
            [("1054", "raised"), ("1053", "raised"), ("1054", "cleared")],
        )
        values, attrs = self.tracker.snapshot(self.maps)
        self.assertEqual(values["active_alarms"], 1)
        self.assertEqual(attrs["alarms"][0]["name"], "辅助电源故障")

    def test_missing_source_retains_fault_does_not_clear_or_show_normal(self):
        self.observe(1, 0x1054)
        self.observe(2)
        self.tracker.observe(self.maps[0], None)
        values, attrs = self.tracker.snapshot(self.maps)
        self.assertIsNone(values["fault_count"])
        self.assertTrue(values["any_fault"])
        self.assertIsNone(values["any_warning"])
        self.assertTrue(attrs["alarms"][0]["stale"])
        self.assertEqual(self.tracker.events, [])
        self.observe(1)
        self.assertEqual(self.tracker.events[0]["change"], "cleared")

    def test_source_baselines_restart_and_observation_times(self):
        self.observe(1, 0x1054)
        self.observe(2, 0x1048)
        self.assertFalse(self.tracker.events)
        data = self.tracker.export()
        other = AlarmTracker(self.tracker.describe, utcnow=lambda: self.now)
        other.restore(data)
        other.observe(self.maps[0], [False] * 202)
        other.observe(self.maps[1], [True] + [False] * 201)
        self.assertFalse(other.events)
        self.assertEqual(other.history[0]["cleared_at"], self.now.isoformat())
        self.assertEqual(other.history[1]["first_seen"], data["history"][1]["first_seen"])

    def test_history_holds_last_100_occurrences_without_losing_active_state(self):
        self.observe(1)
        self.observe(2)
        self.observe(1, *range(0x1048, 0x1112))
        self.assertEqual(len(self.tracker.history), 100)
        other = AlarmTracker(self.tracker.describe)
        other.restore(self.tracker.export())
        self.assertEqual(len(other.active), 202)
        self.assertEqual(len(other.history), 100)
        self.observe(1)
        self.assertEqual(len(self.tracker.events), 404)

    def test_grid_summary_bit_is_described_without_inventing_a_page(self):
        grid = describe_alarm("sinexcel_v2", 2, 1, 0x1014)
        self.assertEqual((grid["name"], grid["name_en"]), ("电网异常", "Grid abnormal"))
        self.assertEqual(grid["severity"], "warning")
        self.assertIsNone(grid["evidence_page"])
        # Only Unit 1 has the summary bit; the BMS rows stay in the table.
        self.assertEqual(describe_alarm("sinexcel_v2", 2, 2, 0x1014)["severity"], "unknown")
        self.assertEqual(describe_alarm("sinexcel_v2", 2, 1, 0x1110)["name"], "BMS1通讯异常")

    def test_revision_changes_only_when_occurrences_change(self):
        # The coordinator writes the state file only when this changes.
        self.observe(1, 0x1054)
        first = self.tracker.revision()
        self.observe(1, 0x1054)
        self.observe(1, 0x1054)
        self.assertEqual(self.tracker.revision(), first)
        self.observe(1, 0x1054, 0x1058)
        raised = self.tracker.revision()
        self.assertNotEqual(raised, first)
        self.observe(1, 0x1058)
        self.assertNotEqual(self.tracker.revision(), raised)

    def test_unknown_and_info_are_not_promoted_to_faults(self):
        mapping = RegisterMapping("other", 0x1112, 1, 2, 1, "bits")
        self.tracker.observe(mapping, [True])
        self.observe(1, 0x1058)
        self.observe(2)
        values, attrs = self.tracker.snapshot([*self.maps, mapping])
        self.assertEqual(values["active_alarms"], 2)
        self.assertEqual(values["fault_count"], 0)
        self.assertEqual(attrs["info_count"], 1)
        self.assertEqual(attrs["unknown_count"], 1)
