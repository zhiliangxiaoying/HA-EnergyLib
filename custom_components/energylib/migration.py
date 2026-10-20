"""Preflight all registry collisions before changing any v1 identity."""

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN


async def async_migrate_entry(hass, entry):
    if entry.version == 2:
        return entry.data.get("profile_version") == 1
    if entry.version != 1 or entry.data.get("profile") != "v001b031_single" or not entry.unique_id:
        return False
    serial = entry.unique_id
    uid = f"energylib_{serial}"
    if any(
        e.entry_id != entry.entry_id and e.unique_id == uid
        for e in hass.config_entries.async_entries(DOMAIN)
    ):
        return False
    entities, devices = er.async_get(hass), dr.async_get(hass)
    updates = []
    for entity in er.async_entries_for_config_entry(entities, entry.entry_id):
        if not entity.unique_id.startswith(serial + "_"):
            return False
        new_id = f"energylib_{entity.unique_id}"
        conflict = entities.async_get_entity_id(entity.domain, DOMAIN, new_id)
        if conflict is not None and conflict != entity.entity_id:
            return False
        updates.append((entity, new_id))
    device_updates = []
    for device in dr.async_entries_for_config_entry(devices, entry.entry_id):
        if (DOMAIN, serial) not in device.identifiers:
            return False
        identifiers = (device.identifiers - {(DOMAIN, serial)}) | {(DOMAIN, uid)}
        # Identifiers are no longer unique across config entries (HA 2026.x):
        # any other device already holding the new identifier is a conflict.
        conflicts = devices.async_get_devices(identifiers={(DOMAIN, uid)})
        if any(other.id != device.id for other in conflicts):
            return False
        device_updates.append((device, identifiers))
    applied_entities, applied_devices = [], []
    try:
        for entity, new_id in updates:
            entities.async_update_entity(entity.entity_id, new_unique_id=new_id)
            applied_entities.append(entity)
        for device, identifiers in device_updates:
            devices.async_update_device(device.id, new_identifiers=identifiers)
            applied_devices.append(device)
        hass.config_entries.async_update_entry(
            entry,
            unique_id=uid,
            version=2,
            data={
                **entry.data,
                "profile_id": "sinexcel_v2",
                "profile_version": 1,
                "serial": serial,
                "legacy_soc": True,
            },
        )
    except Exception:
        for entity in applied_entities:
            entities.async_update_entity(entity.entity_id, new_unique_id=entity.unique_id)
        for device in applied_devices:
            devices.async_update_device(device.id, new_identifiers=device.identifiers)
        raise
    return True
