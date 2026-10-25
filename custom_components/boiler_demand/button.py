"""Reset auto-tune learning, or re-learn it from history."""

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
    """Set up the learning buttons."""
    controller = entry.runtime_data
    async_add_entities(
        [
            ResetLearningButton(controller, "reset_learning"),
            LearnFromHistoryButton(controller, "learn_from_history"),
        ]
    )


class ResetLearningButton(BoilerDemandEntity, ButtonEntity):
    """Forget the learned holding levels, e.g. after changing radiators or valves."""

    _attr_entity_category = EntityCategory.CONFIG

    async def async_press(self) -> None:
        await self.controller.async_reset_learning()


class LearnFromHistoryButton(BoilerDemandEntity, ButtonEntity):
    """Re-learn all holding levels from the recorder's last days."""

    _attr_entity_category = EntityCategory.CONFIG

    async def async_press(self) -> None:
        await self.controller.async_learn_from_history(retrain=True)
