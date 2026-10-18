"""Failsafe mode select."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BoilerDemandConfigEntry
from .const import CONF_FAILSAFE_MODE, FAILSAFE_MODES
from .entity import BoilerDemandEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BoilerDemandConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the failsafe mode select."""
    async_add_entities([FailsafeModeSelect(entry.runtime_data, CONF_FAILSAFE_MODE)])


class FailsafeModeSelect(BoilerDemandEntity, SelectEntity):
    """What to do when all valves are offline."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = FAILSAFE_MODES

    @property
    def current_option(self) -> str:
        return str(self.controller.options[CONF_FAILSAFE_MODE])

    async def async_select_option(self, option: str) -> None:
        self.hass.config_entries.async_update_entry(
            self.controller.entry,
            options={**self.controller.entry.options, CONF_FAILSAFE_MODE: option},
        )
