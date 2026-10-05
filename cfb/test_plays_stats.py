"""
Unit checks for cfb/plays_stats.py — the CFBD call-volume / latency layer.

No network: fetch_week_play_stats is exercised with a monkeypatched
per-game fetcher. estimate_week_cost is pure arithmetic.

    python3 cfb/test_plays_stats.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import plays_stats
from plays_stats import VERCEL_MAX_DURATION_S, estimate_week_cost, fetch_week_play_stats


def check(label, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    return bool(cond)


if __name__ == "__main__":
    r = []

    # ---- estimate_week_cost ------------------------------------------------
    full = estimate_week_cost(90)
    r.append(check(f"90-game week: ~94 CFBD calls (got {full['estimated_cfbd_calls']})",
                   90 <= full["estimated_cfbd_calls"] <= 100))
    r.append(check(f"90-game week fits Vercel {VERCEL_MAX_DURATION_S}s with margin "
                   f"(est {full['estimated_wall_clock_s']}s)",
                   full["fits_with_margin"] and full["estimated_wall_clock_s"] < VERCEL_MAX_DURATION_S * 0.66))
    r.append(check("more workers -> lower estimate",
                   estimate_week_cost(90, workers=12)["estimated_wall_clock_s"]
                   <= estimate_week_cost(90, workers=4)["estimated_wall_clock_s"]))
    r.append(check("a normal week stays well under the wall-clock ceiling even at 130 games",
                   estimate_week_cost(130, workers=8)["fits_with_margin"]))
    r.append(check("call-volume tripwire: a 200-game run flags high_call_volume",
                   estimate_week_cost(200, workers=8)["high_call_volume"]
                   and not estimate_week_cost(95, workers=8)["high_call_volume"]))
    r.append(check("roster_cached drops the call count by 1",
                   estimate_week_cost(90)["estimated_cfbd_calls"]
                   - estimate_week_cost(90, roster_cached=True)["estimated_cfbd_calls"] == 1))

    # ---- fetch_week_play_stats: concurrency + per-item resilience ---------
    calls: list[int] = []

    def fake_fetch(game_id, *, season_type="regular"):
        calls.append(game_id)
        if game_id == 999:
            raise RuntimeError("boom")
        return [{"playId": f"{game_id}-1", "statType": "Rush"},
                {"playId": f"{game_id}-2", "statType": "Reception"}]

    plays_stats.fetch_play_stats_for_game = fake_fetch
    completed = [{"id": i} for i in (1, 2, 3, 999, 5)]
    rows, diag = fetch_week_play_stats(completed, max_workers=4)

    r.append(check("all 5 games attempted", sorted(calls) == [1, 2, 3, 5, 999]))
    r.append(check("the erroring game is recorded, not fatal",
                   len(diag["games_errored"]) == 1 and diag["games_errored"][0]["game_id"] == 999))
    r.append(check("4 good games -> 8 rows", diag["play_stat_rows_total"] == 8 and len(rows) == 8))
    r.append(check("workers capped at completed-game count",
                   fetch_week_play_stats([{"id": 1}], max_workers=8)[1]["workers"] == 1))

    print()
    p = sum(r)
    print(f"{p}/{len(r)} checks passed")
    raise SystemExit(0 if p == len(r) else 1)
