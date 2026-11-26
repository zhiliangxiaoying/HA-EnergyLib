"""AECC devices over their local interface, as standard measurements.

Read-only, except a Shenggerui inverter behind the EMS once the user allows
control (see "Shenggerui controls" below).

Sources: AECC's local interface document (EMS local interface, 2026-11-16) and,
for Shenggerui inverters, the Shenggerui off/on-grid storage inverter Modbus
protocol V2.7.1.1 (EnergyLib custom edition, 2026-11-20) with the manufacturer's
answers on V2.7.1.

Two ways of reading, chosen once at setup:

- Shenggerui inverter behind the EMS (device type 131 and a first register
  read through the pass-through succeeds): EVERY value comes from the
  inverter's registers, read through the EMS RTU pass-through
  (`DataTransmission`, function 0x03 only, slave `TS_SLAVE`). The live block
  every poll, the energy counters once a minute. The JSON summary is not used.
- Any other AECC device: the system summary of `EnergyParameter`
  (`SSumInfoList`).

`EnergycontrolparametersList` is read every 5 minutes for the diagnostics only.

Devices the EMS manages (`EnergyParameter` lists, read only; the document has no
command for them): EV chargers (`ChargerInfoList`) become the standard EV
charger points of this device, summed over chargers and connectors; smart plugs
(`PlugInfoList`) and water heaters / heat pumps (`HotInfoList`) become their own
devices, one per serial number. With a Shenggerui inverter the summary is read
for them every poll while the EMS reports any, else every 5 minutes to notice
new ones. A changed set of accessories rebuilds the entities.

Shenggerui controls (only with the user's "allow control", every write read
back, never retried): inverter on/off 0x9C40 (confirmed from the startup state
0x7594), EMS mode 0xA034 (10 self-consumption, 11 time-of-use, 12 sell first,
13 backup first; valid in Hybrid supply, so 0xA028 is set to 3 first), backup SOC
0xA09E (switch to grid below it), minimum SOC 0xA09C (discharge cut-off), grid
charging 0xA10D (modes 10-13), grid purchase limit 0xA108, the time-of-use plan
0xA0A0-0xA0AD (3 charge + 3 discharge periods, each kind with its enable; no
power, no weekdays) and forced charge/discharge 0xA109-0xA10C (power, duration
timed by the inverter, enable, remaining time; a change while it runs is
ignored by the device, so a new task stops the running one first). Other
0xA034 values (the earlier feed-in settings 0-5, grid certification 8) are read
as "other". Nothing is written to the summary-only devices.

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
from datetime import datetime, timedelta, timezone

from .model.alarms import AlarmTracker
from .model.points import ALARM_SUMMARIES, CONTROLS, MEASUREMENTS, RegisterMapping
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
    "work_mode",  # 0xA034, every 30 s
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
TS_CONTROL_KEYS = frozenset(
    {
        "inverter_power",
        "work_mode",
        "backup_soc",
        "min_soc",
        "grid_charge",
        "grid_import_limit",
    }
)
# 0xA028 load supply priority .. 0xA02E charge priority .. 0xA034 grid feed setting.
TS_MODE_BLOCK = (0xA028, 13)
TS_SUPPLY_PRIORITY, TS_GRID_SETTING, TS_HYBRID = 0xA028, 0xA034, 3
TS_MODE_CODES = {10: "self_consumption", 11: "time_of_use", 12: "feed_in_priority", 13: "backup"}
# 0xA108 purchase limit (W), 0xA109 forced power (W, S16, > 0 discharging), 0xA10A
# duration (s), 0xA10B enable, 0xA10C remaining (s, read only), 0xA10D grid charging.
TS_DISPATCH_BLOCK = (0xA108, 6)
TS_IMPORT_LIMIT, TS_FORCE_POWER, TS_FORCE_ENABLE, TS_GRID_CHARGE = 0xA108, 0xA109, 0xA10B, 0xA10D
TS_FORCE_MAX_S = 64800  # 18 h
# No rated-power register in V2.7.1.1: the forced power's own range is the limit.
TS_MAX_POWER_KW = 32.767
TS_SETTINGS_BLOCK = (
    0xA09C,
    18,
)  # 0xA09C stop SOC .. 0xA09E inverter-to-grid SOC .. TOU 0xA0A0-0xA0AD
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
# Devices the EMS manages: kind -> (list in EnergyParameter, serial field).
ACCESSORY_LISTS = {"plug": ("PlugInfoList", "PlugSN"), "heater": ("HotInfoList", "HotSN")}
CHARGER_LIST = "ChargerInfoList"
CONNECTOR_STATES = {0: "off", 1: "preparing", 2: "charging", 3: "finished"}
CHARGER_KEYS = ("ev_charge_power", "ev_charger_status", "ev_connector_energy")


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


def _items(response, name):
    """The dict items of one device list of an EnergyParameter answer."""
    items = response.get(name) if isinstance(response, dict) else None
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def _flag(raw):
    value = _number(raw)
    return {0: False, 1: True}.get(value) if value is not None else None


def _scaled(raw, factor):
    value = _number(raw)
    return None if value is None else round(value * factor, 3)


def accessory_keys(response):
    """{(kind, serial)} of the plugs and water heaters, plus ("charger", "") with a charger."""
    keys = {
        (kind, str(item[field]))
        for kind, (name, field) in ACCESSORY_LISTS.items()
        for item in _items(response, name)
        if item.get(field) not in (None, "")
    }
    if _items(response, CHARGER_LIST):
        keys.add(("charger", ""))
    return frozenset(keys)


def decode_chargers(response):
    """(values, attributes) of the EV charger points; ({}, None) without a charger.

    Power and energy are summed over chargers and connectors. The state is the
    most active connector: charging, then preparing, then finished, then off.
    """
    chargers = _items(response, CHARGER_LIST)
    if not chargers:
        return {}, None
    powers, energies, states, details = [], [], [], []
    for item in chargers:
        connectors = {}
        for n in (1, 2):
            raw = _number(item.get(f"Connector{n}Status"))
            state = CONNECTOR_STATES.get(raw, "other") if raw is not None else None
            power = _number(item.get(f"Connector{n}Power"))
            states.append(state)
            powers.append(power)
            connectors[f"connector_{n}"] = {"state": state, "power_w": power}
        energy = _number(item.get("ConnectorElectricity"))
        energies.append(energy if energy is None or energy >= 0 else None)
        details.append(
            {
                "serial": item.get("ChargerSN"),
                "status": item.get("ChargerStatus"),  # no value table in the document
                **connectors,
                "energy_kwh": energy,
                "third_party": item.get("lsThirdParty"),
                "model": item.get("FansDevType"),
            }
        )
    if None in states:
        status = None
    else:
        status = next(
            (s for s in ("charging", "preparing", "finished") if s in states),
            "off" if all(s == "off" for s in states) else "other",
        )
    watts = _sum(powers)
    return {
        "ev_charge_power": None if watts is None else round(watts / 1000, 3),
        "ev_charger_status": status,
        "ev_connector_energy": _sum(energies),
    }, {"chargers": details}


def decode_accessories(response):
    """{(kind, serial): {"values": {...}, "attributes": {...}}} for plugs and heaters."""
    result = {}
    for item in _items(response, ACCESSORY_LISTS["plug"][0]):
        serial = item.get("PlugSN")
        if serial in (None, ""):
            continue
        energy = _number(item.get("PlugElectricity"))
        result[("plug", str(serial))] = {
            "values": {
                "plug_on": _flag(item.get("PlugStatus")),
                "plug_power": _number(item.get("PlugActvePower")),
                "plug_energy": energy if energy is None or energy >= 0 else None,
                # The table says V; its example (2359) is in 0.1 V.
                "plug_voltage": _scaled(item.get("PlugVol"), 0.1),
                "plug_current": _number(item.get("PlugCurrent")),
            },
            "attributes": {
                "rated_power_w": _number(item.get("PlugRatePower")),
                "third_party": item.get("lsThirdParty"),
                "model": item.get("FansDevType"),
            },
        }
    for item in _items(response, ACCESSORY_LISTS["heater"][0]):
        serial = item.get("HotSN")
        if serial in (None, ""):
            continue
        result[("heater", str(serial))] = {
            "values": {
                "heater_on": _flag(item.get("HotStatus")),
                "heater_power": _number(item.get("HotActvePower")),
                "heater_temperature": _scaled(item.get("HotTEMP"), 0.1),  # 0.1 degC
                "heater_temperature_max": _scaled(item.get("HotTEMPMAX"), 0.1),
            },
            "attributes": {
                "max_power_w": _number(item.get("HotActvePowerMAX")),
                "third_party": item.get("lsThirdParty"),
                "model": item.get("FansDevType"),
            },
        }
    return result


def _s16(word):
    return word - 0x10000 if word >= 0x8000 else word


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
    """Runtime with the attributes the coordinator and entities use."""

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
        self.remote_target = None
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
        self._next_mode = 0.0
        self._mode_words = None
        self._startup = None
        self._tou_enabled = {}
        # Devices the EMS manages (from EnergyParameter).
        self.accessories = {}  # (kind, serial) -> {"values": ..., "attributes": ...}
        self._accessory_keys = frozenset()
        self._charger_values = {}
        self._lock = asyncio.Lock()

    @property
    def unique_id(self):
        return f"{self.profile.namespace}_{self.identity.serial}"

    @property
    def device_name(self):
        return f"{self.profile.device_name} {self.identity.serial[-4:]}"

    def points(self):
        keys = list(TS_KEYS) + list(ALARM_KEYS) if self.registers else list(KEYS)
        if ("charger", "") in self._accessory_keys:
            keys += CHARGER_KEYS
        if self.registers and self.allow_control:
            keys += ["tou_schedule", "remote_status", "remote_end_time"]
        return [MEASUREMENTS[key] for key in keys]

    def accessory_list(self):
        """(kind, serial) of every plug and water heater, in a stable order."""
        return sorted(k for k in self._accessory_keys if k[0] != "charger")

    async def initialize(self, expected_serial=None):
        """The EMS answers with a system summary (the serial is from discovery).

        With a Shenggerui inverter behind it (type 131), a first register read
        through the pass-through decides that every value comes from registers.
        """
        response = await self.client.energy_parameters()
        if summary(response) is None:
            raise AeccLocalError("The answer has no system summary")
        self._take_accessories(response, initial=True)
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
        self.profile.remote_supported = True  # forced charge/discharge 0xA109-0xA10C
        self.profile.max_power_kw = TS_MAX_POWER_KW
        self.identity = replace(
            self.identity, protocol_version="aecc-pass-through-modbus", inverter_code=code
        )

    def _take_accessories(self, response, *, initial=False):
        """Accessory and charger values from one EnergyParameter answer."""
        keys = accessory_keys(response)
        if not initial and keys != self._accessory_keys:
            self.capabilities_changed = True  # the host reloads to add/remove entities
        self._accessory_keys = keys
        self.accessories = decode_accessories(response)
        self._charger_values, details = decode_chargers(response)
        if details is None:
            self.attributes.pop("ev_charger_status", None)
        else:
            self.attributes["ev_charger_status"] = details

    def _accessories_unknown(self):
        """A failed summary read: every accessory reading is unknown, never stale."""
        self.accessories = {
            key: {"values": {}, "attributes": item["attributes"]}
            for key, item in self.accessories.items()
        }
        self._charger_values = dict.fromkeys(self._charger_values)

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
                self._next_settings = self._next_mode = now + TS_CONTROL_INTERVAL
                try:
                    await self._read_controls()
                    frames += 3
                except AeccLocalError as err:
                    error = error or str(err)
                    self.controls = {}
                    self._mode_words = None
                    self.schedule = None
                    # No stale plan for the overview's card while it is unknown.
                    self.attributes.pop("tou_schedule", None)
            elif force or now >= self._next_mode:
                # The mode sensor also exists without control permission.
                self._next_mode = now + TS_CONTROL_INTERVAL
                try:
                    self._mode_words = await self.client.read_registers(TS_SLAVE, *TS_MODE_BLOCK)
                    frames += 1
                except AeccLocalError as err:
                    error = error or str(err)
                    self._mode_words = None
            values["work_mode"] = self._mode()
            if self.schedule is not None:
                values["tou_schedule"] = "configured" if self.schedule.periods else "empty"
            # The summary only for the devices the EMS manages: every poll while it
            # reports any, else with the control parameters to notice new ones.
            if self._accessory_keys or force or now >= self._next_controls:
                try:
                    self._take_accessories(await self.client.energy_parameters())
                except AeccLocalError as err:
                    error = error or str(err)
                    self._accessories_unknown()
                frames += 1
            # The last failure of this poll; None when every read of it succeeded.
            self.register_error = error
        else:
            response = await self.client.energy_parameters()
            values = decode(response)
            self._take_accessories(response)
            frames = 1
        values.update(self._charger_values)
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

    def _mode(self):
        """The EMS mode from 0xA034 (other values: "other"); None while unknown."""
        if self._mode_words is None:
            self.attributes.pop("work_mode", None)
            return None
        # 0xA034 takes effect in Hybrid supply (0xA028 = 3) only.
        self.attributes["work_mode"] = {
            "hybrid_supply": self._mode_words[0] == TS_HYBRID,
        }
        return TS_MODE_CODES.get(self._mode_words[TS_GRID_SETTING - TS_MODE_BLOCK[0]], "other")

    def _power_state(self, startup):
        """Inverter on/off from the startup state: mains or inverter = on, standby = off."""
        return True if startup in (2, 3) else False if startup == 1 else None

    async def _read_controls(self):
        words = await self.client.read_registers(TS_SLAVE, *TS_SETTINGS_BLOCK)
        self._mode_words = await self.client.read_registers(TS_SLAVE, *TS_MODE_BLOCK)
        dispatch = await self.client.read_registers(TS_SLAVE, *TS_DISPATCH_BLOCK)
        try:
            self.schedule, self._tou_enabled = decode_tou(words[4:18])
        except ValueError:
            self.schedule, self._tou_enabled = None, {}
        mode = self._mode()
        self.controls = {
            "min_soc": words[0],
            "backup_soc": words[2],
            "inverter_power": self._power_state(self._startup),
            "work_mode": mode,
            # Unknown device values leave the entity unavailable.
            "grid_charge": {0: False, 1: True}.get(dispatch[5]),
            "grid_import_limit": dispatch[0] / 1000,
            "remote_control": 1 if dispatch[3] == 1 else 0,
        }
        self.values["work_mode"] = mode
        self._update_remote(dispatch)
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
        return words, dispatch

    def _update_remote(self, dispatch):
        """Forced charge/discharge as the inverter reports it (it times the task)."""
        power, remaining = _s16(dispatch[1]), dispatch[4]
        if dispatch[3] == 1:
            self.remote_target = power / 1000
            self.remote_status = (
                "charging" if power < 0 else "discharging" if power > 0 else "holding"
            )
            self.remote_end_time = datetime.now(timezone.utc) + timedelta(seconds=remaining)
            self.remote_deadline = time.monotonic() + remaining
        else:
            if self.remote_deadline is not None:
                self.remote_status = "completed"  # ended on the device
            self.remote_deadline = self.remote_end_time = self.remote_target = None
        self._publish_remote()

    def _publish_remote(self):
        self.attributes["remote_status"] = {
            "target_power_kw": self.remote_target,
            "actual_power_kw": self.values.get("battery_power"),
            "end_time": self.remote_end_time.isoformat() if self.remote_end_time else None,
            "last_command": self.last_command,
        }

    def diagnostics(self):
        """Extra diagnostics for this source (no identities)."""
        return {
            "registers": self.registers,
            "inverter_code": self.identity.inverter_code,
            "register_error": self.register_error,
            "control_parameters": self.control_parameters,
            "accessories": sorted(kind for kind, _ in self._accessory_keys),
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

    def _mismatch(self):
        self.last_command = "uncertain"
        return PartialWrite("Read-back differs from the value written")

    async def set_control(self, key, value):
        self._gate()
        if key not in TS_CONTROL_KEYS:
            raise ControlError("Unsupported control")
        if key == "inverter_power":
            await self._set_power(bool(value))
            return
        definition = next(c for c in CONTROLS if c.key == key)
        if key == "work_mode":
            if value not in self.control_options(definition):
                raise ControlError("Unsupported mode")
            async with self._lock:
                await self._write_mode(value)
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
                words, _dispatch = await self._read_controls()
                if (words[0] if key == "min_soc" else words[2]) != value:
                    raise self._mismatch()
            elif key == "grid_charge":
                expected = 1 if value else 0
                await self._write(TS_GRID_CHARGE, [expected])
                _words, dispatch = await self._read_controls()
                if dispatch[5] != expected:
                    raise self._mismatch()
            else:  # grid_import_limit, kW on the entity, W on the device
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                ):
                    raise ControlError("Numeric value required")
                low, high = definition.limits(None)
                if not low <= value <= high:
                    raise ControlError("Value outside limits")
                watts = round(value * 1000)
                await self._write(TS_IMPORT_LIMIT, [watts])
                _words, dispatch = await self._read_controls()
                if dispatch[0] != watts:
                    raise self._mismatch()
            self.last_command = "confirmed"

    async def _write_mode(self, mode):
        """Hybrid supply first (0xA034 is valid there only), then the mode; lock held."""
        dispatch = await self.client.read_registers(TS_SLAVE, *TS_DISPATCH_BLOCK)
        if dispatch[3] == 1:
            raise ControlError("Stop remote control first")
        code = {name: c for c, name in TS_MODE_CODES.items()}[mode]
        block = await self.client.read_registers(TS_SLAVE, *TS_MODE_BLOCK)
        if block[0] != TS_HYBRID:
            await self._write(TS_SUPPLY_PRIORITY, [TS_HYBRID])
        await self._write(TS_GRID_SETTING, [code])
        await self._read_controls()
        offset = TS_GRID_SETTING - TS_MODE_BLOCK[0]
        if self._mode_words[0] != TS_HYBRID or self._mode_words[offset] != code:
            raise self._mismatch()
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
            readback, _dispatch = await self._read_controls()
            if readback[4:18] != words:
                self.last_command = "uncertain"
                raise PartialWrite("TOU read-back mismatch")
            if activate:
                await self._write_mode("time_of_use")  # mode 11 runs the plan
            self.last_command = "confirmed"

    async def set_battery_power(self, power_kw, duration):
        """Forced charge (< 0) or discharge (> 0) for `duration` seconds, timed by the inverter."""
        self._gate()
        if (
            isinstance(power_kw, bool)
            or not isinstance(power_kw, (int, float))
            or not math.isfinite(power_kw)
            or abs(power_kw) > TS_MAX_POWER_KW
        ):
            raise ControlError("Power outside the inverter's range")
        if (
            isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or not math.isfinite(duration)
            or not float(duration).is_integer()
            or duration < 1
        ):
            raise ControlError("Duration must be whole seconds")
        duration = int(duration)
        if duration > TS_FORCE_MAX_S:
            raise ControlError("Duration longer than 18 hours")
        watts = round(power_kw * 1000)
        async with self._lock:
            dispatch = await self.client.read_registers(TS_SLAVE, *TS_DISPATCH_BLOCK)
            if dispatch[3] == 1:
                # The inverter ignores changes while a task runs: stop it first.
                await self._write(TS_FORCE_ENABLE, [0])
            await self._write(TS_FORCE_POWER, [watts & 0xFFFF, duration])
            dispatch = await self.client.read_registers(TS_SLAVE, *TS_DISPATCH_BLOCK)
            if dispatch[3] != 0 or _s16(dispatch[1]) != watts or dispatch[2] != duration:
                raise self._mismatch()
            await self._write(TS_FORCE_ENABLE, [1])
            _words, dispatch = await self._read_controls()
            if dispatch[3] != 1:
                raise self._mismatch()
            self.last_command = "confirmed"
            self._publish_remote()

    async def stop_remote(self):
        self._gate()
        async with self._lock:
            await self._write(TS_FORCE_ENABLE, [0])
            _words, dispatch = await self._read_controls()
            if dispatch[3] != 0:
                raise self._mismatch()
            self.remote_status = "stopped"
            self.last_command = "confirmed"
            self._publish_remote()

    def control_options(self, definition, *, restoring=False):
        if definition.key == "work_mode":
            if not self.registers:
                return ()
            return tuple(m for m in definition.options if m in TS_MODE_CODES.values())
        return definition.options

    def schedule_status(self, now):
        """The plan sensor: the active period kind, or why none applies."""
        if self.schedule is None:
            return None
        if self.controls.get("remote_control"):
            return "remote"
        if self.values.get("work_mode") in ("self_consumption", "feed_in_priority", "backup"):
            return "inactive"  # another EMS mode runs instead of the plan
        enabled = [p for p in self.schedule.periods if self._tou_enabled.get(p.type)]
        if not enabled:
            return "inactive"
        period = replace(self.schedule, periods=tuple(enabled)).active(now)
        return period.type if period else "outside_period"

    async def close(self):
        await self.client.close()
