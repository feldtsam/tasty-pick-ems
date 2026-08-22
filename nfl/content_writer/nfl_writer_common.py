"""
NFL Content Generation, Part C — nfl_writer_common.py.

NFL's own concrete parameters for the shared, parameterized engine built
in Part B (pipeline/api/content_writer/card_writer_common.py) — the
pillar enum, the citable-fields list, the numeric-tolerance
classification, and the shaping step that turns one real scored weekly
row into the flat "candidate" dict flatten_source_facts expects.

CROSS-IMPORTS card_writer_common.py from pipeline/, a deliberate
exception to nfl/'s normal "duplicate rather than cross-import" boundary
from pipeline/ — same exception already made for Part B itself: this
file's ~400 lines carry real, hard-won correctness fixes (comparative-
claim handling, near-zero-tolerance scaling, /9-notation exclusion) that
neither sport should risk re-deriving or silently drifting from. Only the
CONSTANTS below are NFL-specific; the validator/API-call logic itself is
the exact same shared code MLB uses, imported, not forked.

APPROVED PILLAR ENUM (five real NFL pillars, per the approved design):
td_opportunity, role_momentum, matchup, environment, market_value.
`situation` split into matchup/environment for narrative granularity
(scoring.py already tracks defensive_matchup_completeness separately
from situation_completeness for exactly this reason). evidence_quality
is deliberately EXCLUDED from this enum — it drives the confidence band
(via tpe_score, which already bakes it in as a multiplier — see
scoring.score_universal_tpe), the same structural role MLB's final_score
plays for confidence_band_for_score(), not a citable "why" reason a
player gets picked for.

NFL HAS NO NESTED pillar_detail STRUCTURE — every real sub-component
(proven_heat, role_trend, defensive_matchup_vulnerability, ...) is a flat
column directly on the scored `weekly` row (confirmed directly against
nfl/scripts/player_redzone_weekly.csv's real columns, not assumed).
flatten_source_facts() is called with nested_dict_fields=() and
flat_dict_fields=() for NFL — everything goes through top_level_fields,
which is already flat-column-shaped by construction.
"""
import sys
from pathlib import Path

# This file's own directory — card_writer_common.py is a LOCAL, vendored
# copy (see its own header for why: this Vercel project's Root Directory
# is `nfl`, so a cross-import into sibling pipeline/ 404s once deployed;
# found and fixed while building the write-connection endpoint).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from card_writer_common import EXACT_TOLERANCE, ROUNDING_TOLERANCE  # noqa: E402

NFL_PILLAR_NAMES = ("td_opportunity", "role_momentum", "matchup", "environment", "market_value")

# {pillar_name: source_fact_key holding that pillar's real 0-100 score} —
# same role as MLB's STAR_PILLAR_SCORE_KEYS. matchup/environment map to
# their OWN real sub-scores (defensive_matchup_vulnerability /
# environment_score), not to the blended `situation` column — the whole
# point of the approved split is that each gets graded against its own
# real signal, not situation's 0.7/0.3 blend of both.
NFL_STAR_PILLAR_SCORE_KEYS = {
    "td_opportunity": "td_opportunity",
    "role_momentum": "role_momentum",
    "matchup": "defensive_matchup_vulnerability",
    "environment": "environment_score",
    "market_value": "market_value_score",
}

# ---------------------------------------------------------------------------
# Citable fields — every real, flat column a why_reasons citation may
# point at. Verified directly against player_redzone_weekly.csv's real
# columns (not assumed from scoring.py's docstrings alone) before this
# list was written. Excludes pipeline-internal bookkeeping (player_id,
# game_id, season, week, defteam, depth_rank, raw per-game touch/target
# counts already summarized by the *_trend_pct / *_trail3 fields below,
# team_offense_snaps/offense_snaps) — same "nothing narratively relevant"
# discipline as MLB's own TOP_LEVEL_CITABLE_FIELDS.
#
# market_value_score/market_value_completeness/consensus_price_american
# are real columns that are NaN across today's entire historical backfill
# (Market Value is snapshot-only, never backfilled — see market_value.py)
# but real and populated on a live poll — included now, harmless: flatten_
# source_facts already skips None/NaN values automatically, so citing
# them just silently isn't possible yet, not broken.
# ---------------------------------------------------------------------------
NFL_TOP_LEVEL_CITABLE_FIELDS = (
    "player_name", "posteam", "position_group", "consensus_price_american",
    "tpe_score", "evidence_quality",
    "td_opportunity", "role_momentum", "situation", "market_value_score",
    # TD Opportunity sub-components (see scoring.score_td_opportunity)
    "proven_heat", "emerging_heat", "recent_td_production_pct", "conversion_rate_pct",
    "touch_share_trend_pct", "snap_share_trend_pct", "touch_volume_trend_pct",
    "td_opportunity_completeness",
    # Real trailing-window counts (see shelves.add_red_zone_trend_windows —
    # the candidate MUST already have these merged on before flattening,
    # done by build_nfl_writer_candidate below)
    "i10_touches_trail3", "gl_touches_trail3", "rz_tds_trail3",
    # Role & Momentum sub-components (see scoring.score_role_momentum)
    "role_trend", "external_opportunity", "touch_share_trend_pct_role",
    "snap_share_trend_pct_role", "depth_chart_movement_pct", "role_momentum_completeness",
    # snap_share_last1/season_avg RESCALED to 0-100 (see build_nfl_writer_
    # candidate's own docstring for why — the raw scoring columns are
    # 0-1 fractions, which would silently break numeric grounding against
    # prose written the natural way, e.g. "32%")
    "snap_share_last1_pct", "snap_share_season_avg_pct",
    "teammates_ahead_injury_status",
    # Situation sub-components, split matchup/environment per the
    # approved design (see scoring.score_situation)
    "defensive_matchup_vulnerability", "recent_tds_allowed_pct", "conversion_rate_allowed_pct",
    "defensive_matchup_completeness", "environment_score", "situation_completeness",
    "temp", "wind", "roof",
    # Market Value (see scoring.score_market_value — real, not yet wired
    # into tpe_score in production; NaN on every historical row today)
    "market_value_completeness",
)


def nfl_tolerance_for_key(key: str) -> float:
    """
    NFL's own tolerance classification, passed to Part B's parameterized
    validate_numeric_grounding as the injected `tolerance_for_key`
    callable — the NFL analog of card_writer_common.mlb_tolerance_for_key,
    built fresh rather than reusing MLB's classification tables, since
    MLB's don't obviously transfer (see below).

    MUCH SIMPLER than MLB's version, and deliberately so: NFL has no
    nested pillar_detail/recent_form prefix structure at all (every real
    field here is a flat top-level key), so there's no prefix-scoped
    branching to write — just three flat sets.

    WHERE MLB'S TOLERANCE VALUES DON'T OBVIOUSLY TRANSFER, investigated
    rather than assumed:
      - odds: MLB's `odds` field gets EXACT_TOLERANCE (a real +650 must
        never pass as grounded for a stated +600). NFL's consensus_
        price_american is the same kind of American-odds price, same
        real-money stakes for getting it wrong — EXACT_TOLERANCE
        transfers directly, no NFL-specific reasoning needed here.
      - MLB's RATE_STAT_TOLERANCE tier (OPS/ERA/per-9 rates, small
        decimals like .742 or 3.19) has NO NFL analog in this field set
        — nothing NFL cites here is naturally written to 2-3 decimal
        places the way a baseball rate stat is. Not used below; noted
        here so a future reviewer doesn't assume it was forgotten.
      - MLB's percentage/score fields (skill_score, temp_f, ...) get
        ROUNDING_TOLERANCE (0.5) — directly reusable AS A TOLERANCE
        VALUE for NFL's own 0-100-scale fields (tpe_score, td_
        opportunity, proven_heat, defensive_matchup_vulnerability, ...)
        and for temp/wind (same reasoning as MLB's temp_f/wind_speed_mph
        — real weather readings, reasonable to round). This is the one
        case where MLB's actual number (0.5) DOES transfer as-is — the
        underlying scale (a 0-100 percentile-style score, or a real
        Fahrenheit/mph reading) is genuinely the same kind of quantity
        in both sports, not just a coincidentally similar tolerance.
      - THE ONE REAL FIELD-SHAPE ISSUE THIS INVESTIGATION FOUND: NFL's
        role metrics (snap_share_last1, snap_share_season_avg) are
        stored as 0-1 FRACTIONS on the scored weekly table (0.32, not
        32) — confirmed directly against real data and against
        shelves.py's own _position_story, which multiplies by 100 before
        ever printing them ("Snap share {snap_season*100:.0f}%"). Citing
        the raw 0.32 value and applying ANY of the tolerance tiers above
        would never match prose written the natural way ("32%") — 32 is
        nowhere near 0.32 under a 0.5 rounding tolerance. This isn't a
        tolerance-VALUE problem at all; it's a scale problem that no
        tolerance tier can fix. Fixed at the source instead: build_nfl_
        writer_candidate below rescales these two fields to 0-100 before
        they ever reach flatten_source_facts (snap_share_last1_pct /
        snap_share_season_avg_pct), so the real source-fact value IS "32"
        for a real 32% — then ordinary ROUNDING_TOLERANCE applies
        correctly, same as every other 0-100 field.
      - Real trailing-window counts (i10_touches_trail3, gl_touches_
        trail3, rz_tds_trail3 — see shelves.add_red_zone_trend_windows)
        are genuine integer counts, same category as MLB's recent_
        games_sampled/recent_home_runs — EXACT_TOLERANCE, exactness IS
        the claim (a real "3 goal-line touches" misstated as "4" is a
        meaningful factual error, not reasonable rounding).
    """
    if key in _NFL_EXACT_FIELDS:
        return EXACT_TOLERANCE
    if key in _NFL_ROUNDING_FIELDS:
        return ROUNDING_TOLERANCE
    return EXACT_TOLERANCE  # safe default direction, same as MLB's own


_NFL_EXACT_FIELDS = {
    "consensus_price_american",
    "i10_touches_trail3", "gl_touches_trail3", "rz_tds_trail3",
}

_NFL_ROUNDING_FIELDS = {
    "tpe_score", "evidence_quality", "td_opportunity", "role_momentum", "situation", "market_value_score",
    "proven_heat", "emerging_heat", "recent_td_production_pct", "conversion_rate_pct",
    "touch_share_trend_pct", "snap_share_trend_pct", "touch_volume_trend_pct", "td_opportunity_completeness",
    "role_trend", "external_opportunity", "touch_share_trend_pct_role", "snap_share_trend_pct_role",
    "depth_chart_movement_pct", "role_momentum_completeness",
    "snap_share_last1_pct", "snap_share_season_avg_pct",
    "defensive_matchup_vulnerability", "recent_tds_allowed_pct", "conversion_rate_allowed_pct",
    "defensive_matchup_completeness", "environment_score", "situation_completeness",
    "market_value_completeness", "temp", "wind",
}


def _as_list(value) -> list:
    """Same real round-trip issue shelves.py's own _as_list handles —
    ahead_injury_statuses is a real Python list in memory but round-trips
    through CSV as its str() repr. Duplicated here rather than importing
    shelves.py's private helper — a trivial one-liner, the normal
    duplicate-over-cross-import case, unlike card_writer_common.py above."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        import ast
        try:
            parsed = ast.literal_eval(value)
            return parsed if isinstance(parsed, list) else []
        except (ValueError, SyntaxError):
            return []
    return []


def build_nfl_writer_candidate(row: dict) -> dict:
    """
    Turns one real scored weekly row (a home-assigned, Tasty-Six-
    qualifying player's row — already enriched with shelves.
    add_red_zone_trend_windows()'s i10_touches_trail3/gl_touches_trail3/
    rz_tds_trail3 columns, same prep Part A's _story_for_row needs) into
    the flat dict flatten_source_facts(candidate, NFL_TOP_LEVEL_CITABLE_
    FIELDS) expects. NFL has no shelf-entry wrapper the way MLB's
    shelf_curation.py candidates do (no "candidate" sub-key) — this
    function's OUTPUT is already the flat shape flatten_source_facts'
    own `candidate.get("candidate", candidate)` unwrap step falls through
    to harmlessly.

    Two real, deliberate transformations happen here, not in card_writer_
    common.py (an NFL-specific concern, doesn't belong in the shared
    engine):
      - snap_share_last1/snap_share_season_avg are RESCALED from their
        real 0-1 fraction storage to 0-100 (see nfl_tolerance_for_key's
        docstring for why this is necessary, not cosmetic).
      - ahead_injury_statuses (a real list, e.g. ["Questionable"]) is
        joined into one citable string field, teammates_ahead_injury_
        status — flatten_source_facts has no list-flattening branch (nor
        should it grow one for a single NFL-only field), so this is
        handled at the shaping layer instead.
    """
    c = dict(row)
    if c.get("snap_share_last1") is not None:
        c["snap_share_last1_pct"] = round(c["snap_share_last1"] * 100, 1)
    if c.get("snap_share_season_avg") is not None:
        c["snap_share_season_avg_pct"] = round(c["snap_share_season_avg"] * 100, 1)
    statuses = _as_list(c.get("ahead_injury_statuses"))
    c["teammates_ahead_injury_status"] = ", ".join(statuses) if statuses else None
    return c


# ---------------------------------------------------------------------------
# Confidence-band thresholds — APPROVED (write-connection task), built
# here now that they're no longer pending. Grounded in tpe_score's real
# distribution among the ONLY population this ever runs against (real
# Tasty-Six-qualifying rows, tpe_score>=55 by the already-approved gate):
# full historical backfill, n=1118, min=55.0 (the gate itself), p25=57.4,
# p50=60.5, p75=64.8, p90=70.4, p95=73.2, p99=78.3, max=85.7 — NOT MLB's
# 25-90 range, which tpe_score never approaches (see the conversation
# this was investigated and proposed in). Band NAMES reused unmodified
# from MLB's emotional_intensity.py (cross-imported, not redefined) —
# only these NFL-specific cutoffs are new.
# ---------------------------------------------------------------------------
NFL_CONFIDENCE_BAND_THRESHOLDS = (
    (55.0, 60.0, "quiet_signal"),
    (60.0, 65.0, "developing_angle"),
    (65.0, 75.0, "strong_setup"),
    (75.0, 100.0, "premium_signal"),
)


def nfl_confidence_band_for_score(tpe_score) -> str | None:
    """
    NFL's own version of MLB's principles.confidence_band_for_score() —
    same fail-safe shape (None outside the real range, half-open bands
    except the top one, which stays upper-inclusive), different cutoffs
    (see NFL_CONFIDENCE_BAND_THRESHOLDS above for why). None below 55 is
    a genuine safety net, not an expected path: 55 is the Tasty Six gate
    itself, so nothing calling this with a real Tasty Six row's tpe_score
    should ever actually hit it — a None here signals this was called on
    a row that shouldn't have reached the LLM writer at all, not a
    legitimate low-confidence case needing quiet_signal.
    """
    if tpe_score is None:
        return None
    last_index = len(NFL_CONFIDENCE_BAND_THRESHOLDS) - 1
    for i, (lo, hi, band) in enumerate(NFL_CONFIDENCE_BAND_THRESHOLDS):
        if i == last_index:
            if lo <= tpe_score <= hi:
                return band
        elif lo <= tpe_score < hi:
            return band
    return None
