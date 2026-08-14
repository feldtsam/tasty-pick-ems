"""
Tasty Pick Ems — NFL Red Zone Backfill

Pulls play-by-play data for the specified seasons and derives per-player,
per-game red zone / inside-10 / goal-line usage via the shared
redzone.aggregate_redzone_game logic, so this backfill and the live weekly
job stay in sync. Also pulls snap-count data and joins on offensive snap
totals / snap share via redzone.add_snap_shares.

This is a standalone, re-runnable batch script — NOT wired into the live
pipeline yet. Run it, inspect/spot-check the output, then wire the live
weekly job up to import the redzone module directly.

Usage:
    python scripts/backfill_redzone.py

Output:
    player_redzone_weekly.csv — one row per player per game
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# nfl_data_py is vendored, not pip-installed — see nfl/vendor/README.md
# for why (0.3.3's own PyPI metadata pins pandas<2.0, incompatible with
# this project's pandas 2.x requirement; the real code works fine with
# pandas 2.x, only the declared constraint is stale).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vendor"))

import nfl_data_py as nfl
import pandas as pd

from redzone import (
    add_defensive_matchup_context,
    add_depth_chart_rank,
    add_environment_data,
    add_injury_context,
    add_opponent,
    add_player_position,
    add_rolling_windows,
    add_snap_shares,
    aggregate_redzone_allowed,
    aggregate_redzone_game,
    build_id_crosswalk,
)
from scoring import (
    score_evidence_quality,
    score_role_momentum,
    score_situation,
    score_td_opportunity,
    score_universal_tpe,
)

SEASONS = [2022, 2024, 2025]


def load_pbp(seasons: list[int]) -> pd.DataFrame:
    """Load raw play-by-play for the given seasons."""
    pbp = nfl.import_pbp_data(seasons, downcast=True)
    return pbp


def load_snap_counts(seasons: list[int]) -> pd.DataFrame:
    """Load raw snap-count data (PFR-sourced) for the given seasons."""
    return nfl.import_snap_counts(seasons)


def load_id_crosswalk(seasons: list[int]) -> pd.DataFrame:
    """Load the raw id/roster tables build_id_crosswalk needs to translate
    snap_counts' pfr_player_id into play-by-play's gsis-style player_id."""
    id_table = nfl.import_ids()
    seasonal_rosters = nfl.import_seasonal_rosters(seasons)
    return build_id_crosswalk(id_table, seasonal_rosters)


def load_depth_charts(seasons: list[int]) -> pd.DataFrame:
    """Load raw depth charts for the given seasons. nflverse changed the
    schema for 2025+ (see redzone._new_schema_depth_chart) — both old- and
    new-schema rows come back mixed together in one frame from this single
    call, and redzone.add_depth_chart_rank parses both transparently."""
    return nfl.import_depth_charts(seasons)


def load_injuries(seasons: list[int]) -> pd.DataFrame:
    """Load raw injury reports for the given seasons. Unlike depth charts,
    this has clean gsis_id coverage across all three backfilled seasons."""
    return nfl.import_injuries(seasons)


def load_seasonal_rosters(seasons: list[int]) -> pd.DataFrame:
    """Load seasonal rosters for the given seasons — the source of clean
    per-player RB/WR/TE position labels used for the defensive-matchup
    aggregation (see redzone._position_lookup)."""
    return nfl.import_seasonal_rosters(seasons)


def load_schedules(seasons: list[int]) -> pd.DataFrame:
    """Load schedule data for the given seasons — provides opponent
    (home_team/away_team) and environment (roof/temp/wind) data."""
    return nfl.import_schedules(seasons)


def spot_check(weekly: pd.DataFrame, season: int, player_name_contains: str) -> None:
    """Print a player's game log for manual cross-check against a public
    red-zone stats source (e.g. a known bellcow RB's inside-5 carries)."""
    sub = weekly[
        (weekly["season"] == season)
        & (weekly["player_name"].str.contains(player_name_contains, case=False, na=False))
    ]
    cols = [
        "week", "player_name", "posteam", "rz_touches", "rz_tds", "gl_touches", "gl_tds",
        "rz_touch_share", "offense_snaps", "team_offense_snaps", "snap_share",
        "depth_rank", "ahead_injury_statuses",
        "defteam", "position_group", "defensive_matchup_vulnerability", "environment_score", "situation",
        "evidence_completeness", "evidence_convergence", "evidence_quality",
        "core_score", "confidence_multiplier", "tpe_score",
    ]
    print(sub[cols].to_string(index=False))


if __name__ == "__main__":
    print(f"Loading play-by-play for seasons {SEASONS} ...")
    pbp = load_pbp(SEASONS)

    print("Aggregating red zone / inside-10 / goal-line usage ...")
    weekly = aggregate_redzone_game(pbp)

    print("Loading snap counts ...")
    snap_counts = load_snap_counts(SEASONS)

    print("Building pfr_id -> gsis player_id crosswalk ...")
    id_crosswalk = load_id_crosswalk(SEASONS)

    print("Joining snap shares ...")
    weekly = add_snap_shares(weekly, snap_counts, id_crosswalk)
    unmatched = weekly["snap_share"].isna().sum()
    print(f"  {unmatched} / {len(weekly)} rows had no snap-share match "
          f"({unmatched / len(weekly):.1%})")

    print("Loading depth charts ...")
    depth_charts = load_depth_charts(SEASONS)

    print("Loading injury reports ...")
    injuries = load_injuries(SEASONS)

    print("Loading seasonal rosters ...")
    seasonal_rosters = load_seasonal_rosters(SEASONS)

    print("Loading schedules ...")
    schedules = load_schedules(SEASONS)

    print("Joining depth-chart rank ...")
    weekly = add_depth_chart_rank(weekly, depth_charts, schedules, seasonal_rosters)
    no_rank = weekly["depth_rank"].isna().sum()
    print(f"  {no_rank} / {len(weekly)} rows had no depth-chart rank "
          f"({no_rank / len(weekly):.1%})")

    print("Joining injury context ...")
    weekly = add_injury_context(weekly, depth_charts, injuries, schedules, seasonal_rosters)
    has_injury_ahead = weekly["ahead_injury_statuses"].apply(len).gt(0).sum()
    print(f"  {has_injury_ahead} / {len(weekly)} rows have >=1 teammate ahead with an injury designation "
          f"({has_injury_ahead / len(weekly):.1%})")

    print("Joining player position ...")
    weekly = add_player_position(weekly, seasonal_rosters)

    print("Joining opponent ...")
    weekly = add_opponent(weekly, schedules)

    print("Joining environment data ...")
    weekly = add_environment_data(weekly, schedules)

    print("Aggregating red zone usage allowed by defense/position group ...")
    allowed_weekly = aggregate_redzone_allowed(pbp, seasonal_rosters)
    allowed_weekly = add_rolling_windows(
        allowed_weekly,
        metrics=["rz_touches", "rz_tds", "i10_touches", "i10_tds", "gl_touches", "gl_tds"],
        group_cols=["defteam", "position_group", "season"],
    )

    print("Joining defensive matchup context ...")
    weekly = add_defensive_matchup_context(weekly, allowed_weekly)
    no_matchup = weekly["allowed_rz_tds_season_avg"].isna().sum()
    print(f"  {no_matchup} / {len(weekly)} rows had no defensive matchup context "
          f"({no_matchup / len(weekly):.1%}) — includes rows with no position_group (mostly QB scrambles)")

    # Must run after add_snap_shares — snap_share has to exist as a column
    # before it can be rolled into snap_share_last1/last3/last5/season_avg.
    print("Adding rolling trend windows ...")
    weekly = add_rolling_windows(weekly)

    print("Scoring TD Opportunity ...")
    weekly = score_td_opportunity(weekly)

    print("Scoring Role & Momentum ...")
    weekly = score_role_momentum(weekly)

    print("Scoring Situation ...")
    weekly = score_situation(weekly, allowed_weekly)

    # Must run last — a pure meta-layer over the other three pillars'
    # own outputs (completeness columns + final scores), nothing here is
    # computable any earlier in the pipeline.
    print("Scoring Evidence Quality & Convergence ...")
    weekly = score_evidence_quality(weekly)

    print("Scoring Universal TPE Score ...")
    weekly = score_universal_tpe(weekly)

    out_path = "player_redzone_weekly.csv"
    weekly.to_csv(out_path, index=False)
    print(f"Wrote {len(weekly)} rows to {out_path}")

    # Example spot check — swap in a known bellcow RB to sanity-check
    # against a public red-zone-touches stat before trusting this table.
    spot_check(weekly, season=2024, player_name_contains="Henry")
