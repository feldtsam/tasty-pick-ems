"""
Tasty Pick Ems — NFL "upcoming game" stub rows.

Phase 1 of the live-weekly-job gap flagged in backfill_redzone.py's own
docstring and in commit 49a6d12's report: nothing currently creates a
pre-game row that holds trailing/rolling features for a game that
hasn't been played yet, so market_value_score (score_market_value.py,
commit b20888a) has nothing real to merge onto in production —
score_universal_tpe only ever sees rows for games already played.

This script creates exactly that row, for one (season, week) at a time:
one stub per (player_id, season, week) for that week's eligible RB/WR/TE
pool, with td_opportunity / role_momentum / situation (and, for free,
evidence_quality / core_score / tpe_score) populated from real trailing
data — everything knowable before kickoff. market_value_score is
deliberately NOT populated here; that's Phase 2's job (a live-odds
poller/upsert, not built this round).

MECHANISM — no new merge/schema logic, all reused: a stub is a skeleton
row shaped exactly like aggregate_redzone_game's / aggregate_redzone_
allowed's own output (same columns, real per-game counts as NaN),
concatenated onto the real play-by-play-derived tables BEFORE any
downstream step runs (see backfill_redzone.run_pipeline's
extra_offense_rows/extra_defense_rows). add_rolling_windows' shift(1)
logic already only looks at the PRIOR row in each (player_id, season) /
(defteam, position_group, season) group, so a stub row inherits real
trailing values from whatever game actually preceded it with zero
changes needed there. Every other add_* step is a left join that
already NaN-fills gracefully for anything not found (a stub row's
game_id has no snap_counts entry yet — offense_snaps/snap_share for
THIS week correctly stay NaN, not fabricated as 0).

STORAGE DECISION: flat file (nfl/data/stub_weeks/{season}_wk{week}.csv),
following nfl/'s existing flat-file convention (backfill_redzone.py's
own CSV, and market_value.py's documented-but-not-yet-built snapshot
design) rather than standing up a real database — this project has no
database anywhere (checked when market_value.py's storage design was
written: no connection strings, no schema.sql, no ORM). A stub file is
small (one week's RB/WR/TE pool, ~150-250 rows) and gets REGENERATED
wholesale on every run of this script (there's no reason to patch it
in place for Phase 1 — depth-chart/injury snapshots themselves are
already "latest wins" semantics upstream, see redzone._new_schema_
depth_chart, so re-deriving the whole stub fresh each time is both
simpler and more correct than trying to patch individual fields).

TRADE-OFF, flagged rather than silently resolved: Phase 2 (the live
odds poller) WILL need to update market_value_score on an EXISTING stub
row repeatedly over the days leading up to kickoff, without touching
the td_opportunity/role_momentum/situation columns already computed
here. A full-file rewrite still works for that (this table is small and
single-process — nothing here needs concurrent-writer safety), but if
Phase 2 ever needs true partial-field atomic updates from multiple
concurrent pollers, that's the point to introduce SQLite (still zero
new ops burden, unlike Postgres/Supabase) — not decided now, since nothing
in Phase 1's scope requires it and building for a hypothetical
concurrency need this project doesn't have yet would be pure overhead.

Usage:
    python scripts/build_stub_week.py SEASON WEEK
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vendor"))

import numpy as np
import pandas as pd

from backfill_redzone import (
    SEASONS,
    load_depth_charts,
    load_id_crosswalk,
    load_injuries,
    load_pbp,
    load_schedules,
    load_seasonal_rosters,
    load_snap_counts,
    run_pipeline,
)

OFFENSE_COUNT_COLS = [
    "rz_touches", "rz_rush_touches", "rz_target_touches", "rz_tds",
    "i10_touches", "i10_rush_touches", "i10_target_touches", "i10_tds",
    "gl_touches", "gl_rush_touches", "gl_target_touches", "gl_tds",
    "team_rz_touches", "rz_touch_share",
]
DEFENSE_COUNT_COLS = [
    "rz_touches", "rz_rush_touches", "rz_target_touches", "rz_tds",
    "i10_touches", "i10_rush_touches", "i10_target_touches", "i10_tds",
    "gl_touches", "gl_rush_touches", "gl_target_touches", "gl_tds",
]


def build_stub_offense_rows(season: int, week: int, seasonal_rosters: pd.DataFrame, schedules: pd.DataFrame) -> pd.DataFrame:
    """
    Skeleton rows for a not-yet-played week, shaped to match
    redzone.aggregate_redzone_game's own output columns exactly (real
    count columns all NaN) so they concatenate cleanly — see this
    module's docstring and run_pipeline's extra_offense_rows for why
    that's the entire mechanism needed, no new joins here.

    One row per RB/WR/TE on a team with a scheduled game_id that week
    (bye-week teams are naturally excluded — they have no row in
    schedules for that week at all). Player pool comes from import_
    seasonal_rosters(), the same source and RB/WR/TE filter redzone.
    _position_lookup already uses — every rostered RB/WR/TE gets a stub
    row, same as every rostered RB/WR/TE gets a real row once the game
    is actually played (whether or not they end up touching the ball).
    """
    games = schedules[(schedules["season"] == season) & (schedules["week"] == week)][
        ["game_id", "home_team", "away_team"]
    ]
    team_game = pd.concat(
        [
            games[["game_id", "home_team"]].rename(columns={"home_team": "posteam"}),
            games[["game_id", "away_team"]].rename(columns={"away_team": "posteam"}),
        ],
        ignore_index=True,
    )

    ros = seasonal_rosters[
        (seasonal_rosters["season"] == season) & seasonal_rosters["position"].isin(["RB", "WR", "TE"])
    ][["player_id", "player_name", "team"]].dropna(subset=["player_id"]).drop_duplicates(subset=["player_id"])

    stub = team_game.merge(ros, left_on="posteam", right_on="team", how="inner").drop(columns=["team"])
    stub["season"] = season
    stub["week"] = week
    for c in OFFENSE_COUNT_COLS:
        stub[c] = np.nan

    return stub[["game_id", "season", "week", "posteam", "player_id", "player_name"] + OFFENSE_COUNT_COLS]


def build_stub_defense_rows(season: int, week: int, schedules: pd.DataFrame) -> pd.DataFrame:
    """
    Skeleton rows matching aggregate_redzone_allowed's own output
    columns exactly (real count columns all NaN) — one row per (team
    with a scheduled game that week) x (RB, WR, TE); every team is "on
    defense" against its opponent for this purpose. Concatenated onto
    the real defense-allowed table before add_rolling_windows(group_
    cols=["defteam","position_group","season"]) runs, so the defense's
    own trailing allowed-rate values carry forward the same shift(1)
    way the offense side's do — see run_pipeline's extra_defense_rows.
    """
    games = schedules[(schedules["season"] == season) & (schedules["week"] == week)][["home_team", "away_team"]]
    defteams = pd.concat([games["home_team"], games["away_team"]]).drop_duplicates().reset_index(drop=True)
    stub = pd.DataFrame({"defteam": defteams}).merge(pd.DataFrame({"position_group": ["RB", "WR", "TE"]}), how="cross")
    stub["season"] = season
    stub["week"] = week
    for c in DEFENSE_COUNT_COLS:
        stub[c] = np.nan

    return stub[["defteam", "season", "week", "position_group"] + DEFENSE_COUNT_COLS]


def build_stub_week(
    season: int,
    week: int,
    historical_seasons: list[int] = None,
    pbp: pd.DataFrame = None,
    snap_counts: pd.DataFrame = None,
    id_crosswalk: pd.DataFrame = None,
    depth_charts: pd.DataFrame = None,
    injuries: pd.DataFrame = None,
    seasonal_rosters: pd.DataFrame = None,
    schedules: pd.DataFrame = None,
    output_dir: Path = None,
) -> pd.DataFrame:
    """
    Build and write the stub week's rows. Loads fresh nfl_data_py data
    for whichever of historical_seasons (default: backfill_redzone.
    SEASONS) plus `season` aren't already passed in — passing the raw
    tables in directly (all seven, or none) lets a caller reuse data
    already loaded elsewhere (e.g. this module's own validation script,
    which needs to build two different pbp/snap_counts variants from
    the same base pull) without a second live nfl_data_py fetch.
    """
    load_seasons = sorted(set(historical_seasons or SEASONS) | {season})

    if pbp is None:
        pbp = load_pbp(load_seasons)
    if snap_counts is None:
        snap_counts = load_snap_counts(load_seasons)
    if id_crosswalk is None:
        id_crosswalk = load_id_crosswalk(load_seasons)
    if depth_charts is None:
        depth_charts = load_depth_charts(load_seasons)
    if injuries is None:
        injuries = load_injuries(load_seasons)
    if seasonal_rosters is None:
        seasonal_rosters = load_seasonal_rosters(load_seasons)
    if schedules is None:
        schedules = load_schedules(load_seasons)

    offense_stub = build_stub_offense_rows(season, week, seasonal_rosters, schedules)
    defense_stub = build_stub_defense_rows(season, week, schedules)

    weekly, _allowed_weekly = run_pipeline(
        pbp, snap_counts, id_crosswalk, depth_charts, injuries, seasonal_rosters, schedules,
        extra_offense_rows=offense_stub, extra_defense_rows=defense_stub,
    )

    stub_week = weekly[(weekly["season"] == season) & (weekly["week"] == week)].copy()
    stub_week["market_value_score"] = np.nan
    stub_week["market_value_completeness"] = np.nan

    out_dir = output_dir or (Path(__file__).resolve().parent.parent / "data" / "stub_weeks")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{season}_wk{week}.csv"
    stub_week.to_csv(out_path, index=False)
    print(f"Wrote {len(stub_week)} stub rows to {out_path}")

    return stub_week


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python scripts/build_stub_week.py SEASON WEEK", file=sys.stderr)
        raise SystemExit(1)
    season_arg, week_arg = int(sys.argv[1]), int(sys.argv[2])
    build_stub_week(season_arg, week_arg)
