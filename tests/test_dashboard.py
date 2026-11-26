"""Dashboard strategy: served by the integration, generated from device entities."""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from energylib_under_test.energylib_core.model.points import (
    BINARY_KINDS,
    BUTTONS,
    CHARGER_POINT_KEYS,
    CONTROLS,
    DRAFTS,
    MEASUREMENTS,
)

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
            ("binary_sensor" if p.kind in BINARY_KINDS else "sensor", p.key)
            for p in MEASUREMENTS.values()
            if p.key
            not in {"active_alarms", "fault_count", "warning_count", "any_fault", "any_warning"}
            and (controls or p.key not in ("remote_status", "remote_end_time"))
        ]
        if controls:
            # The EMS mode has one worded selector (translation key home_mode).
            keys += [
                ("select", "home_mode") if c.key == "work_mode" else (c.platform, c.key)
                for c in CONTROLS + DRAFTS
            ]
            keys += [("button", key) for key in BUTTONS]
            keys += [("switch", "allow_remote_control")]
        # The EV charger is its own device under the inverter.
        charger = f"{device}_charger"
        registry_devices[charger] = {
            "id": charger,
            "name": "EV charger 1234",
            "via_device_id": device,
        }
        for domain, key in keys:
            entity_id = f"{domain}.{device}_renamed_{key}"
            entities[entity_id] = {
                "entity_id": entity_id,
                "platform": "energylib",
                "device_id": charger if key in CHARGER_POINT_KEYS else device,
                "translation_key": key,
                "name": key,
            }
    return {"entities": entities, "devices": registry_devices, "language": language}


def with_accessory():
    """One EMS with a smart plug it manages: the plug is a device under it."""
    hass = fake_hass(["dev1"], "en")
    hass["devices"]["plug1"] = {"id": "plug1", "name": "Smart plug DC40", "via_device_id": "dev1"}
    hass["entities"]["sensor.plug_power"] = {
        "entity_id": "sensor.plug_power",
        "platform": "energylib",
        "device_id": "plug1",
        "translation_key": "plug_power",
        "name": "Power",
    }
    return hass


def two_chargers():
    """An EMS with two read-only chargers (no settings) under it."""
    hass = fake_hass(["dev1"], "en", controls=False)
    hass["devices"]["dev1_charger"]["name"] = "EV charger C594"
    hass["devices"]["dev1_charger2"] = {
        "id": "dev1_charger2",
        "name": "EV charger 0002",
        "via_device_id": "dev1",
    }
    for key in ("ev_charger_status", "ev_charge_power"):
        hass["entities"][f"sensor.second_{key}"] = {
            "entity_id": f"sensor.second_{key}",
            "platform": "energylib",
            "device_id": "dev1_charger2",
            "translation_key": key,
            "name": None,
        }
    return hass


def referenced(value):
    """Every entity ID a generated config points at."""
    found = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if (key in ("entity", "state_of_charge") or key.endswith("_entity")) and isinstance(
                child, str
            ):
                found.add(child)
            elif (key == "entities" or key.endswith("_entities")) and isinstance(child, list):
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
    def test_sankey_measurement_states_and_rendering(self):
        result = subprocess.run(
            [NODE, str(ROOT / "tests/js/run_sankey.mjs"), str(STRATEGY)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=True,
        )
        self.assertEqual(json.loads(result.stdout)["result"], "passed")

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

    def test_schedule_card_saves_on_request(self):
        result = subprocess.run(
            [NODE, str(ROOT / "tests/js/run_tou.mjs"), str(STRATEGY)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=True,
        )
        self.assertEqual(json.loads(result.stdout)["result"], "passed")

    def test_mode_card_writes_on_tap(self):
        result = subprocess.run(
            [NODE, str(ROOT / "tests/js/run_mode.mjs"), str(STRATEGY)],
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
            {"name": "accessory", "flow": True, "hass": with_accessory()},
            {"name": "two_chargers", "flow": True, "hass": two_chargers()},
        ]
        remote_only = fake_hass(["dev1"], "en")
        legacy_keys = {c.key for c in CONTROLS} | {"home_mode"}
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
        gateway = fake_hass(["dev1"], "zh-Hans")
        gateway["panels"] = {"energylib-assistant": {"url_path": "energylib-assistant"}}
        gateway["user"] = {"is_admin": True}
        scenarios.append({"name": "gateway_admin", "flow": False, "hass": gateway})
        member = json.loads(json.dumps(gateway))
        member["user"] = {"is_admin": False}
        scenarios.append({"name": "gateway_member", "flow": False, "hass": member})
        no_battery = fake_hass(["dev1"], "zh-Hans")
        no_battery["states"] = {
            "sensor.dev1_renamed_alarm_status": {
                "state": "warning",
                "attributes": {"battery_disconnected": True},
            }
        }
        scenarios.append({"name": "no_battery", "flow": False, "hass": no_battery})
        # HA's Energy page set up with this inverter's solar and grid counters.
        energy_prefs = {
            "energy_sources": [
                {"type": "solar", "stat_energy_from": "sensor.dev1_renamed_solar_energy"},
                {
                    "type": "grid",
                    "flow_from": [{"stat_energy_from": "sensor.dev1_renamed_grid_import_energy"}],
                },
            ]
        }
        scenarios.append(
            {
                "name": "energy_zh",
                "flow": False,
                "hass": fake_hass(["dev1"], "zh-Hans"),
                "energyPrefs": energy_prefs,
            }
        )
        other_meter = {
            "energy_sources": [{"type": "solar", "stat_energy_from": "sensor.other_meter"}]
        }
        scenarios.append(
            {
                "name": "energy_other",
                "flow": False,
                "hass": fake_hass(["dev1"], "zh-Hans"),
                "energyPrefs": other_meter,
            }
        )
        # A read-only source with power and battery level only (AECC's local interface).
        aecc = fake_hass(["dev1"], "zh-Hans")
        aecc_keys = {
            "solar_power",
            "battery_power",
            "grid_power",
            "load_power",
            "battery_total_soc",
            "backup_load_power",
        }
        aecc["entities"] = {
            k: v for k, v in aecc["entities"].items() if v["translation_key"] in aecc_keys
        }
        scenarios.append({"name": "aecc", "flow": False, "hass": aecc})
        # AECC with a Shenggerui inverter's registers: counters, still no controls.
        counted = fake_hass(["dev1"], "zh-Hans")
        counted_keys = aecc_keys | {
            "solar_energy",
            "battery_charge_energy",
            "battery_discharge_energy",
            "load_energy",
            "grid_import_energy",
            "grid_export_energy",
            "running_state",
        }
        counted["entities"] = {
            k: v for k, v in counted["entities"].items() if v["translation_key"] in counted_keys
        }
        scenarios.append({"name": "aecc_counters", "flow": False, "hass": counted})
        # The same with Shenggerui controls allowed: no EMS mode (pending), no remote task.
        ruled = fake_hass(["dev1"], "zh-Hans")
        ruled_keys = counted_keys | {
            "inverter_power",
            "min_soc",
            "backup_soc",
            "grid_charge",
            "tou_schedule",
            "alarm_status",
        }
        ruled["entities"] = {
            k: v for k, v in ruled["entities"].items() if v["translation_key"] in ruled_keys
        }
        scenarios.append({"name": "aecc_controls", "flow": False, "hass": ruled})
        no_mode = fake_hass(["dev1"], "zh-Hans")
        no_mode["entities"] = {
            k: v for k, v in no_mode["entities"].items() if v["translation_key"] != "work_mode"
        }
        scenarios.append({"name": "no_mode_sensor", "flow": False, "hass": no_mode})
        scenarios.append(
            {
                "name": "energy_error",
                "flow": False,
                "hass": fake_hass(["dev1"], "zh-Hans"),
                "energyPrefs": "error",
            }
        )
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
            f"{c.platform}.dev1_renamed_{'home_mode' if c.key == 'work_mode' else c.key}"
            for c in CONTROLS + DRAFTS
            if c.key not in ("remote_power", "remote_control")
        }
        expected.add("sensor.dev1_renamed_tou_schedule")
        expected.add("sensor.dev1_renamed_remote_status")
        # Read-only mode also supplies the explicit control-permission status.
        self.assertEqual(expected - refs, set())
        # The energy flow is built in: no third-party card is needed.
        self.assertIn("custom:energylib-house-flow", text(self.out["full_zh"]))
        self.assertNotIn("power-flow-card-plus", text(self.out["full_zh"]))
        # The plan is edited on the page (no Jinja template reading its attributes).
        self.assertIn("custom:energylib-tou", text(self.out["full_zh"]))
        self.assertNotIn("s.attributes", text(self.out["full_zh"]))
        self.assertIn("实时能流", text(self.out["full_zh"]))

    def test_flow_card_gets_entities_and_texts(self):
        cards = self.out["full_zh"]["views"][0]["sections"][0]["cards"]
        flow = next(c for c in cards if c.get("type") == "custom:energylib-house-flow")
        self.assertEqual(flow["load_entity"], "sensor.dev1_renamed_load_power")
        self.assertEqual(flow["solar_entity"], "sensor.dev1_renamed_solar_power")
        self.assertEqual(flow["battery_entity"], "sensor.dev1_renamed_battery_power")
        self.assertEqual(flow["grid_entity"], "sensor.dev1_renamed_grid_power")
        # Grid/battery presence comes from the running state and the alarm sensor.
        self.assertEqual(flow["running_entity"], "sensor.dev1_renamed_running_state")
        self.assertEqual(flow["alarm_entity"], "sensor.dev1_renamed_alarm_status")
        self.assertEqual(flow["soc_entity"], "sensor.dev1_renamed_battery_total_soc")
        # The house card switches to the circle diagram; both labels come along.
        self.assertEqual(
            (flow["text"]["toDiagram"], flow["text"]["toHouse"]), ("切换为简图", "切换为房屋图")
        )
        self.assertEqual(flow["text"]["gridMissing"], "电网断开")
        self.assertEqual(flow["text"]["batteryMissing"], "电池未接入")
        # A negative home load is drawn as 0 W with a one-line hint naming the reading.
        self.assertIn("{value}", flow["text"]["negativeLoad"])

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
        self.assertIn("custom:energylib-house-flow", text(page))
        self.assertIn("Allow control", text(page))
        self.assertIn("Live energy flow", text(page))
        self.assertNotIn("null", text(page))

    def test_new_remote_operations_exclude_legacy_immediate_controls(self):
        status = self.out["full_zh"]["views"][0]["sections"][2]
        refs = referenced(status)
        self.assertNotIn("number.dev1_renamed_remote_power", refs)
        self.assertNotIn("switch.dev1_renamed_remote_control", refs)
        self.assertIn("number.dev1_renamed_remote_duration", refs)
        self.assertNotIn("button.dev1_renamed_stop_remote", refs)
        self.assertIn("custom:energylib-dispatch", text(status))
        self.assertNotIn("Controls are off", text(self.out["remote_only"]))
        self.assertIn("custom:energylib-dispatch", text(self.out["remote_only"]))

    def test_mode_card_then_the_settings_as_ha_tiles(self):
        # Read-only modes (manual dispatch, tariff, grid support) lock the choice in
        # the card itself (tests/js/run_mode.mjs).
        cards = self.out["full_zh"]["views"][0]["sections"][2]["cards"]
        mode = next(c for c in cards if c["type"] == "custom:energylib-mode")
        self.assertEqual(mode["mode_entity"], "sensor.dev1_renamed_work_mode")
        self.assertEqual(mode["select_entity"], "select.dev1_renamed_home_mode")
        self.assertNotIn("number_entities", mode)
        # The power switch first (it asks before it acts), the mode card, then the
        # settings as HA's own tiles with a slider or a toggle: one design for every
        # device (user decision 2026-11-19).
        power = next(c for c in cards if c.get("entity") == "switch.dev1_renamed_inverter_power")
        self.assertIn("confirmation", power["icon_tap_action"])
        start = cards.index(mode) + 1
        tiles = {c["entity"]: c for c in cards[start:] if c["type"] == "tile"}
        self.assertEqual(
            tiles["number.dev1_renamed_backup_soc"]["features"],
            [{"type": "numeric-input", "style": "slider"}],
        )
        self.assertEqual(tiles["switch.dev1_renamed_grid_charge"]["features"], [{"type": "toggle"}])
        # Settings are half width: side by side on the phone pages as in a section.
        for key in (
            "switch.dev1_renamed_grid_charge",
            "switch.dev1_renamed_export_limit_enable",
            "number.dev1_renamed_backup_soc",
            "number.dev1_renamed_export_limit",
        ):
            self.assertEqual(tiles[key]["grid_options"], {"columns": 6})
        self.assertIn("number.dev1_renamed_export_limit", tiles)
        # No second mode selector beside the card (the EV charge mode is its own setting).
        self.assertEqual(
            [
                c["entity"]
                for c in cards
                if c["type"] == "tile" and c["entity"].startswith("select.")
            ],
            ["select.dev1_renamed_ev_charge_mode"],
        )

    def test_no_device_explains_what_to_do(self):
        self.assertIn("No EnergyLIB device found", text(self.out["empty"]))

    def test_combined_form_does_not_require_native_parameter_or_button_entities(self):
        page = self.out["combined"]
        self.assertIn("custom:energylib-dispatch", text(page))
        self.assertTrue(referenced(page) <= set(self.hass["combined"]["entities"]))
        self.assertNotIn("number.dev1_renamed_remote_duration", referenced(page))

    def test_no_add_devices_entry_even_with_the_gateway(self):
        # The inverter integration also runs on users' own Home Assistant: its page
        # links to nothing outside it, not even the gateway's quick setup.
        for name in ("gateway_admin", "gateway_member", "full_zh"):
            self.assertNotIn("添加设备", text(self.out[name]))
            self.assertNotIn("energylib-assistant", text(self.out[name]))
        cards = self.out["gateway_admin"]["views"][0]["sections"][0]["cards"]
        self.assertEqual([c["type"] for c in cards[:2]], ["custom:energylib-offline", "heading"])
        self.assertEqual(cards[1]["heading"], "实时能流")

    def test_one_overview_per_device(self):
        views = self.out["two"]["views"]
        self.assertEqual([v["path"] for v in views], ["dev1", "dev2"])
        for view in views:
            self.assertTrue(all(f".{view['path']}_" in r for r in referenced(view)))

    def test_each_charger_under_its_own_name(self):
        page = self.out["two_chargers"]
        self.assertIn("sensor.second_ev_charge_power", referenced(page))
        names = text(page)
        self.assertIn("EV charger C594", names)
        self.assertIn("EV charger 0002", names)

    def test_devices_under_an_ems_get_no_page(self):
        views = self.out["accessory"]["views"]
        self.assertEqual([v["path"] for v in views], ["overview"])

    def test_overview_columns_are_balanced(self):
        sections = self.out["full_zh"]["views"][0]["sections"]
        self.assertEqual(len(sections), 4)  # three columns, then the phone pages
        flow, energy, right, phone = sections
        self.assertFalse(
            any(s.get("column_span") for s in sections[:3])
        )  # the columns; the phone pages span

        def headings(sec):
            return [c["heading"] for c in sec["cards"] if c["type"] == "heading"]

        # The web columns are the phone pages side by side (11-23): now (house,
        # today's curve, today, totals, status), statistics, control with the log.
        # A device with an EV charger gets its block on both: state and power with
        # "now", its settings after the inverter's controls.
        self.assertEqual(headings(flow), ["实时能流", "充电桩", "运行状态"])
        self.assertEqual(headings(right), ["运行控制", "充电桩", "运行记录"])
        ev_now = [c["entity"] for c in flow["cards"] if c["type"] == "tile"]
        self.assertEqual(
            ev_now, ["sensor.dev1_renamed_ev_charger_status", "sensor.dev1_renamed_ev_charge_power"]
        )
        ev_controls = [c["entity"] for c in right["cards"] if "_ev_" in c.get("entity", "")]
        self.assertEqual(ev_controls[0], "switch.dev1_renamed_ev_charger_on")
        self.assertEqual(len(ev_controls), 7)
        self.assertIn("custom:energylib-tou", [c["type"] for c in right["cards"]])
        flow_types = [c["type"] for c in flow["cards"]]
        self.assertEqual(
            flow_types[:5],
            [
                "custom:energylib-offline",
                "heading",
                "custom:energylib-house-flow",
                "custom:energylib-day-curve",
                "custom:energylib-sankey",
            ],
        )
        self.assertIn("custom:energylib-alarms", flow_types)
        # The live power split with "now", the running totals with statistics (11-23).
        self.assertIn("custom:energylib-sankey", flow_types)
        # The house shows the live readings, running state and battery.
        house = flow["cards"][2]
        self.assertEqual(house["running_entity"], "sensor.dev1_renamed_running_state")
        self.assertNotIn("frameless", house)  # framed on the web
        # Offline banner on top (it watches the four powers and the running state).
        self.assertEqual(len(flow["cards"][0]["entities"]), 5)
        tiles = [c["entity"] for c in energy["cards"] if c["type"] == "tile"]
        self.assertTrue(tiles and all(e.endswith("_energy") for e in tiles), tiles)  # the totals
        # No today card beside today's curve (11-23): its mode chip is on the house, its
        # self-use with the curve's PV figures.
        self.assertNotIn("custom:energylib-today", flow_types)
        self.assertEqual(house["mode_entity"], "sensor.dev1_renamed_work_mode")
        self.assertEqual(house["mode_select_entity"], "select.dev1_renamed_home_mode")
        curve = flow["cards"][3]
        solar = next(tab for tab in curve["tabs"] if tab["key"] == "solar")
        self.assertEqual(solar["exportEnergy"], "sensor.dev1_renamed_grid_export_energy")
        self.assertEqual(curve["text"]["selfUse"], "自用 {value}%")
        # Statistics: the power split, then 24 h of power; no Energy page here, so
        # no kWh history charts.
        energy_types = [c["type"] for c in energy["cards"]]
        self.assertEqual(
            energy_types[:3], ["heading", "heading", "tile"]
        )  # statistics, then the totals
        self.assertEqual(energy["cards"][1]["heading"], "累计电量")
        self.assertIn("24 小时功率趋势", [c.get("title") for c in energy["cards"]])
        self.assertNotIn("custom:energylib-sankey", energy_types)
        self.assertNotIn("statistics-graph", energy_types)
        # Control: power on/off first, then the mode card, manual dispatch, the log last.
        right_types = [c["type"] for c in right["cards"]]
        self.assertEqual(right_types[1:3], ["tile", "custom:energylib-mode"])
        self.assertEqual(right["cards"][1]["entity"], "switch.dev1_renamed_inverter_power")
        self.assertEqual(right_types[-3:], ["custom:energylib-dispatch", "heading", "logbook"])
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

    def test_energy_page_cards_when_it_covers_this_inverter(self):
        energy = self.out["energy_zh"]["views"][0]["sections"][1]
        types = [c["type"] for c in energy["cards"]]
        # The power split and 24 h of power, then the Energy page's cards.
        self.assertEqual(
            types[1:2] + types[8:],
            [
                "heading",
                "history-graph",
                "markdown",
                "heading",
                "energy-date-selection",
                "energy-usage-graph",
                "energy-self-sufficiency-gauge",
                "energy-solar-consumed-gauge",
                "energy-sankey",
            ],
        )
        self.assertEqual(energy["cards"][10]["heading"], "能源统计")
        self.assertIn(
            "正为放电", energy["cards"][9]["content"]
        )  # the note on the signs under the 24 h chart
        self.assertNotIn("custom:energylib-links", types)  # the cards are here: no link to the page
        self.assertNotIn("statistics-graph", types)  # HA's cards instead of ours
        # Other meters on the Energy page, or its settings unreadable: no history charts.
        for name in ("energy_other", "energy_error"):
            with self.subTest(name=name):
                types = [c["type"] for c in self.out[name]["views"][0]["sections"][1]["cards"]]
                self.assertNotIn("statistics-graph", types)
                self.assertFalse([t for t in types if t.startswith("energy-")])

    def test_read_only_source_with_counters_uses_two_columns(self):
        live, stats, _phone = self.out["aecc_counters"]["views"][0]["sections"]
        live_types = [c["type"] for c in live["cards"]]
        self.assertIn("custom:energylib-house-flow", live_types)
        self.assertIn("custom:energylib-day-curve", live_types)
        self.assertNotIn("custom:energylib-today", live_types)
        # No controls: no control column; the log goes with "now", as on the phone.
        self.assertEqual(live_types[-1], "logbook")
        stat_types = [c["type"] for c in stats["cards"]]
        self.assertEqual(stat_types[:3], ["heading", "heading", "tile"])  # the totals first
        self.assertNotIn("statistics-graph", stat_types)  # no Energy page: no kWh charts
        page = text(self.out["aecc_counters"]["views"][0])
        self.assertNotIn("custom:energylib-tou", page)
        self.assertNotIn("custom:energylib-mode", page)
        # Phone: the same two pages.
        tabs = _phone["cards"][-1]["tabs"]
        self.assertEqual(len(tabs), 2)
        self.assertEqual(tabs[0]["cards"][-1]["type"], "logbook")
        self.assertEqual(tabs[1]["cards"], stats["cards"][1:])

    def test_a_disabled_mode_sensor_keeps_the_selector(self):
        # An EnergyLIB inverter whose mode sensor was disabled is not "pending":
        # its EMS selector stays as a tile.
        page = text(self.out["no_mode_sensor"]["views"][0]["sections"][2])
        self.assertIn("select.dev1_renamed_home_mode", page)
        self.assertNotIn('"pending"', page)

    def test_controls_without_a_mode_get_the_control_column(self):
        view = self.out["aecc_controls"]["views"][0]
        live, energy, control, phone = view["sections"]
        page = text(control)
        self.assertIn("custom:energylib-tou", page)
        self.assertIn("switch.dev1_renamed_inverter_power", page)
        self.assertIn("number.dev1_renamed_min_soc", page)
        # Same order as a device with a mode: power switch, the mode card (kept
        # in its place, pending), then the settings as tiles.
        types = [c["type"] for c in control["cards"]]
        mode = control["cards"][2]
        self.assertEqual(types[:3], ["heading", "tile", "custom:energylib-mode"])
        self.assertEqual(control["cards"][1]["entity"], "switch.dev1_renamed_inverter_power")
        self.assertEqual(control["cards"][1]["grid_options"], {"columns": 12})
        self.assertTrue(mode["pending"])
        self.assertNotIn("select_entity", mode)
        self.assertEqual(types[3], "tile")
        self.assertNotIn("custom:energylib-dispatch", text(view))
        self.assertNotIn("集成选项", page)  # no "controls are off" note while they are on
        self.assertEqual(len(phone["cards"][-1]["tabs"]), 3)

    def test_read_only_source_shows_no_empty_or_misleading_cards(self):
        view = self.out["aecc"]["views"][0]
        live, stats, phone = view["sections"]
        for section in (live, stats):
            cards = section["cards"]
            for i, card in enumerate(cards):
                if card["type"] == "heading":
                    # A heading always has a card under it.
                    self.assertTrue(i + 1 < len(cards) and cards[i + 1]["type"] != "heading", card)
                if card["type"] in ("statistics-graph", "history-graph", "logbook"):
                    self.assertTrue(
                        card["entities"], card
                    )  # an empty list is HA's "configuration error"
        flow = next(c for c in live["cards"] if c["type"] == "custom:energylib-house-flow")
        self.assertEqual(flow["solar_entity"], "sensor.dev1_renamed_solar_power")
        # No energy counters and no controls: now and statistics, as on the phone.
        self.assertEqual(
            [c["type"] for c in live["cards"]][:4],
            [
                "custom:energylib-offline",
                "heading",
                "custom:energylib-house-flow",
                "custom:energylib-day-curve",
            ],
        )
        self.assertIn("运行状态", [c.get("heading") for c in live["cards"]])
        self.assertEqual(
            [c["type"] for c in stats["cards"]], ["heading", "history-graph", "markdown"]
        )
        self.assertNotIn(
            "markdown", [c["type"] for c in live["cards"]]
        )  # the signs note went with the chart
        self.assertIn("custom:energylib-sankey", [c["type"] for c in live["cards"]])
        self.assertNotIn("custom:energylib-today", text(view))
        self.assertNotIn("statistics-graph", text(view))
        page = text(view)
        self.assertNotIn("custom:energylib-tou", page)
        self.assertNotIn("/config/integrations/integration/energylib", page)
        self.assertNotIn("logbook", page)
        self.assertNotIn("控制未开启", page)
        # The phone shows only pages with something on them: live (with today's
        # curve), and the Sankey with the 24 h chart.
        tabs = phone["cards"][1]["tabs"]
        self.assertEqual([t["title"] for t in tabs], ["实时", "统计"])
        self.assertIn("custom:energylib-day-curve", [c["type"] for c in tabs[0]["cards"]])
        self.assertEqual(tabs[1]["cards"], stats["cards"][1:])

    def test_admin_without_energy_page_gets_a_pointer_to_set_it_up(self):
        energy = self.out["gateway_admin"]["views"][0]["sections"][1]
        links = [c for c in energy["cards"] if c["type"] == "custom:energylib-links"]
        self.assertEqual([c["links"][0]["path"] for c in links], ["/config/energy"])
        self.assertEqual(
            links[0]["links"][0]["name"], "在能源面板中添加本逆变器，可按日 / 周 / 月 / 年查看统计"
        )
        member = self.out["gateway_member"]["views"][0]["sections"][1]
        self.assertFalse([c for c in member["cards"] if c["type"] == "custom:energylib-links"])

    def test_phone_gets_the_columns_as_pages(self):
        flow, energy, right, phone = self.out["full_zh"]["views"][0]["sections"]
        # Phones and touch tablets get pages; a browser with a mouse keeps the columns.
        wide = [
            {
                "condition": "screen",
                "media_query": "(min-width: 1366px), (min-width: 768px) and (pointer: fine)",
            }
        ]
        narrow = [
            {
                "condition": "screen",
                "media_query": "(max-width: 767px), (max-width: 1365px) and (pointer: coarse)",
            }
        ]
        self.assertTrue(all(s["visibility"] == wide for s in (flow, energy, right)))
        self.assertEqual(phone["visibility"], narrow)
        self.assertEqual(phone["column_span"], 3)  # the whole width on a tablet
        # The offline banner above the switch bar; the pages are the columns' cards.
        banner, tabs = phone["cards"]
        self.assertEqual(banner, flow["cards"][0])
        self.assertEqual(tabs["type"], "custom:energylib-tabs")
        self.assertEqual([t["title"] for t in tabs["tabs"]], ["实时", "统计", "控制"])
        now, stats, control = (tab["cards"] for tab in tabs["tabs"])
        # Home screen (11-23): no heading above the house, then today's energy and the
        # totals, then the charts and the status; the Sankey is on the statistics page.
        now_types = [c["type"] for c in now]
        self.assertEqual(
            now_types[:3],
            [
                "custom:energylib-house-flow",
                "custom:energylib-day-curve",
                "custom:energylib-sankey",
            ],
        )
        self.assertIn("custom:energylib-sankey", now_types)  # the live split stays with "now"
        self.assertTrue(now[0]["frameless"])  # the phone house has no card frame
        self.assertNotIn("frameless", flow["cards"][2])
        self.assertNotIn("custom:energylib-today", now_types)
        self.assertIn("tile", [c["type"] for c in stats])  # running totals on the statistics page
        # Today's curve: one reading at a time; the grid shows in, out and net.
        curve = now[1]
        self.assertEqual(
            [tab["key"] for tab in curve["tabs"]], ["grid", "solar", "load", "battery"]
        )
        grid = curve["tabs"][0]
        self.assertTrue(grid["net"])
        self.assertEqual([e["label"] for e in grid["energy"]], ["买电", "卖电"])
        self.assertEqual(grid["icon"], "mdi:transmission-tower")
        self.assertEqual(curve["text"]["dayToday"], "今天")
        self.assertEqual(curve["tabs"][2]["label"], "家庭")
        self.assertEqual(curve["text"]["dayNet"], "净值")
        # The four-line 24 h chart moved to the statistics page.
        self.assertEqual(stats[0]["heading"], "累计电量")
        self.assertNotIn("custom:energylib-sankey", [c["type"] for c in stats])
        self.assertIn("24 小时功率趋势", [c.get("title") for c in stats])
        self.assertNotIn("24 小时功率趋势", [c.get("title") for c in now])
        self.assertNotIn("custom:energylib-today", text(stats))
        self.assertEqual(control, right["cards"])
        # The web columns hold the same cards (with a heading above the house).
        self.assertEqual(stats, energy["cards"][1:])
        self.assertEqual(flow["cards"][1]["type"], "heading")

    def test_no_battery_hides_the_soc_chart(self):
        sections = self.out["no_battery"]["views"][0]["sections"]
        titles = [c.get("title") for sec in sections for c in sec["cards"]]
        self.assertNotIn("电池电量 · 24 小时", titles)
        self.assertIn("24 小时功率趋势", titles)

    def test_single_device_has_only_the_overview(self):
        self.assertEqual(
            [(v["path"], v["title"]) for v in self.out["full_zh"]["views"]],
            [("overview", "EnergyLIB")],
        )
        # Text tabs: a view icon would replace the title in the tab bar.
        self.assertFalse([v for v in self.out["full_zh"]["views"] if "icon" in v])


class StrategyServingTests(unittest.TestCase):
    def test_served_file_exists_and_is_self_contained(self):
        source = STRATEGY.read_text(encoding="utf-8")
        self.assertIn('STRATEGY_TAG = "ll-strategy-dashboard-energylib"', source)
        # Defined only after the HA app shell, so the frontend's registry sees it.
        self.assertIn('customElements.whenDefined("home-assistant")', source)
        # Entities are found by translation key, never by a fixed entity ID.
        self.assertIsNone(re.search(r"[a-z_]+\.energylib_\w+", source))
        # The only import is the versioned translation files next to the strategy.
        self.assertEqual(source.count("import("), 1)
        self.assertIn("./translations/${lang}.json?v=${encodeURIComponent(VERSION)}", source)
        self.assertEqual(
            sorted(p.name for p in STRATEGY.parent.iterdir()),
            [STRATEGY.name, "house", "translations"],
        )
        # The house flow card's pictures, versioned like the translations.
        self.assertEqual(
            sorted(p.name for p in (STRATEGY.parent / "house").iterdir()),
            ["house-dark.webp", "house-light.webp"],
        )
        self.assertIn("./house/house-dark.webp?v=${encodeURIComponent(VERSION)}", source)
        self.assertIn(
            'FRONTEND_DIR / "house"',
            dashboard := (COMPONENT / "dashboard.py").read_text(encoding="utf-8"),
        )
        dashboard = (COMPONENT / "dashboard.py").read_text(encoding="utf-8")
        self.assertIn(f'STRATEGY_FILE = "{STRATEGY.name}"', dashboard)

    def test_frontend_translations_share_keys_and_match_the_language_list(self):
        folder = STRATEGY.parent / "translations"
        files = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in folder.glob("*.json")}
        source = STRATEGY.read_text(encoding="utf-8")
        listed = re.search(r"const LANGUAGES = \[(.*?)\];", source).group(1)
        self.assertEqual(sorted(re.findall(r'"([^"]+)"', listed)), sorted(files))

        def keys(data, prefix=""):
            out = set()
            for key, value in data.items():
                out |= keys(value, f"{prefix}{key}.") if isinstance(value, dict) else {prefix + key}
            return out

        english = keys(files["en"])
        for lang, data in files.items():
            with self.subTest(lang=lang):
                self.assertEqual(keys(data), english)
                # Placeholders survive translation.
                self.assertIn("{value}", data["dashboard"]["negativeLoad"])

    def test_content_hash_covers_the_translation_files(self):
        dashboard = (COMPONENT / "dashboard.py").read_text(encoding="utf-8")
        self.assertIn('FRONTEND_DIR / "translations"', dashboard)

    def test_frontend_is_an_optional_dependency(self):
        manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
        loaded_first = set(manifest.get("after_dependencies", [])) | set(manifest["dependencies"])
        self.assertLessEqual({"frontend", "http"}, loaded_first)
