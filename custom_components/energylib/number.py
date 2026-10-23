"""Opt-in numeric controls with authoritative read-back."""

from decimal import Decimal

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory

from .energylib_core.model.points import DRAFTS
from .entity import EnergyLibControl, EnergyLibEntity, controls_for

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    c = entry.runtime_data
    async_add_entities(EnergyLibNumber(c, d) for d in controls_for(c, "number"))
    if c.device.allow_control and c.device.profile.controls_supported:
        async_add_entities(EnergyLibDraftNumber(c, d) for d in DRAFTS)


class EnergyLibNumber(EnergyLibControl, NumberEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, definition):
        super().__init__(coordinator, definition.key)
        self._attr_translation_key = definition.key
        low, high = definition.limits(coordinator.device.profile.max_power_kw)
        self._attr_native_min_value = low
        self._attr_native_max_value = high
        self._attr_native_step = definition.step
        self._display_digits = max(0, -Decimal(str(definition.step)).as_tuple().exponent)
        self._attr_native_unit_of_measurement = (
            "kW" if definition.key in ("remote_power", "export_limit") else "%"
        )

    @property
    def native_value(self):
        # Float32 read-back can be 6.800000190734863 for a 6.8 kW setting.
        # Format the HA control state to its step; keep runtime verification raw.
        value = self.control_value
        return None if value is None else round(value, self._display_digits)

    async def async_set_native_value(self, value):
        await self.coordinator.async_command(self.key, value)


class EnergyLibDraftNumber(EnergyLibEntity, NumberEntity):
    _attr_entity_registry_enabled_default = False
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator, definition):
        super().__init__(coordinator, definition.key)
        self.definition = definition
        self._attr_translation_key = definition.key
        self._attr_native_min_value = definition.minimum
        self._attr_native_step = definition.step
        self._attr_icon = "mdi:flash" if self.key == "remote_target_power" else "mdi:timer-outline"
        self._attr_native_unit_of_measurement = "kW" if self.key == "remote_target_power" else "min"

    @property
    def native_max_value(self):
        try:
            return self.definition.limits(self.coordinator.device.profile.max_power_kw)[1]
        except ValueError:
            # Rating lost on a reconnect re-probe: the entity is unavailable then,
            # and HA must still be able to write its state attributes.
            return 0.0

    @property
    def available(self):
        runtime = self.coordinator.device
        return runtime.allow_control and runtime.profile.controls_supported

    @property
    def native_value(self):
        return self.coordinator.device.draft.export()[self.key]

    async def async_set_native_value(self, value):
        await self.coordinator.async_set_draft(self.key, value)
