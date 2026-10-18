"""Auto-detection of radiator valve sensors."""

from __future__ import annotations

from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

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
