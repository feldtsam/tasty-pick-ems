"""Historical replay harness (SPEC.md acceptance test 1).

Evaluates checks.py at every 2-hour tick from Sep 8 00:00 UTC to Sep 24
23:59 UTC against the real Make history fixture, using only data that
existed before each tick. checks.evaluate() filters on `now`, so each tick
genuinely cannot see its own future.

`last_collection_at` is set to the previous tick so H1 never fires here --
this harness measures the TPE rules, not Sutton's own uptime.

Usage:
    python3 sutton/replay.py              # day-by-day table
    python3 sutton/replay.py --ticks      # every tick
    python3 sutton/replay.py --scenario 6186710 --ticks
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from checks import evaluate  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE_PATH = os.path.join(
    HERE, "tests", "fixtures", "make_history_2026-09-07_to_09-24.json"
)

REPLAY_START = datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc)
REPLAY_END = datetime(2026, 9, 24, 23, 59, tzinfo=timezone.utc)
TICK = timedelta(hours=2)

SHORT = {
    6186710: "Picks",
    6241867: "GradeBookmarks",
    6152892: "ATTDPoller",
    6079077: "Split2",
}

_STATUS_RANK = {"GREEN": 0, "YELLOW": 1, "RED": 2}


def load_fixture(path: str = FIXTURE_PATH) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def ticks(start: datetime = REPLAY_START, end: datetime = REPLAY_END, step: timedelta = TICK):
    cur = start
    while cur <= end:
        yield cur
        cur += step


def run(payload: dict, scenario_filter: int | None = None) -> list[dict]:
    """Evaluate every tick. Returns one result row per tick."""
    if scenario_filter:
        payload = {
            **payload,
            "scenarios": [
                s for s in payload["scenarios"] if s["scenario_id"] == scenario_filter
            ],
        }

    results = []
    for tick in ticks():
        state = {"last_collection_at": (tick - TICK).strftime("%Y-%m-%dT%H:%M:%SZ")}
        out = evaluate(payload, state=state, now=tick)
        results.append({"tick": tick, "result": out})
    return results


def _loud(signals):
    """RADAR and ESCALATE only. LOG signals are the quiet majority."""
    return [s for s in signals if s["tier"] in ("RADAR", "ESCALATE")]


def print_tick_table(results: list[dict]) -> None:
    print(f"{'TICK (UTC)':<20} {'STATUS':<7} SIGNALS")
    print("-" * 100)
    for row in results:
        out = row["result"]
        loud = _loud(out["tpe_signals"])
        desc = (
            "  ".join(
                f"{SHORT.get(s['scenario_id'], s['scenario_id'])}:{s['signal_id']}({s['tier']})"
                for s in loud
            )
            or "--"
        )
        print(f"{row['tick'].strftime('%Y-%m-%d %H:%M'):<20} {out['status']:<7} {desc}")


def print_day_table(results: list[dict]) -> None:
    by_day: dict[str, dict] = {}
    for row in results:
        day = row["tick"].strftime("%Y-%m-%d")
        out = row["result"]
        entry = by_day.setdefault(day, {"status": "GREEN", "signals": {}, "first": {}})
        if _STATUS_RANK[out["status"]] > _STATUS_RANK[entry["status"]]:
            entry["status"] = out["status"]
        for sig in _loud(out["tpe_signals"]):
            key = (sig["scenario_id"], sig["signal_id"], sig["tier"])
            entry["signals"][key] = True
            entry["first"].setdefault(key, row["tick"])

    print(f"{'DATE':<12} {'STATUS':<7} SIGNALS FIRED (per scenario)")
    print("-" * 100)
    for day in sorted(by_day):
        entry = by_day[day]
        if entry["signals"]:
            parts = []
            for (sid, sig_id, tier), _ in sorted(
                entry["signals"].items(), key=lambda kv: (SHORT.get(kv[0][0], ""), kv[0][1])
            ):
                first = entry["first"][(sid, sig_id, tier)].strftime("%H:%M")
                parts.append(f"{SHORT.get(sid, sid)}:{sig_id}({tier})@{first}")
            desc = "  ".join(parts)
        else:
            desc = "--"
        print(f"{day:<12} {entry['status']:<7} {desc}")


def print_first_fires(results: list[dict]) -> None:
    seen: dict[tuple, datetime] = {}
    for row in results:
        for sig in _loud(row["result"]["tpe_signals"]):
            key = (sig["scenario_id"], sig["signal_id"], sig["tier"])
            seen.setdefault(key, row["tick"])
    print("\nFIRST FIRE")
    print("-" * 100)
    for (sid, sig_id, tier), when in sorted(seen.items(), key=lambda kv: kv[1]):
        print(
            f"{when.strftime('%Y-%m-%d %H:%M')}  {SHORT.get(sid, sid):<16} {sig_id} ({tier})"
        )


def print_radar_table(results: list[dict]) -> None:
    """What Sam would actually receive: one aggregated 7:00am CT radar per day
    (SPEC.md Email formats)."""
    from radar import daily_radars, render_radar_line

    radars = daily_radars(results)
    print(f"{'RADAR SENT (7:00am CT)':<24} {'STATUS':<7} SIGNALS SINCE LAST RADAR")
    print("-" * 110)
    for r in radars:
        label = (
            r["sent_at"].strftime("%Y-%m-%d %H:%MZ")
            if r["sent_at"]
            else "(partial, not yet sent)"
        )
        print(f"{label:<24} {render_radar_line(r, SHORT)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticks", action="store_true", help="print every 2h tick")
    ap.add_argument("--radar", action="store_true", help="print aggregated daily radars")
    ap.add_argument("--scenario", type=int, default=None)
    ap.add_argument("--fixture", default=FIXTURE_PATH)
    args = ap.parse_args()

    payload = load_fixture(args.fixture)
    results = run(payload, args.scenario)

    if args.radar:
        print_radar_table(results)
        return
    if args.ticks:
        print_tick_table(results)
    else:
        print_day_table(results)
    print_first_fires(results)


if __name__ == "__main__":
    main()
