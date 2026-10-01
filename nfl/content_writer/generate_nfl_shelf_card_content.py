"""
NFL Content Generation V1, Part 1 — generate_nfl_shelf_card_content.py.

Ties everything built for NFL's regular shelf card writer into one real
call: resolve this player+shelf's real editorial lens (editorial_lenses.
py) -> scope source facts down to that lens's own eligible fields ->
find the tension (nfl_tension.py -- new pipeline stage, Editorial Voice
Spec's "Find the Tension" addition) -> prompt construction (nfl_shelf_
card_prompt.py) -> a real Claude API call (forced tool-use against
NFL_SHELF_CARD_TOOL_SCHEMA, via card_writer_common.call_claude_with_
tool(), reused unmodified) -> the full deterministic validation suite
(schema shape, citations, numeric grounding, star consistency,
field-narration, banned language) -> shaping the result into a
content_drafts-ready row.

Mirrors generate_nfl_tasty_six_draft() closely (same validators, same
real API call, same candidate-shaping via build_nfl_writer_candidate) —
the real structural differences are (1) a `story` field this writer now
has and Tasty Six's `editorial_sentence` does not (a differently-scoped
field, not the same one renamed -- see nfl_shelf_card_writer_schema.py's
own docstring), fed by its own real Tension Object (Tasty Six does not
yet get one -- see this module's own history for why regular shelf cards
were the confirmed first target), and (2) source_facts is SCOPED to the
editorial lens's own fields, not the full NFL_TOP_LEVEL_CITABLE_FIELDS
list — the whole mechanism that makes "write from the primary lens" real
rather than aspirational (see nfl_shelf_card_prompt.py's own docstring).

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
    system_blocks,
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
from nfl_shelf_card_prompt import (  # noqa: E402
    STATIC_SYSTEM_PROMPT,
    build_dynamic_system_prompt,
    build_user_prompt,
)
from nfl_shelf_card_writer_schema import NFL_SHELF_CARD_TOOL_SCHEMA, validate_schema_shape  # noqa: E402
from nfl_tension import find_tension  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from editorial_lenses import citable_fields_for_lens, is_masked_fallback, resolve_editorial_lens  # noqa: E402

WRITER_TYPE = "shelf_card"

# Fields this removes from source_facts when masked -- see strip_masked_
# role_fields's own docstring. role_momentum pairs with its own real
# completeness column; the four trend-gate columns have none of their
# own (same bare neutral-50 check shelves._trend_candidate's own gate_col
# argument already uses) -- is_masked_fallback() with no completeness_col
# falls back to exactly that check for each of them.
_ROLE_TREND_GATE_FIELDS = (
    "touch_share_trend_pct", "snap_share_trend_pct", "touch_volume_trend_pct",
    "touch_share_trend_pct_role", "snap_share_trend_pct_role",
)


def strip_masked_role_fields(source_facts: dict, candidate: dict) -> dict:
    """
    Removes role_momentum (and its trend-gate inputs) from source_facts
    when they're masked, so the writer cannot cite a fallback 50.0/50%
    reading as if it were a real, settled role signal -- the exact real
    card this closes: Fant's real Week 4 row (role_momentum masked,
    completeness 0.0) produced a why_reason citing touch_share_trend_pct/
    snap_share_trend_pct/touch_volume_trend_pct sitting at their shared
    50.0 fallback as if they were real evidence of a "flat" role.

    Only ever REMOVES keys already present (never adds, never changes a
    value) -- a key the lens never scoped in for this card in the first
    place is simply absent either way, same as before this function
    existed. Does NOT special-case trail3_games_played -- this branch
    deliberately does not add it to role_momentum's own citable group
    (see the games-played hotfix in this same branch, which removes it
    from every pillar's citable fields entirely), so there is nothing
    role_momentum-specific to preserve here.
    """
    stripped = dict(source_facts)
    if "role_momentum" in stripped and is_masked_fallback(candidate, "role_momentum", "role_momentum_completeness"):
        del stripped["role_momentum"]
    for key in _ROLE_TREND_GATE_FIELDS:
        if key in stripped and is_masked_fallback(candidate, key):
            del stripped[key]
    return stripped


class CandidateGatedOut(Exception):
    """
    Raised by generate_nfl_shelf_card_draft() when find_tension()
    returns None -- this candidate's real signal_verdict is FAILS or
    UNRESOLVED (see nfl_tension.py's own STOP GATE). Deliberately a
    DIFFERENT exception type from anything else this function can raise
    (a real API failure, a validation crash) -- those mean "the bespoke
    write attempt failed, fall back to this placement's deterministic
    content." This means "Interrogation actively excluded this
    candidate -- no card, no deterministic fallback either, this
    placement's row is skipped entirely." The caller (curate_home_
    shelves.shape_content_draft_rows) must catch this separately from a
    generic Exception and skip the row, never conflating "gated out" (a
    resource/evidence decision already made) with "this attempt failed"
    (a transient generation problem to degrade gracefully from).
    """

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


# NFL-only override of the shared MAX_TOKENS default (card_writer_common.
# py, 1024 -- unchanged, still what MLB and every other non-NFL-shelf-card
# writer gets). 2048, not the shared default, per the real 25%-truncation
# incident this constant's own history already documents (see NFL_SHELF_
# CARD_MAX_TOKENS's own callers). Kept as a named module constant (not
# just a literal default) so a future real-data re-tune has exactly one
# place to change, and so this value is independently inspectable in a
# test without re-deriving it from the function's own default argument.
#
# NOT raised further as part of the games-played hotfix investigation,
# despite that investigation originally suspecting max_tokens truncation
# as the cause of a real malformed-why_reasons failure mode: real usage
# data from 60 real calls (30 pre-hotfix, 30 post) showed NONE came
# within 150 tokens of this cap (max observed output_tokens: 1904 of
# 2048 pre-hotfix, 1795 post) and zero calls raised call_claude_with_
# tool's own max_tokens-truncation exception -- the malformed output is
# a real, separate model-formatting issue, not a token-budget one, so
# raising this cap is not expected to fix it (confirmed empirically, not
# just reasoned about -- see this task's own report for the real
# before/after rate at a higher cap).
NFL_SHELF_CARD_MAX_TOKENS = 2048


def call_claude_for_nfl_shelf_card(api_key: str, system_prompt, user_prompt: str, max_tokens: int = NFL_SHELF_CARD_MAX_TOKENS) -> dict:
    """Thin, named wrapper around the shared call_claude_with_tool() --
    same shape as every other writer's own entry point in this pipeline.

    max_tokens defaults to NFL_SHELF_CARD_MAX_TOKENS (2048), not the
    shared module default (1024): a real production run showed 8 of 32
    calls (25%) truncated at 1024 for this schema (title + a 1-2
    paragraph story + 2-4 grounded why_reasons) -- not an occasional
    near-miss, a regular occurrence. Now a real parameter (was a bare
    literal in the call below) specifically so this ONE writer can be
    tuned independently -- card_writer_common.MAX_TOKENS (the shared
    default every OTHER writer, including MLB's, still gets) is
    completely untouched by this change; nothing here alters the shared
    function's own default argument.
    """
    return call_claude_with_tool(
        api_key, system_prompt, user_prompt, NFL_SHELF_CARD_TOOL_SCHEMA, max_tokens=max_tokens,
    )


def _is_well_formed_card(candidate_output: dict) -> bool:
    """A block is usable iff it clears the same validate_schema_shape()
    check a published card has to clear anyway -- not a separate,
    looser notion of "looks okay". A degenerate placeholder block
    (empty why_reasons, "placeholder" title/story) fails this the same
    as the raw-XML-fragment malformation does; neither should ever be
    treated as recovered."""
    return len(validate_schema_shape(candidate_output)) == 0


def call_claude_for_nfl_shelf_card_with_retry(
    api_key: str, system_prompt, user_prompt: str, max_tokens: int = NFL_SHELF_CARD_MAX_TOKENS,
) -> tuple[dict, dict]:
    """
    NFL-only recovery wrapper around call_claude_with_tool for the real
    malformed-why_reasons bug (2026-09-30 investigation): why_reasons
    sometimes comes back as a raw string fragment resembling old-style
    XML tool-call syntax (e.g. '\\n<parameter name="pillar">role_momentum')
    instead of the structured array the schema requires -- confirmed via
    real API responses to be a model tool-formatting glitch, NOT
    max_tokens truncation (no call ever hit stop_reason="max_tokens" at
    either 2048 or 3072; see this task's own report).

    Also confirmed from those same raw responses: with tool_choice
    forced, the API sometimes still emits MORE THAN ONE tool_use block
    for the same tool in a single response, and a LATER block is
    sometimes the complete, well-formed card the first one failed to
    be. Checking every block in the response this call already paid for
    costs nothing extra, so that's tried FIRST; only if none of them are
    well-formed does this fall back to one additional real call (capped
    at exactly one retry -- never a retry loop). If even the retry comes
    back with no well-formed block, this returns the original response's
    first block unchanged, same as calling call_claude_for_nfl_shelf_card
    directly -- run_all_validators' existing schema_shape check and the
    flagged-review fallback are what catch that case downstream; this
    function never fabricates a result to make a problem disappear.

    card_writer_common.call_claude_with_tool's own DEFAULT behavior --
    every other caller, including every MLB writer -- is completely
    unaffected; this only uses its new return_all_tool_use_blocks=True
    opt-in, which that function's own default (False) never engages.

    Returns (card_output, retry_stats). retry_stats = {
      "blocks_in_first_response": int,
      "recovered_from_same_response": bool,  # found a later block in the SAME response that validates -- zero extra cost
      "retry_fired": bool,                   # none of the first response's blocks validated -- one extra real call was made
      "retry_recovered": bool,               # the retry's own response had a validating block
    } -- kept on the draft under the underscore-prefixed `_retry_stats` key (stripped before any real write, same convention as `_tension`/`_editorial_lens`) so real production frequency and cost are visible without re-deriving them from logs.
    """
    blocks = call_claude_with_tool(
        api_key, system_prompt, user_prompt, NFL_SHELF_CARD_TOOL_SCHEMA,
        max_tokens=max_tokens, return_all_tool_use_blocks=True,
    )
    stats = {
        "blocks_in_first_response": len(blocks),
        "recovered_from_same_response": False,
        "retry_fired": False,
        "retry_recovered": False,
    }
    for i, block in enumerate(blocks):
        if _is_well_formed_card(block):
            if i > 0:
                stats["recovered_from_same_response"] = True
                print(
                    f"[nfl_shelf_card_retry] recovered a well-formed block at index={i} of "
                    f"{len(blocks)} in the SAME response -- no extra API call",
                    flush=True,
                )
            return block, stats

    stats["retry_fired"] = True
    print(
        f"[nfl_shelf_card_retry] none of {len(blocks)} block(s) in the first response were "
        f"well-formed -- making the one capped retry call",
        flush=True,
    )
    retry_blocks = call_claude_with_tool(
        api_key, system_prompt, user_prompt, NFL_SHELF_CARD_TOOL_SCHEMA,
        max_tokens=max_tokens, return_all_tool_use_blocks=True,
    )
    for block in retry_blocks:
        if _is_well_formed_card(block):
            stats["retry_recovered"] = True
            print("[nfl_shelf_card_retry] retry recovered a well-formed card", flush=True)
            return block, stats

    print(
        "[nfl_shelf_card_retry] retry did NOT recover a well-formed card -- "
        "returning the original first block unchanged (flagged review handles it downstream)",
        flush=True,
    )
    return blocks[0], stats


# Skip source-fact values this small -- a jersey number, a single-digit
# star rating, an ordinary small count. A raw field value THIS size can
# appear in ordinary English by pure coincidence ("he's their No. 2
# option") without being field-narration at all; the real failure mode
# this check exists for is a distinctive, clearly-evidence-shaped number
# (a percentile score, a completeness percentage, an odds price) landing
# verbatim in prose.
_FIELD_NARRATION_MIN_ABS_VALUE = 10


def validate_no_field_narration(story: str, source_facts: dict) -> list[dict]:
    """
    Automated slice of the Editorial Voice Spec's hard rule: "the writing
    agent is prohibited from generating a sentence that could be produced
    by reading a Story Object field aloud in prose." This is NOT the full
    check the spec's own "Editorial QA pass (future)" section describes
    (that needs a real LLM-based read of the whole sentence, out of this
    task's scope) -- it's the cheapest, least-ambiguous slice that's
    directly automatable today: does `story` contain a raw NUMERIC
    source-fact value, verbatim? A `story` that quotes "72.9" or "57.1"
    or "30" straight out of the facts it was given is field-narration by
    definition, regardless of the surrounding sentence -- numbers are
    evidence (why_reasons' job), never the story.

    Checks both the value's natural string form and its rounded-integer
    form (a model narrating "73" instead of "72.9" is still narrating the
    same real field, not writing around it). Skips booleans (a stray
    "True"/"False" is never a field-narration risk) and small integers
    (see _FIELD_NARRATION_MIN_ABS_VALUE's own comment).
    """
    issues = []
    for key, value in source_facts.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if abs(value) < _FIELD_NARRATION_MIN_ABS_VALUE:
            continue
        candidates = {str(value), str(round(value))}
        for candidate in candidates:
            if candidate in story:
                issues.append({"field": key, "value": value, "matched_text": candidate})
                break
    return issues


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
    title, story, and every reason_text.

    PLUS (Editorial Voice Spec, "Find the Tension" addition):
    field-narration checked against `story` specifically (see
    validate_no_field_narration's own docstring) — why_reasons is
    EXEMPT from this check on purpose, since citing real numbers is
    exactly its job; `story` is the one field this rule applies to.
    """
    issues = []

    shape_errors = validate_schema_shape(output)
    issues.extend({"check": "schema_shape", "issue": e} for e in shape_errors)
    if shape_errors:
        return issues

    why_reasons = output["why_reasons"]
    story = output["story"]

    issues.extend({"check": "citation", **v} for v in validate_citations(why_reasons, source_facts))
    issues.extend({"check": "numeric_grounding", **v} for v in validate_numeric_grounding(why_reasons, source_facts, nfl_tolerance_for_key))
    issues.extend({"check": "star_consistency", **v} for v in validate_star_consistency(why_reasons, source_facts, NFL_PILLAR_NAMES, NFL_STAR_PILLAR_SCORE_KEYS))
    issues.extend({"check": "pillar_field_consistency", **v} for v in validate_pillar_field_consistency(why_reasons))
    issues.extend({"check": "field_narration", **v} for v in validate_no_field_narration(story, source_facts))

    banned_targets = [("title", output["title"]), ("story", story)]
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
    interrogation_result: dict | None = None, max_tokens: int = NFL_SHELF_CARD_MAX_TOKENS,
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
        "title":..., "story":..., "why_reasons":..., "confidence_band":...,
        "model_name":..., "validation_passed": bool,
        "validation_issues": [...], "review_status": "pending_review"|"flagged",
        "opening_phrase": str, "_editorial_lens": {...}, "_tension": {...},
        "_interrogation_result": {...} | None, "_raw_model_output": {...},
        "_retry_stats": {...},
      }
    `story` (Editorial Voice Spec, "Find the Tension" addition) is the
    real Story-tier text — see nfl_shelf_card_writer_schema.py's own
    docstring for why this is a new field, not editorial_sentence
    renamed. `_tension` is the real Tension Object (nfl_tension.find_
    tension's own output) that produced it — underscore-prefixed and
    stripped before a real write by draft_for_write() below, same as
    _editorial_lens, but kept on the return value for inspection/
    debugging/QA (the spec's own "storable, inspectable" requirement).

    `opening_phrase` is real, non-underscore output (unlike _editorial_
    lens/_tension/_raw_model_output) — draft_for_write() below still
    strips it before a real write, since nfl_content_drafts has no
    column for it, but the CALLER (shape_content_draft_rows' own batch
    loop) needs it un-prefixed and readable to grow avoid_opening_
    phrases across the batch, the same way it already reads
    draft["title"] for avoid_headlines.

    `interrogation_result`: the CANDIDATE-level Interrogation result
    (story_interrogation.interrogate_story()'s own output — signal_
    verdict/challenge/confirmation/judgment — wrapped in Pass 3/4's own
    three-state {"interrogation_status": ..., "result": ...} shape)
    already computed ONCE for this player+event by curate_home_shelves.
    _interrogate_unique_candidates(), before shape_content_draft_rows'
    own per-placement loop ever calls this function. Passed straight
    through to find_tension() as its own third argument, which may gate
    this candidate out entirely (signal_verdict FAILS/UNRESOLVED) --
    see CandidateGatedOut above for what that means for THIS function's
    own return contract. kept on the return value under
    _interrogation_result purely for inspection/debugging/QA, same
    "storable, inspectable" treatment _editorial_lens/_tension already
    get, whenever a real (non-gated) return happens.

    Raises CandidateGatedOut instead of returning, when find_tension()
    itself returns None for this candidate -- no draft dict, no partial
    content, the caller must skip this placement's row entirely (see
    that exception's own docstring for why this is a distinct case from
    every other exception this function can raise). The new descriptive
    Tension Object fields this gate produces (story_mode/information_
    value/reader_question_type/allowed_claim_strength) are computed and
    stored on `_tension` for every non-gated return, but NOT yet read by
    build_system_prompt/_tension_block -- that's a distinct, later step,
    same "plumbing first, consumption later" pattern this parameter
    itself already followed for one pass before this one.
    """
    candidate = build_nfl_writer_candidate(row)
    lens = resolve_editorial_lens(shelf, candidate)
    scoped_fields = citable_fields_for_lens(lens)
    source_facts = flatten_source_facts(candidate, scoped_fields)
    source_facts = strip_masked_role_fields(source_facts, candidate)
    tension = find_tension(candidate, lens, interrogation_result)
    if tension is None:
        raise CandidateGatedOut(
            f"player_id={candidate.get('player_id')!r} shelf={shelf!r}: "
            f"signal_verdict gated this candidate out (FAILS/UNRESOLVED) -- no card"
        )

    # PROMPT CACHING: the system prompt goes to the API as two blocks, not one
    # string -- STATIC_SYSTEM_PROMPT (frozen, carries the cache_control
    # breakpoint) followed by this per-candidate tail, which is where the lens,
    # the tension, and both variety lists live. See system_blocks() in
    # card_writer_common.py for why the order is load-bearing.
    dynamic_system_prompt = build_dynamic_system_prompt(
        shelf, confidence_band, lens, tension, avoid_headlines=avoid_headlines, avoid_opening_phrases=avoid_opening_phrases,
    )
    user_prompt = build_user_prompt(source_facts)

    if debug_inject_violation_instruction:
        dynamic_system_prompt += f"\n\nFOR THIS GENERATION ONLY, additionally: {debug_inject_violation_instruction}"

    output, retry_stats = call_claude_for_nfl_shelf_card_with_retry(
        anthropic_api_key, system_blocks(STATIC_SYSTEM_PROMPT, dynamic_system_prompt), user_prompt,
        max_tokens=max_tokens,
    )
    issues = run_all_validators(output, source_facts)
    title = output.get("title")

    return {
        "player_id": candidate.get("player_id"),
        "shelf": shelf,
        "writer_type": WRITER_TYPE,
        "title": title,
        "story": output.get("story"),
        "why_reasons": output.get("why_reasons"),
        "confidence_band": confidence_band,
        "model_name": MODEL_NAME,
        "validation_passed": len(issues) == 0,
        "validation_issues": issues,
        "review_status": "pending_review" if not issues else "flagged",
        "opening_phrase": opening_phrase(title),
        "_editorial_lens": lens,
        "_tension": tension,
        "_interrogation_result": interrogation_result,
        "_raw_model_output": output,
        "_retry_stats": retry_stats,
    }


def draft_for_write(draft: dict) -> dict:
    """Strips every local-only (underscore-prefixed) field AND
    opening_phrase (real output, but nfl_content_drafts has no column
    for it — see generate_nfl_shelf_card_draft's own docstring) before a
    real write."""
    return {k: v for k, v in draft.items() if not k.startswith("_") and k != "opening_phrase"}
