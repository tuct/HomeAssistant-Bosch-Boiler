"""Tests for the pure auto-tune logic (no Home Assistant needed)."""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys

_PACKAGE = Path(__file__).parents[1] / "custom_components" / "boiler_demand"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"boiler_demand_{name}", _PACKAGE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


autotune = _load("autotune")
logic = _load("logic")

MIN = 60.0
STEP = 30.0
V = "sensor.valve"


def tuner() -> autotune.AutoTuner:
    return autotune.AutoTuner(base_on=19, base_off=16, base_min_off_s=8 * MIN)


def feed(t: autotune.AutoTuner, start: float, minutes: float, position, boiler_on=True) -> float:
    """Observe a valve every 30 s; position is a value or a function of the step index."""
    now = start
    for i in range(int(minutes * MIN / STEP)):
        value = position(i) if callable(position) else position
        t.observe(now, {V: value}, boiler_on)
        now += STEP
    return now


# --- learning -------------------------------------------------------------

def test_learns_holding_level_from_steady_valve():
    t = tuner()
    feed(t, 0, 29, 30.0)
    assert t.valves[V].samples == 0  # window not full yet
    feed(t, 0, 70, 30.0)
    assert t.holding(V) == 30.0
    assert t.thresholds(V) == (40.0, 33.0)


def test_uses_configured_thresholds_until_enough_samples():
    t = tuner()
    feed(t, 0, 45, 30.0)  # 2 samples (30 min + 10 min)
    assert t.valves[V].samples == 2
    assert t.holding(V) is None
    assert t.thresholds(V) == (19, 16)


def test_no_learning_while_boiler_off_or_valve_moving_or_wide_open():
    for kwargs in (
        {"position": 30.0, "boiler_on": False},
        {"position": lambda i: 20.0 + (i % 20)},  # swings 20..39
        {"position": 100.0},
        {"position": 1.0},
    ):
        t = tuner()
        feed(t, 0, 120, **kwargs)
        assert t.valves[V].samples == 0, kwargs


def test_learned_thresholds_never_below_configured():
    t = tuner()
    t.load({"valves": {V: {"holding": 5, "samples": 9}}})
    assert t.thresholds(V) == (23.0, 16)  # off = max(16, 8), on = off + 7
    t.load({"valves": {V: {"holding": 80, "samples": 9}}})
    assert t.thresholds(V) == (67.0, 60.0)  # off capped at 60


# --- on delay -------------------------------------------------------------

def test_on_delay_blocks_start_for_three_minutes():
    t = tuner()
    t.load({"valves": {V: {"holding": 30, "samples": 9}}})
    on, off = t.control_thresholds(0, {V: 45.0})[0]
    assert math.isinf(on) and off == 33.0
    assert math.isinf(t.control_thresholds(2.5 * MIN, {V: 45.0})[0][0])
    assert t.control_thresholds(3 * MIN, {V: 45.0})[0] == (40.0, 33.0)
    # Dropping below resets the delay.
    t.control_thresholds(4 * MIN, {V: 35.0})
    assert math.isinf(t.control_thresholds(5 * MIN, {V: 45.0})[0][0])


def test_decide_with_per_valve_thresholds():
    s = logic.Settings(19, 16, 0, 0, 30 * MIN, "on")
    # Valve 1 is at 35 %, above the global 19 % but below its own on threshold 40 %.
    d = logic.decide([35, 0], False, False, 1e9, None, s, [(40, 33), (19, 16)])
    assert d.demand is False
    d = logic.decide([35, 20], False, False, 1e9, None, s, [(40, 33), (19, 16)])
    assert d.action == "on"
    # Running boiler: 35 % is above that valve's off threshold, so it keeps heating.
    d = logic.decide([35, 0], True, True, 1e9, None, s, [(40, 33), (19, 16)])
    assert d.demand is True and d.action is None
    d = logic.decide([30, 0], True, True, 1e9, None, s, [(40, 33), (19, 16)])
    assert d.action == "off"
    # On delay (inf) keeps a stopped boiler off.
    d = logic.decide([90], False, False, 1e9, None, s, [(math.inf, 33)])
    assert d.action is None


# --- adaptive minimum off time --------------------------------------------

def test_min_off_grows_with_frequent_starts_and_recovers():
    t = tuner()
    for i in range(4):
        t.record_start(i * 10 * MIN)
    assert t.min_off_s(30 * MIN) == 13 * MIN
    for i in range(4, 8):
        t.record_start(i * 10 * MIN)
    assert t.min_off_s(70 * MIN) == 20 * MIN  # capped
    # Starts leaving the hour window take 1 min off each ...
    assert t.min_off_s(119 * MIN) == 15 * MIN  # starts at 0..50 min expired
    # ... and an hour without any start goes back to the configured time.
    assert t.min_off_s(131 * MIN) == 8 * MIN
    for i in range(3):
        t.record_start((200 + i) * MIN)
    assert t.min_off_s(203 * MIN) == 8 * MIN  # 3 starts per hour are fine


# --- stuck valves ---------------------------------------------------------

def test_stuck_open_only_counts_while_boiler_heats():
    t = tuner()
    assert t.stuck_valves(0, {V: 100.0}, boiler_on=False) == set()
    assert t.stuck_valves(5 * 3600, {V: 100.0}, boiler_on=False) == set()
    assert t.stuck_valves(5 * 3600, {V: 100.0}, boiler_on=True) == set()
    assert t.stuck_valves(9 * 3600 - 1, {V: 100.0}, boiler_on=True) == set()
    assert t.stuck_valves(9 * 3600, {V: 96.0}, boiler_on=True) == {V}
    assert t.stuck_valves(9 * 3600 + 30, {V: 60.0}, boiler_on=True) == set()


# --- persistence ----------------------------------------------------------

def test_store_roundtrip_and_reset():
    t = tuner()
    feed(t, 0, 70, 30.0)
    data = t.as_dict()
    assert data == {"valves": {V: {"holding": 30.0, "samples": 5}}}
    restored = tuner()
    restored.load(data)
    assert restored.thresholds(V) == t.thresholds(V)
    restored.load({"valves": {"bad": {"holding": "x"}}})  # ignored
    t.reset()
    assert t.as_dict() == {"valves": {}} and t.holding(V) is None
