"""Serve the Boiler Demand Lovelace card and load it in the frontend."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN

CARD_URL = f"/{DOMAIN}/boiler-demand-card.js"
CARD_PATH = Path(__file__).parent / "www" / "boiler-demand-card.js"


async def async_register_card(hass: HomeAssistant) -> None:
    """Serve the card JS and add it to every dashboard (no manual resource needed)."""
    if getattr(hass, "http", None) is None:  # e.g. tests without http
        return
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(CARD_PATH), cache_headers=False)]
    )
    if "frontend" in hass.config.components:
        from homeassistant.components.frontend import add_extra_js_url

        version = (await async_get_integration(hass, DOMAIN)).version
        # Version in the URL so browsers pick up a new card after an update.
        add_extra_js_url(hass, f"{CARD_URL}?v={version}")
