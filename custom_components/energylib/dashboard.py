"""Serve the EnergyLIB dashboard strategy so users can add the page from the UI."""

from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN

URL_BASE = "/energylib_static"
STRATEGY_FILE = "energylib-dashboard.js"
FRONTEND_DIR = Path(__file__).parent / "frontend"
_REGISTERED = f"{DOMAIN}_dashboard_registered"


async def async_register_dashboard(hass: HomeAssistant) -> bool:
    """Register once per HA run; skipped when HA runs without HTTP/frontend."""
    if hass.data.get(_REGISTERED):
        return True
    if getattr(hass, "http", None) is None or "frontend" not in hass.config.components:
        return False
    from homeassistant.components.frontend import add_extra_js_url
    from homeassistant.components.http import StaticPathConfig

    version = (await async_get_integration(hass, DOMAIN)).version
    await hass.http.async_register_static_paths(
        [StaticPathConfig(URL_BASE, str(FRONTEND_DIR), cache_headers=True)]
    )
    # The version query makes browsers fetch the new script after an update.
    add_extra_js_url(hass, f"{URL_BASE}/{STRATEGY_FILE}?v={version}")
    hass.data[_REGISTERED] = True
    return True
