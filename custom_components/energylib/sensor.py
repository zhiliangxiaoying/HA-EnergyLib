"""Native measurement entities derived from standard metadata."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory
from homeassistant.util import dt as dt_util

from .entity import EnergyLibEntity

PARALLEL_UPDATES = 0
CLASSES = {
    "power": SensorDeviceClass.POWER,
    "energy": SensorDeviceClass.ENERGY,
    "soc": SensorDeviceClass.BATTERY,
    "voltage": SensorDeviceClass.VOLTAGE,
    "current": SensorDeviceClass.CURRENT,
    "frequency": SensorDeviceClass.FREQUENCY,
    "temperature": SensorDeviceClass.TEMPERATURE,
    "enum": SensorDeviceClass.ENUM,
}


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    async_add_entities(
        EnergyLibSensor(coordinator, p) for p in coordinator.device.points() if p.kind != "problem"
    )


class EnergyLibSensor(EnergyLibEntity, SensorEntity):
    def __init__(self, coordinator, point):
        super().__init__(coordinator, point.key)
        self.point = point
        self._attr_translation_key = point.key
        self._attr_device_class = CLASSES.get(point.kind)
        self._attr_native_unit_of_measurement = point.unit
        self._attr_entity_registry_enabled_default = point.enabled
        if not point.enabled:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        if point.options:
            self._attr_options = list(point.options)
        elif point.kind not in ("schedule",):
            self._attr_state_class = (
                SensorStateClass.TOTAL_INCREASING
                if point.kind == "energy"
                else SensorStateClass.MEASUREMENT
            )
            self._attr_suggested_display_precision = point.precision

    @property
    def available(self):
        return super().available and self.coordinator.data.get(self.key) is not None

    @property
    def native_value(self):
        if self.point.kind == "schedule":
            return self.coordinator.device.schedule_status(dt_util.now())
        return self.coordinator.data.get(self.key)

    @property
    def extra_state_attributes(self):
        return self.coordinator.device.attributes.get(self.key)
