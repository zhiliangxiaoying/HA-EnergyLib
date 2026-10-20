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
        return instance

    async def async_set_unique_id(self, unique_id):
        self.unique_id = unique_id
        if self.other_flow_id == unique_id:
            raise AbortFlow("already_in_progress")

    def _async_current_entries(self):
        return self.entries

    def _abort_if_unique_id_configured(self, updates=None):
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


def load_flow_module():
    modules = {
        name: ModuleType(name)
        for name in [
            "homeassistant",
            "homeassistant.helpers",
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
        return SimpleNamespace(host="192.0.2.2", port=1502, properties=properties)

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

    async def test_verified_dhcp_change_updates_entry_without_new_identity(self):
        entry = SimpleNamespace(
            unique_id="energylib_TEST-001",
            data={
                "host": "192.0.2.1",
                "port": 2502,
                "unit_id": 7,
            },
        )
        self.flow.entries = [entry]
        with self.assertRaises(AbortFlow) as caught:
            await self.flow.async_step_zeroconf(self.discovery(id="TEST-001"))
        self.assertEqual(caught.exception.reason, "already_configured")
        self.assertEqual(entry.unique_id, "energylib_TEST-001")
        self.assertEqual(entry.data, {"host": "192.0.2.2", "port": 1502, "unit_id": 7})
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

    async def test_mismatched_advertisement_cannot_update_existing_entry(self):
        entry = SimpleNamespace(
            unique_id="CLAIMED-ID",
            data={
                "host": "192.0.2.1",
                "port": 1502,
                "unit_id": 1,
            },
        )
        self.flow.entries = [entry]
        result = await self.flow.async_step_zeroconf(self.discovery(id="CLAIMED-ID"))
        self.assertEqual(result["reason"], "identity_mismatch")
        self.assertEqual(entry.data["host"], "192.0.2.1")

    async def test_repeated_discovery_does_not_open_duplicate_flow(self):
        self.flow.other_flow_id = "energylib_TEST-001"
        with self.assertRaises(AbortFlow) as caught:
            await self.flow.async_step_zeroconf(self.discovery())
        self.assertEqual(caught.exception.reason, "already_in_progress")

    async def test_unreachable_announcement_is_still_deduplicated(self):
        self.probe.side_effect = CannotConnect("Test")
        self.flow.other_flow_id = "energylib_TEST-001"
        with self.assertRaises(AbortFlow) as caught:
            await self.flow.async_step_zeroconf(self.discovery(id="TEST-001"))
        self.assertEqual(caught.exception.reason, "already_in_progress")
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

    async def test_manual_input_requires_port_and_forbids_broadcast(self):
        result = await self.flow.async_step_user()
        schema = result["data_schema"]
        for data in [{"host": "192.0.2.1"}, {"host": "192.0.2.1", "port": 1502, "unit_id": 0}]:
            with self.assertRaises(vol.Invalid):
                schema(data)
        data = schema({"host": "192.0.2.1", "port": 1502})
        self.assertEqual(data["unit_id"], 1)
