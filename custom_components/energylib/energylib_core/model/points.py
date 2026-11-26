"""Standard metadata, with no vendor addresses or Home Assistant imports."""

from dataclasses import dataclass

from .remote import REMOTE_STATUSES


@dataclass(frozen=True)
class RegisterMapping:
    key: str
    address: int
    count: int = 2
    function: int = 3
    unit: int = 1
    encoding: str = "float"
    scale: float = 1.0  # -1 flips a device sign convention to the standard one


# Standard mode names. Order carries no meaning: each profile maps its own
# device codes to these names (see profile.work_mode_codes).
WORK_MODES = (
    "self_consumption",
    "time_of_use",
    "dynamic_tariff",
    "backup",
    "remote",
    "pv_charge_priority",
    "feed_in_priority",
    "voltage_regulation",
    "peak_shaving",
    "other",
)
RUNNING_STATES = ("standby", "ready", "off_grid", "on_grid", "other")
# EV charger state. The inverter-attached charger reports its plug state or that it
# charges; a charger of an EMS reports each connector as off, preparing, charging or
# finished.
EV_CHARGER_STATES = (
    "idle",
    "waiting_for_vehicle",
    "connected",
    "charging",
    "preparing",
    "finished",
    "off",
    "other",
)
EV_CHARGE_MODES = ("eco", "fast", "smart", "green")
# Every EV charger point: the entities belong to the charger's own device, under the
# inverter or EMS it is connected to.
CHARGER_POINT_KEYS = frozenset(
    {
        "ev_charge_power",
        "ev_charger_status",
        "ev_charger_energy",
        "ev_session_energy",
        "ev_session_duration",
        "ev_connector_energy",
        "ev_charger_meter_online",
        *(f"ev_charger_{kind}_{phase}" for kind in ("voltage", "current") for phase in "abc"),
        "ev_charger_on",
        "ev_charge_current",
        "ev_charge_mode",
        "ev_green_priority",
        "ev_plug_and_charge",
        "ev_battery_charging",
        "ev_grid_max_power",
    }
)
# Values of DeviceRuntime.schedule_status(): active period type, or why none applies.
TOU_STATUSES = ("charge", "discharge", "outside_period", "expired", "inactive", "remote")


@dataclass(frozen=True)
class Measurement:
    key: str
    kind: str
    unit: str | None = None
    enabled: bool = True
    capabilities: frozenset[str] = frozenset()
    interval: int = 30
    options: tuple[str, ...] = ()
    digits: int | None = None  # Display decimals when the kind default is too coarse.

    @property
    def precision(self) -> int:
        """Suggested display decimals; counts are whole numbers."""
        if self.digits is not None:
            return self.digits
        return {"count": 0, "soc": 1}.get(self.kind, 2)


# Energy-flow powers drive the flow card and the twin, so they get the fast tier.
FLOW_INTERVAL = 10

CORE = (
    tuple(
        Measurement(k, "power", "kW", interval=FLOW_INTERVAL)
        for k in ("solar_power", "battery_power", "load_power", "grid_power")
    )
    + tuple(
        Measurement(k, "energy", "kWh", interval=60)
        for k in (
            "grid_import_energy",
            "grid_export_energy",
            "solar_energy",
            "battery_charge_energy",
            "battery_discharge_energy",
            "load_energy",
        )
    )
    + (
        Measurement("battery_total_soc", "soc", "%"),
        Measurement("running_state", "enum", options=RUNNING_STATES),
        Measurement("active_alarms", "count", enabled=False),
        Measurement("work_mode", "enum", interval=60, options=WORK_MODES),
    )
)
OPTIONAL = (
    (
        Measurement("battery_soc", "soc", "%", False),
        Measurement("grid_voltage_a", "voltage", "V", False),
        Measurement("grid_voltage_b", "voltage", "V", False, frozenset({"three_phase"})),
        Measurement("grid_voltage_c", "voltage", "V", False, frozenset({"three_phase"})),
        Measurement("grid_frequency", "frequency", "Hz", False),
        Measurement("battery_voltage", "voltage", "V", False),
        Measurement("battery_current", "current", "A", False),
        Measurement("battery_soh", "soc", "%", False, frozenset({"bms"})),
        Measurement("battery_2_soc", "soc", "%", False, frozenset({"battery_2"})),
        Measurement("battery_2_power", "power", "kW", False, frozenset({"battery_2"})),
        Measurement("battery_2_voltage", "voltage", "V", False, frozenset({"battery_2"})),
        Measurement("battery_2_current", "current", "A", False, frozenset({"battery_2"})),
    )
    # Battery cabinet 1 as reported by the BMS (0xB10A-0xB15E). Created only for a
    # device that reports a BMS; floats in engineering units like the other points.
    + tuple(
        Measurement(
            key, "energy_storage", "kWh", False, frozenset({"bms", "bms_capacity"}), digits=1
        )
        for key in ("battery_capacity", "battery_remaining_energy")
    )
    + tuple(
        Measurement(key, kind, unit, False, frozenset({"bms"}), digits=digits)
        for key, kind, unit, digits in (
            ("battery_cell_voltage_max", "voltage", "V", 3),
            ("battery_cell_voltage_min", "voltage", "V", 3),
            ("battery_cell_temperature_max", "temperature", "°C", 1),
            ("battery_cell_temperature_min", "temperature", "°C", 1),
            ("bms_charge_current_limit", "current", "A", 1),
            ("bms_discharge_current_limit", "current", "A", 1),
        )
    )
    + tuple(
        Measurement(f"pv{i}_power", "power", "kW", False, frozenset({f"pv{i}"}))
        for i in range(1, 5)
    )
    + (
        Measurement("backup_load_power", "power", "kW", False),
        Measurement("generator_power", "power", "kW", False, frozenset({"generator"})),
        Measurement("ac_couple_power", "power", "kW", False, frozenset({"ac_couple"})),
        Measurement("inverter_temperature", "temperature", "°C", False),
        # External meter link (0xA000, 1 = online); only for devices that answer it.
        Measurement(
            "meter_online", "connectivity", enabled=False, capabilities=frozenset({"meter"})
        ),
    )
    # EV charger behind the inverter, only when the inverter reports one. Power and
    # status are on by default for those devices; the rest start disabled.
    + (
        Measurement(
            "ev_charge_power",
            "power",
            "kW",
            capabilities=frozenset({"charger"}),
            interval=FLOW_INTERVAL,
        ),
        Measurement(
            "ev_charger_status",
            "enum",
            capabilities=frozenset({"charger", "charger_status"}),
            options=EV_CHARGER_STATES,
        ),
        Measurement(
            "ev_charger_energy", "energy", "kWh", False, frozenset({"charger"}), interval=60
        ),
        Measurement(
            "ev_session_energy", "energy", "kWh", False, frozenset({"charger"}), interval=60
        ),
        Measurement(
            "ev_session_duration",
            "duration",
            "s",
            False,
            frozenset({"charger"}),
            interval=60,
            digits=0,
        ),
        Measurement(
            "ev_charger_meter_online",
            "connectivity",
            enabled=False,
            capabilities=frozenset({"charger", "charger_status"}),
        ),
    )
    + tuple(
        Measurement(
            f"ev_charger_{kind}_{phase}",
            kind,
            unit,
            False,
            frozenset({"charger"} | ({"charger_three_phase"} if phase != "a" else set())),
        )
        for kind, unit in (("voltage", "V"), ("current", "A"))
        for phase in "abc"
    )
    + (
        Measurement(
            "tou_schedule",
            "schedule",
            # On by default (user decision 2026-11-19): the overview's schedule card
            # shows and edits the plan from its attributes.
            enabled=True,
            capabilities=frozenset({"ctrl_tou"}),
            interval=300,
            options=TOU_STATUSES,
        ),
    )
)
ALARM_SUMMARIES = (
    Measurement(
        "alarm_status",
        "enum",
        options=("normal", "abnormal", "unrecognized", "incomplete", "offline"),
    ),
    Measurement("fault_count", "count", enabled=False),
    Measurement("warning_count", "count", enabled=False),
    Measurement("any_fault", "problem", enabled=False),
    Measurement("any_warning", "problem", enabled=False),
)
REMOTE_MEASUREMENTS = (
    Measurement("remote_status", "task", options=REMOTE_STATUSES),
    Measurement("remote_end_time", "timestamp", enabled=False),
)
# Point kinds published as binary sensors rather than sensors.
BINARY_KINDS = ("problem", "connectivity")
MEASUREMENTS = {p.key: p for p in CORE + OPTIONAL + ALARM_SUMMARIES + REMOTE_MEASUREMENTS}


@dataclass(frozen=True)
class Control:
    key: str
    platform: str
    minimum: float = 0
    maximum: float = 100
    step: float = 1
    options: tuple[str, ...] = ()
    # "unipolar" = [0, rated], "bipolar" = [-rated, rated]; rated comes from the profile.
    power_range: str | None = None
    # Advanced battery limits: offered only if the device answers them, and their
    # entities start disabled so the default control set stays small.
    advanced: bool = False

    def limits(self, max_power_kw):
        """Return (minimum, maximum); power ranges never use a model-wide default."""
        if self.power_range is None:
            return self.minimum, self.maximum
        if max_power_kw is None or not max_power_kw > 0:
            raise ValueError("Profile does not declare a rated power")
        return (-max_power_kw if self.power_range == "bipolar" else 0.0), float(max_power_kw)


CONTROLS = (
    Control(
        "work_mode",
        "select",
        options=(
            "self_consumption",
            "time_of_use",
            "backup",
            "pv_charge_priority",
            "feed_in_priority",
            "peak_shaving",
        ),
    ),
    Control("backup_soc", "number"),
    Control("min_soc", "number"),
    Control("grid_charge", "switch", maximum=1),
    Control("export_limit", "number", step=0.1, power_range="unipolar"),
    # 0x3002: the limit value alone cannot express "no limit" (0 kW means zero export).
    Control("export_limit_enable", "switch", maximum=1),
    # One-shot power on/off commands, shown as a switch read back from the DCAC state.
    Control("inverter_power", "switch", maximum=1),
    Control("remote_control", "switch", maximum=1),
    Control("remote_power", "number", step=0.1, power_range="bipolar"),
    Control("max_soc", "number", minimum=50, maximum=100, advanced=True),
    Control("max_charge_current", "number", maximum=300, step=0.1, advanced=True),
    Control("max_discharge_current", "number", maximum=300, step=0.1, advanced=True),
    # EV charger behind the inverter: offered only when the inverter reports one.
    # On/off and the current limit are main controls; the rest start disabled.
    Control("ev_charger_on", "switch", maximum=1),
    Control("ev_charge_current", "number", minimum=6, maximum=32),
    Control("ev_charge_mode", "select", options=EV_CHARGE_MODES, advanced=True),
    Control("ev_green_priority", "switch", maximum=1, advanced=True),
    Control("ev_plug_and_charge", "switch", maximum=1, advanced=True),
    Control("ev_battery_charging", "switch", maximum=1, advanced=True),
    # 0 kW turns the limit off (protocol range 0-1000 kW).
    Control("ev_grid_max_power", "number", maximum=1000, step=0.1, advanced=True),
    # Grid purchase limit of an inverter behind an EMS (kW; device default 1 kW).
    Control("grid_import_limit", "number", maximum=65.5, step=0.1, advanced=True),
)
DRAFTS = (
    Control("remote_target_power", "number", step=0.1, power_range="unipolar"),
    Control("remote_duration", "number", minimum=1, maximum=1440),
)
BUTTONS = ("start_charge", "start_discharge", "stop_remote")


@dataclass(frozen=True)
class AccessoryPoint:
    """A reading of a device an EMS manages (smart plug, water heater)."""

    key: str
    kind: str  # power_state (binary), enum, power, energy, voltage, current, temperature
    unit: str | None = None
    enabled: bool = True
    digits: int | None = None
    options: tuple[str, ...] = ()


# Accessories become their own devices under the EMS, one per serial number.
ACCESSORY_POINTS = {
    # An EMS's EV charger: state of its most active connector, both connectors'
    # power, and its "connector energy" (the document does not say whether it is per
    # session or lifetime, so a counter that may reset).
    "charger": (
        AccessoryPoint("ev_charger_status", "enum", options=EV_CHARGER_STATES),
        AccessoryPoint("ev_charge_power", "power", "kW", digits=2),
        AccessoryPoint("ev_connector_energy", "energy", "kWh", False, 2),
    ),
    "plug": (
        AccessoryPoint("plug_on", "power_state"),
        AccessoryPoint("plug_power", "power", "W", digits=0),
        AccessoryPoint("plug_energy", "energy", "kWh", False, 2),
        AccessoryPoint("plug_voltage", "voltage", "V", False, 1),
        AccessoryPoint("plug_current", "current", "A", False, 2),
    ),
    "heater": (
        AccessoryPoint("heater_on", "power_state"),
        AccessoryPoint("heater_power", "power", "W", digits=0),
        AccessoryPoint("heater_temperature", "temperature", "°C", digits=1),
        AccessoryPoint("heater_temperature_max", "temperature", "°C", False, 1),
    ),
}
