"""Sensors for Boiler Demand."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, PERCENTAGE, UnitOfTemperature
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from . import BoilerDemandConfigEntry
from .const import DOMAIN
from .controller import BoilerDemandController
from .discovery import valve_name
from .entity import BoilerDemandEntity
from .logic import STATUSES


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BoilerDemandConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors."""
    controller = entry.runtime_data
    _remove_deselected_rooms(hass, entry, controller)

    entities: list[SensorEntity] = [
        StatusSensor(controller, "status"),
        MaxValveOpeningSensor(controller, "max_valve_opening"),
        BoilerStartsTodaySensor(controller, "boiler_starts_today"),
    ]
    for valve in controller.valves:
        room = _room_device_info(hass, controller, valve)
        entities.append(ValveSensor(controller, valve, room))
        if temperature := controller.temperatures.get(valve):
            entities.append(RoomTemperatureSensor(controller, valve, temperature, room))
    async_add_entities(entities)


def _room_identifier(entry_id: str, valve: str) -> tuple[str, str]:
    return (DOMAIN, f"{entry_id}_room_{valve}")


def _valve_unique_id(entry_id: str, valve: str) -> str:
    return f"{entry_id}_valve_{valve}"


def _temperature_unique_id(entry_id: str, valve: str) -> str:
    return f"{entry_id}_temperature_{valve}"


def _room_device_info(
    hass: HomeAssistant, controller: BoilerDemandController, valve: str
) -> DeviceInfo:
    """One device per room, linked to the Boiler Demand device, in the valve's area."""
    entry_id = controller.entry.entry_id
    area_id = None
    if (valve_entry := er.async_get(hass).async_get(valve)) is not None:
        area_id = valve_entry.area_id
        if area_id is None and valve_entry.device_id is not None:
            device = dr.async_get(hass).async_get(valve_entry.device_id)
            area_id = device.area_id if device else None
    info = DeviceInfo(
        identifiers={_room_identifier(entry_id, valve)},
        name=valve_name(hass, valve),
        manufacturer="Boiler Demand",
        model="Room",
        entry_type=dr.DeviceEntryType.SERVICE,
    )
    # HA >= 2026.8 links by registry id; older versions by identifier tuple.
    if "via_device_id" in DeviceInfo.__annotations__ and controller.device_id:
        info["via_device_id"] = controller.device_id
    else:
        info["via_device"] = (DOMAIN, entry_id)
    if area_id is not None and (area := ar.async_get(hass).async_get_area(area_id)):
        info["suggested_area"] = area.name
    return info


def _remove_deselected_rooms(
    hass: HomeAssistant, entry: BoilerDemandConfigEntry, controller: BoilerDemandController
) -> None:
    """Drop room entities and devices that are no longer configured."""
    entry_id = entry.entry_id
    wanted = {_valve_unique_id(entry_id, valve) for valve in controller.valves} | {
        _temperature_unique_id(entry_id, valve)
        for valve, temperature in controller.temperatures.items()
        if temperature and valve in controller.valves
    }
    prefixes = (_valve_unique_id(entry_id, ""), _temperature_unique_id(entry_id, ""))
    ent_reg = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(ent_reg, entry_id):
        if entity.unique_id.startswith(prefixes) and entity.unique_id not in wanted:
            ent_reg.async_remove(entity.entity_id)

    rooms = {_room_identifier(entry_id, valve) for valve in controller.valves}
    dev_reg = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(dev_reg, entry_id):
        room_ids = {i for i in device.identifiers if i[1].startswith(f"{entry_id}_room_")}
        if room_ids and not room_ids & rooms:
            dev_reg.async_update_device(device.id, remove_config_entry_id=entry_id)


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
            "remaining_minutes": None if remaining is None else round(remaining / 60, 1),
            # Lets the dashboard card show/link the relay without extra config.
            "boiler_switch": self.controller.switch_entity,
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

    def __init__(
        self, controller: BoilerDemandController, valve: str, room: DeviceInfo
    ) -> None:
        super().__init__(controller, "valve_opening")
        self._valve = valve
        self._attr_unique_id = _valve_unique_id(controller.entry.entry_id, valve)
        self._attr_device_info = room

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


class RoomTemperatureSensor(BoilerDemandEntity, SensorEntity):
    """Room temperature mirrored from the sensor assigned to a valve."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        controller: BoilerDemandController,
        valve: str,
        source: str,
        room: DeviceInfo,
    ) -> None:
        super().__init__(controller, "room_temperature")
        self._source = source
        self._attr_unique_id = _temperature_unique_id(controller.entry.entry_id, valve)
        self._attr_device_info = room
        self._attr_extra_state_attributes = {"source_entity": source}
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._update_from_source()
        self.async_on_remove(
            async_track_state_change_event(self.hass, [self._source], self._source_changed)
        )

    @callback
    def _source_changed(self, _event: Event[EventStateChangedData]) -> None:
        self._update_from_source()
        self.async_write_ha_state()

    @callback
    def _update_from_source(self) -> None:
        state = self.hass.states.get(self._source)
        value = None
        if state is not None:
            if unit := state.attributes.get(ATTR_UNIT_OF_MEASUREMENT):
                self._attr_native_unit_of_measurement = unit
            try:
                value = float(state.state)
            except ValueError:
                pass
        self._attr_native_value = value


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
