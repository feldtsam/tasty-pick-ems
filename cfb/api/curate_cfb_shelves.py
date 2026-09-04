"""
CFB Shelf Curation — Track B (2026-09): the missing scoring-to-frontend
pipeline. Reads the three real raw ingestion tables back (season-scoped,
signed reads — same real pattern nfl/scripts/reconcile_week.py's
read_player_redzone_weekly_rows/role_defensive_weekly_snapshot already
establish), runs the real CFB scoring chain (cfb/scoring.py, unmodified
math — this module orchestrates and writes, it does not recompute
anything scoring.py already owns), and shapes + writes one row per
scored player-week to the new cfb_player_shelf_scores table.

Deliberately mirrors nfl/api/curate_home_shelves.py's STRUCTURE (read
helpers -> pure orchestration function -> row-shaping -> write helper,
all separate, testable independently) rather than its full SCOPE — CFB
has no shelf taxonomy yet (confirmed 2026-09-04: no CFB_SHELF_META
exists anywhere in tastypickems' frontend) and no Market Value/odds data
source to build ATTD-band shelves against (deferred to v2, spec §6), so
there is no NFL-style multi-shelf assignment, no Tasty Six selection, no
stickiness here. What this module does is the part CFB is actually
missing today: get every real per-pillar score computed and written
somewhere a frontend can read, one row per (player_id, season, week).
Shelf/grouping is left for a later task once CFB's own shelf taxonomy is
designed — this table's `shelf` column exists (nullable) for that to
land in later without a schema change.

THREE REAL GAPS THIS MODULE DEPENDS ON, confirmed 2026-09-04, NOT yet
closed by this task (all explicitly out of scope, flagged in the
investigation report, and require separate work before real data flows
end-to-end):
  1. cfb_player_redzone_weekly / cfb_defense_redzone_allowed_weekly did
     not exist in Supabase at all before this task's migration (live
     404, confirmed directly) — the ONE existing CFB endpoint
     (/api/ingest-and-write-redzone) has never had anywhere real to
     write. This task's migration creates both tables so ingestion has
     somewhere to land, but does not itself run/trigger ingestion.
  2. cfb_player_role_weekly (Role & Momentum's input) has NO deployed
     ingestion endpoint anywhere — cfb/role_momentum.py's build_role_
     momentum_weekly is only ever called by a local, non-deployed
     script (cfb/scripts/role_momentum_sanity.py). This module reads it
     anyway (read_cfb_player_role_weekly, below) via the same signed-
     read pattern as the other two tables; until that ingestion exists,
     the read returns zero rows every time, and score_universal_tpe_cfb's
     per-row core_weights renormalization (cfb/scoring.py) already
     handles that honestly — role_momentum is absent, not neutral-50,
     and core_score renormalizes over the remaining 88 (td_opportunity +
     situation) rather than crashing or silently scoring low.
  3. The Lovable-side read/write routes this module's URLs point at
     (LOVABLE_CFB_*_READ_URL / LOVABLE_CFB_PLAYER_SHELF_SCORES_WRITE_URL)
     do not exist yet — same category of gap NFL's own curate_home_
     shelves.py already has for stickiness ("no NFL content-drafts read
     endpoint exists either... cannot check at all from this
     environment"). Out of this task's repo scope (feldtsam/tasty-
     pick-ems only) — building them is real, separate work on the
     tastypickems side.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from lovable_forward import forward_to_lovable, resolve_url_env
from scoring import (
    CONFIG,
    drop_non_fbs_opponent_rows,
    score_defensive_matchup_cfb,
    score_evidence_quality_cfb,
    score_role_momentum_cfb,
    score_td_opportunity_cfb,
    score_universal_tpe_cfb,
)

# ---------------------------------------------------------------------------
# Real typed-column shapes, confirmed directly against cfb/redzone.py's and
# cfb/role_momentum.py's actual row-dict construction (2026-09-04
# investigation) -- NOT assumed from either module's prose docstring alone.
# ---------------------------------------------------------------------------
CFB_PLAYER_REDZONE_WEEKLY_TYPED_COLUMNS = [
    "player_id", "season", "week", "game_id", "team_id", "team",
    "opponent_team_id", "opponent", "player_name", "position_group",
    "rz_touches", "rz_rush_touches", "rz_target_touches", "rz_tds",
    "i10_touches", "i10_rush_touches", "i10_target_touches", "i10_tds",
    "gl_touches", "gl_rush_touches", "gl_target_touches", "gl_tds",
    "team_rz_touches", "rz_touch_share",
]

CFB_DEFENSE_REDZONE_ALLOWED_WEEKLY_TYPED_COLUMNS = [
    "team_id", "team", "position_group", "season", "week", "game_id",
    "opponent_team_id", "opponent",
    "rz_touches_allowed", "rz_rush_touches_allowed", "rz_target_touches_allowed", "rz_tds_allowed",
    "i10_touches_allowed", "i10_rush_touches_allowed", "i10_target_touches_allowed", "i10_tds_allowed",
    "gl_touches_allowed", "gl_rush_touches_allowed", "gl_target_touches_allowed", "gl_tds_allowed",
]

CFB_PLAYER_ROLE_WEEKLY_TYPED_COLUMNS = [
    "player_id", "player_name", "position_group", "team_id", "team",
    "opponent_team_id", "opponent", "season", "week", "game_id",
    "touches", "team_touches", "touch_share", "ppa", "is_returning",
]


# ---------------------------------------------------------------------------
# Signed reads -- same real forward_to_lovable-as-read reuse NFL's own
# read_player_redzone_weekly_rows / read_shelf_signal_history already
# establish (this codebase's one generic sign+POST mechanism, used for
# reads and writes alike; see cfb/api/lovable_forward.py's own module
# docstring). Season-scoped, not week-scoped -- scoring needs rolling
# windows/cumulative totals across the WHOLE season to correctly score
# the target week, same reasoning as NFL's own season-scoped read.
# ---------------------------------------------------------------------------
DEFAULT_CFB_PLAYER_REDZONE_WEEKLY_READ_URL = "https://tastypickems.com/api/public/cfb-player-redzone-weekly-read"
DEFAULT_CFB_DEFENSE_REDZONE_ALLOWED_WEEKLY_READ_URL = "https://tastypickems.com/api/public/cfb-defense-redzone-allowed-weekly-read"
DEFAULT_CFB_PLAYER_ROLE_WEEKLY_READ_URL = "https://tastypickems.com/api/public/cfb-player-role-weekly-read"
DEFAULT_CFB_PLAYER_SHELF_SCORES_WRITE_URL = "https://tastypickems.com/api/public/cfb-player-shelf-scores-write"


def _read_rows(season: int, secret: str, env_name: str, default_url: str, read_url: str, response_key: str) -> dict:
    """
    Shared body for the three read_cfb_*_rows functions below -- one
    signed POST (body {"season": season}), same response contract every
    other read route in this codebase already uses: {"ok": bool, "error":
    str|None, "status_code": int|None, "rows": [...]}. A real "zero rows"
    response (nothing ingested for this season yet) is a genuine, valid
    outcome, not an error -- same convention as NFL's read routes.
    """
    url = read_url or resolve_url_env(env_name, default_url)
    result = forward_to_lovable({"season": season}, secret, url)
    if not result["success"]:
        return {"ok": False, "error": result["error"], "status_code": result["status_code"], "rows": []}
    try:
        body = json.loads(result["response_body"])
    except (json.JSONDecodeError, TypeError):
        return {
            "ok": False, "error": f"Non-JSON response body: {result['response_body']!r}",
            "status_code": result["status_code"], "rows": [],
        }
    if not body.get("ok"):
        return {
            "ok": False, "error": body.get("error", "Unknown error"),
            "status_code": result["status_code"], "rows": [],
        }
    return {"ok": True, "error": None, "status_code": result["status_code"], "rows": body.get(response_key, [])}


def read_cfb_player_redzone_weekly_rows(season: int, secret: str, read_url: str = None) -> dict:
    """Whole-season cfb_player_redzone_weekly rows. See _read_rows."""
    return _read_rows(
        season, secret, "LOVABLE_CFB_PLAYER_REDZONE_WEEKLY_READ_URL",
        DEFAULT_CFB_PLAYER_REDZONE_WEEKLY_READ_URL, read_url, "player_redzone_weekly",
    )


def read_cfb_defense_redzone_allowed_weekly_rows(season: int, secret: str, read_url: str = None) -> dict:
    """Whole-season cfb_defense_redzone_allowed_weekly rows. See _read_rows."""
    return _read_rows(
        season, secret, "LOVABLE_CFB_DEFENSE_REDZONE_ALLOWED_WEEKLY_READ_URL",
        DEFAULT_CFB_DEFENSE_REDZONE_ALLOWED_WEEKLY_READ_URL, read_url, "defense_redzone_allowed_weekly",
    )


def read_cfb_player_role_weekly_rows(season: int, secret: str, read_url: str = None) -> dict:
    """
    Whole-season cfb_player_role_weekly rows. See _read_rows.

    EXPECTED TO RETURN ZERO ROWS TODAY, always -- no deployed endpoint
    writes this table yet (confirmed 2026-09-04: cfb/role_momentum.py has
    no Lovable/HMAC forwarding of any kind; only a local sanity script
    calls build_role_momentum_weekly). This is not a bug in this
    function; it is the honest current state of Role & Momentum's
    ingestion, and curate_cfb_shelves() below is built to degrade
    correctly around it (see this module's own docstring, gap 2).
    """
    return _read_rows(
        season, secret, "LOVABLE_CFB_PLAYER_ROLE_WEEKLY_READ_URL",
        DEFAULT_CFB_PLAYER_ROLE_WEEKLY_READ_URL, read_url, "player_role_weekly",
    )


def _snapshot(rows: list, typed_columns: list) -> pd.DataFrame:
    """A genuinely empty read returns a correctly-shaped, zero-row
    DataFrame with every typed column present -- every downstream
    scoring function already degrades correctly against an empty/short
    frame, same honest-degradation shape as NFL's own read wrappers."""
    if not rows:
        return pd.DataFrame(columns=typed_columns)
    return pd.DataFrame([{col: row.get(col) for col in typed_columns} for row in rows]).reset_index(drop=True)


def cfb_player_redzone_weekly_snapshot(season: int, secret: str, read_url: str = None) -> pd.DataFrame:
    result = read_cfb_player_redzone_weekly_rows(season, secret, read_url)
    return _snapshot(result["rows"], CFB_PLAYER_REDZONE_WEEKLY_TYPED_COLUMNS)


def cfb_defense_redzone_allowed_weekly_snapshot(season: int, secret: str, read_url: str = None) -> pd.DataFrame:
    result = read_cfb_defense_redzone_allowed_weekly_rows(season, secret, read_url)
    return _snapshot(result["rows"], CFB_DEFENSE_REDZONE_ALLOWED_WEEKLY_TYPED_COLUMNS)


def cfb_player_role_weekly_snapshot(season: int, secret: str, read_url: str = None) -> pd.DataFrame:
    result = read_cfb_player_role_weekly_rows(season, secret, read_url)
    return _snapshot(result["rows"], CFB_PLAYER_ROLE_WEEKLY_TYPED_COLUMNS)


# ---------------------------------------------------------------------------
# Orchestration -- pure DataFrame-in / dict-out, no I/O. Mirrors nfl/api/
# curate_home_shelves.py's own split (read helpers / pure scoring-and-
# shaping / write helpers, independently testable) rather than its shelf-
# assignment logic, which CFB has no taxonomy for yet -- see module
# docstring.
# ---------------------------------------------------------------------------
CFB_SHELF_SCORE_COLUMNS = [
    "player_id", "player_name", "season", "week", "game_id",
    "team_id", "team", "opponent_team_id", "opponent", "position_group",
    "td_opportunity", "td_opportunity_completeness", "td_opportunity_gated",
    "defensive_matchup_vulnerability", "defensive_matchup_completeness",
    "situation", "situation_completeness",
    "role_momentum", "role_momentum_completeness",
    "evidence_completeness", "evidence_convergence", "evidence_quality",
    "core_score", "confidence_multiplier", "tpe_score",
]


def curate_cfb_shelves(
    player_weekly: pd.DataFrame,
    allowed_weekly: pd.DataFrame,
    role_weekly: pd.DataFrame,
    season: int,
    week: int,
    fbs_ids: frozenset[int],
    config: dict = CONFIG,
) -> dict:
    """
    The real scoring chain, steps 1-6, for one (season, week): FBS-
    opponent filter -> TD Opportunity -> Situation (defensive matchup) ->
    Role & Momentum (merged in from its own separately-keyed table) ->
    Evidence Quality -> Universal TPE Score. Every score_*_cfb call here
    is cfb/scoring.py's real, unmodified function -- this function calls,
    it does not recompute.

    `player_weekly`/`allowed_weekly`: a WHOLE SEASON's real rows from
    cfb_player_redzone_weekly / cfb_defense_redzone_allowed_weekly (see
    the *_snapshot functions above) -- rolling windows/cumulative totals
    are computed fresh here over the full season, same reasoning as
    NFL's own season-scoped read (a single week's rows alone can't
    reproduce a player's trend).

    `role_weekly`: a whole season's real rows from cfb_player_role_weekly
    -- genuinely empty today (see module docstring, gap 2); every step
    below already degrades correctly around that.

    `fbs_ids`: this season's real FBS team ids (cfb.ids.fbs_team_ids) --
    threaded through as a parameter, not fetched here, so a caller that
    already has it this run (e.g. from ingest-and-write-redzone in the
    same request) never re-fetches it, and tests can pass a synthetic set
    with no real CFBD call at all.

    Returns {"scored": DataFrame (whole season, every real column),
    "week_rows": DataFrame (just `week`'s rows), "shelf_score_rows":
    list[dict] (week_rows shaped for cfb_player_shelf_scores, see
    shape_cfb_shelf_score_rows)}.
    """
    player_weekly = drop_non_fbs_opponent_rows(player_weekly, fbs_ids)
    allowed_weekly = drop_non_fbs_opponent_rows(allowed_weekly, fbs_ids) if len(allowed_weekly) else allowed_weekly

    scored = score_td_opportunity_cfb(player_weekly, config)
    scored = score_defensive_matchup_cfb(scored, allowed_weekly, config)

    if len(role_weekly):
        role_scored = score_role_momentum_cfb(role_weekly, config)
        role_cols = role_scored[["player_id", "season", "week", "role_momentum", "role_momentum_completeness"]]
        scored = scored.drop(columns=["role_momentum", "role_momentum_completeness"], errors="ignore")
        scored = scored.merge(role_cols, on=["player_id", "season", "week"], how="left")
    else:
        # Genuinely no Role & Momentum data exists yet (gap 2) -- honest
        # absent columns, not a fabricated neutral-50. score_universal_
        # tpe_cfg's present-columns renormalization (cfb/scoring.py)
        # already handles a wholly-absent pillar column correctly.
        # float("nan"), NOT pd.NA -- keeps the column real float64 dtype
        # so downstream .mean()/np.sqrt() (score_evidence_quality_cfb)
        # operate on floats, not a pd.NA-tainted object-dtype column.
        scored["role_momentum"] = float("nan")
        scored["role_momentum_completeness"] = float("nan")

    scored = score_evidence_quality_cfb(scored, config)
    scored = score_universal_tpe_cfb(scored, config)

    week_rows = scored[(scored["season"] == season) & (scored["week"] == week)].copy()
    shelf_score_rows = shape_cfb_shelf_score_rows(week_rows)

    return {"scored": scored, "week_rows": week_rows, "shelf_score_rows": shelf_score_rows}


def shape_cfb_shelf_score_rows(week_rows: pd.DataFrame) -> list:
    """
    Splits each real scored row into cfb_player_shelf_scores' shape: the
    typed columns (CFB_SHELF_SCORE_COLUMNS) top-level, everything else
    folded into `extra` -- same narrow-core-plus-jsonb-tail pattern
    nfl_player_redzone_weekly/nfl_intelligence_stories already use. Uses
    the to_json()-round-trip JSON-safety idiom nfl/scripts/reconcile_week.
    py's shape_player_redzone_weekly_rows already established (numpy
    int64/float64/NaN/pd.NA survive a naive .to_dict("records") call
    untouched; to_json()'s own encoder handles them correctly).
    """
    if len(week_rows) == 0:
        return []
    records = json.loads(week_rows.to_json(orient="records"))
    rows = []
    for record in records:
        typed = {col: record.get(col) for col in CFB_SHELF_SCORE_COLUMNS}
        typed["extra"] = {k: v for k, v in record.items() if k not in CFB_SHELF_SCORE_COLUMNS}
        rows.append(typed)
    return rows


def write_cfb_player_shelf_scores(rows: list, secret: str, write_url: str = None) -> dict:
    """Same real signed-POST mechanism every other CFB/NFL webhook write
    already uses (see forward_to_lovable). `write_url`, if not passed
    explicitly, resolves from LOVABLE_CFB_PLAYER_SHELF_SCORES_WRITE_URL."""
    url = write_url or resolve_url_env(
        "LOVABLE_CFB_PLAYER_SHELF_SCORES_WRITE_URL", DEFAULT_CFB_PLAYER_SHELF_SCORES_WRITE_URL,
    )
    return forward_to_lovable(rows, secret, url)
