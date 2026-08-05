"""
Content Writer, Phase 4 catch-up — tasty_six_writer_schema.py.

REFACTORED 2026-08-04: everything generic to any writer type (facts
extraction, citation/numeric-grounding/star-consistency validation, the
shared why_reasons schema block, the actual Claude API call) moved to
card_writer_common.py — a second writer type (regular shelf cards) needed
all of it too, and duplicating it would mean every future fix (this file
already has two real ones from tonight) has to be applied twice and can
silently drift. This is a pure refactor: behavior is unchanged, confirmed
by re-running the full pre-existing test suite before and after and
diffing the output (see the pipeline README).

WHAT'S LEFT HERE, genuinely Tasty-Six-specific: the tool schema's
`editorial_sentence` field (the hybrid title+sentence format was
specifically a Tasty Six decision, not the general card format — see
shelf_card_writer_schema.py for the simpler title+why_reasons-only
format), and validate_schema_shape() (which checks for it).

Scoped specifically to the Tasty Six card writer — a "regular shelf card"
writer, if built later, gets its own schema file rather than this one
growing to cover both. (It has been built — see shelf_card_writer_schema.py.)
"""
from card_writer_common import MAX_WHY_REASONS, MIN_WHY_REASONS, PILLAR_NAMES, WHY_REASONS_ARRAY_SCHEMA

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
            "why_reasons": WHY_REASONS_ARRAY_SCHEMA,
        },
        "required": ["title", "editorial_sentence", "why_reasons"],
    },
}


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
