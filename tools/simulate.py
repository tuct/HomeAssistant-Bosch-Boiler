"""Replay a Home Assistant history export through boiler control strategies.

Works with any history CSV exported from Home Assistant (History -> download):
    entity_id,state,last_changed

    python tools/simulate.py history.csv
    python tools/simulate.py history.csv --valves sensor.a,sensor.b --on 25 --off 20
    python tools/simulate.py history.csv --from "2026-10-20 13:00"   # skip days without heat

Use --from/--to to cut out periods where the boiler was off (summer, not yet started):
valves then sit at 100 % waiting for heat, which distorts every strategy.

Strategies compared:
    old        the classic two automations: on if any valve > on, off if all valves < off
               (+ unavailable / -1 blocks off), no minimum times
    current    Boiler Demand with the given thresholds and minimum times
    suggested  Boiler Demand with on 25 % / off 20 %
    autotune   Boiler Demand with auto-tune on (custom_components/boiler_demand/autotune.py)

Caveat: this is open loop. The valves in the export reacted to the boiler that actually
ran at the time, so results are an estimate, best used to compare strategies.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
import importlib.util
from pathlib import Path
import statistics
import sys

_PACKAGE = Path(__file__).parents[1] / "custom_components" / "boiler_demand"


def _load(name: str):
    """Import a pure module of the integration without Home Assistant installed."""
    spec = importlib.util.spec_from_file_location(f"boiler_demand_{name}", _PACKAGE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


logic = _load("logic")
autotune = _load("autotune")

VALVE_HINTS = ("valvetappet", "valve_tappet", "valve_position", "valve_opening", "pi_heating_demand")


# --------------------------------------------------------------------------- input

def load_history(path: Path, valves: list[str] | None) -> dict[str, list[tuple[datetime, str]]]:
    series: dict[str, list[tuple[datetime, str]]] = defaultdict(list)
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            entity = row["entity_id"].strip()
            if valves is not None and entity not in valves:
                continue
            if valves is None and not any(h in entity for h in VALVE_HINTS):
                continue
            ts = datetime.fromisoformat(row["last_changed"].replace("Z", "+00:00"))
            series[entity].append((ts, row["state"].strip()))
    for points in series.values():
        points.sort()
    return dict(series)


def parse_time(text: str) -> datetime:
    ts = datetime.fromisoformat(text)
    return ts if ts.tzinfo else ts.astimezone()  # naive -> local time


def clip(series, start: datetime | None, end: datetime | None):
    """Keep [start, end]; the last value before start becomes the value at start."""
    out = {}
    for valve, points in series.items():
        kept = [(t, s) for t, s in points if (start is None or t >= start) and (end is None or t <= end)]
        before = [(t, s) for t, s in points if start is not None and t < start]
        if before:
            kept.insert(0, (start, before[-1][1]))
        if end is not None and kept:
            kept.append((end, kept[-1][1]))
        if kept:
            out[valve] = kept
    return out


class Timeline:
    """Step through all valve series; .states() gives the raw state of each valve."""

    def __init__(self, series: dict[str, list[tuple[datetime, str]]]) -> None:
        self.series = series
        self.index = {v: -1 for v in series}

    def advance(self, now: datetime) -> dict[str, str | None]:
        out = {}
        for valve, points in self.series.items():
            i = self.index[valve]
            while i + 1 < len(points) and points[i + 1][0] <= now:
                i += 1
            self.index[valve] = i
            out[valve] = points[i][1] if i >= 0 else None
        return out


# --------------------------------------------------------------------------- strategies

@dataclass
class Result:
    name: str
    runs: list[tuple[datetime, datetime]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class OldAutomation:
    """The two original automations (numeric_state above/below are exclusive)."""

    name = "old"

    def __init__(self, on_above: float, off_below: float) -> None:
        self.on_above, self.off_below = on_above, off_below

    def step(self, now, raw: dict[str, str | None], boiler_on: bool, dt_s: float) -> bool:
        nums = {}
        for v, s in raw.items():
            try:
                nums[v] = float(s)
            except (TypeError, ValueError):
                nums[v] = None
        if not boiler_on and any(n is not None and n > self.on_above for n in nums.values()):
            return True
        if boiler_on and all(n is not None and -1 < n < self.off_below for n in nums.values()):
            return False
        return boiler_on


class BoilerDemand:
    """The integration's real decision logic (logic.decide)."""

    def __init__(self, name: str, settings) -> None:
        self.name, self.settings = name, settings
        self.demand = False
        self.since = 1e9  # seconds the switch has been in its state
        self.offline_for: float | None = None

    def step(self, now, raw, boiler_on: bool, dt_s: float) -> bool:
        positions = [logic.parse_position(s) for s in raw.values()]
        if any(p is not None for p in positions):
            self.offline_for = None
        else:
            self.offline_for = (self.offline_for or 0.0) + dt_s
        d = logic.decide(positions, self.demand, boiler_on, self.since, self.offline_for, self.settings)
        self.demand = d.demand
        new = boiler_on
        if d.action == "on":
            new = True
        elif d.action == "off":
            new = False
        self.since = 0.0 if new != boiler_on else self.since + dt_s
        return new


class AutoTune:
    """Boiler Demand with auto-tune on, driven by the integration's AutoTuner."""

    name = "autotune"

    def __init__(self, settings) -> None:
        self.settings = settings
        self.tuner = autotune.AutoTuner(
            settings.on_threshold, settings.off_threshold, settings.min_off_s
        )
        self.demand = False
        self.since = 1e9
        self.offline_for: float | None = None
        self.stuck_reported: set[str] = set()
        self.notes: list[str] = []

    def step(self, now, raw, boiler_on: bool, dt_s: float) -> bool:
        t = now.timestamp()
        positions = {v: logic.parse_position(s) for v, s in raw.items()}
        self.tuner.observe(t, positions, boiler_on)
        for valve in self.tuner.stuck_valves(t, positions, boiler_on) - self.stuck_reported:
            self.stuck_reported.add(valve)
            since = datetime.fromtimestamp(self.tuner.valves[valve].open_since).astimezone()
            self.notes.append(
                f"{valve}: >= {autotune.STUCK_OPEN_PCT:g} % for {autotune.STUCK_AFTER_S / 3600:g} h "
                f"with boiler on since {since:%d.%m %H:%M} - valve stuck or no heat arriving "
                "(boiler off? cut with --from)"
            )
        if any(p is not None for p in positions.values()):
            self.offline_for = None
        else:
            self.offline_for = (self.offline_for or 0.0) + dt_s
        settings = replace(self.settings, min_off_s=self.tuner.min_off_s(t))
        d = logic.decide(
            list(positions.values()), self.demand, boiler_on, self.since, self.offline_for,
            settings, self.tuner.control_thresholds(t, positions),
        )
        self.demand = d.demand
        new = boiler_on
        if d.action == "on":
            new = True
            self.tuner.record_start(t)
        elif d.action == "off":
            new = False
        self.since = 0.0 if new != boiler_on else self.since + dt_s
        return new


# --------------------------------------------------------------------------- run + report

def simulate(series, strategy, step_s: int) -> Result:
    start = min(p[0][0] for p in series.values())
    end = max(p[-1][0] for p in series.values())
    timeline = Timeline(series)
    result = Result(strategy.name)
    boiler_on, run_start = False, None
    now = start
    while now <= end:
        raw = timeline.advance(now)
        new = strategy.step(now, raw, boiler_on, step_s)
        if new and not boiler_on:
            run_start = now
        elif boiler_on and not new:
            result.runs.append((run_start, now))
        boiler_on = new
        now += timedelta(seconds=step_s)
    if boiler_on:
        result.runs.append((run_start, end))
    result.notes = getattr(strategy, "notes", [])
    return result


def report(results: list[Result], days: float) -> str:
    lines = [
        "| strategy | on time | on % | starts | starts/day | median run | runs < 10 min | longest run |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    total_s = days * 86400
    for r in results:
        durations = [(b - a).total_seconds() / 60 for a, b in r.runs]
        on_s = sum(durations) * 60
        lines.append(
            f"| {r.name} | {on_s / 3600:.1f} h | {100 * on_s / total_s:.0f} % | {len(durations)} | "
            f"{len(durations) / days:.1f} | {statistics.median(durations) if durations else 0:.0f} min | "
            f"{sum(d < 10 for d in durations)} | {max(durations) if durations else 0:.0f} min |"
        )
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", type=Path)
    ap.add_argument("--valves", help="comma-separated entity ids (default: auto-detect valve sensors)")
    ap.add_argument("--on", type=float, default=19, help="current on threshold %% (default 19)")
    ap.add_argument("--off", type=float, default=16, help="current off threshold %% (default 16)")
    ap.add_argument("--min-on", type=float, default=10, help="minimum on time, min")
    ap.add_argument("--min-off", type=float, default=8, help="minimum off time, min")
    ap.add_argument("--old-on", type=float, default=19, help="old automation: on above %%")
    ap.add_argument("--old-off", type=float, default=20, help="old automation: off below %%")
    ap.add_argument("--step", type=int, default=30, help="simulation step, seconds")
    ap.add_argument("--from", dest="start", type=parse_time, help="start, e.g. '2026-10-20 13:00' (local)")
    ap.add_argument("--to", dest="end", type=parse_time, help="end (local time unless an offset is given)")
    args = ap.parse_args()

    valves = [v.strip() for v in args.valves.split(",")] if args.valves else None
    series = clip(load_history(args.csv, valves), args.start, args.end)
    if not series:
        sys.exit("no valve sensors found in the CSV (use --valves)")

    def settings(on, off):
        return logic.Settings(on, off, args.min_on * 60, args.min_off * 60, 30 * 60, "on")

    current = settings(args.on, args.off)
    auto = AutoTune(current)
    strategies = [
        OldAutomation(args.old_on, args.old_off),
        BoilerDemand("current", current),
        BoilerDemand("suggested", settings(25, 20)),
        auto,
    ]
    start = min(p[0][0] for p in series.values())
    end = max(p[-1][0] for p in series.values())
    days = (end - start).total_seconds() / 86400

    print(f"Valves ({len(series)}): {', '.join(series)}")
    print(f"Period: {start.astimezone():%Y-%m-%d %H:%M} -> {end.astimezone():%Y-%m-%d %H:%M} ({days:.1f} days)")
    print(f"current = on {args.on:g} / off {args.off:g} %, min on {args.min_on:g} / off {args.min_off:g} min\n")
    results = [simulate(series, s, args.step) for s in strategies]
    print(report(results, days))
    print("\nautotune learned holding levels:")
    for valve in series:
        h = auto.tuner.holding(valve)
        on_t, off_t = auto.tuner.thresholds(valve)
        learning = auto.tuner.valves.get(valve)
        samples = learning.samples if learning else 0
        print(f"  {valve}: " + (f"holding {h:.0f} % -> on {on_t:.0f} / off {off_t:.0f} ({samples} samples)"
                                if h is not None
                                else f"still learning, {samples} samples (stays on {on_t:g} / off {off_t:g})"))
    for note in auto.notes:
        print(f"  ! {note}")


if __name__ == "__main__":
    main()
