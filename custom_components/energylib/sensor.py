"""Native measurement entities derived from standard metadata."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory
from homeassistant.util import dt as dt_util

from .const import RETIRED_ALARM_ENTITIES
from .entity import EnergyLibEntity

PARALLEL_UPDATES = 0
CLASSES = {
    "power": SensorDeviceClass.POWER,
    "energy": SensorDeviceClass.ENERGY,
    "energy_storage": SensorDeviceClass.ENERGY_STORAGE,
    "soc": SensorDeviceClass.BATTERY,
    "voltage": SensorDeviceClass.VOLTAGE,
    "current": SensorDeviceClass.CURRENT,
    "frequency": SensorDeviceClass.FREQUENCY,
    "temperature": SensorDeviceClass.TEMPERATURE,
    "enum": SensorDeviceClass.ENUM,
    "schedule": SensorDeviceClass.ENUM,
    "task": SensorDeviceClass.ENUM,
    "timestamp": SensorDeviceClass.TIMESTAMP,
}


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    async_add_entities(
        EnergyLibSensor(coordinator, p)
        for p in coordinator.device.points()
        if p.kind != "problem" and p.key not in RETIRED_ALARM_ENTITIES
    )


class EnergyLibSensor(EnergyLibEntity, SensorEntity):
    _unrecorded_attributes = frozenset({"alarms", "history"})

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
        elif point.kind not in ("schedule", "timestamp"):
            self._attr_state_class = (
                SensorStateClass.TOTAL_INCREASING
                if point.kind == "energy"
                else SensorStateClass.MEASUREMENT
            )
            self._attr_suggested_display_precision = point.precision

    @property
    def available(self):
        if self.key in ("remote_status", "remote_end_time"):
            return (
                self.coordinator.device.allow_control
                and self.coordinator.device.profile.controls_supported
            )
        if self.key == "alarm_status":
            # Keep observation history visible during a coordinator outage.
            return True
        return super().available and self.coordinator.data.get(self.key) is not None

    @property
    def native_value(self):
        if self.key in ("remote_status", "remote_end_time"):
            return getattr(self.coordinator.device, self.key)
        if self.key == "alarm_status":
            return self.coordinator.device.values.get(self.key, "offline")
        if self.point.kind == "schedule":
            return self.coordinator.device.schedule_status(dt_util.now())
        return self.coordinator.data.get(self.key)

    @property
    def extra_state_attributes(self):
        if self.key == "work_mode":
            runtime = self.coordinator.device
            return {
                **(runtime.attributes.get(self.key) or {}),
                "control_access_enabled": runtime.allow_control,
                "controls_supported": runtime.profile.controls_supported,
            }
        if self.key == "alarm_status":
            return {
                **self.coordinator.device.attributes.get(self.key, {}),
                **{key: self.coordinator.device.values.get(key) for key in RETIRED_ALARM_ENTITIES},
            }
        if self.key == "remote_status":
            return {
                "remote_access_enabled": self.coordinator.remote_enabled,
                "entities_ready": self.coordinator.remote_entities_ready,
                **self.coordinator.device.attributes.get(self.key, {}),
                "settings": {
                    "power_kw": self.coordinator.device.draft.power,
                    "duration_minutes": self.coordinator.device.draft.duration,
                    "max_power_kw": self.coordinator.device.profile.max_power_kw,
                },
                "actual_power_kw": self.coordinator.data.get("battery_power")
                if self.coordinator.last_update_success
                else None,
            }
        return self.coordinator.device.attributes.get(self.key)
