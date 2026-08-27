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

CONTENT GENERATION — Parts A, B, and C all reconnected here (this
update, the write-connection task). Regular (non-Tasty-Six) rows get
nfl/shelves.py's deterministic, non-LLM headline+evidence generator
(red_zone_story/position_story/odds_band_story — Part A), wrapped into
the real why_reasons array shape via _deterministic_why_reasons below
(a single-item array, not the LLM's 2-3 item array — a real, reported
design choice, see that function's own docstring). Tasty Six rows get a
real call into nfl/content_writer/generate_tasty_six_content.py's
generate_nfl_tasty_six_draft() (Part C's actual LLM writer, cross-
imported) — ONLY when a real anthropic_api_key is passed through to
shape_content_draft_rows/curate_nfl_shelves; omit it (the default) and
Tasty Six rows keep title/editorial_sentence/why_reasons/confidence_band
as None, the same honest "not generated yet" signal as before this
task, rather than raising. confidence_band is derived from tpe_score via
nfl_writer_common.nfl_confidence_band_for_score() — the previously-
pending thresholds are now approved (see that function's own docstring
for the real distribution they're grounded in) and hardcoded there.

REAL COLUMN NAMES, confirmed directly against the live nfl_content_
drafts schema (not the placeholder names this module used before):
event_id (= game_id — nflverse's own real per-game identifier, already
unique, already on every scored row — no new identifier scheme
invented), team (= posteam), opponent (= defteam), matchup (parsed
directly from game_id's own "{season}_{week}_{away}_{home}" convention,
formatted "{away} @ {home}" — the exact same convention MLB's own
candidate shape already uses, confirmed by checking pipeline/api/
content_writer's own TOP_LEVEL_CITABLE_FIELDS fixture data, not
invented fresh), odds (= consensus_price_american), kickoff_utc (a real
column with NO existing source anywhere on the scored weekly table —
see redzone.add_kickoff_utc, extracted from the depth-chart week-
derivation logic that already computes this exact value; requires a
`schedules` DataFrame passed through to shape_content_draft_rows/
curate_nfl_shelves — optional, same "omit it, get None for this one
field" fallback as anthropic_api_key).

write_content_draft_rows() below now points at the real endpoint via
the LOVABLE_NFL_CONTENT_DRAFTS_WRITE_URL env var (resolve_url_env, same
established pattern as LOVABLE_NFL_PRICE_HISTORY_WRITE_URL in nfl/api/
index.py) — the DEFAULT_ constant below is kept only as resolve_url_env's
required fallback argument, not the real source of truth.
"""
import json
import sys
from pathlib import Path

# nfl/ itself, so `from shelves import ...` resolves regardless of
# whether this module is imported via nfl/api/index.py (which already
# adds this) or run/imported standalone — same defensive bootstrapping
# reconcile_week.py's own script entry point uses.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from divisions import DIVISIONS
from normalize import build_reference_scale, fill_neutral, percentile_lookup
from redzone import add_kickoff_utc
from shelves import CONFIG as SHELVES_CONFIG
from shelves import (
    ODDS_BANDS, add_red_zone_trend_windows, eligible_pool, odds_band_eligible,
    odds_band_story, position_story, red_zone_story,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "content_writer"))
from generate_tasty_six_content import generate_nfl_tasty_six_draft  # noqa: E402
from nfl_writer_common import nfl_confidence_band_for_score, nfl_regular_row_confidence_band_for_score  # noqa: E402

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


STICKINESS_MARGIN = 20.0


def _compute_sticky_assignment(
    candidate_shelf: str, candidate_signal: float,
    current_home_shelf: str, current_signal,
    prior_pending_shelf, prior_pending_run_count: int,
    margin: float = STICKINESS_MARGIN,
) -> dict:
    """
    Pure state-transition function for Proposal 2's approved stickiness
    rule (20-point margin, 2 consecutive weekly curation runs) — isolated
    from all I/O so it's directly unit-testable against hand-built and
    real historical cases, independent of the read/write plumbing around
    it.

    `candidate_shelf`/`candidate_signal`: THIS week's fresh, non-sticky
    pick — exactly what assign_home_shelves' own existing trend-priority
    + percentile-tiebreak logic already computes, unchanged. `current_
    home_shelf`: the player's real prior-week home shelf. `current_
    signal`: THIS week's fresh signal value for current_home_shelf — NOT
    a historical value; the margin comparison always uses live, current-
    week numbers for BOTH shelves, per the approved design (only WHICH
    shelf was already pending, not the raw comparison itself, is
    historical). None specifically means the player no longer qualifies
    for current_home_shelf AT ALL this week — its real signal is simply
    absent, not low.

    RAW SIGNAL VALUES, NOT PERCENTILE — a deliberate, considered choice,
    not an oversight: Proposal 2's own approved language is "the current
    shelf's underlying signal SCORE" and "a 20-point margin... on the
    0-100 scale", written before this session's later percentile fix to
    the (separate) initial trend-vs-trend tiebreak — and that fix was
    explicitly confirmed at the time to leave "trend-vs-odds priority
    and first-appearance assignment... provably untouched". Stickiness
    is a third, separate mechanism nothing has approved applying that
    fix to. Implemented literally as approved. Flagged here because the
    SAME cross-shelf raw-scale bias that motivated the percentile fix
    (td_opportunity running structurally higher than role_momentum for
    the same real players) applies in principle to this comparison too
    — worth a real second look with Sam, not silently assumed fine.

    current_signal=None (the player's PRIOR home shelf isn't something
    they qualify for AT ALL this week) is NOT explicitly covered by the
    approved rules — those addressed the PENDING shelf disappearing
    (resets the streak), not the CURRENT shelf disappearing. Extended
    here by the same underlying principle: there's no valid "current"
    shelf left to be sticky about, so this week's fresh candidate
    becomes the new home shelf immediately, no 2-week wait required —
    a real interpretive extension of the approved design, not something
    explicitly signed off, flagged as such rather than silently assumed.

    Returns {"home_shelf": str, "pending_shelf": str|None,
    "pending_run_count": int} — the real new state to both use for this
    week's output AND persist to nfl_shelf_signal_history for next week.
    """
    if current_signal is None:
        return {"home_shelf": candidate_shelf, "pending_shelf": None, "pending_run_count": 0}

    if candidate_shelf == current_home_shelf:
        # The fresh, non-sticky pick already agrees with the real current
        # shelf — nothing being challenged, nothing pending.
        return {"home_shelf": current_home_shelf, "pending_shelf": None, "pending_run_count": 0}

    if candidate_signal - current_signal >= margin:
        new_count = (prior_pending_run_count + 1) if candidate_shelf == prior_pending_shelf else 1
        if new_count >= 2:
            # Reassignment fires — nothing pending against the NEW home
            # shelf going forward (a fresh baseline, per the approved
            # design: "nothing pending immediately after a successful
            # reassignment").
            return {"home_shelf": candidate_shelf, "pending_shelf": None, "pending_run_count": 0}
        return {"home_shelf": current_home_shelf, "pending_shelf": candidate_shelf, "pending_run_count": new_count}

    # Margin not met this week — approved rule: reset, don't partial-credit.
    return {"home_shelf": current_home_shelf, "pending_shelf": None, "pending_run_count": 0}


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
    also qualifies for), qualifying_signals (EVERY shelf's real raw
    signal value this player qualifies for this week — the full real
    picture nfl_shelf_signal_history needs, not just the winning
    shelf's), pending_shelf, pending_run_count (this week's REAL,
    updated stickiness state — see _compute_sticky_assignment).

    prior_assignments: {player_id: {"home_shelf": str, "pending_shelf":
    str|None, "pending_run_count": int}} — real prior-week (or walked-
    back further, for a bye gap — see build_prior_state_with_walkback)
    state, as read from nfl_shelf_signal_history. None (the default,
    still fully supported) means every player is treated as a first
    appearance — the exact behavior this function always had before
    stickiness was wired in, not a regression: home_shelf is always
    just this week's fresh candidate, pending_shelf/pending_run_count
    always None/0. A player with no entry in prior_assignments (even
    when prior_assignments itself is non-None for OTHER players) is
    ALSO treated as first-appearance individually — the same "no prior
    row -> no comparison" rule the approved design already specifies.
    """
    overall_pool = weekly[_attd_eligible_overall(weekly, config["attd_odds_floor"])].drop_duplicates(subset=["player_id"]).copy()
    columns = [
        "player_id", "player_name", "posteam", "position_group", "home_shelf",
        "home_shelf_signal_value", "tpe_score", "evidence_quality",
        "consensus_price_american", "qualifying_shelves",
        "qualifying_signals", "pending_shelf", "pending_run_count",
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
            candidate_shelf = max(qualifying_trend, key=lambda s: trend_percentiles[s][pid])
        else:
            # Fallback: whichever odds band matches their current price
            # — ODDS_BANDS are non-overlapping by construction, so this
            # is always exactly one shelf when it's reached at all.
            candidate_shelf = next(iter(qualifying_odds))

        prior = prior_assignments.get(pid) if prior_assignments else None
        if prior is None:
            # First appearance (or stickiness not wired in by this
            # caller at all) — exact prior behavior, unchanged: this
            # week's fresh candidate IS the home shelf, nothing pending.
            home_shelf = candidate_shelf
            pending_shelf, pending_run_count = None, 0
        else:
            current_home_shelf = prior.get("home_shelf")
            current_signal = player_shelves.get(current_home_shelf)
            sticky = _compute_sticky_assignment(
                candidate_shelf, player_shelves[candidate_shelf],
                current_home_shelf, current_signal,
                prior.get("pending_shelf"), prior.get("pending_run_count") or 0,
            )
            home_shelf = sticky["home_shelf"]
            pending_shelf, pending_run_count = sticky["pending_shelf"], sticky["pending_run_count"]

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
            "qualifying_signals": dict(player_shelves),
            "pending_shelf": pending_shelf,
            "pending_run_count": pending_run_count,
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
    weekly_lookup construction, the only caller.
    """
    if shelf_name == "Red Zone Trends":
        return red_zone_story(row)
    if shelf_name in ("RB Trends", "WR Trends", "TE Trends"):
        return position_story(row, shelf_name.split()[0])
    return odds_band_story(row)


# {shelf_name: (why_reasons pillar tag, real column to cite as the sole
# source_fact_key)} — the deterministic path's own pillar mapping, used
# by _deterministic_why_reasons below. Odds-band shelves map to
# "market_value" as the closest available real pillar name (see that
# function's own docstring for why this is a deliberate approximation,
# not a literal match: odds_band_story's real citation is tpe_score, the
# COMPOSITE score, not market_value_score specifically — there's no
# "composite" option in the real 5-pillar enum, and market_value is the
# closest conceptual fit for an odds-driven shelf).
_DETERMINISTIC_PILLAR_FOR_SHELF = {
    "Red Zone Trends": ("td_opportunity", "td_opportunity"),
    "RB Trends": ("role_momentum", "role_momentum"),
    "WR Trends": ("role_momentum", "role_momentum"),
    "TE Trends": ("role_momentum", "role_momentum"),
    "ATTD +300-499": ("market_value", "tpe_score"),
    "ATTD +500-699": ("market_value", "tpe_score"),
    "ATTD +700+": ("market_value", "tpe_score"),
    # Around the League's 8 division "shelves" — same tpe_score-primary
    # shape as the 3 ATTD odds-band shelves directly above (Around the
    # League is grouped by division instead of price, but the ranking
    # signal is the identical composite score), so each division maps to
    # the same ("market_value", "tpe_score") pair rather than inventing
    # a ninth pillar tag that doesn't exist in the real 5-pillar enum.
    **{division: ("market_value", "tpe_score") for division in DIVISIONS},
}


def _deterministic_why_reasons(row: pd.Series, shelf_name: str, story: dict) -> list:
    """
    Wraps Part A's deterministic single headline+evidence text into the
    real why_reasons column's real inner shape — {pillar, stars, text,
    citation}, confirmed directly against the live route's Zod schema
    (NOT {reason_text, source_fact_keys}, the field names an earlier,
    incomplete investigation assumed — see generate_tasty_six_content.py
    for where that MLB-inherited naming still lives internally, and
    shape_content_draft_rows for the translation layer between the two).
    `citation` is built as an array (same one real column name this
    reason is grounded in) — the exact real Zod type for `citation`
    wasn't confirmed beyond "the field is named citation, not source_
    fact_keys"; an array is the closest faithful carry-over of what this
    function already tracked, flagged as an assumption pending real-
    write confirmation, not asserted as verified.

    DESIGN CHOICE, reported per explicit instruction rather than just
    silently decided: a SINGLE-ITEM array, not the LLM writer's 2-3 item
    array. Two real options existed: (a) reshape the deterministic
    story's prose into multiple itemized reasons, which would mean
    retrofitting a citation-tracking system shelves.py's story functions
    were never built to produce (they generate one grounded narrative
    per card, by construction, not itemized claims each traceable to a
    specific fact — inventing that after the fact risks OVER-claiming
    citations the deterministic system never actually validated per-
    item); (b) one honest item, citing the single real pillar column the
    story is actually built from. Chose (b) — it's exactly as grounded
    as the deterministic system already is (no new claims), and matches
    the schema's real minItems=1 requirement without fabricating
    structure that isn't there.

    `stars`: no existing star rating from the deterministic system (it
    was never built to produce one) — derived here from the SAME real
    pillar value that determined this shelf/story, banded the same rough
    way card_writer_common._expected_star_range works (a sanity range,
    collapsed to one representative value per band since `stars` must be
    a single int here, not a range).
    """
    pillar, source_key = _DETERMINISTIC_PILLAR_FOR_SHELF[shelf_name]
    real_score = row.get(source_key)
    if real_score is None or pd.isna(real_score):
        stars = 3
    elif real_score >= 75:
        stars = 5
    elif real_score >= 60:
        stars = 4
    elif real_score >= 40:
        stars = 3
    elif real_score >= 25:
        stars = 2
    else:
        stars = 1
    return [{
        "pillar": pillar,
        "stars": stars,
        "text": story["evidence"],
        "citation": [source_key],
    }]


def _matchup_from_game_id(game_id) -> str | None:
    """"{away} @ {home}", parsed directly from nflverse's own game_id
    convention ("{season}_{week}_{away}_{home}") — no schedules lookup
    needed, since game_id already encodes this and is already on every
    scored row. Matches MLB's own real candidate shape's "matchup"
    convention exactly (confirmed against pipeline/api/content_writer's
    own real fixture data, e.g. "AZ @ PIT") — reused, not invented."""
    if not isinstance(game_id, str):
        return None
    parts = game_id.split("_")
    if len(parts) != 4:
        return None
    _, _, away, home = parts
    return f"{away} @ {home}"


def shape_content_draft_rows(
    capped_assignments: pd.DataFrame, tasty_six: dict, season: int, week: int,
    weekly: pd.DataFrame = None, schedules: pd.DataFrame = None, anthropic_api_key: str = None,
) -> list:
    """
    One dict per (surviving-the-cap) player-shelf placement, shaped to
    match the REAL nfl_content_drafts write schema exactly — confirmed
    directly against the live route's own Zod schema (a real, earlier
    attempt at this shape was REJECTED with a 400 by the real route;
    this version reflects that real error, not a second guess): player_
    id, event_id, shelf, writer_type, is_tasty_six, rank, player_name,
    team, opponent, matchup, odds, kickoff_utc, season, week, title,
    editorial_sentence, why_reasons ({pillar, stars, text, citation} —
    NOT {reason_text, source_fact_keys}, an earlier incomplete
    investigation's assumption), confidence_band, model_name,
    validation_passed, validation_issues, review_status.

    title, why_reasons, confidence_band, validation_passed are REQUIRED
    non-null by the real schema — a row with no real content (no
    anthropic_api_key given, or the Tasty Six writer call never ran) has
    title=None and would FAIL real validation if written; the caller
    (see nfl/api/index.py's endpoint) is responsible for filtering those
    out before calling write_content_draft_rows, not this function —
    shape_content_draft_rows still reports every real row, content or
    not, for accurate curation reporting.

    event_id = game_id (nflverse's own real per-game identifier, already
    unique, already on every scored row — no new identifier scheme
    invented). team = posteam. opponent = defteam. matchup is parsed
    from game_id directly (_matchup_from_game_id) — no schedules
    dependency for this one. odds = consensus_price_american.
    kickoff_utc has NO existing source on `weekly` at all — requires
    `schedules` passed in (redzone.add_kickoff_utc merges it on by
    game_id); omit `schedules` and kickoff_utc stays None for every row,
    same "missing input -> honest None, not a guess" fallback as every
    other optional parameter here.

    writer_type: "shelf_card" for regular rows, "tasty_six" for Tasty
    Six rows — the real DB upsert key includes this field.

    CONFIDENCE_BAND IS REQUIRED, NON-NULL ON EVERY ROW, regular or Tasty
    Six — a real, direct finding from the live schema (an earlier
    version left it None for regular rows and got rejected). Two
    SEPARATE band functions, not one reused across both: nfl_regular_
    row_confidence_band_for_score() (regular rows — spans the FULL real
    tpe_score population, always returns a real band, never None) vs.
    nfl_confidence_band_for_score() (Tasty Six — the approved, narrower
    tpe_score>=55 thresholds). See nfl_writer_common.py for why these
    can't share one function: they're calibrated against genuinely
    different real populations.

    CONTENT: regular (non-Tasty-Six) rows get shelves.py's deterministic
    story generators (Part A), reshaped into the real why_reasons shape
    via _deterministic_why_reasons — real, grounded, no LLM call.
    editorial_sentence stays None for these rows (MLB's own regular-
    card-has-no-editorial-sentence convention, reused). writer_type=
    "shelf_card", model_name=None, validation_passed=True, validation_
    issues=[] — the deterministic system has no separate pass/fail
    validation step of its own (it's grounded by construction, not
    validated after the fact the way LLM output is), so True/[] is the
    honest default, not a placeholder standing in for a real check.

    Tasty Six rows get a REAL call into generate_tasty_six_content.py's
    generate_nfl_tasty_six_draft() (Part C's actual LLM writer) — ONLY
    when `anthropic_api_key` is provided. Omit it (the default) and
    Tasty Six rows keep title/editorial_sentence/why_reasons as None/[]
    — the same honest "not generated yet" signal as before this task,
    not an exception — but STILL get a real confidence_band (required).
    model_name/validation_passed/validation_issues are threaded straight
    through from the real draft's own already-computed values (the real
    citation/numeric-grounding/star-consistency validation Part C's
    writer performs), never hardcoded. confidence_band is derived from
    tpe_score via nfl_confidence_band_for_score() (now-approved
    thresholds) before the writer call; a tpe_score outside its real
    [55, 100] range (shouldn't happen for a genuine Tasty Six row, which
    is gated at >=55 by construction) skips the writer call entirely
    rather than forcing a default band.

    review_status is always "pending_review" — never auto-approved, per
    explicit instruction; nothing here ever sets it to anything else.
    """
    if len(capped_assignments) == 0:
        return []
    tasty_lookup = {shelf: (row["player_id"] if row is not None else None) for shelf, row in tasty_six.items()}

    weekly_lookup = {}
    if weekly is not None and len(weekly) > 0:
        prepped = add_red_zone_trend_windows(weekly)
        if schedules is not None and len(schedules) > 0:
            prepped = add_kickoff_utc(prepped, schedules)
        weekly_lookup = {row["player_id"]: row for _, row in prepped.iterrows()}

    rows = []
    for _, r in capped_assignments[~capped_assignments["capped"]].iterrows():
        is_tasty_six = tasty_lookup.get(r["home_shelf"]) == r["player_id"]
        full_row = weekly_lookup.get(r["player_id"])

        event_id = team = opponent = matchup = kickoff_utc = None
        if full_row is not None:
            game_id = full_row.get("game_id")
            event_id = game_id
            team = full_row.get("posteam")
            opponent = full_row.get("defteam")
            matchup = _matchup_from_game_id(game_id)
            ku = full_row.get("kickoff_utc")
            if pd.notna(ku) and hasattr(ku, "isoformat"):
                kickoff_utc = ku.isoformat()

        title = editorial_sentence = None
        why_reasons = []
        writer_type = "shelf_card"
        model_name = None
        validation_passed = True
        validation_issues = []

        if is_tasty_six:
            writer_type = "tasty_six"
            # REQUIRED, non-null real string on every written row (found
            # directly against the live schema) — computed even when no
            # real LLM content ends up generated below, so a Tasty Six
            # row missing content (no anthropic_api_key, or a tpe_score
            # outside the real gated range) still has a real band, not a
            # blocker for review-queue display. Overwritten by the real
            # draft's own confidence_band below when a real call happens.
            confidence_band = nfl_regular_row_confidence_band_for_score(
                full_row.get("tpe_score") if full_row is not None else None,
            )
            if full_row is not None and anthropic_api_key:
                band = nfl_confidence_band_for_score(full_row.get("tpe_score"))
                if band is not None:
                    draft = generate_nfl_tasty_six_draft(full_row.to_dict(), r["home_shelf"], band, anthropic_api_key)
                    title = draft.get("title")
                    editorial_sentence = draft.get("editorial_sentence")
                    # Translation layer: Part C's internal shape (reason_
                    # text/source_fact_keys, matching card_writer_common.
                    # py's shared validators, which stay untouched) ->
                    # the real column's shape (text/citation) -- ONLY at
                    # this write-shaping boundary, not upstream.
                    raw_reasons = draft.get("why_reasons") or []
                    why_reasons = [
                        {
                            "pillar": wr.get("pillar"),
                            "stars": wr.get("stars"),
                            "text": wr.get("reason_text"),
                            "citation": wr.get("source_fact_keys"),
                        }
                        for wr in raw_reasons
                    ]
                    confidence_band = draft.get("confidence_band") or confidence_band
                    model_name = draft.get("model_name")
                    validation_passed = bool(draft.get("validation_passed", True))
                    validation_issues = draft.get("validation_issues") or []
        elif full_row is not None:
            story = _story_for_row(full_row, r["home_shelf"])
            title = story["headline"]
            why_reasons = _deterministic_why_reasons(full_row, r["home_shelf"], story)
            confidence_band = nfl_regular_row_confidence_band_for_score(full_row.get("tpe_score"))
        else:
            confidence_band = nfl_regular_row_confidence_band_for_score(None)

        rows.append({
            "player_id": r["player_id"],
            "event_id": event_id,
            "shelf": r["home_shelf"],
            "writer_type": writer_type,
            "is_tasty_six": is_tasty_six,
            "rank": int(r["rank"]),
            "player_name": r["player_name"],
            "team": team,
            "opponent": opponent,
            "matchup": matchup,
            "odds": r.get("consensus_price_american"),
            "kickoff_utc": kickoff_utc,
            "season": season,
            "week": week,
            "title": title,
            "editorial_sentence": editorial_sentence,
            "why_reasons": why_reasons,
            "confidence_band": confidence_band,
            "model_name": model_name,
            "validation_passed": validation_passed,
            "validation_issues": validation_issues,
            "review_status": "pending_review",
            # Already computed upstream (scoring.score_evidence_quality) --
            # pulled straight through, same full_row.get(...) pattern as
            # tpe_score above. None (not False) when full_row is missing or
            # never had it computed, so a genuinely unknown case doesn't
            # masquerade as a confirmed non-convergent read.
            "signal_convergence": full_row.get("signal_convergence") if full_row is not None else None,
            # Same already-computed-upstream, straight-through pattern as
            # signal_convergence directly above (scoring.score_signal_breach) --
            # None (not False) when full_row is missing, same reasoning.
            "signal_breach": full_row.get("signal_breach") if full_row is not None else None,
        })
    return rows


def shape_around_the_league_draft_rows(
    division_cards: dict, season: int, week: int, weekly: pd.DataFrame = None, schedules: pd.DataFrame = None,
) -> list:
    """
    One dict per Around the League card, shaped to the SAME real
    nfl_content_drafts write schema shape_content_draft_rows targets —
    same table, same route, same field set. NOT built by extending
    shape_content_draft_rows itself: that function is built around
    capped_assignments/tasty_six, a single-home-shelf-per-player model
    (apply_shelf_cap's whole point is picking ONE home shelf per
    player). Around the League is explicitly non-exclusive and parallel
    to that — the same player is expected to appear here AND on a
    primary shelf (no cross-shelf dedup, per the approved spec) — so it
    needs its own row-shaping path, not a branch bolted onto the
    single-shelf one.

    `division_cards`: build_around_the_league()'s own return shape —
    {"AFC East": [card, ...], ...}, already-finalized cards (rank/
    player_id/player_name/posteam/tpe_score/evidence_quality/
    signal_convergence/signal_breach/consensus_price_american/headline/
    evidence/...). Every division's cards get a row, including a
    division with zero cards (contributes zero rows, not a placeholder
    — no fill/backfill anywhere in this shelf, see shelves.py).

    shelf = the division name itself ("AFC East", not an existing shelf
    slug) — the real per-row identifier the approved spec asked for, no
    new schema column needed for this: the existing shelf column is
    real free text already, not a constrained enum on the write side
    (confirmed against the live route's own Zod schema — NflShelfSchema
    is z.string(), not z.enum([...]); the enum-like NflShelfId union
    only exists on the READ side, in the frontend's own display-order
    lookup — see the frontend investigation note below, not this write
    schema).

    is_tasty_six is always False and writer_type is always "shelf_card"
    — Around the League has no Tasty Six concept of its own (Tasty Six
    is a primary-shelf, single-pick-per-shelf idea; this shelf has no
    analogous "one flagship pick per division" requirement in the
    approved spec). editorial_sentence stays None always, same
    regular-card convention shape_content_draft_rows' non-Tasty-Six
    rows already use.

    CONTENT comes straight from the card's OWN already-computed
    headline/evidence (around_the_league_story, computed inside
    build_around_the_league via _finalize_cards) — NOT a fresh
    _story_for_row dispatch the way shape_content_draft_rows' regular
    rows work. There's no separate "home-assigned but uncapped, needs
    its own fresh story" population here the way _story_for_row's own
    docstring describes for the primary shelves (that gap exists
    because shelves.py's raw top-N and this module's home-assigned
    population diverge); Around the League has exactly one population
    (build_around_the_league's own top-6-per-division cut) and exactly
    one already-computed story per card, so reusing it directly is
    correct, not a shortcut.

    review_status is "pending_review" on every row, same as every other
    real row this module writes — but flagged explicitly here because
    it contradicts an assumption the approved spec itself stated
    ("Around the League does NOT need a separate review pass since it
     only re-slices already-approved picks"): CONFIRMED, directly
    against curate_home_shelves.py's own code and the real nfl_content_
    drafts table, that review_status lives on the CONTENT DRAFT ROW —
    i.e. per (player, shelf) pairing — not globally per player. A
    player already "approved" on a primary shelf's row does not carry
    that approval to a new row for the same player on their division
    shelf; there is no global per-player approval flag anywhere in this
    schema to inherit from. Every Around the League row is therefore a
    genuinely new pending_review row, same as any other new shelf row,
    and needs its own pass through Human Review before publishing —
    this is a real, confirmed finding, not a cautious default.
    """
    weekly_lookup = {}
    if weekly is not None and len(weekly) > 0:
        prepped = weekly
        if schedules is not None and len(schedules) > 0:
            prepped = add_kickoff_utc(prepped, schedules)
        weekly_lookup = {row["player_id"]: row for _, row in prepped.iterrows()}

    rows = []
    for division, cards in division_cards.items():
        for card in cards:
            full_row = weekly_lookup.get(card["player_id"])

            event_id = team = opponent = matchup = kickoff_utc = None
            if full_row is not None:
                game_id = full_row.get("game_id")
                event_id = game_id
                team = full_row.get("posteam")
                opponent = full_row.get("defteam")
                matchup = _matchup_from_game_id(game_id)
                ku = full_row.get("kickoff_utc")
                if pd.notna(ku) and hasattr(ku, "isoformat"):
                    kickoff_utc = ku.isoformat()
            else:
                # weekly/schedules weren't passed (or this player fell out
                # of weekly between build_around_the_league's own run and
                # this call) -- fall back to the card's own posteam rather
                # than leaving team None outright, same "use what's
                # actually available, don't guess further" spirit as every
                # other optional-input fallback in this module.
                team = card.get("posteam")

            rows.append({
                "player_id": card["player_id"],
                "event_id": event_id,
                "shelf": division,
                "writer_type": "shelf_card",
                "is_tasty_six": False,
                "rank": int(card["rank"]),
                "player_name": card["player_name"],
                "team": team,
                "opponent": opponent,
                "matchup": matchup,
                "odds": card.get("consensus_price_american"),
                "kickoff_utc": kickoff_utc,
                "season": season,
                "week": week,
                "title": card["headline"],
                "editorial_sentence": None,
                "why_reasons": _deterministic_why_reasons(card, division, card),
                "confidence_band": nfl_regular_row_confidence_band_for_score(card.get("tpe_score")),
                "model_name": None,
                "validation_passed": True,
                "validation_issues": [],
                "review_status": "pending_review",
                "signal_convergence": card.get("signal_convergence"),
                "signal_breach": card.get("signal_breach"),
            })
    return rows


def curate_nfl_shelves(
    weekly: pd.DataFrame, season: int, week: int, config: dict = CONFIG, shelves_config: dict = SHELVES_CONFIG,
    schedules: pd.DataFrame = None, anthropic_api_key: str = None, prior_assignments: dict = None,
) -> dict:
    """
    The full pipeline, steps 1-7: eligibility -> home-shelf assignment
    (real stickiness applied when prior_assignments is provided — see
    assign_home_shelves/_compute_sticky_assignment) -> max-6 cap ->
    Tasty Six -> content_drafts row shaping (real content included —
    see shape_content_draft_rows). Does NOT write anywhere — see
    write_content_draft_rows/write_shelf_signal_history_rows for that.

    `schedules`/`anthropic_api_key` thread straight through to shape_
    content_draft_rows — see its own docstring for what each unlocks
    (kickoff_utc; real Tasty Six LLM content) and what happens when
    either is omitted (honest None, not a guess or a skipped row).

    `prior_assignments`: the real, walked-back prior-week stickiness
    state (see build_prior_state_with_walkback) — omit it (the default)
    for the exact prior, non-sticky behavior (every player treated as
    first appearance).

    Returns {"home_assignments": DataFrame, "capped": DataFrame,
    "tasty_six": dict, "content_draft_rows": list,
    "shelf_signal_history_rows": list} — the last one is this week's
    real updated stickiness state, shaped and ready for write_shelf_
    signal_history_rows, covering every real home-assigned player (not
    just the ones with a written content-drafts row — next week's
    comparison needs every real qualifying signal, not just what made
    the cap).
    """
    home_assignments = assign_home_shelves(weekly, config, shelves_config, prior_assignments=prior_assignments)
    capped = apply_shelf_cap(home_assignments, config)
    tasty_six = select_tasty_six(capped, config)
    content_draft_rows = shape_content_draft_rows(
        capped, tasty_six, season, week, weekly=weekly, schedules=schedules, anthropic_api_key=anthropic_api_key,
    )
    shelf_signal_history_rows = shape_shelf_signal_history_rows(home_assignments, season, week)
    return {
        "home_assignments": home_assignments,
        "capped": capped,
        "tasty_six": tasty_six,
        "content_draft_rows": content_draft_rows,
        "shelf_signal_history_rows": shelf_signal_history_rows,
    }


# Fallback only for resolve_url_env — the real value comes from the
# LOVABLE_NFL_CONTENT_DRAFTS_WRITE_URL Vercel env var (confirmed real,
# same established pattern as LOVABLE_NFL_PRICE_HISTORY_WRITE_URL in
# nfl/api/index.py), not this constant. Kept only as resolve_url_env's
# required fallback argument. Drive-by fix: corrected to the real
# confirmed production domain (tastypickems.com, not the stale
# .lovable.app placeholder this constant was originally written with,
# before the real domain was confirmed during the write-connection task)
# — cosmetic only, since resolve_url_env's real env-var value already
# overrides this in practice.
DEFAULT_NFL_CONTENT_DRAFTS_WRITE_URL = "https://tastypickems.com/api/public/nfl-content-drafts-write"


def write_content_draft_rows(rows: list, secret: str, write_url: str = None):
    """
    Reuses lovable_forward.py's existing signed-POST machinery (the same
    HMAC/X-Signature pattern every other NFL webhook write already
    uses). `write_url`, if not passed explicitly, resolves from the real
    LOVABLE_NFL_CONTENT_DRAFTS_WRITE_URL env var via resolve_url_env
    (same pattern as every other confirmed-real NFL write route) rather
    than the DEFAULT_ constant above.
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from lovable_forward import forward_to_lovable, resolve_url_env

    url = write_url or resolve_url_env("LOVABLE_NFL_CONTENT_DRAFTS_WRITE_URL", DEFAULT_NFL_CONTENT_DRAFTS_WRITE_URL)
    return forward_to_lovable(rows, secret, url)


# ---------------------------------------------------------------------------
# Stickiness state persistence (nfl_shelf_signal_history) — Proposal 2's
# real read/write plumbing, confirmed real infrastructure (built by
# Lovable): player_id/season/week/home_shelf/qualifying_signals (jsonb)/
# pending_shelf/pending_run_count, unique on (player_id, season, week).
# ---------------------------------------------------------------------------

DEFAULT_NFL_SHELF_SIGNAL_HISTORY_WRITE_URL = "https://tastypickems.com/api/public/nfl-shelf-signal-history-write"
DEFAULT_NFL_SHELF_SIGNAL_HISTORY_READ_URL = "https://tastypickems.com/api/public/nfl-shelf-signal-history-read"


def shape_shelf_signal_history_rows(home_assignments: pd.DataFrame, season: int, week: int) -> list:
    """
    One row per home-assigned player (every ATTD-eligible qualifying
    player, capped or not — this table tracks ALL real qualifying
    signals, not just what survives shape_content_draft_rows' cap/
    content filtering, since next week's stickiness comparison needs
    every real candidate shelf's signal, not just the ones that got a
    written content-drafts row this week).
    """
    if len(home_assignments) == 0:
        return []
    rows = []
    for _, r in home_assignments.iterrows():
        rows.append({
            "player_id": r["player_id"],
            "season": season,
            "week": week,
            "home_shelf": r["home_shelf"],
            "qualifying_signals": r["qualifying_signals"],
            "pending_shelf": r.get("pending_shelf"),
            "pending_run_count": int(r.get("pending_run_count") or 0),
        })
    return rows


def write_shelf_signal_history_rows(rows: list, secret: str, write_url: str = None):
    """Same real signed-POST mechanism as write_content_draft_rows —
    see its own docstring."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from lovable_forward import forward_to_lovable, resolve_url_env

    url = write_url or resolve_url_env("LOVABLE_NFL_SHELF_SIGNAL_HISTORY_WRITE_URL", DEFAULT_NFL_SHELF_SIGNAL_HISTORY_WRITE_URL)
    return forward_to_lovable(rows, secret, url)


def read_shelf_signal_history(season: int, week: int, secret: str, read_url: str = None) -> dict:
    """
    One signed POST (body {"season": season, "week": week}), returns
    {"ok": bool, "error": str|None, "status_code": int|None, "rows":
    {player_id: {"home_shelf", "qualifying_signals", "pending_shelf",
    "pending_run_count"}}} for the ENTIRE real (season, week) — a real
    "no rows for this week" response (e.g. a week before this mechanism
    existed, or before any curation has run for it yet) is a genuine,
    valid outcome (rows={}), not an error.

    Reuses forward_to_lovable's exact sign+POST+capture-response
    mechanism for this READ call too, despite its "forward rows to
    write" naming — a deliberate reuse, not a misuse: the function is
    already fully generic (any JSON-serializable payload, list or dict
    — nothing inside it is actually list-specific at runtime), and this
    read route uses the IDENTICAL sign-the-raw-body-then-POST mechanic
    every write route already does. Building a second, near-duplicate
    function just to rename "rows" to "query" would be pure churn for
    zero real behavior difference.
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from lovable_forward import forward_to_lovable, resolve_url_env

    url = read_url or resolve_url_env("LOVABLE_NFL_SHELF_SIGNAL_HISTORY_READ_URL", DEFAULT_NFL_SHELF_SIGNAL_HISTORY_READ_URL)
    result = forward_to_lovable({"season": season, "week": week}, secret, url)
    if not result["success"]:
        return {"ok": False, "error": result["error"], "status_code": result["status_code"], "rows": {}}
    try:
        body = json.loads(result["response_body"])
    except (json.JSONDecodeError, TypeError):
        return {
            "ok": False, "error": f"non-JSON response body: {result['response_body']!r}",
            "status_code": result["status_code"], "rows": {},
        }
    rows_by_player = {row["player_id"]: row for row in body.get("shelf_signal_history", [])}
    return {"ok": True, "error": None, "status_code": result["status_code"], "rows": rows_by_player}


def build_prior_state_with_walkback(
    season: int, week: int, eligible_player_ids, secret: str, max_lookback: int = 3, read_url: str = None,
) -> dict:
    """
    Real bye-week handling, approved: "pause, don't reset" — a player
    with no row for the immediately prior week (a bye, or simply wasn't
    ATTD-eligible that week) should have their pending_shelf/pending_
    run_count carried forward from their MOST RECENT real row, not
    treated as first-appearance.

    APPROACH CHOSEN: bulk, WEEK-scoped iterative walk-back, not per-
    player round trips. The real read route only ever returns a whole
    week's data in one call (no player_id filtering exists) — so
    "walking back per player" here means walking back per WEEK instead,
    merging each week's bulk response into a growing lookup and keeping
    only the FIRST (=most recent) row found for each player_id, never
    letting an older week's find overwrite a more-recent one already
    located.

    EARLY-STOP OPTIMIZATION: stops as soon as every player in
    `eligible_player_ids` has been located — in the overwhelming normal
    case (no bye-affected players in this week's eligible pool at all),
    that's satisfied by the SINGLE week-1 call, zero extra round trips.
    Only players genuinely missing from week-1 (a real bye, or a gap)
    cost additional calls, and only up to max_lookback of them.

    BOUNDED at max_lookback=3 real weeks back and at week<=1 (a real
    season boundary, not an arbitrary cutoff) — generous enough to
    bridge a single real bye (NFL byes are always exactly one missed
    week, never back-to-back) with margin to spare, without scanning
    arbitrarily far back for a player with no real prior history at all
    (a rookie's first real game, e.g.), which would only ever find
    nothing at the cost of real extra calls every single week for every
    first-appearance player.

    Returns {player_id: {"home_shelf", "qualifying_signals",
    "pending_shelf", "pending_run_count", "found_at_week": int}} —
    found_at_week is real, exposed diagnostic info (how many real weeks
    back this player's row actually came from), not consumed by
    assign_home_shelves' own logic, useful for real validation/
    debugging (e.g. confirming a bye-gap case actually walked back
    correctly, not just landed on week-1 by coincidence).
    """
    merged = {}
    remaining = set(eligible_player_ids)
    lookback_week = week - 1
    attempts = 0
    while lookback_week >= 1 and attempts < max_lookback and remaining:
        result = read_shelf_signal_history(season, lookback_week, secret, read_url)
        if result["ok"]:
            for pid, row in result["rows"].items():
                if pid in remaining:
                    merged[pid] = {**row, "found_at_week": lookback_week}
                    remaining.discard(pid)
        lookback_week -= 1
        attempts += 1
    return merged
