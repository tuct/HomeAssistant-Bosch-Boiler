"""Pure decision logic for Boiler Demand (no Home Assistant imports)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import math

STATUS_IDLE = "idle"
STATUS_HEATING = "heating"
STATUS_WAITING_MIN_ON = "waiting_min_on"
STATUS_WAITING_MIN_OFF = "waiting_min_off"
STATUS_VALVES_OFFLINE = "valves_offline"
STATUS_FAILSAFE = "failsafe"
STATUS_SWITCH_UNAVAILABLE = "switch_unavailable"
STATUS_DISABLED = "disabled"

STATUSES = [
    STATUS_IDLE,
    STATUS_HEATING,
    STATUS_WAITING_MIN_ON,
    STATUS_WAITING_MIN_OFF,
    STATUS_VALVES_OFFLINE,
    STATUS_FAILSAFE,
    STATUS_SWITCH_UNAVAILABLE,
    STATUS_DISABLED,
]

ACTION_ON = "on"
ACTION_OFF = "off"


@dataclass(frozen=True)
class Settings:
    """Control parameters (thresholds in %, times in seconds)."""

    on_threshold: float
    off_threshold: float
    min_on_s: float
    min_off_s: float
    failsafe_after_s: float
    failsafe_mode: str  # "on" | "off" | "hold"


@dataclass(frozen=True)
class Decision:
    """Result of one evaluation."""

    demand: bool
    action: str | None
    status: str
    remaining_s: float | None = None


def parse_position(state: str | None) -> float | None:
    """Return valve opening in %, or None if the value is not usable.

    Unavailable/unknown/non-numeric states and negative values (Bosch reports -1
    while a valve is not ready) are treated as "no data" and ignored.
    """
    if state is None:
        return None
    try:
        value = float(state)
    except (TypeError, ValueError):
        return None
    if math.isnan(value) or value < 0:
        return None
    return min(value, 100.0)


def update_demand(
    valid: Sequence[float], demand: bool, on_threshold: float, off_threshold: float
) -> bool:
    """Apply hysteresis: on if any valve >= on, off if all <= off, else keep."""
    return update_demand_per_valve(
        [(p, on_threshold, off_threshold) for p in valid], demand
    )


def update_demand_per_valve(
    valid: Sequence[tuple[float, float, float]], demand: bool
) -> bool:
    """Hysteresis with own thresholds per valve: (position, on, off) tuples."""
    if any(p >= on for p, on, _ in valid):
        return True
    if all(p <= off for p, _, off in valid):
        return False
    return demand


def decide(
    positions: Sequence[float | None],
    demand: bool,
    switch_on: bool | None,
    switch_state_for_s: float,
    offline_for_s: float | None,
    settings: Settings,
    thresholds: Sequence[tuple[float, float]] | None = None,
) -> Decision:
    """Decide demand and the switch action.

    positions: parsed valve openings, None for valves without usable data.
    demand: demand from the previous evaluation (hysteresis memory).
    switch_on: current boiler switch state, None if unknown/unavailable.
    switch_state_for_s: seconds since the switch last changed state.
    offline_for_s: seconds since all valves went offline, None if any is online.
    thresholds: (on, off) per valve (auto-tune), default: the ones from settings.
    """
    if thresholds is None:
        thresholds = [(settings.on_threshold, settings.off_threshold)] * len(positions)
    valid = [
        (p, on, off) for p, (on, off) in zip(positions, thresholds, strict=True) if p is not None
    ]

    if not valid:
        waiting = offline_for_s is None or offline_for_s < settings.failsafe_after_s
        if waiting or settings.failsafe_mode == "hold":
            remaining = None
            if waiting and settings.failsafe_mode != "hold":
                remaining = settings.failsafe_after_s - (offline_for_s or 0.0)
            return Decision(demand, None, STATUS_VALVES_OFFLINE, remaining)
        # Failsafe bypasses min on/off times.
        demand = settings.failsafe_mode == "on"
        action = None
        if switch_on is not None and switch_on != demand:
            action = ACTION_ON if demand else ACTION_OFF
        return Decision(demand, action, STATUS_FAILSAFE)

    demand = update_demand_per_valve(valid, demand)

    if switch_on is None:
        return Decision(demand, None, STATUS_SWITCH_UNAVAILABLE)

    if demand and not switch_on:
        remaining = settings.min_off_s - switch_state_for_s
        if remaining > 0:
            return Decision(demand, None, STATUS_WAITING_MIN_OFF, remaining)
        return Decision(demand, ACTION_ON, STATUS_HEATING)

    if not demand and switch_on:
        remaining = settings.min_on_s - switch_state_for_s
        if remaining > 0:
            return Decision(demand, None, STATUS_WAITING_MIN_ON, remaining)
        return Decision(demand, ACTION_OFF, STATUS_IDLE)

    return Decision(demand, None, STATUS_HEATING if switch_on else STATUS_IDLE)
