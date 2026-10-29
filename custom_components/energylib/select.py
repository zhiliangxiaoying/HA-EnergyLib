"""Separate writable mode selector; read-only sensor remains compatible."""

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.translation import async_get_translations

from .const import DOMAIN
from .entity import EnergyLibControl, controls_for

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    c = entry.runtime_data
    selects = controls_for(c, "select")
    entities = [EnergyLibSelect(c, d) for d in selects]
    if work_mode := next((d for d in selects if d.key == "work_mode"), None):
        # HomeKit names each option after its raw value, so Apple Home needs a
        # selector whose options are already words in HA's system language.
        strings = await async_get_translations(hass, hass.config.language, "entity", {DOMAIN})
        prefix = f"component.{DOMAIN}.entity.select.home_mode.state."
        labels = {
            mode: strings.get(prefix + mode, mode)
            for mode in c.device.control_options(work_mode, restoring=True)
        }
        entities.append(EnergyLibHomeMode(c, work_mode, labels))
    async_add_entities(entities)


class EnergyLibSelect(EnergyLibControl, SelectEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, definition):
        super().__init__(coordinator, definition.key)
        self._attr_translation_key = definition.key
        self._offered = list(coordinator.device.control_options(definition))
        self._encodable = coordinator.device.control_options(definition, restoring=True)

    @property
    def options(self):
        # A mode set from the App that this model does not offer is still shown.
        current = self.control_value
        if current not in self._offered and current in self._encodable:
            return [*self._offered, current]
        return self._offered

    @property
    def current_option(self):
        return self.control_value

    async def async_select_option(self, option):
        await self.coordinator.async_command(self.key, option)


class EnergyLibHomeMode(EnergyLibSelect):
    """The EMS mode for Apple Home and Siri: options are words, not keys.

    Not a configuration entity, so HomeKit bridges that include the select
    domain pick it up without listing entity IDs. Automations should keep
    using the key-based EMS mode select.
    """

    _attr_entity_category = None

    def __init__(self, coordinator, definition, labels):
        super().__init__(coordinator, definition)
        self._attr_unique_id = f"{coordinator.device.unique_id}_home_mode"
        self._attr_translation_key = "home_mode"
        self._labels = labels
        self._modes = {label: mode for mode, label in labels.items()}

    @property
    def options(self):
        return [self._labels.get(mode, mode) for mode in super().options]

    @property
    def current_option(self):
        mode = self.control_value
        return self._labels.get(mode, mode) if mode is not None else None

    async def async_select_option(self, option):
        await self.coordinator.async_command(self.key, self._modes.get(option, option))
