"""One device-level serialized read/write owner and finite remote-control leases."""

import asyncio
import math
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from .legacy import IdentityMismatch, decode_float
from .model.alarms import ALARM_KEYS, AlarmTracker
from .model.points import CONTROLS, MEASUREMENTS
from .model.remote import REMOTE_KEYS, RemoteDraft
from .model.tou import TouSchedule
from .planner import plan_reads
from .profiles import get_profile
from .transport.modbus_tcp import DeviceException, EnergyLibError

SLOW_TIER = 30  # seconds: alarms, identity and control state
# HA starts each poll exactly one interval after the previous start, a few ms
# before a point's tier has fully elapsed; without slack every other tick is lost.
TIER_SLACK = 1.0


class ControlError(EnergyLibError):
    """Rejected or uncertain command. No private values in exception messages."""


class PartialWrite(ControlError):
    """A command may be partially applied: inspect authoritative read-back."""


class DeviceRuntime:
    def __init__(self, client, profile_id, *, clock=time.monotonic, task_factory=None):
        self.client = client
        self.client.verify_write_identity = self._identity
        self.client.authorize_write_retry = self._authorize_write_retry
        self.profile = get_profile(profile_id)
        # The host (e.g. HA) can own background tasks so they are tracked and
        # cancelled on unload/shutdown; plain asyncio is the library default.
        self._task_factory = task_factory or asyncio.create_task
        self.identity = None
        self._lock = asyncio.Lock()
        self._clock = clock
        self._timestamps = {}
        self.values = {}
        self.attributes = {}
        self.controls = {}
        self.schedule = None
        self.allow_control = False
        self.last_command = "none"
        self._lease_task = None
        self._lease_generation = 0
        self.remote_deadline = None
        self._heartbeat = 0
        self._generation = -1
        self._identity_checked = -math.inf
        self._closed = False
        self.last_frame_count = 0
        self.alarms = AlarmTracker(self.profile.describe_alarm)
        self.draft = RemoteDraft()
        self.remote_status = "idle"
        self.remote_end_time = None
        self.remote_target = None
        self._poll_uncertain = False  # "unconfirmed" caused only by a failed poll
        self.state_changed = None

    @property
    def unique_id(self):
        return f"{self.profile.namespace}_{self.identity.serial}"

    @property
    def device_name(self):
        """Profile name plus the serial's last 4 characters, so devices stay distinct."""
        return f"{self.profile.device_name} {self.identity.serial[-4:]}"

    async def initialize(self, expected_serial=None):
        async with self._lock:
            await self._identity(expected_serial)
            await self.profile.configure(self.client, self.identity)
            await self._verify_connection()
            self._generation = self.client.generation

    def _ensure_open(self):
        if self._closed:
            raise ControlError("Device runtime is closed")

    async def _identity(self, expected=None, *, allow_closed=False):
        if not allow_closed:
            self._ensure_open()
        identity = await self.profile.identify(self.client)
        if expected is None and self.identity is not None:
            expected = self.identity.serial
        if expected is not None and identity.serial != expected:
            raise IdentityMismatch("Endpoint identity changed")
        self.identity = identity
        self.client.write_generation = self.client.generation
        self._identity_checked = self._clock()

    async def _verify_connection(self):
        """A transparent read retry must not publish another device's data."""
        if self.client.write_generation != self.client.generation:
            await self._identity()

    async def _authorize_write_retry(self, address, words):
        """A reconnect can change limits or the constraints on a pending target."""
        await self._prepare_locked()
        self._gate()
        await self.profile.validate_write_retry(
            self.client, address, words, remote_owned=self.remote_deadline is not None
        )
        if self.client.generation != self._generation:
            raise ControlError("Connection changed while validating write retry")

    async def _prepare_locked(self, *, verify=True):
        """Verify identity and refresh capabilities before using a new connection.

        Polls may skip the identity read on a connection that is still the one
        last verified; any reconnect is re-identified by _verify_connection.
        """
        if (
            verify
            or not getattr(self.client, "connected", False)
            or self._generation != self.client.generation
        ):
            await self._identity()
        if self._generation == self.client.generation:
            return False
        await self.profile.configure(self.client, self.identity)
        await self._verify_connection()
        self._generation = self.client.generation
        self._timestamps.clear()
        return True

    def points(self):
        return [
            p
            for p in MEASUREMENTS.values()
            if (
                (
                    p.key in self.profile.mappings
                    or p.kind == "schedule"
                    or (p.key in ALARM_KEYS and "active_alarms" in self.profile.mappings)
                    or (
                        p.key in REMOTE_KEYS
                        and self.allow_control
                        and self.profile.controls_supported
                    )
                )
                and p.capabilities <= self.profile.capabilities
                and (p.kind != "schedule" or self.allow_control)
            )
        ]

    async def _read(self, mapping):
        if mapping.function == 2:
            result = await self.profile.read_discrete_inputs(
                self.client, mapping.address, mapping.count, unit_id=mapping.unit
            )
        else:
            result = await self.client.read_registers(
                mapping.function, mapping.address, mapping.count, unit_id=mapping.unit
            )
        await self._verify_connection()
        return result

    async def refresh(self, enabled, *, force=False):
        async with self._lock:
            count = self.client.requests
            try:
                now = self._clock()
                # Identity and control state change rarely: check them on the 30 s
                # tier, not on every fast energy-flow tick.
                verify = force or now - self._identity_checked >= SLOW_TIER - TIER_SLACK
                if await self._prepare_locked(verify=verify):
                    force = True
                supported = {p.key for p in self.points()}
                enabled = set(enabled) & supported
                # Every enabled summary depends on exactly the same source reads.
                alarm_due = bool(enabled & ALARM_KEYS) and (
                    force
                    or now - self._timestamps.get("active_alarms", -math.inf)
                    >= SLOW_TIER - TIER_SLACK
                )
                if alarm_due:
                    for mapping in self.profile.mappings["active_alarms"]:
                        try:
                            bits = await self._read(mapping)
                        except EnergyLibError:
                            bits = None
                        self.alarms.observe(mapping, bits)
                    self._publish_alarms()
                    self._timestamps["active_alarms"] = now
                due = {
                    k
                    for k in enabled
                    if k not in ALARM_KEYS | REMOTE_KEYS
                    and (
                        force
                        or now - self._timestamps.get(k, -math.inf)
                        >= MEASUREMENTS[k].interval - TIER_SLACK
                    )
                }
                maps = [m for k in due for m in self.profile.mappings.get(k, ())]
                raw = {}
                blocks = plan_reads(
                    maps,
                    register_limit=self.profile.register_limit,
                    bit_limit=self.profile.bit_limit,
                )
                for block in blocks:
                    try:
                        words = await self._read(block)
                    except DeviceException as err:
                        if err.code not in (1, 2, 11):
                            raise
                        # Only a definitive per-map response can isolate unsupported points.
                        for mapping in block.mappings:
                            try:
                                raw[mapping] = await self._read(mapping)
                            except DeviceException as point_error:
                                if point_error.code not in (1, 2, 11):
                                    raise
                                raw[mapping] = None
                    else:
                        for mapping in block.mappings:
                            offset = mapping.address - block.address
                            raw[mapping] = words[offset : offset + mapping.count]
                for key in due:
                    if key == "tou_schedule":
                        self.schedule = await self.profile.read_schedule(self.client)
                        self.values[key] = "configured" if self.schedule.periods else "empty"
                        self.attributes[key] = self.schedule.as_dict()
                    else:
                        self._decode(key, raw)
                        if (
                            key == "battery_total_soc"
                            and self.values[key] is None
                            and self.profile.single_battery_confirmed
                        ):
                            mapping = self.profile.mappings["battery_soc"][0]
                            try:
                                self.values[key] = decode_float(
                                    await self._read(mapping), kind="soc"
                                )
                            except DeviceException as err:
                                if err.code not in (1, 2):
                                    raise
                    self._timestamps[key] = now
                if (
                    self.allow_control
                    and self.profile.controls_supported
                    and (
                        force
                        or now - self._timestamps.get("controls", -math.inf)
                        >= SLOW_TIER - TIER_SLACK
                    )
                ):
                    self.controls = await self.profile.read_controls(self.client)
                    self._timestamps["controls"] = now
                    if (
                        self._poll_uncertain
                        and self.remote_deadline is not None
                        and self.controls.get("remote_control")
                    ):
                        # This poll re-read the device: our session is still active.
                        self._poll_uncertain = False
                        target = self.remote_target or 0
                        self.remote_status = (
                            "charging" if target < 0 else "discharging" if target > 0 else "holding"
                        )
                await self._verify_connection()
                self.last_frame_count = self.client.requests - count
                self._publish_remote()
                return {k: self.values.get(k) for k in enabled}
            except BaseException:
                self._timestamps.clear()  # Recovery must not publish stale slow-tier values.
                self._identity_checked = -math.inf  # Re-verify on the next poll.
                for mapping in self.profile.mappings.get("active_alarms", ()):
                    self.alarms.observe(mapping, None)
                self._publish_alarms()
                if self.remote_deadline is not None:
                    # Only this poll failed; a later poll may confirm the session again.
                    self._poll_uncertain = True
                    self.remote_status = "unconfirmed"
                    self._publish_remote()
                raise

    def _publish_alarms(self):
        values, attributes = self.alarms.snapshot(self.profile.mappings.get("active_alarms", ()))
        self.values.update(values)
        for key in ALARM_KEYS:
            self.attributes[key] = (
                attributes
                if key in ("active_alarms", "alarm_status")
                else {k: attributes[k] for k in ("complete", "unknown_count", "info_count")}
            )

    def _publish_remote(self):
        if self.remote_deadline is None and self.controls.get("remote_control"):
            # After an abrupt restart (or App control), an active device session
            # is not an owned HA task. Do not present it as idle or resume it.
            self.remote_status = "unconfirmed"
        self.values["remote_status"] = self.remote_status
        self.values["remote_end_time"] = self.remote_end_time
        self.attributes["remote_status"] = {
            "target_power_kw": self.remote_target,
            "actual_power_kw": self.values.get("battery_power"),
            "end_time": self.remote_end_time.isoformat() if self.remote_end_time else None,
            "last_command": self.last_command,
        }

    def _remote_changed(self):
        self._publish_remote()
        if self.state_changed is not None and not self._closed:
            self.state_changed()

    def _decode(self, key, raw):
        maps = self.profile.mappings.get(key, ())
        parts = [raw.get(m) for m in maps]
        self.attributes.pop(key, None)
        if not parts or any(p is None for p in parts):
            self.values[key] = None
            return
        if key == "running_state":
            self.values[key] = self.profile.decode_enum(key, parts[0])
        else:
            values = [decode_float(words, kind=MEASUREMENTS[key].kind) for words in parts]
            # Profiles convert device sign conventions to the standard ones here
            # (grid + = import, battery + = discharge).
            values = [None if v is None else v * m.scale for v, m in zip(values, maps, strict=True)]
            if key == "work_mode":
                self.values[key] = self.profile.decode_enum(key, values[0])
            else:
                self.values[key] = max(values) if all(v is not None for v in values) else None

    def control_options(self, definition):
        """Standard options this profile can actually encode."""
        if definition.key != "work_mode":
            return definition.options
        known = set(self.profile.work_mode_codes.values())
        return tuple(o for o in definition.options if o in known)

    def _gate(self):
        self._ensure_open()
        if not self.allow_control or not self.profile.controls_supported:
            raise ControlError("Control is not enabled for this verified profile")

    async def _write_float(self, address, value):
        await self.profile.write_float(self.client, address, value)

    async def set_control(self, key, value):
        self._gate()
        if key not in self.profile.control_keys:
            raise ControlError("Unsupported control")
        if key == "remote_power":
            return await self.set_battery_power(value, 300)
        if key == "remote_control":
            if value:
                return await self.set_battery_power(0, 300)
            return await self.stop_remote()
        async with self._lock:
            await self._prepare_locked()
            self._gate()
            if key not in self.profile.control_keys:
                raise ControlError("Unsupported control")
            definition = next(c for c in CONTROLS if c.key == key)
            if definition.options:
                if value not in self.control_options(definition):
                    raise ControlError("Unsupported mode")
            elif (isinstance(value, bool) and definition.platform != "switch") or not isinstance(
                value, (int, float)
            ):
                raise ControlError("Numeric value required")
            else:
                try:
                    low, high = definition.limits(self.profile.max_power_kw)
                except ValueError as err:
                    raise ControlError("Profile does not declare a rated power") from err
                if not math.isfinite(value) or not low <= value <= high:
                    raise ControlError("Value outside limits")
            actual = await self.profile.read_controls(self.client)
            if actual["remote_control"] and key == "work_mode":
                raise ControlError("Stop remote control first")
            # Only SOC writes can create or fix a min/backup conflict; a device
            # already in conflict (e.g. set from the App) must not block others.
            if key in ("min_soc", "backup_soc"):
                minimum = value if key == "min_soc" else actual["min_soc"]
                backup = value if key == "backup_soc" else actual["backup_soc"]
                if minimum is None or backup is None or minimum > backup:
                    raise ControlError("Minimum SOC must not exceed backup SOC")
                if "battery_2" in self.profile.capabilities:
                    minimum_2 = value if key == "min_soc" else actual["min_soc_2"]
                    backup_2 = value if key == "backup_soc" else actual["backup_soc_2"]
                    if minimum_2 is None or backup_2 is None or minimum_2 > backup_2:
                        raise ControlError("Battery 2 minimum SOC must not exceed backup SOC")
            try:
                await self.profile.apply_control(self.client, key, value)
                await self._verify_connection()
                self.last_command = "confirmed"
            except EnergyLibError as err:
                self.last_command = "uncertain"
                raise PartialWrite("Command failed; some parameters may have changed") from err
            finally:
                await self._update_controls_best_effort()

    async def _update_controls_best_effort(self):
        try:
            await self._identity()
            self.controls = await self.profile.read_controls(self.client)
            await self._verify_connection()
        except EnergyLibError:
            self.controls = {}

    async def set_schedule(self, data, *, activate=False):
        self._gate()
        if type(activate) is not bool:
            raise ControlError("activate must be boolean")
        async with self._lock:
            await self._prepare_locked()
            self._gate()
            if "ctrl_tou" not in self.profile.capabilities or self.profile.tou_limits is None:
                raise ControlError("Device does not expose the TOU registers")
            schedule = TouSchedule.parse(data, self.profile.tou_limits)
            actual = await self.profile.read_controls(self.client)
            if actual["remote_control"]:
                raise ControlError("Stop remote control before editing TOU")
            previous = actual["work_mode"]
            mode = next(c for c in CONTROLS if c.key == "work_mode")
            if previous not in self.control_options(mode):
                raise ControlError("Cannot safely restore current mode")
            current = await self.profile.read_schedule(self.client)
            # Validity is not editable through this API or the native forms.
            # Retain the device's current bounds, even for a stale draft, unless
            # they have already expired: a saved plan would then never run.
            valid_from, valid_until = current.valid_from, current.valid_until
            today = datetime.now().date()
            if valid_until is not None and valid_until < _calendar_day(today):
                valid_from, valid_until = self.profile.fresh_validity(today)
            schedule = replace(
                schedule,
                outside_mode=(
                    current.outside_mode if schedule.outside_mode is None else schedule.outside_mode
                ),
                valid_from=valid_from,
                valid_until=valid_until,
            )
            try:
                await self.profile.write_schedule(self.client, schedule)
                readback = await self.profile.read_schedule(self.client)
                await self._verify_connection()
                if not self.profile.schedules_equal(readback, schedule):
                    raise PartialWrite("TOU read-back mismatch")
                self.schedule = readback
                await self.profile.apply_control(
                    self.client, "work_mode", "time_of_use" if activate else previous
                )
                await self._verify_connection()
                self.last_command = "confirmed"
            except (EnergyLibError, ValueError) as err:
                self.schedule = None
                self.last_command = "uncertain"
                raise PartialWrite(
                    "TOU partially written; mode is not automatically reactivated"
                ) from err
            finally:
                await self._update_controls_best_effort()
                self._timestamps.pop("tou_schedule", None)

    async def set_battery_power(self, power_kw, duration):
        self._gate()
        if any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
            for v in (power_kw, duration)
        ):
            raise ControlError("Finite numeric power and duration required")
        if not 1 <= duration <= 86400:
            raise ControlError("Power or duration outside device limits")
        async with self._lock:
            await self._prepare_locked()
            self._gate()
            if not self.profile.max_power_kw or abs(power_kw) > self.profile.max_power_kw:
                raise ControlError("Power or duration outside device limits")
            actual = await self.profile.read_controls(self.client)
            if actual["work_mode"] == "time_of_use":
                raise ControlError("Leave TOU before starting remote control")
            if actual["remote_control"] and self.remote_deadline is None:
                raise ControlError("An unowned remote session must first be stopped")
            failure = None
            try:
                await self.profile.enter_remote(self.client, power_kw)
                await self._send_heartbeat()
                self.remote_deadline = self._clock() + duration
                self.remote_end_time = datetime.now(timezone.utc) + timedelta(seconds=duration)
                self.remote_target = float(power_kw)
                self.remote_status = (
                    "charging" if power_kw < 0 else "discharging" if power_kw > 0 else "holding"
                )
                # A renewed lease has its own owner. An old worker may already
                # be waiting to clean up after expiry or a heartbeat failure.
                self._lease_generation += 1
                if self._lease_task is not None:
                    self._lease_task.cancel()
                self._lease_task = self._task_factory(self._lease_loop(self._lease_generation))
                self.last_command = "confirmed"
            except EnergyLibError as err:
                failure = err
                self.last_command = "uncertain"
                self.remote_status = "unconfirmed"
            finally:
                await self._update_controls_best_effort()
                if not self.controls:
                    self.remote_status = "unconfirmed"
                elif (
                    failure is not None
                    and self.remote_deadline is None
                    and self.controls.get("remote_control") == 0
                ):
                    # Read-back proves no session started: nothing is left to resolve.
                    self.remote_status = "idle"
                self._remote_changed()
            if failure is not None:
                if self.remote_status == "idle":
                    raise ControlError("The device did not accept the remote request") from failure
                raise PartialWrite(
                    "Remote request uncertain; watchdog remains authoritative"
                ) from failure

    async def _send_heartbeat(self):
        self._heartbeat = (self._heartbeat + 1) & 0xFFFF
        await self.profile.heartbeat(self.client, self._heartbeat)

    async def _lease_loop(self, generation):
        try:
            while generation == self._lease_generation and self.remote_deadline is not None:
                remaining = self.remote_deadline - self._clock()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(10, remaining))
                async with self._lock:
                    if (
                        generation != self._lease_generation
                        or self.remote_deadline is None
                        or self._clock() >= self.remote_deadline
                    ):
                        break
                    await self._identity()
                    await self._send_heartbeat()
        except EnergyLibError:
            # A lost heartbeat means the device may have left early: stays unconfirmed.
            self._poll_uncertain = False
            self.last_command = "uncertain"
            self.remote_status = "unconfirmed"
        finally:
            async with self._lock:
                if generation == self._lease_generation:
                    try:
                        await self._identity()
                        await self.profile.leave_remote(self.client)
                        await self._verify_connection()
                        # A confirmed exit resolves doubt left by a failed poll, but not
                        # by a failed heartbeat or command.
                        if self.remote_status != "unconfirmed" or self._poll_uncertain:
                            self.remote_status = "completed"
                            self.last_command = "confirmed"
                    except EnergyLibError:
                        self.last_command = "uncertain"
                        self.remote_status = "unconfirmed"
                    finally:
                        self.remote_deadline = None
                        self.remote_end_time = None
                        self._lease_task = None
                        self._poll_uncertain = False
                        await self._update_controls_best_effort()
                        if not self.controls:
                            self.remote_status = "unconfirmed"
                        self._remote_changed()

    def _cancel_lease_locked(self):
        """Invalidate the worker before cancellation; caller owns the device lock."""
        self._lease_generation += 1
        task, self._lease_task = self._lease_task, None
        self.remote_deadline = None
        self.remote_end_time = None
        self._poll_uncertain = False
        if task is not None:
            task.cancel()
        return task

    async def stop_remote(self):
        self._gate()
        task = None
        try:
            async with self._lock:
                await self._prepare_locked()
                self._gate()
                task = self._cancel_lease_locked()
                try:
                    await self._identity()
                    await self.profile.leave_remote(self.client)
                    await self._verify_connection()
                    self.last_command = "confirmed"
                    self.remote_status = "stopped"
                except EnergyLibError:
                    self.last_command = "uncertain"
                    self.remote_status = "unconfirmed"
                    raise
                finally:
                    await self._update_controls_best_effort()
                    if not self.controls:
                        self.remote_status = "unconfirmed"
                    self._remote_changed()
        finally:
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)

    async def close(self):
        task = None
        try:
            async with self._lock:
                self._closed = True
                owned_remote = self.remote_deadline is not None
                task = self._cancel_lease_locked()
                if owned_remote and self.profile.controls_supported:
                    try:
                        await self._identity(allow_closed=True)
                        await self.profile.leave_remote(self.client)
                    except EnergyLibError:
                        self.last_command = "uncertain"
                await self.client.close()
        finally:
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)

    def schedule_status(self, now: datetime):
        if self.schedule is None:
            return None
        if self.controls.get("remote_control"):
            return "remote"
        if self.controls.get("work_mode") != "time_of_use":
            return "inactive"
        p = self.schedule.active(now)
        if p:
            return p.type
        until = self.schedule.valid_until
        return "expired" if until is not None and until < _calendar_day(now) else "outside_period"


def _calendar_day(day):
    """The device stores local calendar dates as UTC midnights (see TouSchedule.active)."""
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
