"""
Regression test for the real Week-1 season-boundary bug found and fixed
2026-09-04: /api/build-stub-week's own call into build_stub_week() used
to scope historical_seasons=[season] alone. For Week 1 of a season with
no games played yet, nfl_data_py.import_pbp_data([season]) comes back
genuinely empty — shape (0, 0), zero COLUMNS, not just zero rows,
confirmed directly against the real nflverse endpoint, not assumed —
and run_pipeline -> aggregate_redzone_game -> _touches indexes
pbp["rush_attempt"] unconditionally, so that single-season pbp frame
raises a real KeyError before a single stub row is ever built.

Real, live regression test, not a synthetic fixture — this codebase has
no existing synthetic-pbp fixture builder for run_pipeline's aggregation
step (unlike CFB's redzone tests), and NFL's own established convention
elsewhere (test_team_tendencies.py, test_intelligence_lifecycle.py) is
to validate this exact class of function against real nfl_data_py data
rather than inventing one. Real network calls, ~30s.

Run: python3 nfl/scripts/test_build_stub_week.py
"""
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vendor"))

import pandas as pd

warnings.filterwarnings("ignore")

import nfl_data_py as nfl

from backfill_redzone import (
    load_depth_charts, load_id_crosswalk, load_injuries, load_schedules,
    load_seasonal_rosters, load_snap_counts,
)
from build_stub_week import build_stub_week

# A season already fully played out (real, completed, real pbp) — stands
# in for "last season" relative to the brand-new TARGET_SEASON below,
# exactly the fallback the fix adds.
PRIOR_SEASON = 2025
# The season under test: deliberately a real season with NO games played
# yet as of whenever this test runs (2026's own Week 1 hasn't happened),
# so nfl_data_py genuinely has nothing published for it — the real bug
# scenario, not a mocked one.
TARGET_SEASON = 2026
TARGET_WEEK = 1


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return bool(condition)


if __name__ == "__main__":
    results = []

    print(f"Loading real pbp for {PRIOR_SEASON} and {TARGET_SEASON} (one real network call each)...")
    pbp_prior = nfl.import_pbp_data([PRIOR_SEASON], downcast=True)
    pbp_target = nfl.import_pbp_data([TARGET_SEASON], downcast=True)

    results.append(check(
        f"the real bug precondition still holds: nfl_data_py has zero pbp published for "
        f"{TARGET_SEASON} (0 rows AND 0 columns, not just 0 rows) — if this ever fails, "
        f"{TARGET_SEASON}'s season has started and this test should move to next season",
        pbp_target.shape == (0, 0),
    ))
    results.append(check(
        f"the real fallback data exists: {PRIOR_SEASON} has real, non-empty, correctly-shaped pbp",
        len(pbp_prior) > 0 and "rush_attempt" in pbp_prior.columns,
    ))

    pbp_combined = pd.concat([pbp_prior, pbp_target], ignore_index=True)

    # Shared, non-pbp raw inputs — loaded once for the broader season set
    # and reused across both calls below. The bug (and the fix) is
    # entirely about pbp's own emptiness; these six are already
    # independently resilient to a season with nothing published yet
    # (see load_snap_counts'/load_injuries' own docstrings) regardless
    # of which historical_seasons list is passed, so sharing them here
    # keeps this test fast and focused on the one real variable that
    # actually matters.
    print("Loading shared roster/schedule/snap/injury/depth-chart data (one real network round)...")
    load_seasons = [PRIOR_SEASON, TARGET_SEASON]
    snap_counts = load_snap_counts(load_seasons)
    id_crosswalk = load_id_crosswalk(load_seasons)
    depth_charts = load_depth_charts(load_seasons)
    injuries = load_injuries(load_seasons)
    seasonal_rosters = load_seasonal_rosters(load_seasons)
    schedules = load_schedules(load_seasons)

    common_kwargs = dict(
        snap_counts=snap_counts, id_crosswalk=id_crosswalk, depth_charts=depth_charts,
        injuries=injuries, seasonal_rosters=seasonal_rosters, schedules=schedules,
    )

    # ---- THE REGRESSION: the OLD, buggy single-season scoping really
    # does crash on the real Week-1-of-a-new-season case. Pins the exact
    # failure down so a future change can't silently reintroduce it
    # without this test noticing (a passing build here would mean the
    # bug came back, or run_pipeline grew its own graceful handling of
    # an empty-columns pbp frame — either way, worth knowing).
    try:
        build_stub_week(
            TARGET_SEASON, TARGET_WEEK, historical_seasons=[TARGET_SEASON],
            pbp=pbp_target, **common_kwargs,
        )
        results.append(check(
            "regression check: historical_seasons=[season] alone still raises for a real "
            "Week 1 with no pbp published — DID NOT RAISE (bug may have resurfaced upstream, "
            "or something now handles this gracefully; either way, investigate)",
            False,
        ))
    except KeyError as e:
        results.append(check(
            f"regression check: historical_seasons=[season] alone still raises the exact "
            f"real KeyError this bug produces (got {e!r})",
            str(e) == "'rush_attempt'",
        ))

    # ---- THE FIX: season - 1 alongside season succeeds cleanly on the
    # exact same real data.
    stub = build_stub_week(
        TARGET_SEASON, TARGET_WEEK, historical_seasons=[TARGET_SEASON - 1, TARGET_SEASON],
        pbp=pbp_combined, **common_kwargs,
    )
    results.append(check(
        f"fix check: historical_seasons=[season-1, season] builds real stub rows for "
        f"{TARGET_SEASON} Week {TARGET_WEEK} instead of raising",
        len(stub) > 0,
    ))
    results.append(check(
        "fix check: every stub row is really keyed to the target season/week",
        bool(len(stub)) and (stub["season"] == TARGET_SEASON).all() and (stub["week"] == TARGET_WEEK).all(),
    ))
    results.append(check(
        "fix check: the three real pillars are populated (td_opportunity/role_momentum/situation), "
        "not silently all-NaN",
        {"td_opportunity", "role_momentum", "situation"} <= set(stub.columns)
        and stub["td_opportunity"].notna().all()
        and stub["role_momentum"].notna().all()
        and stub["situation"].notna().all(),
    ))
    results.append(check(
        "fix check: market_value_score stays honestly NaN — that's Phase 2 (the odds poller)'s "
        "job, not build_stub_week's",
        "market_value_score" in stub.columns and stub["market_value_score"].isna().all(),
    ))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
