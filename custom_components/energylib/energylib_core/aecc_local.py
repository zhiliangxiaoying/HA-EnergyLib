"""AECC devices over their local interface, as standard measurements.

Read-only, except a Shenggerui inverter behind the EMS once the user allows
control (see "Shenggerui controls" below).

Sources: AECC's local interface document (EMS local interface, 2026-11-16) and,
for Shenggerui inverters, the Shenggerui off/on-grid storage inverter Modbus
protocol V2.7.1 (standard edition) with the manufacturer's answers on it.

Two ways of reading, chosen once at setup:

- Shenggerui inverter behind the EMS (device type 131 and a first register
  read through the pass-through succeeds): EVERY value comes from the
  inverter's registers, read through the EMS RTU pass-through
  (`DataTransmission`, function 0x03 only, slave `TS_SLAVE`). The live block
  every poll, the energy counters once a minute. The JSON summary is not used.
- Any other AECC device: the system summary of `EnergyParameter`
  (`SSumInfoList`).

`EnergycontrolparametersList` is read every 5 minutes for the diagnostics only.

Shenggerui controls (only with the user's "allow control", every write read
back): inverter on/off 0x9C40 (confirmed from the startup state 0x7594),
backup SOC 0xA09E (switch to grid below it), minimum SOC 0xA09C (discharge
cut-off), grid charging 0xA02E (2 mixed / 3 PV only) and the time-of-use plan
0xA0A0-0xA0AD (3 charge + 3 discharge periods, each kind with its enable; no
power, no weekdays). The EMS mode is PENDING: the manufacturer will add a mode
register once our modes are defined; nothing writes 0xA028/0xA034/0xA041.
No remote charge/discharge (the FE external control block is not on this
model). Nothing is written to the summary-only devices.

Directions:
- Registers (manufacturer-confirmed): battery current 0x7531 > 0 discharging;
  grid-side CT power 0x7584-0x7586 > 0 buying (valid when the CT direction
  setting 0xA101 matches the installation).
- Summary, NOT VERIFIED (user decision 2026-11-19, kept here): output
  `TotalBatteryOutputPower` > 0 discharging, minus `TotalChargePower`;
  `MeterTotalActivePower` > 0 buying; `TotalGridOutputPower` > 0 selling.
Home load: registers give AC OUT (backup) plus the CT-measured normal load; the
summary has no load field, so it is derived from the sources.
"""

import asyncio
import math
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace

from .model.alarms import AlarmTracker
from .model.points import ALARM_SUMMARIES, MEASUREMENTS, RegisterMapping
from .model.remote import RemoteDraft
from .model.tou import WEEKDAYS, TouLimits, TouPeriod, TouSchedule
from .profiles.shenggerui_alarms import ALARM_BITS, BATTERY_MISSING_BITS, alarm_bits, describe_alarm
from .runtime import ControlError, PartialWrite
from .transport.aecc_local import AeccLocalError

SUMMARY = "SSumInfoList"
# Sign of each summary field in the standard convention (battery + discharge, grid + import).
BATTERY_OUTPUT_SIGN = 1
METER_SIGN = 1
GRID_OUTPUT_SIGN = -1
# The summary directions above are assumptions; the register directions are the
# manufacturer's (see the module docstring).
POWER_DIRECTION_VERIFIED = False
KEYS = (
    "solar_power",
    "battery_power",
    "grid_power",
    "load_power",
    "battery_total_soc",
    "backup_load_power",
)

# Shenggerui inverter behind the EMS (device type 131 in AECC's tables).
TS_DEVICE_TYPE = 131
TS_SLAVE = 1  # protocol default (0xA032 sets 1-254)
TS_ENERGY_INTERVAL = 60  # seconds between energy counter reads
# Live blocks, read every poll: (first address, register count), at most 32 words each.
TS_LIVE_BLOCKS = (
    (0x7530, 23),  # battery V/I/SOC, PV1-PV4
    (0x754C, 19),  # AC OUT (backup) and CT normal load per phase, AC OUT total
    (0x756A, 3),  # grid voltage A, frequency
    (0x7584, 3),  # grid-side CT power per phase
    (0x7594, 2),  # startup and output state
    (0x75A5, 8),  # alarm bits
)
TS_ENERGY_BLOCKS = (
    (0xCB52, 8),  # lifetime battery charge/discharge, PV, AC OUT consumption
    (0xCB70, 4),  # lifetime grid export/import (with CT or meter)
)
TS_IDENTITY = (0xC738, 1)  # machine code
TS_KEYS = (
    "solar_power",
    "battery_power",
    "grid_power",
    "load_power",
    "battery_total_soc",
    "backup_load_power",
    "solar_energy",
    "battery_charge_energy",
    "battery_discharge_energy",
    "load_energy",
    "grid_import_energy",
    "grid_export_energy",
    "running_state",
    "battery_voltage",
    "battery_current",
    "pv1_power",
    "pv2_power",
    "pv3_power",
    "grid_voltage_a",
    "grid_frequency",
)
# Control parameters read for the diagnostics (names from the document).
CONTROL_FIELDS = (
    "EnergyManagement",
    "ScheduledModeEnabled",
    "MeterSelector",
    "SystemMaxPowerLimit",
    "PowerRegulationOperatingPoint",
    "DeviceGridInputPowerLimit",
    "PeakShavingConfig",
    "BaseDischargePower",
    "ValleyChargePowerStrategy",
    "SystemAcChargePowerLimits",
    "PeriodValidTimestamp",
    *(f"PowerControlPeriod{i}" for i in range(1, 17)),
)
CONTROL_INTERVAL = 300
# Shenggerui controls.
TS_CONTROL_KEYS = frozenset({"inverter_power", "backup_soc", "min_soc", "grid_charge"})
TS_SETTINGS_BLOCK = (
    0xA09C,
    18,
)  # 0xA09C stop SOC .. 0xA09E inverter-to-grid SOC .. TOU 0xA0A0-0xA0AD
TS_CHARGE_PRIORITY = 0xA02E  # 0 PV first, 1 grid first (to be removed), 2 mixed, 3 PV only
TS_ON_OFF = 0x9C40  # write only: 1 on, 0 off
TS_TOU = 0xA0A0
TS_TOU_LIMITS = TouLimits(
    max_charge_periods=3,
    max_discharge_periods=3,
    max_power_kw=0.0,  # periods have no power on this model
    supports_cross_midnight=True,  # manufacturer: across midnight is supported
)
TS_CONTROL_INTERVAL = 30
POWER_CONFIRM_S = 20
POWER_POLL_S = 2
ALARM_MAPPING = RegisterMapping("active_alarms", 0, count=ALARM_BITS, function=3, unit=TS_SLAVE)
ALARM_KEYS = tuple(p.key for p in ALARM_SUMMARIES)
# Grid voltage above which "inverter state" counts as on grid (hybrid running).
GRID_PRESENT_V = 100


def _number(raw):
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _kw(raw):
    value = _number(raw)
    return None if value is None else value / 1000


def summary(response):
    """The system summary object, or None when the answer has none."""
    item = response.get(SUMMARY) if isinstance(response, dict) else None
    if isinstance(item, list):
        item = item[0] if item and isinstance(item[0], dict) else None
    return item if isinstance(item, dict) else None


def decode(response):
    """Standard values (kW, %) from one EnergyParameter answer; None when unknown."""
    item = summary(response) or {}
    solar = _kw(item.get("TotalPVPower"))
    battery_raw = _kw(item.get("TotalBatteryOutputPower"))
    charge = _kw(item.get("TotalChargePower"))
    if battery_raw is None:
        battery = None
    elif charge is not None and battery_raw >= 0 and charge > 0:
        # Output and charge are separate, non-negative fields.
        battery = BATTERY_OUTPUT_SIGN * (battery_raw - charge)
    else:
        battery = BATTERY_OUTPUT_SIGN * battery_raw
    meter = _kw(item.get("MeterTotalActivePower"))
    output = _kw(item.get("TotalGridOutputPower"))
    if meter is not None:
        grid = METER_SIGN * meter
    elif output is not None:
        grid = GRID_OUTPUT_SIGN * output
    else:
        grid = None
    soc = _number(item.get("AverageBatteryAverageSOC"))
    parts = (solar, battery, grid)
    return {
        "solar_power": solar,
        "battery_power": battery,
        "grid_power": grid,
        # What the sources deliver is what the home uses (no load field).
        "load_power": None if None in parts else sum(parts),
        "battery_total_soc": soc if soc is None or 0 <= soc <= 100 else None,
        "backup_load_power": _kw(item.get("TotalBackUpPower")),
    }


def _register(blocks, address, count=1, *, signed=False, scale=1.0):
    """A register value from the blocks read; u32 values are low word first."""
    for start, values in blocks.items():
        offset = address - start
        if values is not None and 0 <= offset and offset + count <= len(values):
            if count == 2:
                raw = values[offset] | values[offset + 1] << 16
            else:
                raw = values[offset]
                if signed and raw >= 0x8000:
                    raw -= 0x10000
            return raw if scale == 1 else round(raw * scale, 3)
    return None


def _sum(values):
    return None if None in values else sum(values)


def decode_ts(blocks):
    """Standard values from Shenggerui registers ({first address: [registers]}).

    A block that failed is None: its values are unknown, never zero.
    """

    def r(address, count=1, **kw):
        return _register(blocks, address, count, **kw)

    def kw(watts):
        return None if watts is None else round(watts / 1000, 3)

    volts, amps = r(0x7530, scale=0.1), r(0x7531, signed=True, scale=0.1)
    pv = [r(address) for address in (0x753D, 0x7540, 0x7543, 0x7546)]
    backup = r(0x755E, signed=True)
    normal = _sum([r(address, signed=True) for address in (0x754F, 0x7556, 0x755D)])
    grid = _sum([r(address, signed=True) for address in (0x7584, 0x7585, 0x7586)])
    soc = r(0x7532)
    grid_volts = r(0x756A, scale=0.1)
    startup = r(0x7594)
    if startup is None:
        running = None
    elif startup == 3:  # inverter state: hybrid on grid while the grid is there
        running = (
            "on_grid" if grid_volts is not None and grid_volts > GRID_PRESENT_V else "off_grid"
        )
    else:
        running = {1: "standby", 2: "on_grid"}.get(startup, "other")  # 0: initialising
    return {
        "solar_power": kw(_sum(pv)),
        # Battery current > 0 is discharging, as the standard battery power.
        "battery_power": None if None in (volts, amps) else round(volts * amps / 1000, 3),
        "grid_power": kw(grid),
        # AC OUT (backup) plus the normal load measured by the CT.
        "load_power": kw(_sum([backup, normal])),
        "battery_total_soc": soc if soc is None or soc <= 100 else None,
        "backup_load_power": kw(backup),
        "solar_energy": r(0xCB56, 2, scale=0.1),
        "battery_charge_energy": r(0xCB52, 2, scale=0.1),
        "battery_discharge_energy": r(0xCB54, 2, scale=0.1),
        "load_energy": r(0xCB58, 2, scale=0.1),  # AC OUT consumption
        "grid_import_energy": r(0xCB72, 2, scale=0.1),
        "grid_export_energy": r(0xCB70, 2, scale=0.1),
        "running_state": running,
        "battery_voltage": volts,
        "battery_current": amps,
        "pv1_power": kw(pv[0]),
        "pv2_power": kw(pv[1]),
        "pv3_power": kw(pv[2]),
        "grid_voltage_a": grid_volts,
        "grid_frequency": r(0x756C, scale=0.01),
    }


def _tou_time(word):
    hour, minute = word >> 8, word & 0xFF
    if hour > 23 or minute > 59:
        raise ValueError("Invalid TOU time")
    return f"{hour:02}:{minute:02}"


def _tou_word(text):
    return int(text[:2]) << 8 | int(text[3:])


def decode_tou(words):
    """(schedule, enables) from the 14 words 0xA0A0-0xA0AD.

    A period whose start equals its end is not set (manufacturer).
    """
    periods, enables = [], {}
    for kind, offset in (("charge", 0), ("discharge", 7)):
        enables[kind] = bool(words[offset])
        for i in range(3):
            start, end = words[offset + 1 + 2 * i], words[offset + 2 + 2 * i]
            if start != end:
                periods.append(TouPeriod(kind, _tou_time(start), _tou_time(end), 0.0))
    return TouSchedule(
        tuple(sorted(periods, key=lambda p: (p.start, p.type))), WEEKDAYS, None
    ), enables


def encode_tou(schedule):
    """The 14 words 0xA0A0-0xA0AD; a kind is enabled when it has a period."""
    words = []
    for kind in ("charge", "discharge"):
        mine = [p for p in schedule.periods if p.type == kind]
        words.append(1 if mine else 0)
        for i in range(3):
            words += [_tou_word(mine[i].start), _tou_word(mine[i].end)] if i < len(mine) else [0, 0]
    return words


class AeccProfile:
    """What the rest of the integration asks a profile.

    No controls by default; `AeccLocalRuntime.initialize` turns the Shenggerui
    controls on for this instance when its registers answer.
    """

    id = "aecc_local_v1"
    version = 1
    namespace = "aecc"
    manufacturer = "EnergyLIB"
    device_name = "EnergyLIB"
    controls_supported = False
    controls_hw_verified = False
    power_direction_verified = POWER_DIRECTION_VERIFIED
    max_power_kw = None
    control_keys = frozenset()
    remote_supported = False  # no remote charge/discharge (task, drafts, buttons)
    tou_limits = None

    def __init__(self):
        self.capabilities = set()

    def describe_alarm(self, function, unit, bit):
        return describe_alarm(self.id, function, unit, bit)


@dataclass(frozen=True)
class AeccIdentity:
    serial: str
    device_type: int | None = None
    protocol_version: str = "aecc-local-json"
    arm_version: str = ""
    inverter_code: int | None = None  # Shenggerui machine code (0xC738)

    @property
    def machine_type(self):
        return self.device_type

    @property
    def arm_firmware_display(self):
        return ""  # not reported by this interface


class AeccLocalRuntime:
    """Read-only runtime with the attributes the coordinator and entities use."""

    def __init__(self, client, serial, device_type=None):
        self.client = client
        self.profile = AeccProfile()
        self.identity = AeccIdentity(serial, device_type)
        self.values = {}
        self.attributes = {}
        self.controls = {}
        self.schedule = None
        self.allow_control = False
        self.last_command = "none"
        self.last_control_timing = None
        self.last_frame_count = 0
        self.poll_deferred = False
        self.capabilities_changed = False
        self.alarms = AlarmTracker(self.profile.describe_alarm)
        self.draft = RemoteDraft()
        self.remote_status = "idle"
        self.remote_end_time = None
        self.remote_deadline = None
        self.state_changed = None
        self._timestamps = {}
        # Shenggerui registers through the pass-through: decided at setup.
        self.registers = False
        self.register_error = None
        self.control_parameters = None
        self._energy = {}
        self._next_energy = 0.0
        self._next_controls = 0.0
        self._next_settings = 0.0
        self._startup = None
        self._tou_enabled = {}
        self._lock = asyncio.Lock()

    @property
    def unique_id(self):
        return f"{self.profile.namespace}_{self.identity.serial}"

    @property
    def device_name(self):
        return f"{self.profile.device_name} {self.identity.serial[-4:]}"

    def points(self):
        if not self.registers:
            return [MEASUREMENTS[key] for key in KEYS]
        keys = list(TS_KEYS) + list(ALARM_KEYS)  # alarms from the tracker
        if self.allow_control:
            keys.append("tou_schedule")
        return [MEASUREMENTS[key] for key in keys]

    async def initialize(self, expected_serial=None):
        """The EMS answers with a system summary (the serial is from discovery).

        With a Shenggerui inverter behind it (type 131), a first register read
        through the pass-through decides that every value comes from registers.
        """
        if summary(await self.client.energy_parameters()) is None:
            raise AeccLocalError("The answer has no system summary")
        if self.identity.device_type != TS_DEVICE_TYPE:
            return
        try:
            (code,) = await self.client.read_registers(TS_SLAVE, *TS_IDENTITY)
            await self.client.read_registers(TS_SLAVE, *TS_LIVE_BLOCKS[0])
        except AeccLocalError as err:
            self.register_error = str(err)
            return
        self.registers = True
        self.profile.capabilities = {"registers"}
        self.profile.controls_supported = True
        self.profile.control_keys = TS_CONTROL_KEYS
        self.profile.tou_limits = TS_TOU_LIMITS
        self.identity = replace(
            self.identity, protocol_version="aecc-pass-through-modbus", inverter_code=code
        )

    async def _read_blocks(self, blocks):
        """{start: registers or None}, the number read, the last error (or None)."""
        values, error = {}, None
        for start, count in blocks:
            try:
                values[start] = await self.client.read_registers(TS_SLAVE, start, count)
            except AeccLocalError as err:
                values[start] = None  # this block unknown, the others still shown
                error = str(err)
        read = sum(v is not None for v in values.values())
        if not read:
            raise AeccLocalError(error or "No register answers")
        return values, read, error

    async def refresh(self, enabled, *, force=False):
        async with self._lock:
            return await self._refresh(enabled, force=force)

    async def _refresh(self, enabled, *, force=False):
        now = time.monotonic()
        if self.registers:
            try:
                blocks, frames, error = await self._read_blocks(TS_LIVE_BLOCKS)
            except AeccLocalError as err:
                self.register_error = str(err)
                raise
            self._startup = _register(blocks, 0x7594)
            alarm_words = blocks.get(0x75A5)
            self.alarms.observe(
                ALARM_MAPPING, None if alarm_words is None else alarm_bits(alarm_words)
            )
            if force or now >= self._next_energy:
                self._next_energy = now + TS_ENERGY_INTERVAL
                try:
                    self._energy, read, energy_error = await self._read_blocks(TS_ENERGY_BLOCKS)
                    frames += read
                    error = error or energy_error
                except AeccLocalError as err:
                    self._energy = {}  # counters unknown until the next read
                    error = error or str(err)
            values = decode_ts({**blocks, **self._energy})
            values.update(self._alarm_values())
            if self.allow_control and (force or now >= self._next_settings):
                self._next_settings = now + TS_CONTROL_INTERVAL
                try:
                    await self._read_controls()
                    frames += 2
                except AeccLocalError as err:
                    error = error or str(err)
                    self.controls = {}
                    self.schedule = None
                    # No stale plan for the overview's card while it is unknown.
                    self.attributes.pop("tou_schedule", None)
            if self.schedule is not None:
                values["tou_schedule"] = "configured" if self.schedule.periods else "empty"
            # The last failure of this poll; None when every read of it succeeded.
            self.register_error = error
        else:
            values = decode(await self.client.energy_parameters())
            frames = 1
        if now >= self._next_controls:
            self._next_controls = now + CONTROL_INTERVAL
            try:
                self.control_parameters = await self.client.control_parameters(CONTROL_FIELDS)
            except AeccLocalError:
                self.control_parameters = None  # not offered by this device
            frames += 1
        self.values = values
        self.last_frame_count = frames
        return {key: self.values.get(key) for key in enabled}

    def _alarm_values(self):
        values, attributes = self.alarms.snapshot([ALARM_MAPPING])
        codes = {int(a["code"], 16) for a in attributes["alarms"]}
        attributes["battery_disconnected"] = (
            True if codes & BATTERY_MISSING_BITS else False if attributes["complete"] else None
        )
        attributes["grid_disconnected"] = None  # no grid-loss alarm bit on this model
        for key in ALARM_KEYS:
            self.attributes[key] = (
                attributes
                if key in ("active_alarms", "alarm_status")
                else {k: attributes[k] for k in ("complete", "unknown_count", "info_count")}
            )
        return values

    def _power_state(self, startup):
        """Inverter on/off from the startup state: mains or inverter = on, standby = off."""
        return True if startup in (2, 3) else False if startup == 1 else None

    async def _read_controls(self):
        words = await self.client.read_registers(TS_SLAVE, *TS_SETTINGS_BLOCK)
        (priority,) = await self.client.read_registers(TS_SLAVE, TS_CHARGE_PRIORITY, 1)
        try:
            self.schedule, self._tou_enabled = decode_tou(words[4:18])
        except ValueError:
            self.schedule, self._tou_enabled = None, {}
        self.controls = {
            "min_soc": words[0],
            "backup_soc": words[2],
            "grid_charge": priority != 3,
            "inverter_power": self._power_state(self._startup),
        }
        if self.schedule is not None:
            self.attributes["tou_schedule"] = {
                **self.schedule.as_dict(),
                "charge_enabled": self._tou_enabled.get("charge"),
                "discharge_enabled": self._tou_enabled.get("discharge"),
                # What the overview's plan card offers on this model.
                "power_supported": False,
                "weekdays_supported": False,
                "max_per_type": TS_TOU_LIMITS.max_charge_periods,
                "cross_midnight": TS_TOU_LIMITS.supports_cross_midnight,
            }
        else:
            self.attributes.pop("tou_schedule", None)
        return words, priority

    def diagnostics(self):
        """Extra diagnostics for this source (no identities)."""
        return {
            "registers": self.registers,
            "inverter_code": self.identity.inverter_code,
            "register_error": self.register_error,
            "control_parameters": self.control_parameters,
        }

    @contextmanager
    def prioritize_control(self):
        yield

    def _gate(self):
        if not (self.registers and self.allow_control):
            raise ControlError("This device is read-only")

    async def _write(self, address, values):
        """Write, then the caller reads back; a lost answer is an unknown outcome."""
        try:
            await self.client.write_registers(TS_SLAVE, address, values)
        except AeccLocalError as err:
            self.last_command = "uncertain"
            raise PartialWrite(f"Write to 0x{address:04X} not confirmed") from err

    async def set_control(self, key, value):
        self._gate()
        if key not in TS_CONTROL_KEYS:
            raise ControlError("Unsupported control")
        if key == "inverter_power":
            await self._set_power(bool(value))
            return
        async with self._lock:
            if key in ("min_soc", "backup_soc"):
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or value != int(value)
                ):
                    raise ControlError("SOC must be a whole percentage")
                value = int(value)
                if not 0 <= value <= 100:
                    raise ControlError("SOC outside 0-100")
                low = value if key == "min_soc" else self.controls.get("min_soc")
                high = value if key == "backup_soc" else self.controls.get("backup_soc")
                if low is not None and high is not None and low > high:
                    raise ControlError("Minimum SOC must not exceed backup SOC")
                await self._write(0xA09C if key == "min_soc" else 0xA09E, [value])
                expected = value
            else:  # grid_charge
                expected = 2 if value else 3
                await self._write(TS_CHARGE_PRIORITY, [expected])
            words, priority = await self._read_controls()
            actual = words[0] if key == "min_soc" else words[2] if key == "backup_soc" else priority
            if actual != expected:
                self.last_command = "uncertain"
                raise PartialWrite("Read-back differs from the value written")
            self.last_command = "confirmed"

    async def _set_power(self, on):
        async with self._lock:
            (startup,) = await self.client.read_registers(TS_SLAVE, 0x7594, 1)
            if self._power_state(startup) == on:
                self.last_command = "confirmed"
                return
            await self._write(TS_ON_OFF, [1 if on else 0])
        # 0x9C40 cannot be read: the startup state confirms the command. Polls go
        # on meanwhile (the lock is only held for each read).
        deadline = time.monotonic() + POWER_CONFIRM_S
        while time.monotonic() < deadline:
            await asyncio.sleep(POWER_POLL_S)
            try:
                async with self._lock:
                    (startup,) = await self.client.read_registers(TS_SLAVE, 0x7594, 1)
            except AeccLocalError:
                continue
            self._startup = startup
            if self._power_state(startup) == on:
                self.controls["inverter_power"] = on
                self.last_command = "confirmed"
                return
        self.last_command = "uncertain"
        raise PartialWrite("Inverter power state not confirmed")

    async def set_schedule(self, data, *, activate=False):
        self._gate()
        if not isinstance(data, dict):
            raise ControlError("Invalid plan")
        if set(data.get("weekdays", WEEKDAYS)) != set(WEEKDAYS):
            raise ControlError("This inverter runs its plan every day")
        if data.get("outside_mode") is not None:
            raise ControlError("This inverter has no mode outside the periods")
        try:
            schedule = TouSchedule.parse(
                {k: v for k, v in data.items() if k == "periods"}, TS_TOU_LIMITS
            )
        except ValueError as err:
            raise ControlError(str(err)) from err
        words = encode_tou(schedule)
        async with self._lock:
            await self._write(TS_TOU, words)
            readback, _priority = await self._read_controls()
            if readback[4:18] != words:
                self.last_command = "uncertain"
                raise PartialWrite("TOU read-back mismatch")
            self.last_command = "confirmed"

    async def set_battery_power(self, power_kw, duration):
        self._gate()
        raise ControlError("No remote charge/discharge on this device")

    async def stop_remote(self):
        self._gate()
        raise ControlError("No remote charge/discharge on this device")

    def control_options(self, definition, *, restoring=False):
        return ()

    def schedule_status(self, now):
        """The plan sensor: the active period kind, or why none applies."""
        if self.schedule is None:
            return None
        enabled = [p for p in self.schedule.periods if self._tou_enabled.get(p.type)]
        if not enabled:
            return "inactive"
        period = replace(self.schedule, periods=tuple(enabled)).active(now)
        return period.type if period else "outside_period"

    async def close(self):
        await self.client.close()
