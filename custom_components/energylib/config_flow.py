"""Native manual and Zeroconf configuration for one inverter per entry."""

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .const import CONF_PROFILE, CONF_PROFILE_ID, CONF_UNIT_ID, DEFAULT_UNIT_ID, DOMAIN, PROFILE
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
                vol.Required(CONF_PORT): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
                vol.Required(CONF_UNIT_ID, default=DEFAULT_UNIT_ID): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=255)
                ),
            }
        )
        return self.add_suggested_values_to_schema(schema, suggested)

    async def _validate(self, data: dict[str, Any]) -> DeviceIdentity:
        if data.get(CONF_PROFILE) != PROFILE:
            raise UnsupportedDevice("Unsupported profile")
        identity = await async_probe(data)
        if (
            self._discovery is not None
            and self._discovery.advertised_id is not None
            and identity.serial != self._discovery.advertised_id
        ):
            raise IdentityMismatch("Advertised identity differs from device")
        profile = get_profile(data.get(CONF_PROFILE_ID, "sinexcel_v2"))
        data.update(profile_id=profile.id, profile_version=profile.version, serial=identity.serial)
        await self.async_set_unique_id(f"{profile.namespace}_{identity.serial}")
        # These details have been verified via reads, not merely trusted TXT.
        self._abort_if_unique_id_configured(
            updates={
                CONF_HOST: data[CONF_HOST],
                CONF_PORT: data[CONF_PORT],
                CONF_UNIT_ID: data[CONF_UNIT_ID],
            }
        )
        return identity

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
        advertised = self._discovery.advertised_id
        # A device the user already added keeps the profile and unit ID the user
        # chose; the advertisement itself never selects a profile.
        existing = self._existing_entry(advertised)
        profile_id = (
            existing.data.get(CONF_PROFILE_ID, "sinexcel_v2") if existing else "sinexcel_v2"
        )
        if advertised is not None:
            # Deduplicate repeated announcements even while the probe fails.
            # Only a verified identity may later update an existing entry.
            await self.async_set_unique_id(f"{get_profile(profile_id).namespace}_{advertised}")
        self._data = {
            CONF_HOST: self._discovery.host,
            CONF_PORT: self._discovery.port,
            CONF_UNIT_ID: existing.data[CONF_UNIT_ID] if existing else DEFAULT_UNIT_ID,
            CONF_PROFILE: PROFILE,
            CONF_PROFILE_ID: profile_id,
        }
        self.context["title_placeholders"] = {"name": "EnergyLIB"}
        errors = {}
        try:
            await self._validate(self._data)
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
            self._data = {**user_input, CONF_PROFILE_ID: "sinexcel_v2", CONF_PROFILE: PROFILE}
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
