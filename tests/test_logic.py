"""Tests for the pure decision logic (no Home Assistant needed)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_LOGIC = Path(__file__).parents[1] / "custom_components" / "boiler_demand" / "logic.py"
_spec = importlib.util.spec_from_file_location("boiler_demand_logic", _LOGIC)
logic = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = logic
_spec.loader.exec_module(logic)

MIN = 60.0
S = logic.Settings(
    on_threshold=25,
    off_threshold=12,
    min_on_s=10 * MIN,
    min_off_s=8 * MIN,
    failsafe_after_s=30 * MIN,
    failsafe_mode="on",
)
LONG = 60 * MIN  # switch has been in its state long enough


def decide(positions, demand=False, switch_on=False, switch_for=LONG, offline_for=None, settings=S):
    return logic.decide(positions, demand, switch_on, switch_for, offline_for, settings)


# --- parse_position -------------------------------------------------------

def test_parse_position():
    assert logic.parse_position("37") == 37.0
    assert logic.parse_position("12.5") == 12.5
    assert logic.parse_position("0") == 0.0
    assert logic.parse_position("150") == 100.0
    for bad in (None, "-1", "unavailable", "unknown", "", "nan", "abc"):
        assert logic.parse_position(bad) is None


# --- hysteresis -----------------------------------------------------------

def test_turns_on_when_any_valve_reaches_on_threshold():
    d = decide([5, 25, None])
    assert d.demand and d.action == "on" and d.status == "heating"


def test_band_keeps_previous_demand():
    assert decide([20, 5], demand=False).demand is False
    d = decide([20, 5], demand=True, switch_on=True)
    assert d.demand is True and d.action is None and d.status == "heating"


def test_turns_off_when_all_at_or_below_off_threshold():
    d = decide([12, 0, 3], demand=True, switch_on=True)
    assert not d.demand and d.action == "off" and d.status == "idle"


def test_one_open_valve_keeps_boiler_on():
    d = decide([13, 0], demand=True, switch_on=True)
    assert d.demand and d.action is None


def test_offline_valve_does_not_block_off():
    # The old automation never turned off when one valve reported -1.
    positions = [logic.parse_position(s) for s in ("-1", "5", "unavailable")]
    d = decide(positions, demand=True, switch_on=True)
    assert d.action == "off"


# --- min on / off ---------------------------------------------------------

def test_min_off_blocks_restart():
    d = decide([40], switch_on=False, switch_for=3 * MIN)
    assert d.demand and d.action is None and d.status == "waiting_min_off"
    assert d.remaining_s == 5 * MIN


def test_min_off_expired_turns_on():
    assert decide([40], switch_on=False, switch_for=8 * MIN).action == "on"


def test_min_on_blocks_stop():
    d = decide([0], demand=True, switch_on=True, switch_for=4 * MIN)
    assert not d.demand and d.action is None and d.status == "waiting_min_on"
    assert d.remaining_s == 6 * MIN


def test_switch_unavailable_no_action():
    d = decide([80], switch_on=None)
    assert d.demand and d.action is None and d.status == "switch_unavailable"


def test_already_in_desired_state_no_action():
    assert decide([80], demand=True, switch_on=True).action is None
    assert decide([0], switch_on=False).action is None


# --- failsafe -------------------------------------------------------------

def test_all_offline_waits_and_keeps_demand():
    d = decide([None, None], demand=True, switch_on=True, offline_for=10 * MIN)
    assert d.demand and d.action is None and d.status == "valves_offline"
    assert d.remaining_s == 20 * MIN


def test_failsafe_on_ignores_min_off():
    d = decide([None], switch_on=False, switch_for=1 * MIN, offline_for=30 * MIN)
    assert d.demand and d.action == "on" and d.status == "failsafe"


def test_failsafe_off():
    s = logic.Settings(**{**S.__dict__, "failsafe_mode": "off"})
    d = decide([None], demand=True, switch_on=True, offline_for=31 * MIN, settings=s)
    assert not d.demand and d.action == "off" and d.status == "failsafe"


def test_failsafe_hold():
    s = logic.Settings(**{**S.__dict__, "failsafe_mode": "hold"})
    d = decide([None], demand=False, switch_on=True, offline_for=99 * MIN, settings=s)
    assert d.action is None and d.status == "valves_offline" and d.remaining_s is None


def test_valves_back_online_resume_normal_logic():
    d = decide([5], demand=True, switch_on=True)
    assert d.action == "off"
