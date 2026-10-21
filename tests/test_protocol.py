"""Exercise the real client against a local TCP peer; no inverter is contacted."""

import asyncio
import math
import struct
import unittest
from contextlib import suppress

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


def serial_words(serial="TEST-INVERTER-001"):
    return list(struct.unpack(">12H", serial.encode("ascii").ljust(24, b"\0")))


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
        self.assertEqual(decode_serial(serial_words()), "TEST-INVERTER-001")
        for words in [[0] * 12, [0xFFFF] * 12, [0x4100, 0x4200] + [0] * 10, [0]]:
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
        self.connection_count = 0
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
                function, address, count = struct.unpack(">BHH", pdu)
                response = bytes([function, count * 2]) + struct.pack(
                    f">{count}H", *range(address, address + count)
                )
                mode = self.mode
                if mode == "timeout":
                    await reader.read()
                    return
                if mode == "exception":
                    response = b"\x83\x02"
                elif mode == "bad_function":
                    response = b"\x04" + response[1:]
                elif mode == "bad_count":
                    response = bytes([3, 0]) + response[2:]
                elif mode == "bad_exception":
                    response = b"\x83\x02\x00"
                reply = (
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
            return serial_words(self.serial)
        if address == 0x1212:
            return float_words(52.5)
        return float_words(self.energy)


class SnapshotTests(unittest.IsolatedAsyncioTestCase):
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
