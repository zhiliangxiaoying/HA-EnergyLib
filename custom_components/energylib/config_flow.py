"""Native manual and Zeroconf configuration for one inverter per entry."""

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers import selector
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .const import (
    CONF_PROFILE,
    CONF_PROFILE_ID,
    CONF_UNIT_ID,
    DEFAULT_PORT,
    DEFAULT_UNIT_ID,
    DOMAIN,
    PROFILE,
)
from .energylib_core.profiles import get_profile
from .protocol.device import (
    DeviceIdentity,
    IdentityMismatch,
    UnsupportedDevice,
)
from .protocol.discovery import Discovery, parse_discovery
from .protocol.modbus import EnergyLibError, ModbusTcpClient


async def async_probe(data: dict[str, Any]) -> DeviceIdentity:
    """Identify without keeping an unused connection alive."""
    client = ModbusTcpClient(data[CONF_HOST], data[CONF_PORT], data[CONF_UNIT_ID])
    try:
        return await get_profile(data.get(CONF_PROFILE_ID, "sinexcel_v2")).identify(client)
    finally:
        await client.close()


class EnergyLibConfigFlow(ConfigFlow, domain=DOMAIN):
    """Validate business-interface identity before creating/updating an entry."""

    VERSION = 2

    @staticmethod
    def async_get_options_flow(config_entry):
        from .options_flow import EnergyLibOptionsFlow

        return EnergyLibOptionsFlow()

    def __init__(self) -> None:
        self._discovery: Discovery | None = None
        self._data: dict[str, Any] = {}

    def _schema(self, suggested: dict[str, Any]) -> vol.Schema:
        # One protocol profile (Sinexcel V001B031) for real and simulated devices,
        # so the user only supplies the connection. HA's deprecated advanced mode
        # (show_advanced_options, always True since 2026.x) is not used.
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): vol.All(str, vol.Length(min=1)),
                # A form convenience, not a guarantee about the device endpoint.
                # Discovery and failed submissions keep their actual/chosen port.
                vol.Required(CONF_PORT, default=suggested.get(CONF_PORT, DEFAULT_PORT)): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=65535)
                ),
                vol.Required(
                    CONF_UNIT_ID, default=suggested.get(CONF_UNIT_ID, DEFAULT_UNIT_ID)
                ): vol.All(
                    selector.NumberSelector(
                        selector.NumberSelectorConfig(min=1, max=255, step=1, mode="box")
                    ),
                    # The selector returns float even with step=1. Reject fractional
                    # addresses before converting to the integer required by Modbus.
                    vol.In(range(1, 256)),
                    vol.Coerce(int),
                ),
            }
        )
        return self.add_suggested_values_to_schema(schema, suggested)

    async def _validate(self, data: dict[str, Any], *, automatic=False) -> DeviceIdentity:
        if data.get(CONF_PROFILE) != PROFILE:
            raise UnsupportedDevice("Unsupported profile")
        identity = await async_probe(data)
        existing = self._existing_entry(identity.serial)
        if (
            automatic
            and existing
            and getattr(existing, "source", None) != "ignore"
            and data[CONF_UNIT_ID] != existing.data.get(CONF_UNIT_ID, data[CONF_UNIT_ID])
        ):
            # A new logger may lead to a manually configured inverter. Preserve
            # its saved address only after checking it on the new endpoint too.
            expected = identity.serial
            data[CONF_UNIT_ID] = existing.data[CONF_UNIT_ID]
            identity = await async_probe(data)
            if identity.serial != expected:
                raise IdentityMismatch("Saved unit ID resolves to a different inverter")
        profile = get_profile(data.get(CONF_PROFILE_ID, "sinexcel_v2"))
        data.update(profile_id=profile.id, profile_version=profile.version, serial=identity.serial)
        if self._discovery and (data[CONF_HOST], data[CONF_PORT]) == (
            self._discovery.host,
            self._discovery.port,
        ):
            data["logger"] = self._discovery.metadata()
        await self.async_set_unique_id(f"{profile.namespace}_{identity.serial}")
        if existing and getattr(existing, "source", None) != "ignore":
            updates = {
                CONF_HOST: data[CONF_HOST],
                CONF_PORT: data[CONF_PORT],
                CONF_UNIT_ID: data[CONF_UNIT_ID],
            }
            if "logger" in data:
                updates["logger"] = data["logger"]
            elif (existing.data[CONF_HOST], existing.data[CONF_PORT]) != (
                data[CONF_HOST],
                data[CONF_PORT],
            ):
                # A manually corrected endpoint has no verified logger binding.
                updates["logger"] = None
            merged = {**existing.data, **updates}
            if merged != existing.data:
                self.hass.config_entries.async_update_entry(existing, data=merged)
        # The integration's update listener owns reloads, not the flow as well.
        self._abort_if_unique_id_configured(reload_on_update=False)
        return identity

    def _logger_entry(self, logger_sn: str):
        candidates = [
            entry
            for entry in self._async_current_entries()
            if (entry.data.get("logger") or {}).get("sn") == logger_sn
            and entry.data.get(CONF_PROFILE_ID, "sinexcel_v2") == "sinexcel_v2"
        ]
        # A logger reused with a different inverter may have historical bindings.
        # Ambiguous metadata never decides which inverter to update.
        return candidates[0] if len(candidates) == 1 else None

    def _existing_entry(self, serial: str | None):
        """Entry for this serial under any profile (v2 stores the verified serial)."""
        if serial is None:
            return None
        for entry in self._async_current_entries():
            profile_id = entry.data.get(CONF_PROFILE_ID, "sinexcel_v2")
            try:
                namespace = get_profile(profile_id).namespace
            except ValueError:
                continue
            if entry.unique_id in (serial, f"{namespace}_{serial}"):
                return entry
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._async_configure("user", user_input)

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        try:
            self._discovery = parse_discovery(
                discovery_info.host, discovery_info.port, discovery_info.properties
            )
        except (TypeError, ValueError):
            return self.async_abort(reason="unsupported_discovery")
        logger_sn = self._discovery.logger_sn
        self.context["energylib_logger_sn"] = logger_sn
        if self._async_in_progress(
            include_uninitialized=True, match_context={"energylib_logger_sn": logger_sn}
        ):
            return self.async_abort(reason="already_in_progress")
        # A device the user already added keeps the profile and unit ID the user
        # chose; the advertisement itself never selects a profile.
        existing = self._logger_entry(logger_sn)
        profile_id = (
            existing.data.get(CONF_PROFILE_ID, "sinexcel_v2") if existing else "sinexcel_v2"
        )
        self._data = {
            CONF_HOST: self._discovery.host,
            CONF_PORT: self._discovery.port,
            CONF_UNIT_ID: existing.data[CONF_UNIT_ID]
            if existing
            else (self._discovery.unit_id or DEFAULT_UNIT_ID),
            CONF_PROFILE: PROFILE,
            CONF_PROFILE_ID: profile_id,
        }
        self.context["title_placeholders"] = {"name": "EnergyLIB"}
        errors = {}
        try:
            await self._validate(self._data, automatic=True)
        except IdentityMismatch:
            return self.async_abort(reason="identity_mismatch")
        except UnsupportedDevice:
            errors["base"] = "unsupported_device"
        except EnergyLibError:
            # Allow the user to adjust unit ID/port when the advertised endpoint
            # is not ready. No config entry is created on a failed probe.
            errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="discovery_confirm",
            data_schema=self._schema(self._data),
            errors=errors,
        )

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_configure("discovery_confirm", user_input)

    async def _async_configure(
        self, step: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            self._data = {
                **user_input,
                CONF_PROFILE_ID: self._data.get(CONF_PROFILE_ID, "sinexcel_v2"),
                CONF_PROFILE: PROFILE,
            }
            self._data[CONF_HOST] = self._data[CONF_HOST].strip()
            if not self._data[CONF_HOST]:
                return self.async_show_form(
                    step_id=step,
                    data_schema=self._schema(self._data),
                    errors={"base": "cannot_connect"},
                )
            try:
                await self._validate(self._data)
            except IdentityMismatch:
                errors["base"] = "identity_mismatch"
            except UnsupportedDevice:
                errors["base"] = "unsupported_device"
            except EnergyLibError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title="EnergyLIB", data=self._data)
        return self.async_show_form(
            step_id=step,
            data_schema=self._schema(self._data),
            errors=errors,
        )
