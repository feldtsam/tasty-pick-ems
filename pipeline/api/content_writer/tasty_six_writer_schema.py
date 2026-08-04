"""
Content Writer, Phase 4 catch-up — tasty_six_writer_schema.py.

NEW, UNREVIEWED DESIGN WORK (2026-08-03), same status as everything built
tonight for Phase 5's first vertical slice. Scoped specifically to the
Tasty Six card writer, per that scope — a "regular shelf card" writer, if
built later, gets its own schema file rather than this one growing to
cover both.

WHAT THIS FILE IS: the structured output CONTRACT for one Claude API call
— the JSON schema forced via tool-use (never free-form prose parsing, per
the explicit requirement), plus the deterministic checks that verify a
real model response actually honors that contract before a human ever
sees it. Does NOT build the prompt, does NOT call the API — that's the
generation-endpoint phase, sequenced after this, the content_drafts
migration, and the curate-shelves include_rows addition.

THE CITATION MECHANISM: fact-support validation is a mechanical existence
check, not a text-mining problem. Every "why" reason must name which
specific source-fact key(s) it draws from
(`source_fact_keys`) — `flatten_source_facts()` defines the exact,
deterministic set of real keys available to cite from a real scored-pick
candidate, and `validate_citations()` just checks each cited key actually
exists in that set. A model that invents a stat has nowhere real to point
it at; a cited key that doesn't exist is a hard, explainable failure, not
a judgment call.

TWO DEFENSE-IN-DEPTH LAYERS BEYOND THE CITATION CHECK ITSELF, because
citing a REAL key doesn't guarantee the prose describes that key's value
correctly:
  - `validate_numeric_grounding()` — every number appearing in a reason's
    prose must appear somewhere in the real source-fact values. Catches a
    model that cites `pillar_detail.skill.components.power_production`
    but states a number nowhere close to what that key's real value is.
  - `validate_star_consistency()` — a reason's stated `stars` (1-5) must
    fall within a reasonable range for the REAL score of the pillar it's
    tagged with. Catches a 5-star claim built on a mediocre real number.

Both are real but imperfect — see each function's own docstring for what
they deliberately do and don't catch, and why (ordinals, star counts,
and other common small narrative numbers are excluded from numeric
grounding specifically to avoid drowning a reviewer in false positives,
the same "locked in" vs. "lock" precision discipline banned_language.py
was built with).

FIELD SCOPE NOTE: "five-pillar model" per the blueprint (§6) includes
Market Intelligence as a fifth pillar, but this whole project has
deliberately scoped Market Intelligence out from the start (never
implemented in score_candidate.py). PILLAR_NAMES below reflects the real
four pillars this pipeline actually scores, not the blueprint's full
five.
"""
import re

PILLAR_NAMES = ("skill", "matchup", "environment", "opportunity")

MIN_WHY_REASONS = 2
MAX_WHY_REASONS = 3

# ---------------------------------------------------------------------------
# The structured output contract — forced via Claude's tool-use, never
# parsed from free-form prose.
# ---------------------------------------------------------------------------

TASTY_SIX_TOOL_SCHEMA = {
    "name": "emit_tasty_six_card",
    "description": (
        "Emit one Tasty Six story card for a single real, curated home-run-prop "
        "candidate. Every claim in why_reasons must be traceable to the real "
        "source facts provided — never invent a stat, trend, or fact not present "
        "in the source data."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "The cinematic title for this card — see shelf_personalities.py for this shelf's vocabulary/imagery pool and emotional_intensity.py for this confidence band's title register.",
                "minLength": 1,
                "maxLength": 120,
            },
            "editorial_sentence": {
                "type": "string",
                "description": "One sharper, more specific supporting line beneath the title — grounded in a real fact from the source data, not a generic restatement of the title.",
                "minLength": 1,
                "maxLength": 280,
            },
            "why_reasons": {
                "type": "array",
                "minItems": MIN_WHY_REASONS,
                "maxItems": MAX_WHY_REASONS,
                "description": "2-3 of the strongest real pillars behind this pick, each a real 'receipt', not a verdict.",
                "items": {
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
                },
            },
        },
        "required": ["title", "editorial_sentence", "why_reasons"],
    },
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

# Present only on Tasty Six picks sourced from the Hot Hitters or Cold
# Pitchers to Attack shelves specifically — see shelf_curation.py's
# _hot_hitters_shelf()/_cold_pitchers_shelf(), which attach these as extra
# fields on the shelf entry, not on the underlying scored_picks candidate
# itself. Included here so a real recent-form stat is citable when present,
# not silently unavailable depending on which shelf a Tasty Six pick came from.
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


def _source_numbers_with_tolerance(source_facts: dict) -> list:
    """One (number, tolerance) pair per real number found across every
    source fact — tolerance is looked up per KEY via _tolerance_for_key(),
    so a claimed number is judged against the precision expectation that's
    actually right for whichever real value it's closest to, not one flat
    tolerance applied to every kind of fact regardless of what it is."""
    pairs = []
    for key, value in source_facts.items():
        tol = _tolerance_for_key(key)
        for n in _numbers_in(value):
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
    """
    source_pairs = _source_numbers_with_tolerance(source_facts)

    violations = []
    for i, reason in enumerate(why_reasons):
        text_numbers = _numbers_in(reason.get("reason_text", ""))
        for n in text_numbers:
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


def validate_schema_shape(output: dict) -> list[str]:
    """
    Defense-in-depth structural check, independent of trusting forced
    tool-use alone — mirrors this pipeline's habit of validating a JSON
    payload's shape even when a schema "should" already guarantee it (see
    every Lovable route's Zod schema for the same discipline on the other
    side of this boundary).
    """
    errors = []
    if not isinstance(output.get("title"), str) or not output["title"].strip():
        errors.append("title is missing or empty")
    if not isinstance(output.get("editorial_sentence"), str) or not output["editorial_sentence"].strip():
        errors.append("editorial_sentence is missing or empty")

    reasons = output.get("why_reasons")
    if not isinstance(reasons, list):
        errors.append("why_reasons must be a list")
        return errors  # genuinely nothing to iterate

    if not (MIN_WHY_REASONS <= len(reasons) <= MAX_WHY_REASONS):
        errors.append(f"why_reasons must have {MIN_WHY_REASONS}-{MAX_WHY_REASONS} items, got {len(reasons)}")
        # Deliberately NOT returning here — a reviewer fixing a
        # too-short/too-long why_reasons list should see every other real
        # problem with its items in the same pass, not discover them one
        # fix-and-rerun at a time.

    for i, r in enumerate(reasons):
        if r.get("pillar") not in PILLAR_NAMES:
            errors.append(f"why_reasons[{i}].pillar is missing or not one of {PILLAR_NAMES}")
        stars = r.get("stars")
        if not isinstance(stars, int) or not (1 <= stars <= 5):
            errors.append(f"why_reasons[{i}].stars must be an integer 1-5")
        if not isinstance(r.get("reason_text"), str) or not r["reason_text"].strip():
            errors.append(f"why_reasons[{i}].reason_text is missing or empty")
        keys = r.get("source_fact_keys")
        if not isinstance(keys, list) or not keys:
            errors.append(f"why_reasons[{i}].source_fact_keys must be a non-empty list")

    return errors
