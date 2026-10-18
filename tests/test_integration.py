"""End-to-end tests against a real Home Assistant core (pytest-homeassistant-custom-component)."""

from __future__ import annotations

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.boiler_demand.const import (
    CONF_MIN_OFF,
    CONF_MIN_ON,
    CONF_OFF_THRESHOLD,
    CONF_ON_THRESHOLD,
    CONF_SWITCH,
    CONF_TEMPERATURES,
    CONF_VALVES,
    DEFAULTS,
    DOMAIN,
)

BOILER = "input_boolean.therme"
VALVES = ["sensor.heizung_kuche_valvetappet", "sensor.heizung_tobi_valvetappet"]
TOBI_TEMP = "sensor.heizung_tobi_temperature"  # on the TRV device
KUCHE_TEMP = "sensor.kuche_wall_temperature"  # only temperature sensor in the Kueche area
# Room devices created by Boiler Demand (named after the TRV device).
TOBI_SENSOR = "sensor.heizung_tobi_valve_opening"
TOBI_ROOM_TEMP = "sensor.heizung_tobi_room_temperature"
ROOMS = {"Heizung Küche": KUCHE_TEMP, "Heizung Tobi": TOBI_TEMP}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
async def setup_hass(hass: HomeAssistant) -> HomeAssistant:
    assert await async_setup_component(hass, "input_boolean", {"input_boolean": {"therme": {}}})
    bosch = MockConfigEntry(domain="bosch_shc")
    bosch.add_to_hass(hass)
    ent_reg, dev_reg = er.async_get(hass), dr.async_get(hass)
    kuche_area = ar.async_get(hass).async_create("Küche")

    devices = {}
    for room, entity_id in (("Heizung Küche", VALVES[0]), ("Heizung Tobi", VALVES[1])):
        device = dev_reg.async_get_or_create(
            config_entry_id=bosch.entry_id, identifiers={("bosch_shc", room)}, name=room
        )
        devices[room] = device
        object_id = entity_id.split(".", 1)[1]
        ent_reg.async_get_or_create(
            "sensor", "bosch_shc", f"serial-{object_id}",
            suggested_object_id=object_id, device_id=device.id,
        )
        hass.states.async_set(entity_id, "0", {"unit_of_measurement": "%"})
    dev_reg.async_update_device(devices["Heizung Küche"].id, area_id=kuche_area.id)

    # Temperature on the Tobi TRV device (not a valve: must not be detected as one).
    ent_reg.async_get_or_create(
        "sensor", "bosch_shc", "serial-temp", suggested_object_id="heizung_tobi_temperature",
        device_id=devices["Heizung Tobi"].id, original_device_class="temperature",
    )
    hass.states.async_set(TOBI_TEMP, "21.5", {"unit_of_measurement": "°C", "device_class": "temperature"})
    # Kueche TRV has no temperature sensor; the area has exactly one.
    ent_reg.async_get_or_create(
        "sensor", "other", "wall-temp", suggested_object_id="kuche_wall_temperature",
        original_device_class="temperature",
    )
    ent_reg.async_update_entity(KUCHE_TEMP, area_id=kuche_area.id)
    hass.states.async_set(KUCHE_TEMP, "19.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
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
    assert result["step_id"] == "rooms"
    assert result["description_placeholders"]["count"] == "2"
    assert _suggested(result) == ROOMS

    result = await hass.config_entries.flow.async_configure(result["flow_id"], ROOMS)
    assert result["step_id"] == "settings"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**DEFAULTS, **settings}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    return result["result"]


def _room_entity(hass: HomeAssistant, entry, kind: str, valve: str) -> str | None:
    """Entity id of a room entity (kind: valve | temperature); HA may prefix the area."""
    return er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{kind}_{valve}")


def _suggested(result) -> dict[str, str]:
    """Pre-filled values of a form (field name -> suggested value)."""
    return {
        str(key): key.description["suggested_value"]
        for key in result["data_schema"].schema
        if key.description and "suggested_value" in key.description
    }


async def _set_valves(hass: HomeAssistant, *values: str) -> None:
    for entity_id, value in zip(VALVES, values, strict=True):
        hass.states.async_set(entity_id, value, {"unit_of_measurement": "%"})
    await hass.async_block_till_done()
    # Older HA delivers the boiler switch's state event one loop round later.
    await hass.async_block_till_done()


async def test_flow_rejects_off_above_on(setup_hass: HomeAssistant) -> None:
    hass = setup_hass
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"name": "H", CONF_SWITCH: BOILER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_VALVES: VALVES})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
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
    valve = hass.states.get(_room_entity(hass, entry, "valve", VALVES[0]))
    assert valve.state == "25.0"
    assert valve.attributes["role"] == "demand"
    assert valve.attributes["source_entity"] == VALVES[0]

    # Inside the band it keeps heating.
    await _set_valves(hass, "15", "0")
    assert hass.states.get(BOILER).state == "on"

    # -1 / unavailable are ignored, so the remaining low valve turns it off.
    await _set_valves(hass, "-1", "5")
    assert hass.states.get(BOILER).state == "off"
    attrs = hass.states.get("binary_sensor.heating_heat_demand").attributes
    assert attrs["offline_valves"] == [VALVES[0]]
    assert hass.states.get(_room_entity(hass, entry, "valve", VALVES[0])).state == "unknown"
    assert hass.states.get(_room_entity(hass, entry, "valve", VALVES[0])).attributes["role"] == "offline"
    assert hass.states.get(TOBI_SENSOR).attributes["role"] == "closed"

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


async def test_valve_sensors_follow_options(setup_hass: HomeAssistant) -> None:
    hass = setup_hass
    entry = await _create_entry(hass)
    registry = er.async_get(hass)
    assert _room_entity(hass, entry, "valve", VALVES[0]) is not None
    assert registry.async_get(TOBI_SENSOR) is not None

    # Deselect one valve in the options flow -> reload -> its sensor is removed.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {**DEFAULTS, CONF_SWITCH: BOILER, CONF_VALVES: [VALVES[1]]},
    )
    assert result["step_id"] == "rooms"
    assert _suggested(result) == {"Heizung Tobi": TOBI_TEMP}
    # Clear the Tobi temperature sensor.
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert _room_entity(hass, entry, "valve", VALVES[0]) is None
    assert _room_entity(hass, entry, "temperature", VALVES[0]) is None
    assert registry.async_get(TOBI_SENSOR) is not None
    assert registry.async_get(TOBI_ROOM_TEMP) is None
    assert entry.runtime_data.valves == [VALVES[1]]
    assert entry.options[CONF_TEMPERATURES] == {VALVES[1]: None}
    # The Kueche room device is gone, the Tobi one stays.
    dev_reg = dr.async_get(hass)
    rooms = [
        d.name for d in dr.async_entries_for_config_entry(dev_reg, entry.entry_id) if d.model == "Room"
    ]
    assert rooms == ["Heizung Tobi"]

    # Cleared stays cleared when the options are opened again.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**DEFAULTS, CONF_SWITCH: BOILER, CONF_VALVES: [VALVES[1]]}
    )
    assert _suggested(result) == {}
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_rooms_group_valve_and_temperature(setup_hass: HomeAssistant) -> None:
    hass = setup_hass
    entry = await _create_entry(hass)

    temp = hass.states.get(TOBI_ROOM_TEMP)
    assert temp.state == "21.5"
    assert temp.attributes["source_entity"] == TOBI_TEMP
    assert hass.states.get(_room_entity(hass, entry, "temperature", VALVES[0])).state == "19.0"

    hass.states.async_set(TOBI_TEMP, "22.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    await hass.async_block_till_done()
    assert hass.states.get(TOBI_ROOM_TEMP).state == "22.0"

    # Valve + temperature share one room device, linked to the main device, in the valve area.
    ent_reg, dev_reg = er.async_get(hass), dr.async_get(hass)
    main = dev_reg.async_get(entry.runtime_data.device_id)
    assert main.name == "Heating"
    tobi = dev_reg.async_get(ent_reg.async_get(TOBI_SENSOR).device_id)
    assert ent_reg.async_get(TOBI_ROOM_TEMP).device_id == tobi.id
    assert tobi.name == "Heizung Tobi" and tobi.via_device_id == main.id
    kuche = dev_reg.async_get(ent_reg.async_get(_room_entity(hass, entry, "valve", VALVES[0])).device_id)
    assert kuche.area_id == ar.async_get(hass).async_get_area_by_name("Küche").id

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_card_is_served(setup_hass: HomeAssistant, hass_client) -> None:
    hass = setup_hass
    assert await async_setup_component(hass, "http", {})
    entry = await _create_entry(hass)

    client = await hass_client()
    response = await client.get("/boiler_demand/boiler-demand-card.js")
    assert response.status == 200
    body = await response.text()
    assert 'customElements.define("boiler-demand-card"' in body
    # Status sensor tells the card which relay to show.
    assert hass.states.get("sensor.heating_status").attributes["boiler_switch"] == BOILER

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
