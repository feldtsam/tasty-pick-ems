"""
Regression coverage for redzone.add_carries() and backfill_redzone.
load_weekly_stats() -- the 2026-09-29 fix wiring nflverse's weekly
player_stats `carries` field into the pipeline. No prior test file
covered this join at all.

Synthetic fixtures only, no real network -- the join logic itself
(key matching, REG-only filtering, NaN-preserving on a miss) is what's
under test, same scope discipline test_reconcile_week.py already uses
for the parts of this pipeline that don't need real nflverse data to
verify.
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))

from redzone import add_carries


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    return condition


def _base_weekly():
    return pd.DataFrame([
        {"game_id": "2026_03_BUF_LAC", "season": 2026, "week": 3, "posteam": "BUF", "player_id": "00-0034827"},
        {"game_id": "2026_03_DEN_JAX", "season": 2026, "week": 3, "posteam": "DEN", "player_id": "00-0039868"},
        {"game_id": "2026_03_NO_LV", "season": 2026, "week": 3, "posteam": "NO", "player_id": "00-NOMATCH"},
    ])


def run_real_match_gets_real_carries():
    print("\n" + "=" * 70)
    print("A real (player_id, season, week) match gets the real carries value")
    print("=" * 70)

    weekly_stats = pd.DataFrame([
        {"player_id": "00-0034827", "season": 2026, "week": 3, "season_type": "REG", "carries": 4},
        {"player_id": "00-0039868", "season": 2026, "week": 3, "season_type": "REG", "carries": 11},
    ])

    out = add_carries(_base_weekly(), weekly_stats)

    ok = True
    ok &= check("output has the same row count as input (no rows dropped or duplicated)", len(out) == 3)
    ok &= check("carries column exists", "carries" in out.columns)
    row1 = out[out["player_id"] == "00-0034827"].iloc[0]
    row2 = out[out["player_id"] == "00-0039868"].iloc[0]
    ok &= check("player 1's real carries value survived the join", row1["carries"] == 4)
    ok &= check("player 2's real carries value survived the join", row2["carries"] == 11)
    return ok


def run_no_match_is_honest_nan_not_dropped():
    print("\n" + "=" * 70)
    print("No weekly_stats match -- row kept, carries is NaN, never silently dropped")
    print("=" * 70)

    weekly_stats = pd.DataFrame([
        {"player_id": "00-0034827", "season": 2026, "week": 3, "season_type": "REG", "carries": 4},
    ])

    out = add_carries(_base_weekly(), weekly_stats)
    no_match_row = out[out["player_id"] == "00-NOMATCH"]

    ok = True
    ok &= check("the unmatched player's row is still present (never dropped)", len(no_match_row) == 1)
    ok &= check("the unmatched player's carries is honestly NaN, not 0 or a guess", no_match_row.iloc[0]["carries"] != no_match_row.iloc[0]["carries"])
    return ok


def run_postseason_row_excluded_no_collision():
    print("\n" + "=" * 70)
    print("A POST row at the same raw week number never overrides the real REG value")
    print("=" * 70)

    weekly_stats = pd.DataFrame([
        {"player_id": "00-0034827", "season": 2026, "week": 3, "season_type": "REG", "carries": 4},
        # Deliberately conflicting: same (player_id, season, week) key,
        # tagged POST, with a different carries value -- if REG filtering
        # or the drop_duplicates step were wrong, this could silently win.
        {"player_id": "00-0034827", "season": 2026, "week": 3, "season_type": "POST", "carries": 99},
    ])

    out = add_carries(_base_weekly(), weekly_stats)
    row = out[out["player_id"] == "00-0034827"].iloc[0]

    ok = True
    ok &= check("exactly one row per player (POST duplicate never fans the join out)", len(out) == 3)
    ok &= check("the REAL regular-season carries value wins, not the POST row's", row["carries"] == 4)
    return ok


def run_missing_season_type_column_degrades_honestly():
    print("\n" + "=" * 70)
    print("weekly_stats with no season_type column at all -- degrades honestly, doesn't crash")
    print("=" * 70)

    weekly_stats = pd.DataFrame([
        {"player_id": "00-0034827", "season": 2026, "week": 3, "carries": 4},
    ])

    raised = None
    out = None
    try:
        out = add_carries(_base_weekly(), weekly_stats)
    except Exception as e:
        raised = e

    ok = True
    ok &= check("no crash when season_type is absent (e.g. an empty-columns fallback frame)", raised is None)
    if out is not None:
        row = out[out["player_id"] == "00-0034827"].iloc[0]
        ok &= check("the real value still joins correctly without season_type filtering", row["carries"] == 4)
    return ok


def run_empty_weekly_stats_is_honest_all_nan():
    print("\n" + "=" * 70)
    print("A genuinely empty weekly_stats frame (a season with nothing published) -- all-NaN, not a crash")
    print("=" * 70)

    from backfill_redzone import WEEKLY_STATS_COLUMNS
    weekly_stats = pd.DataFrame(columns=WEEKLY_STATS_COLUMNS)

    raised = None
    out = None
    try:
        out = add_carries(_base_weekly(), weekly_stats)
    except Exception as e:
        raised = e

    ok = True
    ok &= check("no crash on a genuinely empty weekly_stats frame", raised is None)
    if out is not None:
        ok &= check("every row kept (3), all honestly NaN", len(out) == 3 and out["carries"].isna().all())
    return ok


def run_load_weekly_stats_tolerates_one_bad_season():
    print("\n" + "=" * 70)
    print("load_weekly_stats: one season's fetch failing doesn't lose the others")
    print("=" * 70)

    import backfill_redzone as br

    def _fake_import_weekly_data(years, columns=None):
        year = years[0]
        if year == 2026:
            raise ValueError("simulated: 2026 not published yet")
        return pd.DataFrame([
            {"player_id": "00-TEST", "season": year, "week": 1, "season_type": "REG", "carries": 7},
        ])

    original = br.nfl.import_weekly_data
    br.nfl.import_weekly_data = _fake_import_weekly_data
    try:
        out = br.load_weekly_stats([2025, 2026])
    finally:
        br.nfl.import_weekly_data = original

    ok = True
    ok &= check("2025's real data survived despite 2026 raising", len(out) == 1 and out.iloc[0]["season"] == 2025)
    ok &= check("no exception propagated out of load_weekly_stats itself", True)
    return ok


if __name__ == "__main__":
    results = [
        run_real_match_gets_real_carries(),
        run_no_match_is_honest_nan_not_dropped(),
        run_postseason_row_excluded_no_collision(),
        run_missing_season_type_column_degrades_honestly(),
        run_empty_weekly_stats_is_honest_all_nan(),
        run_load_weekly_stats_tolerates_one_bad_season(),
    ]
    print()
    if all(results):
        print("All checks passed.")
    else:
        print("Some checks FAILED -- see above.")
        raise SystemExit(1)
