"""Opt-in numeric controls with authoritative read-back."""

from homeassistant.components.number import NumberEntity
from homeassistant.const import EntityCategory

from .entity import EnergyLibControl, controls_for

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    c = entry.runtime_data
    async_add_entities(EnergyLibNumber(c, d) for d in controls_for(c, "number"))


class EnergyLibNumber(EnergyLibControl, NumberEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, definition):
        super().__init__(coordinator, definition.key)
        self._attr_translation_key = definition.key
        low, high = definition.limits(coordinator.device.profile.max_power_kw)
        self._attr_native_min_value = low
        self._attr_native_max_value = high
        self._attr_native_step = definition.step
        self._attr_native_unit_of_measurement = (
            "kW" if definition.key in ("remote_power", "export_limit") else "%"
        )

    @property
    def native_value(self):
        return self.value

    async def async_set_native_value(self, value):
        await self.coordinator.async_command(self.key, value)
