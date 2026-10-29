"""Repairs: confirm an automatically added inverter before allowing its controls."""

from typing import Any

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .auto_add import CONF_AUTO_ADDED
from .const import CONF_ALLOW_CONTROL


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    return ConfirmControlFlow((data or {}).get("entry_id"))


class ConfirmControlFlow(RepairsFlow):
    def __init__(self, entry_id: str | None) -> None:
        self._entry_id = entry_id

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            return self.async_abort(reason="entry_removed")
        if user_input is not None:
            options = {**entry.options, CONF_ALLOW_CONTROL: True}
            options.pop(CONF_AUTO_ADDED, None)
            # The entry reloads on option changes and then drops this issue.
            self.hass.config_entries.async_update_entry(entry, options=options)
            return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "serial": entry.data.get("serial", ""),
                "host": entry.data.get("host", ""),
            },
        )
