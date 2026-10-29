"""Serve the EnergyLIB dashboard strategy, and show it in the sidebar automatically."""

import hashlib
from pathlib import Path

from homeassistant.core import HomeAssistant

from .const import DOMAIN

URL_BASE = "/energylib_static"
STRATEGY_FILE = "energylib-dashboard.js"
FRONTEND_DIR = Path(__file__).parent / "frontend"
TWIN_DIR = Path(__file__).parent / "twin"
_REGISTERED = f"{DOMAIN}_dashboard_registered"


def _content_hash() -> str:
    """Hash of the strategy, its translations and the twin files (all reuse ?v=)."""
    digest = hashlib.sha256((FRONTEND_DIR / STRATEGY_FILE).read_bytes())
    for folder in (FRONTEND_DIR / "translations", TWIN_DIR):
        for path in sorted(p for p in folder.rglob("*") if p.is_file()):
            digest.update(path.relative_to(folder.parent).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:10]


async def async_register_dashboard(hass: HomeAssistant) -> bool:
    """Register once per HA run; skipped when HA runs without HTTP/frontend."""
    if hass.data.get(_REGISTERED):
        return True
    if getattr(hass, "http", None) is None or "frontend" not in hass.config.components:
        return False
    from homeassistant.components.frontend import add_extra_js_url
    from homeassistant.components.http import StaticPathConfig

    digest = await hass.async_add_executor_job(_content_hash)
    await hass.http.async_register_static_paths(
        [StaticPathConfig(URL_BASE, str(FRONTEND_DIR), cache_headers=True)]
    )
    # Files are cached for weeks; a content hash makes any change load at once.
    add_extra_js_url(hass, f"{URL_BASE}/{STRATEGY_FILE}?v={digest}")
    hass.data[_REGISTERED] = True
    return True


PANEL_URL = "energylib"
STRATEGY_TYPE = "custom:energylib"
_PANEL = f"{DOMAIN}_panel"


def _strategy_dashboard(hass: HomeAssistant):
    """A read-only dashboard whose whole configuration is the EnergyLIB strategy."""
    from homeassistant.components.lovelace.dashboard import LovelaceConfig
    from homeassistant.helpers.json import cached_json_fragment

    config = {"strategy": {"type": STRATEGY_TYPE}}

    class StrategyDashboard(LovelaceConfig):
        @property
        def mode(self) -> str:
            return "yaml"  # Shown as managed elsewhere, so users cannot break it.

        async def async_get_info(self):
            return {"mode": self.mode, "views": 0}

        async def async_load(self, force: bool):
            return dict(config)

        async def async_json(self, force: bool):
            return cached_json_fragment(config)

    meta = {"mode": "yaml", "title": "EnergyLIB", "require_admin": False, "show_in_sidebar": True}
    return StrategyDashboard(hass, PANEL_URL, meta)


async def _user_has_strategy_dashboard(hass: HomeAssistant, dashboards) -> bool:
    """A dashboard the user created from the EnergyLIB strategy (avoid a duplicate)."""
    for url_path, dashboard in list(dashboards.items()):
        if url_path in (None, PANEL_URL):
            continue
        try:
            loaded = await dashboard.async_load(False)
        except Exception:  # noqa: BLE001 - missing or unreadable dashboards are skipped
            continue
        if ((loaded or {}).get("strategy") or {}).get("type") == STRATEGY_TYPE:
            return True
    return False


async def async_register_panel(hass: HomeAssistant) -> bool:
    """Show the EnergyLIB dashboard in the sidebar without the user adding it."""
    if hass.data.get(_PANEL) is not None:
        return hass.data[_PANEL]
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    lovelace = hass.data.get(LOVELACE_DATA)
    if lovelace is None or "frontend" not in hass.config.components:
        return False
    if await _user_has_strategy_dashboard(hass, lovelace.dashboards):
        hass.data[_PANEL] = False
        return False
    from homeassistant.components.frontend import async_register_built_in_panel

    lovelace.dashboards[PANEL_URL] = _strategy_dashboard(hass)
    async_register_built_in_panel(
        hass,
        "lovelace",
        sidebar_title="EnergyLIB",
        sidebar_icon="mdi:home-battery",
        frontend_url_path=PANEL_URL,
        config={"mode": "yaml"},
        require_admin=False,
    )
    hass.data[_PANEL] = True
    return True


def async_unregister_panel(hass: HomeAssistant) -> None:
    """Remove the automatic panel once no EnergyLIB inverter is configured."""
    if not hass.data.pop(_PANEL, None):
        return
    from homeassistant.components.frontend import async_remove_panel
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    async_remove_panel(hass, PANEL_URL)
    if (lovelace := hass.data.get(LOVELACE_DATA)) is not None:
        lovelace.dashboards.pop(PANEL_URL, None)
