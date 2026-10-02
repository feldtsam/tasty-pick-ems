"""
Table 1 (nfl_player_season_evidence) backfill for already-reconciled past
weeks -- weeks 1/2 of 2026 specifically, reconciled before Table 1
existed, so never written there. Deliberately NOT reconcile_week(): that
function does three things this backfill must never repeat (see each
function's own docstring below for the real reasoning) -- the market-
value join against CURRENT/live odds, rewriting nfl_player_redzone_weekly
and its `extra`, and mark_stub_week_reconciled(). This module calls
run_pipeline() directly and writes ONLY to nfl_player_season_evidence,
via the exact same pure shaping functions reconcile_week.py itself
already uses (shape_player_redzone_weekly_rows, shape_player_season_
evidence_rows) -- reused, not reimplemented.

Nothing in this module is wired into any endpoint, schedule, or write
path yet. See this project's own plan for the deliberately smallest-
safe-first version this belongs to.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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
from player_season_history import reconciled_weeks_from_redzone_weekly_rows
from reconcile_week import (
    read_player_redzone_weekly_rows,
    shape_player_redzone_weekly_rows,
    shape_player_season_evidence_rows,
    write_player_season_evidence_rows,
)

MAX_WEEKS_PER_CALL = 3


def validate_backfill_request_shape(season, weeks) -> str | None:
    """
    Structural/input validation only -- never looks at real reconciled
    data. Returns a real, specific error message, or None when the
    request shape itself is valid. Checked BEFORE any real data read, so
    a malformed request never costs a real network call to find out.

    isinstance(w, bool) is checked explicitly and rejected even though
    bool is a real subclass of int in Python (True/False would otherwise
    silently pass `isinstance(w, int)`) -- a real, if unlikely, way a
    malformed JSON body could sneak a non-week value past this check.
    """
    if not isinstance(season, int) or isinstance(season, bool):
        return "season must be an integer"
    if not isinstance(weeks, list) or not weeks:
        return "weeks must be a non-empty list"
    if len(weeks) > MAX_WEEKS_PER_CALL:
        return f"at most {MAX_WEEKS_PER_CALL} weeks per call (got {len(weeks)})"
    if len(set(weeks)) != len(weeks):
        return "weeks must not contain duplicates"
    for w in weeks:
        if not isinstance(w, int) or isinstance(w, bool):
            return f"every week must be an integer (got {w!r})"
        if w < 1:
            return f"every week must be >= 1 (got {w})"
    return None


def validate_backfill_weeks(weeks: list, reconciled_weeks_present: list) -> dict:
    """
    Content validation: which of the REQUESTED weeks are real, already-
    reconciled, strictly-past weeks this backfill is allowed to touch.

    reconciled_weeks_present: the real distinct weeks already in
    nfl_player_redzone_weekly for this season (from reconcile_week.
    read_player_redzone_weekly_rows()'s own real rows, reduced via
    player_season_history.reconciled_weeks_from_redzone_weekly_rows() --
    the SAME already-published, already-live read route this project's
    own history-package work already established as the reliable source
    for "which weeks were really reconciled," confirmed more reliable
    than nfl_stub_weeks.reconciled -- see that function's own docstring).

    A requested week is accepted only if it's BOTH present in that real
    set AND strictly earlier than its max (the latest reconciled week
    itself is never backfill-eligible -- it's the live edge of the
    season, not settled history; a week this backfill has never heard
    of at all is rejected for the same reason reconcile_week() itself
    raises on zero real rows -- there's nothing real to compute from).

    Returns {"accepted": [...], "rejected": {week: reason, ...}} --
    every requested week lands in exactly one of the two, nothing silently
    dropped.
    """
    if not reconciled_weeks_present:
        return {"accepted": [], "rejected": {w: "no reconciled weeks exist for this season at all" for w in weeks}}

    latest = max(reconciled_weeks_present)
    present = set(reconciled_weeks_present)
    accepted, rejected = [], {}
    for w in weeks:
        if w not in present:
            rejected[w] = f"week {w} is not present in nfl_player_redzone_weekly for this season"
        elif w == latest:
            rejected[w] = (
                f"week {w} is the latest reconciled week ({latest}) -- only strictly earlier "
                f"weeks are backfill-eligible"
            )
        else:
            accepted.append(w)
    return {"accepted": accepted, "rejected": rejected}


def backfill_season_evidence_rows(season: int, weeks: list, secret: str, write_url: str = None) -> dict:
    """
    The real computation, ASSUMING weeks has already passed both
    validate_backfill_request_shape and validate_backfill_weeks (this
    function itself does not re-validate -- the caller, e.g. the future
    endpoint, owns that gate; this is deliberately a narrow, single-
    purpose function, not a re-implementation of both guards inline).

    Loads the STANDARD multi-season SEASONS range (sorted(set(SEASONS) |
    {season})), exactly what reconcile_week() itself always loads --
    NEVER [season] alone. Confirmed, not assumed: a single-season-only
    depth-chart load trips a real, separate, pre-existing bug in
    redzone._new_schema_depth_chart (KeyError: 'dt') -- see test_run_
    pipeline_integration.py's own comment on this exact workaround,
    already validated earlier in this project's history. weekly_stats is
    passed as an empty DataFrame, not loaded for real -- it's dead,
    unused inside run_pipeline() since the pbp-derived carries fix (see
    that function's own docstring), so loading it for real here would
    only cost a real, pointless network round-trip to nflverse's
    player_stats release.

    Hard-filters run_pipeline()'s own full `weekly` output to EXACTLY
    `season` and `week.isin(weeks)` BEFORE any shaping -- the one real
    guard against ever touching a week outside what was explicitly
    requested (see this module's own docstring on why that matters for
    week 3 specifically).

    Calls ONLY shape_player_redzone_weekly_rows() and shape_player_
    season_evidence_rows() (both pure, reused verbatim from reconcile_
    week.py) and write_player_season_evidence_rows() (the one real
    write). Never calls reconcile_week() itself, write_player_redzone_
    weekly_rows(), mark_stub_week_reconciled(), or anything market-value
    related -- none of those are even imported into this module.

    Raises (never returns a success-shaped result) if len(reconciled) ==
    0 for the requested weeks, or if the real write fails -- this
    backfill's entire reason to exist IS the Table 1 write, so a failed
    write must be loud, the opposite of reconcile_week()'s own
    deliberately-non-fatal treatment of that same write (there, Table 1
    is a bonus; here, it's the only point).

    Returns {"season": season, "weeks_requested": weeks, "rows_written":
    int, "rows_written_by_week": {week: int, ...}}.
    """
    load_seasons = sorted(set(SEASONS) | {season})

    pbp = load_pbp(load_seasons)
    snap_counts = load_snap_counts(load_seasons)
    id_crosswalk = load_id_crosswalk(load_seasons)
    depth_charts = load_depth_charts(load_seasons)
    injuries = load_injuries(load_seasons)
    seasonal_rosters = load_seasonal_rosters(load_seasons)
    schedules = load_schedules(load_seasons)

    weekly, _allowed_weekly = run_pipeline(
        pbp, snap_counts, id_crosswalk, depth_charts, injuries, seasonal_rosters, schedules, pd.DataFrame(),
    )

    reconciled = weekly[(weekly["season"] == season) & (weekly["week"].isin(weeks))].copy()
    if len(reconciled) == 0:
        raise ValueError(
            f"No real play-by-play rows for season={season} weeks={weeks} -- either the games "
            f"haven't been played, or no RB/WR/TE recorded a real red-zone touch in any requested week."
        )

    redzone_rows = shape_player_redzone_weekly_rows(reconciled)
    season_evidence_rows = shape_player_season_evidence_rows(redzone_rows)

    result = write_player_season_evidence_rows(season_evidence_rows, secret, write_url)
    if not result["success"]:
        raise RuntimeError(
            f"Backfill write failed for season={season} weeks={weeks}: "
            f"status={result['status_code']} error={result['error']!r}"
        )

    rows_written_by_week: dict = {}
    for row in season_evidence_rows:
        rows_written_by_week[row["period_index"]] = rows_written_by_week.get(row["period_index"], 0) + 1

    return {
        "season": season,
        "weeks_requested": weeks,
        "rows_written": len(season_evidence_rows),
        "rows_written_by_week": rows_written_by_week,
    }


def reconciled_weeks_present_for_season(season: int, secret: str, read_url: str = None) -> list:
    """
    The real read the week guard needs: distinct weeks already present in
    nfl_player_redzone_weekly for this season, via the ALREADY-PUBLISHED
    read route (reconcile_week.read_player_redzone_weekly_rows) -- NOT
    the whole-season Table 1 read (nfl-player-season-evidence-read.ts's
    own player_id-optional path is built but not yet deployed, so using
    it here would depend on something not actually live).
    """
    result = read_player_redzone_weekly_rows(season, secret, read_url)
    if not result["ok"]:
        raise RuntimeError(f"Could not read nfl_player_redzone_weekly for season={season}: {result['error']!r}")
    return reconciled_weeks_from_redzone_weekly_rows(result["rows"])
