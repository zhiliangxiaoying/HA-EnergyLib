"""Compatibility API; HA uses the profile-aware core runtime."""

from ..energylib_core.legacy import (  # noqa: F401
    POINTS,
    DeviceIdentity,
    EnergyLibDevice,
    IdentityMismatch,
    Point,
    UnsupportedDevice,
    decode_float,
    decode_serial,
    decode_version,
)
