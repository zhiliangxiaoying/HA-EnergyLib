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
from unittest.mock import patch

from .sinexcel_simulator import CONTROL_ADDRESSES, BenchConfig, BenchModel, BenchServer


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
    from homeassistant import config_entries, loader
    from homeassistant.bootstrap import async_load_base_functionality
    from homeassistant.config_entries import ConfigEntries, ConfigEntry, ConfigEntryState
    from homeassistant.core import HomeAssistant
    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from homeassistant.setup import async_setup_component

    default_reload_delay = config_entries.RELOAD_AFTER_UPDATE_DELAY
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
    # The twin files and dashboard need http/frontend; record their routes and
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
        from custom_components.energylib.entity import EnergyLibEntity

        original_language = hass.config.language
        try:
            version = legacy.runtime_data.device.identity.arm_firmware_display
            for language, label in (("zh-Hans", "逆变器"), ("en", "Inverter")):
                hass.config.language = language
                firmware_entity = EnergyLibEntity(legacy.runtime_data, "solar_energy")
                assert firmware_entity.device_info["sw_version"] == f"{label} {version}"
                assert firmware_entity.device_info["identifiers"] == {
                    ("energylib", legacy.unique_id)
                }
        finally:
            hass.config.language = original_language
        results.append(
            "firmware display distinguishes inverter in Chinese/English without changing identity"
        )
        from homeassistant.components.frontend import DATA_PANELS

        from custom_components.energylib.dashboard import async_register_dashboard
        from custom_components.energylib.twin_panel import async_register_twin_files

        # The twin is the dashboard's first tab; no sidebar panel of its own.
        assert not [p for p in hass.data.get(DATA_PANELS, {}) if "energylib" in p]
        await async_register_twin_files(hass)
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
        results.append("twin files and dashboard strategy register once, no extra sidebar panel")
        assert await hass.config_entries.async_unload(legacy.entry_id)

        lab = entry(enabled=False, serial=lab_model.config.serial, peer_port=lab_port)
        await hass.config_entries.async_add(lab)
        await hass.async_block_till_done()
        assert lab.state is ConfigEntryState.LOADED
        entities = er.async_entries_for_config_entry(registry, lab.entry_id)
        enabled = [e for e in entities if e.disabled_by is None]
        assert len(enabled) == 14, [(e.entity_id, e.disabled_by) for e in entities]
        assert not any(e.domain in ("number", "switch", "select") for e in entities)
        read_mode = next(e for e in entities if e.translation_key == "work_mode")
        assert hass.states.get(read_mode.entity_id).attributes["control_access_enabled"] is False
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
        results.append(
            "13 main measurements + one alert status, optional entities disabled, services cannot bypass opt-in"
        )
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
        # 6 basic + remote access/drafts + 3 advanced battery limits (default disabled).
        assert len(controls) == 12
        basic = {
            "work_mode",
            "backup_soc",
            "min_soc",
            "export_limit",
            "grid_charge",
            "export_limit_enable",
        }
        assert {e.translation_key for e in controls if e.disabled_by is None} == basic
        advanced = {"max_soc", "max_charge_current", "max_discharge_current"}
        assert {e.translation_key for e in controls} >= advanced
        assert all(e.disabled_by is not None for e in controls if e.translation_key in advanced)
        assert hass.states.get(read_mode.entity_id).attributes["control_access_enabled"] is True
        assert not lab.runtime_data.remote_enabled
        assert lab_peer.write_count == 0, "Enabling basic controls must not write the inverter"
        results.append(
            "native options enable six basic controls without device writes; advanced battery limits, remote permission and operation helpers remain disabled"
        )

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

        coordinator = lab.runtime_data
        await coordinator._command_lock.acquire()
        try:
            waiting = asyncio.create_task(coordinator.async_command("grid_charge", True))
            await asyncio.sleep(0)
            assert coordinator.device._pending_controls == 1
            before = coordinator.device.client.requests
            await coordinator.device.refresh({"solar_power"}, force=True)
            assert coordinator.device.client.requests == before
            from homeassistant.helpers.update_coordinator import UpdateFailed

            coordinator.last_update_success = False
            try:
                await coordinator._async_update_data()
            except UpdateFailed:
                pass
            else:
                raise AssertionError("Deferred cached data reported communication recovery")
            finally:
                coordinator.last_update_success = True
        finally:
            coordinator._command_lock.release()
        await waiting
        assert coordinator.device._pending_controls == 0
        assert coordinator.device.controls["grid_charge"] == 1
        assert coordinator.last_command_timing["total_ms"] >= 0
        results.append("commands queued at the HA lock take priority over every background read")

        before_writes = lab_peer.write_count
        before_frames = lab.runtime_data.device.client.requests
        try:
            await lab.runtime_data.async_command("min_soc", 101)
        except HomeAssistantError:
            pass
        else:
            raise AssertionError("SOC range was not enforced")
        assert lab_peer.write_count == before_writes
        assert lab.runtime_data.device.client.requests == before_frames
        assert lab.runtime_data.device.last_command == "rejected"
        assert lab.runtime_data.last_command_timing["command"] == "min_soc"
        results.append(
            "pre-write rejection causes no writes or full recovery poll before next command"
        )

        for key, value, address in (("backup_soc", 35, 0x314A), ("min_soc", 12, 0x3046)):
            soc_control = next(e for e in controls if e.translation_key == key)
            lab_peer.read_log.clear()
            before_frames = lab.runtime_data.device.client.requests
            before_writes = lab_peer.write_count
            await hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": soc_control.entity_id, "value": value},
                blocking=True,
            )
            assert lab.runtime_data.device.client.requests - before_frames == 2
            assert lab_peer.write_count - before_writes == 1
            assert list(lab_peer.read_log) == [(3, 1, address, 2)]
            assert hass.states.get(soc_control.entity_id).state == str(float(value))
        results.append("SOC number services write then read back once, with no dependency pre-read")

        export_control = next(e for e in controls if e.translation_key == "export_limit")
        lab_peer.read_log.clear()
        enable_before = lab_model.get_float(CONTROL_ADDRESSES["export_limit_enable"])
        before_frames = lab.runtime_data.device.client.requests
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": export_control.entity_id, "value": 2.3},
            blocking=True,
        )
        command_frames = lab.runtime_data.device.client.requests - before_frames
        assert command_frames == 2
        assert lab.runtime_data.device.last_control_timing["requests"] == 2
        assert lab_model.get_float(CONTROL_ADDRESSES["export_limit_enable"]) == enable_before
        assert lab.runtime_data.last_command_timing["total_ms"] >= 0
        shown = hass.states.get(export_control.entity_id)
        assert shown.state == "2.3" and shown.attributes["step"] == 0.1
        assert shown.attributes["max"] == lab_model.config.rated_kw
        # Display formatting must not round the authoritative protocol read-back.
        assert lab.runtime_data.device.controls["export_limit"] == lab_model.get_float(
            CONTROL_ADDRESSES["export_limit"]
        )
        assert lab.runtime_data.device.controls["export_limit"] != 2.3
        assert not any(
            address in (0x1240, 0x1270, 0x1272, 0x1274, 0x1276)
            for _, _, address, _ in lab_peer.read_log
        )
        results.append(
            "number readback uses 0.1 kW precision without changing raw float32; confirmed edits do not read unrelated telemetry"
        )

        for key, address, domain, service, data in (
            ("work_mode", 0x300C, "select", "select_option", {"option": "backup"}),
            ("grid_charge", 0x322A, "switch", "turn_off", {}),
            ("export_limit_enable", 0x3002, "switch", "turn_off", {}),
        ):
            control = next(e for e in controls if e.translation_key == key)
            lab_peer.read_log.clear()
            before_frames = lab.runtime_data.device.client.requests
            before_writes = lab_peer.write_count
            await hass.services.async_call(
                domain, service, {"entity_id": control.entity_id, **data}, blocking=True
            )
            assert lab.runtime_data.device.client.requests - before_frames == 2
            assert lab_peer.write_count - before_writes == 1
            assert list(lab_peer.read_log) == [(3, 1, address, 2)]
        results.append("EMS mode and switches also use one write and one exact target readback")

        flow_keys = {"solar_power", "battery_power", "load_power", "grid_power"}
        await lab.runtime_data.device.refresh(flow_keys, force=True)
        for key in flow_keys:
            lab.runtime_data.device._timestamps.pop(key, None)
        lab_peer.read_log.clear()
        values = await lab.runtime_data.device.refresh(flow_keys)
        assert list(lab_peer.read_log) == [(3, 1, 0x1270, 8)]
        assert all(values[key] is not None for key in flow_keys)
        results.append("four live flow powers use one known contiguous FC03 response")

        # Measure the full poll the previous command path forced after read-back.
        before_frames = lab.runtime_data.device.client.requests
        lab.runtime_data.device._timestamps.clear()
        await lab.runtime_data.async_refresh()
        avoided_refresh_frames = lab.runtime_data.device.client.requests - before_frames
        assert avoided_refresh_frames > 0

        mode_control = next(e for e in controls if e.translation_key == "work_mode")
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": mode_control.entity_id, "option": "backup"},
            blocking=True,
        )
        assert hass.states.get(read_mode.entity_id).state == "backup"
        results.append(
            "confirmed EMS edit updates both writable and read-only mode before returning"
        )

        lab_peer.fault = "reject_write"
        lab_peer.read_log.clear()
        try:
            await hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": export_control.entity_id, "value": 3.1},
                blocking=True,
            )
        except HomeAssistantError:
            pass
        else:
            raise AssertionError("Rejected write reported success")
        finally:
            lab_peer.fault = "normal"
        assert hass.states.get(export_control.entity_id).state == "2.3"
        assert any(address == 0x1240 for _, _, address, _ in lab_peer.read_log)
        results.append(
            "rejected edits retain authoritative value and use the full recovery refresh"
        )

        client = lab.runtime_data.device.client
        original_request = client._request_once
        applied = asyncio.Event()

        async def hold_control_ack(pdu, unit):
            response = await original_request(pdu, unit)
            if pdu[:3] == b"\x10\x32\x2a":
                applied.set()
                await asyncio.Event().wait()
            return response

        before_writes = lab_peer.write_count
        with patch.object(client, "_request_once", hold_control_ack):
            cancelled = asyncio.create_task(lab.runtime_data.async_command("grid_charge", False))
            await asyncio.wait_for(applied.wait(), 2)
            cancelled.cancel()
            try:
                await cancelled
            except asyncio.CancelledError:
                pass
            else:
                raise AssertionError("Cancelled command was reported as completed")
        assert lab_peer.write_count == before_writes + 1
        assert lab.runtime_data.device.last_command == "uncertain"
        assert lab.runtime_data.device.controls["grid_charge"] == 0
        assert lab.runtime_data.device._pending_controls == 0
        assert not lab.runtime_data.device._control_tasks
        results.append(
            "cancelled write remains uncertain; HA recovers actual state without resending"
        )

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
        # Enabling remote access enables entities and HA reloads after a delay.
        # Starts are refused until the enabled entities are live; otherwise the
        # request below stops at that gate and never re-probes. Shorten HA's 30 s
        # delay only here: elsewhere this check reloads explicitly, and an extra
        # automatic reload would race with it.
        config_entries.RELOAD_AFTER_UPDATE_DELAY = 0.05
        try:
            await lab.runtime_data.async_set_remote_enabled(True)
            async with asyncio.timeout(10):
                while (  # noqa: ASYNC110 - bounded probe of HA reload and live entities
                    lab.state is not ConfigEntryState.LOADED
                    or not lab.runtime_data.remote_entities_ready
                ):
                    await asyncio.sleep(0.02)
            await hass.async_block_till_done()
        finally:
            config_entries.RELOAD_AFTER_UPDATE_DELAY = default_reload_delay
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
        print(
            json.dumps(
                {
                    "result": "passed",
                    "checks": results,
                    "control_requests": {
                        "confirmed_export_edit": command_frames,
                        "avoided_full_refresh": avoided_refresh_frames,
                    },
                },
                ensure_ascii=False,
            )
        )
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
