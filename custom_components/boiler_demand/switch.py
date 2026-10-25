"""Automatic control switch."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_OFF, STATE_ON, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import BoilerDemandConfigEntry
from .entity import BoilerDemandEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BoilerDemandConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the automatic control and auto-tune switches."""
    controller = entry.runtime_data
    async_add_entities(
        [
            AutomaticControlSwitch(controller, "automatic_control"),
            AutoTuneSwitch(controller, "auto_tune"),
        ]
    )


class AutomaticControlSwitch(BoilerDemandEntity, SwitchEntity, RestoreEntity):
    """When off, demand is still computed but the boiler switch is left alone."""

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            self.controller.enabled = last.state != STATE_OFF

    @property
    def is_on(self) -> bool:
        return self.controller.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.controller.async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.controller.async_set_enabled(False)


class AutoTuneSwitch(BoilerDemandEntity, SwitchEntity, RestoreEntity):
    """Use learned per-valve thresholds, on delay and adaptive minimum off time.

    Learning always runs; this only decides whether the result is used. Off by default.
    """

    _attr_entity_category = EntityCategory.CONFIG

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            self.controller.auto_tune = last.state == STATE_ON

    @property
    def is_on(self) -> bool:
        return self.controller.auto_tune

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.controller.async_set_auto_tune(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.controller.async_set_auto_tune(False)
