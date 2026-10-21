"""Local-only remote dispatch drafts; changing them never touches transport."""

import math

REMOTE_STATUSES = (
    "idle",
    "charging",
    "discharging",
    "holding",
    "stopped",
    "completed",
    "unconfirmed",
)
DRAFT_KEYS = frozenset({"remote_target_power", "remote_duration"})
REMOTE_KEYS = frozenset({"remote_status", "remote_end_time"})


class RemoteDraft:
    def __init__(self):
        self.power = 0.0
        self.duration = 5

    def set(self, key, value, maximum):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError("A finite numeric draft value is required")
        if key == "remote_target_power":
            if maximum is None or not 0 <= value <= maximum:
                raise ValueError("Target power outside confirmed device limits")
            self.power = float(value)
        elif key == "remote_duration":
            if isinstance(value, float) and not value.is_integer():
                raise ValueError("Duration must be a whole number of minutes")
            if not 1 <= value <= 1440:
                raise ValueError("Duration must be 1–1440 minutes")
            self.duration = int(value)
        else:
            raise ValueError("Unknown draft setting")

    def restore(self, data):
        if not isinstance(data, dict):
            return
        for key, value in data.items():
            try:
                # Keep a saved draft if the new device rating is lower. A start
                # still checks its current confirmed limit, never silently clamps.
                self.set(key, value, math.inf)
            except ValueError:
                continue

    def export(self):
        return {"remote_target_power": self.power, "remote_duration": self.duration}

    def snapshot(self, direction, maximum):
        if direction not in ("charge", "discharge"):
            raise ValueError("Choose charge or discharge")
        if maximum is None or not 0 < self.power <= maximum:
            raise ValueError("Set a positive target power within device limits before starting")
        return (-self.power if direction == "charge" else self.power), self.duration * 60
