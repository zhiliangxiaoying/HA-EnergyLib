"""Bundled digital twin: serve its static files for the EnergyLIB dashboard.

The twin follows Overview in the EnergyLIB dashboard (an iframe card, see
frontend/energylib-dashboard.js). The page reads EnergyLIB entities from the
parent frontend's hass object, so it needs no configuration, credentials or
REST calls of its own.
"""

from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN

TWIN_URL = "/energylib_twin"
TWIN_DIR = Path(__file__).parent / "twin"
_STATIC_REGISTERED = f"{DOMAIN}_twin_static"


async def async_register_twin_files(hass: HomeAssistant) -> None:
    """Register once per HA run; later calls (reloads, tests) change nothing."""
    if hass.data.get(_STATIC_REGISTERED):
        return
    # Claim before awaiting: aiohttp rejects a second route for the same path.
    hass.data[_STATIC_REGISTERED] = True
    try:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(TWIN_URL, str(TWIN_DIR), True)]
        )
    except BaseException:
        hass.data.pop(_STATIC_REGISTERED, None)
        raise
