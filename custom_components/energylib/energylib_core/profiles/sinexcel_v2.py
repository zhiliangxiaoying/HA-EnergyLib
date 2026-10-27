"""Sinexcel V001B031 profile: reads plus opt-in controls on documented registers.

Device power signs (battery + = charging, grid + = feed-in, confirmed) are flipped to
the standard convention via DEVICE_SIGN.
Control registers are taken from the protocol. Points the protocol leaves open
are implemented as stated assumptions (see docs/FIRMWARE_QUESTIONS.md) and are
not hardware-verified:
  - heartbeat: the EMS writes 1.0 to 0xC068 periodically (Q3);
  - running state: 3 bits from 0x1001, least significant bit first;
  - TOU: the mode is set to self-consumption while blocks are written, there is
    no separate master switch (Q5); effective dates are U32 local-as-UTC stamps;
  - rated power for limits comes from 0x2008 (U16, value / 100).
Controls are offered only when the device answers every control register, and
HA still requires the user's explicit "allow control" option.
"""

import contextlib
import math
import struct
from datetime import date, datetime, timezone

from ..legacy import DeviceIdentity, EnergyLibDevice, decode_float
from ..model.points import CONTROLS, RUNNING_STATES, RegisterMapping
from ..model.tou import OUTSIDE_MODES, WEEKDAYS, TouLimits, TouSchedule, minute
from ..planner import ReadBlock, plan_reads
from ..transport.energylib_push import skip_logger_push
from ..transport.modbus_tcp import DeviceException, EnergyLibError, InvalidResponse
from .sinexcel_alarms import GRID_ALARM_BIT, describe_alarm

Mapping = RegisterMapping

# V001B031 0x300C device code -> standard work-mode name.
WORK_MODE_CODES = {
    0: "self_consumption",
    1: "time_of_use",
    2: "dynamic_tariff",
    3: "backup",
    4: "remote",
    5: "pv_charge_priority",
    6: "feed_in_priority",
    7: "voltage_regulation",
    8: "peak_shaving",
}
EXPORT_LIMIT_ENABLE = 0x3002
CONTROL_ADDRESSES = {
    "work_mode": 0x300C,
    "backup_soc": 0x314A,
    "min_soc": 0x3046,
    "grid_charge": 0x322A,
    "export_limit": 0x3004,
    "export_limit_enable": EXPORT_LIMIT_ENABLE,
    "remote_control": 0xC646,
    "remote_power": 0xC648,
}
# Advanced battery limits (PDF p68/p72/p81): offered per key only when readable.
ADVANCED_CONTROL_ADDRESSES = {
    "max_soc": 0x31B6,  # [50, 101] %
    "max_charge_current": 0x3040,  # [0, 300] A
    "max_discharge_current": 0x3042,  # [0, 300] A
}
# Battery 2 copies, written together with battery 1 when battery 2 is enabled.
BATTERY_2_ADDRESSES = {
    "min_soc": 0x30B8,
    "backup_soc": 0x314C,
    "max_soc": 0x31B8,
    "max_charge_current": 0x30B2,
    "max_discharge_current": 0x30B4,
}
HEARTBEAT, HEARTBEAT_TIMEOUT, ESCAPE_MODE = 0xC068, 0xC06A, 0xC06C
OUTSIDE_MODE = 0x314E
RATED_POWER = 0x2008
PROTOCOL_MAX_KW = 50.0  # 0x3004 / 0xC648 / TOU power ranges in V001B031
SCHEDULE_BLOCKS = ((0xC014, 48), (0xC0B4, 6), (0xC0BA, 24))
# Device sign conventions, flipped to the standard (grid + = import, battery + = discharge).
# Confirmed for Sinexcel 2026-10-19: battery power + = charging (0x1272), grid power
# + = feed-in/export (0x1276). Battery 2 power and both battery currents are
# assumed to follow the battery convention (Q2 follow-up).
DEVICE_SIGN = {
    "battery_power": -1.0,
    "grid_power": -1.0,
    "battery_2_power": -1.0,
    "battery_current": -1.0,
    "battery_2_current": -1.0,
}
# Validity ends are stored as the last second of a day: the real device normalised a
# written 2099-12-31 00:00 to 23:59:59 (and its own windows end at 23:59:59).
FAR_FUTURE = int(datetime(2099, 12, 31, 23, 59, 59, tzinfo=timezone.utc).timestamp())
REMOTE_TIMEOUT_S, REMOTE_ESCAPE = 60, 1  # escape 1 = self-consumption
# Vendor "Working Mode" note: peak shaving (0x300C = 8) exists on 30/50 kW models only.
PEAK_SHAVING_MIN_KW = 30
REMOTE_MODE_CODE = 4  # 0x300C while manual control (0xC646) is or was enabled
# Explicit level parameters only. Heartbeats, remote enable/disable and TOU table
# blocks retain single-send semantics until their repeated-write behavior is verified.
RETRY_SAFE_ADDRESSES = frozenset(
    {v for k, v in CONTROL_ADDRESSES.items() if k != "remote_control"}
    | set(ADVANCED_CONTROL_ADDRESSES.values())
    | set(BATTERY_2_ADDRESSES.values())
    | {EXPORT_LIMIT_ENABLE, HEARTBEAT_TIMEOUT, ESCAPE_MODE}
)


def floats(values):
    return list(struct.unpack(f">{len(values) * 2}H", struct.pack(f">{len(values)}f", *values)))


def u32_words(value):
    return [value >> 16 & 0xFFFF, value & 0xFFFF]


def local_date_stamp(day: date) -> int:
    """V001B031 dates: local calendar date expressed as a UTC timestamp."""
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())


def encode_schedule(schedule, today: date):
    times, powers = [0.0] * 24, [0.0] * 12
    for kind, offset in (("charge", 0), ("discharge", 2)):
        for i, p in enumerate(p for p in schedule.periods if p.type == kind):
            for j, text in enumerate((p.start, p.end)):
                m = minute(text)
                times[i * 4 + offset + j] = m // 60 * 100 + m % 60
            powers[i * 2 + offset // 2] = p.power
    # Bit 0 = Sunday ... bit 6 = Saturday.
    mask = sum(1 << ((WEEKDAYS.index(day) + 1) % 7) for day in schedule.weekdays)
    start = (
        int(schedule.valid_from.timestamp())
        if schedule.valid_from is not None
        else local_date_stamp(today)
    )
    end = int(schedule.valid_until.timestamp()) if schedule.valid_until is not None else FAR_FUTURE
    if not 0 <= start <= end <= 0xFFFFFFFF:
        raise ValueError("Invalid device validity range")
    meta = u32_words(mask) + u32_words(start) + u32_words(end)
    return floats(times), meta, floats(powers)


def decode_schedule(blocks, outside_mode, limits):
    time_words, meta, power_words = blocks
    times = struct.unpack(">24f", struct.pack(">48H", *time_words))
    powers = struct.unpack(">12f", struct.pack(">24H", *power_words))
    periods = []
    for i in range(6):
        for kind, offset in (("charge", 0), ("discharge", 2)):
            a, b = times[i * 4 + offset : i * 4 + offset + 2]
            if a == b:
                continue
            if not a.is_integer() or not b.is_integer():
                raise ValueError("Invalid device time")
            periods.append(
                {
                    "type": kind,
                    "start": f"{int(a) // 100:02}:{int(a) % 100:02}",
                    "end": f"{int(b) // 100:02}:{int(b) % 100:02}",
                    "power": powers[i * 2 + offset // 2],
                }
            )
    if meta[0] != 0 or meta[1] & ~0x7F:
        raise ValueError("Invalid weekday mask")
    days = [d for i, d in enumerate(WEEKDAYS) if meta[1] & (1 << ((i + 1) % 7))]
    if outside_mode is None or not outside_mode.is_integer() or not 0 <= outside_mode < 4:
        raise ValueError("Invalid outside mode")
    return TouSchedule.parse(
        {
            "periods": periods,
            "weekdays": days,
            "outside_mode": OUTSIDE_MODES[int(outside_mode)],
            "valid_from": datetime.fromtimestamp(meta[2] << 16 | meta[3], timezone.utc),
            "valid_until": datetime.fromtimestamp(meta[4] << 16 | meta[5], timezone.utc),
        },
        limits,
    )


class SinexcelProfile:
    def describe_alarm(self, function, unit, address):
        return describe_alarm(self.id, function, unit, address)

    id = "sinexcel_v2"
    version = 1
    namespace = "energylib"
    manufacturer = "EnergyLIB"
    device_name = "EnergyLIB"
    register_limit = 2
    bit_limit = 202
    power_direction_verified = True  # Vendor convention confirmed; see DEVICE_SIGN.
    controls_hw_verified = False  # Implemented from the protocol, not yet on hardware.
    work_mode_codes = WORK_MODE_CODES
    # FC02 codes meaning a link is missing: "grid disconnected", "battery 1 disconnected".
    link_alarms = {"grid": frozenset({"110C"}), "battery": frozenset({"1058"})}

    def __init__(self):
        self.capabilities = set()
        self.mappings = {}
        self.single_battery_confirmed = False
        self.controls_supported = False
        self.control_keys = frozenset()
        self.max_power_kw = None
        self.tou_limits = None
        self._flow_block_enabled = True
        self.control_addresses = dict(CONTROL_ADDRESSES)
        # The device's own watchdog settings, kept from the first remote start until
        # the session is left (a renewal must not record our own 60 s / escape 1).
        self._saved_watchdog = None

    def plan_reads(self, mappings):
        """Keep the four adjacent live powers in one response when all are due."""
        mappings = set(mappings)
        flow = tuple(
            sorted(
                (
                    m
                    for m in mappings
                    if m.function == 3
                    and m.count == 2
                    and m.address in (0x1270, 0x1272, 0x1274, 0x1276)
                ),
                key=lambda m: m.address,
            )
        )
        if (
            self._flow_block_enabled
            and len(flow) == 4
            and len({m.unit for m in flow}) == 1
            and len({m.address for m in flow}) == 4
        ):
            blocks = plan_reads(
                mappings - set(flow), register_limit=self.register_limit, bit_limit=self.bit_limit
            )
            blocks.append(ReadBlock(3, flow[0].unit, 0x1270, 8, flow))
            return sorted(blocks, key=lambda b: (b.function, b.unit, b.address))
        return plan_reads(mappings, register_limit=self.register_limit, bit_limit=self.bit_limit)

    def can_fallback_read(self, block, code):
        """A definitive unsupported flow block disables batching until re-probe."""
        if block.function == 3 and block.address == 0x1270 and block.count == 8:
            if code in (1, 2, 3, 11):
                self._flow_block_enabled = False
                return True
            return False
        return code in (1, 2, 11)

    async def identify(self, client) -> DeviceIdentity:
        client.unsolicited_filter = skip_logger_push
        return await EnergyLibDevice(client).identify()

    async def read_discrete_inputs(self, client, address, count, *, unit_id=None):
        """Align FC02 starts for the observed firmware, preserving logical bit addresses.

        This is a device quirk, not a Modbus restriction on odd quantities. Read
        one leading bit for an odd start and discard it. Split large requests at
        even boundaries to keep every wire request inside the profile block limit.
        """
        if (
            type(address) is not int
            or type(count) is not int
            or not 0 <= address < 65536
            or not 1 <= count <= 2000
            or address + count > 65536
        ):
            raise ValueError("Invalid bit range")
        limit = min(self.bit_limit, 2000) & ~1
        if limit < 2:
            raise ValueError("FC02 block limit cannot support even-address reads")
        offset = address & 1
        start, remaining = address - offset, count + offset
        bits = []
        while remaining:
            size = min(remaining, limit)
            bits.extend(await client.read_discrete_inputs(start, size, unit_id=unit_id))
            start += size
            remaining -= size
        return bits[offset : offset + count]

    async def _readable(self, client, blocks):
        try:
            for address, count in blocks:
                await client.read_holding_registers(address, count)
        except DeviceException as err:
            if err.code not in (1, 2):
                raise
            return False
        return True

    async def configure(self, client, identity):
        self._flow_block_enabled = True
        self.capabilities = {"battery"}
        if identity.machine_type == 0:
            self.capabilities.add("three_phase")
        probes = {}
        for key, address in (
            ("pv_count", 0xC05E),
            ("battery_2", 0x3036),
            ("port_use", 0xC264),
            ("bms", 0xB000),
            ("meter", 0xA000),
        ):
            try:
                probes[key] = decode_float(
                    await client.read_holding_registers(address, 2), kind="capability"
                )
            except DeviceException as err:
                if err.code not in (1, 2):
                    raise
                probes[key] = None
        count = probes["pv_count"]
        if count is not None and count.is_integer() and 0 <= count <= 10:
            self.capabilities.update(f"pv{i}" for i in range(1, min(int(count), 4) + 1))
        if probes["battery_2"] == 1:
            self.capabilities.add("battery_2")
        self.single_battery_confirmed = probes["battery_2"] == 0
        if probes["bms"] == 1:
            self.capabilities.add("bms")
            # A BMS may not report cabinet capacity (the first real one reads 0 kWh);
            # a 0 kWh sensor would mislead, so the capacity pair needs a real value.
            try:
                capacity = decode_float(
                    await client.read_holding_registers(0xB15C, 2), kind="capability"
                )
            except DeviceException as err:
                if err.code not in (1, 2):
                    raise
                capacity = None
            if capacity is not None and capacity > 0:
                self.capabilities.add("bms_capacity")
        if probes["meter"] is not None:
            self.capabilities.add("meter")
        if probes["port_use"] in (1, 2):
            self.capabilities.add("generator")
        elif probes["port_use"] == 4:
            self.capabilities.add("ac_couple")
        await self._configure_controls(client)
        addresses = {
            "battery_soc": 0x1212,
            "battery_total_soc": 0xC644,
            "grid_import_energy": 0x1240,
            "grid_export_energy": 0x1242,
            "solar_energy": 0x125C,
            "battery_charge_energy": 0x125E,
            "battery_discharge_energy": 0x1260,
            "load_energy": 0x1262,
            "solar_power": 0x1270,
            "battery_power": 0x1272,
            "load_power": 0x1274,
            "grid_power": 0x1276,
            "work_mode": 0x300C,
            "grid_voltage_a": 0x1000,
            "grid_voltage_b": 0x1002,
            "grid_voltage_c": 0x1004,
            "grid_frequency": 0x1012,
            "battery_voltage": 0x120C,
            "battery_current": 0x120E,
            "battery_soh": 0xB108,
            # Battery cabinet 1 (PDF p122-p126); BMS capability only.
            "battery_cell_voltage_max": 0xB10A,
            "battery_cell_voltage_min": 0xB10C,
            "battery_cell_temperature_max": 0xB112,
            "battery_cell_temperature_min": 0xB114,
            "bms_charge_current_limit": 0xB11A,
            "bms_discharge_current_limit": 0xB11C,
            "battery_capacity": 0xB15C,  # installed total, kWh
            "battery_remaining_energy": 0xB15E,  # remaining total, kWh
            "battery_2_soc": 0x121C,
            "battery_2_power": 0x121A,
            "battery_2_voltage": 0x1216,  # PDF p99: battery 2 voltage (V)
            "battery_2_current": 0x1218,  # PDF p99: battery 2 current (A)
            "pv1_power": 0x1204,
            "pv2_power": 0x120A,
            "pv3_power": 0x1280,
            "pv4_power": 0x1286,
            "backup_load_power": 0x1278,
            "generator_power": 0x128C,
            "ac_couple_power": 0x12A4,
            "meter_online": 0xA000,  # 0 offline, 1 online
        }
        self.mappings = {
            k: (Mapping(k, a, unit=client.unit_id, scale=DEVICE_SIGN.get(k, 1.0)),)
            for k, a in addresses.items()
        }
        self.mappings["inverter_temperature"] = tuple(
            Mapping(f"temperature_{i}", 0x10DE + i * 2, unit=client.unit_id) for i in range(5)
        )
        if client.unit_id == 1:
            alarm_sources = tuple(Mapping(f"alarms_{u}", 0x1048, 202, 2, u, "bits") for u in (1, 2))
            # The grid summary bit sits outside the alarm table. Only a device that
            # answers it gets the source, so an unsupported address never leaves the
            # alarm status permanently incomplete. (BMS communication is already in
            # the table at 0x1110/0x1111.)
            try:
                await self.read_discrete_inputs(client, GRID_ALARM_BIT, 1)
            except DeviceException as err:
                if err.code not in (1, 2):
                    raise
            else:
                alarm_sources += (Mapping("alarm_grid", GRID_ALARM_BIT, 1, 2, 1, "bits"),)
            self.mappings.update(
                {
                    "running_state": (Mapping("running_state", 0x1001, 3, 2, encoding="bits"),),
                    "active_alarms": alarm_sources,
                }
            )

    async def _configure_controls(self, client):
        """Offer controls only for a device that answers every control register."""
        self.controls_supported, self.control_keys = False, frozenset()
        self.max_power_kw = self.tou_limits = None
        if client.unit_id != 1:  # V001B031 writes go to slave 1 only.
            return
        try:
            rated = (await client.read_holding_registers(RATED_POWER, 1))[0] / 100
        except DeviceException as err:
            if err.code not in (1, 2):
                raise
            rated = 0
        if rated > 0:
            self.max_power_kw = min(rated, PROTOCOL_MAX_KW)
        registers = [
            *CONTROL_ADDRESSES.values(),
            EXPORT_LIMIT_ENABLE,
            HEARTBEAT,
            HEARTBEAT_TIMEOUT,
            ESCAPE_MODE,
        ]
        if "battery_2" in self.capabilities:
            registers += [0x30B8, 0x314C]
        self.control_addresses = dict(CONTROL_ADDRESSES)
        if self.max_power_kw and await self._readable(client, [(a, 2) for a in registers]):
            self.controls_supported = True
            for key, address in ADVANCED_CONTROL_ADDRESSES.items():
                blocks = [(address, 2)]
                if "battery_2" in self.capabilities:
                    blocks.append((BATTERY_2_ADDRESSES[key], 2))
                if await self._readable(client, blocks):
                    self.control_addresses[key] = address
            self.control_keys = frozenset(
                c.key for c in CONTROLS if c.key in self.control_addresses
            )
            self.capabilities.update("ctrl_" + key for key in self.control_keys)
            if await self._readable(client, [*SCHEDULE_BLOCKS, (OUTSIDE_MODE, 2)]):
                self.tou_limits = TouLimits(max_power_kw=self.max_power_kw)
                self.capabilities.add("ctrl_tou")

    def decode_enum(self, key, raw):
        if key == "work_mode":
            if raw is None or not raw.is_integer():
                return None
            return self.work_mode_codes.get(int(raw), "other")
        if key == "running_state":
            # Assumption (unconfirmed): 0x1001 is the least significant bit.
            code = sum(int(bit) << i for i, bit in enumerate(raw))
            return RUNNING_STATES[code] if code < 4 else "other"
        return None

    def control_read_keys(self, key, *, preflight=False):
        """Read related state on demand; normal edits have no preflight reads."""
        if preflight:
            return set()
        if key == "work_mode":
            return {"work_mode", "remote_control"}
        if key in ("min_soc", "backup_soc"):
            keys = {"min_soc", "backup_soc"}
            if "battery_2" in self.capabilities:
                keys.update({"min_soc_2", "backup_soc_2"})
            return keys
        if key in BATTERY_2_ADDRESSES and "battery_2" in self.capabilities:
            return {key, key + "_2"}
        return {key}

    async def read_controls(self, client, keys=None):
        result = {}
        addresses = dict(self.control_addresses)
        if "battery_2" in self.capabilities:
            addresses.update(
                {k + "_2": a for k, a in BATTERY_2_ADDRESSES.items() if k in addresses}
            )
        if keys is not None and not set(keys) <= addresses.keys():
            raise InvalidResponse("Unsupported control read")
        for key, address in addresses.items():
            if keys is not None and key not in keys:
                continue
            raw = decode_float(
                await client.read_holding_registers(address, 2),
                kind="soc" if key in ("min_soc_2", "backup_soc_2") else "control",
            )
            result[key] = self.decode_enum(key, raw) if key == "work_mode" else raw
        if any(v is None for v in result.values()):
            raise InvalidResponse("Control state is incomplete")
        if "export_limit_enable" in result:
            result["export_limit_enable"] = float(result["export_limit_enable"] != 0)
        for key in ("grid_charge", "remote_control"):
            if key in result and result[key] not in (0, 1):
                raise InvalidResponse("Invalid switch state")
        return result

    @staticmethod
    def fresh_validity(today: date):
        """Validity written when the device's own window has already expired."""
        start = datetime.fromtimestamp(local_date_stamp(today), timezone.utc)
        return start, datetime.fromtimestamp(FAR_FUTURE, timezone.utc)

    async def read_schedule(self, client):
        blocks = [await client.read_holding_registers(a, n) for a, n in SCHEDULE_BLOCKS]
        outside = decode_float(await client.read_holding_registers(OUTSIDE_MODE, 2), kind="control")
        return decode_schedule(blocks, outside, self.tou_limits)

    def control_values(self, key, value):
        if key == "work_mode":
            codes = {name: code for code, name in self.work_mode_codes.items()}
            if value not in codes:
                raise InvalidResponse("Mode has no device code in this profile")
            return float(codes[value])
        return float(value)

    async def write_float(self, client, address, value):
        words = floats([value])
        await client.write_registers(address, words, retry_safe=address in RETRY_SAFE_ADDRESSES)
        actual = await client.read_holding_registers(address, 2)
        if actual != words:
            raise InvalidResponse("Write read-back differs")
        return decode_float(actual, kind="control")

    async def validate_write_retry(self, client, address, words, *, remote_owned=False):
        """Recheck a fixed level target against newly probed capabilities/state."""
        if not self.controls_supported or address not in RETRY_SAFE_ADDRESSES:
            raise InvalidResponse("Parameter is not eligible for write retry")
        value = decode_float(list(words), kind="control")
        if value is None:
            raise InvalidResponse("Invalid parameter retry value")
        if address in BATTERY_2_ADDRESSES.values() and "battery_2" not in self.capabilities:
            raise InvalidResponse("Battery 2 is no longer available")
        key = next((k for k, a in self.control_addresses.items() if a == address), None)
        if key is None:
            # A battery 2 copy shares its battery 1 key's range.
            key = next((k for k, a in BATTERY_2_ADDRESSES.items() if a == address), None)
        if key is not None and key not in self.control_keys:
            raise InvalidResponse("Parameter is no longer offered by the device")
        if key is not None:
            definition = next(c for c in CONTROLS if c.key == key)
            if definition.options:
                if self.decode_enum(key, value) not in definition.options:
                    raise InvalidResponse("Unsupported retry mode")
            else:
                low, high = definition.limits(self.max_power_kw)
                if not low <= value <= high:
                    raise InvalidResponse("Retry target exceeds current device limits")
                if definition.platform == "switch" and value not in (0, 1):
                    raise InvalidResponse("Invalid switch retry value")
        if address in (0x3046, 0x30B8, 0x314A, 0x314C):
            other = {0x3046: 0x314A, 0x30B8: 0x314C, 0x314A: 0x3046, 0x314C: 0x30B8}[address]
            bound = decode_float(await client.read_holding_registers(other, 2), kind="soc")
            if (
                bound is None
                or not 0 <= value <= 100
                or (value > bound if address in (0x3046, 0x30B8) else value < bound)
            ):
                raise InvalidResponse("Retry target conflicts with current battery SOC limits")
        if key == "work_mode":
            remote = decode_float(await client.read_holding_registers(0xC646, 2), kind="control")
            if remote != 0:
                raise InvalidResponse("Remote session prevents mode write retry")
        if address in (0xC648, HEARTBEAT_TIMEOUT, ESCAPE_MODE):
            mode = decode_float(await client.read_holding_registers(0x300C, 2), kind="control")
            if (
                mode is None
                or not mode.is_integer()
                or int(mode) not in self.work_mode_codes
                or self.decode_enum("work_mode", mode) == "time_of_use"
            ):
                raise InvalidResponse("Current mode prevents remote parameter retry")
            remote = decode_float(await client.read_holding_registers(0xC646, 2), kind="control")
            if remote not in (0, 1) or (remote == 1 and not remote_owned):
                raise InvalidResponse("Unowned remote session prevents parameter retry")
        if address == EXPORT_LIMIT_ENABLE and value not in (0, 1):
            raise InvalidResponse("Invalid export-enable retry value")
        if address in (HEARTBEAT_TIMEOUT, ESCAPE_MODE):
            expected = REMOTE_TIMEOUT_S if address == HEARTBEAT_TIMEOUT else REMOTE_ESCAPE
            if value != expected:
                raise InvalidResponse("Unsupported remote retry parameter")

    async def apply_control(self, client, key, value):
        changes = [(key, self.control_addresses[key], self.control_values(key, value))]
        if key in BATTERY_2_ADDRESSES and "battery_2" in self.capabilities:
            changes.append((key + "_2", BATTERY_2_ADDRESSES[key], float(value)))
        confirmed = {}
        for name, address, value in changes:
            raw = await self.write_float(client, address, value)
            confirmed[name] = self.decode_enum(name, raw) if name == "work_mode" else raw
        return confirmed

    async def write_schedule(self, client, schedule):
        blocks = encode_schedule(schedule, datetime.now().date())
        # Assumption (Q5): no TOU master switch; leave TOU mode while writing so a
        # half-written table is never executed. The caller restores the mode.
        await self.write_float(client, CONTROL_ADDRESSES["work_mode"], 0.0)
        for (address, _), words in zip(SCHEDULE_BLOCKS, blocks):
            await client.write_registers(address, words)
            if await client.read_holding_registers(address, len(words)) != words:
                raise InvalidResponse("TOU block read-back mismatch")
        await self.write_float(client, OUTSIDE_MODE, OUTSIDE_MODES.index(schedule.outside_mode))

    @staticmethod
    def schedules_equal(first, second):
        return (
            first.weekdays == second.weekdays
            and first.outside_mode == second.outside_mode
            and first.valid_from == second.valid_from
            and first.valid_until == second.valid_until
            and len(first.periods) == len(second.periods)
            and all(
                a.type == b.type
                and a.start == b.start
                and a.end == b.end
                and math.isclose(a.power, b.power, rel_tol=1e-6, abs_tol=1e-6)
                for a, b in zip(first.periods, second.periods)
            )
        )

    def work_mode_offered(self, mode):
        """Modes a user may select; smaller models only report peak shaving."""
        return mode != "peak_shaving" or (self.max_power_kw or 0) >= PEAK_SHAVING_MIN_KW

    async def enter_remote(self, client, power, restore_mode="self_consumption"):
        """Enable manual control first, then set its power.

        Verified on hardware (2026-10-26): 0xC648 is accepted only while 0xC646 = 1
        (otherwise it reads back unchanged or answers exception 3), and enabling
        switches the work mode to 4 (manual), which leave_remote undoes.
        """
        if self._saved_watchdog is None:
            self._saved_watchdog = {
                a: await client.read_holding_registers(a, 2)
                for a in (HEARTBEAT_TIMEOUT, ESCAPE_MODE)
            }
        try:
            for address, value in (
                (HEARTBEAT_TIMEOUT, REMOTE_TIMEOUT_S),
                (ESCAPE_MODE, REMOTE_ESCAPE),
                (CONTROL_ADDRESSES["remote_control"], 1),
                (CONTROL_ADDRESSES["remote_power"], power),
            ):
                await self.write_float(client, address, value)
        except EnergyLibError:
            # Undo a refused start: leave manual mode if it was entered, then give
            # the device back its own watchdog settings. If the exit cannot be
            # confirmed the session may be active and the watchdog stays as written.
            with contextlib.suppress(EnergyLibError):
                remote = await client.read_holding_registers(CONTROL_ADDRESSES["remote_control"], 2)
                if decode_float(remote, kind="control") != 0:
                    await self.leave_remote(client, restore_mode)
                else:
                    await self._restore_watchdog(client)
            raise

    async def _restore_watchdog(self, client):
        """Give the device back its watchdog settings once no session is active.

        Verified on hardware (2026-10-26): the watchdog stays armed after manual
        control is off, so 60 s after the last heartbeat it forced self-consumption
        over a mode chosen meanwhile. Unknown originals (e.g. after a restart) with
        our own settings still in place get escape 0, "keep running".
        """
        saved, self._saved_watchdog = self._saved_watchdog, None
        if saved is not None:
            for address, words in saved.items():
                await client.write_registers(address, words)
            return
        timeout, escape = [
            decode_float(await client.read_holding_registers(a, 2), kind="control")
            for a in (HEARTBEAT_TIMEOUT, ESCAPE_MODE)
        ]
        if timeout == REMOTE_TIMEOUT_S and escape == REMOTE_ESCAPE:
            await self.write_float(client, ESCAPE_MODE, 0)

    async def heartbeat(self, client, value):
        # Assumption (Q3): writing 1.0 (Alive) to 0xC068 refreshes the EMS watchdog.
        words = floats([1.0])
        await client.write_registers(HEARTBEAT, words)
        if await client.read_holding_registers(HEARTBEAT, 2) == words:
            return
        # Firmware may consume the Alive flag at once (it reads 0 at rest); then the
        # still-active session is the evidence that the keepalive was accepted.
        remote = await client.read_holding_registers(CONTROL_ADDRESSES["remote_control"], 2)
        if decode_float(remote, kind="control") != 1:
            raise InvalidResponse("Heartbeat not confirmed")

    async def leave_remote(self, client, restore_mode="self_consumption"):
        """Clear the setpoint, leave manual control and restore the previous mode.

        0xC648 can only be written while manual control is on, so it is cleared
        first. Firmware may keep manual mode or switch to self-consumption when
        manual control is disabled; both require restoration for our own session.
        """
        owned = self._saved_watchdog is not None
        with contextlib.suppress(DeviceException):
            await client.write_registers(CONTROL_ADDRESSES["remote_power"], floats([0.0]))
        await self.write_float(client, CONTROL_ADDRESSES["remote_control"], 0)
        mode = decode_float(
            await client.read_holding_registers(CONTROL_ADDRESSES["work_mode"], 2), kind="control"
        )
        if mode == REMOTE_MODE_CODE or (owned and mode == 0):
            code = self.control_values("work_mode", restore_mode)
            if mode != code:
                await self.write_float(client, CONTROL_ADDRESSES["work_mode"], code)
        await self._restore_watchdog(client)
