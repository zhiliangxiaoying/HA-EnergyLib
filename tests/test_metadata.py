"""Check public HA interface metadata and the HA-independent library boundary."""

import ast
import json
import unittest
from pathlib import Path

from energylib_under_test.energylib_core.model.points import (
    BUTTONS,
    CONTROLS,
    DRAFTS,
    MEASUREMENTS,
    WORK_MODES,
)
from energylib_under_test.energylib_core.profiles.sinexcel_v2 import SinexcelProfile

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "energylib"


def keys(value, prefix=""):
    result = set()
    if isinstance(value, dict):
        for key, child in value.items():
            name = prefix + "." + key
            result.add(name)
            result.update(keys(child, name))
    return result


class MetadataTests(unittest.TestCase):
    def test_translations_cover_entities_actions_and_options(self):
        original = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
        for path in (COMPONENT / "translations").glob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(keys(data), keys(original))
            for point in MEASUREMENTS.values():
                platform = "binary_sensor" if point.kind == "problem" else "sensor"
                self.assertIn(point.key, data["entity"][platform])
            for control in CONTROLS + DRAFTS:
                self.assertIn(control.key, data["entity"][control.platform])
            self.assertEqual(set(data["entity"]["button"]), set(BUTTONS))
            self.assertIn("command_failed", data["options"]["abort"])
        self.assertEqual(
            set(original["services"]),
            {
                "set_battery_power",
                "stop_battery_power",
                "set_remote_parameters",
                "set_remote_access",
                "set_tou_schedule",
                "clear_tou_schedule",
            },
        )

    def test_every_enum_state_is_translated(self):
        files = [COMPONENT / "strings.json", *(COMPONENT / "translations").glob("*.json")]
        enums = [p for p in MEASUREMENTS.values() if p.options]
        self.assertIn("tou_schedule", {p.key for p in enums})
        for path in files:
            sensors = json.loads(path.read_text(encoding="utf-8"))["entity"]["sensor"]
            for point in enums:
                with self.subTest(file=path.name, sensor=point.key):
                    states = sensors[point.key].get("state", {})
                    self.assertEqual(set(states), set(point.options))
                    self.assertTrue(all(states.values()))

    def test_schedule_sensor_declares_every_status(self):
        self.assertEqual(
            set(MEASUREMENTS["tou_schedule"].options),
            {"charge", "discharge", "outside_period", "expired", "inactive", "remote"},
        )
        # HA translates sensor states only for the ENUM device class.
        tree = ast.parse((COMPONENT / "sensor.py").read_text(encoding="utf-8"))
        classes = next(
            n.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Assign)
            and isinstance(n.targets[0], ast.Name)
            and n.targets[0].id == "CLASSES"
        )
        mapping = {k.value: v.attr for k, v in zip(classes.keys, classes.values)}
        self.assertEqual(mapping["schedule"], "ENUM")

    def test_display_precision_by_kind(self):
        self.assertEqual(MEASUREMENTS["active_alarms"].precision, 0)
        self.assertEqual(MEASUREMENTS["battery_total_soc"].precision, 1)
        self.assertEqual(MEASUREMENTS["solar_power"].precision, 2)
        self.assertEqual(MEASUREMENTS["grid_import_energy"].precision, 2)

    def test_power_control_ranges_scale_with_rating(self):
        controls = {c.key: c for c in CONTROLS}
        self.assertEqual(controls["export_limit"].limits(12.0), (0.0, 12.0))
        self.assertEqual(controls["remote_power"].limits(12.0), (-12.0, 12.0))
        self.assertEqual(controls["backup_soc"].limits(None), (0, 100))
        for rating in (None, 0):
            with self.subTest(rating=rating), self.assertRaises(ValueError):
                controls["export_limit"].limits(rating)

    def test_sinexcel_work_mode_codes_live_in_profile(self):
        profile = SinexcelProfile()
        self.assertEqual(profile.decode_enum("work_mode", 1.0), "time_of_use")
        self.assertEqual(profile.decode_enum("work_mode", 8.0), "peak_shaving")
        self.assertEqual(profile.decode_enum("work_mode", 9.0), "other")
        self.assertIsNone(profile.decode_enum("work_mode", 1.5))
        self.assertTrue(set(profile.work_mode_codes.values()) <= set(WORK_MODES))

    def test_sensor_takes_precision_from_model(self):
        source = (COMPONENT / "sensor.py").read_text(encoding="utf-8")
        values = [
            node.value
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Assign)
            and any(
                isinstance(t, ast.Attribute) and t.attr == "_attr_suggested_display_precision"
                for t in node.targets
            )
        ]
        self.assertEqual(len(values), 1)
        self.assertIsInstance(values[0], ast.Attribute)
        self.assertEqual(values[0].attr, "precision")

    def test_core_never_imports_homeassistant(self):
        for path in (COMPONENT / "energylib_core").rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    self.assertFalse(
                        any(n.name.startswith("homeassistant") for n in node.names), path
                    )
                if isinstance(node, ast.ImportFrom):
                    self.assertFalse((node.module or "").startswith("homeassistant"), path)

    def test_ha_layer_does_not_embed_vendor_registers(self):
        for path in COMPONENT.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.Constant) and type(node.value) is int:
                    literal = ast.get_source_segment(source, node) or ""
                    if literal.lower().startswith("0x"):
                        self.assertLess(node.value, 0x1000, f"Vendor address in {path.name}")
