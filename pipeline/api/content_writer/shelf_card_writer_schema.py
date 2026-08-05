"""
Content Writer — shelf_card_writer_schema.py.

The second writer type, built 2026-08-04 on top of card_writer_common.py
(extracted from tasty_six_writer_schema.py specifically to make this
possible without duplicating validator logic). Covers every shelf card
that ISN'T a Tasty Six pick — Hot Hitters, Cold Pitchers to Attack,
Weather Factors, and the three odds-tier shelves (+300-499, +500-699,
Going Nuclear).

THE ONE REAL STRUCTURAL DIFFERENCE FROM THE TASTY SIX WRITER: no
editorial_sentence. The hybrid title+sentence format was specifically a
Tasty Six decision (the top-of-app, deep-review six cards), not the
general card format — a regular shelf card is title + star-rated why
reasons only. Everything else (citation mechanism, numeric grounding,
star consistency, banned language, shelf personality + confidence band
composition) is identical, which is exactly why it's imported from
card_writer_common.py rather than reimplemented here.
"""
from card_writer_common import MAX_WHY_REASONS, MIN_WHY_REASONS, PILLAR_NAMES, WHY_REASONS_ARRAY_SCHEMA

# ---------------------------------------------------------------------------
# The structured output contract — forced via Claude's tool-use, never
# parsed from free-form prose. Same shape as TASTY_SIX_TOOL_SCHEMA minus
# editorial_sentence.
# ---------------------------------------------------------------------------

SHELF_CARD_TOOL_SCHEMA = {
    "name": "emit_shelf_card",
    "description": (
        "Emit one regular shelf story card for a single real, curated home-run-prop "
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
            "why_reasons": WHY_REASONS_ARRAY_SCHEMA,
        },
        "required": ["title", "why_reasons"],
    },
}


def validate_schema_shape(output: dict) -> list[str]:
    """
    Defense-in-depth structural check, independent of trusting forced
    tool-use alone. Same discipline as tasty_six_writer_schema.py's
    version, minus the editorial_sentence check — there is no such field
    in this writer type's contract.
    """
    errors = []
    if not isinstance(output.get("title"), str) or not output["title"].strip():
        errors.append("title is missing or empty")

    reasons = output.get("why_reasons")
    if not isinstance(reasons, list):
        errors.append("why_reasons must be a list")
        return errors  # genuinely nothing to iterate

    if not (MIN_WHY_REASONS <= len(reasons) <= MAX_WHY_REASONS):
        errors.append(f"why_reasons must have {MIN_WHY_REASONS}-{MAX_WHY_REASONS} items, got {len(reasons)}")
        # Deliberately NOT returning here — see tasty_six_writer_schema.py's
        # identical comment: a reviewer should see every real problem in
        # one pass, not one fix-and-rerun at a time.

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
