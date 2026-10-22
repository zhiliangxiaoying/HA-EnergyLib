"""Exercise the real client against a local TCP peer; no inverter is contacted."""

import asyncio
import math
import struct
import unittest
from contextlib import suppress
from unittest.mock import patch

from energylib_under_test.energylib_core.transport import modbus_tcp as transport
from energylib_under_test.energylib_core.transport.energylib_push import crc16, skip_logger_push
from energylib_under_test.protocol.device import (
    EnergyLibDevice,
    IdentityMismatch,
    UnsupportedDevice,
    decode_float,
    decode_serial,
    decode_version,
)
from energylib_under_test.protocol.discovery import parse_discovery
from energylib_under_test.protocol.modbus import (
    CannotConnect,
    DeviceException,
    InvalidResponse,
    ModbusTcpClient,
)

from tools.discovery_advertisement import txt_properties


def float_words(value):
    return list(struct.unpack(">2H", struct.pack(">f", value)))


def serial_words(serial="TEST-INVERTER-001", padding=b"\0"):
    return list(struct.unpack(">12H", serial.encode("ascii").ljust(24, padding)))


class DecodingTests(unittest.TestCase):
    def test_byte_order_and_units_are_not_rescaled(self):
        self.assertEqual(decode_float([0x3F80, 0], kind="power"), 1.0)
        self.assertEqual(decode_float(float_words(-3.5), kind="power"), -3.5)
        self.assertEqual(decode_version(0x0641), "100.1")

    def test_invalid_values_and_real_zero(self):
        for value in [math.nan, math.inf, -math.inf]:
            self.assertIsNone(decode_float(float_words(value), kind="power"))
        for value in [-0.01, 100.01]:
            self.assertIsNone(decode_float(float_words(value), kind="soc"))
        self.assertIsNone(decode_float(float_words(-1), kind="energy"))
        for value in [0, 100]:
            self.assertEqual(decode_float(float_words(value), kind="soc"), value)
        with self.assertRaises(InvalidResponse):
            decode_float([0], kind="power")

    def test_serial_padding_and_invalid_identity(self):
        # Synthetic identities cover the observed 22-character + two-space format.
        for serial in ("TEST-INVERTER-001", "TEST-INVERTER-00000001", "x" * 24):
            for padding in (b"\0", b" "):
                with self.subTest(serial=serial, padding=padding):
                    self.assertEqual(decode_serial(serial_words(serial, padding)), serial)
        mixed = b"TEST-INVERTER-001\x00 \x00 ".ljust(24, b"\0")
        self.assertEqual(decode_serial(list(struct.unpack(">12H", mixed))), "TEST-INVERTER-001")
        for words in [
            [0] * 12,
            [0xFFFF] * 12,
            [0x4100, 0x4200] + [0] * 10,
            [0],
            list(struct.unpack(">12H", b" " * 24)),
            list(struct.unpack(">12H", b" ABC".ljust(24, b" "))),
            list(struct.unpack(">12H", b"ABC DEF".ljust(24, b" "))),
            list(struct.unpack(">12H", b"ABC\x00DEF".ljust(24, b" "))),
            list(struct.unpack(">12H", b"ABC\t".ljust(24, b" "))),
            list(struct.unpack(">12H", b"ABC\xff".ljust(24, b" "))),
            list(struct.unpack(">12H", b" \x00" * 12)),
        ]:
            with self.subTest(words=words), self.assertRaises(UnsupportedDevice):
                decode_serial(words)

    def test_discovery_requires_logger_contract_and_optional_unit(self):
        props = txt_properties("LOGGER-001", unit_id=None)
        discovery = parse_discovery("192.0.2.1", 1502, props)
        self.assertEqual(discovery.logger_sn, "LOGGER-001")
        self.assertIsNone(discovery.unit_id)
        for unit in (1, 7, 255):
            self.assertEqual(
                parse_discovery("::1", 1502, {**props, "unit_id": str(unit)}).unit_id, unit
            )
        self.assertEqual(
            parse_discovery("192.0.2.1", 1502, {**props, "id": "IGNORED", "future": "x"}).port, 1502
        )

    def test_all_six_fields_required_and_nonempty(self):
        props = txt_properties("LOGGER-001", unit_id=None)
        for key in props:
            missing = {k: v for k, v in props.items() if k != key}
            for candidate in (missing, {**props, key: ""}, {**props, key: " "}, {**props, key: 1}):
                with self.subTest(key=key), self.assertRaises(ValueError):
                    parse_discovery("192.0.2.1", 1502, candidate)
        for props in ({}, {"id": "OLD", "transport": "modbus_tcp", "api_ver": "1", "auth": "none"}):
            with self.assertRaises(ValueError):
                parse_discovery("192.0.2.1", 1502, props)

    def test_discovery_rejects_unsupported_or_malformed_metadata(self):
        for properties in [
            {"transport": "rtu"},
            {"api_ver": "2"},
            {"auth": "token"},
            {"sn": " ID "},
            {"sn": "X\nY"},
            {"sn": "X" * 253},
            *({"unit_id": v} for v in ("", "0", "256", "1.5", "-1", " 1", "１", 1, None, "0001")),
        ]:
            with self.subTest(properties=properties), self.assertRaises(ValueError):
                parse_discovery("192.0.2.1", 1502, {**txt_properties("LOGGER"), **properties})
        for port in [None, 0, 65536, True]:
            with self.assertRaises(ValueError):
                parse_discovery("192.0.2.1", port, {})


class TcpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.handlers = set()
        self.writers = set()
        self.requests = []
        self.request_received = asyncio.Event()
        self.mode = "normal"
        self.modes = []
        self.write_modes = []
        self.registers = {}
        self.ignored_writes = 0
        self.push_per_request = b""
        self.connection_count = 0
        self.retry_delays = patch.object(transport, "READ_RETRY_DELAYS", (0, 0, 0))
        self.retry_delays.start()
        self.addCleanup(self.retry_delays.stop)
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        self.client = ModbusTcpClient(
            "127.0.0.1", self.server.sockets[0].getsockname()[1], 1, timeout=0.15
        )

    async def asyncTearDown(self):
        await self.client.close()
        self.server.close()
        await self.server.wait_closed()
        for writer in self.writers:
            writer.close()
        for task in list(self.handlers):
            task.cancel()
        await asyncio.gather(*self.handlers, return_exceptions=True)

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        self.writers.add(writer)
        self.connection_count += 1
        try:
            while True:
                header = await reader.readexactly(7)
                tid, proto, length, unit = struct.unpack(">HHHB", header)
                pdu = await reader.readexactly(length - 1)
                self.requests.append((tid, proto, length, unit, pdu))
                self.request_received.set()
                function, address, count = struct.unpack(">BHH", pdu[:5])
                mode = self.modes.pop(0) if self.modes else self.mode
                if function == 16 and self.write_modes:
                    mode = self.write_modes.pop(0)
                if function == 16:
                    response = pdu[:5]
                    if mode != "exception":
                        if self.ignored_writes:
                            self.ignored_writes -= 1
                        else:
                            words = struct.unpack(f">{count}H", pdu[6:])
                            self.registers.update({address + i: w for i, w in enumerate(words)})
                elif function == 2:
                    response = bytes([2, (count + 7) // 8]) + bytes([0x55]) * ((count + 7) // 8)
                else:
                    response = bytes([function, count * 2]) + struct.pack(
                        f">{count}H",
                        *[self.registers.get(a, a) for a in range(address, address + count)],
                    )
                if mode == "timeout":
                    await reader.read()
                    return
                if mode == "partial_push":
                    writer.write(self.PUSH[:12])
                    await writer.drain()
                    await reader.read()
                    return
                if mode == "exception":
                    response = bytes([function | 0x80, 2])
                elif mode == "bad_function":
                    response = b"\x04" + response[1:]
                elif mode == "bad_count":
                    response = bytes([function, 0]) + response[2:]
                elif mode == "bad_write_echo":
                    response = b"\x10\x00\x01\x00\x01"
                elif mode == "bad_exception":
                    response = b"\x83\x02\x00"
                reply = self.push_per_request + (
                    struct.pack(
                        ">HHHB",
                        tid + (mode == "bad_tid"),
                        int(mode == "bad_protocol"),
                        255 if mode == "oversize" else len(response) + 1,
                        unit + (mode == "bad_unit"),
                    )
                    + response
                )
                if mode == "truncated":
                    writer.write(reply[:-1])
                    await writer.drain()
                    return
                # Explicitly split headers/payloads across TCP writes.
                writer.write(reply[:3])
                await writer.drain()
                await asyncio.sleep(0)
                writer.write(reply[3:])
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()
            self.writers.discard(writer)
            self.handlers.discard(task)

    async def test_tcp_framing_partial_reads_and_reused_connection(self):
        self.assertEqual(await self.client.read_holding_registers(0x2000, 2), [8192, 8193])
        await self.client.read_holding_registers(0x1240, 2)
        self.assertEqual(self.connection_count, 1)
        self.assertEqual(self.requests[0][1:4], (0, 6, 1))
        self.assertEqual(self.requests[0][4], b"\x03\x20\x00\x00\x02")
        self.assertNotEqual(self.requests[0][0], self.requests[1][0])

    # Synthetic payload, not a physical logger identity or private capture.
    PUSH = bytes.fromhex("7e0000000105010010 000102030405060708090a0b0c0d0e0f 5d9c0d")

    async def test_profile_push_filter_preserves_coalesced_and_split_modbus_responses(self):
        self.assertEqual(crc16(b"123456789"), 0x4B37)
        self.client.unsolicited_filter = skip_logger_push
        self.push_per_request = self.PUSH * 2
        self.assertEqual(await self.client.read_holding_registers(10, 2), [10, 11])
        self.assertEqual(self.client.filtered_push_frames, 2)
        self.assertEqual(self.client.read_retries, 0)
        self.assertEqual(self.connection_count, 1)
        await self.client.write_registers(10, [20, 21])
        self.assertEqual(self.client.filtered_push_frames, 4)
        self.assertEqual(self.client.write_retries, 0)

    async def test_generic_client_does_not_filter_vendor_frames(self):
        self.push_per_request = self.PUSH
        with self.assertRaises(InvalidResponse):
            await self.client.read_holding_registers(10, 2)
        self.assertEqual(self.client.filtered_push_frames, 0)
        self.assertEqual(len(self.requests), 4)

    async def test_push_filter_rejects_crc_length_version_and_terminator_errors(self):
        self.client.unsolicited_filter = skip_logger_push
        for offset in (0, 6, 8, 10, 25, 27):
            with self.subTest(offset=offset):
                frame = bytearray(self.PUSH)
                frame[offset] ^= 1
                self.push_per_request = bytes(frame)
                with self.assertRaises(InvalidResponse):
                    await self.client.read_holding_registers(10, 2)
        self.assertEqual(self.client.filtered_push_frames, 0)

    async def test_push_flood_is_bounded(self):
        self.client.unsolicited_filter = skip_logger_push
        self.push_per_request = self.PUSH * 9
        with self.assertRaisesRegex(InvalidResponse, "Too many unsolicited"):
            await self.client.read_holding_registers(10, 2)
        self.assertEqual(len(self.requests), 4)
        self.assertEqual(self.client.filtered_push_frames, 36)

    async def test_truncated_push_frame_obeys_request_timeout_and_retry_limit(self):
        self.client.unsolicited_filter = skip_logger_push
        self.mode = "partial_push"
        with self.assertRaises(CannotConnect):
            await self.client.read_holding_registers(10, 2)
        self.assertEqual(len(self.requests), 4)
        self.assertEqual(self.client.filtered_push_frames, 0)

    async def test_valid_modbus_transaction_starting_with_7e_is_never_filtered(self):
        self.client.unsolicited_filter = skip_logger_push
        self.client._transaction = 0x7DFF
        self.assertEqual(await self.client.read_holding_registers(10, 2), [10, 11])
        self.assertEqual(self.requests[0][0], 0x7E00)
        self.assertEqual(self.client.filtered_push_frames, 0)

    async def verify_test_identity(self):
        # Synthetic identity probe: production uses DeviceRuntime._identity/SN.
        self.assertEqual(await self.client.read_holding_registers(0x2000, 1), [0x2000])
        self.client.write_generation = self.client.generation

    async def test_safe_write_lost_ack_uses_readback_without_duplicate_write(self):
        self.client.verify_write_identity = self.verify_test_identity
        self.write_modes = ["timeout"]
        await self.client.write_registers(10, [20, 21], retry_safe=True)
        self.assertEqual(sum(r[4][0] == 16 for r in self.requests), 1)
        self.assertEqual(self.client.write_readback_recoveries, 1)
        self.assertEqual(self.client.write_retries, 0)

    async def test_safe_parameter_write_recovers_on_fourth_attempt(self):
        self.client.verify_write_identity = self.verify_test_identity
        self.write_modes = ["timeout"] * 3
        self.ignored_writes = 3
        await self.client.write_registers(10, [20, 21], retry_safe=True)
        self.assertEqual(sum(r[4][0] == 16 for r in self.requests), 4)
        self.assertEqual(self.client.write_retries, 3)
        self.assertEqual(self.registers[10], 20)

    async def test_safe_write_exhaustion_stops_after_three_retries(self):
        self.client.verify_write_identity = self.verify_test_identity
        self.write_modes = ["timeout"] * 4
        self.ignored_writes = 4
        with self.assertRaises(CannotConnect):
            await self.client.write_registers(10, [20, 21], retry_safe=True)
        self.assertEqual(sum(r[4][0] == 16 for r in self.requests), 4)

    async def test_changed_identity_or_unreadable_state_prevents_write_resend(self):
        async def changed():
            raise IdentityMismatch("Injected identity mismatch")

        self.client.verify_write_identity = changed
        self.write_modes = ["timeout"]
        with self.assertRaises(IdentityMismatch):
            await self.client.write_registers(10, [20, 21], retry_safe=True)
        self.assertEqual(sum(r[4][0] == 16 for r in self.requests), 1)
        self.assertEqual(self.client.write_retries, 0)

        async def unavailable_after_identity():
            await self.verify_test_identity()
            self.mode = "timeout"

        self.client.verify_write_identity = unavailable_after_identity
        self.requests.clear()
        self.write_modes = ["timeout"]
        with self.assertRaises(CannotConnect):
            await self.client.write_registers(10, [20, 21], retry_safe=True)
        self.assertEqual(sum(r[4][0] == 16 for r in self.requests), 1)

    async def test_concurrent_calls_are_serialized(self):
        values = await asyncio.gather(
            *[self.client.read_holding_registers(address, 2) for address in range(10)]
        )
        self.assertEqual(values, [[n, n + 1] for n in range(10)])
        self.assertEqual(self.connection_count, 1)

    async def test_rejects_malformed_responses_and_reconnects(self):
        for mode in [
            "bad_tid",
            "bad_protocol",
            "bad_unit",
            "bad_function",
            "bad_count",
            "oversize",
            "bad_exception",
        ]:
            with self.subTest(mode=mode):
                self.mode = mode
                with self.assertRaises(InvalidResponse):
                    await self.client.read_holding_registers(0, 2)
                self.mode = "normal"
                self.assertEqual(await self.client.read_holding_registers(0, 2), [0, 1])

    async def test_modbus_exception_is_not_zero(self):
        self.mode = "exception"
        with self.assertRaises(DeviceException) as caught:
            await self.client.read_holding_registers(0, 2)
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.client.read_retries, 0)

    async def test_three_retries_recover_reads_on_fourth_attempt(self):
        for function in (2, 3, 4):
            with self.subTest(function=function):
                await self.client.close()
                self.requests.clear()
                before = self.connection_count
                self.modes = ["bad_tid", "bad_count", "truncated"]
                if function == 2:
                    result = await self.client.read_discrete_inputs(0, 10)
                    self.assertEqual(result, [True, False] * 5)
                else:
                    result = await self.client.read_registers(function, 0, 2)
                    self.assertEqual(result, [0, 1])
                self.assertEqual(len(self.requests), 4)
                self.assertEqual(self.connection_count - before, 4)
                self.assertEqual(len({r[0] for r in self.requests}), 4)
                self.assertTrue(all(r[4] == self.requests[0][4] for r in self.requests))
        self.assertEqual(self.client.read_retries, 9)

    async def test_persistent_read_error_stops_after_three_retries(self):
        self.mode = "bad_count"
        with self.assertRaises(InvalidResponse):
            await self.client.read_holding_registers(0, 2)
        self.assertEqual(len(self.requests), 4)
        self.assertEqual(self.client.read_retries, 3)
        self.assertIsNone(self.client._writer)

    async def test_timeout_retry_recovers_without_accepting_late_response(self):
        self.modes = ["timeout"]
        self.assertEqual(await self.client.read_holding_registers(0, 2), [0, 1])
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.connection_count, 2)

    async def test_connection_failure_is_retried_three_times(self):
        connect = asyncio.open_connection
        attempts = 0

        async def unavailable(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts <= 3:
                raise OSError("Injected refused connection")
            return await connect(*args, **kwargs)

        with patch.object(asyncio, "open_connection", unavailable):
            self.assertEqual(await self.client.read_holding_registers(0, 2), [0, 1])
        self.assertEqual(attempts, 4)
        self.assertEqual(self.client.read_retries, 3)
        self.assertEqual(len(self.requests), 1)

    async def test_retry_series_finishes_before_a_queued_request(self):
        self.modes = ["bad_count"] * 3
        values = await asyncio.gather(
            self.client.read_holding_registers(10, 2),
            self.client.read_holding_registers(20, 2),
        )
        self.assertEqual(values, [[10, 11], [20, 21]])
        self.assertEqual([struct.unpack(">BHH", r[4])[1] for r in self.requests], [10] * 4 + [20])

    async def test_cancellation_during_retry_backoff_sends_no_more_requests(self):
        transport.READ_RETRY_DELAYS = (60, 60, 60)
        self.mode = "bad_tid"
        task = asyncio.create_task(self.client.read_holding_registers(0, 2))
        await self.request_received.wait()
        for _ in range(100):
            if self.client.read_retries:
                break
            await asyncio.sleep(0)
        self.assertEqual(self.client.read_retries, 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(len(self.requests), 1)
        self.assertIsNone(self.client._writer)
        self.mode = "normal"
        self.assertEqual(await self.client.read_holding_registers(0, 2), [0, 1])

    async def test_writes_are_never_retried(self):
        for mode, error in (
            ("timeout", CannotConnect),
            ("bad_tid", InvalidResponse),
            ("bad_write_echo", InvalidResponse),
            ("exception", DeviceException),
        ):
            with self.subTest(mode=mode):
                self.mode = mode
                self.requests.clear()
                with self.assertRaises(error):
                    await self.client.write_registers(0, [1, 2])
                self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.client.read_retries, 0)

    async def test_read_reconnect_invalidates_pinned_write_connection(self):
        await self.client.read_holding_registers(0, 2)
        self.client.write_generation = self.client.generation
        self.modes = ["bad_count"]
        await self.client.read_holding_registers(0, 2)
        self.requests.clear()
        with self.assertRaises(CannotConnect):
            await self.client.write_registers(0, [1, 2])
        self.assertEqual(self.requests, [])

    async def test_timeout_and_truncated_frame_close_stream(self):
        for mode in ["timeout", "truncated"]:
            self.mode = mode
            with self.assertRaises(CannotConnect):
                await self.client.read_holding_registers(0, 2)
            self.mode = "normal"
            self.assertEqual(await self.client.read_holding_registers(0, 2), [0, 1])

    async def test_cancellation_discards_connection(self):
        self.mode = "timeout"
        task = asyncio.create_task(self.client.read_holding_registers(0, 2))
        await self.request_received.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.mode = "normal"
        self.assertEqual(await self.client.read_holding_registers(0, 2), [0, 1])

    async def test_rejects_bad_ranges_without_sending(self):
        for address, count in [(-1, 1), (0, 0), (0, 126), (65535, 2)]:
            with self.assertRaises(ValueError):
                await self.client.read_holding_registers(address, count)
        self.assertEqual(self.requests, [])


class FakeRegisters:
    def __init__(self):
        self.serial = "TEST-INVERTER-001"
        self.serial_padding = b"\0"
        self.energy = 123.5
        self.unsupported = set()
        self.fail = False
        self.calls = []

    async def read_holding_registers(self, address, count):
        self.calls.append((address, count))
        if self.fail:
            raise CannotConnect("Test disconnected")
        if address in self.unsupported:
            raise DeviceException(2)
        if address == 0x2000:
            return [0x0641, 0, 0, 0x0652, 0, 0, 1, 0, 0, 0]
        if address == 0xC102:
            return serial_words(self.serial, self.serial_padding)
        if address == 0x1212:
            return float_words(52.5)
        return float_words(self.energy)


class SnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_snapshot_rechecks_serial_after_read_reconnect(self):
        registers = FakeRegisters()
        registers.generation = 1
        original = registers.read_holding_registers

        async def replaced(address, count):
            if address == 0x125C:
                registers.generation += 1
                registers.serial = "OTHER-INVERTER"
            return await original(address, count)

        with patch.object(registers, "read_holding_registers", replaced):
            with self.assertRaises(IdentityMismatch):
                await EnergyLibDevice(registers).read_snapshot("TEST-INVERTER-001")

    async def test_padding_change_preserves_identity_and_changed_serial_still_fails(self):
        registers = FakeRegisters()
        device = EnergyLibDevice(registers)
        original = await device.identify()
        registers.serial_padding = b" "
        self.assertEqual(await device.identify(), original)
        snapshot = await device.read_snapshot(original.serial)
        self.assertEqual(snapshot["battery_soc"], 52.5)
        registers.serial = "OTHER-INVERTER"
        registers.calls.clear()
        with self.assertRaises(IdentityMismatch):
            await device.read_snapshot(original.serial)
        self.assertEqual([address for address, _ in registers.calls], [0x2000, 0xC102])

    async def test_identity_unsupported_point_and_counter_reset(self):
        registers = FakeRegisters()
        device = EnergyLibDevice(registers)
        identity = await device.identify()
        self.assertEqual(identity.arm_version, "101.2")
        registers.unsupported.add(0x1212)
        first = await device.read_snapshot(identity.serial)
        self.assertIsNone(first["battery_soc"])
        self.assertEqual(first["solar_energy"], 123.5)
        registers.energy = 0
        second = await device.read_snapshot(identity.serial)
        self.assertEqual(second["solar_energy"], 0)
        registers.energy = 1
        third = await device.read_snapshot(identity.serial)
        self.assertEqual(third["solar_energy"], 1)

    async def test_changed_identity_aborts_before_telemetry(self):
        registers = FakeRegisters()
        device = EnergyLibDevice(registers)
        with self.assertRaises(IdentityMismatch):
            await device.read_snapshot("OTHER-INVERTER")
        self.assertEqual([x[0] for x in registers.calls], [0x2000, 0xC102])

    async def test_transport_failure_cannot_become_an_empty_or_zero_snapshot(self):
        registers = FakeRegisters()
        registers.fail = True
        with self.assertRaises(CannotConnect):
            await EnergyLibDevice(registers).read_snapshot(registers.serial)

    async def test_unknown_profile_identity_is_rejected(self):
        registers = FakeRegisters()
        registers.unsupported.add(0x2000)
        with self.assertRaises(UnsupportedDevice):
            await EnergyLibDevice(registers).identify()
