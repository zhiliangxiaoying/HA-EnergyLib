"""Explicit starts take a draft snapshot; stops share the public action path."""

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory

from .energylib_core.model.points import BUTTONS
from .entity import EnergyLibEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    c = entry.runtime_data
    if c.device.allow_control and c.device.profile.controls_supported:
        async_add_entities(EnergyLibRemoteButton(c, key) for key in BUTTONS)


class EnergyLibRemoteButton(EnergyLibEntity, ButtonEntity):
    _attr_entity_registry_enabled_default = False
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, key):
        super().__init__(coordinator, key)
        self._attr_translation_key = key
        self._attr_icon = {
            "start_charge": "mdi:battery-arrow-down",
            "start_discharge": "mdi:battery-arrow-up",
            "stop_remote": "mdi:stop-circle",
        }[key]

    @property
    def available(self):
        runtime = self.coordinator.device
        return (
            runtime.allow_control
            and runtime.profile.controls_supported
            and (
                self.key == "stop_remote" or (self.coordinator.remote_enabled and super().available)
            )
        )

    async def async_press(self):
        await self.coordinator.async_command(self.key)
