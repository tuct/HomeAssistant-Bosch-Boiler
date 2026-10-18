"""Boiler Demand: switch a boiler from radiator valve positions."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .card import async_register_card
from .const import DOMAIN
from .controller import BoilerDemandController
from .entity import main_device_info

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

type BoilerDemandConfigEntry = ConfigEntry[BoilerDemandController]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the dashboard card once per Home Assistant run."""
    await async_register_card(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: BoilerDemandConfigEntry) -> bool:
    """Set up Boiler Demand from a config entry."""
    controller = BoilerDemandController(hass, entry)
    entry.runtime_data = controller
    # Register the main device first so room devices can link to it.
    controller.device_id = (
        dr.async_get(hass)
        .async_get_or_create(config_entry_id=entry.entry_id, **main_device_info(entry))
        .id
    )

    # Entities restore state (automatic control, starts today) before the controller runs.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await controller.async_start()

    entry.async_on_unload(controller.async_stop)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BoilerDemandConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(hass: HomeAssistant, entry: BoilerDemandConfigEntry) -> None:
    """Reload if watched entities changed, otherwise apply tunables in place."""
    controller = entry.runtime_data
    if controller.needs_reload(entry.options):
        await hass.config_entries.async_reload(entry.entry_id)
    else:
        await controller.async_apply_options(entry.options)
