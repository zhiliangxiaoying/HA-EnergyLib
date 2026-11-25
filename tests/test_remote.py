"""Draft edits are pure local data; starts use immutable validated snapshots."""

import unittest

from energylib_under_test.energylib_core.model.remote import RemoteDraft


class RemoteDraftTests(unittest.TestCase):
    def test_defaults_persistence_and_signed_snapshot(self):
        draft = RemoteDraft()
        self.assertEqual(draft.export(), {"remote_target_power": 0, "remote_duration": 5})
        with self.assertRaises(ValueError):
            draft.snapshot("charge", 5)
        draft.set("remote_target_power", 2.5, 5)
        draft.set("remote_duration", 12, 5)
        snapshot = draft.snapshot("charge", 5)
        draft.set("remote_target_power", 1, 5)
        self.assertEqual(snapshot, (-2.5, 720))
        restored = RemoteDraft()
        restored.restore(draft.export())
        self.assertEqual(restored.snapshot("discharge", 5), (1, 720))

    def test_reject_invalid_power_minutes_and_changed_rating(self):
        draft = RemoteDraft()
        for key, values in (
            ("remote_target_power", [-1, 5.1, float("nan"), float("inf"), True, "2"]),
            ("remote_duration", [0, -1, 1441, 1.5, float("nan"), False, "5"]),
        ):
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    draft.set(key, value, 5)
        draft.set("remote_duration", 1440.0, 5)
        draft.set("remote_target_power", 5, 5)
        with self.assertRaises(ValueError):
            draft.snapshot("charge", 3)
        self.assertEqual(draft.export()["remote_duration"], 1440)

    def test_invalid_stored_values_do_not_start_or_create_a_task(self):
        draft = RemoteDraft()
        draft.restore({"remote_target_power": float("nan"), "remote_duration": 1.1, "other": 5})
        self.assertEqual(draft.export(), {"remote_target_power": 0, "remote_duration": 5})
