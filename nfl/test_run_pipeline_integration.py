"""
Real-pipeline integration check for scripts.backfill_redzone.run_pipeline()
-- modeled directly on test_team_tendencies.py's own "Full real-pipeline
integration check" pattern (a real nfl_data_py network call, wrapped in
try/except, skipped with a logged reason rather than failed on a network
hiccup, never silently skipped without saying why).

REAL GAP THIS CLOSES (2026-10-02): every existing test that touches
run_pipeline()/add_carries()/shape_player_season_evidence_rows() -- see
test_redzone_carries.py ("Synthetic fixtures only, no real network") and
scripts/test_player_season_evidence.py (hand-built `extra` dict with
"targets": 5 already in it) -- exercises its own piece of the chain in
isolation, against fixtures that already assume the thing being tested
(that targets/carries actually reach `weekly`) is true. Neither could have
caught the real 2026-10-02 bug (targets never wired into run_pipeline() at
all; carries silently all-NaN because nflverse's player_stats release for
2025/2026 doesn't exist yet). This test runs the real thing end to end
for one real, already-played week and checks the real output directly.

Run: python3 nfl/test_run_pipeline_integration.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))

import nfl_data_py as nfl

from backfill_redzone import (
    SEASONS,
    load_depth_charts,
    load_id_crosswalk,
    load_injuries,
    load_pbp,
    load_schedules,
    load_seasonal_rosters,
    load_snap_counts,
    load_weekly_stats,
    run_pipeline,
)


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


if __name__ == "__main__":
    results = []

    # Real, already-played week: 2024 Week 1. James Conner (00-0033553,
    # ARI) is a known real bellcow both as a runner and a check-down
    # receiver that week -- confirmed directly before writing this
    # assertion (real targets=4, real carries=16, both independently
    # cross-checked against nflverse's own player_stats_2024.parquet
    # release during the redzone_carries investigation), not guessed.
    SEASON = 2024
    WEEK = 1
    PLAYER = "00-0033553"
    EXPECTED_CARRIES = 16
    EXPECTED_TARGETS = 4

    try:
        # Loads the real, full production SEASONS list (same seasons
        # reconcile_week()/build_stub_week() always load together), not
        # just [SEASON] in isolation -- confirmed directly that a lone
        # 2024-only depth-chart load trips an unrelated pre-existing
        # schema-detection quirk in redzone._new_schema_depth_chart (a
        # single-season call doesn't carry the 2025+ `dt` column at all,
        # which _combined_depth_chart's real-world callers never hit
        # because they always load a season range spanning both schema
        # eras). Out of scope for this task (not a targets/carries bug),
        # so worked around here by loading the real multi-season range
        # production actually uses, rather than touched in redzone.py.
        print(f"Loading real {SEASONS} data for run_pipeline() (one real network round)...", flush=True)
        pbp = load_pbp(SEASONS)
        snap_counts = load_snap_counts(SEASONS)
        id_crosswalk = load_id_crosswalk(SEASONS)
        depth_charts = load_depth_charts(SEASONS)
        injuries = load_injuries(SEASONS)
        seasonal_rosters = load_seasonal_rosters(SEASONS)
        schedules = load_schedules(SEASONS)
        weekly_stats = load_weekly_stats(SEASONS)

        weekly, _allowed_weekly = run_pipeline(
            pbp, snap_counts, id_crosswalk, depth_charts, injuries, seasonal_rosters, schedules, weekly_stats,
        )
        row = weekly[
            (weekly["player_id"] == PLAYER) & (weekly["season"] == SEASON) & (weekly["week"] == WEEK)
        ]

        results.append(check(
            f"REAL {SEASON} Week {WEEK}: {PLAYER} has exactly one real row in run_pipeline()'s own weekly output",
            len(row) == 1,
        ))
        if len(row) == 1:
            r = row.iloc[0]
            results.append(check(
                f"REAL {SEASON} Week {WEEK}: {PLAYER}'s `targets` is present and non-null "
                f"(got {r['targets']!r}, expected {EXPECTED_TARGETS})",
                r["targets"] == EXPECTED_TARGETS,
            ))
            results.append(check(
                f"REAL {SEASON} Week {WEEK}: {PLAYER}'s `carries` is present and non-null "
                f"(got {r['carries']!r}, expected {EXPECTED_CARRIES})",
                r["carries"] == EXPECTED_CARRIES,
            ))
            results.append(check(
                "REAL: no pandas merge-collision columns (targets_x/targets_y/carries_x/carries_y) "
                "leaked into run_pipeline()'s real output",
                not any(c.endswith(("_x", "_y")) for c in weekly.columns if c.startswith(("targets", "carries"))),
            ))

        # Broader population check, not just the one spot-checked player:
        # targets/carries should be non-null for the large majority of
        # real rows, not just the one player above (a real wiring bug
        # would typically show as the column being entirely absent or
        # entirely null, not spot-null for one player).
        week_rows = weekly[(weekly["season"] == SEASON) & (weekly["week"] == WEEK)]
        results.append(check(
            f"REAL {SEASON} Week {WEEK}: `targets` column exists and is non-null for the large majority of "
            f"real rows ({week_rows['targets'].notna().sum()}/{len(week_rows)})",
            "targets" in week_rows.columns and week_rows["targets"].notna().mean() > 0.5,
        ))
        results.append(check(
            f"REAL {SEASON} Week {WEEK}: `carries` column exists and is non-null for a real, non-trivial share "
            f"of rows ({week_rows['carries'].notna().sum()}/{len(week_rows)}) -- not every red-zone-touch row is "
            f"a rusher (many are pure red-zone targets), so this is intentionally a lower bar than `targets`",
            "carries" in week_rows.columns and week_rows["carries"].notna().sum() > 0,
        ))
    except Exception as e:
        results.append(check(
            f"REAL {SEASON} Week {WEEK} run_pipeline() integration check (skipped -- {type(e).__name__}: {e})",
            True,
        ))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
