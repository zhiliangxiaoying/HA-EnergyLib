"""Opt-in switches; starting remote mode uses a finite 300-second lease."""

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory

from .entity import EnergyLibControl, controls_for

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    c = entry.runtime_data
    async_add_entities(EnergyLibSwitch(c, d) for d in controls_for(c, "switch"))


class EnergyLibSwitch(EnergyLibControl, SwitchEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, definition):
        super().__init__(coordinator, definition.key)
        self._attr_translation_key = definition.key

    @property
    def is_on(self):
        return bool(self.value)

    async def async_turn_on(self, **kwargs):
        await self.coordinator.async_command(self.key, True)

    async def async_turn_off(self, **kwargs):
        await self.coordinator.async_command(self.key, False)
