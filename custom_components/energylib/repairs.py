"""Repairs: confirm an automatically added inverter; replace an offline one."""

import json
from typing import Any

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from . import replace
from .auto_add import CONF_AUTO_ADDED
from .const import CONF_ALLOW_CONTROL, DOMAIN


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    if issue_id.startswith(replace.ISSUE_PREFIX):
        return ReplaceFlow(json.loads((data or {}).get("new") or "{}"))
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


class ReplaceFlow(RepairsFlow):
    """ "Was this inverter replaced?" Keeps the old one's history and settings."""

    def __init__(self, new_data: dict[str, Any]) -> None:
        self._new = new_data

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        serial = self._new.get("serial")
        if not serial:
            return self.async_abort(reason="entry_removed")
        new_id = replace.unique_id(self._new)
        if any(e.unique_id == new_id for e in self.hass.config_entries.async_entries(DOMAIN)):
            return self.async_abort(reason="already_added")
        choices = {
            e.entry_id: replace.label(self.hass, e) for e in replace.offline_entries(self.hass)
        }
        if not choices:
            return self.async_abort(reason="old_online")
        if user_input is not None:
            old_id = user_input.get("old", next(iter(choices)))
            old = self.hass.config_entries.async_get_entry(old_id)
            if old is None or old_id not in choices:
                return self.async_abort(reason="old_online")
            await replace.async_replace(self.hass, old, self._new)
            return self.async_create_entry(data={})
        schema = (
            vol.Schema({vol.Required("old", default=next(iter(choices))): vol.In(choices)})
            if len(choices) > 1
            else vol.Schema({})
        )
        return self.async_show_form(
            step_id="confirm",
            data_schema=schema,
            description_placeholders={
                "new": replace.default_name(serial),
                "old": ", ".join(choices.values()),
            },
        )
