"""
CFB Shelf Curation — Track B (2026-09): the missing scoring-to-frontend
pipeline. Reads the three real raw ingestion tables back (season-scoped,
signed reads — same real pattern nfl/scripts/reconcile_week.py's
read_player_redzone_weekly_rows/role_defensive_weekly_snapshot already
establish), runs the real CFB scoring chain (cfb/scoring.py, unmodified
math — this module orchestrates and writes, it does not recompute
anything scoring.py already owns), and shapes + writes scored rows to
the cfb_player_shelf_scores table.

UPDATED (Phase 5 + this task) — the paragraph below is the module's
ORIGINAL framing and is now partly stale: CFB DOES have a real shelf
taxonomy as of Phase 5 (the 8-shelf section at the end of this file --
assign_cfb_shelves / add_shelf_convergence / select_cfb_tasty_six), and
this task wires story_archetype.resolve_cfb_archetype() into a NEW
per-shelf-placement write path (shape_cfb_shelf_placement_rows, below)
that is now what the live endpoint actually forwards to
cfb_player_shelf_scores -- see that function's own docstring. shape_cfb_
shelf_score_rows (the ORIGINAL one-row-per-player-week shaping this
paragraph describes) is kept, unmodified, for the whole-population raw
scored snapshot it always was -- it is a different, still-valid output,
just no longer the one the live endpoint writes to this table. Left the
rest of this historical paragraph as originally written below (Market
Value/odds are still genuinely deferred to v2, unaffected by Phase 5 or
this task):

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
    score_target_magnets_cfb,
    score_td_opportunity_cfb,
    score_universal_tpe_cfb,
)
from story_archetype import resolve_cfb_archetype

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

# Phase 5 — confirmed directly against cfb/redzone.py::
# aggregate_receiving_game_cfb's real row-dict construction (same
# discipline as the three column lists above).
CFB_PLAYER_RECEIVING_WEEKLY_TYPED_COLUMNS = [
    "player_id", "player_name", "position_group", "team_id", "team",
    "opponent_team_id", "opponent", "season", "week", "game_id",
    "targets", "receptions", "team_targets", "target_share",
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
DEFAULT_CFB_PLAYER_RECEIVING_WEEKLY_READ_URL = "https://tastypickems.com/api/public/cfb-player-receiving-weekly-read"
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


def read_cfb_player_receiving_weekly_rows(season: int, secret: str, read_url: str = None) -> dict:
    """
    Whole-season cfb_player_receiving_weekly rows. See _read_rows.

    Same honest-gap posture as read_cfb_player_role_weekly_rows: no
    deployed ingestion endpoint writes this table yet either (Phase 5
    built the aggregation function — aggregate_receiving_game_cfb — and
    this read wrapper, not a live write path). Returns zero rows today,
    which curate_cfb_shelves() below degrades around exactly the way it
    already does for role_weekly (gap 2).
    """
    return _read_rows(
        season, secret, "LOVABLE_CFB_PLAYER_RECEIVING_WEEKLY_READ_URL",
        DEFAULT_CFB_PLAYER_RECEIVING_WEEKLY_READ_URL, read_url, "player_receiving_weekly",
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


def cfb_player_receiving_weekly_snapshot(season: int, secret: str, read_url: str = None) -> pd.DataFrame:
    result = read_cfb_player_receiving_weekly_rows(season, secret, read_url)
    return _snapshot(result["rows"], CFB_PLAYER_RECEIVING_WEEKLY_TYPED_COLUMNS)


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
    # `shelf` was always in the live DB schema (original migration's own
    # comment: "reserved so a future shelf-assignment task can populate
    # it") but never in this list, since shape_cfb_shelf_score_rows'
    # per-player rows have no single real shelf value to put here. Now
    # genuinely populated -- but only by shape_cfb_shelf_placement_rows
    # (below), one real shelf name per row. shape_cfb_shelf_score_rows'
    # own rows still read this back as None (week_rows has no "shelf"
    # column), which is correct -- an unfiltered whole-population row
    # was never ON any one shelf.
    "shelf",
    "td_opportunity", "td_opportunity_completeness", "td_opportunity_gated",
    "defensive_matchup_vulnerability", "defensive_matchup_completeness",
    "situation", "situation_completeness",
    "role_momentum", "role_momentum_completeness",
    # Phase 5 — standalone shelf-ranking signal, NOT a Universal TPE input
    # (see score_target_magnets_cfb's own docstring). Carried on the same
    # per-player-week row as every other pillar for one shared read/write
    # shape, exactly like role_momentum's own columns above.
    "target_magnets", "target_magnets_completeness", "target_magnets_gated",
    "evidence_completeness", "evidence_convergence", "evidence_quality",
    "core_score", "confidence_multiplier", "tpe_score",
    # Phase 5 -- shelf-membership convergence tracking (see
    # add_shelf_convergence). Only present on week_rows once that function
    # has run; shape_cfb_shelf_score_rows' own column-existence guard
    # (record.get(col)) means calling it BEFORE add_shelf_convergence
    # simply omits these two (None), not a KeyError.
    "shelves", "shelf_count",
    # Story Archetype Resolver wiring (this task) -- story_archetype.
    # resolve_cfb_archetype()'s own output, resolved PER SHELF PLACEMENT
    # by shape_cfb_shelf_placement_rows (below), never by shape_cfb_
    # shelf_score_rows (a per-player row with no single shelf has no one
    # correct archetype to resolve -- reads back None here, same as
    # `shelf` above, for the same reason).
    "archetype",
]


def curate_cfb_shelves(
    player_weekly: pd.DataFrame,
    allowed_weekly: pd.DataFrame,
    role_weekly: pd.DataFrame,
    season: int,
    week: int,
    fbs_ids: frozenset[int],
    config: dict = CONFIG,
    receiving_weekly: pd.DataFrame = None,
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

    `receiving_weekly` (Phase 5, optional): a whole season's real rows
    from cfb_player_receiving_weekly (cfb/redzone.py::
    aggregate_receiving_game_cfb). Same merge-in-by-key pattern as
    role_weekly, same honest-absent-column degradation when omitted or
    empty (no deployed ingestion endpoint for this table yet either --
    see read_cfb_player_receiving_weekly_rows). target_magnets is NOT a
    Universal TPE input (see score_target_magnets_cfb), so unlike role_
    momentum its absence never affects core_score's renormalization --
    it just means the Target Magnets shelf has nothing to rank by yet.

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

    if receiving_weekly is not None and len(receiving_weekly):
        tm_scored = score_target_magnets_cfb(receiving_weekly, config)
        tm_cols = tm_scored[
            ["player_id", "season", "week", "target_magnets", "target_magnets_completeness", "target_magnets_gated"]
        ]
        scored = scored.drop(
            columns=["target_magnets", "target_magnets_completeness", "target_magnets_gated"], errors="ignore"
        )
        scored = scored.merge(tm_cols, on=["player_id", "season", "week"], how="left")
    else:
        scored["target_magnets"] = float("nan")
        scored["target_magnets_completeness"] = float("nan")
        # True (not pd.NA) -- "no receiving data at all for this player-
        # week" is structurally the same as "gated": no trustworthy
        # signal, exclude from the Target Magnets shelf. Also avoids a
        # real crash a nullable pd.NA would cause the first time
        # assign_cfb_shelves() below does `== False` as a boolean mask
        # (pandas raises on NA-containing boolean masks).
        scored["target_magnets_gated"] = True

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


# ---------------------------------------------------------------------------
# Phase 5 (2026-09) -- 8-shelf CFB Picks curation. CFB's first real shelf
# taxonomy: this module's own header docstring, written 2026-09-04, said
# "CFB has no shelf taxonomy yet ... no NFL-style multi-shelf assignment,
# no Tasty Six selection" -- this section is exactly that, backend only
# (no frontend/UI work in this pass, per spec).
# ---------------------------------------------------------------------------

PLAYER_BEHAVIOR_SHELVES = ("goal_line_favorites", "workhorses", "target_magnets")

# Exact CFBD conference spelling (confirmed against cfb/scripts/
# fbs_teams_2026.json and a live /rankings response) -- "Big 12"/"Big Ten"
# with the space, never a squashed/abbreviated form.
CONFERENCE_TD_WATCH_SHELVES = {
    "sec_td_watch": "SEC",
    "big_ten_td_watch": "Big Ten",
    "big12_td_watch": "Big 12",
    "acc_td_watch": "ACC",
}

CFB_WORLD_SHELVES = ("top25_td_watch",) + tuple(CONFERENCE_TD_WATCH_SHELVES)

# The 8 real shelves, in display order -- also Tasty Six's own walk order
# (select_cfb_tasty_six). Tasty Six itself is NOT one of these 8 -- it's a
# derived showcase drawn FROM them (spec: "8-shelf curation logic" lists 9
# names including Tasty Six, but frames Tasty Six as "the top pick from a
# subset of these 8 shelves" -- it is the +1, not a 9th independent
# eligibility population, and is excluded from shelf_count/"N SHELVES
# AGREE" for the same reason).
CFB_SHELF_ORDER = PLAYER_BEHAVIOR_SHELVES + CFB_WORLD_SHELVES

# Confirmed (2026-09-13): 15, uniformly across all 8 shelves -- was 6
# (an earlier flagged assumption, extending the 5 CFB-world shelves' own
# originally-scoped cap to the 3 player-behavior shelves too). Same
# uniform-across-all-8 application either way; only the number changed.
# select_cfb_tasty_six's own `n=6` default is a SEPARATE literal (how
# many final picks the showcase itself surfaces) and does not read this
# constant -- raising the per-shelf pool size doesn't change Tasty Six's
# own output count.
SHELF_SIZE = 15


def _top_n(df: pd.DataFrame, sort_col: str, n: int) -> pd.DataFrame:
    return df.sort_values(sort_col, ascending=False).head(n)


def assign_cfb_shelves(
    week_rows: pd.DataFrame,
    ap_ranks: dict,
    team_conference: dict,
    shelf_size: int = SHELF_SIZE,
) -> dict:
    """
    Build the 8 real CFB Picks shelves for one already-scored week
    (curate_cfb_shelves()'s own `week_rows` output). Pure function, no
    I/O -- `ap_ranks` (cfb.ids.fetch_ap_top25) / `team_conference`
    (cfb.ids.team_conference_map) are pre-fetched by the caller, so this
    stays synchronous and testable with a synthetic dict, no real CFBD
    call.

    NO DEDUP ACROSS SHELVES (explicit spec decision, unlike MLB/NFL): a
    player can legitimately appear on multiple shelves -- each shelf's
    DataFrame is built independently; nothing here filters a player out
    for already appearing on an earlier one. Do not add
    _resolve_player_conflicts/_dedupe_by_player-style logic to this
    function.

    Player-behavior shelves (rank by the player's own signal, never
    composite tpe_score):
      * goal_line_favorites -- td_opportunity, excluding
        td_opportunity_gated rows (a gated row is forced to exactly
        neutral 50 by construction -- score_td_opportunity_cfb -- so
        including it would rank real scores against fake-neutral ones on
        the same scale).
      * workhorses -- role_momentum, excluding rows with
        role_momentum_completeness == 0. role_momentum_cfb has no
        explicit _gated boolean the way td_opportunity does; completeness
        == 0 is the honest proxy -- that value is only possible when both
        of its trend inputs were NaN (thin history), the same real
        condition td_opportunity's own gate tests for.
      * target_magnets -- target_magnets (this same Phase 5 build),
        excluding target_magnets_gated rows -- that flag was modeled
        directly on td_opportunity_gated for exactly this symmetry.

    CFB-world shelves (an eligibility POPULATION, then ranked by the real
    composite tpe_score within it -- never a player-behavior signal):
      * top25_td_watch -- team_id present in ap_ranks (this week's real
        AP Top 25; poll name "AP Top 25" specifically, never "Coaches
        Poll" -- resolved by the caller's fetch_ap_top25 call, not here).
      * {conf}_td_watch -- team_conference[team_id] equals the real
        conference string (CONFERENCE_TD_WATCH_SHELVES' exact spelling).

    REAL GAP, NOT SILENTLY SUBSTITUTED: spec asked for a second
    eligibility leg on the 4 conference shelves -- "player meets the
    existing ATTD odds-floor eligibility rule already used elsewhere in
    CFB scoring." Investigated directly before writing this (grepped cfb/
    for odds_floor/min_odds/ATTD/qualifying_odds): no such rule exists
    anywhere. CFB has no Market Value/odds data source at all yet (see
    this module's own header docstring and cfb/scoring.py's -- both
    explicit that it's deferred to v2). Rather than invent an unrelated
    stand-in gate that would LOOK like it's doing the referenced job while
    actually checking something else, this function applies ONLY the
    conference/AP-rank membership test on these 4-5 shelves -- the odds
    leg is simply not applied, flagged here and in the build report. Flag
    back if a different interim gate (e.g. a minimum-sample floor) is
    wanted instead.

    Returns {shelf_name: DataFrame}, one entry per CFB_SHELF_ORDER name,
    each already sorted best-first and capped at `shelf_size` rows -- a
    thin week can legitimately return fewer, never backfilled.
    """
    shelves: dict = {}

    glf = week_rows[week_rows["td_opportunity_gated"] == False]  # noqa: E712 -- explicit bool column
    shelves["goal_line_favorites"] = _top_n(glf, "td_opportunity", shelf_size)

    wh = week_rows[week_rows["role_momentum_completeness"].fillna(0) > 0]
    shelves["workhorses"] = _top_n(wh, "role_momentum", shelf_size)

    tm = week_rows[week_rows["target_magnets_gated"] == False]  # noqa: E712
    shelves["target_magnets"] = _top_n(tm, "target_magnets", shelf_size)

    top25 = week_rows[week_rows["team_id"].isin(ap_ranks.keys())]
    shelves["top25_td_watch"] = _top_n(top25, "tpe_score", shelf_size)

    conference = week_rows["team_id"].map(team_conference)
    for shelf_name, conf_name in CONFERENCE_TD_WATCH_SHELVES.items():
        pool = week_rows[conference == conf_name]
        shelves[shelf_name] = _top_n(pool, "tpe_score", shelf_size)

    return shelves


def add_shelf_convergence(week_rows: pd.DataFrame, shelves: dict) -> pd.DataFrame:
    """
    Attach `shelves` (list[str]) and `shelf_count` (int) to every row in
    week_rows -- "N SHELVES AGREE" is exactly this: how many of the 8 real
    shelves (CFB_SHELF_ORDER; Tasty Six itself is excluded, see that
    constant's own comment) a player's row landed on, and which ones.
    Pure aggregation over assign_cfb_shelves' own output -- no new
    scoring, no re-ranking, matches the spec's own framing exactly
    ("just aggregating existing shelf-membership data").

    A player absent from every shelf still gets a row here (shelves=[],
    shelf_count=0), not dropped -- week_rows is the full scored
    population; shelf membership is informational on top of it.
    """
    membership: dict = {pid: [] for pid in week_rows["player_id"]}
    for shelf_name in CFB_SHELF_ORDER:
        pool = shelves.get(shelf_name)
        if pool is None or pool.empty:
            continue
        for pid in pool["player_id"]:
            membership.setdefault(pid, []).append(shelf_name)

    out = week_rows.copy()
    out["shelves"] = out["player_id"].map(membership)
    out["shelf_count"] = out["shelves"].map(len)
    return out


def select_cfb_tasty_six(shelves: dict, n: int = 6) -> list:
    """
    DEFAULT rule (spec: "confirm with me which shelves feed Tasty Six ...
    or default to one-per-shelf-family logic if that's ambiguous" --
    defaulting here per that explicit permission, flagged in the build
    report for confirmation/correction): walk CFB_SHELF_ORDER (the 8 real
    shelves) and take each shelf's own top-ranked player not already
    claimed by an earlier shelf in the walk, falling back to that shelf's
    next-ranked entry when its top pick is already claimed -- the SAME
    fallback-to-next-eligible mechanism MLB/NFL's own Tasty Six selection
    already uses, just walking shelves (one claim each) instead of one
    ranked list.

    If a shelf is exhausted (every one of its rows already claimed by an
    earlier shelf) it contributes nothing and the walk continues -- Tasty
    Six can come back with fewer than `n` picks on a genuinely thin week;
    never backfilled with a lower-quality choice just to force a full six.

    Returns up to `n` dicts (each shelves[...]'s own row, as a dict) plus
    a `tasty_six_source` key naming which shelf it was claimed from, so a
    caller can show why a player is in the six, not just that they are.
    """
    picks: list = []
    claimed: set = set()

    for shelf_name in CFB_SHELF_ORDER:
        if len(picks) >= n:
            break
        pool = shelves.get(shelf_name)
        if pool is None or pool.empty:
            continue
        for _, row in pool.iterrows():
            pid = row["player_id"]
            if pid in claimed:
                continue
            record = row.to_dict()
            record["tasty_six_source"] = shelf_name
            picks.append(record)
            claimed.add(pid)
            break  # exactly one claim per shelf per walk

    return picks[:n]


# ---------------------------------------------------------------------------
# Story Archetype Resolver wiring (this task) — the real per-shelf-
# placement write path. See story_archetype.py's own module docstring
# for the full resolver design; this is only the shaping step that turns
# assign_cfb_shelves' output into cfb_player_shelf_scores rows.
# ---------------------------------------------------------------------------


def shape_cfb_shelf_placement_rows(shelves: dict) -> list:
    """
    One row per REAL (player, shelf) placement — THE actual "shelf-
    assignment" write, as distinct from shape_cfb_shelf_score_rows (the
    whole scored population, one row per player, `shelf`/`archetype`
    always None there — see CFB_SHELF_SCORE_COLUMNS' own comments).

    Expands assign_cfb_shelves()'s own {shelf_name: DataFrame} output:
    only players who made at least one of the 8 real shelves get a row
    here (a player on zero shelves this week gets none), and a player on
    N shelves gets N rows — one per shelf, `shelf` set to that shelf's
    real name and `archetype` resolved INDEPENDENTLY per placement via
    story_archetype.resolve_cfb_archetype(shelf_name, row). This is
    deliberately NOT deduplicated across shelves, extending assign_cfb_
    shelves' own "no dedup across shelves" rule to the write layer: a
    player on both Goal-Line Favorites and SEC TD Watch produces TWO
    rows here — GOAL_LINE on one, SEC on the other — never one row
    picking a winner between them (see story_archetype.py's own module
    docstring for why the same player genuinely gets a different
    archetype depending on which shelf's row it is).

    `shelves`: assign_cfb_shelves()'s own {shelf_name: DataFrame} output
    — already sorted best-first and capped at shelf_size per shelf. Row
    order within each shelf's own block in the returned list preserves
    that rank order.

    Uses the same to_json()-round-trip JSON-safety idiom shape_cfb_
    shelf_score_rows already establishes (numpy int64/float64/NaN/pd.NA
    survive a naive .to_dict("records") call untouched; to_json()'s own
    encoder handles them correctly).
    """
    rows: list = []
    for shelf_name in CFB_SHELF_ORDER:
        pool = shelves.get(shelf_name)
        if pool is None or pool.empty:
            continue
        records = json.loads(pool.to_json(orient="records"))
        for record in records:
            archetype_result = resolve_cfb_archetype(shelf_name, record)
            typed = {col: record.get(col) for col in CFB_SHELF_SCORE_COLUMNS if col not in ("shelf", "archetype")}
            typed["shelf"] = shelf_name
            typed["archetype"] = archetype_result["archetype"]
            typed["extra"] = {k: v for k, v in record.items() if k not in CFB_SHELF_SCORE_COLUMNS}
            rows.append(typed)
    return rows
