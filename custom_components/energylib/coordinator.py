"""HA polling adapter; the library owns protocol and control coordination."""

import asyncio
import logging
import time
from datetime import timedelta

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, REMOTE_OPERATION_ENTITIES, RETIRED_ALARM_ENTITIES
from .energylib_core.model.points import FLOW_INTERVAL
from .energylib_core.model.remote import RemoteDraft
from .energylib_core.runtime import ControlError, PartialWrite
from .energylib_core.transport.modbus_tcp import EnergyLibError

_LOGGER = logging.getLogger(__name__)


class EnergyLibCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, device):
        # Tick at the fastest tier; the runtime reads slower points only when due.
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=FLOW_INTERVAL),
        )
        self.entry, self.device = entry, device
        self.remote_enabled = False
        self._native_activation_pending = False
        self._command_lock = asyncio.Lock()
        self.last_command_timing = None
        self._saved_alarm_revision = None
        self.store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.state")
        device.state_changed = self.async_update_listeners

    async def async_load_state(self):
        state = await self.store.async_load() or {}
        self.device.alarms.restore(state.get("alarms"))
        self._saved_alarm_revision = self.device.alarms.revision()
        self.device.draft.restore(state.get("draft"))
        self.remote_enabled = state.get("remote_enabled") is True
        if self.remote_enabled and self.device.allow_control:
            self._sync_remote_entities(True)

    def _remote_entity_changes(self, enabled):
        registry = er.async_get(self.hass)
        targets = dict(REMOTE_OPERATION_ENTITIES)
        if enabled:
            targets["allow_remote_control"] = "switch"
        changes = []
        for key, domain in targets.items():
            entity_id = registry.async_get_entity_id(
                domain, DOMAIN, f"{self.device.unique_id}_{key}"
            )
            entity = registry.async_get(entity_id) if entity_id else None
            if entity is None or entity.config_entry_id != self.entry.entry_id:
                continue
            # A feature toggle must not override disabled devices/config entries.
            if entity.disabled_by not in (
                None,
                er.RegistryEntryDisabler.USER,
                er.RegistryEntryDisabler.INTEGRATION,
            ):
                continue
            target = None if enabled else er.RegistryEntryDisabler.INTEGRATION
            if entity.disabled_by != target:
                changes.append((entity.entity_id, entity.disabled_by, target))
        return changes

    def _sync_remote_entities(self, enabled):
        registry = er.async_get(self.hass)
        applied = []
        previous_pending = self._native_activation_pending
        try:
            changes = self._remote_entity_changes(enabled)
            if enabled and changes and self.entry.supports_unload:
                # Old states can briefly survive a rapid off/on. Do not mistake
                # them for entities that survived HA's queued enable reload.
                self._native_activation_pending = True
            elif not enabled:
                self._native_activation_pending = False
            for entity_id, previous, target in changes:
                registry.async_update_entity(entity_id, disabled_by=target)
                applied.append((entity_id, previous))
        except Exception:
            self._native_activation_pending = previous_pending
            for entity_id, previous in reversed(applied):
                registry.async_update_entity(entity_id, disabled_by=previous)
            raise

    @property
    def remote_entities_ready(self):
        """HA may reload after enabling entities; block starts until they are live."""
        if self._native_activation_pending:
            return False
        registry = er.async_get(self.hass)
        for key, domain in {**REMOTE_OPERATION_ENTITIES, "allow_remote_control": "switch"}.items():
            entity_id = registry.async_get_entity_id(
                domain, DOMAIN, f"{self.device.unique_id}_{key}"
            )
            entity = registry.async_get(entity_id) if entity_id else None
            state = self.hass.states.get(entity_id) if entity_id else None
            if (
                entity is None
                or entity.disabled_by
                or state is None
                or state.attributes.get("restored")
            ):
                return False
        return True

    def _state(self):
        return {
            "alarms": self.device.alarms.export(),
            "draft": self.device.draft.export(),
            "remote_enabled": self.remote_enabled,
        }

    async def async_set_remote_enabled(self, enabled):
        """Link native entities to permission; confirm any active exit first."""
        async with self._command_lock:
            try:
                self.device._gate()
                if (
                    enabled
                    and self._remote_entity_changes(True)
                    and (
                        self.device.remote_deadline is not None
                        or self.device.controls.get("remote_control")
                        or self.device.remote_status == "unconfirmed"
                    )
                ):
                    raise HomeAssistantError(
                        "Stop the active task before restoring native entities"
                    )
                if not enabled and (
                    self.device.remote_deadline is not None
                    or self.device.controls.get("remote_control")
                    or self.device.remote_status == "unconfirmed"
                ):
                    await self._async_command("stop_remote")
                    if (
                        self.device.remote_status != "stopped"
                        or self.device.controls.get("remote_control") != 0
                    ):
                        raise HomeAssistantError(
                            "Remote exit is unconfirmed; access remains enabled"
                        )
            except (EnergyLibError, ValueError) as err:
                raise HomeAssistantError(str(err)) from err
            previous = self.remote_enabled
            self.remote_enabled = bool(enabled)
            try:
                await self.async_save_state()
                self._sync_remote_entities(bool(enabled))
            except Exception:
                self.remote_enabled = previous
                await self.async_save_state()
                raise
            self.async_update_listeners()

    async def async_set_draft(self, key, value):
        async with self._command_lock:
            try:
                self.device._gate()
                candidate = RemoteDraft()
                candidate.restore(self.device.draft.export())
                candidate.set(key, value, self.device.profile.max_power_kw)
            except (EnergyLibError, ValueError) as err:
                raise HomeAssistantError(str(err)) from err
            await self._async_commit_draft(candidate)

    async def async_set_remote_parameters(self, power_kw, duration_minutes):
        """Validate the whole local form before committing either parameter."""
        async with self._command_lock:
            try:
                self.device._gate()
                candidate = RemoteDraft()
                candidate.set("remote_target_power", power_kw, self.device.profile.max_power_kw)
                candidate.set("remote_duration", duration_minutes, self.device.profile.max_power_kw)
            except (EnergyLibError, ValueError) as err:
                raise HomeAssistantError(str(err)) from err
            await self._async_commit_draft(candidate)

    async def _async_commit_draft(self, candidate):
        # Neither entry data/options nor the transport are touched.
        previous, self.device.draft = self.device.draft, candidate
        try:
            await self.async_save_state()
        except Exception:
            self.device.draft = previous
            raise
        self.async_update_listeners()

    async def async_save_state(self):
        await self.store.async_save(self._state())

    def _alarm_changes(self):
        events, self.device.alarms.events = self.device.alarms.events, []
        if events:
            device = dr.async_get(self.hass).async_get_device_by_identifier(
                (DOMAIN, self.device.unique_id), self.entry.entry_id
            )
            if device:
                for event in events:
                    self.hass.bus.async_fire(
                        "energylib_alarm_changed", {"device_id": device.id, **event}
                    )
        # Write only when occurrences change: polls refresh last_seen every 30 s,
        # which unload/stop still persist, and must not wear out SD cards.
        revision = self.device.alarms.revision()
        if events or revision != self._saved_alarm_revision:
            self._saved_alarm_revision = revision
            self.store.async_delay_save(self._state, 1)

    @property
    def identity(self):
        return self.device.identity

    def enabled_keys(self):
        registry = er.async_get(self.hass)
        result = set()
        for p in self.device.points():
            if p.key in RETIRED_ALARM_ENTITIES:
                continue
            domain = "binary_sensor" if p.kind == "problem" else "sensor"
            entity_id = registry.async_get_entity_id(
                domain, DOMAIN, f"{self.device.unique_id}_{p.key}"
            )
            if entity_id is not None:
                if registry.async_get(entity_id).disabled_by is None:
                    result.add(p.key)
            elif p.enabled or (p.key == "battery_soc" and self.entry.data.get("legacy_soc")):
                result.add(p.key)
        return result

    async def _async_update_data(self):
        try:
            data = await self.device.refresh(self.enabled_keys())
            if self.device.poll_deferred and not self.last_update_success:
                # Yielding to a command is not proof that an offline device
                # recovered; keep previous unavailable states until a real read.
                raise UpdateFailed("Waiting for control before communication recovery")
            return data
        except (EnergyLibError, ValueError) as err:
            raise UpdateFailed("Unable to read configured inverter") from err
        finally:
            self._alarm_changes()

    async def async_command(self, command, value=None, **kwargs):
        queued = time.monotonic()
        # Announce even while another command owns the HA lock: background
        # polling must not slip between already queued user actions.
        with self.device.prioritize_control():
            async with self._command_lock:
                acquired = time.monotonic()
                try:
                    await self._async_serial_command(command, value, **kwargs)
                finally:
                    finished = time.monotonic()
                    self.last_command_timing = {
                        "command": command,
                        "queue_ms": round((acquired - queued) * 1000, 1),
                        "service_ms": round((finished - acquired) * 1000, 1),
                        "total_ms": round((finished - queued) * 1000, 1),
                    }

    async def _async_serial_command(self, command, value=None, **kwargs):
        if command in (
            "power",
            "start_charge",
            "start_discharge",
            "remote_power",
            "remote_control",
        ):
            if not self.remote_enabled:
                raise HomeAssistantError("Enable remote charge/discharge before starting a task")
            if not self.remote_entities_ready:
                raise HomeAssistantError(
                    "Remote entities are loading; wait for Home Assistant to finish enabling them"
                )
        await self._async_command(command, value, **kwargs)

    async def _async_command(self, command, value=None, **kwargs):
        completed = False
        rejected = False
        try:
            if command == "schedule":
                await self.device.set_schedule(value, **kwargs)
            elif command == "power":
                await self.device.set_battery_power(value, kwargs["duration"])
            elif command in ("start_charge", "start_discharge"):
                self.device._gate()
                power, seconds = self.device.draft.snapshot(
                    "charge" if command == "start_charge" else "discharge",
                    self.device.profile.max_power_kw,
                )
                await self.device.set_battery_power(power, seconds)
            elif command == "stop_remote":
                await self.device.stop_remote()
            else:
                await self.device.set_control(command, value)
            completed = True
        except (EnergyLibError, ValueError) as err:
            # Basic ControlError reaches here before any write. Once a write
            # starts the runtime wraps failures as PartialWrite instead.
            rejected = isinstance(err, ControlError) and not isinstance(err, PartialWrite)
            if rejected:
                self.device.last_command = "rejected"
            raise HomeAssistantError(str(err)) from err
        finally:
            basic = command in self.device.profile.control_keys and command not in (
                "remote_power",
                "remote_control",
            )
            if (
                (completed or rejected)
                and basic
                and self.last_update_success
                and self.data is not None
                and self.device.controls
            ):
                # The runtime already read back the control state. Notify now,
                # keeping the normal polling timer and measurement timestamps.
                # async_set_updated_data would postpone that timer on each edit.
                self.data = {key: self.device.values.get(key) for key in self.data}
                self.async_update_listeners()
            else:
                # Failures, recovery, TOU and dispatch still require full refresh.
                self.device._timestamps.clear()
                await self.async_refresh()
