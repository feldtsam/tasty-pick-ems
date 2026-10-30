"""
Searched Card writer — the real entry point (Stage 2).

STAGE 2 DELIVERABLE — a real implementation, not a shape sketch. Still
gated: SEARCHED_CARD_WRITER_ENABLED (searched_card_flags.py) is checked
FIRST, before any row/pool is touched, before any import-time side effect
other than the ones Python itself already did at import, and before any
network call — calling this function while the flag is False raises
RuntimeError immediately, exactly as Stage 1 already guaranteed. SEARCHED_
CARD_WRITER_ENABLED stays False as of this commit. The one new caller,
nfl/api/index.py's POST /api/generate-searched-card route, independently
checks the same flag itself and returns 503 before ever importing this
far enough to call this function with a live key — see that route's own
docstring and searched_card_flags.py's "STAGE 2 UPDATE" note.

THE REAL SEQUENCE, now actually executed (not just documented as a future
shape): classify (evidence_price_classifier.classify_evidence_vs_price) ->
build pillar context (pillar_context.build_pillar_context, gated through
writer_facing_pillar_context per the row's label) -> build the real
system/user prompts (searched_card_prompt.py) -> call the model -> run
every validator -> assemble the card (searched_card_schema.build_
searched_card) or report why it was rejected.

MODEL_NAME BELOW IS THIS WRITER'S OWN, NOT SHARED WITH THE CURATED WRITER.
card_writer_common.py's MODEL_NAME ("claude-sonnet-5") is that module's
own constant, hardcoded directly into card_writer_common.call_claude_
with_tool's request body with no parameter to override it — reusing that
function would mean reusing ITS model choice too, silently coupling this
writer's model to the curated writer's. _call_claude below is therefore
its own small, independent HTTP call (raw requests, no SDK — same style
as card_writer_common.call_claude_with_tool, duplicated rather than
imported), the same "duplicate rather than cross-import" call this
package already makes elsewhere for a non-trivial, deliberate reason —
see evidence_price_classifier.py's own docstring for the fuller statement
of that principle.

THE ANTHROPIC CALL IS WRAPPED IN TRY/EXCEPT INSIDE THIS FUNCTION, not left
to an outer caller. The curated writer's own equivalent
(generate_nfl_shelf_card_content.generate_nfl_shelf_card_draft) has NO
try/except around its Claude call at all — it relies entirely on an outer
caller (curate_home_shelves._run_writer_llm_for_plan) to catch whatever
it raises, because that pipeline is a batch job with its own blanket
catch already in place one level up. This writer has no equivalent outer
safety net (its real caller, the Vercel route above, is a synchronous,
user-facing request/response) — so generate_searched_card_draft below
is itself the boundary that turns a raised exception into a clean
{"status": "transport_failure", ...} result, never a raw exception
escaping to the route.

THREE RESULT STATUSES, every real outcome mapped to exactly one of them:
  "ready"              — validators passed; "card" holds the real
                          assembled card (searched_card_schema.
                          build_searched_card's own output).
  "validator_rejected" — the model responded, but one or more validators
                          rejected the output; "validation_issues" holds
                          every issue found, same {"check": ..., ...}
                          dict shape every validator in this pipeline
                          already returns. No card is assembled on this
                          path — a rejected output never reaches build_
                          searched_card at all.
  "transport_failure"  — the Anthropic call itself failed (network error,
                          non-2xx, truncated response, missing tool_use
                          block) before there was any model output to
                          validate; "error" holds the real exception
                          text, logged the same way card_writer_common.
                          call_claude_with_tool already logs a failed
                          call, not swallowed silently.
`classification` (the real evidence_price_classifier output) is included
on every one of the three results, including transport_failure — it's
computed before the network call and costs nothing extra to return, and
a caller/test debugging a transport failure still benefits from seeing
what the deterministic gate decided for this row.
"""
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "content_writer"))

from searched_card_flags import SEARCHED_CARD_WRITER_ENABLED  # noqa: E402
from evidence_price_classifier import classify_evidence_vs_price  # noqa: E402
from pillar_context import build_pillar_context, writer_facing_pillar_context  # noqa: E402
from searched_card_prompt import (  # noqa: E402
    build_searched_card_system_prompt,
    build_searched_card_user_prompt,
)
from searched_card_schema import (  # noqa: E402
    build_searched_card,
    searched_card_tool_schema_for_label,
    validate_searched_card_schema_shape,
)
from searched_card_pricing_lexicon import validate_searched_card_pricing_claims  # noqa: E402
from searched_card_citable_fields import (  # noqa: E402
    SEARCHED_CARD_TOP_LEVEL_CITABLE_FIELDS,
    build_searched_card_candidate,
    searched_card_tolerance_for_key,
)
from card_writer_common import validate_citations, validate_numeric_grounding  # noqa: E402 -- shared, read-only import

# This writer's OWN model constant -- never read by, never shared with,
# card_writer_common.MODEL_NAME. See this module's own docstring.
MODEL_NAME = "claude-sonnet-5-5"
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
MAX_TOKENS = 1024
REQUEST_TIMEOUT_SECONDS = 60

STATUS_READY = "ready"
STATUS_VALIDATOR_REJECTED = "validator_rejected"
STATUS_TRANSPORT_FAILURE = "transport_failure"


def _build_source_facts(row: dict) -> dict:
    """
    The DATA-LAYER half of "citable fields" enforcement: scopes the
    candidate dict down to exactly SEARCHED_CARD_TOP_LEVEL_CITABLE_FIELDS
    before it ever reaches the prompt or the model, so the model cannot
    see (let alone cite) tpe_score/market_value_score or anything else
    outside that allowlist -- the same "scope it out, don't just instruct
    around it" discipline this package already uses for every other
    product rule (see searched_card_citable_fields.py's own docstring).
    validate_citations/validate_numeric_grounding below are the matching
    POST-HOC half -- defense in depth on top of this scoping, not a
    replacement for it.
    """
    candidate = build_searched_card_candidate(row)
    return {
        key: value
        for key, value in candidate.items()
        if key in SEARCHED_CARD_TOP_LEVEL_CITABLE_FIELDS and value is not None
    }


def _call_claude(anthropic_api_key: str, system_prompt, user_prompt: str, tool_schema: dict) -> dict:
    """
    One real Claude API call for THIS writer -- forced tool-use, raw
    requests, this module's own MODEL_NAME. See module docstring for why
    this is an independent implementation, not a call into card_writer_
    common.call_claude_with_tool.

    Raises (ValueError, or whatever requests itself raises on a network-
    level failure) on anything that isn't a clean, complete, correctly-
    shaped tool_use response. Never returns a partial or best-effort
    result -- the caller (generate_searched_card_draft) treats ANY
    exception from this function as a transport_failure, so this function
    itself does not need its own fallback behavior; raising loudly here is
    the correct contract, not a gap to close.
    """
    tool_name = tool_schema["name"]
    call_start = time.monotonic()
    try:
        response = requests.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": anthropic_api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            json={
                "model": MODEL_NAME,
                "max_tokens": MAX_TOKENS,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
                "tools": [tool_schema],
                "tool_choice": {"type": "tool", "name": tool_name},
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except Exception as e:
        elapsed = round(time.monotonic() - call_start, 2)
        print(
            f"[searched_card._call_claude] tool={tool_name} elapsed_seconds={elapsed} "
            f"status=EXCEPTION error={e!r}",
            flush=True,
        )
        raise

    elapsed = round(time.monotonic() - call_start, 2)
    if response.status_code >= 400:
        print(
            f"[searched_card._call_claude] tool={tool_name} elapsed_seconds={elapsed} "
            f"status={response.status_code}",
            flush=True,
        )
        raise ValueError(f"Anthropic API error {response.status_code}: {response.text}")

    data = response.json()
    if data.get("stop_reason") == "max_tokens":
        raise ValueError(
            f"Claude response was truncated at max_tokens={MAX_TOKENS} (stop_reason=max_tokens) -- "
            "the tool_use input is real but incomplete, not a usable response."
        )

    for block in data.get("content", []):
        if block.get("type") == "tool_use" and block.get("name") == tool_name:
            print(
                f"[searched_card._call_claude] tool={tool_name} elapsed_seconds={elapsed} status=200",
                flush=True,
            )
            return block.get("input", {})

    raise ValueError(f"No tool_use block for {tool_name!r} in Claude response")


def generate_searched_card_draft(row: dict, pool: list, anthropic_api_key: str, **kwargs) -> dict:
    """
    The real pipeline sequence: classify -> build pillar context -> build
    prompts -> call the model -> validate -> assemble the card, or report
    why it was rejected. See module docstring for the three possible
    result statuses and what each one means.

    Calling this with the flag at its default (False) raises RuntimeError
    immediately -- no row processing, no classification, no prompt
    building, no API call happens before that raise. This check stays
    FIRST, unchanged from Stage 1.

    kwargs: shelf_context (str, optional -- passed straight through to
    build_searched_card), confidence_band (str, optional, same).
    """
    if not SEARCHED_CARD_WRITER_ENABLED:
        raise RuntimeError(
            "SEARCHED_CARD_WRITER_ENABLED is False -- the Searched Card writer is design-and-tests only "
            "(Stage 1/2). Set searched_card_flags.SEARCHED_CARD_WRITER_ENABLED = True only after a human has "
            "reviewed every file in nfl/searched_card/ and wired a real caller; this function performs none "
            "of that wiring itself."
        )

    classification = classify_evidence_vs_price(row, pool)
    label = classification["label"]

    full_pillar_context = build_pillar_context(row, pool)
    pillar_context_for_writer = writer_facing_pillar_context(full_pillar_context, label)

    source_facts = _build_source_facts(row)

    system_prompt = build_searched_card_system_prompt(classification, pillar_context_for_writer)
    user_prompt = build_searched_card_user_prompt(source_facts)
    tool_schema = searched_card_tool_schema_for_label(label)

    try:
        llm_output = _call_claude(anthropic_api_key, system_prompt, user_prompt, tool_schema)
    except Exception as e:
        return {
            "status": STATUS_TRANSPORT_FAILURE,
            "error": str(e),
            "classification": classification,
        }

    shape_errors = validate_searched_card_schema_shape(llm_output, label=label)
    if shape_errors:
        return {
            "status": STATUS_VALIDATOR_REJECTED,
            "validation_issues": [{"check": "schema_shape", "issue": e} for e in shape_errors],
            "classification": classification,
        }

    why_reasons = llm_output.get("why_reasons") or []
    issues = []
    issues.extend({"check": "citation", **v} for v in validate_citations(why_reasons, source_facts))
    issues.extend(
        {"check": "numeric_grounding", **v}
        for v in validate_numeric_grounding(why_reasons, source_facts, searched_card_tolerance_for_key)
    )
    issues.extend(validate_searched_card_pricing_claims({**llm_output, "_source_facts": source_facts}))

    if issues:
        return {
            "status": STATUS_VALIDATOR_REJECTED,
            "validation_issues": issues,
            "classification": classification,
        }

    card = build_searched_card(
        llm_output,
        classification,
        player_id=row.get("player_id"),
        shelf_context=kwargs.get("shelf_context"),
        tpe_score=row.get("tpe_score"),
        market_value_score=row.get("market_value_score"),
        confidence_band=kwargs.get("confidence_band"),
    )
    return {
        "status": STATUS_READY,
        "card": card,
        "pillar_context": full_pillar_context,
        "classification": classification,
    }
