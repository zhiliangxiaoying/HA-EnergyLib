"""Shared HA device identity and default-disabled control behavior."""

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


class EnergyLibEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, key):
        super().__init__(coordinator)
        self.key = key
        runtime = coordinator.device
        self._attr_unique_id = f"{runtime.unique_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.unique_id)},
            name=runtime.device_name,
            manufacturer=runtime.profile.manufacturer,
            sw_version=runtime.identity.arm_version,
        )


class EnergyLibControl(EnergyLibEntity):
    _attr_entity_registry_enabled_default = False

    @property
    def available(self):
        runtime = self.coordinator.device
        return (
            super().available
            and runtime.allow_control
            and runtime.profile.controls_supported
            and runtime.controls.get(self.key) is not None
        )

    @property
    def value(self):
        return self.coordinator.device.controls.get(self.key)

    @property
    def extra_state_attributes(self):
        runtime = self.coordinator.device
        result = {"last_command": runtime.last_command}
        second = self.key + "_2"
        if second in runtime.controls:
            result["battery_2_value"] = runtime.controls[second]
            result["batteries_match"] = runtime.controls[second] == self.value
        return result


def controls_for(coordinator, platform):
    from .energylib_core.model.points import CONTROLS

    device = coordinator.device
    if not device.allow_control or not device.profile.controls_supported:
        return []
    return [
        c
        for c in CONTROLS
        if c.platform == platform
        and c.key in device.profile.control_keys
        and c.key not in ("remote_power", "remote_control")
    ]
