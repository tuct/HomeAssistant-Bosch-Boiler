"""End-to-end tests against a real Home Assistant core (pytest-homeassistant-custom-component)."""

from __future__ import annotations

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.boiler_demand.const import (
    CONF_MIN_OFF,
    CONF_MIN_ON,
    CONF_OFF_THRESHOLD,
    CONF_ON_THRESHOLD,
    CONF_SWITCH,
    CONF_VALVES,
    DEFAULTS,
    DOMAIN,
)

BOILER = "input_boolean.therme"
VALVES = ["sensor.heizung_kuche_valvetappet", "sensor.heizung_tobi_valvetappet"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
async def setup_hass(hass: HomeAssistant) -> HomeAssistant:
    assert await async_setup_component(hass, "input_boolean", {"input_boolean": {"therme": {}}})
    registry = er.async_get(hass)
    for entity_id in VALVES:
        object_id = entity_id.split(".", 1)[1]
        registry.async_get_or_create(
            "sensor", "bosch_shc", f"serial-{object_id}", suggested_object_id=object_id
        )
        hass.states.async_set(entity_id, "0", {"unit_of_measurement": "%"})
    # Not a valve: must not be detected.
    registry.async_get_or_create("sensor", "bosch_shc", "serial-temp", suggested_object_id="heizung_tobi_temperature")
    await hass.async_block_till_done()
    return hass


async def _create_entry(hass: HomeAssistant, **settings) -> config_entries.ConfigEntry:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Heating", CONF_SWITCH: BOILER}
    )
    assert result["step_id"] == "valves"
    assert result["description_placeholders"]["count"] == "2"
    defaults = {
        str(key): key.default() for key in result["data_schema"].schema if hasattr(key, "default")
    }
    assert sorted(defaults[CONF_VALVES]) == VALVES

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_VALVES: VALVES})
    assert result["step_id"] == "settings"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**DEFAULTS, **settings}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    return result["result"]


async def _set_valves(hass: HomeAssistant, *values: str) -> None:
    for entity_id, value in zip(VALVES, values, strict=True):
        hass.states.async_set(entity_id, value, {"unit_of_measurement": "%"})
    await hass.async_block_till_done()


async def test_flow_rejects_off_above_on(setup_hass: HomeAssistant) -> None:
    hass = setup_hass
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"name": "H", CONF_SWITCH: BOILER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_VALVES: VALVES})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**DEFAULTS, CONF_ON_THRESHOLD: 10, CONF_OFF_THRESHOLD: 20}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_OFF_THRESHOLD: "off_above_on"}


async def test_controls_boiler(setup_hass: HomeAssistant) -> None:
    hass = setup_hass
    entry = await _create_entry(hass, **{CONF_MIN_ON: 0, CONF_MIN_OFF: 0})

    assert hass.states.get("binary_sensor.heating_heat_demand").state == "off"
    assert hass.states.get("sensor.heating_status").state == "idle"

    # Band: 20 % is not enough to start.
    await _set_valves(hass, "20", "0")
    assert hass.states.get(BOILER).state == "off"

    # One valve at 25 % starts the boiler.
    await _set_valves(hass, "25", "0")
    assert hass.states.get(BOILER).state == "on"
    assert hass.states.get("binary_sensor.heating_heat_demand").state == "on"
    assert hass.states.get("sensor.heating_max_valve_opening").state == "25.0"
    assert hass.states.get("sensor.heating_boiler_starts_today").state == "1"

    # Inside the band it keeps heating.
    await _set_valves(hass, "15", "0")
    assert hass.states.get(BOILER).state == "on"

    # -1 / unavailable are ignored, so the remaining low valve turns it off.
    await _set_valves(hass, "-1", "5")
    assert hass.states.get(BOILER).state == "off"
    attrs = hass.states.get("binary_sensor.heating_heat_demand").attributes
    assert attrs["offline_valves"] == [VALVES[0]]

    # Changing the on threshold from the dashboard applies without reload.
    await hass.services.async_call(
        "number", "set_value",
        {"entity_id": "number.heating_on_threshold", "value": 50},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert entry.options[CONF_ON_THRESHOLD] == 50
    await _set_valves(hass, "40", "5")
    assert hass.states.get(BOILER).state == "off"

    # Automatic control off: demand computed, relay untouched.
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.heating_automatic_control"}, blocking=True
    )
    await _set_valves(hass, "80", "5")
    assert hass.states.get("binary_sensor.heating_heat_demand").state == "on"
    assert hass.states.get(BOILER).state == "off"
    assert hass.states.get("sensor.heating_status").state == "disabled"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_min_off_time_blocks(setup_hass: HomeAssistant) -> None:
    hass = setup_hass
    entry = await _create_entry(hass)  # defaults: min off 8 min
    await _set_valves(hass, "60", "0")
    assert hass.states.get(BOILER).state == "off"
    status = hass.states.get("sensor.heating_status")
    assert status.state == "waiting_min_off"
    assert 7 < status.attributes["remaining_minutes"] <= 8

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_duplicate_switch_aborts(setup_hass: HomeAssistant) -> None:
    hass = setup_hass
    entry = await _create_entry(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"name": "x", CONF_SWITCH: BOILER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
