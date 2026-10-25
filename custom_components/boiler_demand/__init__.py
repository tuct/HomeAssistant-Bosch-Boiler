"""Boiler Demand: switch a boiler from radiator valve positions."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .card import async_register_card
from .const import DOMAIN
from .controller import STORAGE_VERSION, BoilerDemandController, storage_key
from .entity import main_device_info

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
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
    # Register the main device first so room devices can link to it; the integration
    # version shows on the device page.
    version = (await async_get_integration(hass, DOMAIN)).version
    controller.device_id = (
        dr.async_get(hass)
        .async_get_or_create(
            config_entry_id=entry.entry_id,
            sw_version=str(version) if version else None,
            **main_device_info(entry),
        )
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


async def async_remove_entry(hass: HomeAssistant, entry: BoilerDemandConfigEntry) -> None:
    """Delete the learned auto-tune data with the entry."""
    await Store(hass, STORAGE_VERSION, storage_key(entry.entry_id)).async_remove()


async def _async_update_listener(hass: HomeAssistant, entry: BoilerDemandConfigEntry) -> None:
    """Reload if watched entities changed, otherwise apply tunables in place."""
    controller = entry.runtime_data
    if controller.needs_reload(entry.options):
        await hass.config_entries.async_reload(entry.entry_id)
    else:
        await controller.async_apply_options(entry.options)
