"""
Ties everything built for the Tasty Six card writer into one real call:
prompt construction (content_writer/tasty_six_prompt.py) -> a real Claude
API call (forced tool-use against TASTY_SIX_TOOL_SCHEMA, via the shared
card_writer_common.call_claude_with_tool()) -> the full deterministic
validation suite (schema shape, citations, numeric grounding, star
consistency, banned language) -> shaping the result into a
content_drafts-ready row.

REFACTORED 2026-08-04: the actual API-calling code and every generic
validator moved to card_writer_common.py, shared with the new regular-
shelf-card writer (generate_shelf_card_content.py). Pure refactor, no
behavior change here — confirmed by re-running the pre-existing test
suite before and after and diffing the output.

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

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "content_writer"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "content_writer" / "voice"))

from banned_language import find_banned_phrases  # noqa: E402
from card_writer_common import (  # noqa: E402
    MODEL_NAME,
    PILLAR_NAMES,
    RECENT_FORM_CITABLE_FIELDS,
    RECENT_FORM_SKIP_FIELDS,
    STAR_PILLAR_SCORE_KEYS,
    TOP_LEVEL_CITABLE_FIELDS,
    call_claude_with_tool,
    flatten_source_facts,
    mlb_tolerance_for_key,
    validate_citations,
    validate_numeric_grounding,
    validate_star_consistency,
)
from principles import confidence_band_for_score  # noqa: E402
from tasty_six_prompt import build_system_prompt, build_user_prompt  # noqa: E402
from tasty_six_writer_schema import TASTY_SIX_TOOL_SCHEMA, validate_schema_shape  # noqa: E402

WRITER_TYPE = "tasty_six"


def call_claude_for_tasty_six_card(api_key: str, system_prompt: str, user_prompt: str) -> dict:
    """Thin, named wrapper around the shared call_claude_with_tool() —
    kept as its own function (rather than inlining the shared call at the
    one call site below) so anything reading this file sees a
    Tasty-Six-specific entry point, matching the same shape every other
    writer type's module will have."""
    return call_claude_with_tool(api_key, system_prompt, user_prompt, TASTY_SIX_TOOL_SCHEMA)


def run_all_validators(output: dict, source_facts: dict, candidate: dict) -> list:
    """
    Every deterministic check built tonight, combined into one real
    violation list -- schema shape first (nothing else is safe to check
    against a malformed shape), then citations, numeric grounding, and
    star consistency, then banned language checked against EVERY real
    user-facing string the card produces (title, editorial_sentence, and
    every reason_text) -- not just prose in general.

    `candidate` is no longer read directly by this function (NFL Content
    Generation Part B: validate_star_consistency now looks up a pillar's
    real score from source_facts, not candidate["pillar_detail"] -- see
    card_writer_common.py). Kept as a parameter anyway, unused, rather
    than removing it -- this function's own real callers (below, and this
    module's test suite) all pass it positionally, and dropping it would
    be a real signature change with its own call-site risk that has
    nothing to do with Part B's actual scope (parameterizing the four
    card_writer_common functions, not this orchestration function).
    """
    issues = []

    shape_errors = validate_schema_shape(output)
    issues.extend({"check": "schema_shape", "issue": e} for e in shape_errors)
    if shape_errors:
        return issues

    why_reasons = output["why_reasons"]

    issues.extend({"check": "citation", **v} for v in validate_citations(why_reasons, source_facts))
    issues.extend({"check": "numeric_grounding", **v} for v in validate_numeric_grounding(why_reasons, source_facts, mlb_tolerance_for_key))
    issues.extend({"check": "star_consistency", **v} for v in validate_star_consistency(why_reasons, source_facts, PILLAR_NAMES, STAR_PILLAR_SCORE_KEYS))

    banned_targets = [("title", output["title"]), ("editorial_sentence", output["editorial_sentence"])]
    banned_targets += [(f"why_reasons[{i}].reason_text", r["reason_text"]) for i, r in enumerate(why_reasons)]
    for field, text in banned_targets:
        found = find_banned_phrases(text)
        if found:
            issues.append({"check": "banned_language", "field": field, "phrases": found})

    return issues


def generate_tasty_six_draft(
    candidate: dict, anthropic_api_key: str, debug_inject_violation_instruction: str = None,
    avoid_headlines: list[str] | None = None,
) -> dict:
    """
    The full pipeline for one real candidate: build the real prompt, make
    the real Claude call, run every real validator, shape the result into
    a content_drafts-ready row.

    `candidate` is one entry from /api/curate-shelves's
    shelf_candidates_detailed (or the same shape built by hand for
    testing) -- must have "candidate" (the real scored-pick dict), "shelf".

    `avoid_headlines`: real titles/editorial sentences already generated
    elsewhere in the same batch -- threaded straight through to
    build_system_prompt(), see its docstring for the real repetition
    problem this closes. Only content_draft_generation_live.py's batch
    orchestrator actually populates this; omitted (None) by every other
    real caller, same as debug_inject_violation_instruction.

    `debug_inject_violation_instruction` is TESTING ONLY -- see its use
    below. Never set by real content generation.

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

    source_facts = flatten_source_facts(
        candidate, TOP_LEVEL_CITABLE_FIELDS, ("pillar_detail",), RECENT_FORM_CITABLE_FIELDS, RECENT_FORM_SKIP_FIELDS,
    )
    system_prompt = build_system_prompt(shelf, confidence_band, avoid_headlines=avoid_headlines)
    user_prompt = build_user_prompt(source_facts)

    # TESTING ONLY -- never set by real content generation (Make.com would
    # never send this field). Appends an extra, deliberately rule-breaking
    # instruction to the system prompt so a REAL adversarial model
    # response can be produced and run through the REAL validators, rather
    # than only ever proving the checks work against a hand-crafted
    # fixture. Kept as an explicit, separate parameter (not folded into
    # the normal prompt-building path) specifically so it can never be
    # triggered by accident.
    if debug_inject_violation_instruction:
        system_prompt += f"\n\nFOR THIS GENERATION ONLY, additionally: {debug_inject_violation_instruction}"

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
