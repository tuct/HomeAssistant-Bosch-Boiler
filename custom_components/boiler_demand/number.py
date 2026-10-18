"""Tunable thresholds and times as number entities."""

from __future__ import annotations

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BoilerDemandConfigEntry
from .const import (
    CONF_MIN_OFF,
    CONF_MIN_ON,
    CONF_OFF_THRESHOLD,
    CONF_ON_THRESHOLD,
    DOMAIN,
)
from .controller import BoilerDemandController
from .entity import BoilerDemandEntity


NUMBERS = (
    NumberEntityDescription(
        key=CONF_ON_THRESHOLD,
        native_min_value=1,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
    ),
    NumberEntityDescription(
        key=CONF_OFF_THRESHOLD,
        native_min_value=0,
        native_max_value=99,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
    ),
    NumberEntityDescription(
        key=CONF_MIN_ON,
        native_min_value=0,
        native_max_value=60,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        mode=NumberMode.BOX,
    ),
    NumberEntityDescription(
        key=CONF_MIN_OFF,
        native_min_value=0,
        native_max_value=60,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        mode=NumberMode.BOX,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BoilerDemandConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up number entities."""
    async_add_entities(
        BoilerDemandNumber(entry.runtime_data, description) for description in NUMBERS
    )


class BoilerDemandNumber(BoilerDemandEntity, NumberEntity):
    """Edits one option of the config entry."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        controller: BoilerDemandController,
        description: NumberEntityDescription,
    ) -> None:
        super().__init__(controller, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float:
        return float(self.controller.options[self.entity_description.key])

    async def async_set_native_value(self, value: float) -> None:
        key = self.entity_description.key
        options = {**self.controller.entry.options, key: value}
        on = float(options.get(CONF_ON_THRESHOLD, self.controller.options[CONF_ON_THRESHOLD]))
        off = float(options.get(CONF_OFF_THRESHOLD, self.controller.options[CONF_OFF_THRESHOLD]))
        if off >= on:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="off_above_on",
                translation_placeholders={"on": f"{on:g}", "off": f"{off:g}"},
            )
        self.hass.config_entries.async_update_entry(self.controller.entry, options=options)
