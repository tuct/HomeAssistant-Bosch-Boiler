"""Heat demand binary sensor."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BoilerDemandConfigEntry
from .entity import BoilerDemandEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BoilerDemandConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the heat demand sensor."""
    async_add_entities([HeatDemandBinarySensor(entry.runtime_data, "heat_demand")])


class HeatDemandBinarySensor(BoilerDemandEntity, BinarySensorEntity):
    """On while the valves ask for heat (after hysteresis)."""

    _attr_device_class = BinarySensorDeviceClass.HEAT

    @property
    def is_on(self) -> bool:
        return self.controller.demand

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        positions = self.controller.positions
        off_threshold = self.controller.settings.off_threshold
        return {
            "valves": dict(positions),
            "open_valves": [v for v, p in positions.items() if p is not None and p > off_threshold],
            "offline_valves": [v for v, p in positions.items() if p is None],
        }
