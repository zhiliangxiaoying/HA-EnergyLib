"""TOU validation shared by forms, services and profiles."""

import math
from dataclasses import dataclass
from datetime import datetime, timezone

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
OUTSIDE_MODES = ("self_consumption", "backup", "pv_charge_priority", "feed_in_priority")


def minute(value):
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        raise ValueError("Use HH:MM")
    if not value[:2].isdigit() or not value[3:].isdigit():
        raise ValueError("Use HH:MM")
    h, m = int(value[:2]), int(value[3:])
    if not 0 <= h < 24 or not 0 <= m < 60:
        raise ValueError("24:00 and invalid times are not supported")
    return h * 60 + m


@dataclass(frozen=True)
class TouLimits:
    max_charge_periods: int = 6
    max_discharge_periods: int = 6
    max_power_kw: float | None = None  # Declared by each profile; no model-wide default.
    supports_cross_midnight: bool = False
    time_resolution_min: int = 1


@dataclass(frozen=True)
class TouPeriod:
    type: str
    start: str
    end: str
    power: float

    def as_dict(self):
        return dict(type=self.type, start=self.start, end=self.end, power=self.power)


@dataclass(frozen=True)
class TouSchedule:
    periods: tuple[TouPeriod, ...] = ()
    weekdays: tuple[str, ...] = WEEKDAYS
    outside_mode: str | None = None
    # Device calendar dates are encoded as UTC timestamps. Retain the complete
    # bounds so reading/editing a plan never rounds or replaces its device data.
    valid_from: datetime | None = None
    valid_until: datetime | None = None

    @classmethod
    def parse(cls, data, limits):
        if not isinstance(data, dict) or not isinstance(data.get("periods"), list):
            raise ValueError("A structured periods list is required")
        days = data.get("weekdays", list(WEEKDAYS))
        if not isinstance(days, (list, tuple)) or not days or any(d not in WEEKDAYS for d in days):
            raise ValueError("Choose weekdays")
        if len(set(days)) != len(days):
            raise ValueError("Duplicate weekday")
        outside = data.get("outside_mode")
        if outside is not None and outside not in OUTSIDE_MODES:
            raise ValueError("Invalid outside mode")

        def bound(key):
            value = data.get(key)
            if value is None:
                return None
            if isinstance(value, str):
                value = datetime.fromisoformat(value)
            if not isinstance(value, datetime) or value.tzinfo is None:
                raise ValueError("Validity bounds require timezone-aware ISO timestamps")
            return value.astimezone(timezone.utc)

        valid_from, valid_until = bound("valid_from"), bound("valid_until")
        if valid_from is not None and valid_until is not None and valid_from > valid_until:
            raise ValueError("Validity start must not exceed validity end")
        periods, occupied = [], {day: [] for day in range(7)}
        for item in data["periods"]:
            if not isinstance(item, dict) or set(item) != {"type", "start", "end", "power"}:
                raise ValueError("Each period needs type, start, end and power")
            if item["type"] not in ("charge", "discharge"):
                raise ValueError("Invalid period type")
            start, end = minute(item["start"]), minute(item["end"])
            if start == end or (end < start and not limits.supports_cross_midnight):
                raise ValueError("Empty or unsupported cross-midnight period")
            if start % limits.time_resolution_min or end % limits.time_resolution_min:
                raise ValueError("Invalid resolution")
            power = item["power"]
            if isinstance(power, bool) or not isinstance(power, (int, float)):
                raise ValueError("Numeric power required")
            if limits.max_power_kw is None:
                raise ValueError("Profile does not declare a rated power")
            if not math.isfinite(power) or not 0 <= power <= limits.max_power_kw:
                raise ValueError("Power outside device limits")
            for day in (WEEKDAYS.index(d) for d in days):
                spans = (
                    [(day, start, end)]
                    if end > start
                    else [(day, start, 1440), ((day + 1) % 7, 0, end)]
                )
                for index, low, high in spans:
                    if any(low < b and a < high for a, b in occupied[index]):
                        raise ValueError("Periods overlap")
                    occupied[index].append((low, high))
            periods.append(TouPeriod(item["type"], item["start"], item["end"], float(power)))
        for kind, maximum in (
            ("charge", limits.max_charge_periods),
            ("discharge", limits.max_discharge_periods),
        ):
            if sum(p.type == kind for p in periods) > maximum:
                raise ValueError("Too many periods")
        return cls(
            tuple(sorted(periods, key=lambda p: (p.start, p.type))),
            tuple(d for d in WEEKDAYS if d in days),
            outside,
            valid_from,
            valid_until,
        )

    def as_dict(self):
        result = {"periods": [p.as_dict() for p in self.periods], "weekdays": list(self.weekdays)}
        if self.outside_mode is not None:
            result["outside_mode"] = self.outside_mode
        if self.valid_from is not None:
            result["valid_from"] = self.valid_from.isoformat()
        if self.valid_until is not None:
            result["valid_until"] = self.valid_until.isoformat()
        return result

    def active(self, now):
        # Compare the device's local-calendar-as-UTC day, exactly as encoded;
        # do not round stored bounds or apply the HA host's UTC offset to them.
        calendar_day = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        if (self.valid_from is not None and calendar_day < self.valid_from) or (
            self.valid_until is not None and calendar_day > self.valid_until
        ):
            return None
        day, current = now.weekday(), now.hour * 60 + now.minute
        for p in self.periods:
            a, b = minute(p.start), minute(p.end)
            if b > a and WEEKDAYS[day] in self.weekdays and a <= current < b:
                return p
            if b < a and (
                (WEEKDAYS[day] in self.weekdays and current >= a)
                or (WEEKDAYS[(day - 1) % 7] in self.weekdays and current < b)
            ):
                return p
        return None
