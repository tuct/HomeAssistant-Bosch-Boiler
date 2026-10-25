"""Fill the dev Home Assistant's recorder with a week of history, for the history card.

Replays a Home Assistant history export (valve sensors) onto the simulated rooms of
dev/config, shifted so it ends now. Boiler runs come from Boiler Demand's own logic,
room and flow temperatures are synthesised from them, and auto-tune's learned holding
levels are pre-filled from the same replay.

    docker compose -f dev/docker-compose.yml stop
    python dev/seed_history.py history.csv
    docker compose -f dev/docker-compose.yml start

Valves are matched to the dev rooms by name (tobi / timon / kuche); override with
--map csv_entity=dev_entity. Run it again to replace the seeded history.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import math
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "dev" / "config"
DB = CONFIG / "home-assistant_v2.db"

DEV_VALVES = {
    "tobi": "sensor.heizung_tobi_valve_position",
    "timon": "sensor.heizung_timon_valve_position",
    "kuche": "sensor.heizung_kuche_valve_position",
}
DEV_TEMPS = {
    "sensor.heizung_tobi_valve_position": ("sensor.heizung_tobi_temperature", 21.5),
    "sensor.heizung_timon_valve_position": ("sensor.heizung_timon_temperature", 20.5),
    "sensor.heizung_kuche_valve_position": ("sensor.heizung_kuche_temperature", 20.0),
}
BOILER = "input_boolean.therme"
FLOW = "sensor.boiler_flow_temperature"
STEP_S = 30


def load_simulator():
    spec = importlib.util.spec_from_file_location("simulate", ROOT / "tools" / "simulate.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def match_valves(series: dict, overrides: dict[str, str]) -> dict[str, str]:
    """dev valve entity -> csv valve entity."""
    mapping = {dev: csv for csv, dev in overrides.items()}
    free = [v for v in series if v not in mapping.values()]
    for key, dev in DEV_VALVES.items():
        if dev in mapping:
            continue
        candidates = [v for v in free if key in v] or free
        if not candidates:
            continue
        best = max(candidates, key=lambda v: len(series[v]))  # most active
        mapping[dev] = best
        free.remove(best)
    return mapping


def dev_entry_id() -> str | None:
    try:
        entries = json.loads((CONFIG / ".storage" / "core.config_entries").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return next((e["entry_id"] for e in entries["data"]["entries"] if e["domain"] == "boiler_demand"), None)


def write_learning(sim, mapped: dict) -> None:
    """Replay with auto-tune and store what it learned, as the integration would."""
    entry_id = dev_entry_id()
    if entry_id is None:
        print("No Boiler Demand entry in dev/config - skipping learned holding levels")
        return
    tuner = sim.AutoTune(dev_settings(sim))
    sim.simulate(mapped, tuner, STEP_S)
    data = tuner.tuner.as_dict()
    store = {"version": 1, "minor_version": 1, "key": f"boiler_demand.{entry_id}", "data": data}
    (CONFIG / ".storage" / f"boiler_demand.{entry_id}").write_text(json.dumps(store, indent=2), encoding="utf-8")
    print("Learned holding levels:")
    for valve, item in data["valves"].items():
        print(f"  {valve}: {item['holding']:.0f} % ({item['samples']} samples)")


def dev_settings(sim):
    """Thresholds of the dev Boiler Demand entry (defaults if not set up yet)."""
    options = {"on_threshold": 25, "off_threshold": 12, "min_on_minutes": 10, "min_off_minutes": 8}
    try:
        entries = json.loads((CONFIG / ".storage" / "core.config_entries").read_text(encoding="utf-8"))
        for entry in entries["data"]["entries"]:
            if entry["domain"] == "boiler_demand":
                options.update(entry["options"])
                break
    except (OSError, KeyError, ValueError):
        pass
    return sim.logic.Settings(
        float(options["on_threshold"]), float(options["off_threshold"]),
        float(options["min_on_minutes"]) * 60, float(options["min_off_minutes"]) * 60, 1800, "on",
    )


def build_states(sim, series, mapping, start, end) -> dict[str, list[tuple[float, str]]]:
    """entity_id -> [(timestamp, state)] for valves, boiler, temperatures and flow."""
    out: dict[str, list[tuple[float, str]]] = {}
    for dev, csv in mapping.items():
        out[dev] = [(t.timestamp(), s) for t, s in series[csv]]

    # Boiler from Boiler Demand's logic on the mapped valves.
    mapped = {dev: series[csv] for dev, csv in mapping.items()}
    result = sim.simulate(mapped, sim.BoilerDemand("dev", dev_settings(sim)), STEP_S)
    boiler = [(start.timestamp(), "off")]
    for run_start, run_end in result.runs:
        boiler += [(run_start.timestamp(), "on"), (run_end.timestamp(), "off")]
    out[BOILER] = boiler

    # Temperatures: first-order lag towards "warm" while heat arrives, "cold" otherwise.
    timeline = sim.Timeline(mapped)
    temps = {dev: DEV_TEMPS[dev][1] - 1.0 for dev in mapping}
    flow = 25.0
    now = start
    tick = 0
    temp_rows: dict[str, list[tuple[float, str]]] = {DEV_TEMPS[dev][0]: [] for dev in mapping}
    flow_rows: list[tuple[float, str]] = []
    on_at = lambda t: any(a <= t < b for a, b in result.runs)  # noqa: E731
    while now <= end:
        raw = timeline.advance(now)
        heating = on_at(now)
        flow += ((55.0 if heating else 22.0) - flow) * STEP_S / (8 * 60)
        for dev, state in raw.items():
            valve = sim.logic.parse_position(state) or 0.0
            comfort = DEV_TEMPS[dev][1]
            target = comfort + 1.0 if heating and valve > 5 else comfort - 3.0
            temps[dev] += (target - temps[dev]) * STEP_S / (3 * 3600)
        if tick % 20 == 0:  # every 10 min
            day = now.timestamp() / 86400
            for dev, value in temps.items():
                noise = 0.1 * math.sin(day * 97 + hash(dev) % 7)
                temp_rows[DEV_TEMPS[dev][0]].append((now.timestamp(), f"{value + noise:.1f}"))
            flow_rows.append((now.timestamp(), f"{flow:.1f}"))
        now += timedelta(seconds=STEP_S)
        tick += 1
    out.update(temp_rows)
    out[FLOW] = flow_rows
    return out


def write_db(states: dict[str, list[tuple[float, str]]], before: float) -> None:
    con = sqlite3.connect(DB)
    cur = con.cursor()
    for entity_id, rows in states.items():
        found = cur.execute("SELECT metadata_id FROM states_meta WHERE entity_id = ?", (entity_id,)).fetchone()
        if found:
            meta = found[0]
        else:
            cur.execute("INSERT INTO states_meta (entity_id) VALUES (?)", (entity_id,))
            meta = cur.lastrowid
        old = [r[0] for r in cur.execute(
            "SELECT state_id FROM states WHERE metadata_id = ? AND last_updated_ts < ?", (meta, before))]
        if old:
            marks = ",".join("?" * len(old))
            cur.execute(f"UPDATE states SET old_state_id = NULL WHERE old_state_id IN ({marks})", old)
            cur.execute(f"DELETE FROM states WHERE state_id IN ({marks})", old)
        cur.executemany(
            "INSERT INTO states (state, last_updated_ts, metadata_id, origin_idx) VALUES (?, ?, ?, 0)",
            [(state, ts, meta) for ts, state in rows],
        )
        print(f"  {entity_id}: {len(rows)} states")
    con.commit()
    con.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", type=Path)
    ap.add_argument("--days", type=float, default=8, help="days of history to keep (default 8)")
    ap.add_argument("--map", action="append", default=[], metavar="CSV=DEV",
                    help="map a csv valve to a dev valve, e.g. sensor.radiator_kuche_valvetappet=sensor.heizung_kuche_valve_position")
    args = ap.parse_args()
    if not DB.exists():
        sys.exit(f"{DB} not found - start the dev Home Assistant once first")

    sim = load_simulator()
    series = sim.load_history(args.csv, None)
    if not series:
        sys.exit("no valve sensors found in the CSV")
    overrides = dict(item.split("=", 1) for item in args.map)
    mapping = match_valves(series, overrides)

    # Shift so the export ends now, keep the last --days.
    last = max(points[-1][0] for points in series.values())
    shift = datetime.now(timezone.utc) - last
    end = last + shift
    start = end - timedelta(days=args.days)
    shifted = {v: [(t + shift, s) for t, s in pts] for v, pts in series.items()}
    clipped = sim.clip({csv: shifted[csv] for csv in mapping.values()}, start, end)

    print("Valves:")
    for dev, csv in mapping.items():
        print(f"  {csv} -> {dev}")
    print(f"Period: {start.astimezone():%Y-%m-%d %H:%M} -> {end.astimezone():%Y-%m-%d %H:%M}")
    states = build_states(sim, clipped, mapping, start, end)
    write_db(states, end.timestamp() + 1)
    write_learning(sim, {dev: clipped[csv] for dev, csv in mapping.items()})
    print("Done - start the dev Home Assistant again.")


if __name__ == "__main__":
    main()
