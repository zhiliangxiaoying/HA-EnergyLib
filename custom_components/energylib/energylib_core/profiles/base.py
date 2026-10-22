"""Minimum read-profile contract; control hooks are capability-gated."""

from typing import Protocol

from ..legacy import DeviceIdentity
from ..model.tou import TouLimits


class DeviceProfile(Protocol):
    id: str
    version: int
    namespace: str
    manufacturer: str
    device_name: str
    work_mode_codes: dict[int, str]  # Device code -> standard WORK_MODES name.
    register_limit: int
    bit_limit: int
    controls_supported: bool  # Device answers the control registers (set by configure).
    controls_hw_verified: bool  # Control behaviour confirmed on physical hardware.
    power_direction_verified: bool
    max_power_kw: float | None
    control_keys: frozenset[str]
    tou_limits: TouLimits | None
    capabilities: set[str]
    mappings: dict
    single_battery_confirmed: bool

    async def identify(self, client) -> DeviceIdentity: ...
    async def configure(self, client, identity: DeviceIdentity) -> None: ...
    async def read_discrete_inputs(self, client, address, count, *, unit_id=None): ...
    def decode_enum(self, key, raw): ...
    def describe_alarm(self, function, unit, address): ...
