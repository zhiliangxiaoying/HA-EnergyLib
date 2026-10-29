"""Real HA flow/registry checks with temporary config and loopback peers only.

Run inside the target HA image: python -m tools.ha_discovery_check.
No multicast publisher, physical hardware or existing HA config is used.
"""

import asyncio
import json
import shutil
import tempfile
from datetime import datetime, timezone
from ipaddress import ip_address
from pathlib import Path
from unittest.mock import patch

from tools.discovery_advertisement import SERVICE, txt_properties
from tools.ha_runtime_check import StaticPaths
from tools.inverter_simulator import InverterServer
from tools.mdns_peer import PeerModel


async def check():
    from homeassistant import loader
    from homeassistant.bootstrap import async_load_base_functionality
    from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL, UrlManager
    from homeassistant.config_entries import ConfigEntries, ConfigEntryState
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
    from homeassistant.setup import async_setup_component

    root = Path(__file__).resolve().parents[1]
    tmp = tempfile.TemporaryDirectory(prefix="energylib-discovery-check-")
    config = Path(tmp.name)
    shutil.copytree(
        root / "custom_components" / "energylib", config / "custom_components" / "energylib"
    )
    hass = HomeAssistant(str(config))
    loader.async_setup(hass)
    hass.config_entries = ConfigEntries(hass, {})
    assert await async_load_base_functionality(hass)
    hass.config.components.update({"zeroconf", "http", "frontend"})
    hass.http = StaticPaths()
    hass.data[DATA_EXTRA_MODULE_URL] = UrlManager(lambda *args: None, [])
    peers = []
    results = []

    async def peer(serial, unit=1):
        model = PeerModel(serial)
        model.step(datetime.now(timezone.utc), 0)
        device = InverterServer(model, {"127.0.0.1"}, unit_id=unit)
        server = await asyncio.start_server(device.handle, "127.0.0.1", 0)
        peers.append((server, device))
        return server.sockets[0].getsockname()[1]

    async def discover(logger, port, *, props=None):
        return await hass.config_entries.flow.async_init(
            "energylib",
            context={"source": "zeroconf"},
            data=ZeroconfServiceInfo(
                ip_address=ip_address("127.0.0.1"),
                ip_addresses=[ip_address("127.0.0.1")],
                port=port,
                hostname=f"{logger.lower()}.local.",
                name=f"EnergyLIB-{logger}.{SERVICE}",
                type=SERVICE,
                properties=txt_properties(logger, unit_id=7) if props is None else props,
            ),
        )

    async def confirm(flow, port, unit=7):
        result = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {"host": "127.0.0.1", "port": port, "unit_id": unit}
        )
        assert result["type"] == "create_entry", result
        await hass.async_block_till_done()
        assert result["result"].state is ConfigEntryState.LOADED
        return result["result"]

    try:
        assert await async_setup_component(hass, "homeassistant", {})
        from homeassistant.helpers import issue_registry as ir

        from custom_components.energylib import auto_add, repairs

        auto_add.SETTLE_SECONDS = 0.2
        issues = ir.async_get(hass)

        # Two loggers announcing together: neither is added without asking.
        pa, pb = await peer("AUTO-INVERTER-A", 7), await peer("AUTO-INVERTER-B", 7)
        both = await asyncio.gather(discover("AUTO-LOGGER-A", pa), discover("AUTO-LOGGER-B", pb))
        assert [f["type"] for f in both] == ["form", "form"], both
        for flow in both:
            hass.config_entries.flow.async_abort(flow["flow_id"])
        assert not hass.config_entries.async_entries("energylib")
        results.append("two loggers on the network: both wait for confirmation")

        # The only logger: added read-only, with a Repairs confirmation for control.
        auto = await discover("AUTO-LOGGER-A", pa)
        assert auto["type"] == "create_entry", auto
        await hass.async_block_till_done()
        entry = auto["result"]
        assert entry.state is ConfigEntryState.LOADED
        assert entry.options == {"allow_control": False, "auto_added": True}, entry.options
        issue_id = auto_add.control_issue_id(entry)
        assert issues.async_get_issue("energylib", issue_id).is_fixable
        assert not [
            e
            for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
            if e.domain in ("select", "number", "switch") and e.disabled_by is None
        ], "an automatically added inverter must start without controls"
        results.append("the only logger is added read-only and Repairs asks before control")

        fix = await repairs.async_create_fix_flow(hass, issue_id, {"entry_id": entry.entry_id})
        fix.hass = hass
        form = await fix.async_step_init()
        assert form["type"] == "form" and form["step_id"] == "confirm", form
        assert form["description_placeholders"]["serial"] == "AUTO-INVERTER-A"
        assert (await fix.async_step_confirm({}))["type"] == "create_entry"
        await hass.async_block_till_done()
        assert entry.options == {"allow_control": True}, entry.options
        assert issues.async_get_issue("energylib", issue_id) is None
        results.append("confirming in Repairs allows control and closes the issue")

        # Removed by the user: never added back automatically.
        assert await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()
        again = await discover("AUTO-LOGGER-A", pa)
        assert again["type"] == "form", again
        hass.config_entries.flow.async_abort(again["flow_id"])
        results.append("after the user removes an inverter, discovery asks again")

        p1 = await peer("MDNS-INVERTER-001", 7)
        flow = await discover("MDNS-LOGGER-001", p1)
        assert flow["type"] == "form" and not hass.config_entries.async_entries("energylib")
        repeated = await discover("MDNS-LOGGER-001", p1)
        assert repeated["type"] == "abort" and repeated["reason"] == "already_in_progress", repeated
        entry = await confirm(flow, p1)
        assert entry.unique_id == "energylib_MDNS-INVERTER-001"
        assert entry.data["logger"]["sn"] == "MDNS-LOGGER-001" and entry.data["unit_id"] == 7
        registry, devices = er.async_get(hass), dr.async_get(hass)

        def identities():
            return {
                (e.entity_id, e.unique_id, e.device_id)
                for e in er.async_entries_for_config_entry(registry, entry.entry_id)
            }

        before = identities()
        device_before = {d.id for d in dr.async_entries_for_config_entry(devices, entry.entry_id)}
        results.append(
            "new contract, independent logger SN, unit 7, explicit confirmation, repeated flow dedup"
        )

        with patch.object(
            hass.config_entries, "async_reload", wraps=hass.config_entries.async_reload
        ) as reload:
            repeat = await discover("MDNS-LOGGER-001", p1)
            await hass.async_block_till_done()
            assert repeat["reason"] == "already_configured" and reload.call_count == 0
            metadata = await discover(
                "MDNS-LOGGER-001",
                p1,
                props=txt_properties("MDNS-LOGGER-001", unit_id=7, fw="test-2.0"),
            )
            await hass.async_block_till_done()
            assert metadata["reason"] == "already_configured" and reload.call_count == 0
            p2 = await peer("MDNS-INVERTER-001", 7)
            moved = await discover("MDNS-LOGGER-001", p2)
            await hass.async_block_till_done()
            assert moved["reason"] == "already_configured" and entry.data["port"] == p2
            assert reload.call_count == 1, reload.call_count
        assert identities() == before
        assert {
            d.id for d in dr.async_entries_for_config_entry(devices, entry.entry_id)
        } == device_before
        results.append(
            "unchanged/metadata announcements do not reload; verified port change reloads once; registries stable"
        )

        replacement = await discover("MDNS-LOGGER-NEW", p2)
        assert replacement["reason"] == "already_configured"
        assert entry.data["logger"]["sn"] == "MDNS-LOGGER-NEW"
        await hass.async_block_till_done()
        p3 = await peer("MDNS-INVERTER-002", 7)
        swapped = await discover("MDNS-LOGGER-NEW", p3)
        assert swapped["type"] == "form" and entry.data["port"] == p2
        second = await confirm(swapped, p3)
        assert second.unique_id != entry.unique_id and identities() == before
        results.append(
            "replacement logger keeps history; reused logger/new inverter requires a separate confirmation"
        )

        p4 = await peer("MDNS-INVERTER-003")
        third_flow = await discover(
            "MDNS-LOGGER-003", p4, props=txt_properties("MDNS-LOGGER-003", unit_id=None)
        )
        third = await confirm(third_flow, p4, 1)
        assert (
            third.data["unit_id"] == 1 and len(hass.config_entries.async_entries("energylib")) == 3
        )
        for bad in (
            {"id": "OLD"},
            txt_properties("BAD", auth="token"),
            txt_properties("BAD", api_ver="2"),
            txt_properties("BAD", omit=("fw",)),
            txt_properties("BAD", overrides={"unit_id": "256"}),
        ):
            rejected = await discover("BAD", p4, props=bad)
            assert rejected["reason"] == "unsupported_discovery", rejected
        results.append(
            "three independent inverters; optional unit ID fallback; legacy, incomplete and unsupported contracts rejected"
        )

        await hass.config_entries.async_unload(entry.entry_id)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert identities() == before
        assert (
            hass.states.get(
                next(eid for eid, uid, _ in before if uid.endswith("_solar_energy"))
            ).attributes["state_class"]
            == "total_increasing"
        )
        results.append("unload/setup preserves registry identities and energy state class")
        print(json.dumps({"result": "passed", "checks": results}))
    finally:
        for entry in list(hass.config_entries.async_entries("energylib")):
            if entry.state is ConfigEntryState.LOADED:
                await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_stop(force=True)
        for server, device in peers:
            server.close()
            await server.wait_closed()
            await device.close()
        tmp.cleanup()


if __name__ == "__main__":
    asyncio.run(check())
