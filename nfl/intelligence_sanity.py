"""
NFL Intelligence — the sanity gate, per the approved design (see the
conversation this was approved in). Deliberately its OWN module, not
folded into intelligence_schema.py's build_story() (which only enforces
that the right KEYS are present, never that their VALUES are sane — it
would happily accept trend_strength=float('nan') or sample_size=-5) and
not folded into intelligence_lifecycle.py either (a genuinely separate
concern: this checks whether ONE story is well-formed; lifecycle tracks
state ACROSS weeks for an already-accepted story).

WHY THIS EXISTS AT ALL, given every family's headline/story text is
already deterministically templated (not LLM-generated) with its own
baked-in self-consistency checks (e.g. defensive_trends.py's own
td_agrees check): the real risk category here isn't "hallucinated
citation" the way it is for Picks' LLM-written cards — it's a code-level
bug in a detection family's own math (a stale threshold, a sign flip, a
NaN/inf leaking through from a pandas groupby/shrinkage calculation on a
thin sample) producing a technically well-formed but wrong story every
week, silently, with nobody reviewing it before it's live (Intelligence
auto-publishes; there's no human review step the way Picks has).

NEVER SILENTLY DROP, per the approved design: a story failing this gate
still gets written (by the caller, not this module — see intelligence_
write.py) with sanity_check_passed=False and is_visible=False, not
skipped outright. The same reasoning Picks' validation_passed/
validation_issues already established (see content_writer/generate_
tasty_six_content.py) applies with MORE force here, not less: Picks has
a human reviewer who might eventually notice something's off; Intelligence
has nobody looking at anything before it's live, so a silent drop means a
systematic bug could produce zero visible output for a family for weeks
with no breadcrumb anywhere to even query against.
"""
import math

# entity_key_for (intelligence_lifecycle.py) already establishes which
# keys each real entity "type" needs to be identifiable at all --
# reusing that same real requirement here rather than inventing a
# second, possibly-drifting definition of "a well-formed entity dict".
_REQUIRED_ENTITY_KEYS = {
    "player": ("player_id",),
    "defense": ("team", "position_group"),
    "team": ("team",),
}

# The three STORY_FIELDS that the schema's own docstring documents as a
# real 0-100 scale (see intelligence_schema.py) -- sample_size is also
# numeric but is a real COUNT, not a percentage, so it's checked only
# for finiteness/non-negativity below, not range-checked against 0-100.
_ZERO_TO_HUNDRED_FIELDS = ("trend_strength", "completeness", "confidence")


def _is_finite_number(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def sanity_check_story(story: dict) -> list:
    """
    Family-agnostic structural/numeric sanity check on ONE already-
    built story dict (the real 13-field schema — see intelligence_
    schema.py). Deliberately does NOT re-validate the STORY_FIELDS key
    set itself (build_story() already raises hard on that at
    construction time; a story reaching this function already has
    every required key present, by construction).

    Returns a list of human-readable issue strings -- empty means the
    story passed every check. Never raises: a story with a badly-shaped
    field (e.g. entity missing entirely, or not a dict) is reported as
    an issue, the same as any other real check failure, not a crash --
    this function's whole job is to characterize badness, not to be
    another thing that can go wrong at write time.
    """
    issues = []

    for field in ("primary_signal",):
        value = story.get(field)
        if not isinstance(value, dict) or "value" not in value:
            issues.append(f"{field} is missing or not a dict with a 'value' key: {value!r}")
        elif not _is_finite_number(value["value"]):
            issues.append(f"{field}['value'] is not a finite number: {value['value']!r}")

    for field in ("trend_strength", "sample_size", "completeness", "confidence"):
        value = story.get(field)
        if not _is_finite_number(value):
            issues.append(f"{field} is not a finite number: {value!r}")
            continue
        if field in _ZERO_TO_HUNDRED_FIELDS and not (0 <= float(value) <= 100):
            issues.append(f"{field}={value!r} is outside the real documented 0-100 range")
        if field == "sample_size" and float(value) < 0:
            issues.append(f"sample_size={value!r} is negative")

    entity = story.get("entity")
    if not isinstance(entity, dict) or "type" not in entity:
        issues.append(f"entity is missing or not a dict with a 'type' key: {entity!r}")
    else:
        required = _REQUIRED_ENTITY_KEYS.get(entity["type"])
        if required is None:
            issues.append(f"entity has unrecognized type {entity['type']!r}")
        else:
            missing = [k for k in required if not entity.get(k)]
            if missing:
                issues.append(f"entity (type={entity['type']!r}) is missing required key(s): {missing}")

    for field in ("headline", "story"):
        value = story.get(field)
        if not isinstance(value, str) or not value.strip():
            issues.append(f"{field} is empty or not a string: {value!r}")

    return issues
