"""Optional problem and link entities; not hundreds of individual alarms."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory

from .const import RETIRED_ALARM_ENTITIES
from .energylib_core.model.points import BINARY_KINDS
from .entity import EnergyLibAccessoryEntity, EnergyLibEntity, accessory_points

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    c = entry.runtime_data
    async_add_entities(
        (EnergyLibProblem if p.kind == "problem" else EnergyLibConnectivity)(c, p)
        for p in c.device.points()
        if p.kind in BINARY_KINDS and p.key not in RETIRED_ALARM_ENTITIES
    )
    async_add_entities(
        EnergyLibAccessoryPower(c, kind, serial, point)
        for kind, serial, point in accessory_points(c, binary=True)
    )


class EnergyLibAccessoryPower(EnergyLibAccessoryEntity, BinarySensorEntity):
    """Whether a smart plug or water heater is switched on."""

    _attr_device_class = BinarySensorDeviceClass.POWER

    @property
    def is_on(self):
        return self.accessory_value


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


class EnergyLibConnectivity(EnergyLibEntity, BinarySensorEntity):
    """Link state reported by the device, e.g. its external meter."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, point):
        super().__init__(coordinator, point.key)
        self._attr_translation_key = point.key
        self._attr_entity_registry_enabled_default = point.enabled

    @property
    def available(self):
        return super().available and self.coordinator.data.get(self.key) is not None

    @property
    def is_on(self):
        return self.coordinator.data.get(self.key)
