"""End-to-end tests against a real Home Assistant core (pytest-homeassistant-custom-component)."""

from __future__ import annotations

from datetime import timedelta
import json

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.boiler_demand.const import (
    CONF_FLOW_SENSOR,
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

    # Compact room list for ESPHome displays; follows temperature and valve changes.
    hass.states.async_set(VALVES[0], "30", {"unit_of_measurement": "%"})
    await hass.async_block_till_done()
    rooms = json.loads(hass.states.get("sensor.heating_status").attributes["rooms"])
    assert rooms == [
        {"n": "Küche", "t": 19.0, "v": 30, "r": "d"},
        {"n": "Tobi", "t": 22.0, "v": 0, "r": "c"},
    ]

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


def _entity(hass: HomeAssistant, entry, platform: str, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{entry.entry_id}_{key}")
    assert entity_id is not None, key
    return entity_id


async def _tick(hass: HomeAssistant, freezer, delta: timedelta) -> None:
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    await hass.async_block_till_done()


async def test_auto_tune_uses_learned_thresholds(setup_hass: HomeAssistant, freezer) -> None:
    hass = setup_hass
    entry = await _create_entry(hass, **{CONF_MIN_ON: 0, CONF_MIN_OFF: 0})
    controller = entry.runtime_data
    auto_tune = _entity(hass, entry, "switch", "auto_tune")
    holding = _room_entity(hass, entry, "holding", VALVES[0])
    assert hass.states.get(auto_tune).state == "off"
    assert hass.states.get(holding).state == "unknown"
    assert hass.states.get(_entity(hass, entry, "sensor", "effective_min_off")).state == "0.0"

    # Kueche has learned that it holds its room warm at 30 %.
    controller.tuner.load({"valves": {VALVES[0]: {"holding": 30, "samples": 5}}})
    await hass.services.async_call("switch", "turn_on", {"entity_id": auto_tune}, blocking=True)
    await hass.async_block_till_done()
    state = hass.states.get(holding)
    assert state.state == "30.0"
    assert state.attributes["on_threshold"] == 40 and state.attributes["off_threshold"] == 33

    # 35 % is above the configured on threshold (25) but below Kueche's own (40).
    await _set_valves(hass, "35", "0")
    assert hass.states.get(BOILER).state == "off"
    assert hass.states.get(_room_entity(hass, entry, "valve", VALVES[0])).attributes["role"] == "holding"

    # 45 % starts the boiler only after the 3 min on delay.
    await _set_valves(hass, "45", "0")
    assert hass.states.get(BOILER).state == "off"
    await _tick(hass, freezer, timedelta(minutes=3, seconds=30))
    assert hass.states.get(BOILER).state == "on"

    # Back at its holding level (below 33 %) the room is warm: boiler off.
    await _set_valves(hass, "30", "0")
    assert hass.states.get(BOILER).state == "off"

    # Auto-tune off: the configured 25 % applies again, without delay.
    await hass.services.async_call("switch", "turn_off", {"entity_id": auto_tune}, blocking=True)
    await _set_valves(hass, "35", "0")
    assert hass.states.get(BOILER).state == "on"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_stuck_open_valve_raises_repair(setup_hass: HomeAssistant, freezer) -> None:
    hass = setup_hass
    entry = await _create_entry(hass, **{CONF_MIN_ON: 0, CONF_MIN_OFF: 0})
    issue_id = f"valve_stuck_open_{entry.entry_id}_{VALVES[0]}"
    holding = _room_entity(hass, entry, "holding", VALVES[0])

    await _set_valves(hass, "100", "0")
    assert hass.states.get(BOILER).state == "on"
    await _tick(hass, freezer, timedelta(seconds=30))
    await _tick(hass, freezer, timedelta(hours=3, minutes=59))
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    await _tick(hass, freezer, timedelta(minutes=1))
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None and issue.translation_key == "valve_stuck_open"
    assert issue.translation_placeholders["valve"] == "Heizung Küche"
    assert hass.states.get(holding).attributes["stuck_open"] is True

    # The valve closes: the issue clears itself.
    await _set_valves(hass, "50", "0")
    await _tick(hass, freezer, timedelta(seconds=30))
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_learning_is_stored_and_reset(setup_hass: HomeAssistant, hass_storage) -> None:
    hass = setup_hass
    entry = await _create_entry(hass)
    controller = entry.runtime_data
    holding = _room_entity(hass, entry, "holding", VALVES[0])

    controller.tuner.load({"valves": {VALVES[0]: {"holding": 30, "samples": 5}}})
    await controller._store.async_save(controller.tuner.as_dict())
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(holding).state == "30.0"

    await hass.services.async_call(
        "button", "press", {"entity_id": _entity(hass, entry, "button", "reset_learning")}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(holding).state == "unknown"
    assert hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"] == {"valves": {}}

    # Removing the entry deletes the stored data.
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert f"{DOMAIN}.{entry.entry_id}" not in hass_storage


async def test_flow_sensor_gates_learning_and_reports_no_heat(setup_hass: HomeAssistant, freezer) -> None:
    hass = setup_hass
    flow = "sensor.boiler_flow_temperature"
    hass.states.async_set(flow, "18.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    entry = await _create_entry(hass, **{CONF_MIN_ON: 0, CONF_MIN_OFF: 0, CONF_FLOW_SENSOR: flow})
    controller = entry.runtime_data
    assert controller.flow_sensor == flow
    no_heat_id = f"no_heat_{entry.entry_id}"
    stuck_id = f"valve_stuck_open_{entry.entry_id}_{VALVES[0]}"

    # Switch on, flow pipe cold: nothing is learned, a fully open valve is not "stuck",
    # and after 30 min Repairs reports that the boiler does not heat.
    await _set_valves(hass, "100", "30")
    assert hass.states.get(BOILER).state == "on"
    for _ in range(8):
        await _tick(hass, freezer, timedelta(minutes=10))
    assert hass.states.get("sensor.heating_status").attributes["flow_temperature"] == 18.0
    assert controller.tuner.valves[VALVES[1]].samples == 0
    assert ir.async_get(hass).async_get_issue(DOMAIN, no_heat_id) is not None
    await _tick(hass, freezer, timedelta(hours=4))
    assert ir.async_get(hass).async_get_issue(DOMAIN, stuck_id) is None

    # Flow pipe hot: the warning clears and the steady 30 % valve is learned.
    hass.states.async_set(flow, "55.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    await _tick(hass, freezer, timedelta(seconds=30))
    assert ir.async_get(hass).async_get_issue(DOMAIN, no_heat_id) is None
    for _ in range(120):
        await _tick(hass, freezer, timedelta(seconds=30))
    assert controller.tuner.holding(VALVES[1]) == 30.0

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_flow_sensor_found_on_relay_device(setup_hass: HomeAssistant) -> None:
    hass = setup_hass
    from custom_components.boiler_demand.discovery import find_flow_sensor

    esphome = MockConfigEntry(domain="esphome")
    esphome.add_to_hass(hass)
    ent_reg, dev_reg = er.async_get(hass), dr.async_get(hass)
    node = dev_reg.async_get_or_create(
        config_entry_id=esphome.entry_id, identifiers={("esphome", "boiler-relay")}, name="Boiler Relay"
    )
    ent_reg.async_get_or_create(
        "switch", "esphome", "relay", suggested_object_id="boiler_relay_boiler", device_id=node.id
    )
    ent_reg.async_get_or_create(
        "sensor", "esphome", "wifi", suggested_object_id="boiler_relay_wifi_signal",
        device_id=node.id, original_device_class="signal_strength",
    )
    ent_reg.async_get_or_create(
        "sensor", "esphome", "flow", suggested_object_id="boiler_relay_flow_temperature",
        device_id=node.id, original_device_class="temperature",
    )
    assert find_flow_sensor(hass, "switch.boiler_relay_boiler") == "sensor.boiler_relay_flow_temperature"
    assert find_flow_sensor(hass, BOILER) is None
