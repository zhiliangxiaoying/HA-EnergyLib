"""TCP coverage for the Sinexcel even-start quirk, not a transport restriction."""

import asyncio
import unittest
from datetime import datetime, timezone

from energylib_under_test.energylib_core.profiles.sinexcel_v2 import Mapping
from energylib_under_test.energylib_core.runtime import DeviceRuntime
from energylib_under_test.energylib_core.transport.modbus_tcp import (
    DeviceException,
    InvalidResponse,
    ModbusTcpClient,
)

from tools.sinexcel_simulator import BenchModel, BenchServer


class Fc02AlignmentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.model = BenchModel()  # Writes disabled.
        self.model.tick(datetime(2026, 10, 22, 12, tzinfo=timezone.utc), 0)
        self.peer = BenchServer(self.model, {"127.0.0.1"}, fault="fc02_even_address")
        self.server = await asyncio.start_server(self.peer.handle, "127.0.0.1", 0)
        self.client = ModbusTcpClient(
            "127.0.0.1", self.server.sockets[0].getsockname()[1], 1, timeout=0.3
        )
        self.runtime = DeviceRuntime(self.client, "sinexcel_v2")
        await self.runtime.initialize()
        self.profile = self.runtime.profile
        self.peer.read_log.clear()

    async def asyncTearDown(self):
        self.peer.fault = "normal"
        await self.runtime.close()
        self.server.close()
        await self.server.wait_closed()
        await self.peer.close()
        self.assertEqual(self.peer.write_count, 0)

    async def test_running_states_keep_original_bit_offsets(self):
        # A leading 1 must be discarded, not shifted into the logical enum.
        self.model.bits[1][0x1000] = True
        for code, name in enumerate(("standby", "ready", "off_grid", "on_grid")):
            with self.subTest(code=code):
                for bit in range(3):
                    self.model.bits[1][0x1001 + bit] = bool(code & (1 << bit))
                values = await self.runtime.refresh({"running_state"}, force=True)
                self.assertEqual(values["running_state"], name)
        bit_reads = [r for r in self.peer.read_log if r[0] == 2]
        self.assertEqual(bit_reads, [(2, 1, 0x1000, 4)] * 4)

    async def test_even_alarm_sources_and_alarm_ids_are_preserved(self):
        self.model.bits[1][0x104A] = True
        self.model.bits[2][0x1054] = True
        values = await self.runtime.refresh({"alarm_status"}, force=True)
        self.assertEqual(values["alarm_status"], "abnormal")
        alarms = self.runtime.attributes["alarm_status"]["alarms"]
        self.assertEqual(
            {a["alarm_id"] for a in alarms},
            {"sinexcel_v2:fc02:u1:104A", "sinexcel_v2:fc02:u2:1054"},
        )
        self.assertEqual(
            [r for r in self.peer.read_log if r[0] == 2],
            [(2, 1, 0x1048, 202), (2, 2, 0x1048, 202), (2, 1, 0x1014, 1)],
        )

    async def test_odd_start_returns_only_requested_bits_and_preserves_unit(self):
        self.model.bits[2][0x1048] = True  # Leading bit must not leak into result.
        self.model.bits[2][0x1049] = False
        self.model.bits[2][0x104A] = True
        bits = await self.profile.read_discrete_inputs(self.client, 0x1049, 2, unit_id=2)
        self.assertEqual(bits, [False, True])
        self.assertEqual(list(self.peer.read_log), [(2, 2, 0x1048, 3)])

    async def test_even_start_keeps_odd_quantity(self):
        self.model.bits[1][0x105A] = True
        bits = await self.profile.read_discrete_inputs(self.client, 0x105A, 1)
        self.assertEqual(bits, [True])
        self.assertEqual(list(self.peer.read_log), [(2, 1, 0x105A, 1)])

    async def test_alignment_padding_respects_block_limit(self):
        self.model.bits[1][0x1000] = True
        self.model.bits[1][0x10CA] = True
        expected = [self.model.bits[1][a] for a in range(0x1001, 0x10CB)]
        bits = await self.profile.read_discrete_inputs(self.client, 0x1001, 202)
        self.assertEqual(bits, expected)
        self.assertEqual(list(self.peer.read_log), [(2, 1, 0x1000, 202), (2, 1, 0x10CA, 1)])

    async def test_last_address_is_read_without_overflow(self):
        self.model.bits[1].update({0xFFFE: True, 0xFFFF: False})
        bits = await self.profile.read_discrete_inputs(self.client, 0xFFFF, 1)
        self.assertEqual(bits, [False])
        self.assertEqual(list(self.peer.read_log), [(2, 1, 0xFFFE, 2)])

    async def test_invalid_ranges_are_rejected_before_network_access(self):
        for address, count in ((-1, 1), (65535, 2), (0, 0), (0, 2001), (True, 1)):
            with self.subTest(address=address, count=count), self.assertRaises(ValueError):
                await self.profile.read_discrete_inputs(self.client, address, count)
        self.assertEqual(list(self.peer.read_log), [])

    async def test_failed_second_block_does_not_return_partial_bits(self):
        self.model.bits[1].pop(0x10CA)
        with self.assertRaises(DeviceException) as caught:
            await self.profile.read_discrete_inputs(self.client, 0x1001, 202)
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(list(self.peer.read_log), [(2, 1, 0x1000, 202), (2, 1, 0x10CA, 1)])

    async def test_alignment_does_not_accept_reversed_mbap_length(self):
        self.peer.fault = "swapped_length"
        with self.assertRaisesRegex(InvalidResponse, "Invalid MBAP header"):
            await self.profile.read_discrete_inputs(self.client, 0x1001, 3)
        # Three reconnect retries still reject the invalid length on every frame.
        self.assertEqual(list(self.peer.read_log), [(2, 1, 0x1000, 4)] * 4)
        self.peer.fault = "fc02_even_address"
        bits = await self.profile.read_discrete_inputs(self.client, 0x1001, 3)
        self.assertEqual(bits, [True, True, False])

    async def test_generic_transport_and_register_reads_remain_unaligned(self):
        # The strict firmware refuses this, proving the generic client still sent
        # the caller's odd address. A normal Modbus peer must be allowed to accept it.
        with self.assertRaises(DeviceException):
            await self.client.read_discrete_inputs(0x1001, 3)
        self.assertEqual(self.peer.read_log[-1], (2, 1, 0x1001, 3))
        self.peer.fault = "normal"
        self.assertEqual(await self.client.read_discrete_inputs(0x1001, 3), [True, True, False])
        for function in (3, 4):
            mapping = Mapping("version_word", 0x2001, 1, function)
            words = await self.runtime._read(mapping)
            self.assertEqual(words, [self.model.registers[0x2001]])
            self.assertEqual(self.peer.read_log[-1], (function, 1, 0x2001, 1))
