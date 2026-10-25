"""Constants for Boiler Demand."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "boiler_demand"

CONF_SWITCH = "switch_entity"
CONF_VALVES = "valves"
# valve entity id -> room temperature sensor entity id (or None)
CONF_TEMPERATURES = "temperature_sensors"
CONF_ON_THRESHOLD = "on_threshold"
CONF_OFF_THRESHOLD = "off_threshold"
CONF_MIN_ON = "min_on_minutes"
CONF_MIN_OFF = "min_off_minutes"
CONF_FAILSAFE_AFTER = "failsafe_after_minutes"
CONF_FAILSAFE_MODE = "failsafe_mode"
# Optional temperature sensor on the boiler's flow pipe (e.g. DS18B20 on the relay node).
CONF_FLOW_SENSOR = "flow_sensor"

FAILSAFE_ON = "on"
FAILSAFE_OFF = "off"
FAILSAFE_HOLD = "hold"
FAILSAFE_MODES = [FAILSAFE_ON, FAILSAFE_OFF, FAILSAFE_HOLD]

DEFAULTS: dict[str, float | str] = {
    CONF_ON_THRESHOLD: 25.0,
    CONF_OFF_THRESHOLD: 12.0,
    CONF_MIN_ON: 10.0,
    CONF_MIN_OFF: 8.0,
    CONF_FAILSAFE_AFTER: 30.0,
    CONF_FAILSAFE_MODE: FAILSAFE_ON,
}

# Options that can change without reloading the entry.
TUNABLE_OPTIONS = (
    CONF_ON_THRESHOLD,
    CONF_OFF_THRESHOLD,
    CONF_MIN_ON,
    CONF_MIN_OFF,
    CONF_FAILSAFE_AFTER,
    CONF_FAILSAFE_MODE,
)

EVAL_INTERVAL = timedelta(seconds=30)
# Flow pipe at or above this: the boiler really delivers heat. A clamp-on sensor reads a
# few degrees below the water temperature.
FLOW_HOT_C = 30.0
# Boiler switch on this long with a cold flow pipe: "boiler not heating" repair issue.
NO_HEAT_AFTER = timedelta(minutes=30)
# Do not resend the same switch command within this window.
COMMAND_DEBOUNCE_S = 15.0

# Substrings that identify valve-opening sensors (Bosch SHC, ZHA, Zigbee2MQTT, ...).
VALVE_KEYWORDS = (
    "valvetappet",
    "valve_tappet",
    "valve_position",
    "valve_opening",
    "pi_heating_demand",
)


def signal_update(entry_id: str) -> str:
    """Dispatcher signal for controller updates of one entry."""
    return f"{DOMAIN}_update_{entry_id}"
