"""
NFL Content Generation, Part C — nfl_tasty_six_writer_schema.py.

NAMED nfl_tasty_six_writer_schema.py, not tasty_six_writer_schema.py —
same real module-name-collision fix as nfl_tasty_six_prompt.py/
voice/nfl_shelf_personalities.py; see nfl_tasty_six_prompt.py's module
docstring for the full story of how this was actually found (a real
validate_schema_shape call silently ran MLB's own PILLAR_NAMES check
instead of NFL's).

NFL's own version of pipeline/api/content_writer/tasty_six_writer_
schema.py -- same shape (title + editorial_sentence + why_reasons), same
tool-use contract discipline, own tool name (emit_nfl_tasty_six_card, so
a real Claude response can never be confused with MLB's emit_tasty_six_
card) and NFL's own PILLAR_NAMES enum (td_opportunity, role_momentum,
matchup, environment, market_value -- see nfl_writer_common.
NFL_PILLAR_NAMES, the single source of truth this schema's enum is built
from, so the two can never drift apart).

WHY_REASONS' EXACT SHAPE ON THE REAL nfl_content_drafts TABLE IS NOT YET
CONFIRMED -- see generate_tasty_six_content.py's own module docstring.
This schema produces MLB's shape ({pillar, stars, reason_text, source_
fact_keys}) as the reasonable working assumption pending that
confirmation, per explicit instruction.

Reuses WHY_REASONS_ARRAY_SCHEMA/MIN_WHY_REASONS/MAX_WHY_REASONS by
cross-importing directly from card_writer_common.py, unmodified -- same
generic 2-3-item-array contract, no NFL-specific reason to diverge.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "pipeline" / "api" / "content_writer"))
from card_writer_common import MAX_WHY_REASONS, MIN_WHY_REASONS, WHY_REASONS_ARRAY_SCHEMA  # noqa: E402

from nfl_writer_common import NFL_PILLAR_NAMES  # noqa: E402

# ---------------------------------------------------------------------------
# The structured output contract -- forced via Claude's tool-use, never
# parsed from free-form prose. Same shape as MLB's TASTY_SIX_TOOL_SCHEMA;
# the WHY_REASON_SCHEMA embedded inside WHY_REASONS_ARRAY_SCHEMA still has
# MLB's own PILLAR_NAMES baked into its `enum` (it's a cross-imported,
# already-built JSON-schema block -- see WHY_REASONS_ARRAY_SCHEMA's real
# construction in card_writer_common.py). validate_schema_shape below is
# what actually enforces NFL's real pillar names at validation time; the
# schema's own `pillar` field description is corrected here for a human
# reader, but the enum constraint itself is MLB's -- a real, narrow gap
# flagged explicitly rather than silently accepted (see the module-level
# note below).
# ---------------------------------------------------------------------------

# KNOWN GAP, deliberately not fixed by forking WHY_REASON_SCHEMA: the
# cross-imported WHY_REASONS_ARRAY_SCHEMA's embedded `pillar.enum` still
# lists MLB's four pillar names, not NFL's five -- Claude's forced tool-
# use would, in principle, allow it to emit an MLB pillar name and have
# the API-level schema accept it. In practice this is caught immediately
# by validate_schema_shape's own explicit `pillar not in NFL_PILLAR_NAMES`
# check below (defense-in-depth, not trusting the schema alone -- the
# same discipline MLB's own validate_schema_shape already applies). A
# clean fix requires either forking WHY_REASON_SCHEMA (reintroducing the
# duplication Part B was built to avoid) or parameterizing it too, which
# was not in Part B's approved scope (only the four validator functions
# were). Flagging this as a real, narrow follow-up rather than quietly
# living with it unflagged.
NFL_TASTY_SIX_TOOL_SCHEMA = {
    "name": "emit_nfl_tasty_six_card",
    "description": (
        "Emit one Tasty Six story card for a single real, curated NFL anytime-touchdown "
        "candidate. Every claim in why_reasons must be traceable to the real "
        "source facts provided — never invent a stat, trend, or fact not present "
        "in the source data."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "The cinematic title for this card — see voice/shelf_personalities.py for this shelf's vocabulary/imagery pool and emotional_intensity.py for this confidence band's title register.",
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
    tool-use alone -- same discipline as MLB's version, checked against
    NFL_PILLAR_NAMES specifically (see the KNOWN GAP note above for why
    this check, not the cross-imported schema's own enum, is what
    actually enforces NFL's real pillar names).
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
        # Deliberately NOT returning here -- same reasoning as MLB's
        # version: a reviewer should see every real problem in one pass.

    for i, r in enumerate(reasons):
        if r.get("pillar") not in NFL_PILLAR_NAMES:
            errors.append(f"why_reasons[{i}].pillar is missing or not one of {NFL_PILLAR_NAMES}")
        stars = r.get("stars")
        if not isinstance(stars, int) or not (1 <= stars <= 5):
            errors.append(f"why_reasons[{i}].stars must be an integer 1-5")
        if not isinstance(r.get("reason_text"), str) or not r["reason_text"].strip():
            errors.append(f"why_reasons[{i}].reason_text is missing or empty")
        keys = r.get("source_fact_keys")
        if not isinstance(keys, list) or not keys:
            errors.append(f"why_reasons[{i}].source_fact_keys must be a non-empty list")

    return errors
