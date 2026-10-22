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

    @property
    def precision(self) -> int:
        """Suggested display decimals; counts are whole numbers."""
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
    + tuple(
        Measurement(f"pv{i}_power", "power", "kW", False, frozenset({f"pv{i}"}))
        for i in range(1, 5)
    )
    + (
        Measurement("backup_load_power", "power", "kW", False),
        Measurement("generator_power", "power", "kW", False, frozenset({"generator"})),
        Measurement("ac_couple_power", "power", "kW", False, frozenset({"ac_couple"})),
        Measurement("inverter_temperature", "temperature", "°C", False),
        Measurement(
            "tou_schedule",
            "schedule",
            enabled=False,
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
    Control("remote_control", "switch", maximum=1),
    Control("remote_power", "number", step=0.1, power_range="bipolar"),
)
DRAFTS = (
    Control("remote_target_power", "number", step=0.1, power_range="unipolar"),
    Control("remote_duration", "number", minimum=1, maximum=1440),
)
BUTTONS = ("start_charge", "start_discharge", "stop_remote")
