"""Actual HA, isolated config and loopback peer: remote drafts/buttons/status/services."""

import asyncio
import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .ha_runtime_check import StaticPaths
from .sinexcel_simulator import BenchConfig, BenchModel, BenchServer


def energylib_device_id(dr, hass, entry):
    # async_get_device() raises in newer HA when called outside an integration.
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    return next(d.id for d in devices if ("energylib", entry.unique_id) in d.identifiers)


async def check():
    from homeassistant import config_entries, loader
    from homeassistant.bootstrap import async_load_base_functionality
    from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL, UrlManager
    from homeassistant.config_entries import ConfigEntries, ConfigEntry, ConfigEntryState
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import entity_registry as er
    from homeassistant.setup import async_setup_component

    # Exercise HA's own delayed entity-enable reload, accelerated only in this
    # isolated process. Production uses HA's unmodified 30-second delay.
    config_entries.RELOAD_AFTER_UPDATE_DELAY = 0.05

    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="energylib-remote-check-") as tmp:
        config = Path(tmp)
        shutil.copytree(
            root / "custom_components/energylib", config / "custom_components/energylib"
        )
        model = BenchModel(BenchConfig(serial="ELIB-REMOTE-CHECK", writes=True))
        model.tick(datetime.now(timezone.utc), 0)
        peer = BenchServer(model, {"127.0.0.1"})
        server = await asyncio.start_server(peer.handle, "127.0.0.1", 0)
        hass = HomeAssistant(tmp)
        loader.async_setup(hass)
        hass.config_entries = ConfigEntries(hass, {})
        assert await async_load_base_functionality(hass)
        hass.config.components.update({"zeroconf", "http", "frontend"})
        hass.http = StaticPaths()
        hass.data[DATA_EXTRA_MODULE_URL] = UrlManager(lambda *args: None, [])
        await async_setup_component(hass, "homeassistant", {})
        entry = ConfigEntry(
            domain="energylib",
            data={
                "host": "127.0.0.1",
                "port": server.sockets[0].getsockname()[1],
                "unit_id": 1,
                "serial": model.config.serial,
                "profile_id": "sinexcel_v2",
                "profile_version": 1,
            },
            version=2,
            minor_version=1,
            options={"allow_control": True},
            source="user",
            title="Remote check",
            unique_id="energylib_" + model.config.serial,
            discovery_keys={},
            subentries_data=[],
        )
        checks = []
        try:
            from homeassistant.exceptions import HomeAssistantError
            from homeassistant.helpers import device_registry as dr

            await hass.config_entries.async_add(entry)
            await hass.async_block_till_done()
            assert entry.state is ConfigEntryState.LOADED
            registry = er.async_get(hass)
            entities = er.async_entries_for_config_entry(registry, entry.entry_id)
            keys = {
                "remote_target_power",
                "remote_duration",
                "start_charge",
                "start_discharge",
                "stop_remote",
                "remote_status",
                "remote_end_time",
            }
            chosen = {e.translation_key: e.entity_id for e in entities if e.translation_key in keys}
            assert len(chosen) == 7
            assert not any(
                e.translation_key in ("remote_power", "remote_control") for e in entities
            )
            assert all(
                e.disabled_by
                is (
                    er.RegistryEntryDisabler.INTEGRATION
                    if e.translation_key
                    in {
                        "remote_target_power",
                        "remote_duration",
                        "start_charge",
                        "start_discharge",
                        "stop_remote",
                        "remote_end_time",
                    }
                    else None
                )
                for e in entities
                if e.translation_key in keys
            )
            device_id = energylib_device_id(dr, hass, entry)
            access = next(e for e in entities if e.translation_key == "allow_remote_control")
            assert access.disabled_by is er.RegistryEntryDisabler.INTEGRATION
            assert hass.states.get(access.entity_id) is None
            assert hass.states.get(chosen["remote_end_time"]) is None
            assert await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()
            assert (
                registry.async_get(chosen["remote_end_time"]).disabled_by
                is er.RegistryEntryDisabler.INTEGRATION
            )
            before_access = peer.write_count
            unrelated = {
                e.entity_id: e.disabled_by
                for e in entities
                if e.translation_key not in keys | {"allow_remote_control"}
            }

            async def settle_enabled(previous_runtime):
                async with asyncio.timeout(10):
                    while (  # noqa: ASYNC110 - bounded probe of HA reload and live entities
                        entry.state is not ConfigEntryState.LOADED
                        or entry.runtime_data.device is previous_runtime
                        or not entry.runtime_data.remote_entities_ready
                    ):
                        await asyncio.sleep(0.02)
                    await hass.async_block_till_done()

            for enabled in (True, False):
                previous_runtime = entry.runtime_data.device
                await hass.services.async_call(
                    "energylib",
                    "set_remote_access",
                    {"device_id": device_id, "enabled": enabled},
                    blocking=True,
                )
                if enabled:
                    assert all(
                        registry.async_get(entity_id).disabled_by is None
                        for entity_id in [*chosen.values(), access.entity_id]
                    )
                    assert not entry.runtime_data.remote_entities_ready
                    try:
                        await entry.runtime_data.async_command("power", 1, duration=60)
                    except HomeAssistantError:
                        pass
                    else:
                        raise AssertionError("Task started during entity activation")
                    await settle_enabled(previous_runtime)
                else:
                    await hass.async_block_till_done()
                    assert all(
                        registry.async_get(chosen[key]).disabled_by
                        is er.RegistryEntryDisabler.INTEGRATION
                        for key in keys - {"remote_status"}
                    )
                    assert registry.async_get(access.entity_id).disabled_by is None
                assert (
                    hass.states.get(chosen["remote_status"]).attributes["remote_access_enabled"]
                    is enabled
                )
                assert all(
                    registry.async_get(entity_id).disabled_by == disabled
                    for entity_id, disabled in unrelated.items()
                )
            assert peer.write_count == before_access
            checks.append(
                "card permission restores native entities, blocks starts during HA activation, hides helpers on exit; unrelated entities unchanged and idle toggle never writes"
            )
            initial_runtime = entry.runtime_data.device
            before = peer.write_count
            await hass.services.async_call(
                "energylib",
                "set_remote_parameters",
                {"device_id": device_id, "power_kw": 1.5, "duration_minutes": 7},
                blocking=True,
            )
            settings = hass.states.get(chosen["remote_status"]).attributes["settings"]
            assert settings == {"power_kw": 1.5, "duration_minutes": 7, "max_power_kw": 5}
            assert hass.states.get(chosen["remote_target_power"]) is None
            assert hass.states.get(chosen["remote_duration"]) is None
            assert peer.write_count == before and entry.runtime_data.device is initial_runtime
            try:
                await hass.services.async_call(
                    "energylib",
                    "set_remote_parameters",
                    {"device_id": device_id, "power_kw": 2, "duration_minutes": 1.5},
                    blocking=True,
                )
            except HomeAssistantError:
                pass
            else:
                raise AssertionError("Fractional minutes accepted")
            assert initial_runtime.draft.power == 1.5 and initial_runtime.draft.duration == 7
            checks.append(
                "combined settings work with native helpers disabled; parameter edits cause no writes/reload; validation is atomic"
            )
            retired = [
                registry.async_get_or_create(
                    platform,
                    "energylib",
                    f"{entry.unique_id}_{key}",
                    config_entry=entry,
                ).entity_id
                for platform, key in (("number", "remote_power"), ("switch", "remote_control"))
            ]
            for entity_id in [*chosen.values(), access.entity_id]:
                registry.async_update_entity(entity_id, disabled_by=None)
            assert await hass.config_entries.async_reload(entry.entry_id)
            await asyncio.sleep(0.2)
            await hass.async_block_till_done()
            assert all(registry.async_get(entity_id) is None for entity_id in retired)
            runtime = entry.runtime_data.device
            device_id = energylib_device_id(dr, hass, entry)
            checks.append(
                "status ready; native helpers and end timestamp default disabled, retained on reload; retired UI controls absent"
            )

            async def number(key, value):
                await hass.services.async_call(
                    "number", "set_value", {"entity_id": chosen[key], "value": value}, blocking=True
                )

            async def press(key):
                await hass.services.async_call(
                    "button", "press", {"entity_id": chosen[key]}, blocking=True
                )

            def state(key):
                return hass.states.get(chosen[key])

            access = next(e for e in entities if e.translation_key == "allow_remote_control")
            assert hass.states.get(access.entity_id).state == "off"
            before = peer.write_count
            for command in ("power", "start_charge", "start_discharge"):
                try:
                    await entry.runtime_data.async_command(command, 1, duration=60)
                except HomeAssistantError:
                    pass
                else:
                    raise AssertionError("Remote permission bypassed")
            assert peer.write_count == before

            async def access_switch(enabled):
                previous_runtime = entry.runtime_data.device
                needs_reload = (
                    bool(entry.runtime_data._remote_entity_changes(True)) if enabled else False
                )
                await hass.services.async_call(
                    "switch",
                    "turn_on" if enabled else "turn_off",
                    {"entity_id": access.entity_id},
                    blocking=True,
                )
                if needs_reload:
                    await settle_enabled(previous_runtime)
                else:
                    await hass.async_block_till_done()

            await access_switch(True)
            assert peer.write_count == before and entry.runtime_data.device is runtime
            checks.append(
                "remote access defaults off; commands blocked; enabling makes no writes or reload"
            )

            before = peer.write_count
            await number("remote_target_power", 2)
            await number("remote_duration", 1)
            assert peer.write_count == before and entry.runtime_data.device is runtime
            assert runtime.remote_deadline is None
            assert state("remote_duration").attributes["mode"] == "box"
            assert (
                state("remote_duration").state == "1.0"
                or float(state("remote_duration").state) == 1
            )
            assert await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()
            runtime = entry.runtime_data.device
            assert runtime.draft.export() == {"remote_target_power": 2, "remote_duration": 1}
            assert hass.states.get(access.entity_id).state == "on"
            assert peer.write_count == before and runtime.remote_status == "idle"
            checks.append(
                "native draft edits cause no writes/reload; drafts persist through runtime reload"
            )

            await press("start_charge")
            assert model.get_float(0xC648) == -2 and model.get_float(0xC646) == 1
            assert state("remote_status").state == "charging"
            assert state("remote_end_time").state not in ("unknown", "unavailable")
            deadline = runtime.remote_deadline
            writes = peer.write_count
            await entry.runtime_data.async_set_remote_enabled(True)
            assert runtime.remote_deadline == deadline and peer.write_count == writes
            await number("remote_target_power", 3)
            await number("remote_duration", 2)
            assert peer.write_count == writes and runtime.remote_deadline == deadline
            assert runtime.remote_target == -2
            await press("start_discharge")
            assert model.get_float(0xC648) == 3 and state("remote_status").state == "discharging"
            peer.fault = "mismatch"
            try:
                await access_switch(False)
            except HomeAssistantError:
                pass
            else:
                raise AssertionError("Unconfirmed exit reported disabled")
            assert hass.states.get(access.entity_id).state == "on"
            peer.fault = "normal"
            await access_switch(False)
            assert model.get_float(0xC646) == 0 and hass.states.get(access.entity_id).state == "off"
            assert state("remote_status").state == "stopped"
            before = peer.write_count
            try:
                await hass.services.async_call(
                    "energylib",
                    "set_battery_power",
                    {"device_id": device_id, "power_kw": 1, "duration": 60},
                    blocking=True,
                )
            except HomeAssistantError:
                pass
            else:
                raise AssertionError("Public service bypassed remote access")
            assert peer.write_count == before
            await access_switch(True)
            runtime = entry.runtime_data.device
            checks.append(
                "disabling confirms active exit; failed exit stays enabled; public service cannot bypass access"
            )
            await hass.services.async_call(
                "energylib", "stop_battery_power", {"device_id": device_id}, blocking=True
            )
            assert state("remote_status").state == "stopped" and model.get_float(0xC646) == 0
            checks.append(
                "charge/discharge snapshot sign, end timestamp, immutable running task, shared stop action"
            )

            for key, value in (("remote_target_power", 0), ("remote_duration", 1)):
                await number(key, value)
            before = peer.write_count
            try:
                await press("start_charge")
            except HomeAssistantError:
                pass
            else:
                raise AssertionError("New start accepted zero power")
            assert peer.write_count == before
            try:
                await number("remote_duration", 1.5)
            except HomeAssistantError:
                pass
            else:
                raise AssertionError("Draft accepted fractional minutes")
            await number("remote_target_power", 2)
            await entry.runtime_data.async_command("work_mode", "time_of_use")
            before = peer.write_count
            try:
                await press("start_discharge")
            except HomeAssistantError:
                pass
            else:
                raise AssertionError("New start silently took over TOU")
            assert peer.write_count == before
            await entry.runtime_data.async_command("work_mode", "self_consumption")
            checks.append(
                "new zero/fractional inputs and TOU conflicts rejected without device writes"
            )

            await hass.services.async_call(
                "energylib",
                "set_battery_power",
                {"device_id": device_id, "power_kw": 0, "duration": 300},
                blocking=True,
            )
            assert state("remote_status").state == "holding"
            await press("stop_remote")
            checks.append(
                "public service retains zero-power and seconds compatibility without legacy UI"
            )

            await hass.services.async_call(
                "energylib",
                "set_battery_power",
                {"device_id": device_id, "power_kw": 1, "duration": 1},
                blocking=True,
            )
            await asyncio.wait_for(asyncio.shield(runtime._lease_task), 2)
            await hass.async_block_till_done()
            assert state("remote_status").state == "completed"
            assert model.get_float(0xC646) == 0
            peer.fault = "mismatch"
            try:
                await press("start_charge")
            except HomeAssistantError:
                pass
            else:
                raise AssertionError("Mismatched write reported success")
            # Acknowledged but not applied: read-back proves no session started.
            assert state("remote_status").state == "idle"
            peer.fault = "normal"
            await press("stop_remote")
            await press("start_charge")
            assert await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()
            assert (
                state("remote_status").state == "idle"
                and entry.runtime_data.device.remote_deadline is None
            )
            assert state("remote_target_power").state in ("2.0", "2")
            assert model.get_float(0xC646) == 0
            checks.append(
                "expiration publishes without polling; refused start returns to idle; reload exits without task resume"
            )
            hass.config_entries.async_update_entry(entry, options={"allow_control": False})
            await hass.async_block_till_done()
            before = peer.write_count
            try:
                await hass.services.async_call(
                    "energylib",
                    "set_remote_access",
                    {"device_id": device_id, "enabled": True},
                    blocking=True,
                )
            except HomeAssistantError:
                pass
            else:
                raise AssertionError("Remote permission bypassed integration control access")
            try:
                await hass.services.async_call(
                    "energylib", "stop_battery_power", {"device_id": device_id}, blocking=True
                )
            except HomeAssistantError:
                pass
            else:
                raise AssertionError("Stop service bypassed disabled controls")
            assert peer.write_count == before
            checks.append("new public stop service cannot bypass control opt-in")
            print(json.dumps({"result": "passed", "checks": checks}))
        finally:
            peer.fault = "normal"
            if entry.state is ConfigEntryState.LOADED:
                await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_stop(force=True)
            server.close()
            await peer.close()
            await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(check())
