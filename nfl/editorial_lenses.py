"""
NFL Content Generation V1, Part 1 — editorial_lenses.py.

The shelf -> editorial-lens mapping this whole feature is built around:
which real scoring signal each shelf leads its story with, and which
others back it up. Kept as its own config module, not inline in the
generation code, so a future shelf or a lens change never means editing
prompt-construction logic — per explicit instruction.

WHY THIS EXISTS: the same player can qualify for two different shelves
in the same week (e.g. a role_momentum riser landed on both RB Trends
and ATTD +300-499). Without a lens, both cards would draw on the exact
same five-pillar picture and read like the same story twice. The lens
forces each shelf to write from ONE primary signal's own real numbers —
generate_nfl_shelf_card_content.py scopes the source facts a card is
even ALLOWED to cite down to the primary + supporting signals' own
fields before the prompt is built, not just instructed to "focus on X"
while every other pillar's numbers still sit right there in the facts.

SIGNAL NAMING, reconciled against two already-approved, pre-existing
conventions rather than invented fresh:
  - NFL_PILLAR_NAMES (content_writer/nfl_writer_common.py) is the
    already-approved, already-validated 5-pillar enum for why_reasons
    citations: td_opportunity, role_momentum, matchup, environment,
    market_value — `situation` deliberately SPLIT into matchup/
    environment there for citation granularity (each gets its own star
    rating). evidence_quality is deliberately EXCLUDED from that enum:
    it drives the confidence band, not a citable "why" reason.
  - This task's own shelf->lens spec, and scoring.py's own real column,
    both use `situation` as a single unsplit signal, and lists
    evidence_quality as a real, nameable supporting signal.
  Both conventions are honored, not silently collapsed into one:
  EDITORIAL_LENSES below uses `situation` (matching scoring.py's real
  column and this task's own spec) as the identity for evidence-scoping
  and prompt language. SIGNAL_TO_CITABLE_FIELDS expands `situation` to
  BOTH matchup's and environment's real fields (so a "situation"-led
  card can still cite either), and the actual why_reasons OUTPUT schema
  (nfl_shelf_card_writer_schema.py) still constrains `pillar` to
  NFL_PILLAR_NAMES unchanged — a why_reason tagged "situation" was never
  valid there and still isn't; the model tags each individual reason
  matchup or environment, same as Tasty Six already does. evidence_
  quality is real and citable as a SUPPORTING signal's evidence (it
  informs how the prompt talks about overall conviction) but can never
  be a why_reasons.pillar value, matching the existing exclusion exactly
  — enforced by the schema's own validate_schema_shape, not re-derived
  here.

PRIMARY SIGNAL PER SHELF cross-checked against api/curate_home_shelves.
py's own _DETERMINISTIC_PILLAR_FOR_SHELF (the deterministic system's
existing per-shelf ranking signal) — confirmed they agree on every
shelf's primary identity (td_opportunity / role_momentum / market_value
respectively); this module's SUPPORTING lists are new, since the
deterministic system never needed a "backup" signal for one templated
sentence.

SHELF NAMES are the exact internal Title-Case identifiers curate_home_
shelves.SHELF_ORDER and voice/nfl_shelf_personalities.NFL_SHELF_
PERSONALITIES already use — "ATTD +300-499" / "ATTD +500-699" /
"ATTD +700+", not the task-spec's shorter "+300-499" / "Going Nuclear
(+700+)" display phrasing. Reusing the same keys those two already-
validated tables use means a shelf-name typo here fails loudly at
lookup time in three places at once, not silently in just this one.
"""

# {shelf: {"primary": signal_name, "supporting": (signal_name, ...)}}
# supporting is an ORDERED tuple — first entry is the stronger/more
# specific backup signal for that shelf, per the approved table. Real
# and specific for six of seven shelves; "ATTD +700+" is the one
# genuine exception (see _GOING_NUCLEAR_SHELF handling below).
EDITORIAL_LENSES = {
    "Red Zone Trends": {"primary": "td_opportunity", "supporting": ("situation", "evidence_quality")},
    "RB Trends": {"primary": "role_momentum", "supporting": ("td_opportunity", "situation")},
    "WR Trends": {"primary": "role_momentum", "supporting": ("situation", "evidence_quality")},
    "TE Trends": {"primary": "role_momentum", "supporting": ("td_opportunity", "situation")},
    "ATTD +300-499": {"primary": "market_value", "supporting": ("td_opportunity", "evidence_quality")},
    "ATTD +500-699": {"primary": "market_value", "supporting": ("situation", "role_momentum")},
    # "strongest outlier signal" per the approved spec — not a fixed
    # pair, resolved per-player at generation time (see
    # resolve_supporting_signals below). Listed here as an empty tuple,
    # not omitted, so EDITORIAL_LENSES stays a complete, iterable map of
    # every real shelf with no silent gap a caller could mistake for an
    # oversight.
    "ATTD +700+": {"primary": "market_value", "supporting": ()},
}

_GOING_NUCLEAR_SHELF = "ATTD +700+"

# Every real signal name this module knows about, in a fixed order used
# only to break ties deterministically when resolving "ATTD +700+"'s
# dynamic outlier (see resolve_supporting_signals) — NOT a ranking of
# importance.
ALL_SIGNAL_NAMES = ("td_opportunity", "role_momentum", "situation", "evidence_quality", "market_value")

# {signal_name: source_fact_key holding that signal's own real top-level
# score} — used only to find "ATTD +700+"'s real outlier (the signal
# with the highest raw score among the four non-market_value ones);
# NOT used for citation/star-consistency (that's NFL_STAR_PILLAR_
# SCORE_KEYS's job, a separate, already-approved mapping with its own
# matchup/environment split — see this module's own docstring for why
# the two mappings deliberately don't collapse into one).
_SIGNAL_SCORE_KEY = {
    "td_opportunity": "td_opportunity",
    "role_momentum": "role_momentum",
    "situation": "situation",
    "evidence_quality": "evidence_quality",
    "market_value": "market_value_score",
}

# {signal_name: (source_fact_key, ...)} — every NFL_TOP_LEVEL_CITABLE_
# FIELDS entry that belongs to each real signal, grouped directly from
# nfl_writer_common.NFL_TOP_LEVEL_CITABLE_FIELDS' own inline comments
# (scoring.score_td_opportunity / score_role_momentum / score_situation
# / score_market_value's real sub-components), not re-derived from
# scratch. Used to SCOPE a card's real source_facts down to only its
# lens's own eligible fields before the prompt is built — see this
# module's own docstring for why scoping, not just instruction, is what
# actually makes "write from the primary lens" real rather than
# aspirational.
#
# `situation` deliberately includes BOTH matchup's and environment's
# real fields (see module docstring on the situation/matchup+environment
# reconciliation) — a "situation"-led or situation-supporting card can
# cite either half.
SIGNAL_TO_CITABLE_FIELDS = {
    "td_opportunity": (
        "td_opportunity", "proven_heat", "emerging_heat", "recent_td_production_pct", "conversion_rate_pct",
        "touch_share_trend_pct", "snap_share_trend_pct", "touch_volume_trend_pct", "td_opportunity_completeness",
        "i10_touches_trail3", "gl_touches_trail3", "rz_tds_trail3",
    ),
    "role_momentum": (
        "role_momentum", "role_trend", "external_opportunity", "touch_share_trend_pct_role",
        "snap_share_trend_pct_role", "depth_chart_movement_pct", "role_momentum_completeness",
        "snap_share_last1_pct", "snap_share_season_avg_pct", "teammates_ahead_injury_status",
    ),
    "situation": (
        "situation", "defensive_matchup_vulnerability", "recent_tds_allowed_pct", "conversion_rate_allowed_pct",
        "defensive_matchup_completeness", "environment_score", "situation_completeness", "temp", "wind", "roof",
    ),
    "market_value": ("market_value_score", "consensus_price_american", "market_value_completeness"),
    # Never primary (see module docstring) — real citable evidence when
    # it appears as a supporting signal, informing how the prompt talks
    # about overall conviction (pillars agreeing vs. thin/conflicting),
    # never a why_reasons.pillar tag on its own.
    "evidence_quality": ("evidence_quality", "tpe_score"),
}

# Every card gets these regardless of lens — basic scene-setting facts,
# not evidence for any one signal. Kept separate from SIGNAL_TO_CITABLE_
# FIELDS so adding/removing a signal from a lens never accidentally
# drops the player's own name or team.
ALWAYS_INCLUDED_FIELDS = ("player_name", "posteam", "position_group", "consensus_price_american")


def resolve_supporting_signals(shelf: str, row: dict) -> tuple:
    """
    The real, per-player supporting-signal list for `shelf` — EDITORIAL_
    LENSES' own static tuple for six of seven shelves; for "ATTD +700+"
    ("strongest outlier signal" per the approved spec, not a fixed
    pair), the one non-market_value signal with this player's OWN
    highest real score among the other four, ties broken by
    ALL_SIGNAL_NAMES' fixed order (deterministic, not by insertion
    order into a dict, which Python only guarantees but this makes
    explicit and reviewable).

    A signal whose real score is missing/NaN on `row` is treated as the
    lowest possible score for this comparison only — a genuinely absent
    reading should never win "strongest outlier" by default.

    Raises KeyError for an unrecognized shelf — same fail-loud reasoning
    as personality_for_shelf()/intensity_for_band() elsewhere in this
    pipeline; a typo here should never silently resolve to an empty
    lens.
    """
    lens = EDITORIAL_LENSES[shelf]
    if shelf != _GOING_NUCLEAR_SHELF:
        return lens["supporting"]

    candidates = [s for s in ALL_SIGNAL_NAMES if s != "market_value"]

    def _score(signal_name):
        value = row.get(_SIGNAL_SCORE_KEY[signal_name])
        if value is None or (isinstance(value, float) and value != value):  # NaN check without importing pandas
            return float("-inf")
        return float(value)

    best = max(candidates, key=_score)
    return (best,)


def resolve_editorial_lens(shelf: str, row: dict) -> dict:
    """
    {"primary": signal_name, "supporting": (signal_name, ...)} for one
    real player on one real shelf — the primary is always EDITORIAL_
    LENSES' own static value (never dynamic); only "ATTD +700+"'s
    supporting list is resolved per-player (see resolve_supporting_
    signals). Every other shelf's supporting list is returned unchanged
    from EDITORIAL_LENSES.
    """
    return {
        "primary": EDITORIAL_LENSES[shelf]["primary"],
        "supporting": resolve_supporting_signals(shelf, row),
    }


def citable_fields_for_lens(lens: dict) -> tuple:
    """
    The real, deduplicated set of NFL_TOP_LEVEL_CITABLE_FIELDS entries a
    card is allowed to cite for this lens — ALWAYS_INCLUDED_FIELDS plus
    every field belonging to the primary signal plus every field
    belonging to each supporting signal. Order-stable (primary's own
    fields first, then each supporting signal's in the order given) so
    the same lens always scopes to the same field ORDER, not just the
    same set — makes a real generated prompt reproducible/diffable
    across runs for the same player+shelf.
    """
    seen = []
    for field in ALWAYS_INCLUDED_FIELDS:
        if field not in seen:
            seen.append(field)
    for field in SIGNAL_TO_CITABLE_FIELDS[lens["primary"]]:
        if field not in seen:
            seen.append(field)
    for signal_name in lens["supporting"]:
        for field in SIGNAL_TO_CITABLE_FIELDS[signal_name]:
            if field not in seen:
                seen.append(field)
    return tuple(seen)
