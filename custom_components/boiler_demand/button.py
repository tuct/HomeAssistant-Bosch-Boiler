"""Reset auto-tune learning."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BoilerDemandConfigEntry
from .entity import BoilerDemandEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BoilerDemandConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the reset button."""
    async_add_entities([ResetLearningButton(entry.runtime_data, "reset_learning")])


class ResetLearningButton(BoilerDemandEntity, ButtonEntity):
    """Forget the learned holding levels, e.g. after changing radiators or valves."""

    _attr_entity_category = EntityCategory.CONFIG

    async def async_press(self) -> None:
        await self.controller.async_reset_learning()
