"""Real HA smoke/integration checks in a temporary configuration, never /config.

Run inside a HA image: python -m tools.ha_runtime_check
This script starts a loopback Sinexcel simulator and no HTTP server or physical devices.
"""

import asyncio
import json
import shutil
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .sinexcel_simulator import BenchConfig, BenchModel, BenchServer


class StaticPaths:
    """Records static routes like HA's HTTP server would, without binding a port."""

    def __init__(self):
        self.paths = []

    async def async_register_static_paths(self, configs):
        for config in configs:
            if any(p.url_path == config.url_path for p in self.paths):
                raise RuntimeError(f"Duplicate static route {config.url_path}")
            self.paths.append(config)


async def check():
    from homeassistant import loader
    from homeassistant.bootstrap import async_load_base_functionality
    from homeassistant.config_entries import ConfigEntries, ConfigEntry, ConfigEntryState
    from homeassistant.core import HomeAssistant
    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from homeassistant.setup import async_setup_component

    root = Path(__file__).resolve().parents[1]
    tmp = tempfile.TemporaryDirectory(prefix="energylib-ha-check-")
    config = Path(tmp.name)
    shutil.copytree(
        root / "custom_components" / "energylib", config / "custom_components" / "energylib"
    )
    # Two independent simulators: one for the v1 migration entry, one (with a
    # different serial) for the control entry, so they never share a device.
    model = BenchModel(BenchConfig(writes=True))
    model.tick(datetime.now(timezone.utc), 0)
    peer = BenchServer(model, {"127.0.0.1"})
    server = await asyncio.start_server(peer.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    lab_model = BenchModel(BenchConfig(serial="ELIB-RUNTIME-CTRL", writes=True))
    lab_model.tick(datetime.now(timezone.utc), 0)
    lab_peer = BenchServer(lab_model, {"127.0.0.1"})
    lab_server = await asyncio.start_server(lab_peer.handle, "127.0.0.1", 0)
    lab_port = lab_server.sockets[0].getsockname()[1]
    hass = HomeAssistant(str(config))
    loader.async_setup(hass)
    hass.config_entries = ConfigEntries(hass, {})
    assert await async_load_base_functionality(hass)
    # Discovery contracts have separate tests. Do not bind a second HTTP/mDNS
    # service on the running host merely to exercise HA registries/entities.
    hass.config.components.add("zeroconf")
    # The twin panel and dashboard need http/frontend; record their routes and
    # extra modules instead of serving them.
    from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL, UrlManager

    hass.http = StaticPaths()
    hass.data[DATA_EXTRA_MODULE_URL] = UrlManager(lambda *args: None, [])
    hass.config.components.update({"http", "frontend"})
    results = []

    def entry(*, version=2, lab=True, enabled=False, serial=None, peer_port=None):
        serial = serial or model.config.serial
        data = {
            "host": "127.0.0.1",
            "port": peer_port or port,
            "unit_id": 1,
            "profile": "v001b031_single",
        }
        uid = serial
        if version == 2:
            # One Sinexcel profile for real and simulated devices; "lab" only
            # names the writable simulator entry within this check.
            uid = "energylib_" + serial
            data.update(profile_id="sinexcel_v2", profile_version=1, serial=serial)
        return ConfigEntry(
            domain="energylib",
            data=data,
            version=version,
            minor_version=1,
            options={"allow_control": enabled},
            source="user",
            title="EnergyLIB",
            unique_id=uid,
            discovery_keys={},
            subentries_data=[],
        )

    try:
        await async_setup_component(hass, "homeassistant", {})
        registry, devices = er.async_get(hass), dr.async_get(hass)
        legacy = entry(version=1, lab=False)
        # Register v1 records before setup so actual HA performs migration.
        hass.config_entries._entries[legacy.entry_id] = legacy
        device = devices.async_get_or_create(
            config_entry_id=legacy.entry_id,
            identifiers={("energylib", model.config.serial)},
            name="EnergyLIB",
        )
        old = registry.async_get_or_create(
            "sensor",
            "energylib",
            model.config.serial + "_solar_energy",
            config_entry=legacy,
            device_id=device.id,
            suggested_object_id="legacy_solar_energy",
        )
        before_id, before_device = old.entity_id, device.id
        assert await hass.config_entries.async_setup(legacy.entry_id)
        await hass.async_block_till_done()
        assert legacy.version == 2 and legacy.unique_id.startswith("energylib_")
        migrated = registry.async_get(before_id)
        assert migrated.device_id == before_device
        assert migrated.unique_id == f"energylib_{model.config.serial}_solar_energy"
        assert hass.states.get(before_id).attributes["state_class"] == "total_increasing"
        assert hass.states.get(before_id).state not in ("unknown", "unavailable")
        results.append("v1 migration preserves entity_id, device_id and energy metadata")
        from homeassistant.components.frontend import DATA_PANELS

        from custom_components.energylib.dashboard import async_register_dashboard
        from custom_components.energylib.twin_panel import (
            PANEL_URL_PATH,
            async_register_twin_panel,
        )

        panel = hass.data[DATA_PANELS][PANEL_URL_PATH]
        assert panel.component_name == "iframe" and not panel.require_admin
        assert panel.config["url"].startswith("/energylib_twin/index.html?v=")
        await async_register_twin_panel(hass)
        await async_register_dashboard(hass)
        routes = sorted(p.url_path for p in hass.http.paths)
        assert routes == ["/energylib_static", "/energylib_twin"], routes
        for route in hass.http.paths:
            index = (
                "index.html" if route.url_path == "/energylib_twin" else "energylib-dashboard.js"
            )
            assert (Path(route.path) / index).is_file()
        modules = hass.data[DATA_EXTRA_MODULE_URL].urls
        assert [u.split("?")[0] for u in modules] == ["/energylib_static/energylib-dashboard.js"]
        results.append("twin panel and dashboard strategy register once, visible to all users")
        assert await hass.config_entries.async_unload(legacy.entry_id)

        lab = entry(enabled=False, serial=lab_model.config.serial, peer_port=lab_port)
        await hass.config_entries.async_add(lab)
        await hass.async_block_till_done()
        assert lab.state is ConfigEntryState.LOADED
        entities = er.async_entries_for_config_entry(registry, lab.entry_id)
        enabled = [e for e in entities if e.disabled_by is None]
        assert len(enabled) == 14, [(e.entity_id, e.disabled_by) for e in entities]
        assert not any(e.domain in ("number", "switch", "select") for e in entities)
        lab_device = devices.async_get_device_by_identifier(
            ("energylib", lab.unique_id), lab.entry_id
        )
        try:
            await hass.services.async_call(
                "energylib",
                "set_battery_power",
                {"device_id": lab_device.id, "power_kw": 1, "duration": 10},
                blocking=True,
            )
        except HomeAssistantError:
            pass
        else:
            raise AssertionError("Service bypassed disabled controls")
        results.append("14 defaults, optional entities disabled, services cannot bypass opt-in")
        flow = await hass.config_entries.options.async_init(lab.entry_id)
        assert flow["type"] == "menu"
        flow = await hass.config_entries.options.async_configure(
            flow["flow_id"], {"next_step_id": "control"}
        )
        assert flow["type"] == "form"
        await hass.config_entries.options.async_configure(flow["flow_id"], {"allow_control": True})
        await hass.async_block_till_done()
        assert lab.state is ConfigEntryState.LOADED
        entities = er.async_entries_for_config_entry(registry, lab.entry_id)
        controls = [e for e in entities if e.domain in ("number", "switch", "select")]
        assert len(controls) == 7 and all(e.disabled_by is not None for e in controls)
        results.append("native options enable controls; all seven start disabled")

        backup_control = next(e for e in controls if e.unique_id.endswith("_backup_soc"))
        registry.async_update_entity(backup_control.entity_id, disabled_by=None)
        assert await hass.config_entries.async_reload(lab.entry_id)
        await hass.async_block_till_done()
        for value in (31, 32):
            await hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": backup_control.entity_id, "value": value},
                blocking=True,
            )
            assert float(hass.states.get(backup_control.entity_id).state) == value
        results.append("consecutive native control actions publish readback before returning")

        await hass.services.async_call(
            "energylib",
            "set_tou_schedule",
            {
                "device_id": lab_device.id,
                "periods": [{"type": "charge", "start": "01:00", "end": "06:00", "power": 1.5}],
                "activate": True,
            },
            blocking=True,
        )
        assert lab_model.get_float(0x300C) == 1
        from .sinexcel_simulator import _sx

        previous_dates = [lab_model.registers[0xC0B6 + i] for i in range(4)]
        future_dates = _sx.u32_words(4039372800) + _sx.u32_words(4102358400)
        lab_model.registers.update({0xC0B6 + i: w for i, w in enumerate(future_dates)})
        await hass.services.async_call(
            "energylib",
            "set_tou_schedule",
            {
                "device_id": lab_device.id,
                "periods": [{"type": "charge", "start": "01:00", "end": "06:00", "power": 1}],
                "outside_mode": "backup",
            },
            blocking=True,
        )
        assert [lab_model.registers[0xC0B6 + i] for i in range(4)] == future_dates
        assert (
            lab.runtime_data.device.schedule_status(datetime(2026, 10, 20, 2)) == "outside_period"
        )
        results.append("native TOU save preserves future validity and reports the plan inactive")
        await hass.services.async_call(
            "energylib", "clear_tou_schedule", {"device_id": lab_device.id}, blocking=True
        )
        assert not lab_model.schedule().periods and lab_model.get_float(0x300C) == 1
        assert [lab_model.registers[0xC0B6 + i] for i in range(4)] == future_dates
        lab_model.registers.update({0xC0B6 + i: w for i, w in enumerate(previous_dates)})
        results.append("TOU action roundtrip, clear preserves work mode")
        await lab.runtime_data.async_command("work_mode", "self_consumption")
        lab_model.config = replace(lab_model.config, rated_kw=3)
        lab_model.tick(datetime.now(timezone.utc), 0)
        await lab.runtime_data.device.client.close()
        writes = lab_peer.write_count
        try:
            await hass.services.async_call(
                "energylib",
                "set_battery_power",
                {"device_id": lab_device.id, "power_kw": 4, "duration": 10},
                blocking=True,
            )
        except HomeAssistantError:
            pass
        else:
            raise AssertionError("Reconnected device accepted stale power limits")
        assert lab_peer.write_count == writes
        assert lab.runtime_data.device.profile.max_power_kw == 3
        lab_model.config = replace(lab_model.config, rated_kw=5)
        lab_model.tick(datetime.now(timezone.utc), 0)
        await lab.runtime_data.device.client.close()
        results.append(
            "native service re-probes rating after reconnect and rejects an oversized write"
        )
        await hass.services.async_call(
            "energylib",
            "set_battery_power",
            {"device_id": lab_device.id, "power_kw": -1, "duration": 30},
            blocking=True,
        )
        assert lab_model.get_float(0xC646) == 1
        old_runtime = lab.runtime_data.device
        assert await hass.config_entries.async_unload(lab.entry_id)
        assert lab_model.get_float(0xC646) == 0
        from custom_components.energylib.energylib_core.runtime import ControlError

        requests = old_runtime.client.requests
        try:
            await old_runtime.set_battery_power(1, 10)
        except ControlError:
            pass
        else:
            raise AssertionError("Unloaded runtime reconnected and started a lease")
        assert old_runtime.client.requests == requests
        results.append("unloaded runtime rejects retained command references without reconnecting")
        assert await hass.config_entries.async_setup(lab.entry_id)
        assert lab.runtime_data.device.remote_deadline is None
        results.append("remote action, unload exit, reload never resumes lease")

        flow = await hass.config_entries.options.async_init(lab.entry_id)
        flow = await hass.config_entries.options.async_configure(
            flow["flow_id"], {"next_step_id": "tou"}
        )
        flow = await hass.config_entries.options.async_configure(
            flow["flow_id"], {"next_step_id": "add"}
        )
        count = lab_peer.write_count
        flow = await hass.config_entries.options.async_configure(
            flow["flow_id"], {"type": "charge", "start": "02:00:00", "end": "03:00:00", "power": 2}
        )
        assert flow["type"] == "menu" and count == lab_peer.write_count
        flow = await hass.config_entries.options.async_configure(
            flow["flow_id"], {"next_step_id": "save"}
        )
        assert "02:00" in flow["description_placeholders"]["preview"]
        await hass.config_entries.options.async_configure(flow["flow_id"], {"activate": False})
        assert len(lab_model.schedule().periods) == 1
        results.append("native TOU forms edit draft without writes until explicit Save")

        await lab.runtime_data.async_command("backup_soc", 30)
        from custom_components.energylib.diagnostics import async_get_config_entry_diagnostics

        diagnostic = await async_get_config_entry_diagnostics(hass, lab)
        encoded = json.dumps(diagnostic)
        assert lab_model.config.serial not in encoded and "127.0.0.1" not in encoded
        assert "registers" not in encoded
        results.append("diagnostics omit serial, network address and raw registers")
        assert await hass.config_entries.async_unload(lab.entry_id)
        for control in controls:
            if control.domain == "number":
                registry.async_update_entity(control.entity_id, disabled_by=None)
                break
        hass.config_entries.async_update_entry(lab, options={"allow_control": False})
        await hass.async_block_till_done()
        if lab.state is not ConfigEntryState.LOADED:
            assert await hass.config_entries.async_setup(lab.entry_id)
        assert all(
            s.state == "unavailable"
            for s in hass.states.async_all()
            if s.domain in ("number", "select", "switch")
        ), [(s.entity_id, s.state) for s in hass.states.async_all() if s.domain == "number"]
        try:
            await hass.services.async_call(
                "energylib",
                "set_battery_power",
                {"device_id": lab_device.id, "power_kw": 1, "duration": 10},
                blocking=True,
            )
        except HomeAssistantError:
            pass
        else:
            raise AssertionError("Disabling controls left the action callable")
        results.append("disabling control leaves no available controls and rejects service actions")
        from custom_components.energylib.migration import async_migrate_entry

        # Collision preflight must leave all registries and entry data untouched.
        collision = entry(version=1, serial="LAB-CONFLICT")
        duplicate = entry(lab=False, serial="LAB-CONFLICT")
        hass.config_entries._entries[collision.entry_id] = collision
        hass.config_entries._entries[duplicate.entry_id] = duplicate
        assert not await async_migrate_entry(hass, collision)
        assert collision.version == 1 and collision.unique_id == "LAB-CONFLICT"
        entity_collision = entry(version=1, serial="LAB-ENTITY-CONFLICT")
        hass.config_entries._entries[entity_collision.entry_id] = entity_collision
        a = registry.async_get_or_create(
            "sensor", "energylib", "LAB-ENTITY-CONFLICT_solar_energy", config_entry=entity_collision
        )
        registry.async_get_or_create(
            "sensor",
            "energylib",
            "energylib_LAB-ENTITY-CONFLICT_solar_energy",
            config_entry=duplicate,
        )
        assert not await async_migrate_entry(hass, entity_collision)
        assert registry.async_get(a.entity_id).unique_id == "LAB-ENTITY-CONFLICT_solar_energy"
        device_collision = entry(version=1, serial="LAB-DEVICE-CONFLICT")
        hass.config_entries._entries[device_collision.entry_id] = device_collision
        original = devices.async_get_or_create(
            config_entry_id=device_collision.entry_id,
            identifiers={("energylib", "LAB-DEVICE-CONFLICT")},
        )
        devices.async_get_or_create(
            config_entry_id=duplicate.entry_id,
            identifiers={("energylib", "energylib_LAB-DEVICE-CONFLICT")},
        )
        assert not await async_migrate_entry(hass, device_collision)
        assert devices.async_get(original.id).identifiers == {("energylib", "LAB-DEVICE-CONFLICT")}
        results.append("config, entity and device collisions abort before any migration mutation")
        print(json.dumps({"result": "passed", "checks": results}, ensure_ascii=False))
    finally:
        for e in list(hass.config_entries.async_entries("energylib")):
            if e.state is ConfigEntryState.LOADED:
                await hass.config_entries.async_unload(e.entry_id)
        await hass.async_stop(force=True)
        for srv, simulator in ((server, peer), (lab_server, lab_peer)):
            srv.close()
            await srv.wait_closed()
            await simulator.close()
        # HA delayed stores must finish before TemporaryDirectory is removed.
        tmp.cleanup()


if __name__ == "__main__":
    asyncio.run(check())
