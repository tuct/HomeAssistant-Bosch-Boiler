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
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from . import BoilerDemandConfigEntry
from .controller import BoilerDemandController
from .entity import BoilerDemandEntity
from .logic import STATUSES


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BoilerDemandConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors."""
    controller = entry.runtime_data
    _remove_deselected_valves(hass, entry, controller)
    async_add_entities(
        [
            StatusSensor(controller, "status"),
            MaxValveOpeningSensor(controller, "max_valve_opening"),
            BoilerStartsTodaySensor(controller, "boiler_starts_today"),
            *(
                ValveSensor(controller, valve, _valve_name(hass, valve))
                for valve in controller.valves
            ),
        ]
    )


def _valve_unique_id(entry_id: str, valve: str) -> str:
    return f"{entry_id}_valve_{valve}"


def _remove_deselected_valves(
    hass: HomeAssistant, entry: BoilerDemandConfigEntry, controller: BoilerDemandController
) -> None:
    """Drop valve sensors of valves no longer selected in the options."""
    registry = er.async_get(hass)
    prefix = _valve_unique_id(entry.entry_id, "")
    wanted = {_valve_unique_id(entry.entry_id, valve) for valve in controller.valves}
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.unique_id.startswith(prefix) and entity.unique_id not in wanted:
            registry.async_remove(entity.entity_id)


def _valve_name(hass: HomeAssistant, valve: str) -> str:
    """Name of the valve's device (e.g. the room thermostat), else its entity name."""
    entity = er.async_get(hass).async_get(valve)
    if entity is not None and entity.device_id is not None:
        device = dr.async_get(hass).async_get(entity.device_id)
        if device is not None and (name := device.name_by_user or device.name):
            return name
    if (state := hass.states.get(valve)) is not None:
        return state.name
    return valve


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


class ValveSensor(BoilerDemandEntity, SensorEntity):
    """One selected valve, as seen by the controller (unknown = ignored/offline)."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, controller: BoilerDemandController, valve: str, name: str) -> None:
        super().__init__(controller, "valve")
        self._valve = valve
        self._attr_unique_id = _valve_unique_id(controller.entry.entry_id, valve)
        self._attr_name = name

    @property
    def native_value(self) -> float | None:
        return self.controller.positions.get(self._valve)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        position = self.controller.positions.get(self._valve)
        settings = self.controller.settings
        if position is None:
            role = "offline"
        elif position >= settings.on_threshold:
            role = "demand"
        elif position > settings.off_threshold:
            role = "holding"
        else:
            role = "closed"
        return {"source_entity": self._valve, "role": role}


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
