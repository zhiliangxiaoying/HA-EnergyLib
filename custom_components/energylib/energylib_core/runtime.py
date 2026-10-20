"""One device-level serialized read/write owner and finite remote-control leases."""

import asyncio
import math
import time
from datetime import datetime

from .legacy import IdentityMismatch, decode_float
from .model.points import CONTROLS, MEASUREMENTS
from .model.tou import TouSchedule
from .planner import plan_reads
from .profiles import get_profile
from .transport.modbus_tcp import DeviceException, EnergyLibError


class ControlError(EnergyLibError):
    """Rejected or uncertain command. No private values in exception messages."""


class PartialWrite(ControlError):
    """A command may be partially applied: inspect authoritative read-back."""


class DeviceRuntime:
    def __init__(self, client, profile_id, *, clock=time.monotonic, task_factory=None):
        self.client = client
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
        self.last_frame_count = 0

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
            self._generation = self.client.generation

    async def _identity(self, expected=None):
        identity = await self.profile.identify(self.client)
        if expected is None and self.identity is not None:
            expected = self.identity.serial
        if expected is not None and identity.serial != expected:
            raise IdentityMismatch("Endpoint identity changed")
        self.identity = identity

    def points(self):
        return [
            p
            for p in MEASUREMENTS.values()
            if (
                (p.key in self.profile.mappings or p.kind == "schedule")
                and p.capabilities <= self.profile.capabilities
                and (p.kind != "schedule" or self.allow_control)
            )
        ]

    async def _read(self, mapping):
        if mapping.function == 2:
            return await self.client.read_discrete_inputs(
                mapping.address, mapping.count, unit_id=mapping.unit
            )
        return await self.client.read_registers(
            mapping.function, mapping.address, mapping.count, unit_id=mapping.unit
        )

    async def refresh(self, enabled, *, force=False):
        async with self._lock:
            count = self.client.requests
            try:
                await self._identity()
                if self._generation != self.client.generation:
                    await self.profile.configure(self.client, self.identity)
                    self._generation = self.client.generation
                    force = True
                now = self._clock()
                supported = {p.key for p in self.points()}
                enabled = set(enabled) & supported
                due = {
                    k
                    for k in enabled
                    if force or now - self._timestamps.get(k, -math.inf) >= MEASUREMENTS[k].interval
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
                if self.allow_control and self.profile.controls_supported:
                    self.controls = await self.profile.read_controls(self.client)
                self.last_frame_count = self.client.requests - count
                return {k: self.values.get(k) for k in enabled}
            except BaseException:
                self._timestamps.clear()  # Recovery must not publish stale slow-tier values.
                raise

    def _decode(self, key, raw):
        maps = self.profile.mappings.get(key, ())
        parts = [raw.get(m) for m in maps]
        self.attributes.pop(key, None)
        if key == "active_alarms":
            alarms = [
                {"source": m.unit, "code": f"{m.address + i:04X}"}
                for m, bits in zip(maps, parts)
                if bits is not None
                for i, bit in enumerate(bits)
                if bit
            ]
            complete = all(p is not None for p in parts)
            self.attributes[key] = {"alarms": alarms, "complete": complete}
            self.values[key] = len(alarms) if complete else None
            return
        if not parts or any(p is None for p in parts):
            self.values[key] = None
            return
        if key in ("grid_fault", "bms_comm_fault"):
            self.values[key] = any(any(p) for p in parts)
        elif key == "running_state":
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
        async with self._lock:
            self._gate()
            await self._identity()
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
        except EnergyLibError:
            self.controls = {}

    async def set_schedule(self, data, *, activate=False):
        self._gate()
        if type(activate) is not bool:
            raise ControlError("activate must be boolean")
        if "ctrl_tou" not in self.profile.capabilities or self.profile.tou_limits is None:
            raise ControlError("Device does not expose the TOU registers")
        schedule = TouSchedule.parse(data, self.profile.tou_limits)
        async with self._lock:
            self._gate()
            await self._identity()
            actual = await self.profile.read_controls(self.client)
            if actual["remote_control"]:
                raise ControlError("Stop remote control before editing TOU")
            previous = actual["work_mode"]
            mode = next(c for c in CONTROLS if c.key == "work_mode")
            if previous not in self.control_options(mode):
                raise ControlError("Cannot safely restore current mode")
            if schedule.outside_mode is None:
                current = await self.profile.read_schedule(self.client)
                data = {**schedule.as_dict(), "outside_mode": current.outside_mode}
                schedule = TouSchedule.parse(data, self.profile.tou_limits)
            try:
                await self.profile.write_schedule(self.client, schedule)
                readback = await self.profile.read_schedule(self.client)
                if not self.profile.schedules_equal(readback, schedule):
                    raise PartialWrite("TOU read-back mismatch")
                self.schedule = readback
                await self.profile.apply_control(
                    self.client, "work_mode", "time_of_use" if activate else previous
                )
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
        if (
            not self.profile.max_power_kw
            or abs(power_kw) > self.profile.max_power_kw
            or not 1 <= duration <= 86400
        ):
            raise ControlError("Power or duration outside device limits")
        async with self._lock:
            self._gate()
            await self._identity()
            actual = await self.profile.read_controls(self.client)
            if actual["work_mode"] == "time_of_use":
                raise ControlError("Leave TOU before starting remote control")
            if actual["remote_control"] and self.remote_deadline is None:
                raise ControlError("An unowned remote session must first be stopped")
            try:
                await self.profile.enter_remote(self.client, power_kw)
                await self._send_heartbeat()
                self.remote_deadline = self._clock() + duration
                # A renewed lease has its own owner. An old worker may already
                # be waiting to clean up after expiry or a heartbeat failure.
                self._lease_generation += 1
                if self._lease_task is not None:
                    self._lease_task.cancel()
                self._lease_task = self._task_factory(self._lease_loop(self._lease_generation))
                self.last_command = "confirmed"
            except EnergyLibError as err:
                self.last_command = "uncertain"
                raise PartialWrite(
                    "Remote request uncertain; watchdog remains authoritative"
                ) from err
            finally:
                await self._update_controls_best_effort()

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
            self.last_command = "uncertain"
        finally:
            async with self._lock:
                if generation == self._lease_generation:
                    try:
                        await self._identity()
                        await self.profile.leave_remote(self.client)
                    except EnergyLibError:
                        self.last_command = "uncertain"
                    finally:
                        self.remote_deadline = None
                        self._lease_task = None
                        await self._update_controls_best_effort()

    def _cancel_lease_locked(self):
        """Invalidate the worker before cancellation; caller owns the device lock."""
        self._lease_generation += 1
        task, self._lease_task = self._lease_task, None
        self.remote_deadline = None
        if task is not None:
            task.cancel()
        return task

    async def stop_remote(self):
        self._gate()
        task = None
        try:
            async with self._lock:
                self._gate()
                task = self._cancel_lease_locked()
                try:
                    await self._identity()
                    await self.profile.leave_remote(self.client)
                    self.last_command = "confirmed"
                except EnergyLibError:
                    self.last_command = "uncertain"
                    raise
                finally:
                    await self._update_controls_best_effort()
        finally:
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)

    async def close(self):
        task = None
        try:
            async with self._lock:
                owned_remote = self.remote_deadline is not None
                task = self._cancel_lease_locked()
                if owned_remote and self.profile.controls_supported:
                    try:
                        await self._identity()
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
        return p.type if p else "outside_period"
