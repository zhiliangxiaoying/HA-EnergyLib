"""Device-targeted actions share exactly the same command path as entity controls."""

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN


def async_register_services(hass):
    if hass.services.has_service(DOMAIN, "set_battery_power"):
        return

    def coordinator(device_id):
        device = dr.async_get(hass).async_get(device_id)
        # HA 2026.10 gives each device one entry (config_entry_id) and deprecates the set.
        if device is None:
            entry_ids = []
        elif (entry_id := getattr(device, "config_entry_id", None)) is not None:
            entry_ids = [entry_id]
        else:
            entry_ids = list(device.config_entries)
        entries = [hass.config_entries.async_get_entry(i) for i in entry_ids]
        entries = [
            e
            for e in entries
            if e is not None
            and e.domain == DOMAIN
            and e.state is ConfigEntryState.LOADED
            and getattr(e, "runtime_data", None)
        ]
        if len(entries) != 1:
            raise HomeAssistantError("Select one loaded EnergyLIB device")
        c = entries[0].runtime_data
        if (DOMAIN, c.device.unique_id) not in device.identifiers:
            raise HomeAssistantError("Device identity does not match runtime")
        return c

    async def power(call):
        c = coordinator(call.data["device_id"])
        await c.async_command("power", call.data["power_kw"], duration=call.data["duration"])

    async def parameters(call):
        await coordinator(call.data["device_id"]).async_set_remote_parameters(
            call.data["power_kw"], call.data["duration_minutes"]
        )

    async def access(call):
        await coordinator(call.data["device_id"]).async_set_remote_enabled(call.data["enabled"])

    async def schedule(call):
        c = coordinator(call.data["device_id"])
        data = {k: v for k, v in call.data.items() if k not in ("device_id", "activate")}
        await c.async_command("schedule", data, activate=call.data.get("activate", False))

    async def clear(call):
        c = coordinator(call.data["device_id"])
        await c.async_command("schedule", {"periods": []}, activate=False)

    async def stop(call):
        await coordinator(call.data["device_id"]).async_command("stop_remote")

    async def ems_mode(call):
        # The EMS selector shows the modes as words in HA's language (for Apple
        # Home); automations name the mode by its fixed key here instead.
        from .energylib_core.model.points import CONTROLS

        c = coordinator(call.data["device_id"])
        definition = next(d for d in CONTROLS if d.key == "work_mode")
        if call.data["mode"] not in c.device.control_options(definition):
            raise HomeAssistantError(f"EMS mode {call.data['mode']} is not offered by this device")
        await c.async_command("work_mode", call.data["mode"])

    hass.services.async_register(
        DOMAIN, "stop_battery_power", stop, schema=vol.Schema({vol.Required("device_id"): str})
    )
    hass.services.async_register(
        DOMAIN,
        "set_ems_mode",
        ems_mode,
        schema=vol.Schema({vol.Required("device_id"): str, vol.Required("mode"): str}),
    )
    hass.services.async_register(
        DOMAIN,
        "set_remote_access",
        access,
        schema=vol.Schema({vol.Required("device_id"): str, vol.Required("enabled"): bool}),
    )
    hass.services.async_register(
        DOMAIN,
        "set_remote_parameters",
        parameters,
        schema=vol.Schema(
            {
                vol.Required("device_id"): str,
                vol.Required("power_kw"): vol.Any(int, float),
                vol.Required("duration_minutes"): vol.Any(int, float),
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        "set_battery_power",
        power,
        schema=vol.Schema(
            {
                vol.Required("device_id"): str,
                vol.Required("power_kw"): vol.Any(int, float),
                vol.Required("duration"): vol.Any(int, float),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "set_tou_schedule",
        schedule,
        schema=vol.Schema(
            {
                vol.Required("device_id"): str,
                vol.Required("periods"): [dict],
                vol.Optional("weekdays"): [str],
                vol.Optional("outside_mode"): str,
                vol.Optional("activate", default=False): bool,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN, "clear_tou_schedule", clear, schema=vol.Schema({vol.Required("device_id"): str})
    )
