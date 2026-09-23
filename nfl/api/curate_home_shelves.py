"""
NFL Shelf Curation & Tasty Six selection — assigns exactly one HOME
shelf per ATTD-eligible player across all seven shelves (shelves.py's
own per-shelf pools), applies the per-shelf cap (CONFIG["max_per_shelf"]
— secondary qualifications become tags, not duplicate placements), and
selects Tasty Six.

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
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
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
    ODDS_BANDS, add_red_zone_trend_windows, add_td_opportunity_history_lookup,
    add_whole_game_target_share_trend, build_around_the_league, eligible_pool, odds_band_eligible,
    odds_band_story, position_story, red_zone_story, section_title_for_shelf, td_opportunity_trend_for_row,
)
from story_archetype import resolve_archetype
from story_interrogation import interrogate_story

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "content_writer"))
from generate_tasty_six_content import generate_nfl_tasty_six_draft  # noqa: E402
from generate_nfl_shelf_card_content import CandidateGatedOut, generate_nfl_shelf_card_draft  # noqa: E402
from nfl_writer_common import nfl_confidence_band_for_score, nfl_regular_row_confidence_band_for_score  # noqa: E402

SHELF_ORDER = [
    "Red Zone Trends", "RB Trends", "WR Trends", "TE Trends",
    "ATTD +300-499", "ATTD +500-699", "ATTD +700+",
]
TREND_SHELVES = SHELF_ORDER[:4]
ODDS_SHELVES = SHELF_ORDER[4:]

# The Title-Case names above are the pipeline's INTERNAL identifiers — the
# keys for PRIMARY_SIGNAL_COL, _DETERMINISTIC_PILLAR_FOR_SHELF,
# _shelf_qualifying_pools, shelves.py's SECTION_TITLE_BY_SHELF / ODDS_BANDS
# / ODDS_BAND_ROLE_SIGNALS / NFL_SHELF_PERSONALITIES, _story_for_row's own
# branch + .split()[0], and _compute_sticky_assignment's comparisons.
# They deliberately stay Title-Case (several are human-facing — the LLM
# shelf personalities, prompt context; the odds bands carry range
# semantics).
#
# The FRONTEND (tastypickems: NflShelfId / NFL_SHELF_ORDER / isShelfId)
# validates the persisted `shelf` value against snake_case slugs. A
# Title-Case value fails isShelfId(), so rowToNflCard() drops it and the
# curated draft never renders — even after a human approves it.
#
# SHELF_SLUG bridges exactly that gap: it is applied ONLY at the two
# serialization points (the nfl_content_drafts row's `shelf`, and
# nfl_shelf_signal_history's `home_shelf`/`pending_shelf`), and reversed
# on read (see _shelf_unslug / read_shelf_signal_history) so the sticky-
# assignment loop keeps comparing internal Title-Case. Keep these values
# byte-identical to tastypickems' NFL_SHELF_ORDER.
#
# Around the League's 8 division strings ("AFC East" …) are a SEPARATE
# valid `shelf` domain the frontend expects Title-Case-with-spaces — they
# are not in this map and pass through _shelf_slug / _shelf_unslug
# unchanged via the .get(name, name) fallback.
SHELF_SLUG = {
    "Red Zone Trends": "red_zone_trends",
    "RB Trends": "rb_trends",
    "WR Trends": "wr_trends",
    "TE Trends": "te_trends",
    "ATTD +300-499": "attd_300_499",
    "ATTD +500-699": "attd_500_699",
    "ATTD +700+": "attd_700_plus",
}
_SLUG_TO_SHELF = {slug: name for name, slug in SHELF_SLUG.items()}


def _shelf_slug(name):
    """Internal Title-Case shelf name -> persisted snake_case slug. A
    division string (or anything already a slug / unknown) passes through
    untouched. None -> None (pending_shelf is nullable)."""
    if name is None:
        return None
    return SHELF_SLUG.get(name, name)


def _shelf_unslug(slug):
    """Reverse of _shelf_slug — persisted slug -> internal Title-Case name,
    for values read back out of nfl_shelf_signal_history so the sticky-
    assignment comparisons stay in the internal representation. Division
    strings / unknowns pass through. None -> None."""
    if slug is None:
        return None
    return _SLUG_TO_SHELF.get(slug, slug)


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
    # Soft ceiling, not a target count — a backstop against an unbounded
    # shelf if eligibility/matching logic ever breaks, not a number a
    # shelf is expected to reach. A shelf with 3 real qualifying players
    # shows 3; a shelf with 20+ shows 20 and the rest are tagged
    # capped=True (see apply_shelf_cap), same "tag, don't duplicate"
    # treatment as before, just at a much less binding threshold. Raised
    # from the original 6 once real data showed 6 was actively cutting
    # off genuinely-qualifying players on the three ATTD odds-band
    # shelves, not just capping overflow.
    "max_per_shelf": 20,
    # Decouples "how many players display on a shelf" (max_per_shelf,
    # above) from "how many get a bespoke Claude-written card" — a real,
    # confirmed production incident, not a hypothetical: raising max_per_
    # shelf from 6 to 20 tripled the ceiling on shape_content_draft_
    # rows()'s own sequential, blocking real-Claude-call-per-regular-row
    # loop (7 shelves x 20 = 140 vs. 7 x 6 = 42), and the FIRST real
    # curation run at the new cap (season=2026 week=1, 377 eligible
    # players) timed out at Vercel's 300s ceiling mid-generation, never
    # reaching the write step. Only the top shelf_card_llm_top_n players
    # per shelf, RANKED BY THE SAME rank apply_shelf_cap already assigned
    # (so it's always the top-displayed players, never an arbitrary
    # subset), get a real Claude call; every row beyond that gets the
    # same deterministic template fallback already used whenever
    # anthropic_api_key is unset or a specific call fails — a real,
    # already-exercised code path, not new content logic. 8, not the old
    # 6, deliberately: a little headroom above the previous effective
    # ceiling, not just a revert.
    "shelf_card_llm_top_n": 8,
    # Proposal 2, approved, PROVISIONAL — needs real-data validation
    # once the season is live. NOT enforced yet — see module docstring.
    "sticky_margin": 20.0,
    "sticky_run_count": 2,
    # Proposal 3, approved, PROVISIONAL — same as above.
    "tasty_six_tpe_threshold": 55.0,
    "tasty_six_evidence_threshold": 65.0,
    # Pass 3 capacity policy, LOCKED: within each real ATTD price band
    # (ODDS_BANDS), rank unique candidates by _candidate_best_gap
    # descending and keep the top this-fraction BY COUNT of that band's
    # own population, round() to the nearest integer. Chosen over a
    # single pooled threshold/ranking (which under-represents longshots
    # -- ATTD +700+ candidates structurally produce a smaller best_gap
    # than shorter-priced bands, confirmed against the real 316-
    # candidate population) and over a tiered absolute threshold per
    # band (which fixes the percentage skew only by starving the small
    # bands' absolute count, since they're ~37 of 316 candidates
    # combined). Within-band percentile ranking keeps each band's
    # representation proportional to its own real population by
    # construction, real-data-validated at ~207/316 selected with
    # ~87.9% ATTD +700+ share (vs. the real pool's own 88.3%) before
    # this was locked in as production policy. See _interrogate_
    # unique_candidates' own docstring for the full mechanism.
    "interrogation_top_pct_per_price_band": 0.65,
    # Pass 4 execution policy: how many of the Pass-3-selected candidates'
    # interrogate_story() calls run at once (ThreadPoolExecutor max_
    # workers), never how MANY candidates get called -- that's Pass 3's
    # own, frozen, 65%-per-band selection gate, entirely unaffected by
    # this number.
    #
    # FINALIZED against real constraints, not a guess. Real account
    # limits for Claude Sonnet 5 (confirmed against the Anthropic
    # Console): 10K requests/min, 10M input tokens/min, 2M output
    # tokens/min -- nowhere close to a bottleneck for a 205-call batch
    # at any concurrency considered here. The REAL constraint is
    # Vercel's 300s hard function ceiling (vercel.json maxDuration) --
    # shared with the pre-existing shelf-card writer loop that runs
    # AFTER this in the same invocation and has its own real, separate
    # history of exhausting that budget (see shelf_card_llm_top_n's own
    # comment). Interrogation must leave that loop real room, not just
    # fit itself.
    #
    # Sized against a real timing measurement, not the first one taken:
    # an initial 3-call sample (mean 17.22s) turned out to be inflated
    # by a real, now-fixed bug -- _shelf_neutral_candidate_evidence's
    # "proven heat" label tripped story_interrogation.py's own
    # confidence-escalation scan (the literal word "proven") on nearly
    # every call, forcing its existing internal retry and roughly
    # doubling wall time. Relabeled to "season-long heat" (see
    # _SHELF_NEUTRAL_SIGNAL_LABELS), re-measured clean: 5 real calls,
    # zero retries, mean 8.53s (range 7.17-9.42s).
    #
    # At mean=8.53s, ceil(205/c) waves x 8.53s: c=10 -> 179s (40% margin
    # under 300s), c=15 -> 119s (60% margin), c=20 -> 94s (69% margin).
    # 15 is chosen over 10 for a real cushion against a small (n=5)
    # sample's own uncertainty, and over 20+ for not taking more margin
    # than the writer loop's own real, still-uncharacterized need
    # justifies -- roughly half the 300s budget for Interrogation,
    # leaving the other half (~180s) for everything already downstream
    # of it. NOTE: no concurrency value makes the genuine WORST case
    # (every call hitting its own 60s REQUEST_TIMEOUT_SECONDS ceiling)
    # fit under 300s below c~52 -- concurrency tuning protects the
    # expected case, not a full pile-up; true worst-case safety is a
    # separate, structural question (tied to the still-dormant
    # shelves_to_process two-call split, tracked separately). REVISIT
    # if either the writer loop's own real budget need gets measured
    # (no granular timing checkpoint exists yet -- see this module's
    # own open-question notes) or real production telemetry at this
    # setting shows more/less headroom than modeled here.
    "interrogation_max_concurrency": 15,
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
    Only considers players who survived the per-shelf cap (capped=False) —
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

    Phase 2: shelf_name is now also threaded into odds_band_story's own
    band_label param, so a home-assigned +500-699/+700+ row gets that
    band's real role_signals pool here too, not always the +300-499
    default. WR/TE Trends' role_signals (target_share/target_share_
    trend) will be unavailable via this path specifically — this
    reconnection function's own weekly_lookup (see shape_content_draft_
    rows) is built from add_red_zone_trend_windows(weekly) alone, with
    no pbp threaded through, unlike shelves.py's own build_wr_trends/
    build_te_trends. Confirmed, not silently papered over: flagged in
    this task's own report as a known limitation of this specific write
    path, not a bug introduced here.
    """
    if shelf_name == "Red Zone Trends":
        return red_zone_story(row)
    if shelf_name in ("RB Trends", "WR Trends", "TE Trends"):
        return position_story(row, shelf_name.split()[0])
    return odds_band_story(row, shelf_name)


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
        "text": story["why_this_hits"],
        "citation": [source_key],
    }]


def _llm_why_reasons_for_write(raw_reasons) -> list:
    """
    Translation layer: an LLM writer's internal shape (reason_text/
    source_fact_keys, matching card_writer_common.py's shared
    validators, which stay untouched) -> the real nfl_content_drafts
    column shape (text/citation) — see _deterministic_why_reasons' own
    docstring for how that real shape was confirmed. Factored out here
    (NFL Content Generation V1, Part 1) since it's now used by BOTH the
    Tasty Six writer and the new regular shelf card writer — was inline
    only in the Tasty Six branch before Part 1 needed it a second time.

    REAL BUG FIX — confirmed production crash root cause: a Claude tool-
    call can return why_reasons in a shape validate_schema_shape()
    correctly flags (validation_passed=False), but generate_nfl_tasty_
    six_draft()/generate_nfl_shelf_card_draft() still return it as-is in
    the draft dict, and this function used to iterate it unconditionally
    regardless. A malformed why_reasons that came back as a plain STRING
    (instead of a list) crashed with "'str' object has no attribute
    'get'" — iterating a string yields characters, and .get() on a
    character raises exactly that. A list containing a non-dict item hit
    the same failure per-item. Both degrade to being skipped now, same
    "one bad response never crashes the whole batch" resilience already
    used one layer up (the try/except around the Claude call itself) —
    this closes the real gap where a validation FAILURE (not a raised
    exception) still reached this function untouched. Reproduced and
    confirmed against the real crash text before this fix; see this
    module's regression tests for the locked-in cases.
    """
    if not isinstance(raw_reasons, list):
        return []
    return [
        {
            "pillar": wr.get("pillar"),
            "stars": wr.get("stars"),
            "text": wr.get("reason_text"),
            "citation": wr.get("source_fact_keys"),
        }
        for wr in raw_reasons
        if isinstance(wr, dict)
    ]


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


_SHELF_NEUTRAL_SIGNAL_LABELS = (
    ("td_opportunity", "TD opportunity"),
    ("role_momentum", "role momentum"),
    ("situation", "situation"),
    ("role_trend", "role trend"),
    # "season-long heat", not "proven heat" -- deliberately reworded,
    # confirmed via a real measurement (Pass 4 timing investigation):
    # the literal word "proven" here tripped story_interrogation.py's
    # own confidence-escalation scan (CONFIDENCE_ESCALATING_LANGUAGE's
    # \bproven\b) on nearly every real call, not because the model made
    # an overconfident claim, but because its own prose echoed this
    # field's name back. The scan itself is correct and untouched (a
    # real word-boundary match on a real banned word, exactly as
    # designed) -- the fix belongs in the input, not the guardrail.
    # "season-long" pairs with "emerging" the same way nfl_tension.py's
    # own change-type story_angle already does for this exact field
    # pair ("the season-long ... read" vs. "the recent (emerging) read"
    # -- see that module's `change` branch) -- reused terminology, not
    # invented fresh here. This label is internal-evidence-record only
    # (Interrogation's own input), never reader-facing -- the real,
    # published "Proven Heat" display label elsewhere (shelves.py's own
    # role_signals) is untouched and unaffected.
    ("proven_heat", "season-long heat"),
    ("emerging_heat", "emerging heat"),
)


def _shelf_neutral_candidate_evidence(full_row) -> tuple[str | None, list[str] | None]:
    """Candidate-level headline/supporting_evidence for Interrogation,
    built ONLY from full_row's own already-scored fields — the same six
    real signals find_tension() itself reads (nfl_tension.py's own
    `_INTERNAL_SIGNALS` plus role_trend/proven_heat/emerging_heat).
    Deliberately mechanical, not a "preferred shelf" pick: no shelf name
    or shelf-specific story (_story_for_row / red_zone_story /
    position_story / odds_band_story) is read here, so the same
    candidate produces the same evidence regardless of which shelf(s) it
    appears on or in what order they're encountered (see this function's
    caller for the real bug this closes).

    Numeric, not narrated -- this is Interrogation's own internal
    evidence record, not reader-facing shelf-card prose, so citing raw
    0-100 scores directly is the established convention at this layer
    (story_interrogation.py's own supporting_evidence/judgment fields do
    the same; distinct from the shelf-card WRITER's separate "never
    narrate a raw field" rule, which governs reader-facing text only).

    Missing/NaN fields are dropped, not guessed at -- honest gap, same
    convention as _real() elsewhere in this pipeline. Returns (None,
    None) if full_row is None or no real field survives.
    """
    if full_row is None:
        return None, None
    readings = []
    for key, label in _SHELF_NEUTRAL_SIGNAL_LABELS:
        value = full_row.get(key)
        if value is None:
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if value != value:  # NaN
            continue
        readings.append((label, value))
    if not readings:
        return None, None
    headline = "This week's real scored signals: " + ", ".join(
        f"{label} {value:.1f}/100" for label, value in readings
    ) + "."
    supporting_evidence = [f"{label}: {value:.1f}/100" for label, value in readings]
    supporting_evidence.extend(_evidentiary_basis_readings(full_row))
    return headline, supporting_evidence


# Real fields find_tension() itself already reads from the SAME full_row
# (_evidence_strength/_uncertainty_note in nfl_tension.py) -- already
# present on the real weekly DataFrame in production (build_nfl_writer_
# candidate() is a straight `dict(row)`, confirmed by reading it; no new
# RPC needed for THIS field to reach here). Absent from the narrow
# get_nfl_current_week_candidates RPC used for this session's own funnel
# MEASUREMENT work (a real, separate finding, documented in Pass 3) --
# that gap is in the measurement harness's own data source, not in what
# full_row actually carries during a real curation run.
_EVIDENTIARY_BASIS_LABELS = (
    ("evidence_quality", "evidence quality"),
    ("td_opportunity_completeness", "TD opportunity completeness"),
    ("role_momentum_completeness", "role momentum completeness"),
    ("situation_completeness", "situation completeness"),
    ("defensive_matchup_completeness", "defensive matchup completeness"),
)


def _evidentiary_basis_readings(full_row) -> list:
    """
    Real evidence_quality/completeness readings from full_row, formatted
    as additional supporting_evidence entries -- closes a real,
    measured gap: a real 36-candidate sample of Interrogation calls
    built WITHOUT this (and without market_data/prior_history) came back
    36/36 UNRESOLVED, because the input gave the model nothing to judge
    how much basis the six scored signals actually stand on. This
    doesn't fabricate a "sample_size" (no real games-played-style count
    field exists on full_row) -- it gives Interrogation the real,
    already-computed confidence/completeness numbers behind the six
    signals themselves, the same honest substitute this pipeline's own
    "sample_size / evidentiary basis" framing calls for. Missing/NaN
    fields dropped, same honest-gap convention as the six signal
    readings above. Returns [] (not None) when full_row is None or
    nothing survives -- always safe to .extend() onto an existing list.
    """
    if full_row is None:
        return []
    readings = []
    for key, label in _EVIDENTIARY_BASIS_LABELS:
        value = full_row.get(key)
        if value is None:
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if value != value:  # NaN
            continue
        readings.append(f"{label}: {value:.0f}% (how much basis this week's scored signals actually stand on)")
    return readings


_MARKET_DATA_MIN_CHECKPOINT_GAP_HOURS = 24.0


def _market_data_for_candidate(price_history_rows: list, max_checkpoints: int = 5) -> dict | None:
    """
    Reduces one candidate's REAL nfl_price_history poll rows (market_
    value.read_price_history()'s own unreduced row shape -- player_id/
    poll_timestamp/consensus_price_american, already a real, live,
    established data source in this pipeline, not a new one) into the
    market_data shape interrogate_story()'s own input contract expects:
    {"current_attd_odds": str, "odds_history": [{"timestamp": str,
    "odds": str}, ...]}.

    current_attd_odds: the single most RECENT real poll's price
    (max poll_timestamp), formatted with an explicit sign ("+450"/"-150"
    -- consensus_price_american itself is a plain int, per market_value.
    py's own snapshot_scoring_inputs()).

    odds_history: real checkpoints, NOISE-FILTERED, not every raw poll
    or every raw price change. Two real problems, confirmed against a
    real 36-candidate measurement, not assumed:
      1. Most polls repeat the same price (multiple polls/day with no
         real movement) -- burying the real signal (does the price
         actually move) without adding information. Collapsed by
         de-duplicating consecutive identical prices, same as before.
      2. That de-duplication ALONE still let same-day/same-few-hours
         churn through as if it were meaningful movement -- real
         examples from that measurement showed swings like +190 ->
         +196 -> +537 -> +1000 -> +3000 within ~4 hours on a single
         day, which reads as thin-liquidity noise from a still-settling
         early market, not genuine market disagreement, and was a real,
         confirmed driver of an inflated FAILS rate once this function
         started feeding Interrogation real market_data.

    Fix for (2): a MINIMUM ELAPSED TIME between any two INCLUDED
    checkpoints (_MARKET_DATA_MIN_CHECKPOINT_GAP_HOURS = 24h). Walked
    backward from the current (most recent) price -- always kept -- and
    each earlier checkpoint is only included once it's at least the
    threshold BEFORE the previously-included (more recent) one; anything
    closer than that in real elapsed time is treated as the same
    observation and dropped, not averaged or specially reinterpreted.
    24h, not a shorter window, because the real polling cadence in this
    data is itself sub-daily (multiple real polls per day, confirmed
    directly against real cached data) -- a same-day gap is exactly the
    noise case (1) already exists to filter within a single price level,
    just not across DIFFERENT price levels reached the same day. A full
    day is the smallest gap that reliably separates "the market moved
    and held" from "the market wobbled for a few hours."

    Capped at the most recent `max_checkpoints` checkpoints AFTER this
    filtering, oldest-first, current price last -- still favors recent
    (now genuinely separated) movement over the full multi-week history,
    since "did the market react" is a near-term question.

    Returns None for no rows / nothing with a real player_id+price+
    timestamp -- honest absence, matching this function's own caller's
    existing "no market_data provided" default, never a fabricated
    price.
    """
    if not price_history_rows:
        return None
    real_rows = [
        r for r in price_history_rows
        if r.get("poll_timestamp") and r.get("consensus_price_american") is not None
    ]
    if not real_rows:
        return None
    real_rows.sort(key=lambda r: r["poll_timestamp"])

    def _fmt(price) -> str:
        price = int(price)
        return f"+{price}" if price >= 0 else str(price)

    def _parse_ts(ts: str):
        try:
            return datetime.fromisoformat(ts)
        except ValueError:
            return None

    distinct = []
    for r in real_rows:
        price = r["consensus_price_american"]
        if not distinct or distinct[-1][1] != price:
            distinct.append((r["poll_timestamp"], price))

    # Noise filter: walk backward from the most recent distinct price,
    # keeping an earlier one only once it's genuinely >= the minimum gap
    # before the last KEPT checkpoint. A timestamp that fails to parse
    # is kept as its own checkpoint rather than silently dropped or
    # crashing -- honest inclusion of real, if malformed, data over a
    # guess at its real age.
    filtered = [distinct[-1]]
    last_kept_ts = _parse_ts(distinct[-1][0])
    for ts, price in reversed(distinct[:-1]):
        parsed = _parse_ts(ts)
        if last_kept_ts is None or parsed is None:
            filtered.append((ts, price))
            last_kept_ts = parsed
            continue
        gap_hours = (last_kept_ts - parsed).total_seconds() / 3600.0
        if gap_hours >= _MARKET_DATA_MIN_CHECKPOINT_GAP_HOURS:
            filtered.append((ts, price))
            last_kept_ts = parsed
    filtered.reverse()

    checkpoints = filtered[-max_checkpoints:]

    return {
        "current_attd_odds": _fmt(checkpoints[-1][1]),
        "odds_history": [{"timestamp": ts, "odds": _fmt(price)} for ts, price in checkpoints],
    }


def _real(value) -> float | None:
    """None for missing/NaN, the real float value otherwise -- same
    honest-None convention as nfl_tension.py's own _real (duplicated,
    not imported: this module's selection gate must not depend on or
    reach into Tension's own module, see _candidate_best_gap)."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN != NaN


def _candidate_best_gap(full_row) -> float:
    """The raw magnitude of the strongest real relationship among a
    candidate's own scored signals -- REIMPLEMENTS (does not import)
    nfl_tension.py's own three internal gap computations (divergence:
    market_value_score vs. the blended td_opportunity/role_momentum/
    situation read; contradiction: the largest pairwise gap among those
    same three; change: role_trend vs. role_momentum, and emerging_heat
    vs. proven_heat). find_tension() itself never returns this number,
    only descriptive text built from it, so this is duplicated at this
    layer on purpose -- this selection gate is a resource-allocation
    concern (Pass 3), not an editorial one, and must not import from or
    modify nfl_tension.py (Tension/Eyebrow Test stays frozen, unaffected
    by candidate selection, per this pass's own scope). This
    reimplementation was validated against find_tension()'s real
    behavior across the real 316-candidate population during Pass 3's
    own investigation before being approved as the ranking signal.

    Missing/NaN inputs are dropped, never guessed -- same honest-gap
    convention as _shelf_neutral_candidate_evidence. Returns 0.0 (never
    None) when full_row is None or no real gap is computable at all --
    a candidate with too little data to compute any gap ranks lowest
    within its band, it never crashes or silently opts itself out of
    grouping.
    """
    if full_row is None:
        return 0.0
    mv = _real(full_row.get("market_value_score"))
    td = _real(full_row.get("td_opportunity"))
    rm = _real(full_row.get("role_momentum"))
    sit = _real(full_row.get("situation"))
    rm_trend = _real(full_row.get("role_trend"))
    proven = _real(full_row.get("proven_heat"))
    emerging = _real(full_row.get("emerging_heat"))

    internal = {"td_opportunity": td, "role_momentum": rm, "situation": sit}
    internal_present = {k: v for k, v in internal.items() if v is not None}

    gaps = []
    if mv is not None and internal_present:
        internal_avg = sum(internal_present.values()) / len(internal_present)
        gaps.append(abs(mv - internal_avg))
    if len(internal_present) >= 2:
        names = list(internal_present)
        pairs = [(a, b) for i, a in enumerate(names) for b in names[i + 1:]]
        gaps.append(max(abs(internal_present[a] - internal_present[b]) for a, b in pairs))
    for level_val, trend_val in ((rm, rm_trend), (proven, emerging)):
        if level_val is not None and trend_val is not None:
            gaps.append(abs(trend_val - level_val))

    return max(gaps) if gaps else 0.0


def _price_band_for_row(full_row) -> str | None:
    """Real ATTD price band (ODDS_BANDS) for one candidate's own real
    consensus_price_american -- a genuine candidate-level property (the
    real market price on this player), never derived from or dependent
    on which shelf(s) the candidate happens to be placed on. None when
    full_row is missing or its price doesn't resolve to a real band
    (honest gap, not a guess) -- callers group these under their own
    "unbanded" bucket rather than dropping them silently."""
    if full_row is None:
        return None
    price = _real(full_row.get("consensus_price_american"))
    if price is None:
        return None
    for label, lo, hi in ODDS_BANDS:
        if price >= lo and (hi is None or price <= hi):
            return label
    return None


def _select_candidates_for_interrogation(candidate_rows: list, top_pct: float) -> tuple:
    """
    Pass 3's LOCKED capacity policy: within each real price band, rank
    candidates by _candidate_best_gap DESCENDING and keep the top
    `top_pct` of that band's own population BY COUNT (round() to the
    nearest integer -- e.g. a 279-candidate band at top_pct=0.65 keeps
    round(279 * 0.65) = 181, not "gap >= some pooled value"). Deliberately
    NOT a single pooled ranking/threshold across all candidates: real-
    data modeling (Pass 3) showed pooled ranking structurally under-
    represents ATTD +700+ candidates (their best_gap runs lower on
    average -- noisier internal-signal comparisons vs. the shorter-
    priced bands' cleaner market-divergence gaps), and a tiered
    ABSOLUTE threshold per band only inverts that skew by starving the
    small bands' absolute count instead. Ranking WITHIN each band and
    taking a uniform top_pct keeps every band's representation
    proportional to its own real population by construction.

    price_band is a real, intrinsic property of the candidate (its own
    market price) -- this is NOT a shelf-placement policy, and does not
    reopen the leak Pass 2.1 closed (no shelf name or shelf-specific
    story enters this function; see _price_band_for_row).

    Deterministic tie-break: candidates with an equal best_gap at a
    band's cutoff boundary are ordered by `key` (not by input order),
    so selection never depends on capped_assignments' own row order --
    same shelf-order-invariance discipline Pass 2.1 established for the
    Interrogation input itself, now extended to whether Interrogation
    runs at all.

    candidate_rows: list of {"key": (player_id, event_id), "price_band":
    str | None, "best_gap": float}. Returns (selected_keys, not_selected_
    keys) -- two disjoint sets whose union is every key in candidate_rows.
    """
    by_band = {}
    for row in candidate_rows:
        by_band.setdefault(row["price_band"], []).append(row)

    selected = set()
    for band_rows in by_band.values():
        ordered = sorted(band_rows, key=lambda r: (-r["best_gap"], r["key"]))
        # Round-half-UP, deliberately not Python's builtin round() (banker's
        # rounding: round(6.5) == 6, not 7) -- a band size landing exactly
        # on a half-integer keep-count must round to the MORE-selected
        # side, never silently under-select. math.floor(x + 0.5) is exact
        # for these small positive magnitudes (band sizes here are at most
        # a few hundred).
        keep_count = math.floor(len(ordered) * top_pct + 0.5)
        selected.update(row["key"] for row in ordered[:keep_count])

    all_keys = {row["key"] for row in candidate_rows}
    return selected, all_keys - selected


def _interrogate_one_candidate(
    key: tuple, weekly_lookup: dict, anthropic_api_key: str,
    max_retries: int = 2, retry_backoff_seconds: float = 1.0,
    price_history_by_player: dict = None,
) -> dict:
    """
    Runs ONE candidate's Interrogation call in isolation -- the unit of
    work Pass 4's ThreadPoolExecutor dispatches concurrently (see
    _interrogate_unique_candidates below). NEVER raises: every real
    failure, a genuine exception or interrogate_story() returning None,
    resolves to a real {"interrogation_status": ..., "result": ...}
    dict -- the SAME three-state contract and the SAME meaning per
    state as the original sequential loop this replaces. Concurrency
    changes HOW this runs, never WHAT a given outcome means.

    BLIND, BOUNDED RETRY on a None result. interrogate_story() itself
    doesn't distinguish "real API/network failure" from "a content-rule
    violation that persisted after its own one internal retry" in what
    it returns -- both come back as a bare None (see that function's
    own docstring). This function deliberately does NOT reach into or
    modify interrogate_story()/call_claude_with_tool() to make that
    distinction; that would touch code Pass 4 is scoped to leave alone.
    Instead: up to max_retries additional attempts at THIS layer,
    exponential backoff (retry_backoff_seconds, doubling each attempt)
    between them. A transient network/rate-limit failure is likely to
    recover on retry; a persisted content-rule violation likely won't --
    the cost of a wasted retry in that case is small and bounded, and
    staying outside interrogate_story()'s own code is the actual point.

    Exhausting every retry without a real result -- or any exception,
    caught here rather than left to propagate into the calling
    ThreadPoolExecutor Future -- resolves to interrogation_status:
    "failed", the same state a single failed attempt already produced
    before this pass. This candidate's own failure never crashes the
    batch and never leaves its key unset; the caller always gets a real
    dict back for every key it submits.

    price_history_by_player: {player_id: [real nfl_price_history row,
    ...]}, optional. Reduced via _market_data_for_candidate() into this
    candidate's own real market_data -- see _interrogate_unique_
    candidates' own docstring for why this exists (a real, measured
    input-starvation finding, not a speculative enrichment) and what's
    still NOT wired (a live per-request fetch in api/index.py). None by
    default -- market_data stays None, same as before this existed.
    """
    player_id, event_id = key
    full_row = weekly_lookup.get(player_id)
    headline, supporting_evidence = _shelf_neutral_candidate_evidence(full_row)
    market_data = None
    if price_history_by_player is not None:
        market_data = _market_data_for_candidate(price_history_by_player.get(player_id))
    story_input = {
        "intelligence_family": "nfl_picks",
        "entity": {
            "type": "player",
            "player_id": player_id,
            "player_name": full_row.get("player_name") if full_row is not None else None,
        },
        "headline": headline,
        "hero_metric": None,
        "time_window": None,
        "sample_size": None,
        "supporting_evidence": supporting_evidence,
        "related_players": [],
    }

    attempt = 0
    backoff = retry_backoff_seconds
    while True:
        try:
            result = interrogate_story(story_input, anthropic_api_key, prior_history=None, market_data=market_data)
        except Exception as e:
            print(
                f"[shape_content_draft_rows] interrogate_story raised for {key!r} "
                f"(attempt {attempt + 1}/{max_retries + 1}): {e!r}",
                flush=True,
            )
            result = None

        # A real, well-formed result with signal_verdict itself absent
        # (confirmed live: 2 of 36 real calls in one measurement session
        # -- challenge/confirmation/judgment all present, signal_verdict
        # missing from the model's own tool-call response despite being
        # schema-required) is NOT a usable "complete" result -- it's a
        # response gap, the same real-attempt-no-usable-outcome case a
        # None result already is. Treating it as "complete" would let it
        # silently fall through the STOP gate into legacy-tier treatment
        # as if Interrogation ran meaningfully, when it didn't produce
        # the one field the gate actually depends on. Retried the same
        # as a None result, not given special-case handling.
        if result is not None and result.get("signal_verdict"):
            return {"interrogation_status": "complete", "result": result}

        if attempt >= max_retries:
            if result is not None:
                print(
                    f"[shape_content_draft_rows] {key!r} got a real response with signal_verdict missing "
                    f"after {max_retries} retries -- recording a 'failed' status, not silent legacy "
                    f"fallthrough and not defaulting to a passed-scrutiny result",
                    flush=True,
                )
            else:
                print(
                    f"[shape_content_draft_rows] {key!r} exhausted {max_retries} retries with no usable "
                    f"result -- recording a 'failed' status, not defaulting to a passed-scrutiny result",
                    flush=True,
                )
            return {"interrogation_status": "failed", "result": None}

        attempt += 1
        time.sleep(backoff)
        backoff *= 2


def _interrogate_unique_candidates(
    capped_assignments: pd.DataFrame, weekly_lookup: dict, anthropic_api_key: str = None, config: dict = CONFIG,
    price_history_by_player: dict = None,
) -> dict:
    """
    Candidate-level Interrogation — called ONCE per (player_id, event_id),
    NOT once per placement. This is the real fix for a real, measured bug:
    shape_content_draft_rows' own loop below produces one row per
    (surviving-the-cap) player-shelf PLACEMENT, and calling Interrogation
    from inside that loop — the same pattern find_tension() already uses,
    correctly, for placement-level Tension — would call it once per shelf
    a candidate appears on. Confirmed directly against a real live-data
    measurement, not assumed: a real candidate can appear on 3 shelves at
    once (its price-band shelf + its own position trend shelf + Red Zone
    Trends, which is position-agnostic), and every one of 316 real
    survivors in that measurement cleared all three simultaneously — so
    calling Interrogation per placement would have run it ~3x more than
    necessary for no real reason. The underlying candidate truth (does
    the signal survive scrutiny) does not change depending on which
    shelf is asking; only Tension's framing of it does. See this
    project's own architecture note: "Truth belongs to the candidate.
    Editorial framing belongs to the placement. Deduplicate scrutiny,
    not storytelling."

    (player_id, event_id) is the real candidate identity key, not bare
    player_id — adopted from where this exact convention is already
    established in this codebase (nfl-results.ts's own nflResultKey;
    get_published_nfl_shelf_picks' own PARTITION BY ..., player_id,
    event_id, writer_type), not invented fresh here. event_id = game_id,
    the same real field this function's own caller already derives a few
    lines below in its main loop.

    SHELF-NEUTRAL INPUT, not a "representative placement": the `headline`
    / `supporting_evidence` fed into Interrogation's own input contract
    come from _shelf_neutral_candidate_evidence(full_row) — a function
    that takes ONLY full_row, never a shelf name, and reads the same six
    already-scored fields find_tension() itself reads (td_opportunity,
    role_momentum, situation, role_trend, proven_heat, emerging_heat).
    This is a deliberate fix for a real bug an earlier version of this
    function had: it built `story` from _story_for_row(full_row,
    representative_shelf), where `representative_shelf` was whichever
    placement happened to be first-encountered while grouping — making
    the candidate-level Interrogation call's own input depend on shelf
    iteration order (or, with a "preferred"/sorted shelf, on which shelf
    a fixed priority list picks — the same leak, just hidden behind
    determinism instead of iteration order). Truth belongs to the
    candidate; a shelf-specific story (red_zone_story / position_story /
    odds_band_story) is placement-level editorial framing and must never
    determine what candidate-level Interrogation is told. See this
    module's own acceptance test (test_curate_home_shelves.py) proving
    the same candidate produces a byte-for-byte identical Interrogation
    input regardless of placement order.

    INPUT-COMPLETENESS FIX (real, measured): a real 36-candidate sample
    of Interrogation calls built with market_data/prior_history=None and
    no evidentiary-basis context came back 36/36 UNRESOLVED, regardless
    of tension_type or price band -- not a gate-tuning problem, a real
    input-starvation bug (see story_interrogation.py's own docstring for
    why: SURVIVES/FAILS both require real independent evidence the input
    simply never supplied). Two real fixes, both wired here:
      - _evidentiary_basis_readings() (inside _shelf_neutral_candidate_
        evidence itself) adds real evidence_quality/completeness
        readings to supporting_evidence -- already present on full_row
        in a real curation run (find_tension() already reads them from
        the same row), no new data source needed for this one.
      - _market_data_for_candidate() reduces price_history_by_player's
        real per-player nfl_price_history rows (market_value.
        read_price_history()'s own established, live RPC) into
        interrogate_story()'s expected market_data shape.
    price_history_by_player: {player_id: [real price-history row, ...]}
    -- optional, defaults to None (matches this module's own convention
    for every other optional enrichment: absence degrades honestly, no
    crash, no fabrication). NOT YET wired to a live per-request fetch
    inside api/index.py -- that's the one real remaining integration
    step; this function is ready for it, correctly no-ops without it.
    prior_history stays None: the only real "prior" data available here
    is price history, already captured under market_data's own
    odds_history -- populating both with the same underlying data would
    be redundant, not a second real source, so prior_history is left
    honestly unpopulated rather than duplicated for its own sake.

    OPEN QUESTION, DESIGN DECIDED (NOT IMPLEMENTED), CONFIRMED DORMANT
    — shelves_to_process (api/index.py) can split ONE logical curation
    run across two separate HTTP invocations, each calling shape_
    content_draft_rows (and therefore this function) fresh, with no
    shared state between them. This function's own dedup is correct
    WITHIN one call, but has no cross-call memory — if shelves_to_
    process is ever activated, this function would re-run its full
    grouping + Pass 3 selection + interrogate_story() calls
    independently in EACH split call, over the same candidate
    population each time (full duplication, not partial overlap), with
    a real risk of the same candidate getting two independently-
    generated, potentially-divergent signal_verdicts across the two
    calls. See the full write-up, the FUTURE SPLIT-EXECUTION INVARIANT,
    and the decided (not yet implemented) precompute-step design in
    api/index.py's own shelves_to_process docstring (decided 2026-09-23:
    a dedicated precompute step, not a read-through cache — see that
    block for why single-flight/claim semantics were investigated and
    rejected as unnecessary complexity). Confirmed dormant as of this
    writing (re-confirmed 2026-09-23; no caller anywhere sets shelves_
    to_process) — does not block this function's own current behavior
    or Pass 4's concurrency work, both of which correctly target
    today's real, single-invocation call pattern.

    Pass 3 SELECTION GATE, before any Interrogation call is made: not
    every unique candidate is interrogated. config["interrogation_top_
    pct_per_price_band"] (see CONFIG's own comment) keeps only the top
    fraction of EACH real price band's own candidates, ranked by
    _candidate_best_gap — see that function and _select_candidates_for_
    interrogation for the real mechanism and why it's within-band, not a
    single pooled threshold or a tiered absolute one. This is a resource-
    allocation decision, not an epistemic one.

    Pass 4 EXECUTION, after selection, before any state is returned:
    selected candidates' interrogate_story() calls run CONCURRENTLY,
    bounded by config["interrogation_max_concurrency"] (see CONFIG's own
    comment on why that default is provisional), via ThreadPoolExecutor
    -- see _interrogate_one_candidate for the per-candidate isolation/
    retry unit of work, and this function's own body for how completed
    futures get mapped back to the right candidate key regardless of
    completion order. This changes ONLY how the selected set's calls
    execute -- which candidates are selected (above) and what each
    interrogation_status means (below) are both unaffected.

    Returns {(player_id, event_id): {"interrogation_status": str,
    "result": dict | None}} — a THREE-state contract, deliberately not a
    bare result-or-None, so a caller can never confuse "we chose not to
    scrutinize this" with "scrutiny found a problem" or with "scrutiny
    should have run but didn't":
      - "complete": interrogate_story() returned a real result.
        result is that dict (has a real signal_verdict).
      - "not_selected": the selection gate above did not choose this
        candidate this run. result is always None. NOT an epistemic
        verdict of any kind — no attempt was made, and this placement's
        `signal_verdict` must stay genuinely absent, never defaulted or
        fabricated.
      - "failed": the candidate WAS selected and interrogate_story() was
        called, but never produced a usable result on any attempt --
        either it returned None every time (its own one internal
        content-rule retry, PLUS Pass 4's own bounded blind retry at the
        dispatch layer for a real API/network failure -- see
        _interrogate_one_candidate), OR it returned a real, well-formed
        result with signal_verdict itself absent (confirmed live: a real
        response can have challenge/confirmation/judgment all present
        with signal_verdict missing despite being schema-required --
        also retried, same as a None result, and also resolves to
        "failed" if it never appears). result is None either way. Never
        silently treated as "complete" just because SOME response came
        back -- the field the STOP gate actually depends on is what
        makes a result usable, not response presence alone. Distinct
        from "not_selected": an attempt (several, in fact) genuinely
        happened and did not produce a usable result.
    A key MISSING from this dict entirely (as opposed to present with
    any of the three statuses above) is a different, fourth case this
    function's own caller (below) must treat as a loud, visible contract
    failure ("missing_unexpectedly") — every real placement is expected
    to find its candidate's key present with one of the three statuses
    above, even when that status is "not_selected".
    """
    if not anthropic_api_key:
        # No key -- the same "no bespoke content this run" degradation
        # every other real LLM call in this module already has. Nothing
        # to look up later; every placement's own fan-out lookup skips
        # the hard-failure check in this same case (see the main loop
        # below), matching this early return exactly.
        return {}

    candidates = capped_assignments[~capped_assignments["capped"]]
    candidate_keys = set()
    for _, r in candidates.iterrows():
        full_row = weekly_lookup.get(r["player_id"])
        if full_row is None:
            continue
        candidate_keys.add((r["player_id"], full_row.get("game_id")))

    candidate_rows_for_selection = [
        {
            "key": key,
            "price_band": _price_band_for_row(weekly_lookup.get(key[0])),
            "best_gap": _candidate_best_gap(weekly_lookup.get(key[0])),
        }
        for key in candidate_keys
    ]
    top_pct = config["interrogation_top_pct_per_price_band"]
    selected_keys, not_selected_keys = _select_candidates_for_interrogation(candidate_rows_for_selection, top_pct)

    print(
        f"[shape_content_draft_rows] {len(candidate_keys)} unique (player_id, event_id) candidates "
        f"derived from {len(candidates)} real placements "
        f"({len(candidates) - len(candidate_keys)} placement(s) share a candidate already covered by "
        f"another placement's interrogation call) -- selection gate (top {top_pct:.0%} of each real "
        f"price band, ranked by best_gap) keeps {len(selected_keys)}, does not select {len(not_selected_keys)}",
        flush=True,
    )

    results = {}
    for key in not_selected_keys:
        results[key] = {"interrogation_status": "not_selected", "result": None}

    # Pass 4: bounded-concurrency dispatch. Each selected candidate's
    # call is fully isolated -- _interrogate_one_candidate never raises,
    # always returns the real {"interrogation_status": ..., "result": ...}
    # dict, so one candidate's failure can never cancel or corrupt any
    # other's in-flight call (ThreadPoolExecutor's own per-Future
    # exception isolation is a second, redundant layer of the same
    # guarantee, not the only one). future_to_key is built at SUBMISSION
    # time and resolved by dict lookup on the Future object itself, never
    # by list position or completion order -- results complete in
    # whatever order the real network calls happen to finish, and that
    # order must never determine which candidate a result gets attached
    # to. This governs EXECUTION ONLY: which candidates are selected
    # (Pass 3) and what each interrogation_status means (the three-state
    # contract) are both unchanged.
    if selected_keys:
        max_workers = max(1, min(config["interrogation_max_concurrency"], len(selected_keys)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_key = {
                executor.submit(
                    _interrogate_one_candidate, key, weekly_lookup, anthropic_api_key,
                    price_history_by_player=price_history_by_player,
                ): key
                for key in selected_keys
            }
            for future in as_completed(future_to_key):
                key = future_to_key[future]
                try:
                    results[key] = future.result()
                except Exception as e:
                    # Defense in depth -- _interrogate_one_candidate is
                    # designed to never raise, but if something truly
                    # unexpected still escapes it, this candidate alone
                    # resolves to "failed" rather than this exception
                    # propagating and taking down every other in-flight
                    # future's own result.
                    print(
                        f"[shape_content_draft_rows] unexpected error resolving future for {key!r}: {e!r} -- "
                        f"recording a 'failed' status, not letting this candidate's failure affect any other",
                        flush=True,
                    )
                    results[key] = {"interrogation_status": "failed", "result": None}

    return results


def shape_content_draft_rows(
    capped_assignments: pd.DataFrame, tasty_six: dict, season: int, week: int,
    weekly: pd.DataFrame = None, schedules: pd.DataFrame = None, anthropic_api_key: str = None,
    history_weekly: pd.DataFrame = None, pbp: pd.DataFrame = None, config: dict = CONFIG,
    shelves_to_process: list = None, avoid_headlines: list = None, avoid_opening_phrases: list = None,
    price_history_by_player: dict = None,
) -> dict:
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

    CONTENT: regular (non-Tasty-Six) rows are genuinely two-path, same
    shape as Tasty Six below — this paragraph used to claim "no LLM
    call, ever," which stopped being true once generate_nfl_shelf_card_
    draft() shipped; corrected here rather than left stale. A row whose
    OWN rank on its shelf is <= config["shelf_card_llm_top_n"] gets a
    real call into generate_nfl_shelf_card_content.py's generate_nfl_
    shelf_card_draft() (when `anthropic_api_key` is provided); a row
    ranked beyond that cutoff — or any row when no key is provided, or
    one whose real call raised — falls back to shelves.py's
    deterministic story generators (Part A), reshaped via
    _deterministic_why_reasons. Both paths are real, grounded content;
    only the SOURCE (bespoke prose vs. a data-driven template) differs.
    THE CUTOFF EXISTS BECAUSE OF A REAL INCIDENT, not a cost-saving
    guess: this function's own per-row loop makes each real Claude call
    sequentially and blocking, and raising config["max_per_shelf"] from
    6 to 20 (a separate, deliberate change — see that key's own
    comment) tripled the ceiling on how many of those calls one curation
    run could make (7 shelves x 20 = 140 vs. 7 x 6 = 42). The first real
    run at the new cap (season=2026 week=1, 377 eligible players) timed
    out at Vercel's 300s ceiling mid-generation, confirmed via Vercel's
    own logs (it had already logged a successful market-value fetch,
    then produced nothing further). shelf_card_llm_top_n decouples the
    two concerns: every genuinely qualifying player still DISPLAYS (up
    to max_per_shelf), but only the top shelf_card_llm_top_n per shelf
    — ranked by the exact same `rank` apply_shelf_cap already assigned,
    so it's always the top-displayed players, never an arbitrary subset
    — cost a real sequential API call.
    editorial_sentence stays None for these rows (MLB's own regular-
    card-has-no-editorial-sentence convention, reused). writer_type=
    "shelf_card"; model_name/validation_passed/validation_issues follow
    whichever path actually ran for that row — real draft-reported
    values for a bespoke call, the deterministic system's own True/[]
    honest default (no separate pass/fail step to report) otherwise.

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

    Phase 2 (structured Role Signals evidence layer): why_this_hits,
    confidence_band (already existed), td_opportunity_trend, role_
    signals, and section_title are now computed UNIFORMLY for every
    row, Tasty Six or not — unlike title/editorial_sentence/why_reasons
    (which stay genuinely two-path: LLM-written for Tasty Six, deter-
    ministic otherwise), these five are all real, deterministic facts
    about the player/pillar, not narrative — there's no reason a Tasty
    Six row's role_signals should be any less real than a regular row's.
    `story` is therefore computed once, up front, whenever full_row
    exists, regardless of is_tasty_six.

    `pbp`: FIXED, previously a known gap (flagged in the Phase 2 Role
    Signals report) — this function's own weekly_lookup used to be
    built from add_red_zone_trend_windows(weekly) alone, with no `pbp`
    threaded through, unlike shelves.py's own build_wr_trends/build_te_
    trends. Now, when `pbp` is provided, add_whole_game_target_share_
    trend(weekly, pbp) runs on the SAME full-table prep step (the exact
    same batch-merge shape build_wr_trends/build_te_trends already use
    it for — no per-row adjustment needed), so WR/TE Trends' target_
    share/target_share_trend role_signal candidates become eligible via
    this live path too, matching the fixture path. Omit `pbp` (the
    default) and behavior is UNCHANGED from before this fix — those two
    candidates are simply ineligible, same graceful degradation as
    every other missing-input case in this module, never an error.

    history_weekly: real per-week history (nfl/scripts/player_redzone_
    weekly.csv shape) for td_opportunity_trend — see shelves.
    add_td_opportunity_history_lookup. Omit it and every row's
    td_opportunity_trend degrades to a length-1 list (this week only).

    Two-call split (NFL Weekly Lifecycle, runtime ceiling fix): raising
    the real success rate of the per-row Claude calls (fixing the NaN-
    crash and max_tokens=1024 truncation bugs) made the OLD single-call
    architecture's own timing worse, not better -- a genuinely-completed
    generation takes longer than one that fails fast or gets cut off
    early, and a real production run already timed out at 300s with real
    per-call data showing ~13s of slack, none left once failures became
    successes. shelves_to_process/avoid_headlines/avoid_opening_phrases
    exist to let ONE curation run be split across two HTTP calls, each
    handling a disjoint subset of the 7 primary shelves, each getting
    its own fresh 300s ceiling.

    shelves_to_process: optional list of home_shelf names (the internal
    Title-Case names, e.g. "Red Zone Trends" -- NOT the write-schema
    slug) to shape a real row for. None (the default) processes every
    row, unchanged from before this parameter existed. A row whose
    home_shelf is NOT in this list is skipped ENTIRELY -- not shaped, not
    counted, not returned -- so the OTHER call in a two-call split is
    fully responsible for it; there is no partial/deterministic-fallback
    row emitted here for an out-of-scope shelf. Around the League rows
    are shaped by a completely separate function (shape_around_the_
    league_draft_rows) and are never subject to this scoping at all --
    they're cheap (no LLM calls) and always processed in full by both
    calls in a split.

    avoid_headlines/avoid_opening_phrases: optional SEED lists for the
    real cross-batch variety state (see generated_titles/
    generated_opening_phrases below) -- pass the FIRST call's own
    returned generated_titles/generated_opening_phrases here on the
    SECOND call of a split, so a title generated by call 1 still counts
    as "already used" for call 2's own real LLM calls, preserving the
    real anti-repetition mechanism across the two-call boundary instead
    of silently resetting it per call. None (the default, and every call
    site before this parameter existed) starts both lists empty, exactly
    the original single-call behavior.

    price_history_by_player: {player_id: [real nfl_price_history row,
    ...]}, optional -- threaded straight through to _interrogate_unique_
    candidates' own real market_data wiring (see that function's own
    docstring for the real, measured input-starvation finding this
    closes). None (the default, and every call site before this
    parameter existed) leaves market_data unpopulated, unchanged from
    before this existed. NOT fetched by this function itself -- the
    caller owns getting real price-history rows (market_value.
    read_price_history(), already established elsewhere in this
    pipeline), same "caller fetches, this function only consumes"
    convention `weekly`/`schedules`/`pbp` already follow.

    Returns {"rows": [...], "generated_titles": [...], "generated_
    opening_phrases": [...]} -- NOT a bare list anymore (a real, deliberate
    return-shape change from before this parameter existed; the one real
    caller, curate_nfl_shelves, unpacks this and re-exposes "rows" under
    its own unchanged "content_draft_rows" key so every existing caller
    of curate_nfl_shelves keeps working without touching this function
    directly). generated_titles/generated_opening_phrases are this
    call's OWN full accumulated lists (seed + everything generated this
    call) -- exactly what the caller should pass as the next call's own
    seed.
    """
    if len(capped_assignments) == 0:
        return {"rows": [], "generated_titles": list(avoid_headlines or []), "generated_opening_phrases": list(avoid_opening_phrases or [])}
    tasty_lookup = {shelf: (row["player_id"] if row is not None else None) for shelf, row in tasty_six.items()}

    weekly_lookup = {}
    if weekly is not None and len(weekly) > 0:
        prepped = add_red_zone_trend_windows(weekly)
        if pbp is not None and len(pbp) > 0:
            prepped = add_whole_game_target_share_trend(prepped, pbp)
        if schedules is not None and len(schedules) > 0:
            prepped = add_kickoff_utc(prepped, schedules)
        weekly_lookup = {row["player_id"]: row for _, row in prepped.iterrows()}
    history_lookup = add_td_opportunity_history_lookup(history_weekly)

    # Candidate-level Interrogation — ONE call per (player_id, event_id),
    # before any per-placement processing below, not one call per shelf
    # placement. See _interrogate_unique_candidates' own docstring for
    # the full reasoning. Tension (find_tension, inside generate_nfl_
    # shelf_card_draft below) is UNCHANGED — still runs once per
    # placement, with its own per-shelf lens, exactly as it does today;
    # this dict only makes the shared candidate-level result reachable
    # at that call site, it does not change what Tension does with it.
    interrogation_by_candidate = _interrogate_unique_candidates(
        capped_assignments, weekly_lookup, anthropic_api_key, config,
        price_history_by_player=price_history_by_player,
    )

    rows = []
    # REAL CROSS-BATCH VARIETY STATE (NFL Content Generation V1, Part 1)
    # -- grown across EVERY row in this one curation run, Tasty Six and
    # regular cards alike, and fed back into every subsequent real LLM
    # call as avoid_headlines/avoid_opening_phrases (see nfl_shelf_card_
    # prompt.py's own docstring for the real angle-level repetition this
    # closes). Shared across writer types deliberately, not one list per
    # type -- a Tasty Six title and a regular card's title can collide
    # just as easily as two regular cards' can, and they're generated in
    # the same batch either way. Titles only (not why_reasons prose),
    # same real scope MLB's own content_draft_generation_live.py already
    # established this pattern at.
    generated_titles = list(avoid_headlines or [])
    generated_opening_phrases = list(avoid_opening_phrases or [])

    for _, r in capped_assignments[~capped_assignments["capped"]].iterrows():
        if shelves_to_process is not None and r["home_shelf"] not in shelves_to_process:
            continue
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

        # Candidate-level Interrogation fan-out -- O(1) lookup of the
        # ONE shared result this placement's candidate already got from
        # _interrogate_unique_candidates above, never recomputed here.
        # interrogation_result is the FULL three-state wrapper dict
        # ({"interrogation_status": ..., "result": ...}), not just its
        # "result" unwrapped -- find_tension() itself (see nfl_tension.py's
        # own _interrogation_status_for) needs the status tag to tell
        # "not_selected"/"failed" apart from "complete", and to tell all
        # three apart from a placement with no interrogation_result at
        # all. Passed straight through for every real status; "complete"
        # carries a real signal_verdict, "not_selected"/"failed" carry
        # result=None but a real, distinct status label -- neither is a
        # signal_verdict, and NEITHER may be defaulted or fabricated into
        # one. A key MISSING entirely (full_row real, a key was expected,
        # anthropic_api_key was provided, but the dict has no entry at
        # all -- not even a "not_selected"/"failed" status) is the real,
        # fourth, loudly-logged case: a genuine contract failure, never
        # silently treated as if this candidate passed scrutiny.
        interrogation_result = None
        if full_row is not None and anthropic_api_key:
            interrogation_key = (r["player_id"], event_id)
            if interrogation_key not in interrogation_by_candidate:
                print(
                    f"[shape_content_draft_rows] CONTRACT FAILURE (missing_unexpectedly): no "
                    f"interrogation entry at all for {interrogation_key!r} on shelf "
                    f"{r['home_shelf']!r} -- every placement with a real full_row must have a "
                    f"candidate-level interrogation entry (complete/not_selected/failed) when "
                    f"anthropic_api_key is provided. Proceeding with interrogation_result=None for "
                    f"this placement only; NOT defaulting to a passed-scrutiny result.",
                    flush=True,
                )
            else:
                interrogation_result = interrogation_by_candidate[interrogation_key]

        title = editorial_sentence = None
        # Editorial Voice Spec, "Find the Tension" addition -- the real
        # Story-tier field generate_nfl_shelf_card_draft() now returns as
        # draft["story"]. Named `story_text` here, NOT `story`, to avoid
        # colliding with this function's own PRE-EXISTING `story` local
        # (the _story_for_row() templated dict two lines below, unrelated
        # and much older -- headline/why_this_hits/role_signals). The
        # write-row's own JSON key stays "story" (see rows.append below);
        # only this Python identifier is renamed to keep both real,
        # unrelated things distinct in the same function scope.
        story_text = None
        why_reasons = []
        writer_type = "shelf_card"
        model_name = None
        validation_passed = True
        validation_issues = []

        story = _story_for_row(full_row, r["home_shelf"]) if full_row is not None else None
        why_this_hits = story["why_this_hits"] if story is not None else None
        role_signals = story["role_signals"] if story is not None else []
        td_opportunity_trend = td_opportunity_trend_for_row(full_row, history_lookup) if full_row is not None else []
        section_title = section_title_for_shelf(r["home_shelf"])
        # NFL Phase D, Part 1 -- story_archetype.py's resolver was fully
        # built and validated earlier but never actually called anywhere in
        # this pipeline until now (confirmed zero hits before this change).
        # Wired in here because this is the one place that already has both
        # the shelf (r["home_shelf"], the exact internal Title-Case name
        # resolve_archetype/resolve_editorial_lens expect -- e.g. "ATTD
        # +700+", not the slugged write-schema value) and the enriched
        # per-player row (full_row) resolve_archetype's own docstring
        # requires. resolve_archetype() itself is untouched -- only called.
        # Same "missing input -> honest default, never a guess" convention
        # as every other full_row-derived field above: a row with no
        # enriched data degrades to GENERIC/no position, matching exactly
        # what resolve_archetype() itself already returns when it has
        # nothing real to work with, rather than inventing a fallback here.
        archetype_result = (
            resolve_archetype(r["home_shelf"], full_row)
            if full_row is not None
            else {"archetype": "GENERIC", "position_variant": None, "confidence": None}
        )

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
                    # REAL RESILIENCE FIX (NFL Content Generation V1, Part
                    # 1) -- confirmed, previously-flagged gap (see this
                    # function's own module docstring history): a Claude
                    # API failure, rate limit, or malformed response for
                    # ONE Tasty Six pick used to raise straight out of
                    # this loop and abort curation for the ENTIRE week,
                    # including every other shelf's already-successful
                    # rows. Now caught and degraded to this row's own
                    # deterministic content instead -- one bad LLM call
                    # never sinks the whole real batch, matching MLB's own
                    # content_draft_generation_live.py's per-candidate
                    # try/except discipline. avoid_headlines is threaded
                    # through here too -- previously never passed at all
                    # despite generate_nfl_tasty_six_draft() already
                    # supporting it, a real, live gap this fix also closes.
                    try:
                        draft = generate_nfl_tasty_six_draft(
                            full_row.to_dict(), r["home_shelf"], band, anthropic_api_key,
                            avoid_headlines=generated_titles,
                        )
                    except Exception as e:
                        print(
                            f"[shape_content_draft_rows] tasty_six LLM generation failed for "
                            f"player_id={r['player_id']!r} shelf={r['home_shelf']!r}: {e!r} -- "
                            f"falling back to this row's deterministic content instead",
                            flush=True,
                        )
                        title = story["headline"] if story is not None else None
                        why_reasons = _deterministic_why_reasons(full_row, r["home_shelf"], story) if story is not None else []
                    else:
                        title = draft.get("title")
                        editorial_sentence = draft.get("editorial_sentence")
                        why_reasons = _llm_why_reasons_for_write(draft.get("why_reasons"))
                        confidence_band = draft.get("confidence_band") or confidence_band
                        model_name = draft.get("model_name")
                        validation_passed = bool(draft.get("validation_passed", True))
                        validation_issues = draft.get("validation_issues") or []
        elif full_row is not None:
            confidence_band = nfl_regular_row_confidence_band_for_score(full_row.get("tpe_score"))
            # r["rank"] <= shelf_card_llm_top_n: only the top-displayed
            # players on this shelf get a real, sequential Claude call —
            # see CONFIG["shelf_card_llm_top_n"]'s own comment for the
            # real incident (a 5-minute Vercel timeout) this closes.
            # Everyone else takes the exact SAME branch as "no
            # anthropic_api_key at all" below, not a new code path —
            # the deterministic template is already real, grounded
            # content, just not bespoke prose.
            if anthropic_api_key and r["rank"] <= config["shelf_card_llm_top_n"]:
                # Same per-row resilience discipline as the Tasty Six
                # branch above -- a bad Claude call for one regular card
                # (of which there are many more per batch than Tasty Six
                # picks) degrades to THIS row's own existing deterministic
                # template, never the whole week's curation run.
                try:
                    draft = generate_nfl_shelf_card_draft(
                        full_row.to_dict(), r["home_shelf"], confidence_band, anthropic_api_key,
                        avoid_headlines=generated_titles, avoid_opening_phrases=generated_opening_phrases,
                        interrogation_result=interrogation_result,
                    )
                except CandidateGatedOut as e:
                    # signal_verdict FAILS/UNRESOLVED for this candidate
                    # (nfl_tension.find_tension's own STOP GATE) -- NOT a
                    # generation failure to fall back from. This placement
                    # gets no row at all: no bespoke content, no
                    # deterministic template either. No backfill/promotion
                    # of the next-ranked candidate to fill the gap --
                    # explicitly out of scope for this pass; a shelf
                    # simply displays fewer real cards when this fires.
                    # Logged loudly so a gated-out placement is visible in
                    # QA, never a silent gap.
                    verdict = None
                    if interrogation_result and interrogation_result.get("interrogation_status") == "complete":
                        verdict = (interrogation_result.get("result") or {}).get("signal_verdict")
                    print(
                        f"[shape_content_draft_rows] SKIPPING row -- player_id={r['player_id']!r} "
                        f"shelf={r['home_shelf']!r} gated out by signal_verdict={verdict!r}: {e!r}",
                        flush=True,
                    )
                    continue
                except Exception as e:
                    print(
                        f"[shape_content_draft_rows] shelf_card LLM generation failed for "
                        f"player_id={r['player_id']!r} shelf={r['home_shelf']!r}: {e!r} -- "
                        f"falling back to this row's deterministic content instead",
                        flush=True,
                    )
                    draft = None
                if draft is not None:
                    title = draft.get("title")
                    story_text = draft.get("story")
                    why_reasons = _llm_why_reasons_for_write(draft.get("why_reasons"))
                    confidence_band = draft.get("confidence_band") or confidence_band
                    model_name = draft.get("model_name")
                    validation_passed = bool(draft.get("validation_passed", True))
                    validation_issues = draft.get("validation_issues") or []
                    if draft.get("opening_phrase"):
                        generated_opening_phrases.append(draft["opening_phrase"])
                else:
                    title = story["headline"]
                    why_reasons = _deterministic_why_reasons(full_row, r["home_shelf"], story)
                    # story_text stays None -- the deterministic template
                    # (Part A) has no Tension Object behind it, so there is
                    # no real Story-tier text to fall back to here. Honest
                    # None, not why_this_hits repurposed as a stand-in (see
                    # this task's own module docstring on why why_this_hits
                    # is evidence-tier, not story-tier, by design).
            else:
                title = story["headline"]
                why_reasons = _deterministic_why_reasons(full_row, r["home_shelf"], story)
        else:
            confidence_band = nfl_regular_row_confidence_band_for_score(None)

        if title:
            generated_titles.append(title)

        rows.append({
            "player_id": r["player_id"],
            "event_id": event_id,
            # Serialization boundary: persist the frontend's snake_case slug
            # (isShelfId / NflShelfId), not the internal Title-Case name.
            # Division strings pass through unchanged. See SHELF_SLUG.
            "shelf": _shelf_slug(r["home_shelf"]),
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
            # Editorial Voice Spec, "Find the Tension" addition -- the real
            # Story-tier text (see the story_text local's own comment
            # above for why this Python identifier differs from the JSON
            # key). None whenever this row fell back to the deterministic
            # template (no LLM call made, or one failed) -- honest
            # absence, not why_this_hits or editorial_sentence repurposed.
            "story": story_text,
            "why_reasons": why_reasons,
            "confidence_band": confidence_band,
            "why_this_hits": why_this_hits,
            "td_opportunity_trend": td_opportunity_trend,
            "role_signals": role_signals,
            "section_title": section_title,
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
            # NFL Odds by Sportsbook, Phase 1 -- straight through from
            # market_value.py's snapshot_scoring_inputs (threaded here via
            # CURATION_MARKET_VALUE_COLUMNS -> merge_market_value_and_
            # rescore -> weekly -> weekly_lookup -> full_row, the SAME
            # already-computed-upstream pattern signal_convergence/
            # signal_breach use directly above). isinstance check, not a
            # None check: a player with no live odds at all gets NaN
            # (float) here after the left merge, same as every other
            # merged column's own missing-value shape -- isinstance
            # correctly treats that as "no real book_odds" without a
            # pd.isna() call on a value that might genuinely be a list
            # (ambiguous/erroring on a real list, not just a style choice).
            "book_odds": (
                full_row.get("book_odds")
                if full_row is not None and isinstance(full_row.get("book_odds"), list)
                else None
            ),
            # NFL Phase D, Part 1 -- straight through from archetype_result
            # above (resolve_archetype()'s own output, untouched here).
            "archetype": archetype_result["archetype"],
            "position_variant": archetype_result["position_variant"],
        })
    return {"rows": rows, "generated_titles": generated_titles, "generated_opening_phrases": generated_opening_phrases}


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
                # card already carries a real confidence_band (build_around_
                # the_league -> _finalize_cards -> _confidence_band_for_row,
                # the exact same nfl_regular_row_confidence_band_for_score
                # call this used to duplicate here) -- read straight through
                # rather than recomputing an identical value a second time.
                "confidence_band": card["confidence_band"],
                # Phase 2 fields — all three already computed by build_
                # around_the_league's own _finalize_cards call, read
                # straight through, same pattern as confidence_band above.
                "why_this_hits": card["why_this_hits"],
                "td_opportunity_trend": card["td_opportunity_trend"],
                "role_signals": card["role_signals"],
                "section_title": card["section_title"],
                "model_name": None,
                "validation_passed": True,
                "validation_issues": [],
                "review_status": "pending_review",
                "signal_convergence": card.get("signal_convergence"),
                "signal_breach": card.get("signal_breach"),
                # NFL Odds by Sportsbook, Phase 1 -- from full_row (weekly_
                # lookup, above), same source and same isinstance-not-None
                # shape shape_content_draft_rows' own regular rows use --
                # NOT from `card`, since build_around_the_league's own
                # _finalize_cards never computed book_odds (it's threaded
                # through weekly/CURATION_MARKET_VALUE_COLUMNS, not
                # anything shelves.py's own card-building touches).
                "book_odds": (
                    full_row.get("book_odds")
                    if full_row is not None and isinstance(full_row.get("book_odds"), list)
                    else None
                ),
            })
    return rows


def curate_nfl_shelves(
    weekly: pd.DataFrame, season: int, week: int, config: dict = CONFIG, shelves_config: dict = SHELVES_CONFIG,
    schedules: pd.DataFrame = None, anthropic_api_key: str = None, prior_assignments: dict = None,
    history_weekly: pd.DataFrame = None, pbp: pd.DataFrame = None,
    shelves_to_process: list = None, avoid_headlines: list = None, avoid_opening_phrases: list = None,
) -> dict:
    """
    The full pipeline: eligibility -> home-shelf assignment (real
    stickiness applied when prior_assignments is provided — see
    assign_home_shelves/_compute_sticky_assignment) -> per-shelf cap ->
    Tasty Six -> content_drafts row shaping (real content included —
    see shape_content_draft_rows), PLUS Around the League (division
    re-slice), run in parallel off the SAME raw `weekly` this function
    itself received — not chained off capped/tasty_six at all. Does NOT
    write anywhere — see write_content_draft_rows/write_shelf_signal_
    history_rows for that.

    AROUND THE LEAGUE, CONFIRMED-BEFORE-WIRING (previously built and
    tested — test_shelves.py exercises build_around_the_league directly
    — but never actually called from here, the exact same shape of gap
    the archetype resolver had before it was wired in): build_around_
    the_league(weekly, config, history_weekly) needs the SAME eligible-
    pool input assign_home_shelves starts from, confirmed directly
    against its own body (_build_around_the_league_division calls
    odds_band_eligible(weekly, 300, None) on this same `weekly`) — never
    apply_shelf_cap's per-player-one-home-shelf output, since Around the
    League is explicitly non-exclusive (the same player is expected to
    appear here AND on a primary shelf). It also takes shelves_config
    (shelves.py's own CONFIG, config["shelf_size"]=6), NOT this
    function's own `config` (curate_home_shelves.py's CONFIG,
    config["max_per_shelf"]=20) — passing the wrong one would KeyError,
    since curate_home_shelves.py's CONFIG has no "shelf_size" key. That
    mismatch is also the real reason max_per_shelf's 20-card ceiling and
    select_tasty_six never apply to Around the League automatically:
    it's a structurally separate config and a separate call, not a flag
    to opt out of the primary-shelf pipeline's own limits.

    `schedules`/`anthropic_api_key`/`history_weekly`/`pbp` thread
    straight through to shape_content_draft_rows — see its own
    docstring for what each unlocks (kickoff_utc; real Tasty Six LLM
    content; td_opportunity_trend; WR/TE Trends' target_share/target_
    share_trend role_signal candidates) and what happens when any is
    omitted (honest None/length-1/ineligible, not a guess or a skipped
    row). `schedules`/`history_weekly` also thread through to the Around
    the League calls below, same honest-degradation shape (kickoff_utc/
    td_opportunity_trend stay None/[] without them, never a guess).

    `prior_assignments`: the real, walked-back prior-week stickiness
    state (see build_prior_state_with_walkback) — omit it (the default)
    for the exact prior, non-sticky behavior (every player treated as
    first appearance). Around the League has no stickiness concept of
    its own — it's a fresh re-slice every run, same as every other
    build_around_the_league call site (test_shelves.py included).

    Returns {"home_assignments": DataFrame, "capped": DataFrame,
    "tasty_six": dict, "content_draft_rows": list,
    "shelf_signal_history_rows": list, "around_the_league_rows": list,
    "generated_titles": list, "generated_opening_phrases": list}
    — "shelf_signal_history_rows" is this week's real updated stickiness
    state, shaped and ready for write_shelf_signal_history_rows,
    covering every real home-assigned player (not just the ones with a
    written content-drafts row — next week's comparison needs every
    real qualifying signal, not just what made the cap).
    "around_the_league_rows" is a FLAT list (like content_draft_rows,
    not keyed by division) — a caller writing both to nfl_content_drafts
    concatenates the two lists; this function does not write anywhere
    itself, so it doesn't do that concatenation (see nfl/api/index.py's
    curate-and-write-drafts endpoint, the one real caller, for that step
    — CONFIRMED it was a fixed `result["content_draft_rows"]` key access
    there, not a generic "write everything returned" loop, so adding
    this key alone would have silently written nothing without also
    updating that call site).

    shelves_to_process/avoid_headlines/avoid_opening_phrases: thread
    straight through to shape_content_draft_rows — see its own docstring
    for the real two-call-split runtime fix these unlock. "content_draft_
    rows" stays a bare flat list either way (shape_content_draft_rows'
    own "rows" key, unpacked here) — every existing caller of THIS
    function keeps working unchanged; only shape_content_draft_rows'
    own return shape changed. "generated_titles"/"generated_opening_
    phrases" are the real, full accumulated lists (seed + this call's own
    real generations) — a caller splitting one curation across two HTTP
    calls returns these to the client after call 1, then passes them
    back in as avoid_headlines/avoid_opening_phrases on call 2.
    """
    home_assignments = assign_home_shelves(weekly, config, shelves_config, prior_assignments=prior_assignments)
    capped = apply_shelf_cap(home_assignments, config)
    tasty_six = select_tasty_six(capped, config)
    shaped = shape_content_draft_rows(
        capped, tasty_six, season, week, weekly=weekly, schedules=schedules, anthropic_api_key=anthropic_api_key,
        history_weekly=history_weekly, pbp=pbp, config=config,
        shelves_to_process=shelves_to_process, avoid_headlines=avoid_headlines,
        avoid_opening_phrases=avoid_opening_phrases,
    )
    shelf_signal_history_rows = shape_shelf_signal_history_rows(home_assignments, season, week)
    division_cards = build_around_the_league(weekly, config=shelves_config, history_weekly=history_weekly)
    around_the_league_rows = shape_around_the_league_draft_rows(
        division_cards, season, week, weekly=weekly, schedules=schedules,
    )
    return {
        "home_assignments": home_assignments,
        "capped": capped,
        "tasty_six": tasty_six,
        "content_draft_rows": shaped["rows"],
        "shelf_signal_history_rows": shelf_signal_history_rows,
        "around_the_league_rows": around_the_league_rows,
        "generated_titles": shaped["generated_titles"],
        "generated_opening_phrases": shaped["generated_opening_phrases"],
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
DEFAULT_NFL_CONTENT_DRAFTS_READ_URL = "https://tastypickems.com/api/public/nfl-content-drafts-read"


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


def read_content_draft_review_states(season: int, week: int, secret: str, read_url: str = None) -> dict:
    """
    Phase C re-run guard pre-flight. One signed POST (body {"season",
    "week"}) to nfl-content-drafts-read, returns
    {"ok": bool, "error": str|None, "status_code": int|None,
     "reviewed_count": int, "rows": [ {player_id, event_id, shelf,
     writer_type, review_status, reviewed_at}, ... ]}.

    `reviewed_count` is the number of nfl_content_drafts rows for the
    week whose review_status is NOT 'pending_review' — i.e. rows a human
    has already approved / rejected / flagged. The curate endpoint
    refuses to re-run (409) when that is > 0, unless force=True.

    Same forward_to_lovable sign+POST+capture reuse as read_shelf_signal_
    history (see its docstring) — a read call over the identical
    HMAC-signed-raw-body mechanic, not a misuse of the "forward rows"
    naming. A real "no rows for this week yet" response is a valid
    outcome (reviewed_count=0, rows=[]), not an error.
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from lovable_forward import forward_to_lovable, resolve_url_env

    url = read_url or resolve_url_env("LOVABLE_NFL_CONTENT_DRAFTS_READ_URL", DEFAULT_NFL_CONTENT_DRAFTS_READ_URL)
    result = forward_to_lovable({"season": season, "week": week}, secret, url)
    if not result["success"]:
        return {"ok": False, "error": result["error"], "status_code": result["status_code"],
                "reviewed_count": 0, "rows": []}
    try:
        body = json.loads(result["response_body"])
    except (json.JSONDecodeError, TypeError):
        return {"ok": False, "error": f"non-JSON response body: {result['response_body']!r}",
                "status_code": result["status_code"], "reviewed_count": 0, "rows": []}
    if not body.get("ok"):
        return {"ok": False, "error": body.get("error", "unknown error"),
                "status_code": result["status_code"], "reviewed_count": 0, "rows": []}
    rows = body.get("content_drafts", [])
    reviewed_count = body.get("reviewed_count")
    if reviewed_count is None:  # defensive: compute locally if the route didn't
        reviewed_count = sum(1 for r in rows if r.get("review_status") != "pending_review")
    return {"ok": True, "error": None, "status_code": result["status_code"],
            "reviewed_count": reviewed_count, "rows": rows}


DEFAULT_NFL_CONTENT_DRAFTS_SUPERSEDE_URL = "https://tastypickems.com/api/public/nfl-content-drafts-supersede-write"


def compute_stale_approved_targets(rows_to_write: list, existing_rows: list) -> list:
    """
    Multi-run duplication fix (see 20260909010000_add_superseded_review_
    status.sql for the full real-data incident this closes): a forced
    re-run (force:true — the only path that can reach a week with
    existing approved rows at all, see the 409 pre-flight guard above)
    can legitimately select a different top-N for a shelf than a prior
    run did — OR a different single Tasty Six pick for a shelf (see
    select_tasty_six). Either way, a different player means a different
    natural key (player_id, event_id, shelf, writer_type), so
    write_content_draft_rows()'s own upsert never touches the prior
    run's now-stale approved row — confirmed live: attd_500_699/
    attd_700_plus each carrying two full sets of rank-1-through-6
    approved rows from two separate runs.

    Pure function, no I/O: `rows_to_write` is THIS run's own shaped
    output (shape_content_draft_rows(), post-cap — the same list about
    to be passed to write_content_draft_rows), `existing_rows` is
    read_content_draft_review_states()'s own rows for the SAME
    (season, week). Returns the natural-key targets — {player_id,
    event_id, shelf, writer_type} — for existing 'approved' rows whose
    player is no longer among this run's own surviving picks for that
    SAME (shelf, writer_type), for the caller to pass to
    supersede_stale_approved_rows.

    COVERS BOTH writer_type == "shelf_card" AND "tasty_six", tracked as
    two genuinely separate survivor sets per shelf (keyed by
    (shelf, writer_type), never merged) — select_tasty_six is a
    completely separate selection mechanism from the max_per_shelf cap,
    so a Tasty Six row's presence or absence must never affect whether a
    shelf_card row (or vice versa) looks stale. This used to be scoped to
    shelf_card only (a real, confirmed gap: a stale 'approved' tasty_six
    row from a prior run had no path to ever being superseded, even
    after a later run picked a different player or none at all for that
    shelf's Tasty Six slot) — fixed here by applying the exact same
    per-(shelf, writer_type) logic to both, not by inventing a second
    mechanism.

    CALLER CONTRACT, not enforced here: `rows_to_write` and `existing_
    rows` must cover the SAME shelf scope — under player_ids_to_write /
    max_rows_to_write (the endpoint's partial-write TEST knobs), a
    player who genuinely still survives this run's cap but simply wasn't
    included in a deliberately-scoped test write would incorrectly look
    stale, so the endpoint only calls this when neither is set. Under
    shelves_to_process (the real two-call-split PRODUCTION scoping —
    see shape_content_draft_rows' own docstring), `rows_to_write` is
    legitimately scoped to a shelf subset, but genuinely correct for it
    (every real eligible player on those shelves is included) — the
    endpoint compensates by filtering `existing_rows` to the SAME shelf
    scope before calling this function, so the per-(shelf, writer_type)
    survivor comparison below still only ever compares like with like.
    A caller violating either contract (passing an existing_rows shelf
    with no corresponding entry in rows_to_write's own scope) will see
    every approved row for that shelf marked stale — this function has
    no way to detect that it was handed a mismatched pair.
    """
    STALE_ELIGIBLE_WRITER_TYPES = ("shelf_card", "tasty_six")

    surviving_by_shelf_writer: dict[tuple[str, str], set[str]] = {}
    for r in rows_to_write:
        writer_type = r.get("writer_type")
        if writer_type not in STALE_ELIGIBLE_WRITER_TYPES:
            continue
        key = (r["shelf"], writer_type)
        surviving_by_shelf_writer.setdefault(key, set()).add(str(r["player_id"]))

    targets = []
    for row in existing_rows:
        writer_type = row.get("writer_type")
        if writer_type not in STALE_ELIGIBLE_WRITER_TYPES:
            continue
        if row.get("review_status") != "approved":
            continue
        shelf = row.get("shelf")
        key = (shelf, writer_type)
        if str(row.get("player_id")) not in surviving_by_shelf_writer.get(key, set()):
            targets.append({
                "player_id": row["player_id"],
                "event_id": row["event_id"],
                "shelf": shelf,
                "writer_type": writer_type,
            })
    return targets


def supersede_stale_approved_rows(targets: list, season: int, week: int, secret: str, write_url: str = None) -> dict:
    """
    One signed POST (body {"season", "week", "targets": [...]}) to
    nfl-content-drafts-supersede-write — flips existing 'approved' rows
    to 'superseded' for exactly the natural-key targets given (see that
    route's own docstring for why this is a dedicated narrow route
    rather than reusing write_content_draft_rows' full-content upsert).

    Empty targets is a valid, common no-op (most runs supersede
    nothing — supersession only ever has something to do on a force:true
    re-run of a week that already has approved rows) — skips the network
    call entirely rather than sending an empty batch.

    Returns {"ok": bool, "error": str|None, "status_code": int|None,
    "requested": int, "superseded": int}. A route-level failure (network,
    non-2xx, bad JSON) is reported here, not raised — the caller treats
    this the same as the existing shelf_signal_history write: logged,
    not fatal to the overall curate-and-write response.
    """
    if not targets:
        return {"ok": True, "error": None, "status_code": None, "requested": 0, "superseded": 0}

    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from lovable_forward import forward_to_lovable, resolve_url_env

    url = write_url or resolve_url_env(
        "LOVABLE_NFL_CONTENT_DRAFTS_SUPERSEDE_URL", DEFAULT_NFL_CONTENT_DRAFTS_SUPERSEDE_URL,
    )
    result = forward_to_lovable({"season": season, "week": week, "targets": targets}, secret, url)
    if not result["success"]:
        return {"ok": False, "error": result["error"], "status_code": result["status_code"],
                "requested": len(targets), "superseded": 0}
    try:
        body = json.loads(result["response_body"])
    except (json.JSONDecodeError, TypeError):
        return {"ok": False, "error": f"non-JSON response body: {result['response_body']!r}",
                "status_code": result["status_code"], "requested": len(targets), "superseded": 0}
    if not body.get("ok"):
        return {"ok": False, "error": body.get("error", "unknown error"),
                "status_code": result["status_code"], "requested": len(targets), "superseded": 0}
    return {"ok": True, "error": None, "status_code": result["status_code"],
            "requested": body.get("requested", len(targets)), "superseded": body.get("superseded", 0)}


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
            # Same serialization boundary as shape_content_draft_rows'
            # `shelf` — persist the slug, reversed on read (see
            # read_shelf_signal_history / _shelf_unslug).
            "home_shelf": _shelf_slug(r["home_shelf"]),
            "qualifying_signals": r["qualifying_signals"],
            "pending_shelf": _shelf_slug(r.get("pending_shelf")),
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
    # Reverse the serialization boundary: nfl_shelf_signal_history stores
    # snake_case slugs, but the sticky-assignment loop
    # (_compute_sticky_assignment) compares against internal Title-Case
    # SHELF_ORDER names. Un-slug on the way in so nothing downstream has to
    # know the persisted form. Division strings / unknowns pass through.
    rows_by_player = {}
    for row in body.get("shelf_signal_history", []):
        row = dict(row)
        row["home_shelf"] = _shelf_unslug(row.get("home_shelf"))
        row["pending_shelf"] = _shelf_unslug(row.get("pending_shelf"))
        rows_by_player[row["player_id"]] = row
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
