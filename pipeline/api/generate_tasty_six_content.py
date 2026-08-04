"""
Ties everything built tonight for the Tasty Six card writer into one real
call: prompt construction (content_writer/tasty_six_prompt.py) -> a real
Claude API call (forced tool-use against TASTY_SIX_TOOL_SCHEMA, never
free-form prose) -> the full deterministic validation suite (schema
shape, citations, numeric grounding, star consistency, banned language)
-> shaping the result into a content_drafts-ready row.

NEVER auto-approves or publishes anything -- this only ever produces a
draft. review_status is set mechanically from validation_passed
(pending_review if clean, flagged if not) at the moment the draft is
built -- the actual approve/quick-edit/reject workflow is the (not yet
built) Admin Review Screen's job, not this module's. A draft that fails
validation is still returned and still forwarded to content_drafts, not
silently discarded -- flagged, with the real violations attached, so a
human reviewer sees WHY it failed rather than it just not existing (same
"never silently drop data" discipline as excluded_below_odds_filter_count
and every write-endpoint reporting fix tonight).

Does not itself forward to Lovable -- see index.py's route for that, same
separation between pure orchestration and network calls used throughout
this pipeline.
"""
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "content_writer"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "content_writer" / "voice"))

from banned_language import find_banned_phrases  # noqa: E402
from principles import confidence_band_for_score  # noqa: E402
from tasty_six_prompt import build_system_prompt, build_user_prompt  # noqa: E402
from tasty_six_writer_schema import (  # noqa: E402
    TASTY_SIX_TOOL_SCHEMA,
    flatten_source_facts,
    validate_citations,
    validate_numeric_grounding,
    validate_schema_shape,
    validate_star_consistency,
)

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
MODEL_NAME = "claude-sonnet-5"
MAX_TOKENS = 1024
REQUEST_TIMEOUT_SECONDS = 60

WRITER_TYPE = "tasty_six"


def call_claude_for_tasty_six_card(api_key: str, system_prompt: str, user_prompt: str) -> dict:
    """
    One real Claude API call, forced tool-use against TASTY_SIX_TOOL_SCHEMA
    -- structured output enforced by the API itself, never parsed from
    free-form prose. Raises for network/HTTP errors (including a real
    401/invalid-key); raises ValueError if the model response somehow has
    no tool_use block -- shouldn't happen with tool_choice forced, but not
    assumed.
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
            "tools": [TASTY_SIX_TOOL_SCHEMA],
            "tool_choice": {"type": "tool", "name": TASTY_SIX_TOOL_SCHEMA["name"]},
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json()

    for block in data.get("content", []):
        if block.get("type") == "tool_use" and block.get("name") == TASTY_SIX_TOOL_SCHEMA["name"]:
            return block["input"]

    raise ValueError(f"Claude response had no {TASTY_SIX_TOOL_SCHEMA['name']} tool_use block: {data}")


def run_all_validators(output: dict, source_facts: dict, candidate: dict) -> list:
    """
    Every deterministic check built tonight, combined into one real
    violation list -- schema shape first (nothing else is safe to check
    against a malformed shape), then citations, numeric grounding, and
    star consistency, then banned language checked against EVERY real
    user-facing string the card produces (title, editorial_sentence, and
    every reason_text) -- not just prose in general.
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

    banned_targets = [("title", output["title"]), ("editorial_sentence", output["editorial_sentence"])]
    banned_targets += [(f"why_reasons[{i}].reason_text", r["reason_text"]) for i, r in enumerate(why_reasons)]
    for field, text in banned_targets:
        found = find_banned_phrases(text)
        if found:
            issues.append({"check": "banned_language", "field": field, "phrases": found})

    return issues


def generate_tasty_six_draft(candidate: dict, anthropic_api_key: str) -> dict:
    """
    The full pipeline for one real candidate: build the real prompt, make
    the real Claude call, run every real validator, shape the result into
    a content_drafts-ready row.

    `candidate` is one entry from /api/curate-shelves's
    shelf_candidates_detailed (or the same shape built by hand for
    testing) -- must have "candidate" (the real scored-pick dict), "shelf".

    Raises ValueError if final_score falls outside confidence_band_for_
    score()'s normal 25-90 range -- that's deliberate handling territory
    per principles.py, not something to default silently into a band.

    Returns:
      {
        "mlbam_id":..., "game_pk":..., "shelf":..., "writer_type": "tasty_six",
        "title":..., "editorial_sentence":..., "why_reasons":...,
        "confidence_band":..., "model_name":...,
        "validation_passed": bool, "validation_issues": [...],
        "review_status": "pending_review"|"flagged",
        "_raw_model_output": {...},  # not part of the content_drafts write
                                      # payload -- local inspection only
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
    system_prompt = build_system_prompt(shelf, confidence_band)
    user_prompt = build_user_prompt(source_facts)

    output = call_claude_for_tasty_six_card(anthropic_api_key, system_prompt, user_prompt)
    issues = run_all_validators(output, source_facts, candidate)

    return {
        "mlbam_id": c["mlbam_id"],
        "game_pk": c["game_pk"],
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
    """Strips the local-only _raw_model_output field before forwarding to
    Lovable -- content_drafts has no column for it."""
    return {k: v for k, v in draft.items() if not k.startswith("_")}
