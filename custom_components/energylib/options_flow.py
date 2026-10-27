"""Draft-only native forms; only Save performs device writes."""

import copy

import voluptuous as vol
from homeassistant.config_entries import ConfigFlowResult, OptionsFlow
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector

from .energylib_core.model.tou import OUTSIDE_MODES, WEEKDAYS, TouSchedule
from .energylib_core.transport.modbus_tcp import EnergyLibError


class EnergyLibOptionsFlow(OptionsFlow):
    def __init__(self):
        self.draft = None
        self.saving_failed = False

    @property
    def coordinator(self):
        return self.config_entry.runtime_data

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        options = ["control"]
        runtime = self.coordinator.device
        if runtime.allow_control and "ctrl_tou" in runtime.profile.capabilities:
            options.append("tou")
        return self.async_show_menu(step_id="init", menu_options=options)

    async def async_step_control(self, user_input=None):
        errors = {}
        if user_input is not None:
            if (
                user_input["allow_control"]
                and not self.coordinator.device.profile.controls_supported
            ):
                errors["base"] = "control_unverified"
            else:
                return self.async_create_entry(
                    title="", data={**self.config_entry.options, **user_input}
                )
        return self.async_show_form(
            step_id="control",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "allow_control",
                        default=self.config_entry.options.get("allow_control", False),
                    ): bool
                }
            ),
        )

    async def async_step_tou(self, user_input=None):
        runtime = self.coordinator.device
        if not runtime.allow_control or "ctrl_tou" not in runtime.profile.capabilities:
            return self.async_abort(reason="control_unverified")
        if self.draft is None:
            try:
                async with runtime._lock:
                    await runtime._identity()
                    schedule = await runtime.profile.read_schedule(runtime.client)
                self.draft = copy.deepcopy(schedule.as_dict())
            except (EnergyLibError, ValueError):
                return self.async_abort(reason="cannot_connect")
        return self.async_show_menu(
            step_id="tou", menu_options=["add", "edit", "delete", "settings", "save"]
        )

    async def _period(self, step, user_input, index=None):
        errors = {}
        default = {} if index is None else self.draft["periods"][index]
        if user_input is not None:
            candidate = copy.deepcopy(self.draft)
            if index is None:
                candidate["periods"].append(user_input)
            else:
                candidate["periods"][index] = user_input
            try:
                TouSchedule.parse(candidate, self.coordinator.device.profile.tou_limits)
                self.draft = candidate
                return await self.async_step_tou()
            except ValueError:
                errors["base"] = "invalid_schedule"

        def field(name, fallback):
            return vol.Required(name, default=default.get(name, fallback))

        return self.async_show_form(
            step_id=step,
            errors=errors,
            data_schema=vol.Schema(
                {
                    field("type", "charge"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=["charge", "discharge"],
                            mode=selector.SelectSelectorMode.LIST,
                            translation_key="period_type",
                        )
                    ),
                    field("start", "01:00"): selector.TimeSelector(),
                    field("end", "06:00"): selector.TimeSelector(),
                    field("power", 1): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=self.coordinator.device.profile.max_power_kw,
                            step=0.1,
                            unit_of_measurement="kW",
                        )
                    ),
                }
            ),
        )

    async def async_step_add(self, user_input=None):
        return await self._period("add", self._normalize_time(user_input))

    @staticmethod
    def _normalize_time(data):
        if data is None:
            return None
        result = dict(data)
        for key in ("start", "end"):
            value = result[key]
            if len(value) == 8 and value[5:] == ":00":
                result[key] = value[:5]
        return result

    async def _select_period(self, step, user_input):
        if not self.draft["periods"]:
            return await self.async_step_tou()
        if user_input is not None:
            self.edit_index = int(user_input["period"])
            if step == "delete":
                self.draft["periods"].pop(self.edit_index)
                return await self.async_step_tou()
            return await self.async_step_edit_period()
        choices = {
            str(i): f"{self._type_label(p)} {p['start']}–{p['end']} ({p['power']} kW)"
            for i, p in enumerate(self.draft["periods"])
        }
        return self.async_show_form(
            step_id=step, data_schema=vol.Schema({vol.Required("period"): vol.In(choices)})
        )

    def _type_label(self, period):
        # Labels built in code; the form fields themselves use selector translations.
        labels = {"charge": "充电", "discharge": "放电"}
        if not self.hass.config.language.startswith("zh"):
            labels = {"charge": "Charge", "discharge": "Discharge"}
        return labels[period["type"]]

    async def async_step_edit(self, user_input=None):
        return await self._select_period("edit", user_input)

    async def async_step_edit_period(self, user_input=None):
        return await self._period("edit_period", self._normalize_time(user_input), self.edit_index)

    async def async_step_delete(self, user_input=None):
        return await self._select_period("delete", user_input)

    async def async_step_settings(self, user_input=None):
        errors = {}
        if user_input is not None:
            candidate = {**self.draft, **user_input}
            try:
                TouSchedule.parse(candidate, self.coordinator.device.profile.tou_limits)
                self.draft = candidate
                return await self.async_step_tou()
            except ValueError:
                errors["base"] = "invalid_schedule"
        return self.async_show_form(
            step_id="settings",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "weekdays", default=self.draft["weekdays"]
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=list(WEEKDAYS), multiple=True, translation_key="weekday"
                        )
                    ),
                    vol.Required(
                        "outside_mode", default=self.draft.get("outside_mode", "self_consumption")
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=list(OUTSIDE_MODES), translation_key="outside_mode"
                        )
                    ),
                }
            ),
        )

    async def async_step_save(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                await self.coordinator.async_command(
                    "schedule", self.draft, activate=user_input["activate"]
                )
                return self.async_create_entry(title="", data=dict(self.config_entry.options))
            except (EnergyLibError, ValueError, HomeAssistantError):
                self.saving_failed = True
                errors["base"] = "command_failed"
        if self.saving_failed:
            # A retry must start from authoritative device data, not the old draft.
            return self.async_abort(reason="command_failed")
        preview = (
            "\n".join(
                f"{self._type_label(p)} {p['start']}–{p['end']} {p['power']} kW"
                for p in self.draft["periods"]
            )
            or "—"
        )
        return self.async_show_form(
            step_id="save",
            errors=errors,
            description_placeholders={"preview": preview},
            data_schema=vol.Schema({vol.Required("activate", default=False): bool}),
        )
