"""Config and options flow for Boiler Demand."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .const import (
    CONF_FAILSAFE_AFTER,
    CONF_FAILSAFE_MODE,
    CONF_MIN_OFF,
    CONF_MIN_ON,
    CONF_OFF_THRESHOLD,
    CONF_ON_THRESHOLD,
    CONF_SWITCH,
    CONF_VALVES,
    DEFAULTS,
    DOMAIN,
    FAILSAFE_MODES,
)
from .discovery import find_valve_candidates

SWITCH_SELECTOR = EntitySelector(EntitySelectorConfig(domain=["switch", "input_boolean"]))
VALVES_SELECTOR = EntitySelector(EntitySelectorConfig(domain="sensor", multiple=True))


def _percent(minimum: int, maximum: int) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=1,
            unit_of_measurement="%",
            mode=NumberSelectorMode.SLIDER,
        )
    )


def _minutes(minimum: int, maximum: int) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=1,
            unit_of_measurement="min",
            mode=NumberSelectorMode.BOX,
        )
    )


def _settings_schema(values: Mapping[str, Any]) -> dict[vol.Marker, Any]:
    """Schema fields for thresholds, times and failsafe."""
    return {
        vol.Required(CONF_ON_THRESHOLD, default=values[CONF_ON_THRESHOLD]): _percent(1, 100),
        vol.Required(CONF_OFF_THRESHOLD, default=values[CONF_OFF_THRESHOLD]): _percent(0, 99),
        vol.Required(CONF_MIN_ON, default=values[CONF_MIN_ON]): _minutes(0, 60),
        vol.Required(CONF_MIN_OFF, default=values[CONF_MIN_OFF]): _minutes(0, 60),
        vol.Required(CONF_FAILSAFE_AFTER, default=values[CONF_FAILSAFE_AFTER]): _minutes(1, 240),
        vol.Required(CONF_FAILSAFE_MODE, default=values[CONF_FAILSAFE_MODE]): SelectSelector(
            SelectSelectorConfig(
                options=FAILSAFE_MODES,
                translation_key=CONF_FAILSAFE_MODE,
                mode=SelectSelectorMode.DROPDOWN,
            )
        ),
    }


def _validate(user_input: Mapping[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if CONF_VALVES in user_input and not user_input[CONF_VALVES]:
        errors[CONF_VALVES] = "no_valves"
    if float(user_input[CONF_OFF_THRESHOLD]) >= float(user_input[CONF_ON_THRESHOLD]):
        errors[CONF_OFF_THRESHOLD] = "off_above_on"
    return errors


class BoilerDemandConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up Boiler Demand: switch -> valves -> settings."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_SWITCH])
            self._abort_if_unique_id_configured()
            self._data.update(user_input)
            return await self.async_step_valves()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME, default="Heating"): TextSelector(),
                    vol.Required(CONF_SWITCH): SWITCH_SELECTOR,
                }
            ),
        )

    async def async_step_valves(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_VALVES):
                self._data.update(user_input)
                return await self.async_step_settings()
            errors[CONF_VALVES] = "no_valves"

        detected = find_valve_candidates(self.hass)
        return self.async_show_form(
            step_id="valves",
            data_schema=vol.Schema(
                {vol.Required(CONF_VALVES, default=detected): VALVES_SELECTOR}
            ),
            errors=errors,
            description_placeholders={"count": str(len(detected))},
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                options = {
                    CONF_SWITCH: self._data[CONF_SWITCH],
                    CONF_VALVES: self._data[CONF_VALVES],
                    **user_input,
                }
                return self.async_create_entry(
                    title=self._data[CONF_NAME], data={}, options=options
                )

        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(_settings_schema({**DEFAULTS, **(user_input or {})})),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return BoilerDemandOptionsFlow()


class BoilerDemandOptionsFlow(OptionsFlow):
    """Change switch, valves and settings."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                new_switch = user_input[CONF_SWITCH]
                if new_switch != self.config_entry.unique_id:
                    self.hass.config_entries.async_update_entry(
                        self.config_entry, unique_id=new_switch
                    )
                return self.async_create_entry(data=user_input)

        values = {**DEFAULTS, **self.config_entry.options, **(user_input or {})}
        detected = find_valve_candidates(self.hass)
        schema = {
            vol.Required(CONF_SWITCH, default=values[CONF_SWITCH]): SWITCH_SELECTOR,
            vol.Required(CONF_VALVES, default=values[CONF_VALVES]): VALVES_SELECTOR,
            **_settings_schema(values),
        }
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={
                "detected": ", ".join(detected) if detected else "-",
            },
        )
