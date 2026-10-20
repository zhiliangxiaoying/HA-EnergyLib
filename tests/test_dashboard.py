"""Dashboard strategy: served by the integration, generated from device entities."""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from energylib_under_test.energylib_core.model.points import CONTROLS, MEASUREMENTS

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "energylib"
STRATEGY = COMPONENT / "frontend" / "energylib-dashboard.js"
NODE = shutil.which("node")


def fake_hass(devices, language, controls=True):
    """Entities named like HA would, keyed only by device and translation key."""
    entities, registry_devices = {}, {}
    for device in devices:
        registry_devices[device] = {"id": device, "name": f"EnergyLIB {device}"}
        keys = [
            ("binary_sensor" if p.kind == "problem" else "sensor", p.key)
            for p in MEASUREMENTS.values()
        ]
        if controls:
            keys += [(c.platform, c.key) for c in CONTROLS]
        for domain, key in keys:
            entity_id = f"{domain}.{device}_renamed_{key}"
            entities[entity_id] = {
                "entity_id": entity_id,
                "platform": "energylib",
                "device_id": device,
                "translation_key": key,
                "name": key,
            }
    return {"entities": entities, "devices": registry_devices, "language": language}


def referenced(value):
    """Every entity ID a generated config points at."""
    found = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in ("entity", "state_of_charge") and isinstance(child, str):
                found.add(child)
            elif key == "entities" and isinstance(child, list):
                found.update(c for c in child if isinstance(c, str))
            found |= referenced(child)
    elif isinstance(value, list):
        for child in value:
            found |= referenced(child)
    return found


def text(value):
    return json.dumps(value, ensure_ascii=False)


@unittest.skipUnless(NODE, "node is required to execute the dashboard strategy")
class StrategyOutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        scenarios = [
            {"name": "full_zh", "flow": True, "hass": fake_hass(["dev1"], "zh-Hans")},
            {"name": "read_only_en", "flow": False, "hass": fake_hass(["dev1"], "en", False)},
            {"name": "empty", "flow": False, "hass": fake_hass([], "en")},
            {"name": "two", "flow": True, "hass": fake_hass(["dev1", "dev2"], "en")},
        ]
        cls.hass = {s["name"]: s["hass"] for s in scenarios}
        run = subprocess.run(
            [NODE, str(ROOT / "tests" / "js" / "run_strategy.mjs"), str(STRATEGY)],
            input=json.dumps({"scenarios": scenarios}),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            check=True,
        )
        cls.out = json.loads(run.stdout)

    def test_registered_in_dashboard_picker(self):
        self.assertEqual(
            [s["type"] for s in self.out["registered"] if s["strategyType"] == "dashboard"],
            ["energylib"],
        )

    def test_every_default_entity_and_control_is_on_the_page(self):
        refs = referenced(self.out["full_zh"])
        self.assertTrue(refs <= set(self.hass["full_zh"]["entities"]), refs)
        expected = {f"sensor.dev1_renamed_{p.key}" for p in MEASUREMENTS.values() if p.enabled}
        expected |= {f"{c.platform}.dev1_renamed_{c.key}" for c in CONTROLS}
        expected.add("sensor.dev1_renamed_tou_schedule")
        # With controls, the work mode select replaces the read-only sensor.
        expected.remove("sensor.dev1_renamed_work_mode")
        self.assertEqual(expected - refs, set())
        self.assertIn("custom:power-flow-card-plus", text(self.out["full_zh"]))
        self.assertIn("实时能流", text(self.out["full_zh"]))

    def test_read_only_device_gets_no_controls_and_no_missing_card(self):
        page = self.out["read_only_en"]
        refs = referenced(page)
        self.assertFalse({r for r in refs if r.split(".")[0] in ("select", "number", "switch")})
        self.assertIn("sensor.dev1_renamed_work_mode", refs)
        self.assertNotIn("power-flow-card-plus", text(page))
        self.assertIn("Allow control", text(page))
        self.assertIn("Live energy flow", text(page))
        self.assertNotIn("null", text(page))

    def test_no_device_explains_what_to_do(self):
        self.assertIn("No EnergyLIB device found", text(self.out["empty"]))

    def test_one_view_per_device(self):
        views = self.out["two"]["views"]
        self.assertEqual([v["path"] for v in views], ["dev1", "dev2"])
        for view in views:
            self.assertTrue(all(f".{view['path']}_" in r for r in referenced(view)))


class StrategyServingTests(unittest.TestCase):
    def test_served_file_exists_and_is_self_contained(self):
        source = STRATEGY.read_text(encoding="utf-8")
        self.assertIn('STRATEGY_TAG = "ll-strategy-dashboard-energylib"', source)
        # Defined only after the HA app shell, so the frontend's registry sees it.
        self.assertIn('customElements.whenDefined("home-assistant")', source)
        # Entities are found by translation key, never by a fixed entity ID.
        self.assertIsNone(re.search(r"[a-z_]+\.energylib_\w+", source))
        self.assertNotIn("import(", source)
        self.assertEqual(sorted(p.name for p in STRATEGY.parent.iterdir()), [STRATEGY.name])
        dashboard = (COMPONENT / "dashboard.py").read_text(encoding="utf-8")
        self.assertIn(f'STRATEGY_FILE = "{STRATEGY.name}"', dashboard)

    def test_frontend_is_an_optional_dependency(self):
        manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
        loaded_first = set(manifest.get("after_dependencies", [])) | set(manifest["dependencies"])
        self.assertLessEqual({"frontend", "http"}, loaded_first)
