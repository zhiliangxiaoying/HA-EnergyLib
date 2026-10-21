"""Optional problem entities; not hundreds of individual alarms."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory

from .const import RETIRED_ALARM_ENTITIES
from .entity import EnergyLibEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    c = entry.runtime_data
    async_add_entities(
        EnergyLibProblem(c, p)
        for p in c.device.points()
        if p.kind == "problem" and p.key not in RETIRED_ALARM_ENTITIES
    )


class EnergyLibProblem(EnergyLibEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator, point):
        super().__init__(coordinator, point.key)
        self._attr_translation_key = point.key
        self._attr_entity_registry_enabled_default = point.enabled

    @property
    def extra_state_attributes(self):
        return self.coordinator.device.attributes.get(self.key)

    @property
    def available(self):
        if self.key in ("any_fault", "any_warning"):
            return self.coordinator.device.values.get(self.key) is not None
        return super().available and self.coordinator.data.get(self.key) is not None

    @property
    def is_on(self):
        if self.key in ("any_fault", "any_warning"):
            return self.coordinator.device.values.get(self.key)
        return self.coordinator.data.get(self.key)
