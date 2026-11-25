"""Add the home's only EnergyLIB inverter without asking, read-only until confirmed.

A discovered logger is added automatically only when it is the sole EnergyLIB
logger announcing itself, no EnergyLIB entry exists (ignored ones included)
and the user has never removed this inverter (by serial). The entry starts without
control; a Repairs issue asks the user to confirm it is their inverter before
controls are allowed. Discovery alone never enables control.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

from .const import CONF_ALLOW_CONTROL, DOMAIN

CONF_AUTO_ADDED = "auto_added"
# Loggers announce within seconds of each other; wait before deciding one is alone.
SETTLE_SECONDS = 10.0
_STORE_KEY = f"{DOMAIN}.auto_add"


def _store(hass: HomeAssistant) -> Store:
    return Store(hass, 1, _STORE_KEY)


async def async_allowed(hass: HomeAssistant, serial: str) -> bool:
    """False for an inverter the user removed: do not add that one back.

    Only the removed serial is remembered, so a replacement inverter is still
    added (read-only). The store written before 0.8.44 only said "something was
    removed" without the serial; it keeps blocking every inverter as before.
    """
    data = await _store(hass).async_load() or {}
    if data.get("user_removed"):
        return False
    return serial not in data.get("removed_serials", [])


async def async_note_removal(hass: HomeAssistant, serial: str | None) -> None:
    store = _store(hass)
    data = await store.async_load() or {}
    if data.get("user_removed") or not serial:
        # Without a serial only the old, blanket rule can be kept.
        await store.async_save({"user_removed": True})
        return
    removed = [s for s in data.get("removed_serials", []) if s != serial]
    await store.async_save({"removed_serials": [*removed, serial]})


def control_issue_id(entry: ConfigEntry) -> str:
    return f"confirm_control_{entry.entry_id}"


def async_update_control_issue(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Ask for confirmation while an automatically added entry has no control."""
    if entry.options.get(CONF_AUTO_ADDED) and not entry.options.get(CONF_ALLOW_CONTROL):
        ir.async_create_issue(
            hass,
            DOMAIN,
            control_issue_id(entry),
            is_fixable=True,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="confirm_control",
            translation_placeholders={
                "serial": entry.data.get("serial", ""),
                "host": entry.data.get("host", ""),
            },
            data={"entry_id": entry.entry_id},
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, control_issue_id(entry))
