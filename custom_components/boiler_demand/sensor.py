"""Sensors for Boiler Demand."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from . import BoilerDemandConfigEntry
from .entity import BoilerDemandEntity
from .logic import STATUSES


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BoilerDemandConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors."""
    controller = entry.runtime_data
    async_add_entities(
        [
            StatusSensor(controller, "status"),
            MaxValveOpeningSensor(controller, "max_valve_opening"),
            BoilerStartsTodaySensor(controller, "boiler_starts_today"),
        ]
    )


class StatusSensor(BoilerDemandEntity, SensorEntity):
    """What the controller is doing right now."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = STATUSES

    @property
    def native_value(self) -> str:
        return self.controller.status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        remaining = self.controller.remaining_s
        return {
            "remaining_minutes": None if remaining is None else round(remaining / 60, 1)
        }


class MaxValveOpeningSensor(BoilerDemandEntity, SensorEntity):
    """Highest opening among valves with usable data."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | None:
        valid = self.controller.valid_positions
        return max(valid) if valid else None


class BoilerStartsTodaySensor(BoilerDemandEntity, SensorEntity, RestoreEntity):
    """Number of boiler switch-ons since midnight."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is None or dt_util.as_local(last.last_changed).date() != dt_util.now().date():
            return
        try:
            self.controller.starts_today = int(float(last.state))
        except ValueError:
            pass

    @property
    def native_value(self) -> int:
        return self.controller.starts_today
