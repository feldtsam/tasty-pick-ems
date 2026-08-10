"""
Ties everything built for the regular shelf card writer into one real
call: prompt construction (content_writer/shelf_card_prompt.py) -> a real
Claude API call (forced tool-use against SHELF_CARD_TOOL_SCHEMA, via the
shared card_writer_common.call_claude_with_tool()) -> the full
deterministic validation suite (schema shape, citations, numeric
grounding, star consistency, banned language) -> shaping the result into
a content_drafts-ready row.

Mirrors generate_tasty_six_content.py exactly, minus everything tied to
the editorial_sentence field -- the one real structural difference
between the two writer types (see shelf_card_writer_schema.py). Every
validator, the actual API call, and the source-facts extraction are the
SAME real, tested code (card_writer_common.py) the Tasty Six writer uses
-- not a parallel reimplementation.

NEVER auto-approves or publishes anything -- same reasoning as
generate_tasty_six_content.py. A draft that fails validation is still
returned and still forwarded to content_drafts, flagged rather than
silently discarded.

Does not itself forward to Lovable -- see index.py's route for that.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "content_writer"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "content_writer" / "voice"))

from banned_language import find_banned_phrases  # noqa: E402
from card_writer_common import (  # noqa: E402
    MODEL_NAME,
    call_claude_with_tool,
    flatten_source_facts,
    validate_citations,
    validate_numeric_grounding,
    validate_star_consistency,
)
from principles import confidence_band_for_score  # noqa: E402
from shelf_card_prompt import build_system_prompt, build_user_prompt  # noqa: E402
from shelf_card_writer_schema import SHELF_CARD_TOOL_SCHEMA, validate_schema_shape  # noqa: E402

WRITER_TYPE = "shelf_card"


def call_claude_for_shelf_card(api_key: str, system_prompt: str, user_prompt: str) -> dict:
    """Thin, named wrapper around the shared call_claude_with_tool() --
    same shape as generate_tasty_six_content.py's equivalent entry point."""
    return call_claude_with_tool(api_key, system_prompt, user_prompt, SHELF_CARD_TOOL_SCHEMA)


def run_all_validators(output: dict, source_facts: dict, candidate: dict) -> list:
    """
    Same combination as generate_tasty_six_content.py's version, minus
    editorial_sentence: schema shape first (nothing else is safe to check
    against a malformed shape), then citations, numeric grounding, and
    star consistency, then banned language checked against title and
    every reason_text.
    """
    issues = []

    shape_errors = validate_schema_shape(output)
    issues.extend({"check": "schema_shape", "issue": e} for e in shape_errors)
    if shape_errors:
        return issues

    why_reasons = output["why_reasons"]

    issues.extend({"check": "citation", **v} for v in validate_citations(why_reasons, source_facts))
    issues.extend({"check": "numeric_grounding", **v} for v in validate_numeric_grounding(why_reasons, source_facts))
    issues.extend({"check": "star_consistency", **v} for v in validate_star_consistency(why_reasons, candidate))

    banned_targets = [("title", output["title"])]
    banned_targets += [(f"why_reasons[{i}].reason_text", r["reason_text"]) for i, r in enumerate(why_reasons)]
    for field, text in banned_targets:
        found = find_banned_phrases(text)
        if found:
            issues.append({"check": "banned_language", "field": field, "phrases": found})

    return issues


def generate_shelf_card_draft(
    candidate: dict, anthropic_api_key: str, debug_inject_violation_instruction: str = None,
    avoid_headlines: list[str] | None = None,
) -> dict:
    """
    The full pipeline for one real candidate -- same shape as
    generate_tasty_six_draft(), minus editorial_sentence.

    `candidate` is one entry from /api/curate-shelves's
    shelf_candidates_detailed (or the same shape built by hand for
    testing) -- must have "candidate" (the real scored-pick dict), "shelf".

    `avoid_headlines`: real titles already generated elsewhere in the same
    batch -- see shelf_card_prompt.build_system_prompt's docstring for the
    real production repetition problem this closes. Only
    content_draft_generation_live.py's batch orchestrator populates this.

    `debug_inject_violation_instruction` is TESTING ONLY -- never set by
    real content generation.

    Raises ValueError if final_score falls outside confidence_band_for_
    score()'s normal 25-90 range.

    Returns:
      {
        "mlbam_id":..., "game_pk":..., "shelf":..., "writer_type": "shelf_card",
        "title":..., "why_reasons":..., "confidence_band":..., "model_name":...,
        "validation_passed": bool, "validation_issues": [...],
        "review_status": "pending_review"|"flagged",
        "_raw_model_output": {...},
      }
    """
    c = candidate["candidate"]
    shelf = candidate["shelf"]
    final_score = c["final_score"]
    confidence_band = confidence_band_for_score(final_score)
    if confidence_band is None:
        raise ValueError(
            f"final_score={final_score} is outside the normal 25-90 confidence-band range -- "
            f"needs deliberate handling per principles.py, not a default band."
        )

    source_facts = flatten_source_facts(candidate)
    system_prompt = build_system_prompt(shelf, confidence_band, avoid_headlines=avoid_headlines)
    user_prompt = build_user_prompt(source_facts)

    if debug_inject_violation_instruction:
        system_prompt += f"\n\nFOR THIS GENERATION ONLY, additionally: {debug_inject_violation_instruction}"

    output = call_claude_for_shelf_card(anthropic_api_key, system_prompt, user_prompt)
    issues = run_all_validators(output, source_facts, candidate)

    return {
        "mlbam_id": c["mlbam_id"],
        "game_pk": c["game_pk"],
        "shelf": shelf,
        "writer_type": WRITER_TYPE,
        "title": output.get("title"),
        "why_reasons": output.get("why_reasons"),
        "confidence_band": confidence_band,
        "model_name": MODEL_NAME,
        "validation_passed": len(issues) == 0,
        "validation_issues": issues,
        "review_status": "pending_review" if not issues else "flagged",
        "_raw_model_output": output,
    }


def draft_for_write(draft: dict) -> dict:
    """Strips the local-only _raw_model_output field before forwarding to
    Lovable -- content_drafts has no column for it."""
    return {k: v for k, v in draft.items() if not k.startswith("_")}
