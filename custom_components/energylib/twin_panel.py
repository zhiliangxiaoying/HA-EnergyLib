"""Bundled digital twin: serve its static files and add a sidebar iframe panel.

The page reads EnergyLIB entities from the parent frontend's hass object, so the
panel needs no configuration, credentials or REST calls of its own.
"""

from pathlib import Path

from homeassistant.components.frontend import async_register_built_in_panel
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN

TWIN_URL = "/energylib_twin"
TWIN_DIR = Path(__file__).parent / "twin"
PANEL_URL_PATH = "energylib-twin"
PANEL_TITLE = "EnergyLIB 数字孪生"
PANEL_ICON = "mdi:home-lightning-bolt-outline"
_STATIC_REGISTERED = f"{DOMAIN}_twin_static"


async def async_register_twin_panel(hass: HomeAssistant) -> None:
    """Register once per HA run; later calls (reloads, tests) change nothing."""
    if not hass.data.get(_STATIC_REGISTERED):
        # Claim before awaiting: aiohttp rejects a second route for the same path.
        hass.data[_STATIC_REGISTERED] = True
        try:
            await hass.http.async_register_static_paths(
                [StaticPathConfig(TWIN_URL, str(TWIN_DIR), True)]
            )
        except BaseException:
            hass.data.pop(_STATIC_REGISTERED, None)
            raise
    # Files are cached by the browser; the version makes upgrades fetch new ones.
    version = (await async_get_integration(hass, DOMAIN)).version
    async_register_built_in_panel(
        hass,
        "iframe",
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        frontend_url_path=PANEL_URL_PATH,
        config={"url": f"{TWIN_URL}/index.html?v={version}"},
        require_admin=False,
        update=True,
    )
