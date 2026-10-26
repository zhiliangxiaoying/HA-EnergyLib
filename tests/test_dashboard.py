"""Dashboard strategy: served by the integration, generated from device entities."""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from energylib_under_test.energylib_core.model.points import BUTTONS, CONTROLS, DRAFTS, MEASUREMENTS

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
            if p.key
            not in {"active_alarms", "fault_count", "warning_count", "any_fault", "any_warning"}
            and (controls or p.key not in ("remote_status", "remote_end_time"))
        ]
        if controls:
            keys += [(c.platform, c.key) for c in CONTROLS + DRAFTS]
            keys += [("button", key) for key in BUTTONS]
            keys += [("switch", "allow_remote_control")]
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
            if (key in ("entity", "state_of_charge") or key.endswith("_entity")) and isinstance(
                child, str
            ):
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
    def test_native_device_rendering_is_not_patched(self):
        result = subprocess.run(
            [NODE, str(ROOT / "tests/js/run_ems_priority.mjs"), str(STRATEGY)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=True,
        )
        self.assertEqual(json.loads(result.stdout)["result"], "passed")

    def test_dispatch_card_events_and_feedback(self):
        result = subprocess.run(
            [NODE, str(ROOT / "tests/js/run_dispatch.mjs"), str(STRATEGY)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=True,
        )
        self.assertEqual(json.loads(result.stdout)["result"], "passed")

    @classmethod
    def setUpClass(cls):
        scenarios = [
            {"name": "full_zh", "flow": True, "hass": fake_hass(["dev1"], "zh-Hans")},
            {"name": "read_only_en", "flow": False, "hass": fake_hass(["dev1"], "en", False)},
            {"name": "empty", "flow": False, "hass": fake_hass([], "en")},
            {"name": "two", "flow": True, "hass": fake_hass(["dev1", "dev2"], "en")},
        ]
        remote_only = fake_hass(["dev1"], "en")
        legacy_keys = {c.key for c in CONTROLS}
        remote_only["entities"] = {
            key: value
            for key, value in remote_only["entities"].items()
            if not (
                key.split(".")[0] in ("select", "number", "switch")
                and value["translation_key"] in legacy_keys
            )
        }
        scenarios.append({"name": "remote_only", "flow": False, "hass": remote_only})
        combined = fake_hass(["dev1"], "zh-Hans")
        combined["entities"] = {
            key: value
            for key, value in combined["entities"].items()
            if value["translation_key"]
            not in {
                "remote_target_power",
                "remote_duration",
                "start_charge",
                "start_discharge",
                "stop_remote",
                "allow_remote_control",
                "remote_end_time",
            }
        }
        scenarios.append({"name": "combined", "flow": False, "hass": combined})
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
        expected = {
            f"{'binary_sensor' if p.kind == 'problem' else 'sensor'}.dev1_renamed_{p.key}"
            for p in MEASUREMENTS.values()
            if p.enabled
        }
        expected |= {
            f"{c.platform}.dev1_renamed_{c.key}"
            for c in CONTROLS + DRAFTS
            if c.key not in ("remote_power", "remote_control")
        }
        expected.add("sensor.dev1_renamed_tou_schedule")
        expected.add("sensor.dev1_renamed_remote_status")
        # Read-only mode also supplies the explicit control-permission status.
        self.assertEqual(expected - refs, set())
        self.assertIn("custom:power-flow-card-plus", text(self.out["full_zh"]))
        # An offline TOU sensor has no plan attributes: HA's strict templates
        # must not dereference them directly (UndefinedError in the HA log).
        self.assertNotIn("s.attributes.periods", text(self.out["full_zh"]))
        self.assertIn("s.attributes.get('periods', [])", text(self.out["full_zh"]))
        self.assertIn("实时能流", text(self.out["full_zh"]))

    def test_flow_direction_matches_standard_power_signs(self):
        cards = self.out["full_zh"]["views"][0]["sections"][0]["cards"]
        guard = next(
            c["card"] for c in cards if c.get("card", {}).get("type") == "custom:energylib-flow"
        )
        flow = guard["card"]
        # Card defaults are +battery discharge, +grid import, the same as EnergyLIB.
        # Hardware check: an inverted battery drew charging as discharging.
        self.assertIs(flow["entities"]["battery"]["invert_state"], False)
        self.assertIs(flow["entities"]["grid"]["invert_state"], False)
        self.assertEqual(flow["display_zero_lines"]["mode"], "transparency")
        self.assertEqual(flow["kilo_threshold"], 1000)
        self.assertIn("已暂停能流动画", guard["warning"])
        self.assertEqual(guard["load_entity"], "sensor.dev1_renamed_load_power")
        # An asynchronous flow residual can differ from measured home load.
        # Display the supplied measurement; the guard rejects negative loads.
        self.assertEqual(flow["entities"]["home"]["entity"], guard["load_entity"])
        self.assertIs(flow["entities"]["home"]["override_state"], True)

    def test_flow_guard_pauses_invalid_load_and_recovers(self):
        result = subprocess.run(
            [NODE, str(ROOT / "tests/js/run_flow_guard.mjs"), str(STRATEGY)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=True,
        )
        self.assertEqual(json.loads(result.stdout)["result"], "passed")

    def test_read_only_device_gets_no_controls_and_no_missing_card(self):
        page = self.out["read_only_en"]
        refs = referenced(page)
        self.assertFalse(
            {r for r in refs if r.split(".")[0] in ("select", "number", "switch", "button")}
        )
        self.assertIn("sensor.dev1_renamed_work_mode", refs)
        self.assertNotIn("power-flow-card-plus", text(page))
        self.assertIn("Allow control", text(page))
        self.assertIn("Live energy flow", text(page))
        self.assertNotIn("null", text(page))

    def test_new_remote_operations_exclude_legacy_immediate_controls(self):
        status = self.out["full_zh"]["views"][0]["sections"][-1]
        refs = referenced(status)
        self.assertNotIn("number.dev1_renamed_remote_power", refs)
        self.assertNotIn("switch.dev1_renamed_remote_control", refs)
        self.assertIn("number.dev1_renamed_remote_duration", refs)
        self.assertNotIn("button.dev1_renamed_stop_remote", refs)
        self.assertIn("custom:energylib-dispatch", text(status))
        self.assertNotIn("Controls are off", text(self.out["remote_only"]))
        self.assertIn("custom:energylib-dispatch", text(self.out["remote_only"]))

    def test_no_device_explains_what_to_do(self):
        self.assertIn("No EnergyLIB device found", text(self.out["empty"]))

    def test_combined_form_does_not_require_native_parameter_or_button_entities(self):
        page = self.out["combined"]
        self.assertIn("custom:energylib-dispatch", text(page))
        self.assertTrue(referenced(page) <= set(self.hass["combined"]["entities"]))
        self.assertNotIn("number.dev1_renamed_remote_duration", referenced(page))

    def test_one_overview_per_device_then_twin(self):
        views = self.out["two"]["views"]
        self.assertEqual([v["path"] for v in views], ["dev1", "dev2", "twin"])
        twin = views[-1]
        self.assertEqual(twin["type"], "panel")
        self.assertEqual(twin["cards"][0]["type"], "iframe")
        self.assertTrue(twin["cards"][0]["url"].startswith("/energylib_twin/index.html?v="))
        for view in views[:-1]:
            self.assertTrue(all(f".{view['path']}_" in r for r in referenced(view)))

    def test_overview_columns_are_balanced(self):
        sections = self.out["full_zh"]["views"][0]["sections"]
        self.assertEqual(len(sections), 3)
        flow, energy, right = sections
        self.assertFalse(any(s.get("column_span") for s in sections))

        def headings(sec):
            return [c["heading"] for c in sec["cards"] if c["type"] == "heading"]

        # Desktop controls stay in the last column; no second-row spillover.
        self.assertEqual(headings(flow), ["实时能流", "今日电量", "累计电量", "运行记录"])
        self.assertEqual(headings(energy), ["储能电池"])
        self.assertEqual(headings(right), ["运行状态", "运行控制", "定时充放电"])
        self.assertEqual(flow["cards"][-1]["type"], "logbook")
        self.assertEqual(energy["cards"][-1]["type"], "history-graph")
        self.assertEqual(energy["cards"][-2]["type"], "statistics-graph")
        self.assertFalse(any(c["type"] == "statistics-graph" for c in flow["cards"]))
        self.assertEqual(right["cards"][-1]["type"], "custom:energylib-dispatch")
        for section in (flow, energy):
            self.assertFalse(
                any(c["type"] in ("button", "custom:energylib-dispatch") for c in section["cards"])
            )
            self.assertFalse(
                any(
                    c.get("entity", "").split(".")[0] in ("number", "switch", "select")
                    for c in section["cards"]
                    if c["type"] == "tile"
                )
            )

    def test_single_device_tabs_are_overview_and_twin(self):
        self.assertEqual(
            [(v["path"], v["title"]) for v in self.out["full_zh"]["views"]],
            [("overview", "总览"), ("twin", "数字孪生")],
        )
        # Text tabs: a view icon would replace the title in the tab bar.
        self.assertFalse([v for v in self.out["full_zh"]["views"] if "icon" in v])
        self.assertNotIn("twin", text(self.out["empty"]))

    def test_standalone_twin_and_dashboard_follow_current_resource_version(self):
        standalone = self.out["standaloneTwin"]
        generated = self.out["full_zh"]["views"][-1]
        self.assertEqual(standalone["type"], "panel")
        self.assertEqual(standalone["cards"], generated["cards"])
        self.assertEqual(
            standalone["cards"][0]["url"],
            "/energylib_twin/index.html?v=strategy-test-1",
        )
        result = subprocess.run(
            [NODE, str(ROOT / "tests/js/run_strategy.mjs"), str(STRATEGY)],
            input=json.dumps({"scenarios": [], "version": "strategy-test-2"}),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=True,
        )
        self.assertEqual(
            json.loads(result.stdout)["standaloneTwin"]["cards"][0]["url"],
            "/energylib_twin/index.html?v=strategy-test-2",
        )


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
