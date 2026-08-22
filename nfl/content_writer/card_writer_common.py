"""
COPIED from pipeline/api/content_writer/card_writer_common.py, NOT
cross-imported — real deployment constraint, found while building the
write-connection endpoint: this Vercel project's Root Directory is
`nfl` (confirmed directly via `vercel project inspect`), so pipeline/
(a sibling top-level directory) is never bundled into the deployed
function's filesystem at all — a sys.path insert pointing at it works
locally but 404/ImportErrors the moment this is actually deployed.
Same reasoning nfl/api/index.py's own docstring already documents for
check_pipeline_secret()/lovable_forward.py's OWN copy-not-import
choice ("avoids betting on whether Vercel's Python build would even
bundle a sibling top-level directory outside pipeline/'s own tree") —
this file should have followed that same precedent from the start.
If this file changes on the MLB side, this copy needs updating by
hand — same accepted cost as every other copied-not-imported file in
this pipeline's nfl/ deployment.

Content Writer — card_writer_common.py.

Extracted 2026-08-04 from tasty_six_writer_schema.py, a PURE REFACTOR with
zero behavior change (confirmed by re-running the full pre-existing
Tasty Six test suite before and after and diffing the output — see the
pipeline README). Motivation: a second writer type (regular shelf cards)
needed everything here too, and importing it from a module literally
named after one specific writer would be backwards, while duplicating
~300 lines of already-tested validator code (including two real bug
fixes) would mean every future fix has to be applied twice and can
silently drift between writer types.

WHAT LIVES HERE: everything that has no dependency on which specific
writer type is calling it — facts extraction, every deterministic
validation check (citations, numeric grounding, star consistency), the
shared why_reasons JSON-schema block, and the actual Claude API call.
Each writer type's own module (tasty_six_writer_schema.py,
shelf_card_writer_schema.py, ...) keeps only what's genuinely specific to
it: its own tool schema's writer-specific fields, and its own
validate_schema_shape().

See tasty_six_writer_schema.py's original docstring (still present there)
for the full historical reasoning behind each piece — this file is a
relocation, not a rewrite, and that context still applies.

PARAMETERIZED (NFL Content Generation Part B, this update): flatten_
source_facts, validate_numeric_grounding (+ mlb_tolerance_for_key, the
renamed former _tolerance_for_key), and validate_star_consistency no
longer read MLB-specific module constants (PILLAR_NAMES, TOP_LEVEL_
CITABLE_FIELDS, etc.) implicitly — they take them as explicit parameters,
so a future sport can pass its own without forking this file's ~400
lines of validator logic (three real production bugs already found and
fixed here: comparative-claim false positives, near-zero-tolerance blind
spots, /9-notation misparsing — a fork risks each of those silently
drifting between sports). validate_citations needed no change (checked
directly: it never depended on an MLB-specific constant in the first
place). MLB's own constants (PILLAR_NAMES, TOP_LEVEL_CITABLE_FIELDS,
RECENT_FORM_CITABLE_FIELDS, RECENT_FORM_SKIP_FIELDS, the tolerance-tier
sets, mlb_tolerance_for_key, STAR_PILLAR_SCORE_KEYS) all still live here,
unchanged in value — MLB's real call sites (generate_shelf_card_content.
py, generate_tasty_six_content.py) now pass them explicitly rather than
relying on these functions reading them implicitly. Confirmed a strict
behavioral no-op for MLB via a real before/after regression diff on real
production inputs, not just "tests still pass" — see the conversation
this change was validated in.
"""
import re

import requests

PILLAR_NAMES = ("skill", "matchup", "environment", "opportunity")

MIN_WHY_REASONS = 2
MAX_WHY_REASONS = 3

# ---------------------------------------------------------------------------
# The shared why_reasons contract — every writer type's tool schema
# embeds this identically, so the two can never silently drift apart on
# what a "why reason" actually is.
# ---------------------------------------------------------------------------

WHY_REASON_SCHEMA = {
    "type": "object",
    "properties": {
        "pillar": {
            "type": "string",
            "enum": list(PILLAR_NAMES),
            "description": "Which of the four real scored pillars this reason is drawn from.",
        },
        "stars": {
            "type": "integer",
            "minimum": 1,
            "maximum": 5,
            "description": "How strong this specific reason is, grounded in this pillar's real score — not an independent creative choice.",
        },
        "reason_text": {
            "type": "string",
            "minLength": 1,
            "maxLength": 240,
        },
        "source_fact_keys": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string"},
            "description": "The exact key(s) from the provided source facts this reason is built from. Every claim must be traceable here.",
        },
    },
    "required": ["pillar", "stars", "reason_text", "source_fact_keys"],
}

WHY_REASONS_ARRAY_SCHEMA = {
    "type": "array",
    "minItems": MIN_WHY_REASONS,
    "maxItems": MAX_WHY_REASONS,
    "description": "2-3 of the strongest real pillars behind this pick, each a real 'receipt', not a verdict.",
    "items": WHY_REASON_SCHEMA,
}


# ---------------------------------------------------------------------------
# Source facts — the exact, deterministic set of real keys a card is
# allowed to cite from. Deliberately excludes pipeline-internal bookkeeping
# (mlbam_id, game_pk, bookmaker, num_bookmakers, match_type,
# passes_odds_filter, scored_at, id/created_at-style fields) — nothing
# narratively relevant, and excluding them means the model can't even
# accidentally cite something a reader was never meant to see.
# ---------------------------------------------------------------------------

TOP_LEVEL_CITABLE_FIELDS = (
    "player_name", "team", "opp_pitcher_name", "matchup", "venue_name",
    "odds", "batting_order_slot", "skill_score", "matchup_score",
    "environment_score", "opportunity_score", "final_score", "star_rating",
    "score_tier", "temp_f", "wind_speed_mph", "wind_description",
    "roof_status", "notes",
)

# Present only on picks sourced from the Hot Hitters or Cold Pitchers to
# Attack shelves specifically — see shelf_curation.py's
# _hot_hitters_eligible()/_cold_pitchers_eligible(), which attach these as
# extra fields on the shelf entry, not on the underlying scored_picks
# candidate itself. Included here so a real recent-form stat is citable
# when present, not silently unavailable depending on which shelf a pick
# came from.
#
# Flattened into dotted per-field keys (recent_form.recent_ops, etc.),
# same treatment as pillar_detail's components — NOT stored as one opaque
# dict-valued key. This matters for two real reasons, not just citation
# granularity: (1) _numbers_in() below has no dict branch (a raw dict
# value silently contributes zero numbers to grounding — a real gap this
# flattening closes rather than papers over), and (2) different recent-
# form fields need genuinely different numeric tolerances (a count like
# recent_games_sampled vs. a rate stat like recent_ops) — see
# _tolerance_for_key(), which needs each field addressable by its own key
# to classify it correctly.
RECENT_FORM_CITABLE_FIELDS = (
    "recent_form", "opposing_pitcher_recent_form",
)

# recent_window_dates is a nested {"first": ..., "last": ...} dict of date
# STRINGS, not a numeric or narrative fact worth citing here — skipped
# rather than flattened. PUBLIC (no leading underscore) — same reasoned
# exception as nfl/shelves.py's eligible_pool: a real caller outside this
# module (MLB's own writer call sites, post-parameterization — see below)
# now passes this explicitly, not a trivial one-liner worth hiding.
RECENT_FORM_SKIP_FIELDS = {"recent_window_dates"}


def flatten_source_facts(
    candidate: dict,
    top_level_fields: tuple,
    nested_dict_fields: tuple = (),
    flat_dict_fields: tuple = (),
    flat_dict_skip_fields: frozenset = frozenset(),
) -> dict:
    """
    PARAMETERIZED (NFL Content Generation Part B) — was hardcoded to
    MLB's own TOP_LEVEL_CITABLE_FIELDS/RECENT_FORM_CITABLE_FIELDS/
    _RECENT_FORM_SKIP_FIELDS module constants; every real call site now
    passes them explicitly (see MLB's own callers below — this function's
    BODY is otherwise byte-for-byte the same logic, just reading its
    inputs from parameters instead of module globals; confirmed a strict
    no-op via a real before/after regression diff, not just "tests still
    pass" — see the conversation this change was validated in).

    THE REAL SHAPE OF CHANGE NEEDED, investigated before this rework:
    a single parameter swap was NOT enough. MLB's nested-dict flattening
    (pillar_detail.{pillar}.score / pillar_detail.{pillar}.components.{x})
    assumes a specific two-level shape — a named group with a "score" plus
    a "components" sub-dict — that NFL's real scored output does not have
    at all (nfl/scoring.py's five pillars are flat columns directly on the
    weekly row, no pillar_detail nesting anywhere). Swapping only the
    FIELD NAMES and leaving the nesting assumption hardcoded would still
    make this function unusable for NFL. So `nested_dict_fields` is its
    own parameter, defaulting to empty — a sport with no such nesting
    (NFL, today) just omits it and gets everything through
    `top_level_fields` instead, which is already flat-column-shaped by
    construction. MLB passes ("pillar_detail",) to keep its own real
    behavior unchanged.

    Turns one real curated candidate (a shelf_assignments/Tasty-Six entry,
    itself wrapping a scored_picks-shaped candidate dict under "candidate"
    per shelf_curation.py's real entry shape, OR a bare scored_picks-shaped
    dict) into the flat, citable {key: value} set a writer call is allowed
    to reference. Dotted paths for a nested group's components (e.g.
    "pillar_detail.skill.components.power_production") and for flat-dict
    fields (e.g. "recent_form.recent_ops") give citations real granularity
    — pointing at the specific number a claim is about, not just the
    parent group's overall score or an opaque nested blob.

    top_level_fields: keys pulled directly from the (unwrapped) candidate
    dict if present and non-None.
    nested_dict_fields: top-level keys (default: none) whose value is
    itself a dict of {group_name: {"score": ..., "components": {...}}} —
    MLB's pillar_detail shape. Flattened to "{nested_key}.{group_name}
    .score" and "{nested_key}.{group_name}.components.{comp_key}".
    flat_dict_fields: top-level keys (read from the OUTER candidate, same
    as MLB's original recent_form/opposing_pitcher_recent_form behavior —
    these live on the shelf-entry wrapper, not inside "candidate") whose
    value is a flat dict, flattened to "{key}.{field_name}", skipping any
    name in flat_dict_skip_fields.
    """
    c = candidate.get("candidate", candidate)  # unwrap a shelf-entry shape if present

    facts = {}
    for key in top_level_fields:
        if key in c and c[key] is not None:
            facts[key] = c[key]

    for nested_key in nested_dict_fields:
        nested = c.get(nested_key) or {}
        for group_name, group_data in nested.items():
            if not isinstance(group_data, dict):
                continue
            if "score" in group_data:
                facts[f"{nested_key}.{group_name}.score"] = group_data["score"]
            components = group_data.get("components") or {}
            for comp_key, comp_val in components.items():
                facts[f"{nested_key}.{group_name}.components.{comp_key}"] = comp_val

    for key in flat_dict_fields:
        form = candidate.get(key)
        if not isinstance(form, dict):
            continue
        for field_name, field_val in form.items():
            if field_name in flat_dict_skip_fields or field_val is None:
                continue
            facts[f"{key}.{field_name}"] = field_val

    return facts


# ---------------------------------------------------------------------------
# Validation — each function returns a list of violation dicts (empty =
# clean), same "explainable, not a black box" discipline as
# banned_language.find_banned_phrases().
# ---------------------------------------------------------------------------

def validate_citations(why_reasons: list, source_facts: dict) -> list[dict]:
    """
    NOT CHANGED for NFL Content Generation Part B — checked directly
    before touching anything: this function already has zero dependency
    on any MLB-specific module constant (no PILLAR_NAMES, no field-name
    table, nothing). It operates purely on its two arguments, so it's
    already sport-agnostic as written. Listed in Part B's task scope
    alongside the other three, but no rework was needed or made here.

    Every cited key must be a real key that exists in source_facts. A
    reason with zero citations is itself a violation — the schema requires
    minItems=1 on source_fact_keys, but a model can still technically emit
    an empty array if it ignores the schema description, so this is
    checked again here rather than trusted from the schema alone."""
    violations = []
    for i, reason in enumerate(why_reasons):
        cited = reason.get("source_fact_keys") or []
        if not cited:
            violations.append({"reason_index": i, "issue": "no source_fact_keys cited"})
            continue
        for key in cited:
            if key not in source_facts:
                violations.append({
                    "reason_index": i,
                    "issue": f"cited key {key!r} does not exist in the real source facts",
                })
    return violations


# \d*\.?\d+ (not \d+\.?\d*) so a leading-decimal number with no digit
# before the point — real, idiomatic baseball notation (".300 hitter",
# ".910 OPS") — is captured correctly. The original \d+\.?\d* form has no
# digit for \d+ to anchor to at the start of ".74", so it silently matched
# "74" instead of 0.74 — not a miss, a WRONG number, caught by this file's
# own numeric-grounding tests once real rate-stat citations exercised it.
#
# NO LONGER excludes ordinals via an inline negative lookahead — real bug
# found during NFL Content Generation Part C's own validation (2026-08-
# 21): a MULTI-DIGIT ordinal like "89th" or "21st" was NOT excluded the
# way single-digit ordinals like "3rd" were. `\d+(?!\s*(?:st|nd|rd|th)\b)`
# is greedy, so it first tries to match "89", fails the lookahead (the
# very next characters are "th"), and — since Python's re engine
# backtracks on failure rather than giving up — retries with a SHORTER
# match, "8" alone, whose lookahead trivially succeeds (the text right
# after "8" is "9th", which doesn't literally start with a suffix
# keyword). Net effect: "89th percentile" silently extracted a bare,
# WRONG number ("8") instead of correctly excluding "89" as an ordinal —
# confirmed directly (`_NUMBER_PATTERN.findall("89th percentile")` ->
# `['8']` on the old pattern), and confirmed this was never NFL-specific:
# the same collapse happens for any 2+-digit ordinal regardless of sport
# (`_NUMBER_PATTERN.findall("21st century")` -> `['2']`) — MLB's own real
# prose (shelves.py-style "trending 88th percentile" phrasing) could have
# hit this identically; single-digit ordinals ("3rd home run", the only
# case MLB's own test suite exercised) happened to never trigger it,
# since \d+ can't backtrack below one digit.
#
# FIXED by moving ordinal exclusion out of this pattern entirely and into
# its own span-based exclusion (_ORDINAL_PATTERN below), matching the
# SAME architecture already used for comparative claims and /9 notation
# (see validate_numeric_grounding) — exclude by SPAN, not by a fragile
# lookahead baked into the extraction pattern itself. This pattern now
# has no backtracking trap: it just extracts every number, full stop.
_NUMBER_PATTERN = re.compile(r"(?<![a-zA-Z])-?\d*\.?\d+")

# Matches a full ordinal token ("89th", "3rd", "21st") as ONE span, reused
# by validate_numeric_grounding to exclude the number portion from the
# point-value pass — see _NUMBER_PATTERN's docstring above for why this
# is a separate, span-based exclusion rather than a lookahead.
_ORDINAL_PATTERN = re.compile(r"(?<![a-zA-Z])-?\d*\.?\d+(?:st|nd|rd|th)\b", re.IGNORECASE)


# Real false positive found in production generation (2026-08-04, George
# Springer, Cold Pitchers to Attack): "a 9.33 recent ERA and 1.47 HR/9" and
# "walking nearly 6 per 9" both got the "9" extracted as its own separate
# ungrounded number. Baseball's standard "per nine innings" rate-stat
# notation (K/9, BB/9, HR/9, or spelled out as "X per 9") uses a literal
# "9" as a unit denominator, not a separate factual claim -- the real
# numbers in those two examples (9.33, 1.47) were already correctly
# grounded; the bare "9" from the notation itself is what got wrongly
# flagged. Real and recurring, not a one-off: Cold Pitchers to Attack's
# whole premise is citing opposing-pitcher recent form, which is almost
# always exactly these per-9 stats.
#
# Word-boundary-anchored so this ONLY matches the exact "/9" or "per 9"
# notation, never a genuinely different number that happens to follow a
# slash or the word "per" for some other reason: the \b immediately after
# the "9" requires a non-digit boundary right there, which two adjacent
# digits never have -- so "/90", "/95", "per 90" don't match. An unrelated
# fraction like "3/4" or a date like "8/15" doesn't match either, since
# neither contains a literal 9 in that position at all.
_PER_NINE_PATTERN = re.compile(r"(?:/|per\s+)(9)\b", re.IGNORECASE)


# Real false positive found in production generation (2026-08-04, Jeremy
# Pena): "Bieber's contact-allowed and rate-outcome marks both sit above
# 90" was flagged as ungrounded because the real values (90.8, 90.7) don't
# ROUND to 90 (they round to 91) — but the claim isn't a rounded point
# value in the first place, it's a comparative/threshold claim, and it's
# TRUE (90.8 and 90.7 genuinely are both above 90). A point-value check
# has no way to represent "exceeds N" as distinct from "equals
# approximately N". Not a one-off: any real writer summary reaching for
# "both over X" / "each above Y" / "under Z" phrasing hits the same gap.
#
# Fix: detect this phrasing explicitly and validate it as what it actually
# claims — does at least one real source value satisfy the comparison —
# rather than forcing it through the point-value rounding check. Numbers
# consumed by a comparative match are excluded from the point-value pass
# below so they're never double-counted or double-flagged.
#
# Deliberately covers common comparative phrasing, not exhaustive NLP —
# "use judgment, don't over-specify" per the explicit instruction this was
# built under. Extend these patterns if real generations surface a
# phrasing they miss.
_ABOVE_PATTERN = re.compile(r"\b(?:above|over|exceeds?|greater than|more than)\s+(-?\d*\.?\d+)", re.IGNORECASE)
_BELOW_PATTERN = re.compile(r"\b(?:below|under|less than|fewer than)\s+(-?\d*\.?\d+)", re.IGNORECASE)

# Same scale as ROUNDING_TOLERANCE — natural language rounds a threshold
# the same way it rounds a point value ("above 90" said for a real 90.4 is
# the same kind of reasonable interpretation as "90" said for a real 90.4).
COMPARATIVE_EPSILON = 0.5


def _extract_comparative_claims(text: str) -> list:
    """Returns [(comparator, claimed_value, (start, end)), ...] for every
    detected "above/over/exceeds N" or "below/under/less than N" phrase in
    text, along with the exact character span of the number itself (used
    to exclude it from the plain point-value pass)."""
    claims = []
    for m in _ABOVE_PATTERN.finditer(text):
        claims.append(("above", float(m.group(1)), m.span(1)))
    for m in _BELOW_PATTERN.finditer(text):
        claims.append(("below", float(m.group(1)), m.span(1)))
    return claims


def _comparative_claim_satisfied(comparator: str, claimed_value: float, cited_facts: dict) -> bool:
    """
    True iff at least one real value among this reason's OWN CITED facts
    actually satisfies the comparison — "above 90" is satisfied by a real
    90.7 among what THIS reason cited, not by finding a real value near 90
    ANYWHERE in the whole candidate. COMPARATIVE_EPSILON allows the same
    natural rounding at the threshold itself that ROUNDING_TOLERANCE
    allows for point values (a real 89.6 reasonably supports "above 90").

    Scoped to cited_facts, not the full source_facts dict, for a real
    reason found while testing this fix: "above 200" against the full
    candidate was satisfied by `odds` (routinely 300+, since this whole
    pipeline gates on a +300 minimum) even when the claim had nothing to
    do with odds — checking the entire candidate makes a comparative claim
    with a high threshold almost impossible to falsify. Scoping to what
    the reason actually cited closes that gap and is the more correct
    design anyway: a comparative claim should be checked against what it
    claims to be about, the same way a point-value citation is.
    """
    for value in cited_facts.values():
        for n in _numbers_in(value):
            if comparator == "above" and n >= claimed_value - COMPARATIVE_EPSILON:
                return True
            if comparator == "below" and n <= claimed_value + COMPARATIVE_EPSILON:
                return True
    return False


def _numbers_in(value) -> set:
    """Extracts real numbers from a value, deliberately excluding ordinal
    suffixes (e.g. "3rd") — an ordinal reference ("3rd inning", "on his
    3rd try") is common, real narrative language that has no reason to
    correspond to a literal source-fact number, and including it would be
    exactly the kind of false-positive noise this check exists to avoid.
    No dict branch: flatten_source_facts() flattens every nested dict
    (pillar_detail, recent_form, opposing_pitcher_recent_form) into dotted
    scalar keys before this function ever sees them — a raw dict reaching
    here would mean a future field was added without being flattened, not
    something to silently recurse into and guess a tolerance for."""
    if isinstance(value, (int, float)):
        return {round(float(value), 2)}
    if isinstance(value, str):
        return {round(float(m), 2) for m in _NUMBER_PATTERN.findall(value)}
    if isinstance(value, list):
        out = set()
        for item in value:
            out |= _numbers_in(item)
        return out
    return set()


# ---------------------------------------------------------------------------
# Numeric tolerance — three real tiers, not one flat number. Two were
# requested directly; the third (RATE_STAT_TOLERANCE) is this file's
# answer to "use your judgment for anything not obviously in one category
# or the other" — see _tolerance_for_key()'s docstring for the reasoning.
# ---------------------------------------------------------------------------

# "Reasonable rounding to the nearest whole number" — writing "71%" for a
# real 71.2 is normal, good sports-writing interpretation backed by a real
# receipt, not fabrication. 0.5 is the largest gap a value can have from
# its own nearest-whole-number rounding, so this tolerance exactly
# captures "did the model round this reasonably" without also accepting
# genuinely different numbers.
ROUNDING_TOLERANCE = 0.5

# Small-scale rate stats (OPS, ERA, per-9 rates) conventionally reported
# to 2-3 decimals. These don't fit ROUNDING_TOLERANCE — "nearest whole
# number" is nonsensical for an OPS of 0.910 (nobody writes "an OPS of
# 1") — but genuine decimal-level rounding (".910" written as ".91") is
# completely normal and shouldn't be flagged as fabrication either.
RATE_STAT_TOLERANCE = 0.02

# Genuine floating-point noise only. Used for odds (a real +650 must never
# quietly pass as grounded for a stated +600) and anything else where
# exactness IS the fact being claimed, not an approximation of it.
EXACT_TOLERANCE = 0.01

_ROUNDING_TOP_LEVEL_FIELDS = {
    "skill_score", "matchup_score", "environment_score", "opportunity_score",
    "final_score", "temp_f", "wind_speed_mph",
}

# pillar_detail component names that are NOT 0-100 percentile-style scores
# despite living under pillar_detail, so they default to EXACT instead of
# the ROUNDING treatment every other pillar_detail leaf gets.
_EXACT_PILLAR_COMPONENT_NAMES = {
    # A small ~1.0-2.5 multiplier (see score_candidate.py's matchup
    # pillar), not a percentile score. Rounding a real 1.76 to "2" is a
    # ~14% relative jump — a large distortion — unlike rounding a 0-100
    # score by the same 0.5, which is a ~1% jump. Same absolute tolerance,
    # very different real-world meaning, hence the exception.
    "platoon_adjustment",
}

# recent_form.py's real per-game/per-start COUNTING fields — exactness is
# the fact being claimed (a real "sampled 15 games" silently becoming "12"
# is a real, meaningful factual error), same reasoning as
# batting_order_slot/star_rating below.
_EXACT_RECENT_FORM_FIELD_NAMES = {
    "recent_games_sampled", "recent_plate_appearances", "recent_home_runs",
    "recent_starts_sampled",
}

# recent_form.py's rate-stat fields — see RATE_STAT_TOLERANCE above.
_RATE_STAT_RECENT_FORM_FIELD_NAMES = {
    "recent_ops", "recent_hr_per_pa", "recent_era", "recent_hr_per_9",
    "recent_k_per_9", "recent_bb_per_9",
}

# recent_innings_pitched (e.g. 31.0, 1.7) is conventionally reported to 1
# decimal place and rounds sensibly to a whole number ("31 innings" for a
# real 31.0) — unlike its rate-stat neighbors above, this one genuinely
# belongs in the same bucket as the 0-100 scores.
_ROUNDING_RECENT_FORM_FIELD_NAMES = {
    "recent_innings_pitched",
}


def mlb_tolerance_for_key(key: str) -> float:
    """
    PUBLIC, RENAMED from _tolerance_for_key (NFL Content Generation Part
    B) — this function's BODY is unchanged (same MLB-specific
    classification logic, same reliance on the module-level tolerance-tier
    sets above), it's just now explicitly nameable and passable rather
    than called implicitly by name from inside validate_numeric_grounding.
    PARAMETERIZATION DESIGN CHOICE (config object vs. callable): this
    classification logic is genuinely bespoke conditional branching (exact
    literal key names, THEN prefix-scoped nested lookups with DIFFERENT
    default tolerances per prefix — pillar_detail leaves default to
    ROUNDING, recent-form leaves default to EXACT) — not a flat table. A
    generic declarative config trying to capture that shape risks either
    losing fidelity or reimplementing this exact branching one level up
    for no real benefit. Passing the whole function as-is, unchanged, is
    the genuinely zero-risk choice — validate_numeric_grounding below now
    takes a `tolerance_for_key` callable parameter; MLB passes this
    function by name, NFL's Part C would write and pass its own (almost
    certainly much simpler, since NFL's scored columns have no nested-
    prefix structure at all).

    Classifies a source-fact key into its real numeric tolerance. odds is
    checked first and explicitly — the field this whole design change was
    motivated by protecting. Everything not explicitly classified falls
    through to EXACT_TOLERANCE by default: the safe direction to err,
    since this check exists to catch real fabrication, not to be lenient
    by default for a field nobody thought about yet.
    """
    if key == "odds":
        return EXACT_TOLERANCE
    if key in ("batting_order_slot", "star_rating"):
        return EXACT_TOLERANCE
    if key in _ROUNDING_TOP_LEVEL_FIELDS:
        return ROUNDING_TOLERANCE

    if key.startswith("pillar_detail."):
        component_name = key.rsplit(".", 1)[-1]
        if component_name in _EXACT_PILLAR_COMPONENT_NAMES:
            return EXACT_TOLERANCE
        return ROUNDING_TOLERANCE  # every other pillar_detail leaf is a 0-100 percentile-style score

    if key.startswith("recent_form.") or key.startswith("opposing_pitcher_recent_form."):
        field_name = key.rsplit(".", 1)[-1]
        if field_name in _RATE_STAT_RECENT_FORM_FIELD_NAMES:
            return RATE_STAT_TOLERANCE
        if field_name in _ROUNDING_RECENT_FORM_FIELD_NAMES:
            return ROUNDING_TOLERANCE
        return EXACT_TOLERANCE  # count fields — exactness is the fact being claimed

    return EXACT_TOLERANCE


# Real bug found via the deliberate adversarial test (2026-08-04): a flat
# ROUNDING_TOLERANCE=0.5 applied near a real value of 0 (e.g.
# wind_speed_mph=0 on a closed-roof game) creates a wide, silently-
# permissive band around zero — a fabricated ".385 batting average" sits
# well within 0.5 of a real 0 and passed as "grounded" by pure numeric
# coincidence, despite having nothing to do with wind speed. 0.5 is the
# right tolerance for "did this round reasonably" when the real value is
# large enough that rounding is a meaningful operation (71.2 -> "71"); at
# or near zero, rounding isn't really happening — the reasonable written
# form of a real 0 is just "0" — so the tolerance should shrink toward a
# much tighter floor rather than staying at a flat 0.5 regardless of
# magnitude. NEAR_ZERO_TOLERANCE_FLOOR is an order of magnitude tighter
# than ROUNDING_TOLERANCE specifically so a fabricated number from a
# genuinely different scale (a batting average, an ERA) can't hide in the
# neighborhood of an unrelated real zero.
NEAR_ZERO_TOLERANCE_FLOOR = 0.05


def _rounding_tolerance_for_value(real_value: float) -> float:
    """
    Scales ROUNDING_TOLERANCE down as the real value approaches zero,
    floored at NEAR_ZERO_TOLERANCE_FLOOR rather than degenerating into a
    flat, magnitude-independent 0.5. Values at or beyond
    ROUNDING_TOLERANCE itself are unaffected (still get the full 0.5,
    same as before this fix) — this only tightens the specific danger
    zone the adversarial test exposed, not rounding behavior generally.

    tolerance = max(floor, min(ROUNDING_TOLERANCE, abs(real_value)))

    real_value=0     -> 0.05 (floor)
    real_value=0.3   -> 0.3  (tapers linearly between the floor and the cap)
    real_value=0.5+  -> 0.5  (unchanged — full rounding tolerance, as before)
    real_value=71.2  -> 0.5  (unchanged)
    """
    return max(NEAR_ZERO_TOLERANCE_FLOOR, min(ROUNDING_TOLERANCE, abs(real_value)))


def _source_numbers_with_tolerance(source_facts: dict, tolerance_for_key) -> list:
    """One (number, tolerance) pair per real number found across every
    source fact — tolerance is looked up per KEY via the injected
    tolerance_for_key callable (see validate_numeric_grounding's own
    docstring for why this is a callable parameter, not a config object),
    so a claimed number is judged against the precision expectation that's
    actually right for whichever real value it's closest to, not one flat
    tolerance applied to every kind of fact regardless of what it is.
    ROUNDING_TOLERANCE specifically is further scaled per-value via
    _rounding_tolerance_for_value() to avoid the near-zero degeneracy
    above — the other two tiers (EXACT_TOLERANCE, RATE_STAT_TOLERANCE) are
    already tight enough in absolute terms that they don't have the same
    problem at zero (0.01 or 0.02 around a real 0 is not a meaningful gap
    to exploit)."""
    pairs = []
    for key, value in source_facts.items():
        base_tolerance = tolerance_for_key(key)
        for n in _numbers_in(value):
            tol = _rounding_tolerance_for_value(n) if base_tolerance == ROUNDING_TOLERANCE else base_tolerance
            pairs.append((n, tol))
    return pairs


def validate_numeric_grounding(why_reasons: list, source_facts: dict, tolerance_for_key) -> list[dict]:
    """
    PARAMETERIZED (NFL Content Generation Part B): `tolerance_for_key` is
    now an injected callable (str -> float) instead of this function
    always calling the module-level _tolerance_for_key by name. MLB's
    real call sites pass mlb_tolerance_for_key (see its own docstring for
    why a callable, not a config object, was the right shape here) —
    behavior for MLB is unchanged, confirmed by a real before/after
    regression diff, not just passing tests.

    Every number appearing in a reason's prose must appear somewhere among
    the real source-fact VALUES, within a tolerance appropriate to that
    specific value's type (see the injected tolerance_for_key). This is
    defense in depth on top of validate_citations() — a model can cite a
    real key and still misstate its value; this catches that case
    specifically.

    TOLERANCE IS PER-FIELD, NOT FLAT: a percentage/score-type value
    (skill_score, the pillar_detail percentile components, temp_f,
    wind_speed_mph) tolerates rounding to the nearest whole number —
    writing "71%" for a real 71.2 is normal interpretation backed by a
    real receipt, not fabrication. Odds, and anything else where
    exactness itself is the claim (batting_order_slot, star_rating, count
    stats like recent_games_sampled), keep tight/exact matching — a real
    +650 must never quietly pass as grounded for a stated +600. Rate
    stats (OPS, ERA, per-9 rates) get their own small tolerance tier —
    see _tolerance_for_key's docstring for why they fit neither of the
    other two.

    Deliberately excludes single-digit numbers 1-5 from being flagged as
    ungrounded on their own — star counts, "top-3", and small narrative
    counts (e.g. "his 2nd homer of the week" written without an ordinal
    suffix) are common, low-stakes, and a realistic false-positive source
    at this magnitude; this check is tuned to catch a model inventing a
    specific, consequential-looking statistic (a percentage, an exit
    velocity, an odds price), not to flag every small integer in prose.

    COMPARATIVE/THRESHOLD PHRASING IS CHECKED SEPARATELY, before the plain
    point-value pass: "both sit above 90" is validated as "does a real
    cited value actually exceed 90" (see _comparative_claim_satisfied,
    scoped to THIS reason's own source_fact_keys — not the whole
    candidate, or a high threshold becomes nearly impossible to falsify
    against unrelated real fields like odds), not forced through the
    point-value rounding check that a real 90.7/90.8 would otherwise fail
    (they round to 91, not 90 — a genuine false positive found in
    production; see _extract_comparative_claims' docstring). Numbers
    consumed by a detected comparative phrase are excluded from the
    point-value pass so they're never double-checked.

    "/9" AND "PER 9" NOTATION IS EXCLUDED ENTIRELY, not validated as a
    claim at all — see _PER_NINE_PATTERN's docstring. The "9" in "1.47
    HR/9" or "walking 6 per 9" is a unit denominator (per nine innings
    pitched), not a separate factual number, and word-boundary-anchoring
    keeps this narrow: a genuinely different number that happens to
    follow a slash or the word "per" for an unrelated reason is untouched.

    ORDINALS ("3rd", "89th", "21st") ARE EXCLUDED BY SPAN, not by a
    lookahead inside _NUMBER_PATTERN itself — see _NUMBER_PATTERN's own
    docstring for the real multi-digit-ordinal bug this replaced (found
    during NFL Content Generation Part C's validation, not NFL-specific).
    """
    source_pairs = _source_numbers_with_tolerance(source_facts, tolerance_for_key)

    violations = []
    for i, reason in enumerate(why_reasons):
        text = reason.get("reason_text", "")

        cited_facts = {k: source_facts[k] for k in (reason.get("source_fact_keys") or []) if k in source_facts}

        comparative_claims = _extract_comparative_claims(text)
        exclude_spans = [span for _, _, span in comparative_claims]
        for comparator, claimed_value, _ in comparative_claims:
            if not _comparative_claim_satisfied(comparator, claimed_value, cited_facts):
                violations.append({
                    "reason_index": i,
                    "issue": (
                        f"comparative claim '{comparator} {claimed_value}' in reason_text is not "
                        f"satisfied by any of this reason's own cited source fact values"
                    ),
                })

        # "/9" and "per 9" per-nine-innings notation -- the "9" is a unit
        # denominator, not a claimed fact, so it's excluded from grounding
        # the same way a comparative-claim number is. Never validated
        # against source facts at all (unlike comparative claims, there's
        # no "claim" here to check) -- just excluded from the point-value
        # pass below.
        exclude_spans += [m.span(1) for m in _PER_NINE_PATTERN.finditer(text)]
        exclude_spans += [m.span() for m in _ORDINAL_PATTERN.finditer(text)]

        for m in _NUMBER_PATTERN.finditer(text):
            if any(m.start() >= lo and m.end() <= hi for lo, hi in exclude_spans):
                continue  # already validated as a comparative claim, or is per-nine notation, above
            n = round(float(m.group()), 2)
            if 1 <= n <= 5 and n == int(n):
                continue  # small narrative integer, not flagged — see docstring
            if not any(abs(n - sn) <= tol for sn, tol in source_pairs):
                violations.append({
                    "reason_index": i,
                    "issue": (
                        f"number {n} in reason_text does not appear in any real source fact "
                        f"value within that value's expected tolerance"
                    ),
                })
    return violations


def _expected_star_range(real_score) -> tuple:
    """
    Coarse, deliberately generous banding from a real 0-100 pillar score to
    a reasonable star range — mirrors confidence_band_for_score()'s
    thresholds loosely, but this is a sanity range, not a precise mapping;
    the goal is catching an obviously ungrounded claim (a 5-star reason
    built on a real score of 22), not enforcing a rigid formula the model
    has to hit exactly.
    """
    if real_score is None:
        return (1, 5)  # no real score to check against — nothing to flag
    if real_score >= 75:
        return (3, 5)
    if real_score >= 60:
        return (2, 5)
    if real_score >= 40:
        return (1, 4)
    if real_score >= 25:
        return (1, 3)
    return (1, 2)


# {pillar_name: source_fact_key holding that pillar's real 0-100 score}
# — the mapping validate_star_consistency uses to look up a pillar's real
# score, now via already-flattened source_facts rather than reaching back
# into candidate["pillar_detail"] directly (see validate_star_consistency's
# own docstring for why this generalizes more cleanly than an MLB-specific
# nested-dict lookup would). Derived from PILLAR_NAMES + flatten_source_
# facts' own dotted-path convention ("pillar_detail.{pillar}.score"), so
# it can never drift out of sync with what flatten_source_facts actually
# produces for MLB.
STAR_PILLAR_SCORE_KEYS = {p: f"pillar_detail.{p}.score" for p in PILLAR_NAMES}


def validate_star_consistency(why_reasons: list, source_facts: dict, pillar_names: tuple, pillar_score_keys: dict) -> list[dict]:
    """
    PARAMETERIZED (NFL Content Generation Part B) — REWORKED, not just a
    parameter swap: previously took `candidate` and reached directly into
    candidate["candidate"]["pillar_detail"][pillar]["score"], an MLB-only
    nested shape NFL has no equivalent of at all. Now takes the already-
    flattened `source_facts` (the SAME dict every other validator already
    operates on) plus `pillar_score_keys` — a {pillar_name: source_fact_
    key} mapping telling this function which flat key holds each pillar's
    real score. MLB passes STAR_PILLAR_SCORE_KEYS above (built from
    flatten_source_facts' own "pillar_detail.{pillar}.score" convention,
    so it's guaranteed to match what flatten_source_facts actually
    produced — not a second, independently-typed path to the same data).
    A future NFL writer (Part C) would pass its own flat mapping (e.g.
    {"td_opportunity": "td_opportunity"} — no nesting to point through at
    all) and needs no other change to this function.

    Confirmed byte-identical behavior for MLB by construction: source_
    facts["pillar_detail.{pillar}.score"] holds exactly the same value the
    old code's pillar_detail.get(pillar, {}).get("score") did (both are
    None when the pillar has no "score" key, or when the pillar isn't
    present at all) — verified with a real before/after regression diff,
    not assumed from the construction alone.

    A reason's stated `stars` must fall within a reasonable range for
    the REAL score of the pillar it's tagged with — treats the star rating
    itself as a claim that needs grounding, not just the prose.
    """
    violations = []
    for i, reason in enumerate(why_reasons):
        pillar = reason.get("pillar")
        stars = reason.get("stars")
        if pillar not in pillar_names or stars is None:
            continue  # a missing/invalid pillar or stars is a schema-shape violation, not this check's job
        real_score = source_facts.get(pillar_score_keys.get(pillar))
        lo, hi = _expected_star_range(real_score)
        if not (lo <= stars <= hi):
            violations.append({
                "reason_index": i,
                "issue": (
                    f"{stars} stars claimed for pillar {pillar!r} (real score={real_score}), "
                    f"expected roughly {lo}-{hi} stars for that real score"
                ),
            })
    return violations


# ---------------------------------------------------------------------------
# The real Claude API call — forced tool-use, raw requests (no SDK,
# matching this pipeline's existing style elsewhere). Generic over which
# tool schema is being forced, so every writer type shares one real,
# tested implementation of the actual network call.
# ---------------------------------------------------------------------------

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
MODEL_NAME = "claude-sonnet-5"
MAX_TOKENS = 1024
REQUEST_TIMEOUT_SECONDS = 60


def call_claude_with_tool(api_key: str, system_prompt: str, user_prompt: str, tool_schema: dict) -> dict:
    """
    One real Claude API call, forced tool-use against the given
    tool_schema — structured output enforced by the API itself, never
    parsed from free-form prose. Raises ValueError with Claude's real
    response body on any 4xx/5xx (surfacing the actual error detail, e.g.
    "credit balance is too low", rather than a generic HTTP status line —
    a real bug found and fixed 2026-08-04). Raises ValueError if the model
    response somehow has no tool_use block for the requested tool —
    shouldn't happen with tool_choice forced, but not assumed.
    """
    response = requests.post(
        ANTHROPIC_API_URL,
        headers={
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        },
        json={
            "model": MODEL_NAME,
            "max_tokens": MAX_TOKENS,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
            "tools": [tool_schema],
            "tool_choice": {"type": "tool", "name": tool_schema["name"]},
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        raise ValueError(f"Claude API returned {response.status_code}: {response.text}")
    data = response.json()

    for block in data.get("content", []):
        if block.get("type") == "tool_use" and block.get("name") == tool_schema["name"]:
            return block["input"]

    raise ValueError(f"Claude response had no {tool_schema['name']} tool_use block: {data}")
