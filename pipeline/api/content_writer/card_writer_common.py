"""
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
# rather than flattened.
_RECENT_FORM_SKIP_FIELDS = {"recent_window_dates"}


def flatten_source_facts(candidate: dict) -> dict:
    """
    Turns one real curated candidate (a shelf_assignments/Tasty-Six entry,
    itself wrapping a scored_picks-shaped candidate dict under "candidate"
    per shelf_curation.py's real entry shape, OR a bare scored_picks-shaped
    dict) into the flat, citable {key: value} set a writer call is allowed
    to reference. Dotted paths for pillar_detail's nested components (e.g.
    "pillar_detail.skill.components.power_production") and for recent-form
    fields (e.g. "recent_form.recent_ops") give citations real granularity
    — pointing at the specific number a claim is about, not just the
    parent pillar's overall score or an opaque nested blob.
    """
    c = candidate.get("candidate", candidate)  # unwrap a shelf-entry shape if present

    facts = {}
    for key in TOP_LEVEL_CITABLE_FIELDS:
        if key in c and c[key] is not None:
            facts[key] = c[key]

    pillar_detail = c.get("pillar_detail") or {}
    for pillar_name, pillar_data in pillar_detail.items():
        if not isinstance(pillar_data, dict):
            continue
        if "score" in pillar_data:
            facts[f"pillar_detail.{pillar_name}.score"] = pillar_data["score"]
        components = pillar_data.get("components") or {}
        for comp_key, comp_val in components.items():
            facts[f"pillar_detail.{pillar_name}.components.{comp_key}"] = comp_val

    for key in RECENT_FORM_CITABLE_FIELDS:
        form = candidate.get(key)
        if not isinstance(form, dict):
            continue
        for field_name, field_val in form.items():
            if field_name in _RECENT_FORM_SKIP_FIELDS or field_val is None:
                continue
            facts[f"{key}.{field_name}"] = field_val

    return facts


# ---------------------------------------------------------------------------
# Validation — each function returns a list of violation dicts (empty =
# clean), same "explainable, not a black box" discipline as
# banned_language.find_banned_phrases().
# ---------------------------------------------------------------------------

def validate_citations(why_reasons: list, source_facts: dict) -> list[dict]:
    """Every cited key must be a real key that exists in source_facts. A
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
_NUMBER_PATTERN = re.compile(r"(?<![a-zA-Z])-?\d*\.?\d+(?!\s*(?:st|nd|rd|th)\b)")


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


def _tolerance_for_key(key: str) -> float:
    """
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


def _source_numbers_with_tolerance(source_facts: dict) -> list:
    """One (number, tolerance) pair per real number found across every
    source fact — tolerance is looked up per KEY via _tolerance_for_key(),
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
        base_tolerance = _tolerance_for_key(key)
        for n in _numbers_in(value):
            tol = _rounding_tolerance_for_value(n) if base_tolerance == ROUNDING_TOLERANCE else base_tolerance
            pairs.append((n, tol))
    return pairs


def validate_numeric_grounding(why_reasons: list, source_facts: dict) -> list[dict]:
    """
    Every number appearing in a reason's prose must appear somewhere among
    the real source-fact VALUES, within a tolerance appropriate to that
    specific value's type (see _tolerance_for_key). This is defense in
    depth on top of validate_citations() — a model can cite a real key and
    still misstate its value; this catches that case specifically.

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
    """
    source_pairs = _source_numbers_with_tolerance(source_facts)

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


def validate_star_consistency(why_reasons: list, candidate: dict) -> list[dict]:
    """A reason's stated `stars` must fall within a reasonable range for
    the REAL score of the pillar it's tagged with — treats the star rating
    itself as a claim that needs grounding, not just the prose."""
    c = candidate.get("candidate", candidate)
    pillar_detail = c.get("pillar_detail") or {}

    violations = []
    for i, reason in enumerate(why_reasons):
        pillar = reason.get("pillar")
        stars = reason.get("stars")
        if pillar not in PILLAR_NAMES or stars is None:
            continue  # a missing/invalid pillar or stars is a schema-shape violation, not this check's job
        real_score = (pillar_detail.get(pillar) or {}).get("score")
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
