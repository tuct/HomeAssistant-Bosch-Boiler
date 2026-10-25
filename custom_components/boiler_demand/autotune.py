"""Auto-tune: learn per-valve thresholds from how the valves behave (no Home Assistant imports).

A thermostatic valve that has reached its setpoint does not close completely while the
boiler runs; it settles at a steady opening that just replaces the heat the room loses.
That "holding level" differs per radiator (big radiator: 15 %, small one: 50 %), so a
single on/off threshold either starts the boiler for rooms that are already warm or
waits too long for rooms that are cold.

The tuner:
- learns each valve's holding level while the boiler is on and the valve is steady,
- derives per-valve thresholds from it (off just above the holding level, on clearly above),
- only lets a valve start the boiler once it stayed above its on threshold for a while,
- stretches the minimum off time when the boiler starts too often (more than 3
  starts within an hour: +5 min, up to 20 min; it shrinks again as starts age out),
- flags valves that stay (almost) fully open for hours although the boiler heats.

All times are POSIX timestamps in seconds.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

# Learning: a valve is "steady" if it moved at most STEADY_BAND_PCT within STEADY_WINDOW_S.
STEADY_WINDOW_S = 30 * 60
STEADY_BAND_PCT = 3.0
LEARN_MIN_PCT = 3.0  # below: valve is closed, nothing to learn
LEARN_MAX_PCT = 90.0  # above: valve is wide open (cold room), not holding
SAMPLE_EVERY_S = 10 * 60
SMOOTHING = 0.2  # weight of a new sample in the moving average
MIN_SAMPLES = 3  # samples before learned thresholds are used

# Thresholds derived from the holding level h.
OFF_MARGIN_PCT = 3.0  # off = h + 3
ON_GAP_PCT = 7.0  # on = off + 7
MAX_OFF_PCT = 60.0

ON_DELAY_S = 3 * 60

# Adaptive minimum off time.
STARTS_WINDOW_S = 60 * 60
MAX_STARTS_PER_WINDOW = 3
MIN_OFF_STEP_UP_S = 5 * 60
MIN_OFF_STEP_DOWN_S = 60
MIN_OFF_MAX_S = 20 * 60

# Valve open (almost) fully this long while the boiler is on: stuck, or no heat arrives.
STUCK_OPEN_PCT = 95.0
STUCK_AFTER_S = 4 * 60 * 60


@dataclass
class ValveLearning:
    """What was learned about one valve."""

    holding: float | None = None
    samples: int = 0
    window: deque[tuple[float, float]] = field(default_factory=deque)
    last_sample: float | None = None
    above_since: float | None = None
    open_since: float | None = None


class AutoTuner:
    """Learning state for all valves of one boiler."""

    def __init__(self, base_on: float, base_off: float, base_min_off_s: float) -> None:
        self.base_on = base_on
        self.base_off = base_off
        self.base_min_off_s = base_min_off_s
        self.valves: dict[str, ValveLearning] = {}
        self.starts: deque[float] = deque()
        self.extra_min_off_s = 0.0

    def set_base(self, base_on: float, base_off: float, base_min_off_s: float) -> None:
        """Configured thresholds/min off time, the floor for everything learned."""
        self.base_on, self.base_off, self.base_min_off_s = base_on, base_off, base_min_off_s

    def _valve(self, valve: str) -> ValveLearning:
        return self.valves.setdefault(valve, ValveLearning())

    # ------------------------------------------------------------------ learning

    def observe(
        self, now: float, positions: Mapping[str, float | None], boiler_on: bool
    ) -> bool:
        """Feed one periodic sample of all valves; returns True if a holding level changed."""
        changed = False
        for valve, position in positions.items():
            learning = self._valve(valve)
            window = learning.window
            if not boiler_on or position is None:
                window.clear()  # only valves throttling against a running boiler count
                continue
            window.append((now, position))
            while window and window[0][0] < now - STEADY_WINDOW_S:
                window.popleft()
            # Allow one missed sample at the start of the window.
            if window[0][0] > now - STEADY_WINDOW_S + 60:
                continue
            values = [p for _, p in window]
            low, high = min(values), max(values)
            if low < LEARN_MIN_PCT or high > LEARN_MAX_PCT or high - low > STEADY_BAND_PCT:
                continue
            if learning.last_sample is not None and now - learning.last_sample < SAMPLE_EVERY_S:
                continue
            sample = sum(values) / len(values)
            learning.last_sample = now
            if learning.holding is None:
                learning.holding = sample
            else:
                learning.holding += SMOOTHING * (sample - learning.holding)
            learning.samples += 1
            changed = True
        return changed

    def holding(self, valve: str) -> float | None:
        """Learned holding level, None while still learning."""
        learning = self.valves.get(valve)
        if learning is None or learning.holding is None or learning.samples < MIN_SAMPLES:
            return None
        return learning.holding

    def thresholds(self, valve: str) -> tuple[float, float]:
        """(on, off) for one valve; the configured ones until a holding level is known."""
        holding = self.holding(valve)
        if holding is None:
            return self.base_on, self.base_off
        off = max(self.base_off, min(MAX_OFF_PCT, holding + OFF_MARGIN_PCT))
        return max(self.base_on, off + ON_GAP_PCT), off

    def control_thresholds(
        self, now: float, positions: Mapping[str, float | None]
    ) -> list[tuple[float, float]]:
        """Thresholds for logic.decide, with the on delay applied.

        A valve that is above its on threshold for less than ON_DELAY_S gets an
        unreachable on threshold, so it keeps a running boiler on but cannot start it.
        """
        result = []
        for valve, position in positions.items():
            learning = self._valve(valve)
            on, off = self.thresholds(valve)
            if position is None or position < on:
                learning.above_since = None
            else:
                if learning.above_since is None:
                    learning.above_since = now
                if now - learning.above_since < ON_DELAY_S:
                    on = float("inf")
            result.append((on, off))
        return result

    # ------------------------------------------------------------------ min off time

    def record_start(self, now: float) -> None:
        """The boiler was switched on."""
        self._expire_starts(now)
        self.starts.append(now)
        if len(self.starts) > MAX_STARTS_PER_WINDOW:
            limit = max(0.0, MIN_OFF_MAX_S - self.base_min_off_s)
            self.extra_min_off_s = min(limit, self.extra_min_off_s + MIN_OFF_STEP_UP_S)

    def _expire_starts(self, now: float) -> None:
        while self.starts and self.starts[0] < now - STARTS_WINDOW_S:
            self.starts.popleft()
            self.extra_min_off_s = max(0.0, self.extra_min_off_s - MIN_OFF_STEP_DOWN_S)
        if not self.starts:
            self.extra_min_off_s = 0.0  # a whole hour without a start: back to configured

    def min_off_s(self, now: float) -> float:
        """Minimum off time including the extra added for frequent starts."""
        self._expire_starts(now)
        return self.base_min_off_s + self.extra_min_off_s

    # ------------------------------------------------------------------ stuck valves

    def stuck_valves(
        self, now: float, positions: Mapping[str, float | None], boiler_on: bool
    ) -> set[str]:
        """Valves at >= STUCK_OPEN_PCT for STUCK_AFTER_S while the boiler is on.

        With the boiler off a fully open valve is normal (waiting for heat), so the
        timer only runs while the boiler heats.
        """
        stuck = set()
        for valve, position in positions.items():
            learning = self._valve(valve)
            if not boiler_on or position is None or position < STUCK_OPEN_PCT:
                learning.open_since = None
                continue
            if learning.open_since is None:
                learning.open_since = now
            if now - learning.open_since >= STUCK_AFTER_S:
                stuck.add(valve)
        return stuck

    # ------------------------------------------------------------------ persistence

    def reset(self) -> None:
        """Forget everything learned."""
        self.valves.clear()
        self.starts.clear()
        self.extra_min_off_s = 0.0

    def as_dict(self) -> dict[str, Any]:
        """Learned holding levels for storage."""
        return {
            "valves": {
                valve: {"holding": learning.holding, "samples": learning.samples}
                for valve, learning in self.valves.items()
                if learning.holding is not None
            }
        }

    def load(self, data: Mapping[str, Any] | None) -> None:
        """Restore holding levels saved with as_dict."""
        for valve, item in ((data or {}).get("valves") or {}).items():
            try:
                holding, samples = float(item["holding"]), int(item["samples"])
            except (KeyError, TypeError, ValueError):
                continue
            learning = self._valve(valve)
            learning.holding, learning.samples = holding, samples
