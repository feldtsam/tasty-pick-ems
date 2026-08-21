"""
NFL Content Generation, Part C — generate_tasty_six_content.py.

Ties everything built for NFL's Tasty Six writer into one real call:
prompt construction (tasty_six_prompt.py) -> a real Claude API call
(forced tool-use against NFL_TASTY_SIX_TOOL_SCHEMA, via card_writer_
common.call_claude_with_tool(), cross-imported and reused unmodified) ->
the full deterministic validation suite (schema shape, citations, numeric
grounding, star consistency, banned language) -> shaping the result into
a content_drafts-ready row shape.

CONFIDENCE BAND IS A REQUIRED, EXPLICIT PARAMETER HERE, NOT DERIVED
INTERNALLY FROM tpe_score -- deliberately, and temporarily. MLB's
equivalent function derives its band internally via confidence_band_
for_score(final_score). NFL's analog of that function does not exist yet
-- Part C's own investigation found tpe_score's real distribution is
structurally different from final_score's (see the conversation this was
reported in: tpe_score's real historical max is 85.7, never near 90;
among the ONLY population that ever reaches this writer -- tpe_score>=55,
evidence_quality>=65, the approved Tasty Six gate -- the real range is
[55.0, 85.7]) and proposed new band thresholds grounded in that real
distribution, but those specific numbers are PENDING APPROVAL, per
explicit instruction ("wait for approval on the specific numbers before
hardcoding them into the confidence-band logic"). Taking confidence_band
as a direct parameter here, rather than writing a not-yet-approved
scoring function, lets every OTHER piece of this module (prompt
construction, the real Claude call, every validator) be built and
validated now. Once the thresholds are approved, wiring in real internal
derivation is a small, additive change to generate_nfl_tasty_six_draft
below (replace the confidence_band parameter with a tpe_score parameter
and one internal function call) -- not a rewrite.

WHY_REASONS' EXACT SHAPE ON nfl_content_drafts IS UNCONFIRMED -- see
tasty_six_writer_schema.py's own docstring. This module's output shape
(and the {pillar, stars, reason_text, source_fact_keys} shape within it)
is the reasonable working assumption pending Sam's direct confirmation,
per explicit instruction. draft_for_write() below is shaped to match
that assumption and is easy to adjust once confirmed.

NEVER auto-approves or publishes anything -- same reasoning as MLB's
version. review_status is set mechanically from validation_passed. Does
NOT itself write to nfl_content_drafts -- explicitly out of scope for
this task (a separate, not-yet-built write connection).
"""
import sys
from pathlib import Path

_PIPELINE_CONTENT_WRITER = Path(__file__).resolve().parent.parent.parent / "pipeline" / "api" / "content_writer"
sys.path.insert(0, str(_PIPELINE_CONTENT_WRITER))
sys.path.insert(0, str(_PIPELINE_CONTENT_WRITER / "voice"))

from banned_language import find_banned_phrases  # noqa: E402 -- reused unmodified
from card_writer_common import (  # noqa: E402
    MODEL_NAME,
    call_claude_with_tool,
    flatten_source_facts,
    validate_citations,
    validate_numeric_grounding,
    validate_star_consistency,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nfl_writer_common import (  # noqa: E402
    NFL_PILLAR_NAMES,
    NFL_STAR_PILLAR_SCORE_KEYS,
    NFL_TOP_LEVEL_CITABLE_FIELDS,
    build_nfl_writer_candidate,
    nfl_tolerance_for_key,
)
from nfl_tasty_six_prompt import build_system_prompt, build_user_prompt  # noqa: E402
from nfl_tasty_six_writer_schema import NFL_TASTY_SIX_TOOL_SCHEMA, validate_schema_shape  # noqa: E402

WRITER_TYPE = "nfl_tasty_six"


def call_claude_for_nfl_tasty_six_card(api_key: str, system_prompt: str, user_prompt: str) -> dict:
    """Thin, named wrapper around the shared call_claude_with_tool() --
    same shape as MLB's own entry point."""
    return call_claude_with_tool(api_key, system_prompt, user_prompt, NFL_TASTY_SIX_TOOL_SCHEMA)


def run_all_validators(output: dict, source_facts: dict) -> list:
    """
    Every deterministic check, combined into one real violation list --
    schema shape first (nothing else is safe to check against a malformed
    shape), then citations, numeric grounding, and star consistency
    (all three via Part B's parameterized card_writer_common functions,
    with NFL's own parameters wired in below), then banned language
    checked against every real user-facing string the card produces.

    No `candidate` parameter here (unlike MLB's version, which still
    accepts one, unused, for call-site-stability reasons specific to
    MLB's existing callers -- see generate_tasty_six_content.py's own
    note). NFL has no pre-existing callers to stay compatible with, so
    this signature is already the clean, final shape -- star_consistency
    reads source_facts directly, same as every other validator here.
    """
    issues = []

    shape_errors = validate_schema_shape(output)
    issues.extend({"check": "schema_shape", "issue": e} for e in shape_errors)
    if shape_errors:
        return issues

    why_reasons = output["why_reasons"]

    issues.extend({"check": "citation", **v} for v in validate_citations(why_reasons, source_facts))
    issues.extend({"check": "numeric_grounding", **v} for v in validate_numeric_grounding(why_reasons, source_facts, nfl_tolerance_for_key))
    issues.extend({"check": "star_consistency", **v} for v in validate_star_consistency(why_reasons, source_facts, NFL_PILLAR_NAMES, NFL_STAR_PILLAR_SCORE_KEYS))

    banned_targets = [("title", output["title"]), ("editorial_sentence", output["editorial_sentence"])]
    banned_targets += [(f"why_reasons[{i}].reason_text", r["reason_text"]) for i, r in enumerate(why_reasons)]
    for field, text in banned_targets:
        found = find_banned_phrases(text)
        if found:
            issues.append({"check": "banned_language", "field": field, "phrases": found})

    return issues


def generate_nfl_tasty_six_draft(
    row: dict, shelf: str, confidence_band: str, anthropic_api_key: str,
    debug_inject_violation_instruction: str = None, avoid_headlines: list[str] | None = None,
) -> dict:
    """
    The full pipeline for one real NFL Tasty Six candidate.

    `row`: one real scored weekly row (a dict or pandas Series) for a
    player who cleared the approved Tasty Six gate (tpe_score>=55,
    evidence_quality>=65) and is that shelf's real pick -- see curate_
    home_shelves.select_tasty_six(). MUST already have shelves.
    add_red_zone_trend_windows()'s three trailing-window columns merged
    on if `shelf` is a trend shelf whose story cites them (Red Zone
    Trends specifically) -- same prep Part A's _story_for_row needs; not
    re-derived here, since a caller iterating multiple Tasty Six picks
    should only pay that cost once, not once per player.
    `shelf`: the real home shelf this pick came from (one of curate_
    home_shelves.SHELF_ORDER).
    `confidence_band`: TEMPORARY explicit parameter -- see module
    docstring for why this isn't derived internally yet.

    `debug_inject_violation_instruction`/`avoid_headlines`: same testing/
    real-batch-variety mechanisms as MLB's version.

    Returns:
      {
        "player_id":..., "shelf":..., "writer_type": "nfl_tasty_six",
        "title":..., "editorial_sentence":..., "why_reasons":...,
        "confidence_band":..., "model_name":...,
        "validation_passed": bool, "validation_issues": [...],
        "review_status": "pending_review"|"flagged",
        "_raw_model_output": {...},
      }
    """
    candidate = build_nfl_writer_candidate(row)
    source_facts = flatten_source_facts(candidate, NFL_TOP_LEVEL_CITABLE_FIELDS)
    system_prompt = build_system_prompt(shelf, confidence_band, avoid_headlines=avoid_headlines)
    user_prompt = build_user_prompt(source_facts)

    if debug_inject_violation_instruction:
        system_prompt += f"\n\nFOR THIS GENERATION ONLY, additionally: {debug_inject_violation_instruction}"

    output = call_claude_for_nfl_tasty_six_card(anthropic_api_key, system_prompt, user_prompt)
    issues = run_all_validators(output, source_facts)

    return {
        "player_id": candidate.get("player_id"),
        "shelf": shelf,
        "writer_type": WRITER_TYPE,
        "title": output.get("title"),
        "editorial_sentence": output.get("editorial_sentence"),
        "why_reasons": output.get("why_reasons"),
        "confidence_band": confidence_band,
        "model_name": MODEL_NAME,
        "validation_passed": len(issues) == 0,
        "validation_issues": issues,
        "review_status": "pending_review" if not issues else "flagged",
        "_raw_model_output": output,
    }


def draft_for_write(draft: dict) -> dict:
    """Strips the local-only _raw_model_output field -- same as MLB's
    version. NOT wired to any real write endpoint -- explicitly out of
    scope for this task; nfl_content_drafts' real column shape (and
    whether "why_reasons" is even the right top-level key name) is
    unconfirmed -- see module docstring."""
    return {k: v for k, v in draft.items() if not k.startswith("_")}
