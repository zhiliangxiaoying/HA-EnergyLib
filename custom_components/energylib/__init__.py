"""EnergyLIB profile-aware integration. Physical profiles remain read-only."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PORT,
    EVENT_CORE_CONFIG_UPDATE,
    EVENT_HOMEASSISTANT_STOP,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, RETIRED_ALARM_ENTITIES, RETIRED_DIAGNOSTIC_ENTITIES
from .coordinator import EnergyLibCoordinator
from .dashboard import async_register_dashboard
from .energylib_core.legacy import IdentityMismatch, UnsupportedDevice
from .energylib_core.runtime import DeviceRuntime
from .energylib_core.transport.modbus_tcp import EnergyLibError, ModbusTcpClient
from .migration import async_migrate_entry  # noqa: F401
from .services import async_register_services
from .twin_panel import async_register_twin_files

PLATFORMS = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SELECT,
    Platform.NUMBER,
    Platform.SWITCH,
    Platform.BUTTON,
]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config) -> bool:
    await async_register_dashboard(hass)
    await async_register_twin_files(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if entry.version != 2 or entry.data.get("profile_version") != 1:
        raise ConfigEntryError("Unsupported profile revision")
    try:
        device = DeviceRuntime(
            ModbusTcpClient(entry.data[CONF_HOST], entry.data[CONF_PORT], entry.data["unit_id"]),
            entry.data["profile_id"],
            task_factory=lambda coro: entry.async_create_background_task(
                hass, coro, "energylib remote lease"
            ),
        )
    except ValueError as err:
        raise ConfigEntryError("Unsupported profile") from err
    try:
        await device.initialize(entry.data["serial"])
        if entry.unique_id != device.unique_id:
            raise ConfigEntryError("Identity differs from configuration")
        # Controls need both the user's opt-in and a device that answers every
        # control register; a read-only device simply gets no control entities.
        device.allow_control = bool(
            entry.options.get("allow_control", False) and device.profile.controls_supported
        )
        _update_entity_registry(hass, entry, device)
        coordinator = EnergyLibCoordinator(hass, entry, device)
        entry.runtime_data = coordinator
        await coordinator.async_load_state()
        await coordinator.async_config_entry_first_refresh()
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        coordinator.async_update_listeners()
    except (IdentityMismatch, UnsupportedDevice) as err:
        await device.close()
        raise ConfigEntryError("Device identity/profile mismatch") from err
    except EnergyLibError as err:
        await device.close()
        raise ConfigEntryNotReady("Cannot read inverter") from err
    except BaseException:
        await device.close()
        raise

    async def stop(event):
        await device.close()
        await coordinator.async_save_state()

    async def registry_changed(event):
        entity = er.async_get(hass).async_get(event.data.get("entity_id", ""))
        if entity is not None and entity.config_entry_id == entry.entry_id:
            coordinator.device._timestamps.clear()
            await coordinator.async_request_refresh()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop))
    entry.async_on_unload(hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, registry_changed))
    # Advertised metadata may change without changing the live connection.
    settings = _reload_settings(entry)

    async def updated(hass, updated_entry):
        if _reload_settings(updated_entry) != settings:
            await async_reload_entry(hass, updated_entry)

    entry.async_on_unload(entry.add_update_listener(updated))
    # Entity and device names are translated once, in HA's system language;
    # reload so they follow a later change of that language.
    language = hass.config.language

    async def core_config_changed(event):
        if hass.config.language != language:
            hass.config_entries.async_schedule_reload(entry.entry_id)

    entry.async_on_unload(hass.bus.async_listen(EVENT_CORE_CONFIG_UPDATE, core_config_changed))
    async_register_services(hass)
    return True


def _update_entity_registry(hass, entry, device):
    """Remove redundant UI entities; preserve the single alert status and store.

    Explicitly user-disabled entities stay disabled. No protocol write is made.
    Alarm counts remain attributes; stored history and events are unaffected.
    """
    registry = er.async_get(hass)
    ready = {
        "remote_status",
    }
    retired = {
        (domain, f"{device.unique_id}_{key}")
        for key, domain in {
            **RETIRED_ALARM_ENTITIES,
            **RETIRED_DIAGNOSTIC_ENTITIES,
            "remote_power": "number",
            "remote_control": "switch",
        }.items()
    }
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.platform != DOMAIN:
            continue
        if (entity.domain, entity.unique_id) in retired:
            registry.async_remove(entity.entity_id)
            continue
        if (
            device.allow_control
            and entity.translation_key in ready
            and entity.disabled_by is er.RegistryEntryDisabler.INTEGRATION
        ):
            registry.async_update_entity(entity.entity_id, disabled_by=None)


async def async_reload_entry(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


def _reload_settings(entry):
    return (
        tuple(
            entry.data.get(key)
            for key in (CONF_HOST, CONF_PORT, "unit_id", "serial", "profile_id", "profile_version")
        ),
        dict(entry.options),
    )


async def async_unload_entry(hass, entry):
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.device.close()
        await entry.runtime_data.async_save_state()
    return unloaded


async def async_remove_entry(hass, entry):
    from homeassistant.helpers.storage import Store

    await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.state").async_remove()
