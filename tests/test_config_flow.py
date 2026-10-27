"""Flow contract tests with a minimal HA API double, not HA runtime tests."""

import ast
import importlib
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

import voluptuous as vol
from energylib_under_test.protocol.device import DeviceIdentity
from energylib_under_test.protocol.modbus import CannotConnect

from tools.discovery_advertisement import txt_properties


class AbortFlow(Exception):
    def __init__(self, reason):
        self.reason = reason


class ConfigFlowDouble:
    def __init_subclass__(cls, **kwargs):
        pass

    def __new__(cls):
        instance = super().__new__(cls)
        instance.context = {}
        instance.entries = []
        instance.other_flow_id = None
        instance.other_logger = None
        instance.updates = []

        def update(entry, *, data):
            entry.data = data
            instance.updates.append(entry)

        instance.hass = SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=update))
        return instance

    async def async_set_unique_id(self, unique_id):
        self.unique_id = unique_id
        if self.other_flow_id == unique_id:
            raise AbortFlow("already_in_progress")

    def _async_current_entries(self):
        return self.entries

    def _async_in_progress(self, **kwargs):
        if kwargs["match_context"].get("energylib_logger_sn") == self.other_logger:
            return [{"flow_id": "other"}]
        return []

    def _abort_if_unique_id_configured(self, updates=None, reload_on_update=True):
        for entry in self.entries:
            if entry.unique_id == self.unique_id:
                entry.data.update(updates or {})
                raise AbortFlow("already_configured")

    def add_suggested_values_to_schema(self, schema, suggested):
        return schema

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}

    def async_abort(self, **kwargs):
        return {"type": "abort", **kwargs}

    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", "unique_id": self.unique_id, **kwargs}


class SectionDouble:
    """Mirror HA's data_entry_flow.section: a validator wrapping a sub-schema."""

    def __init__(self, schema, options=None):
        self.schema, self.options = schema, options or {}

    def __call__(self, value):
        return self.schema(value)


class NumberSelectorDouble:
    """Match HA's float-returning number selector; actual UI is checked in HA."""

    def __init__(self, config):
        self.config = config

    def __call__(self, value):
        return vol.All(
            vol.Coerce(float), vol.Range(min=self.config["min"], max=self.config["max"])
        )(value)


def load_flow_module():
    modules = {
        name: ModuleType(name)
        for name in [
            "homeassistant",
            "homeassistant.helpers",
            "homeassistant.helpers.selector",
            "homeassistant.helpers.service_info",
            "homeassistant.helpers.service_info.zeroconf",
            "homeassistant.config_entries",
            "homeassistant.const",
            "homeassistant.data_entry_flow",
        ]
    }
    modules["homeassistant.data_entry_flow"].section = SectionDouble
    modules["homeassistant.helpers.service_info.zeroconf"].ZeroconfServiceInfo = SimpleNamespace
    modules["homeassistant.config_entries"].ConfigFlow = ConfigFlowDouble
    modules["homeassistant.config_entries"].ConfigFlowResult = dict
    modules["homeassistant.const"].CONF_HOST = "host"
    modules["homeassistant.const"].CONF_PORT = "port"
    modules["homeassistant.helpers.selector"].NumberSelector = NumberSelectorDouble
    modules["homeassistant.helpers.selector"].NumberSelectorConfig = dict
    with patch.dict(sys.modules, modules):
        return importlib.import_module("energylib_under_test.config_flow")


class ConfigFlowTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_flow_module()

    def setUp(self):
        self.flow = self.module.EnergyLibConfigFlow()
        self.identity = DeviceIdentity("TEST-001", 1, "100.1", "101.2")
        self.probe = AsyncMock(return_value=self.identity)
        self.patch = patch.object(self.module, "async_probe", self.probe)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def discovery(self, **properties):
        return SimpleNamespace(
            host="192.0.2.2", port=1502, properties={**txt_properties("LOGGER-001"), **properties}
        )

    async def test_discovery_requires_confirmation_and_uses_verified_identity(self):
        form = await self.flow.async_step_zeroconf(self.discovery(id="TEST-001"))
        self.assertEqual(form["type"], "form")
        self.assertEqual(form["step_id"], "discovery_confirm")
        result = await self.flow.async_step_discovery_confirm(
            {
                "host": "192.0.2.2",
                "port": 1502,
                "unit_id": 1,
            }
        )
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["unique_id"], "energylib_TEST-001")
        self.assertEqual(result["data"]["profile_id"], "sinexcel_v2")
        self.assertEqual(result["data"]["profile_version"], 1)
        self.assertEqual(result["data"]["profile"], "v001b031_single")
        self.assertEqual(result["data"]["logger"]["sn"], "LOGGER-001")
        self.assertEqual(result["data"]["logger"]["source"], "zeroconf")
        self.assertEqual(result["options"], {"allow_control": True})

    async def test_discovery_card_and_confirmation_identify_the_logger(self):
        form = await self.flow.async_step_zeroconf(self.discovery(model="WF_ETH", fw="400.3.3"))
        self.assertEqual(
            self.flow.context["title_placeholders"], {"name": "EnergyLIB · LOGGER-001"}
        )
        self.assertEqual(
            form["description_placeholders"],
            {
                "logger_sn": "LOGGER-001",
                "logger_model": r"WF\_ETH",
                "logger_firmware": "400.3.3",
            },
        )
        self.assertNotIn(self.identity.serial, str(form["description_placeholders"]))

    async def test_logger_details_survive_display_and_failed_confirmation(self):
        self.probe.side_effect = CannotConnect("offline")
        initial = await self.flow.async_step_zeroconf(self.discovery())
        for values in (
            None,
            {"host": "192.0.2.2", "port": 8888, "unit_id": 7},
            {"host": " ", "port": 8888, "unit_id": 7},
        ):
            form = await self.flow.async_step_discovery_confirm(values)
            self.assertEqual(form["type"], "form")
            self.assertEqual(form["description_placeholders"], initial["description_placeholders"])
        self.assertEqual(form["errors"], {"base": "cannot_connect"})
        self.assertEqual(self.flow.updates, [])

    async def test_discovery_metadata_is_displayed_as_text_not_markdown(self):
        form = await self.flow.async_step_zeroconf(
            self.discovery(sn="<LOGGER>", model="[model](url)", fw="*1.0*`build`")
        )
        self.assertEqual(
            form["description_placeholders"],
            {
                "logger_sn": r"\<LOGGER\>",
                "logger_model": r"\[model\](url)",
                "logger_firmware": r"\*1.0\*\`build\`",
            },
        )

    async def test_manual_form_does_not_claim_advertised_logger_metadata(self):
        form = await self.flow.async_step_user()
        self.assertEqual(form["description_placeholders"], {})
        self.assertNotIn("title_placeholders", self.flow.context)

    async def test_verified_dhcp_change_updates_entry_without_new_identity(self):
        entry = SimpleNamespace(
            unique_id="energylib_TEST-001",
            data={
                "host": "192.0.2.1",
                "port": 2502,
                "unit_id": 7,
                "logger": txt_properties("LOGGER-001"),
            },
        )
        self.flow.entries = [entry]
        with self.assertRaises(AbortFlow) as caught:
            await self.flow.async_step_zeroconf(self.discovery(id="TEST-001"))
        self.assertEqual(caught.exception.reason, "already_configured")
        self.assertEqual(entry.unique_id, "energylib_TEST-001")
        self.assertEqual(entry.data["host"], "192.0.2.2")
        self.assertEqual(entry.data["port"], 1502)
        self.assertEqual(entry.data["unit_id"], 7)
        self.assertEqual(self.probe.call_args.args[0]["unit_id"], 7)

    async def test_entry_with_removed_profile_does_not_break_discovery(self):
        # A pre-0.5.0.dev6 "energylib_lab" entry: unknown profiles are skipped and
        # the device is offered again as a normal Sinexcel discovery.
        self.flow.entries = [
            SimpleNamespace(
                unique_id="energylib_lab_TEST-001",
                data={
                    "host": "127.0.0.1",
                    "port": 15021,
                    "unit_id": 1,
                    "profile_id": "energylib_lab",
                },
            )
        ]
        form = await self.flow.async_step_zeroconf(self.discovery(id="TEST-001"))
        self.assertEqual(form["step_id"], "discovery_confirm")
        self.assertEqual(self.probe.call_args.args[0]["profile_id"], "sinexcel_v2")

    async def test_form_has_no_profile_choice(self):
        schema = (await self.flow.async_step_user())["data_schema"]
        self.assertEqual({str(k) for k in schema.schema}, {"host", "port", "unit_id"})
        result = await self.flow.async_step_user({"host": "192.0.2.1", "port": 1502, "unit_id": 1})
        self.assertEqual(result["data"]["profile_id"], "sinexcel_v2")

    async def test_same_logger_new_inverter_does_not_overwrite_old_entry(self):
        entry = SimpleNamespace(
            unique_id="CLAIMED-ID",
            data={
                "host": "192.0.2.1",
                "port": 1502,
                "unit_id": 1,
                "logger": txt_properties("LOGGER-001"),
            },
        )
        self.flow.entries = [entry]
        result = await self.flow.async_step_zeroconf(self.discovery(id="CLAIMED-ID"))
        self.assertEqual(result["type"], "form")
        self.assertEqual(self.flow.unique_id, "energylib_TEST-001")
        self.assertEqual(entry.data["host"], "192.0.2.1")

    async def test_repeated_discovery_does_not_open_duplicate_flow(self):
        self.flow.other_logger = "LOGGER-001"
        result = await self.flow.async_step_zeroconf(self.discovery())
        self.assertEqual(result["reason"], "already_in_progress")
        self.probe.assert_not_awaited()

    async def test_unreachable_announcement_is_still_deduplicated(self):
        self.probe.side_effect = CannotConnect("Test")
        self.flow.other_logger = "LOGGER-001"
        result = await self.flow.async_step_zeroconf(self.discovery())
        self.assertEqual(result["reason"], "already_in_progress")
        self.probe.assert_not_awaited()

    def test_flow_never_reads_deprecated_advanced_mode(self):
        # HA 2026.x deprecated show_advanced_options; it always returns True.
        source = Path(self.module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        names |= {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)}
        self.assertNotIn("show_advanced_options", names)

    async def test_unreachable_discovery_can_be_corrected_without_creating_entry(self):
        self.probe.side_effect = CannotConnect("Test")
        result = await self.flow.async_step_zeroconf(self.discovery())
        self.assertEqual(result["errors"], {"base": "cannot_connect"})
        self.probe.side_effect = None
        result = await self.flow.async_step_discovery_confirm(
            {
                "host": "192.0.2.2",
                "port": 1502,
                "unit_id": 7,
            }
        )
        self.assertEqual(result["type"], "create_entry")

    async def test_unsupported_service_aborts_before_network_access(self):
        result = await self.flow.async_step_zeroconf(self.discovery(transport="http"))
        self.assertEqual(result["reason"], "unsupported_discovery")
        self.probe.assert_not_awaited()

    async def test_manual_input_defaults_to_502_and_forbids_broadcast(self):
        result = await self.flow.async_step_user()
        schema = result["data_schema"]
        data = schema({"host": "192.0.2.1"})
        self.assertEqual(data["port"], 502)
        self.assertEqual(data["unit_id"], 1)
        for data in [
            {"host": "192.0.2.1", "port": 1502, "unit_id": 0},
            {"host": "192.0.2.1", "port": 0},
            {"host": "192.0.2.1", "port": 65536},
        ]:
            with self.assertRaises(vol.Invalid):
                schema(data)
        data = schema({"host": "192.0.2.1", "port": 1502})
        self.assertEqual(data["port"], 1502)
        self.assertEqual(data["unit_id"], 1)

    async def test_discovery_form_defaults_to_advertised_port(self):
        result = await self.flow.async_step_zeroconf(self.discovery(id="TEST-001"))
        data = result["data_schema"]({"host": "192.0.2.2"})
        self.assertEqual(data["port"], 1502)

    async def test_failed_submission_keeps_custom_port(self):
        self.probe.side_effect = CannotConnect("Test")
        result = await self.flow.async_step_user({"host": "192.0.2.1", "port": 15021, "unit_id": 1})
        self.assertEqual(result["errors"], {"base": "cannot_connect"})
        data = result["data_schema"]({"host": "192.0.2.1"})
        self.assertEqual(data["port"], 15021)

    async def test_unit_id_is_an_integer_even_with_float_returning_selector(self):
        schema = (await self.flow.async_step_user())["data_schema"]
        for unit in (1, 7.0, "255"):
            data = schema({"host": "192.0.2.1", "unit_id": unit})
            self.assertIs(type(data["unit_id"]), int)
            self.assertEqual(data["unit_id"], int(unit))
        for unit in (0, 256, 1.5, "7.1"):
            with self.assertRaises(vol.Invalid):
                schema({"host": "192.0.2.1", "unit_id": unit})

    async def test_retry_preserves_selected_unit_id(self):
        self.probe.side_effect = CannotConnect("Test")
        result = await self.flow.async_step_user({"host": "192.0.2.1", "port": 502, "unit_id": 7})
        data = result["data_schema"]({"host": "192.0.2.1"})
        self.assertEqual(data["unit_id"], 7)

    def existing(self, *, serial="TEST-001", unit=7, logger="LOGGER-001"):
        data = {
            "host": "192.0.2.1",
            "port": 2502,
            "unit_id": unit,
            "serial": serial,
            "profile_id": "sinexcel_v2",
            "profile_version": 1,
        }
        if logger:
            data["logger"] = {**txt_properties(logger, unit_id=None), "source": "zeroconf"}
        entry = SimpleNamespace(unique_id=f"energylib_{serial}", data=data)
        self.flow.entries = [entry]
        return entry

    async def test_advertised_unit_missing_or_present_and_user_override(self):
        for unit in (None, 7, 255):
            props = txt_properties("LOGGER-001", unit_id=unit)
            info = SimpleNamespace(host="192.0.2.2", port=1502, properties=props)
            result = await self.flow.async_step_zeroconf(info)
            self.assertEqual(result["type"], "form")
            self.assertEqual(self.probe.call_args.args[0]["unit_id"], unit or 1)
        result = await self.flow.async_step_discovery_confirm(
            {"host": "192.0.2.2", "port": 1502, "unit_id": 8}
        )
        self.assertEqual(result["data"]["unit_id"], 8)

    async def test_new_logger_preserves_verified_existing_inverter(self):
        entry = self.existing(logger="OLD-LOGGER")
        with self.assertRaises(AbortFlow):
            await self.flow.async_step_zeroconf(self.discovery(unit_id="9"))
        self.assertEqual(self.probe.await_count, 2)
        self.assertEqual(entry.data["unit_id"], 7)
        self.assertEqual(entry.data["logger"]["sn"], "LOGGER-001")
        self.assertEqual(entry.data["serial"], "TEST-001")

    async def test_saved_unit_verification_failure_does_not_update(self):
        entry = self.existing(logger=None)
        before = dict(entry.data)
        self.probe.side_effect = [self.identity, CannotConnect("saved unit offline")]
        result = await self.flow.async_step_zeroconf(self.discovery(unit_id="9"))
        self.assertEqual(result["errors"]["base"], "cannot_connect")
        self.assertEqual(entry.data, before)

    async def test_same_logger_unreachable_preserves_configuration(self):
        entry = self.existing()
        before = dict(entry.data)
        self.probe.side_effect = CannotConnect("offline")
        result = await self.flow.async_step_zeroconf(self.discovery())
        self.assertEqual(result["errors"]["base"], "cannot_connect")
        self.assertEqual(entry.data, before)
        self.assertEqual(self.flow.updates, [])

    async def test_unchanged_announcement_does_not_write(self):
        entry = self.existing(unit=1)
        entry.data.update(host="192.0.2.2", port=1502)
        with self.assertRaises(AbortFlow):
            await self.flow.async_step_zeroconf(self.discovery())
        self.assertEqual(self.flow.updates, [])

    async def test_metadata_refresh_preserves_other_entry_settings(self):
        entry = self.existing(unit=1)
        entry.data.update(host="192.0.2.2", port=1502, legacy_soc=True)
        with self.assertRaises(AbortFlow):
            await self.flow.async_step_zeroconf(self.discovery(fw="test-2.0"))
        self.assertEqual(entry.data["logger"]["fw"], "test-2.0")
        self.assertTrue(entry.data["legacy_soc"])
        self.assertEqual(len(self.flow.updates), 1)

    async def test_different_loggers_same_inverter_pending_flow_deduplicated(self):
        self.flow.other_flow_id = "energylib_TEST-001"
        with self.assertRaises(AbortFlow) as caught:
            await self.flow.async_step_zeroconf(self.discovery(sn="OTHER-LOGGER"))
        self.assertEqual(caught.exception.reason, "already_in_progress")

    async def test_three_loggers_and_inverters_have_separate_identities(self):
        for i in range(3):
            flow = self.module.EnergyLibConfigFlow()
            self.probe.return_value = DeviceIdentity(f"INVERTER-{i}", 1, "100.1", "101.2")
            await flow.async_step_zeroconf(self.discovery(sn=f"LOGGER-{i}"))
            result = await flow.async_step_discovery_confirm(
                {"host": "192.0.2.2", "port": 1502, "unit_id": 1}
            )
            self.assertEqual(result["unique_id"], f"energylib_INVERTER-{i}")
            self.assertEqual(result["data"]["logger"]["sn"], f"LOGGER-{i}")

    async def test_manual_correction_does_not_bind_unrelated_logger(self):
        await self.flow.async_step_zeroconf(self.discovery())
        result = await self.flow.async_step_discovery_confirm(
            {"host": "192.0.2.99", "port": 1502, "unit_id": 1}
        )
        self.assertNotIn("logger", result["data"])

    async def test_legacy_announcement_aborts_before_probe(self):
        info = SimpleNamespace(host="192.0.2.2", port=1502, properties={"id": "TEST-001"})
        result = await self.flow.async_step_zeroconf(info)
        self.assertEqual(result["reason"], "unsupported_discovery")
        self.probe.assert_not_awaited()

    async def test_ignored_entry_needs_no_connection_fields(self):
        entry = SimpleNamespace(unique_id="energylib_TEST-001", source="ignore", data={})
        self.flow.entries = [entry]
        with self.assertRaises(AbortFlow) as caught:
            await self.flow.async_step_zeroconf(self.discovery())
        self.assertEqual(caught.exception.reason, "already_configured")
        self.assertEqual(entry.data, {})
