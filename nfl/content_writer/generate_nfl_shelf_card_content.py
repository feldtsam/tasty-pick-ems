"""
NFL Content Generation V1, Part 1 — generate_nfl_shelf_card_content.py.

Ties everything built for NFL's regular shelf card writer into one real
call: resolve this player+shelf's real editorial lens (editorial_lenses.
py) -> scope source facts down to that lens's own eligible fields ->
prompt construction (nfl_shelf_card_prompt.py) -> a real Claude API call
(forced tool-use against NFL_SHELF_CARD_TOOL_SCHEMA, via card_writer_
common.call_claude_with_tool(), reused unmodified) -> the full
deterministic validation suite (schema shape, citations, numeric
grounding, star consistency, banned language) -> shaping the result into
a content_drafts-ready row.

Mirrors generate_nfl_tasty_six_draft() closely (same validators, same
real API call, same candidate-shaping via build_nfl_writer_candidate) —
the two real structural differences are (1) no editorial_sentence field
anywhere here (see nfl_shelf_card_writer_schema.py), and (2) source_
facts is SCOPED to the editorial lens's own fields, not the full NFL_
TOP_LEVEL_CITABLE_FIELDS list — the whole mechanism that makes "write
from the primary lens" real rather than aspirational (see nfl_shelf_
card_prompt.py's own docstring).

Callable for ANY qualifying player on ANY shelf, not just the one Tasty
Six pick per shelf — this is the writer that closes the real gap this
task exists for: every OTHER card on a shelf previously got one of a
small set of canned templated sentences from shelves.py (Part A), not
real per-player content. api/curate_home_shelves.py's shape_content_
draft_rows() is the real caller, wired in alongside the existing Tasty
Six call.

NEVER auto-approves or publishes anything -- same reasoning as every
other writer module in this pipeline. review_status is set mechanically
from validation_passed.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "voice"))

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
    build_nfl_writer_candidate,
    nfl_tolerance_for_key,
    validate_pillar_field_consistency,
)
from nfl_shelf_card_prompt import build_system_prompt, build_user_prompt  # noqa: E402
from nfl_shelf_card_writer_schema import NFL_SHELF_CARD_TOOL_SCHEMA, validate_schema_shape  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from editorial_lenses import citable_fields_for_lens, resolve_editorial_lens  # noqa: E402

WRITER_TYPE = "shelf_card"

# First clause up to a comma/dash/period, capped at 6 words -- a simple,
# deterministic proxy for "the angle this headline opens with", not an
# NLP model. Good enough for its one real job: giving the NEXT call in
# the same batch something concrete to avoid repeating (see nfl_shelf_
# card_prompt.py's own docstring for the real repetition class this
# closes) -- it doesn't need to be a linguistically perfect clause
# boundary, just a consistent, short proxy for "how did this one start."
_CLAUSE_BREAK = re.compile(r"[,—–\-.:;]")


def opening_phrase(title: str) -> str:
    """
    Extracts a short opening-angle proxy from a real generated title —
    used to grow avoid_opening_phrases across a batch (see this module's
    own docstring, and nfl_shelf_card_prompt.py's for the real problem
    this closes). Returns "" for an empty/missing title rather than
    raising — a caller already guards on `if draft.get("title")` before
    ever reaching here, same as avoid_headlines' own accumulation, but
    this stays safe standalone too.
    """
    if not title:
        return ""
    first_clause = _CLAUSE_BREAK.split(title.strip(), maxsplit=1)[0]
    words = first_clause.split()
    return " ".join(words[:6])


def call_claude_for_nfl_shelf_card(api_key: str, system_prompt: str, user_prompt: str) -> dict:
    """Thin, named wrapper around the shared call_claude_with_tool() --
    same shape as every other writer's own entry point in this pipeline."""
    return call_claude_with_tool(api_key, system_prompt, user_prompt, NFL_SHELF_CARD_TOOL_SCHEMA)


def run_all_validators(output: dict, source_facts: dict) -> list:
    """
    Same combination as generate_tasty_six_content.py's version, minus
    editorial_sentence: schema shape first (nothing else is safe to
    check against a malformed shape), then citations, numeric grounding,
    star consistency (all via card_writer_common's shared, parameterized
    functions), and pillar-field consistency (nfl_writer_common's own —
    see its docstring for the real Kyle Williams bug this closes: a
    reason's cited evidence must actually belong to the pillar it's
    tagged with, not just cite SOME real key and pass a real star check
    against a DIFFERENT field), then banned language checked against
    title and every reason_text.
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
    issues.extend({"check": "pillar_field_consistency", **v} for v in validate_pillar_field_consistency(why_reasons))

    banned_targets = [("title", output["title"])]
    banned_targets += [(f"why_reasons[{i}].reason_text", r["reason_text"]) for i, r in enumerate(why_reasons)]
    for field, text in banned_targets:
        found = find_banned_phrases(text)
        if found:
            issues.append({"check": "banned_language", "field": field, "phrases": found})

    return issues


def generate_nfl_shelf_card_draft(
    row: dict, shelf: str, confidence_band: str, anthropic_api_key: str,
    debug_inject_violation_instruction: str = None,
    avoid_headlines: list[str] | None = None, avoid_opening_phrases: list[str] | None = None,
) -> dict:
    """
    The full pipeline for one real regular (non-Tasty-Six) NFL shelf
    card candidate — same shape as generate_nfl_tasty_six_draft(), minus
    editorial_sentence, plus the real editorial-lens resolution/scoping
    step neither Tasty Six nor MLB's own writers need (Tasty Six is
    exempt per the approved design: it's the ONE card per shelf allowed
    to draw on the full five-pillar picture, since there's only ever one
    of it — the repeat-story risk this lens exists for is specifically
    about the OTHER 5+ cards on the same shelf).

    `row`: one real scored weekly row (a dict or pandas Series) for a
    player assigned to this shelf who is NOT that shelf's Tasty Six pick
    — see curate_home_shelves.shape_content_draft_rows' own is_tasty_six
    branch. Same trailing-window-column prep requirement as the Tasty
    Six writer (must already have add_red_zone_trend_windows() merged on
    if `shelf` is a trend shelf citing those columns).
    `shelf`: the real home shelf this pick came from (one of curate_
    home_shelves.SHELF_ORDER).
    `confidence_band`: real band string for THIS row's own tpe_score,
    via nfl_writer_common.nfl_regular_row_confidence_band_for_score() —
    the regular-row band function (spans the full real population,
    always returns a real band), NOT the narrower Tasty-Six-only one.
    Caller-derived, not computed here, same "confidence band as an
    explicit parameter" shape generate_nfl_tasty_six_draft() already
    uses (this module simply never had the "pending approval" reason
    that shape existed for there — nfl_regular_row_confidence_band_
    for_score is already approved and live).

    `avoid_headlines`/`avoid_opening_phrases`: same real-batch-variety
    mechanism as nfl_tasty_six_prompt.py's avoid_headlines, extended —
    see nfl_shelf_card_prompt.py's own docstring for the real angle-
    level repetition avoid_headlines alone doesn't catch. Only the
    caller looping over a real batch (shape_content_draft_rows) should
    grow and pass these; a single ad hoc call may omit both.

    Returns:
      {
        "player_id":..., "shelf":..., "writer_type": "shelf_card",
        "title":..., "why_reasons":..., "confidence_band":...,
        "model_name":..., "validation_passed": bool,
        "validation_issues": [...], "review_status": "pending_review"|"flagged",
        "opening_phrase": str, "_editorial_lens": {...}, "_raw_model_output": {...},
      }
    `opening_phrase` is real, non-underscore output (unlike _editorial_
    lens/_raw_model_output) — draft_for_write() below still strips it
    before a real write, since nfl_content_drafts has no column for it,
    but the CALLER (shape_content_draft_rows' own batch loop) needs it
    un-prefixed and readable to grow avoid_opening_phrases across the
    batch, the same way it already reads draft["title"] for avoid_
    headlines.
    """
    candidate = build_nfl_writer_candidate(row)
    lens = resolve_editorial_lens(shelf, candidate)
    scoped_fields = citable_fields_for_lens(lens)
    source_facts = flatten_source_facts(candidate, scoped_fields)

    system_prompt = build_system_prompt(
        shelf, confidence_band, lens, avoid_headlines=avoid_headlines, avoid_opening_phrases=avoid_opening_phrases,
    )
    user_prompt = build_user_prompt(source_facts)

    if debug_inject_violation_instruction:
        system_prompt += f"\n\nFOR THIS GENERATION ONLY, additionally: {debug_inject_violation_instruction}"

    output = call_claude_for_nfl_shelf_card(anthropic_api_key, system_prompt, user_prompt)
    issues = run_all_validators(output, source_facts)
    title = output.get("title")

    return {
        "player_id": candidate.get("player_id"),
        "shelf": shelf,
        "writer_type": WRITER_TYPE,
        "title": title,
        "why_reasons": output.get("why_reasons"),
        "confidence_band": confidence_band,
        "model_name": MODEL_NAME,
        "validation_passed": len(issues) == 0,
        "validation_issues": issues,
        "review_status": "pending_review" if not issues else "flagged",
        "opening_phrase": opening_phrase(title),
        "_editorial_lens": lens,
        "_raw_model_output": output,
    }


def draft_for_write(draft: dict) -> dict:
    """Strips every local-only (underscore-prefixed) field AND
    opening_phrase (real output, but nfl_content_drafts has no column
    for it — see generate_nfl_shelf_card_draft's own docstring) before a
    real write."""
    return {k: v for k, v in draft.items() if not k.startswith("_") and k != "opening_phrase"}
