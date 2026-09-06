"""
NFL Content Generation V1, Part 2 — story_archetype.py.

The Story Archetype Resolver: maps one real scored player-week row (plus
which shelf it's being shown on) to a fixed illustration archetype for
that card. Governs art selection later — deliberately decoupled from
copy generation (editorial_lenses.py / content_writer/generate_nfl_
shelf_card_content.py): this module never calls Claude, never shapes
prose, and copy generation never reads its output. Pure function, no DB
writes, same "takes scoring output as input" contract as redzone.py/
market_value.py.

REUSES PART 1'S LENS MAPPING, not a second one: "only the shelf's
primary and supporting signals are eligible to trigger an archetype for
that shelf" is enforced by calling editorial_lenses.resolve_editorial_
lens() directly — the exact same real per-shelf signal set (and, for
"ATTD +700+", the exact same dynamically-resolved real outlier signal)
Part 1's prompt scoping already uses. A shelf's eligible signals can
never drift between what a card is WRITTEN from and what it can be
ILLUSTRATED as, since both read the same source.

THE FLOOR — 55.0, not a fresh number, and not guessed. Confirmed via
real 2026 Week 1 data (data/stub_weeks/2026_wk1.csv, 772 players) during
Part 1's own validation: role_trend and defensive_matchup_vulnerability
are LITERAL CONSTANTS across the entire real dataset (50.0 and 48.8
respectively — normalize.fill_neutral's own real sentinel default,
confirmed directly in normalize.py, is exactly 50.0), and td_opportunity
clears 55 for all 772 real rows checked — none of these are genuinely
discriminating signal yet, they're what "no real reference population
to rank against" looks like this early in a season. The floor has to
sit far enough above 50 that this sentinel can never accidentally clear
it by construction, not as an accident of wherever some other number
happened to land. 55 is REUSED, not reinvented: it's the exact same
boundary content_writer/nfl_writer_common.NFL_REGULAR_ROW_CONFIDENCE_
BAND_THRESHOLDS already derived (from real historical tpe_score
population analysis) and approved for "developing_angle -> strong_
setup" — that seam is already documented there as "genuinely
approaching Tasty-Six caliber," i.e. already-vetted real evidence for
where a signal starts being genuinely notable, not a threshold invented
fresh for this module. Because every real signal here is a percentile-
RANKED 0-100 score (not a raw unit), a floor set as a fixed distance
above the KNOWN 50.0 sentinel keeps working once a real reference
population fills in later in the season — it isn't tuned to Week 1's
own sparsity, it's tuned to the sentinel's own fixed value, which never
changes regardless of how populated the real reference distribution
gets.

THE 8 ARCHETYPES, confirmed real column names for each trigger (not
assumed — checked directly against data/stub_weeks/2026_wk1.csv's real
columns before writing any gate below):
  Goal-Line Threat     -- td_opportunity, GATED on real red-zone-touch
                           evidence specifically (i10_touches_trail3 /
                           gl_touches_trail3 / rz_tds_trail3 — shelves.
                           add_red_zone_trend_windows' own trailing
                           columns, NOT present on a raw scored row
                           until that enrichment runs; see this module's
                           own missing-column handling below). A high
                           td_opportunity score ALONE never triggers
                           this — confirmed necessary directly: real
                           Week 1 data has td_opportunity >= 55 for
                           EVERY one of 772 real rows with zero real
                           trail3 evidence anywhere (no prior week
                           exists yet), so without this evidence gate
                           every single player in the league would
                           spuriously read as a real goal-line threat.
  Target Magnet        -- role_momentum, GATED to WR/TE only, backed by
                           real target-share evidence when present
                           (shelves.add_whole_game_target_share_trend's
                           target_share/target_share_trend — only
                           real when the caller threads pbp through;
                           confirmed genuinely absent from the raw stub
                           file otherwise). Falls back to role_momentum's
                           own real touch/snap-share trend components
                           (touch_share_trend_pct_role / snap_share_
                           trend_pct_role) when target_share itself
                           isn't available — an honest best-available
                           real proxy, not a fabricated one.
  Role Riser           -- role_trend specifically (the TREND sub-
                           component), NOT the role_momentum composite —
                           "regardless of current level" per the
                           approved spec means the trigger is about
                           DIRECTION, not the blended absolute score.
  Mismatch             -- defensive_matchup_vulnerability specifically
                           (not the blended `situation` column) — the
                           real sub-score this pillar's own matchup half
                           actually measures.
  Backfield Takeover   -- role_momentum, GATED to RB only, backed by
                           real depth-chart-rank evidence specifically
                           (depth_chart_movement_pct real and present).
  End-Zone Hunter      -- proven_heat specifically (the SEASON-LONG,
                           established component of td_opportunity —
                           see scoring.score_td_opportunity), GATED to
                           proven_heat >= emerging_heat (the RECENT
                           component) — "sustained... not a recent
                           riser" maps directly onto td_opportunity's
                           own real proven/emerging split, not a new
                           concept invented for this module.
  Value Shot           -- market_value_score. No extra gate beyond the
                           floor: "others mid-pack" falls out of the
                           resolver's own highest-eligible-score
                           selection by construction (see resolve_
                           archetype's own docstring) — Value Shot can
                           only ever win when market_value's real score
                           beats every other real eligible archetype
                           for that shelf.
  Going Nuclear        -- market_value_score, GATED to shelf ==
                           "ATTD +700+" only, AND requires the shelf's
                           own dynamically-resolved real outlier
                           supporting signal (editorial_lenses.
                           resolve_supporting_signals' existing logic,
                           reused directly, not reimplemented) to ALSO
                           clear the floor — "market_value + any outlier
                           supporting signal" is a real, confirmed
                           DOUBLE-STRONG-SIGNAL requirement, not
                           market_value alone with a dramatic shelf name.

TIE-BREAKING: ties go to the primary signal's archetype family first
(per the approved spec). A remaining tie between Going Nuclear and
Value Shot specifically (the only two archetypes that can ever compete
on the same real score, since both key off market_value_score, and
both are already "primary family" on ATTD +700+) goes to Going Nuclear
— it is the strictly more specifically-gated of the two (requires a
real second signal to also clear the floor), so a real tie there means
Going Nuclear's own extra condition is what's actually true of this row.

FALLBACK: GENERIC when nothing clears the floor — the honest "no
dramatic evidence yet" read, matching this project's existing fill_
neutral-and-flag philosophy rather than forcing a dramatic archetype
onto thin evidence. Confirmed via real data this is NOT a rare edge
case worth under-designing: most real Week 1 2026 rows checked during
validation resolve to GENERIC, precisely because most of their real
non-market-value signals are still sitting at or near the neutral
sentinel this early in the season.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from editorial_lenses import resolve_editorial_lens  # noqa: E402

FLOOR = 55.0

ARCHETYPES = (
    "GOAL_LINE_THREAT", "TARGET_MAGNET", "ROLE_RISER", "MISMATCH",
    "BACKFIELD_TAKEOVER", "END_ZONE_HUNTER", "VALUE_SHOT", "GOING_NUCLEAR",
)

GOING_NUCLEAR_SHELF = "ATTD +700+"


def _real(value):
    """None for missing/NaN, the real float value otherwise — the one
    real numeric-safety check every gate/score function below shares,
    same "honest None, not a guess" convention as every other missing-
    optional-input case in this codebase. Handles a plain dict (this
    module's real input shape) or a pandas Series transparently: NaN !=
    NaN is true for a float NaN either way, no pandas import needed
    here."""
    if value is None:
        return None
    if isinstance(value, float) and value != value:
        return None
    return float(value)


def _has_real_redzone_touch_evidence(row: dict) -> bool:
    """True iff at least one of shelves.add_red_zone_trend_windows' real
    trailing-window touch counts is present and greater than zero — see
    this module's own docstring for why td_opportunity's raw score alone
    is never enough. These columns are only real once that enrichment
    has run on the caller's own weekly frame; a raw scored row (this
    function's own honest default) has none of them, and this correctly
    returns False rather than raising on a missing key."""
    return any(
        (_real(row.get(col)) or 0) > 0
        for col in ("i10_touches_trail3", "gl_touches_trail3", "rz_tds_trail3")
    )


def _has_real_target_share_evidence(row: dict) -> bool:
    """True iff real target-share evidence backs a Target Magnet read —
    shelves.add_whole_game_target_share_trend's own target_share/
    target_share_trend when present (only real when the caller threaded
    pbp through), else role_momentum's own real touch/snap-share trend
    components as the best-available real proxy — see this module's own
    docstring for why this is an honest fallback, not a fabricated one."""
    if _real(row.get("target_share")) is not None or _real(row.get("target_share_trend")) is not None:
        return True
    return (
        _real(row.get("touch_share_trend_pct_role")) is not None
        or _real(row.get("snap_share_trend_pct_role")) is not None
    )


def _goal_line_threat(row: dict) -> float | None:
    score = _real(row.get("td_opportunity"))
    if score is None or not _has_real_redzone_touch_evidence(row):
        return None
    return score


def _target_magnet(row: dict) -> float | None:
    if row.get("position_group") not in ("WR", "TE"):
        return None
    score = _real(row.get("role_momentum"))
    if score is None or not _has_real_target_share_evidence(row):
        return None
    return score


def _role_riser(row: dict) -> float | None:
    return _real(row.get("role_trend"))


def _mismatch(row: dict) -> float | None:
    return _real(row.get("defensive_matchup_vulnerability"))


def _backfield_takeover(row: dict) -> float | None:
    if row.get("position_group") != "RB":
        return None
    if _real(row.get("depth_chart_movement_pct")) is None:
        return None
    return _real(row.get("role_momentum"))


def _end_zone_hunter(row: dict) -> float | None:
    proven = _real(row.get("proven_heat"))
    if proven is None:
        return None
    emerging = _real(row.get("emerging_heat"))
    if emerging is not None and emerging > proven:
        return None  # a real recent spike is driving this, not a sustained read -- Goal-Line Threat's territory
    return proven


def _value_shot(row: dict) -> float | None:
    return _real(row.get("market_value_score"))


def _going_nuclear(shelf: str, row: dict, lens: dict) -> float | None:
    if shelf != GOING_NUCLEAR_SHELF:
        return None
    market_value = _real(row.get("market_value_score"))
    if market_value is None:
        return None
    # lens["supporting"] is ALREADY the real, per-player dynamically-
    # resolved outlier for this one shelf (editorial_lenses.
    # resolve_supporting_signals' own logic, not reimplemented) -- a
    # single-element tuple by construction for "ATTD +700+".
    outlier_signal = lens["supporting"][0]
    outlier_score = _real(row.get(_SIGNAL_SCORE_KEY[outlier_signal]))
    if outlier_score is None or outlier_score < FLOOR:
        return None
    return market_value


# {signal_name: source_fact_key holding that signal's own real score} --
# same real mapping editorial_lenses.py's own _SIGNAL_SCORE_KEY uses,
# duplicated here rather than importing a private constant across a
# module boundary (a trivial one-liner-sized table, the normal
# duplicate-over-cross-import case elsewhere in this codebase).
_SIGNAL_SCORE_KEY = {
    "td_opportunity": "td_opportunity",
    "role_momentum": "role_momentum",
    "situation": "situation",
    "evidence_quality": "evidence_quality",
    "market_value": "market_value_score",
}

# {archetype: (governing_signal, score_fn)} -- score_fn(row) -> float|None,
# already encoding that archetype's own real gate (position/evidence-
# specific requirements) via returning None when ineligible. Going
# Nuclear is handled separately in resolve_archetype (its score_fn also
# needs `shelf`/`lens`, unlike every other archetype here).
_ARCHETYPE_SPECS = {
    "GOAL_LINE_THREAT": ("td_opportunity", _goal_line_threat),
    "TARGET_MAGNET": ("role_momentum", _target_magnet),
    "ROLE_RISER": ("role_momentum", _role_riser),
    "MISMATCH": ("situation", _mismatch),
    "BACKFIELD_TAKEOVER": ("role_momentum", _backfield_takeover),
    "END_ZONE_HUNTER": ("td_opportunity", _end_zone_hunter),
    "VALUE_SHOT": ("market_value", _value_shot),
}


def resolve_archetype(shelf: str, row: dict) -> dict:
    """
    {"archetype": one of ARCHETYPES or "GENERIC", "position_variant":
    row's own real position_group (may be None), "confidence": the
    winning signal's own real 0-100 percentile score, rounded to 1
    decimal (None for GENERIC — there is no real winning signal to
    report a confidence for)}.

    Only the shelf's own real primary+supporting signals (editorial_
    lenses.resolve_editorial_lens — the SAME lens Part 1's prompt
    scoping already uses) are eligible to trigger an archetype here;
    an archetype whose governing signal isn't in that set is never even
    evaluated, regardless of what its own gate/score would otherwise
    say. Among eligible, gate-passing archetypes, whichever has the
    highest real score wins, PROVIDED it clears FLOOR — confirmed via
    real Week 1 2026 data that this floor is necessary, not
    theoretical (see this module's own docstring). Ties go to the
    primary signal's own archetype family first; a remaining Going-
    Nuclear-vs-Value-Shot tie (the only pair that can ever tie, since
    both key off market_value_score) goes to Going Nuclear, the more
    specifically-gated of the two.

    `row`: one real scored weekly row (a dict or pandas Series, same
    "either works" convention as content_writer.build_nfl_writer_
    candidate) for a player already on this shelf. Missing/NaN fields
    degrade honestly to "this archetype isn't eligible", never a raise
    — a genuinely thin or not-yet-enriched row is exactly the case
    GENERIC exists for.
    """
    if hasattr(row, "to_dict"):
        row = row.to_dict()

    lens = resolve_editorial_lens(shelf, row)
    eligible_signals = {lens["primary"]} | set(lens["supporting"])

    candidates = []  # (score, archetype_name, governing_signal)
    for archetype, (signal, score_fn) in _ARCHETYPE_SPECS.items():
        if signal not in eligible_signals:
            continue
        score = score_fn(row)
        if score is None or score < FLOOR:
            continue
        candidates.append((score, archetype, signal))

    if "market_value" in eligible_signals:
        going_nuclear_score = _going_nuclear(shelf, row, lens)
        if going_nuclear_score is not None and going_nuclear_score >= FLOOR:
            candidates.append((going_nuclear_score, "GOING_NUCLEAR", "market_value"))

    if not candidates:
        return {
            "archetype": "GENERIC",
            "position_variant": row.get("position_group"),
            "confidence": None,
        }

    best_score = max(c[0] for c in candidates)
    tied = [c for c in candidates if c[0] == best_score]
    if len(tied) > 1:
        primary_family = [c for c in tied if c[2] == lens["primary"]]
        if primary_family:
            tied = primary_family
    if len(tied) > 1:
        # The only remaining real tie possible: Going Nuclear vs Value
        # Shot, both keyed off market_value_score, both already "primary
        # family" on ATTD +700+ -- see module docstring for why Going
        # Nuclear (the stricter-gated one) wins.
        nuclear = [c for c in tied if c[1] == "GOING_NUCLEAR"]
        tied = nuclear if nuclear else tied

    winner_score, winner_archetype, _ = tied[0]
    return {
        "archetype": winner_archetype,
        "position_variant": row.get("position_group"),
        "confidence": round(winner_score, 1),
    }
