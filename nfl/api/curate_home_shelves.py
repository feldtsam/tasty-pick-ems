"""
NFL Shelf Curation & Tasty Six selection — assigns exactly one HOME
shelf per ATTD-eligible player across all seven shelves (shelves.py's
own per-shelf pools), applies the max-6-per-shelf cap (secondary
qualifications become tags, not duplicate placements), and selects
Tasty Six.

THREE APPROVED PROPOSALS THIS BUILDS AGAINST (confirmed before any code
was written, see the conversation this was designed and approved in):

1. PRIORITY: trend shelves (Red Zone Trends, RB/WR/TE Trends) beat odds
   shelves (the three ATTD bands) whenever a player qualifies for both
   — UNCHANGED by the fix below, still a flat rule with no score
   comparison involved. Trend-vs-trend conflicts (e.g. a RB eligible
   for both Red Zone Trends via td_opportunity AND RB Trends via role_
   momentum) are resolved by comparing the two shelves' own PERCENTILE
   RANK for that player within each shelf's own eligible pool (see
   _trend_percentiles) — the same "most interesting reason, for THIS
   player" principle, one level deeper than the blueprint explicitly
   covers (that gap was flagged and folded into the approved design,
   not assumed). REVISED from an initial raw-score comparison: real
   validation confirmed td_opportunity and role_momentum aren't
   calibrated to the same distribution even though both are nominally
   0-100 (2025 Week 10: mean 64.0 vs. 51.4 for the same real players
   eligible for both), which made Red Zone Trends structurally win
   82-86% of real contested cases — a scale artifact, not genuine
   signal. Fixed the same way MLB's own analogous cross-shelf
   comparison (the Ohtani case) was fixed earlier this session:
   percentile-normalize within each shelf's own population first.

2. STICKINESS — APPROVED BUT NOT WIRED IN, see below. The approved
   design (20-point margin on the underlying 0-100 signal score,
   evaluated across 2 consecutive WEEKLY curation runs, not poller
   runs) is fully specified but deliberately not implemented in
   assign_home_shelves() yet: it requires reading each player's
   prior-week shelf assignment back from nfl_content_drafts, and that
   table's real schema / history-retention behavior could not be
   verified from this repo before building — no Supabase credentials
   exist anywhere in this codebase (.env, or any .py file), and no NFL
   content-drafts read endpoint exists either (confirmed by direct
   search, zero references to nfl_content_drafts anywhere under nfl/).
   This is a harder blocker than "checked and it doesn't retain
   history" — it's "cannot check at all from this environment" — so
   per explicit instruction, stickiness stops here pending that
   confirmation, rather than being built against an unverified
   persistence layer. Every player is currently assigned as if it were
   their first-ever appearance (priority rule only, no stickiness
   comparison) — this is not a shortcut standing in for the real
   design, it is the EXACT documented behavior Proposal 2 already
   specifies for that case. Wiring in the real stickiness comparison
   once the persistence question is resolved is a small, additive
   change to assign_home_shelves() (a prior_assignments parameter is
   already threaded through for exactly this — see its docstring), not
   a rewrite.

3. TASTY SIX THRESHOLD: tpe_score >= 55 AND evidence_quality >= 65,
   applied per home shelf — one pick per shelf (of however many of the
   seven have a qualifying candidate), never manufactured, sparse is
   fine (see select_tasty_six). Both this and Proposal 2's numbers are
   explicitly provisional, flagged for validation against real weekly
   data once the season is live — not treated as final on paper.

CONTENT GENERATION — Part A reconnected (this update), Part C still not
built. Investigation (a separate cycle, before this update) confirmed
the "adapted MLB Content Voice Engine already exists and works for NFL"
premise did not hold: pipeline/api/content_writer's PILLAR_NAMES was
(and still is) hardcoded to MLB's own four pillars, and no NFL-specific
LLM writer exists anywhere in nfl/ — that's real, separate work (Part C)
with its own approved design, not built in this module.

What Part A DOES reconnect: nfl/shelves.py already has a deterministic,
non-LLM headline+evidence generator (red_zone_story/position_story/
odds_band_story, now public — see shelves.py) wired into all seven
shelves, real and already debugged against real historical bugs. This
module previously discarded that entirely and wrote headline/why_its_
tasty as None unconditionally. shape_content_draft_rows() below now
calls those same story generators directly on every REGULAR (non-Tasty-
Six) home-assigned row — not via a build_all_shelves() lookup, which has
a real population gap (see _story_for_row's own docstring: shelves.py's
own top-N ranking for a shelf and this module's home-assigned population
for that shelf are genuinely different sets, so a lookup keyed by
(shelf, player_id) silently misses real rows). is_tasty_six=True rows
are deliberately EXCLUDED from this path and keep headline/why_its_tasty
as None — they're reserved for Part C's LLM writer, not this
deterministic system, per the approved architecture. editorial_content
stays None for every row at this stage (no source, deterministic or
LLM, produces it yet — see Part C).

NOT BUILT HERE EITHER: an actual HTTP write to nfl_content_drafts. The
shaping function (shape_content_draft_rows) produces exactly the rows a
write would need, and write_content_draft_rows() below is structured to
send them (reusing lovable_forward.py's existing signed-POST machinery,
same convention as every other NFL webhook write) — but isn't wired to
a confirmed real endpoint (same "URL naming convention documented, not
guessed at" pattern nfl/api/index.py's own DEFAULT_NFL_PRICE_HISTORY_
WRITE_URL already established for exactly this "target isn't live yet"
situation), and wasn't exercised against anything real in this task:
writing incomplete rows (null headline/why_its_tasty) into a real
production content-drafts table is exactly the kind of hard-to-reverse
action worth Sam's explicit go-ahead first, not something to fire off
as a side effect of validating selection logic.
"""
import sys
from pathlib import Path

# nfl/ itself, so `from shelves import ...` resolves regardless of
# whether this module is imported via nfl/api/index.py (which already
# adds this) or run/imported standalone — same defensive bootstrapping
# reconcile_week.py's own script entry point uses.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from normalize import build_reference_scale, fill_neutral, percentile_lookup
from shelves import CONFIG as SHELVES_CONFIG
from shelves import (
    ODDS_BANDS, add_red_zone_trend_windows, eligible_pool, odds_band_eligible,
    odds_band_story, position_story, red_zone_story,
)

SHELF_ORDER = [
    "Red Zone Trends", "RB Trends", "WR Trends", "TE Trends",
    "ATTD +300-499", "ATTD +500-699", "ATTD +700+",
]
TREND_SHELVES = SHELF_ORDER[:4]
ODDS_SHELVES = SHELF_ORDER[4:]

# The exact shelf-name strings above are this task's own working labels,
# matching the blueprint's own display names — NOT independently
# confirmed against nfl_content_drafts' real `shelf` column values,
# same access limitation as the stickiness persistence question. Flag
# this if the real schema expects a different string format.
PRIMARY_SIGNAL_COL = {
    "Red Zone Trends": "td_opportunity",
    "RB Trends": "role_momentum",
    "WR Trends": "role_momentum",
    "TE Trends": "role_momentum",
    "ATTD +300-499": "tpe_score",
    "ATTD +500-699": "tpe_score",
    "ATTD +700+": "tpe_score",
}

CONFIG = {
    # Build step 1's own eligibility filter. Same floor shelves.py's own
    # trend shelves already use — kept as its own named constant here
    # (not just read off SHELVES_CONFIG) since this is the OVERALL gate
    # across all seven shelves, a slightly different concern than any
    # one shelf's own attd_odds_floor, even though the value is
    # identical today.
    "attd_odds_floor": 300,
    "max_per_shelf": 6,
    # Proposal 2, approved, PROVISIONAL — needs real-data validation
    # once the season is live. NOT enforced yet — see module docstring.
    "sticky_margin": 20.0,
    "sticky_run_count": 2,
    # Proposal 3, approved, PROVISIONAL — same as above.
    "tasty_six_tpe_threshold": 55.0,
    "tasty_six_evidence_threshold": 65.0,
}


def _attd_eligible_overall(weekly: pd.DataFrame, min_odds: int) -> pd.Series:
    """
    Build step 1's own eligibility filter. The same one-line check
    shelves.py's own _attd_eligible uses internally — duplicated here
    rather than imported, since it IS the kind of trivial one-liner
    this codebase normally prefers duplicating over reaching into
    another module's private helper (unlike eligible_pool/odds_band_
    eligible below, which are real multi-line business logic now made
    public for exactly this cross-module reuse — see their own
    docstrings in shelves.py for that distinction).
    """
    return weekly["consensus_price_american"].notna() & (weekly["consensus_price_american"] >= min_odds)


def _shelf_qualifying_pools(weekly: pd.DataFrame, shelves_config: dict = SHELVES_CONFIG) -> dict:
    """
    {shelf_name: DataFrame} — the FULL QUALIFYING pool per shelf (not
    truncated to shelf_size), one entry per SHELF_ORDER name.

    "Qualifying" for a TREND shelf means clearing that shelf's own
    completeness gate (td_opportunity_completeness / role_momentum_
    completeness >= shelves_config's own threshold), NOT merely having
    a non-null primary-signal value — eligible_pool() alone returns the
    latter, deliberately (it's also the source for _build_shelf's own
    below-gate FALLBACK population, which exists for when a shelf's
    display list needs backfilling, not as a definition of "this player
    has a real story"). Using eligible_pool() ungated here was a real
    bug caught during this module's own validation: it let a player
    with a technically-real-but-uninformative primary-signal value count
    as "qualifying" for home-shelf assignment purposes, which is a
    materially looser bar than what actually gets a player onto that
    shelf's own real card list. Gating on completeness here, matching
    _build_shelf's own "gated" population exactly, is the correct
    definition of "genuinely qualifies," confirmed against real data
    (2025 Week 10: 112 RB/WR/TE have SOME td_opportunity value, only 104
    clear completeness>=50 — a real, non-trivial difference).

    Odds shelves have no analogous gate (matching build_odds_band_shelf's
    own design — no single pillar's completeness is the natural fit for
    a composite-ranked shelf), so odds_band_eligible()'s own output is
    used as-is.
    """
    threshold = shelves_config["completeness_threshold"]
    odds_floor = shelves_config["attd_odds_floor"]

    rz = eligible_pool(weekly, "td_opportunity", odds_floor)
    rz = rz[rz["td_opportunity_completeness"] >= threshold["red_zone_trends"]]

    def _position_trend_pool(position: str, shelf_key: str) -> pd.DataFrame:
        pool = eligible_pool(weekly, "role_momentum", odds_floor, position_filter=position)
        return pool[pool["role_momentum_completeness"] >= threshold[shelf_key]]

    pools = {
        "Red Zone Trends": rz,
        "RB Trends": _position_trend_pool("RB", "rb_trends"),
        "WR Trends": _position_trend_pool("WR", "wr_trends"),
        "TE Trends": _position_trend_pool("TE", "te_trends"),
    }
    for label, lo, hi in ODDS_BANDS:
        pools[label] = odds_band_eligible(weekly, lo, hi)
    return pools


def _trend_percentiles(shelf_pools: dict) -> dict:
    """
    {shelf_name: {player_id: percentile}} for the FOUR TREND shelves
    only — the fix for the trend-vs-trend tiebreak (see assign_home_
    shelves). td_opportunity and role_momentum are both nominally 0-100
    but are NOT calibrated to the same underlying distribution — real
    validation on this module's own real-data build confirmed this
    directly (2025 Week 10: mean td_opportunity 64.0 vs. mean role_
    momentum 51.4 for the SAME real players eligible for both RB Trends
    and Red Zone Trends), which made Red Zone Trends structurally win
    82-86% of real contested cases under a raw-score comparison — a
    scale artifact, not a genuine "which story is more interesting"
    read. Reuses normalize.py's build_reference_scale/percentile_lookup/
    fill_neutral — the exact same percentile-ranking primitives every
    scoring.py pillar already uses (and the same fix already applied
    once this session for MLB's own cross-shelf shelf_score comparison,
    the Ohtani case) — rather than a new implementation. Each shelf's
    reference population is its own qualifying pool (already
    completeness-gated by _shelf_qualifying_pools), so a player's
    percentile here answers "how strong is this reading relative to
    everyone else who genuinely qualifies for THIS shelf," the
    apples-to-apples comparison the raw score never was.
    """
    percentiles = {}
    for shelf_name in TREND_SHELVES:
        pool = shelf_pools[shelf_name]
        col = PRIMARY_SIGNAL_COL[shelf_name]
        scale = build_reference_scale(pool[col])
        pct = fill_neutral(percentile_lookup(pool[col], scale))
        percentiles[shelf_name] = dict(zip(pool["player_id"], pct))
    return percentiles


def assign_home_shelves(
    weekly: pd.DataFrame, config: dict = CONFIG, shelves_config: dict = SHELVES_CONFIG,
    prior_assignments: dict = None,
) -> pd.DataFrame:
    """
    One row per ATTD-eligible player (odds >= config["attd_odds_floor"])
    who qualifies for at least one of the seven shelves — columns:
    player_id, player_name, posteam, position_group, home_shelf,
    home_shelf_signal_value, tpe_score, evidence_quality, consensus_
    price_american, qualifying_shelves (every OTHER shelf this player
    also qualifies for — the "secondary qualifications become tags"
    data build step 3 needs).

    prior_assignments: {player_id: {"shelf": str, "run_count": int}},
    reserved for the real stickiness comparison (Proposal 2) — accepted
    here but NOT YET USED (always None-equivalent behavior regardless
    of what's passed) until nfl_content_drafts' real history-retention
    is confirmed. Threading the parameter through now, unused, means
    wiring in the real comparison later only touches this function's
    body, not every caller's signature.
    """
    overall_pool = weekly[_attd_eligible_overall(weekly, config["attd_odds_floor"])].drop_duplicates(subset=["player_id"]).copy()
    columns = [
        "player_id", "player_name", "posteam", "position_group", "home_shelf",
        "home_shelf_signal_value", "tpe_score", "evidence_quality",
        "consensus_price_american", "qualifying_shelves",
    ]
    if len(overall_pool) == 0:
        return pd.DataFrame(columns=columns)

    shelf_pools = _shelf_qualifying_pools(weekly, shelves_config)
    trend_percentiles = _trend_percentiles(shelf_pools)

    # player_id -> {shelf_name: signal_value}, only for shelves they
    # actually qualify for. RAW values — still used for home_shelf_
    # signal_value / within-shelf cap ranking (apply_shelf_cap), which
    # only ever compares players already on the SAME shelf against each
    # other, where the cross-shelf scale mismatch doesn't apply and raw
    # score is already a correct, monotonic ranking.
    qualifies = {}
    for shelf_name in SHELF_ORDER:
        signal_col = PRIMARY_SIGNAL_COL[shelf_name]
        for _, row in shelf_pools[shelf_name].iterrows():
            qualifies.setdefault(row["player_id"], {})[shelf_name] = row[signal_col]

    rows = []
    for _, prow in overall_pool.iterrows():
        pid = prow["player_id"]
        player_shelves = qualifies.get(pid, {})
        if not player_shelves:
            # ATTD-eligible overall, but not RB/WR/TE, or missing every
            # shelf's own primary-signal value — genuinely nothing to
            # assign, not an error.
            continue

        qualifying_trend = {s: v for s, v in player_shelves.items() if s in TREND_SHELVES}
        qualifying_odds = {s: v for s, v in player_shelves.items() if s in ODDS_SHELVES}

        if qualifying_trend:
            # Proposal 1: trend beats odds (UNCHANGED — this branch
            # still fires whenever ANY trend shelf qualifies, regardless
            # of odds-shelf eligibility). Trend-vs-trend itself is now
            # resolved by PERCENTILE rank within each shelf's own
            # population, not raw score — see _trend_percentiles.
            home_shelf = max(qualifying_trend, key=lambda s: trend_percentiles[s][pid])
        else:
            # Fallback: whichever odds band matches their current price
            # — ODDS_BANDS are non-overlapping by construction, so this
            # is always exactly one shelf when it's reached at all.
            home_shelf = next(iter(qualifying_odds))

        rows.append({
            "player_id": pid,
            "player_name": prow["player_name"],
            "posteam": prow["posteam"],
            "position_group": prow.get("position_group"),
            "home_shelf": home_shelf,
            "home_shelf_signal_value": player_shelves[home_shelf],
            "tpe_score": prow.get("tpe_score"),
            "evidence_quality": prow.get("evidence_quality"),
            "consensus_price_american": prow.get("consensus_price_american"),
            "qualifying_shelves": sorted(s for s in player_shelves if s != home_shelf),
        })

    return pd.DataFrame(rows, columns=columns)


def apply_shelf_cap(home_assignments: pd.DataFrame, config: dict = CONFIG) -> pd.DataFrame:
    """
    Ranks each shelf's own home-assigned players by home_shelf_signal_
    value (tpe_score then evidence_quality as tiebreakers, same order
    _rank_pool uses elsewhere), keeps the top max_per_shelf, and marks
    the rest capped=True. A capped player still has their real
    qualifying_shelves tag data (they don't disappear from the dataset),
    they just don't get a written row for this shelf this week — the
    blueprint's own "secondary qualifications become tags, not
    duplicate placements" principle applied to a shelf's OWN overflow,
    not just cross-shelf duplicates.
    """
    columns = list(home_assignments.columns) + ["rank", "capped"]
    if len(home_assignments) == 0:
        return pd.DataFrame(columns=columns)

    parts = []
    for _, group in home_assignments.groupby("home_shelf", sort=False):
        ranked = group.sort_values(
            ["home_shelf_signal_value", "tpe_score", "evidence_quality"], ascending=False,
        ).reset_index(drop=True)
        ranked["rank"] = ranked.index + 1
        ranked["capped"] = ranked["rank"] > config["max_per_shelf"]
        parts.append(ranked)
    return pd.concat(parts, ignore_index=True)


def select_tasty_six(capped_assignments: pd.DataFrame, config: dict = CONFIG) -> dict:
    """
    One pick per HOME shelf (of however many of the seven have a
    qualifying candidate) — never manufactured, sparse is fine.
    Approved threshold (Proposal 3): tpe_score >= tasty_six_tpe_
    threshold AND evidence_quality >= tasty_six_evidence_threshold.
    Only considers players who survived the max-6 cap (capped=False) —
    a player bumped off their home shelf's own display list this week
    has no case being made for them there.

    Returns {shelf_name: row_or_None}, one entry per SHELF_ORDER name.
    """
    picks = {}
    for shelf_name in SHELF_ORDER:
        if len(capped_assignments) == 0:
            picks[shelf_name] = None
            continue
        pool = capped_assignments[
            (capped_assignments["home_shelf"] == shelf_name) & (~capped_assignments["capped"])
            & (capped_assignments["tpe_score"] >= config["tasty_six_tpe_threshold"])
            & (capped_assignments["evidence_quality"] >= config["tasty_six_evidence_threshold"])
        ]
        if len(pool) == 0:
            picks[shelf_name] = None
            continue
        picks[shelf_name] = pool.sort_values(["tpe_score", "evidence_quality"], ascending=False).iloc[0]
    return picks


def _story_for_row(row: pd.Series, shelf_name: str) -> dict:
    """
    Dispatches to shelves.py's own deterministic, already-validated
    per-row story generator (red_zone_story/position_story/odds_band_
    story) for whichever shelf this IS the player's real home shelf —
    the exact same headline/evidence text a player would get on
    shelves.py's own display list, computed FRESH for this specific row
    rather than looked up from build_all_shelves()'s own card output.

    WHY FRESH, NOT A LOOKUP — the real reason Part A's reconnection
    isn't just "call build_all_shelves() and match by (shelf, player_id)":
    shelves.py's own top-N ranking for a shelf and this module's
    home-assigned population for that SAME shelf are genuinely different
    populations, not just different names for the same thing. shelves.py
    ranks the FULL eligible pool by that shelf's primary signal alone,
    with no cross-shelf exclusivity — a player can sit in the raw top 6
    for RB Trends there even though this module home-assigns them to Red
    Zone Trends instead (their percentile there won the trend-vs-trend
    comparison). That "frees a slot" in RB Trends' real home-assigned
    top 6 for a player who wouldn't have cracked shelves.py's own raw
    top-6 cut at all (confirmed directly against the real 2025 Week 10
    pool during this task's own validation — see the module test suite).
    A card-list lookup keyed by (shelf, player_id) would silently miss
    exactly these players — real home-assigned, uncapped rows with no
    corresponding card anywhere in build_all_shelves()'s output. Calling
    the row-level story function directly, on every home-assigned row,
    has no such gap: it doesn't care whether this row would have made
    shelves.py's own cut.

    Requires add_red_zone_trend_windows() already applied upstream when
    shelf_name == "Red Zone Trends" — see shape_content_draft_rows'
    weekly_story_lookup construction, the only caller.
    """
    if shelf_name == "Red Zone Trends":
        return red_zone_story(row)
    if shelf_name in ("RB Trends", "WR Trends", "TE Trends"):
        return position_story(row, shelf_name.split()[0])
    return odds_band_story(row)


def shape_content_draft_rows(
    capped_assignments: pd.DataFrame, tasty_six: dict, season: int, week: int, weekly: pd.DataFrame = None,
) -> list:
    """
    One dict per (surviving-the-cap) player-shelf placement, shaped for
    an eventual nfl_content_drafts write. Every field this task's own
    selection/ranking logic actually owns is real.

    RECONNECTED (Part A): headline/why_its_tasty now come from shelves.
    py's own deterministic, already-validated story generators (see
    _story_for_row) for every REGULAR (non-Tasty-Six) row — real,
    grounded template text, not an LLM call. `weekly` is the same
    already-scored table assign_home_shelves() was built from; pass it
    here (or through curate_nfl_shelves, which threads it automatically)
    to populate real content. Omit it (the old behavior, still supported
    for callers that only need the selection/ranking fields) and every
    row's headline/why_its_tasty fall back to None, same as before this
    task.

    TASTY SIX ROWS ARE DELIBERATELY EXCLUDED from this deterministic
    path — is_tasty_six=True rows keep headline/why_its_tasty as None
    here regardless of whether `weekly` was passed, per the approved
    architecture: Tasty Six gets its own LLM writer (Part C, not yet
    built), not shelves.py's per-shelf template text. Writing the
    deterministic version in first would mean a real risk of a
    half-upgraded card shipping if Part C is delayed — None is the
    honest "not written yet" signal, same discipline as leaving these
    fields None entirely before this task.

    editorial_content stays None for every row, Tasty Six or not — no
    deterministic or LLM source for it exists yet at either tier (see
    module docstring; Part C is scoped to add it for Tasty Six only,
    matching MLB's own regular-card-has-no-editorial-sentence split).

    review_status is always "pending_review" — never auto-approved, per
    explicit instruction; nothing in this task ever sets it to anything
    else.
    """
    if len(capped_assignments) == 0:
        return []
    tasty_lookup = {shelf: (row["player_id"] if row is not None else None) for shelf, row in tasty_six.items()}

    story_lookup = {}
    if weekly is not None and len(weekly) > 0:
        prepped = add_red_zone_trend_windows(weekly)
        story_lookup = {row["player_id"]: row for _, row in prepped.iterrows()}

    rows = []
    for _, r in capped_assignments[~capped_assignments["capped"]].iterrows():
        is_tasty_six = tasty_lookup.get(r["home_shelf"]) == r["player_id"]

        headline, why_its_tasty = None, None
        if not is_tasty_six and r["player_id"] in story_lookup:
            story = _story_for_row(story_lookup[r["player_id"]], r["home_shelf"])
            headline, why_its_tasty = story["headline"], story["evidence"]

        rows.append({
            "player_id": r["player_id"],
            "player_name": r["player_name"],
            "season": season,
            "week": week,
            "shelf": r["home_shelf"],
            "rank": int(r["rank"]),
            "is_tasty_six": is_tasty_six,
            "review_status": "pending_review",
            "tpe_score": r.get("tpe_score"),
            "evidence_quality": r.get("evidence_quality"),
            "consensus_price_american": r.get("consensus_price_american"),
            "headline": headline,
            "why_its_tasty": why_its_tasty,
            "editorial_content": None,
        })
    return rows


def curate_nfl_shelves(
    weekly: pd.DataFrame, season: int, week: int, config: dict = CONFIG, shelves_config: dict = SHELVES_CONFIG,
) -> dict:
    """
    The full pipeline, steps 1-5 + shaping (step 7 minus content, per
    this module's own docstring): eligibility -> home-shelf assignment
    -> max-6 cap -> Tasty Six -> content_drafts row shaping. Does NOT
    write anywhere — see write_content_draft_rows for that, and its own
    docstring for why this task stops short of actually calling it.

    Returns {"home_assignments": DataFrame, "capped": DataFrame,
    "tasty_six": dict, "content_draft_rows": list}.
    """
    home_assignments = assign_home_shelves(weekly, config, shelves_config)
    capped = apply_shelf_cap(home_assignments, config)
    tasty_six = select_tasty_six(capped, config)
    content_draft_rows = shape_content_draft_rows(capped, tasty_six, season, week, weekly=weekly)
    return {
        "home_assignments": home_assignments,
        "capped": capped,
        "tasty_six": tasty_six,
        "content_draft_rows": content_draft_rows,
    }


DEFAULT_NFL_CONTENT_DRAFTS_WRITE_URL = "https://tastypickems.lovable.app/api/public/nfl-content-drafts-write"


def write_content_draft_rows(rows: list, secret: str, write_url: str = None):
    """
    NOT CALLED anywhere in this task — see module docstring. Reuses
    lovable_forward.py's existing signed-POST machinery (the same
    HMAC/X-Signature pattern every other NFL webhook write already
    uses), rather than a new mechanism. write_url follows the same
    "documented naming convention, not a confirmed real route" pattern
    as DEFAULT_NFL_PRICE_HISTORY_WRITE_URL in nfl/api/index.py — will
    404/502 harmlessly, not silently succeed, until the real endpoint
    and env var are confirmed and set.
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from lovable_forward import forward_to_lovable

    url = write_url or DEFAULT_NFL_CONTENT_DRAFTS_WRITE_URL
    return forward_to_lovable(rows, secret, url)
