"""
NFL Content Generation V1, Part 1 — nfl_shelf_card_writer_schema.py.

NAMED nfl_shelf_card_writer_schema.py, not shelf_card_writer_schema.py —
same real module-name-collision fix nfl_tasty_six_writer_schema.py's own
docstring already documents (nfl/ and pipeline/ both on sys.path at once
means a same-named MLB file can silently win the sys.modules cache
regardless of import-time sys.path order). Every NFL file that shares an
MLB filename here gets a distinct name for exactly that reason.

NFL's own version of pipeline/api/content_writer/shelf_card_writer_
schema.py — same shape (title + why_reasons only, NO editorial_sentence:
that hybrid format is Tasty-Six-specific, per shelf_card_prompt.py's own
comment and shape_content_draft_rows' existing "editorial_sentence stays
None for these rows" convention, both confirmed before this file was
written, not assumed) and same tool-use contract discipline as nfl_tasty
_six_writer_schema.py. Own tool name (emit_nfl_shelf_card) and NFL's own
PILLAR_NAMES enum (content_writer.nfl_writer_common.NFL_PILLAR_NAMES —
the SAME 5-pillar enum Tasty Six already uses, unchanged; Part 1 needed
no new pillar names, see editorial_lenses.py's own docstring for how
`situation`/evidence_quality reconcile against this existing enum).

SAME KNOWN GAP nfl_tasty_six_writer_schema.py already flags, not fixed
here either: the cross-imported WHY_REASONS_ARRAY_SCHEMA's embedded
`pillar.enum` still lists MLB's four pillar names at the JSON-schema
level; validate_schema_shape below is what actually enforces NFL's real
pillar names, same defense-in-depth discipline as Tasty Six's own
version.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from card_writer_common import MAX_WHY_REASONS, MIN_WHY_REASONS, WHY_REASONS_ARRAY_SCHEMA  # noqa: E402

from nfl_writer_common import NFL_PILLAR_NAMES  # noqa: E402

NFL_SHELF_CARD_TOOL_SCHEMA = {
    "name": "emit_nfl_shelf_card",
    "description": (
        "Emit one regular shelf card for a single real, curated NFL anytime-touchdown "
        "candidate. Every claim in why_reasons must be traceable to the real "
        "source facts provided — never invent a stat, trend, or fact not present "
        "in the source data."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "The headline for this card — see voice/nfl_shelf_personalities.py for this shelf's vocabulary/imagery pool and emotional_intensity.py for this confidence band's title register.",
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
    tool-use alone — same discipline as nfl_tasty_six_writer_schema.py's
    version, minus editorial_sentence (this writer type never has one).
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
        # Deliberately NOT returning here -- same reasoning as Tasty Six's
        # own version: a reviewer should see every real problem in one pass.

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
