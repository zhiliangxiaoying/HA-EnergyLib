"""Actual HA, isolated config and loopback peer: alarms/events/store/registries."""

import asyncio
import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .ha_runtime_check import StaticPaths
from .sinexcel_simulator import BenchConfig, BenchModel, BenchServer


async def check():
    from homeassistant import loader
    from homeassistant.bootstrap import async_load_base_functionality
    from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL, UrlManager
    from homeassistant.config_entries import ConfigEntries, ConfigEntry, ConfigEntryState
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import entity_registry as er
    from homeassistant.setup import async_setup_component

    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="energylib-alarm-check-") as tmp:
        config = Path(tmp)
        shutil.copytree(
            root / "custom_components/energylib", config / "custom_components/energylib"
        )
        model = BenchModel(BenchConfig(serial="ELIB-ALARM-CHECK", writes=True))
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
            options={},
            source="user",
            title="Alarm check",
            unique_id="energylib_" + model.config.serial,
            discovery_keys={},
            subentries_data=[],
        )
        events = []
        hass.bus.async_listen("energylib_alarm_changed", lambda e: events.append(e.data))
        checks = []
        try:
            model.bits[1][0x1054] = True
            await hass.config_entries.async_add(entry)
            await hass.async_block_till_done()
            assert entry.state is ConfigEntryState.LOADED
            registry = er.async_get(hass)
            entries = er.async_entries_for_config_entry(registry, entry.entry_id)
            ids = {e.translation_key: e.entity_id for e in entries if e.domain == "sensor"}
            before = {e.unique_id: (e.entity_id, e.device_id) for e in entries}
            retired = {
                "active_alarms": "sensor",
                "fault_count": "sensor",
                "warning_count": "sensor",
                "any_fault": "binary_sensor",
                "any_warning": "binary_sensor",
                # Former diagnostics now reported by the alarm status.
                "grid_fault": "binary_sensor",
                "bms_comm_fault": "binary_sensor",
            }
            assert not any(e.translation_key in retired for e in entries)
            seeded = []
            for index, (key, domain) in enumerate(retired.items()):
                old = registry.async_get_or_create(
                    domain,
                    "energylib",
                    f"{entry.unique_id}_{key}",
                    config_entry=entry,
                    suggested_object_id=f"old_summary_{index}",
                    disabled_by=er.RegistryEntryDisabler.USER if index % 2 else None,
                )
                registry.async_update_entity(old.entity_id, name="Renamed old summary")
                seeded.append(old.entity_id)
            assert await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()
            assert all(registry.async_get(entity_id) is None for entity_id in seeded)
            assert not events
            alarm = hass.states.get(ids["alarm_status"])
            assert alarm.state == "abnormal" and alarm.attributes["alarms"][0]["name"] == "风扇故障"
            assert alarm.attributes["active_alarms"] == 1 and alarm.attributes["fault_count"] == 1
            assert alarm.attributes["any_fault"] is True and alarm.attributes["warning_count"] == 0
            checks.append(
                "one alert entity; seven renamed/enabled/disabled legacy helpers and diagnostics removed on reload; counts retained as attributes"
            )

            async def refresh():
                entry.runtime_data.device._timestamps.clear()
                await entry.runtime_data.async_refresh()
                await hass.async_block_till_done()

            model.bits[1][0x1054] = False
            model.bits[1][0x1053] = True
            await refresh()
            assert len(events) == 2
            assert {e["change"] for e in events} == {"raised", "cleared"}
            assert all(
                set(e)
                == {
                    "device_id",
                    "alarm_id",
                    "change",
                    "source",
                    "code",
                    "name",
                    "severity",
                    "observed_at",
                }
                for e in events
            )
            assert hass.states.get(ids["alarm_status"]).attributes["active_alarms"] == 1
            await refresh()
            assert len(events) == 2
            checks.append(
                "same-count replacement emits two device-targeted events, repeated read emits none"
            )

            model.bits[2][0x1048] = True
            await refresh()
            peer.fault = "unit2_offline"
            await refresh()
            alarm = hass.states.get(ids["alarm_status"])
            assert alarm.state == "incomplete" and not alarm.attributes["complete"]
            assert alarm.attributes["active_alarms"] is None
            assert any(a["source"] == 2 and a["stale"] for a in alarm.attributes["alarms"])
            assert len(events) == 3
            checks.append("missing source stays stale with no false clear; count unavailable")
            peer.fault = "short_frame"
            await refresh()
            assert hass.states.get(ids["alarm_status"]).state == "offline"
            assert hass.states.get(ids["alarm_status"]).attributes["alarms"]
            assert len(events) == 3
            checks.append(
                "complete communication outage keeps alarm history visible without clear events"
            )
            peer.fault = "normal"
            await refresh()
            count = len(events)
            assert await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()
            assert len(events) == count
            after = er.async_entries_for_config_entry(registry, entry.entry_id)
            assert all(before[e.unique_id] == (e.entity_id, e.device_id) for e in after)
            history = hass.states.get(ids["alarm_status"]).attributes["history"]
            assert len(history) == 3 and any(r["cleared_at"] for r in history)
            assert list((config / ".storage").glob("energylib.*.state"))
            assert peer.write_count == 0
            checks.append(
                "durable occurrence history, reload baseline, stable registries, zero Modbus writes"
            )
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
