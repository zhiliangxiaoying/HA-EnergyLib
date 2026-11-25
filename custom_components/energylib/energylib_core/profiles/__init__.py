"""Explicit profiles: mDNS never grants control permission."""

from .base import DeviceProfile


def get_profile(profile_id) -> DeviceProfile:
    from .sinexcel_v2 import SinexcelProfile

    profiles = {
        "sinexcel_v2": SinexcelProfile,
        "v001b031_single": SinexcelProfile,
    }
    try:
        return profiles[profile_id]()
    except KeyError as err:
        raise ValueError("Unsupported profile") from err
