"""Source-aware alarm observations. Timestamps describe observation, not device time."""

from copy import deepcopy
from datetime import datetime, timezone

ALARM_KEYS = frozenset(
    {"active_alarms", "alarm_status", "fault_count", "warning_count", "any_fault", "any_warning"}
)


class AlarmTracker:
    def __init__(self, describe, *, utcnow=None):
        self.describe = describe
        self.utcnow = utcnow or (lambda: datetime.now(timezone.utc))
        self.history = []
        self.active = {}
        self.sources = {}
        self.baselines = set()
        self.events = []

    def restore(self, data):
        """Restore occurrences, but require fresh per-source baselines after every load."""
        data = data if isinstance(data, dict) else {}
        self.history = deepcopy(data.get("history", [])[-100:])
        self.active = {r["alarm_id"]: deepcopy(r) for r in data.get("active", [])}
        # Reuse occurrence objects so recovery updates the persisted history too.
        for record in self.history:
            if record["alarm_id"] in self.active and not record.get("cleared_at"):
                self.active[record["alarm_id"]] = record
        for r in self.active.values():
            r["stale"] = True

    def observe(self, mapping, bits):
        source = (mapping.function, mapping.unit, mapping.address, mapping.count)
        if bits is None or len(bits) != mapping.count:
            self.sources[source] = False
            for item in self.active.values():
                if self._belongs(item, mapping):
                    item["stale"] = True
            return
        now = self.utcnow().isoformat()
        self.sources[source] = True
        baseline = source not in self.baselines
        self.baselines.add(source)
        seen = set()
        for offset, bit in enumerate(bits):
            if not bit:
                continue
            detail = self.describe(mapping.function, mapping.unit, mapping.address + offset)
            alarm_id = detail["alarm_id"]
            seen.add(alarm_id)
            record = self.active.get(alarm_id)
            if record is None:
                record = {**detail, "first_seen": now, "cleared_at": None}
                self.active[alarm_id] = record
                self.history.append(record)
                if not baseline:
                    self._event(record, "raised", now)
            record.update(last_seen=now, observed_at=now, stale=False)
        for alarm_id, record in list(self.active.items()):
            if self._belongs(record, mapping) and alarm_id not in seen:
                record.update(cleared_at=now, observed_at=now, stale=False)
                del self.active[alarm_id]
                if not baseline:
                    self._event(record, "cleared", now)
        self.history = self.history[-100:]

    @staticmethod
    def _belongs(record, mapping):
        return (
            record["function"] == mapping.function
            and record["source"] == mapping.unit
            and mapping.address <= int(record["code"], 16) < mapping.address + mapping.count
        )

    def _event(self, record, change, now):
        self.events.append(
            {
                **{k: record[k] for k in ("alarm_id", "source", "code", "name", "severity")},
                "change": change,
                "observed_at": now,
            }
        )

    def snapshot(self, mappings):
        complete = bool(mappings) and all(
            self.sources.get((m.function, m.unit, m.address, m.count), False) for m in mappings
        )
        alarms = sorted(self.active.values(), key=lambda r: r["alarm_id"])
        counts = {
            s: sum(r["severity"] == s for r in alarms)
            for s in ("fault", "warning", "info", "unknown")
        }
        values = {
            "active_alarms": len(alarms) if complete else None,
            "fault_count": counts["fault"] if complete else None,
            "warning_count": counts["warning"] if complete else None,
        }
        for severity in ("fault", "warning"):
            # A previously confirmed active alarm remains evidence until a valid
            # source reports its recovery. Missing sources cannot prove absence.
            values[f"any_{severity}"] = True if counts[severity] else (False if complete else None)
        attrs = {
            "alarms": deepcopy(alarms),
            "complete": complete,
            "valid_sources": sum(
                self.sources.get((m.function, m.unit, m.address, m.count), False) for m in mappings
            ),
            "unknown_count": counts["unknown"],
            "info_count": counts["info"],
            "history": deepcopy(list(reversed(self.history))),
        }
        values["alarm_status"] = (
            "offline"
            if not attrs["valid_sources"]
            else "incomplete"
            if not complete
            else "unrecognized"
            if counts["unknown"]
            else "abnormal"
            if alarms
            else "normal"
        )
        return values, attrs

    def revision(self):
        """Changes when an occurrence is raised, cleared or added to history."""
        return (
            frozenset(self.active),
            len(self.history),
            tuple(r.get("cleared_at") for r in self.history[-3:]),
        )

    def export(self):
        return {
            "history": deepcopy(self.history[-100:]),
            "active": deepcopy(list(self.active.values())),
        }
