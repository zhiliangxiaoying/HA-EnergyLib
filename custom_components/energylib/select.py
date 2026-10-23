"""Separate writable mode selector; read-only sensor remains compatible."""

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory

from .entity import EnergyLibControl, controls_for

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    c = entry.runtime_data
    async_add_entities(EnergyLibSelect(c, d) for d in controls_for(c, "select"))


class EnergyLibSelect(EnergyLibControl, SelectEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, definition):
        super().__init__(coordinator, definition.key)
        self._attr_translation_key = definition.key
        self._attr_options = list(coordinator.device.control_options(definition))

    @property
    def current_option(self):
        return self.control_value

    async def async_select_option(self, option):
        await self.coordinator.async_command(self.key, option)
