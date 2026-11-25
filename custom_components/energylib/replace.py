"""Replace an inverter that went offline with a newly discovered one.

When a new inverter is discovered while an EnergyLIB inverter is offline, the
user is asked in Repairs whether the new one replaces it (it may also simply be
a second inverter; then the user ignores the question and confirms the
discovery card as usual). The same rule applies with one inverter or several.

Replacing keeps the home's history: the old entry is removed (not counted as
"the user removed it"), every entity of the new inverter is registered under
the old entity ID before the new entry starts, so the energy dashboard,
automations and long-term statistics carry on. The old entry's options
(control allowed or not) and the device's room and name are kept.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from .const import CONF_PROFILE_ID, CONF_SOURCE, DOMAIN, SOURCE_AECC_LOCAL
from .energylib_core.profiles import get_profile

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

ISSUE_PREFIX = "replace_"
SOURCE = "replace"
# Entries being removed as part of a replacement (not a user removal).
REPLACING = f"{DOMAIN}_replacing"


def issue_id(serial: str) -> str:
    return f"{ISSUE_PREFIX}{serial}"


def unique_id(data: dict[str, Any]) -> str:
    namespace = get_profile(data.get(CONF_PROFILE_ID, "sinexcel_v2")).namespace
    return f"{namespace}_{data['serial']}"


def default_name(serial: str) -> str:
    """The device name the integration gives (see DeviceRuntime.device_name)."""
    return f"EnergyLIB {serial[-4:]}"


def _device(hass: HomeAssistant, entry_id: str, device_unique_id: str) -> dr.DeviceEntry | None:
    """The entry's inverter device (async_get_device is deprecated from HA 2026.11)."""
    for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry_id):
        if (DOMAIN, device_unique_id) in device.identifiers:
            return device
    return None


def label(hass: HomeAssistant, entry: ConfigEntry) -> str:
    device = _device(hass, entry.entry_id, entry.unique_id)
    if device is not None and (device.name_by_user or device.name):
        return device.name_by_user or device.name
    return default_name(entry.data.get("serial", ""))


def offline_entries(hass: HomeAssistant) -> list[ConfigEntry]:
    """EnergyLIB inverters that cannot be read right now.

    AECC devices are left out: a newly discovered EnergyLIB logger never
    replaces one of them.
    """
    found = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.source == "ignore" or entry.disabled_by is not None:
            continue
        if entry.data.get(CONF_SOURCE) == SOURCE_AECC_LOCAL:
            continue
        if entry.state is ConfigEntryState.SETUP_RETRY:
            found.append(entry)
        elif entry.state is ConfigEntryState.LOADED:
            coordinator = getattr(entry, "runtime_data", None)
            if coordinator is not None and not coordinator.last_update_success:
                found.append(entry)
    return found


def async_offer(hass: HomeAssistant, data: dict[str, Any]) -> bool:
    """Ask whether a newly discovered inverter replaces an offline one."""
    candidates = offline_entries(hass)
    if not candidates:
        return False
    serial = data["serial"]
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id(serial),
        is_fixable=True,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="replace_inverter",
        translation_placeholders={
            "new": default_name(serial),
            "old": ", ".join(label(hass, entry) for entry in candidates),
        },
        # Issue data holds simple values; the entry data is kept as JSON text.
        data={"new": json.dumps(data)},
    )
    return True


async def async_replace(
    hass: HomeAssistant, old: ConfigEntry, new_data: dict[str, Any]
) -> ConfigEntry | None:
    """Remove the old inverter and add the new one under the old entity IDs."""
    entities = er.async_get(hass)
    devices = dr.async_get(hass)
    prefix = f"{old.unique_id}_"
    kept = [
        entity
        for entity in er.async_entries_for_config_entry(entities, old.entry_id)
        if entity.platform == DOMAIN and entity.unique_id.startswith(prefix)
    ]
    device = _device(hass, old.entry_id, old.unique_id)
    area_id = device.area_id if device else None
    name_by_user = device.name_by_user if device else None
    options = dict(old.options)

    replacing = hass.data.setdefault(REPLACING, set())
    replacing.add(old.entry_id)
    try:
        await hass.config_entries.async_remove(old.entry_id)
    finally:
        replacing.discard(old.entry_id)

    new_prefix = f"{unique_id(new_data)}_"
    for entity in kept:
        created = entities.async_get_or_create(
            entity.domain,
            DOMAIN,
            new_prefix + entity.unique_id[len(prefix) :],
            suggested_object_id=entity.entity_id.split(".", 1)[1],
            disabled_by=entity.disabled_by,
            hidden_by=entity.hidden_by,
        )
        if created.entity_id != entity.entity_id:
            _LOGGER.warning(
                "Replacement: %s could not keep %s (now %s)",
                entity.unique_id[len(prefix) :],
                entity.entity_id,
                created.entity_id,
            )
        if entity.name or entity.icon or entity.area_id:
            entities.async_update_entity(
                created.entity_id, name=entity.name, icon=entity.icon, area_id=entity.area_id
            )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE}, data={"data": new_data, "options": options}
    )
    new = result.get("result")
    ir.async_delete_issue(hass, DOMAIN, issue_id(new_data["serial"]))
    if new is None:
        # The old entry is gone; the discovery card can still add the new one.
        _LOGGER.error("Replacement: the new inverter was not added (%s)", result.get("reason"))
        return None
    if new.state is ConfigEntryState.LOADED:
        # Entities the new inverter does not have were never claimed.
        for entity in list(entities.entities.values()):
            if (
                entity.platform == DOMAIN
                and entity.config_entry_id is None
                and entity.unique_id.startswith(new_prefix)
            ):
                entities.async_remove(entity.entity_id)
    new_device = _device(hass, new.entry_id, unique_id(new_data))
    if new_device is not None and (area_id or name_by_user):
        devices.async_update_device(new_device.id, area_id=area_id, name_by_user=name_by_user)
    _LOGGER.info("Inverter %s replaced by %s", old.unique_id, unique_id(new_data))
    return new
