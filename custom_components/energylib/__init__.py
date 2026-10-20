"""EnergyLIB profile-aware integration. Physical profiles remain read-only."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er

from .coordinator import EnergyLibCoordinator
from .energylib_core.legacy import IdentityMismatch, UnsupportedDevice
from .energylib_core.runtime import DeviceRuntime
from .energylib_core.transport.modbus_tcp import EnergyLibError, ModbusTcpClient
from .migration import async_migrate_entry  # noqa: F401
from .services import async_register_services

PLATFORMS = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SELECT,
    Platform.NUMBER,
    Platform.SWITCH,
]


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
        coordinator = EnergyLibCoordinator(hass, entry, device)
        entry.runtime_data = coordinator
        await coordinator.async_config_entry_first_refresh()
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
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

    async def registry_changed(event):
        entity = er.async_get(hass).async_get(event.data.get("entity_id", ""))
        if entity is not None and entity.config_entry_id == entry.entry_id:
            coordinator.device._timestamps.clear()
            await coordinator.async_request_refresh()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop))
    entry.async_on_unload(hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, registry_changed))
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    async_register_services(hass)
    return True


async def async_reload_entry(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.device.close()
    return unloaded
