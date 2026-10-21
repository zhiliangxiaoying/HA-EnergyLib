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

import math
import struct
from datetime import date, datetime, timezone

from ..legacy import DeviceIdentity, EnergyLibDevice, decode_float
from ..model.points import CONTROLS, RUNNING_STATES, RegisterMapping
from ..model.tou import OUTSIDE_MODES, WEEKDAYS, TouLimits, TouSchedule, minute
from ..transport.modbus_tcp import DeviceException, InvalidResponse
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
CONTROL_ADDRESSES = {
    "work_mode": 0x300C,
    "backup_soc": 0x314A,
    "min_soc": 0x3046,
    "grid_charge": 0x322A,
    "export_limit": 0x3004,
    "remote_control": 0xC646,
    "remote_power": 0xC648,
}
EXPORT_LIMIT_ENABLE = 0x3002
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
FAR_FUTURE = int(datetime(2099, 12, 31, tzinfo=timezone.utc).timestamp())
REMOTE_TIMEOUT_S, REMOTE_ESCAPE = 60, 1  # escape 1 = self-consumption


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

    def __init__(self):
        self.capabilities = set()
        self.mappings = {}
        self.single_battery_confirmed = False
        self.controls_supported = False
        self.control_keys = frozenset()
        self.max_power_kw = None
        self.tou_limits = None

    async def identify(self, client) -> DeviceIdentity:
        return await EnergyLibDevice(client).identify()

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
        self.capabilities = {"battery"}
        if identity.machine_type == 0:
            self.capabilities.add("three_phase")
        probes = {}
        for key, address in (
            ("pv_count", 0xC05E),
            ("battery_2", 0x3036),
            ("port_use", 0xC264),
            ("bms", 0xB000),
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
                await client.read_discrete_inputs(GRID_ALARM_BIT, 1)
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
        if self.max_power_kw and await self._readable(client, [(a, 2) for a in registers]):
            self.controls_supported = True
            self.control_keys = frozenset(c.key for c in CONTROLS)
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

    async def read_controls(self, client):
        result = {}
        for key, address in CONTROL_ADDRESSES.items():
            raw = decode_float(await client.read_holding_registers(address, 2), kind="control")
            result[key] = self.decode_enum(key, raw) if key == "work_mode" else raw
        if "battery_2" in self.capabilities:
            for key, address in (("min_soc_2", 0x30B8), ("backup_soc_2", 0x314C)):
                result[key] = decode_float(
                    await client.read_holding_registers(address, 2), kind="soc"
                )
        if any(v is None for v in result.values()):
            raise InvalidResponse("Control state is incomplete")
        for key in ("grid_charge", "remote_control"):
            if result[key] not in (0, 1):
                raise InvalidResponse("Invalid switch state")
        return result

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
        await client.write_registers(address, words)
        if await client.read_holding_registers(address, 2) != words:
            raise InvalidResponse("Write read-back differs")

    async def apply_control(self, client, key, value):
        changes = [(CONTROL_ADDRESSES[key], self.control_values(key, value))]
        if key in ("min_soc", "backup_soc") and "battery_2" in self.capabilities:
            changes.append((0x30B8 if key == "min_soc" else 0x314C, float(value)))
        if key == "export_limit":
            changes.insert(0, (EXPORT_LIMIT_ENABLE, 1.0))  # 1 = all-day export limit
        for address, value in changes:
            await self.write_float(client, address, value)

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

    async def enter_remote(self, client, power):
        for address, value in (
            (HEARTBEAT_TIMEOUT, REMOTE_TIMEOUT_S),
            (ESCAPE_MODE, REMOTE_ESCAPE),
            (CONTROL_ADDRESSES["remote_power"], power),
            (CONTROL_ADDRESSES["remote_control"], 1),
        ):
            await self.write_float(client, address, value)

    async def heartbeat(self, client, value):
        # Assumption (Q3): writing 1.0 (Alive) to 0xC068 refreshes the EMS watchdog.
        await self.write_float(client, HEARTBEAT, 1.0)

    async def leave_remote(self, client):
        await self.write_float(client, CONTROL_ADDRESSES["remote_control"], 0)
