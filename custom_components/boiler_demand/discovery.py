"""Auto-detection of radiator valve and room temperature sensors."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import ATTR_DEVICE_CLASS, ATTR_UNIT_OF_MEASUREMENT, PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import DOMAIN, VALVE_KEYWORDS


def find_valve_candidates(hass: HomeAssistant) -> list[str]:
    """Return entity ids of sensors that look like valve-opening sensors."""
    registry = er.async_get(hass)
    found: list[str] = []
    for entry in registry.entities.values():
        if entry.domain != "sensor" or entry.disabled_by or entry.platform == DOMAIN:
            continue
        haystack = " ".join(
            part
            for part in (
                entry.entity_id,
                entry.unique_id,
                entry.original_name,
                entry.translation_key,
            )
            if part
        ).lower()
        if not any(keyword in haystack for keyword in VALVE_KEYWORDS):
            continue
        if entry.platform == "bosch_shc":
            found.append(entry.entity_id)
            continue
        unit = entry.unit_of_measurement
        if unit is None and (state := hass.states.get(entry.entity_id)):
            unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        if unit == PERCENTAGE:
            found.append(entry.entity_id)
    return sorted(found)


def _is_temperature_sensor(hass: HomeAssistant, entry: er.RegistryEntry) -> bool:
    if entry.domain != "sensor" or entry.disabled_by or entry.platform == DOMAIN:
        return False
    device_class = entry.device_class or entry.original_device_class
    if device_class is None and (state := hass.states.get(entry.entity_id)):
        device_class = state.attributes.get(ATTR_DEVICE_CLASS)
    return device_class == SensorDeviceClass.TEMPERATURE


def find_room_temperature(hass: HomeAssistant, valve: str) -> str | None:
    """Temperature sensor for a valve's room.

    First a temperature sensor on the valve's own device (Bosch TRVs have one),
    else the only temperature sensor in the valve's area.
    """
    ent_reg = er.async_get(hass)
    valve_entry = ent_reg.async_get(valve)
    if valve_entry is None:
        return None

    if valve_entry.device_id is not None:
        same_device = sorted(
            entry.entity_id
            for entry in er.async_entries_for_device(ent_reg, valve_entry.device_id)
            if _is_temperature_sensor(hass, entry)
        )
        if same_device:
            return same_device[0]

    dev_reg = dr.async_get(hass)
    area_id = valve_entry.area_id
    if area_id is None and valve_entry.device_id is not None:
        device = dev_reg.async_get(valve_entry.device_id)
        area_id = device.area_id if device else None
    if area_id is None:
        return None

    in_area = []
    for entry in ent_reg.entities.values():
        if not _is_temperature_sensor(hass, entry):
            continue
        entry_area = entry.area_id
        if entry_area is None and entry.device_id is not None:
            device = dev_reg.async_get(entry.device_id)
            entry_area = device.area_id if device else None
        if entry_area == area_id:
            in_area.append(entry.entity_id)
    return in_area[0] if len(in_area) == 1 else None


def valve_name(hass: HomeAssistant, valve: str) -> str:
    """Room name for a valve: its device name (e.g. the TRV), else the entity name."""
    entity = er.async_get(hass).async_get(valve)
    if entity is not None and entity.device_id is not None:
        device = dr.async_get(hass).async_get(entity.device_id)
        if device is not None and (name := device.name_by_user or device.name):
            return name
    if (state := hass.states.get(valve)) is not None:
        return state.name
    return valve


def valve_labels(hass: HomeAssistant, valves: list[str]) -> dict[str, str]:
    """Unique display label -> valve entity id (labels are form field names)."""
    labels: dict[str, str] = {}
    for valve in valves:
        label = valve_name(hass, valve)
        if label in labels:
            label = f"{label} ({valve})"
        labels[label] = valve
    return labels
