"""Optional switch adapters, including local remote-operation permission."""

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory

from .entity import EnergyLibControl, EnergyLibEntity, controls_for

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    c = entry.runtime_data
    async_add_entities(EnergyLibSwitch(c, d) for d in controls_for(c, "switch"))
    if c.device.allow_control and c.device.profile.controls_supported:
        async_add_entities([EnergyLibRemoteAccess(c, "allow_remote_control")])


class EnergyLibRemoteAccess(EnergyLibEntity, SwitchEntity):
    """Local permission, independent of the inverter's active control mode."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False
    _attr_translation_key = "allow_remote_control"
    _attr_icon = "mdi:battery-lock"

    @property
    def available(self):
        return (
            self.coordinator.device.allow_control
            and self.coordinator.device.profile.controls_supported
        )

    @property
    def is_on(self):
        return self.coordinator.remote_enabled

    async def async_turn_on(self, **kwargs):
        await self.coordinator.async_set_remote_enabled(True)

    async def async_turn_off(self, **kwargs):
        await self.coordinator.async_set_remote_enabled(False)


class EnergyLibSwitch(EnergyLibControl, SwitchEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, definition):
        super().__init__(coordinator, definition.key)
        self._attr_translation_key = definition.key
        if definition.key == "inverter_power":
            self._attr_icon = "mdi:power"
            # A main control, not a setting: HomeKit bridges then include it
            # with the switch domain instead of by entity ID.
            self._attr_entity_category = None

    @property
    def is_on(self):
        return bool(self.control_value)

    async def async_turn_on(self, **kwargs):
        await self.coordinator.async_command(self.key, True)

    async def async_turn_off(self, **kwargs):
        await self.coordinator.async_command(self.key, False)
