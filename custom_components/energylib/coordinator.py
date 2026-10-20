"""HA polling adapter; the library owns protocol and control coordination."""

import logging
from datetime import timedelta

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .energylib_core.transport.modbus_tcp import EnergyLibError

_LOGGER = logging.getLogger(__name__)


class EnergyLibCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, device):
        super().__init__(
            hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=timedelta(seconds=30)
        )
        self.entry, self.device = entry, device

    @property
    def identity(self):
        return self.device.identity

    def enabled_keys(self):
        registry = er.async_get(self.hass)
        result = set()
        for p in self.device.points():
            domain = "binary_sensor" if p.kind == "problem" else "sensor"
            entity_id = registry.async_get_entity_id(
                domain, DOMAIN, f"{self.device.unique_id}_{p.key}"
            )
            if entity_id is not None:
                if registry.async_get(entity_id).disabled_by is None:
                    result.add(p.key)
            elif p.enabled or (p.key == "battery_soc" and self.entry.data.get("legacy_soc")):
                result.add(p.key)
        return result

    async def _async_update_data(self):
        try:
            return await self.device.refresh(self.enabled_keys())
        except (EnergyLibError, ValueError) as err:
            raise UpdateFailed("Unable to read configured inverter") from err

    async def async_command(self, command, value=None, **kwargs):
        try:
            if command == "schedule":
                await self.device.set_schedule(value, **kwargs)
            elif command == "power":
                await self.device.set_battery_power(value, kwargs["duration"])
            else:
                await self.device.set_control(command, value)
        except (EnergyLibError, ValueError) as err:
            raise HomeAssistantError(str(err)) from err
        finally:
            self.device._timestamps.clear()
            # Commands must publish their read-back before the service returns;
            # the polling debouncer can otherwise leave controls stale for 10s.
            await self.async_refresh()
