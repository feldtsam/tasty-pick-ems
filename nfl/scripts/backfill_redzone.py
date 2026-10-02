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
    aggregate_whole_game_carries,
    aggregate_whole_game_targets,
    build_id_crosswalk,
)
from scoring import (
    score_evidence_quality,
    score_role_momentum,
    score_signal_breach,
    score_situation,
    score_td_opportunity,
    score_universal_tpe,
)

SEASONS = [2022, 2024, 2025]


def load_pbp(seasons: list[int]) -> pd.DataFrame:
    """Load raw play-by-play for the given seasons."""
    pbp = nfl.import_pbp_data(seasons, downcast=True)
    return pbp


SNAP_COUNTS_COLUMNS = [
    "game_id", "pfr_game_id", "season", "game_type", "week", "player", "pfr_player_id",
    "position", "team", "opponent", "offense_snaps", "offense_pct", "defense_snaps",
    "defense_pct", "st_snaps", "st_pct",
]
INJURIES_COLUMNS = [
    "season", "season_type", "game_type", "team", "week", "gsis_id", "position", "full_name",
    "first_name", "last_name", "report_primary_injury", "report_secondary_injury",
    "report_status", "practice_primary_injury", "practice_secondary_injury", "practice_status",
]


def _load_per_season_tolerant(seasons: list[int], import_fn, empty_columns: list[str], label: str) -> pd.DataFrame:
    """
    Shared resilience helper for load_snap_counts/load_injuries below.

    import_pbp_data (nfl_data_py, vendored) already fetches year-by-year
    internally with a per-year try/except, so one season with no
    published file yet (confirmed directly: 2026 snap counts and
    injuries both 404 right now, since no 2026 game has been played to
    generate either) doesn't take down the other, real seasons in the
    same call — see its vendored source. import_snap_counts and
    import_injuries do NOT have that same per-year isolation (both are a
    single pandas.concat over a plain per-year list comprehension with
    no try/except at all — confirmed by reading their vendored source),
    so passing a mixed list like [2022, 2024, 2025, 2026] to either
    raises on 2026 alone and loses 2022/2024/2025's real data too, not
    just 2026's. Reimplemented here as its own per-year loop, matching
    import_pbp_data's own established resilience pattern rather than
    inventing a new one — one bad season is skipped and logged, the rest
    still load normally.
    """
    parts = []
    for year in seasons:
        try:
            parts.append(import_fn([year]))
        except Exception as e:
            print(f"  {label} unavailable for {year} ({type(e).__name__}: {e}) — skipping that season", flush=True)
    if not parts:
        return pd.DataFrame(columns=empty_columns)
    return pd.concat(parts, ignore_index=True)


def load_snap_counts(seasons: list[int]) -> pd.DataFrame:
    """
    Load raw snap-count data (PFR-sourced) for the given seasons. See
    _load_per_season_tolerant for why this fetches year-by-year rather
    than passing the whole list to nfl_data_py in one call — a season
    with no games played yet (e.g. build_stub_week.py's target season,
    right before its first game) would otherwise take down every other
    season's real data too, not just its own. A stub week's own
    add_snap_shares call is already designed to NaN-fill gracefully for
    any row with no snap-count match (see redzone.add_snap_shares), so
    an empty frame for that one season is the correct fallback, not a
    crash.
    """
    return _load_per_season_tolerant(seasons, nfl.import_snap_counts, SNAP_COUNTS_COLUMNS, "snap counts")


# Real, confirmed columns on nflverse's player_stats_{year}.parquet
# release (the same file import_weekly_data/import_seasonal_data both
# read — see vendor/nfl_data_py/__init__.py's own import_seasonal_data,
# which selects 'carries' directly off this file with no transform).
# season_type is requested so callers can filter to REG the same way
# import_seasonal_data itself does for this file -- weekly's own
# (player_id, season, week) key has no season_type component, so a
# postseason row at the same raw week number as a regular-season row
# would otherwise be a real, silent join collision risk, not a
# hypothetical one.
WEEKLY_STATS_COLUMNS = ["player_id", "season", "week", "season_type", "carries"]


def load_weekly_stats(seasons: list[int]) -> pd.DataFrame:
    """
    Load raw weekly player-stats data (nflverse's player_stats release)
    for the given seasons -- currently just `carries`, the one field
    add_carries actually needs (see redzone.add_carries). Restricted via
    import_weekly_data's own `columns` param rather than pulling the
    full ~50-column real release and discarding most of it.

    Same _load_per_season_tolerant wrapping as load_snap_counts/load_
    injuries, for the identical real reason: import_weekly_data (like
    import_snap_counts/import_injuries) is a single pandas.concat over a
    plain per-year list comprehension with no try/except of its own
    (confirmed by reading its vendored source) -- one season with no
    published file yet would otherwise take down every other season's
    real data too.
    """
    return _load_per_season_tolerant(
        seasons,
        lambda yrs: nfl.import_weekly_data(yrs, columns=WEEKLY_STATS_COLUMNS),
        WEEKLY_STATS_COLUMNS,
        "weekly stats",
    )


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
    """
    Load raw injury reports for the given seasons. Unlike depth charts,
    this has clean gsis_id coverage across all three backfilled seasons.
    Same year-by-year resilience as load_snap_counts — see
    _load_per_season_tolerant — for the same reason: a season with no
    injury report published yet (e.g. build_stub_week.py's target
    season, right before its first game) would otherwise take down every
    other season's real data too.
    """
    return _load_per_season_tolerant(seasons, nfl.import_injuries, INJURIES_COLUMNS, "injury reports")


def load_seasonal_rosters(seasons: list[int]) -> pd.DataFrame:
    """Load seasonal rosters for the given seasons — the source of clean
    per-player RB/WR/TE position labels used for the defensive-matchup
    aggregation (see redzone._position_lookup)."""
    return nfl.import_seasonal_rosters(seasons)


def load_schedules(seasons: list[int]) -> pd.DataFrame:
    """Load schedule data for the given seasons — provides opponent
    (home_team/away_team) and environment (roof/temp/wind) data."""
    return nfl.import_schedules(seasons)


def run_pipeline(
    pbp: pd.DataFrame,
    snap_counts: pd.DataFrame,
    id_crosswalk: pd.DataFrame,
    depth_charts: pd.DataFrame,
    injuries: pd.DataFrame,
    seasonal_rosters: pd.DataFrame,
    schedules: pd.DataFrame,
    weekly_stats: pd.DataFrame,
    extra_offense_rows: pd.DataFrame = None,
    extra_defense_rows: pd.DataFrame = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    The exact join/rolling-window/scoring sequence __main__ below runs,
    extracted so both the batch backfill and the live weekly job (see
    redzone.py's own module docstring: "Imported by both scripts/
    backfill_redzone.py (batch backfill) and the live weekly job, so
    there is exactly one implementation of this logic to keep in sync")
    can call the identical sequence — this is that shared implementation
    for the ORCHESTRATION ORDER too, not just redzone.py's individual
    add_* functions. Two hand-maintained copies of this multi-step
    sequence drifting apart over time (e.g. someone adds a new add_*
    step to one copy and forgets the other) is exactly the risk this
    closes.

    weekly_stats: UNUSED here as of 2026-10-02 (see add_carries' own
    docstring) — kept as a required parameter so every existing caller's
    own `if weekly_stats is None: weekly_stats = load_weekly_stats(...)`
    resolution keeps working unchanged; `carries` is now sourced from
    aggregate_whole_game_carries(pbp) instead, same real pbp every other
    step here already reads, with no nflverse-player_stats-release
    dependency. Not removed from the signature — changing it would be a
    real, rippling signature change across all three real callers
    (backfill_redzone.py's own __main__, scripts/build_stub_week.py,
    scripts/reconcile_week.py) for a parameter that costs nothing left in
    place unused.

    extra_offense_rows / extra_defense_rows: optional skeleton rows
    (matching aggregate_redzone_game's / aggregate_redzone_allowed's own
    output schema exactly, with the real count columns as NaN) — the
    entire mechanism scripts/build_stub_week.py needs for an upcoming,
    not-yet-played week. Any real rows already present at the SAME
    (season, week) as the stub rows are dropped first (a genuine future
    week has no real rows there yet, so this is a no-op in production;
    it matters for retrospective validation against an already-played
    week, where it prevents a duplicate real+stub pair at that key).
    After that, concatenation is all that's needed — add_rolling_windows'
    shift(1) logic already only ever looks at the PRIOR row in each
    (player_id, season) / (defteam, position_group, season) group, so a
    stub row with a real season/week but NaN counts correctly inherits
    real trailing values from whatever actually-played game preceded it,
    with zero changes needed to add_rolling_windows itself. Every other
    add_* step here is a left join that already NaN-fills gracefully for
    anything not found (a stub row's game_id has no snap_counts entry
    yet, etc.) — the same missing-data philosophy this file already
    relies on for real historical gaps, not a new mechanism.
    """
    weekly = aggregate_redzone_game(pbp, seasonal_rosters)
    if extra_offense_rows is not None:
        stub_keys = extra_offense_rows[["season", "week"]].drop_duplicates()
        weekly = weekly.merge(stub_keys, on=["season", "week"], how="left", indicator=True)
        weekly = weekly[weekly["_merge"] == "left_only"].drop(columns=["_merge"])
        weekly = pd.concat([weekly, extra_offense_rows], ignore_index=True)

    weekly = add_snap_shares(weekly, snap_counts, id_crosswalk)
    # carries/targets: both pbp-derived, whole-game (not red-zone-scoped)
    # metrics merged directly onto `weekly` here so nfl_player_season_
    # evidence (Follow the Story V1, Table 1) has real values to persist —
    # see aggregate_whole_game_carries'/aggregate_whole_game_targets' own
    # docstrings for why each replaces/extends its prior source. Plain
    # left merges, same NaN-on-no-match philosophy every other add_* step
    # here already uses — a player with zero real carries/targets that
    # game (e.g. a pure blocker, or a non-target-receiving lineman
    # scramble) correctly gets NaN, not a fabricated 0.
    carries = aggregate_whole_game_carries(pbp)
    weekly = weekly.merge(carries, on=["player_id", "season", "week"], how="left")
    targets = aggregate_whole_game_targets(pbp)
    weekly = weekly.merge(
        targets[["game_id", "player_id", "season", "week", "targets", "target_share"]],
        on=["game_id", "player_id", "season", "week"], how="left",
    )
    # Must run before add_depth_chart_rank/add_injury_context — both now
    # join on position_group (in addition to player_id/season/week/team)
    # to resolve a player with a genuine multi-position depth-chart
    # listing (e.g. Jackson Meeks, TE AND WR, DET 2026 Week 1) to only
    # the one entry matching their own canonical position, rather than
    # fanning weekly out to one row per listing. Confirmed nothing
    # between add_snap_shares and here reads position_group (grep
    # confirms it's produced solely by add_player_position and consumed
    # first by these two functions), so moving it up is safe.
    weekly = add_player_position(weekly, seasonal_rosters)
    weekly = add_depth_chart_rank(weekly, depth_charts, schedules, seasonal_rosters)
    weekly = add_injury_context(weekly, depth_charts, injuries, schedules, seasonal_rosters)
    weekly = add_opponent(weekly, schedules)
    weekly = add_environment_data(weekly, schedules)

    allowed_weekly = aggregate_redzone_allowed(pbp, seasonal_rosters)
    if extra_defense_rows is not None:
        stub_keys = extra_defense_rows[["season", "week"]].drop_duplicates()
        allowed_weekly = allowed_weekly.merge(stub_keys, on=["season", "week"], how="left", indicator=True)
        allowed_weekly = allowed_weekly[allowed_weekly["_merge"] == "left_only"].drop(columns=["_merge"])
        allowed_weekly = pd.concat([allowed_weekly, extra_defense_rows], ignore_index=True)
    allowed_weekly = add_rolling_windows(
        allowed_weekly,
        metrics=["rz_touches", "rz_tds", "i10_touches", "i10_tds", "gl_touches", "gl_tds"],
        group_cols=["defteam", "position_group", "season"],
    )

    weekly = add_defensive_matchup_context(weekly, allowed_weekly)
    # Must run after add_snap_shares — snap_share has to exist as a column
    # before it can be rolled into snap_share_last1/last3/last5/season_avg.
    #
    # gl_touches/i10_touches added (Stage 1, Masked-Value Handling,
    # dead-column fix): shelves.red_zone_trends_role_signals and shelves.
    # attd_700_plus_role_signals have always called _level_candidate on
    # these two raw_cols expecting real gl_touches_last3/season_avg and
    # i10_touches_last3/season_avg columns -- those never existed on this
    # offense-side table (only the DEFENSE-allowed table above rolled them,
    # via its own explicit metrics= list), so both candidates were dead
    # code, always value=None/_eligible=False, on every real row, every
    # week. This is the one call site producing `weekly` for both the
    # batch backfill and the live weekly job (see this function's own
    # docstring), so the fix lands in both places at once. No new masking
    # logic needed for these two: _level_candidate's existing `value is
    # not None` eligibility already IS the correct games-played gate for
    # a raw-count column (min_periods=1 + shift(1) above means _last3 is
    # real NaN, not a 50.0-style sentinel, for a player with no real prior
    # game -- the same accepted small-sample trade-off _level_candidate's
    # own docstring already documents for this exact pair of metrics).
    weekly = add_rolling_windows(
        weekly, metrics=["rz_touches", "rz_touch_share", "rz_tds", "snap_share", "gl_touches", "i10_touches"],
    )

    weekly = score_td_opportunity(weekly)
    weekly = score_role_momentum(weekly)
    weekly = score_situation(weekly, allowed_weekly)
    # Must run after the three pillars above — a pure meta-layer over
    # their own outputs (completeness columns + final scores), nothing
    # here is computable any earlier in the pipeline.
    weekly = score_evidence_quality(weekly)
    # Independent of score_evidence_quality/signal_convergence — reads
    # only defensive_matchup_vulnerability/td_opportunity/role_momentum,
    # touches no evidence_quality column. Must run after score_situation
    # (defensive_matchup_vulnerability) same as evidence_quality does;
    # order relative to evidence_quality itself doesn't matter, kept
    # after it here so every "meta-layer over the three pillars" step
    # reads top to bottom.
    weekly = score_signal_breach(weekly)
    weekly = score_universal_tpe(weekly)

    return weekly, allowed_weekly


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

    print("Loading snap counts ...")
    snap_counts = load_snap_counts(SEASONS)

    print("Building pfr_id -> gsis player_id crosswalk ...")
    id_crosswalk = load_id_crosswalk(SEASONS)

    print("Loading depth charts ...")
    depth_charts = load_depth_charts(SEASONS)

    print("Loading injury reports ...")
    injuries = load_injuries(SEASONS)

    print("Loading seasonal rosters ...")
    seasonal_rosters = load_seasonal_rosters(SEASONS)

    print("Loading schedules ...")
    schedules = load_schedules(SEASONS)

    print("Loading weekly player stats ...")
    weekly_stats = load_weekly_stats(SEASONS)

    print("Running the shared join/rolling-window/scoring pipeline ...")
    weekly, allowed_weekly = run_pipeline(
        pbp, snap_counts, id_crosswalk, depth_charts, injuries, seasonal_rosters, schedules, weekly_stats,
    )

    unmatched = weekly["snap_share"].isna().sum()
    print(f"  {unmatched} / {len(weekly)} rows had no snap-share match "
          f"({unmatched / len(weekly):.1%})")
    no_rank = weekly["depth_rank"].isna().sum()
    print(f"  {no_rank} / {len(weekly)} rows had no depth-chart rank "
          f"({no_rank / len(weekly):.1%})")
    has_injury_ahead = weekly["ahead_injury_statuses"].apply(len).gt(0).sum()
    print(f"  {has_injury_ahead} / {len(weekly)} rows have >=1 teammate ahead with an injury designation "
          f"({has_injury_ahead / len(weekly):.1%})")
    no_matchup = weekly["allowed_rz_tds_season_avg"].isna().sum()
    print(f"  {no_matchup} / {len(weekly)} rows had no defensive matchup context "
          f"({no_matchup / len(weekly):.1%}) — includes rows with no position_group (mostly QB scrambles)")

    out_path = "player_redzone_weekly.csv"
    weekly.to_csv(out_path, index=False)
    print(f"Wrote {len(weekly)} rows to {out_path}")

    # Example spot check — swap in a known bellcow RB to sanity-check
    # against a public red-zone-touches stat before trusting this table.
    spot_check(weekly, season=2024, player_name_contains="Henry")
