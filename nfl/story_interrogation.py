"""
Story Interrogation V1 — Step 2 of the spec (the "Population Prompt"
piece): LLM-populated `challenge`/`confirmation`/`judgment`/
`signal_verdict` sections of the `interrogation` object Step 1 already
attaches (as None) to every real Story Object in all four NFL
Intelligence families.

`change` and `context` are deterministic passthrough from fields
already on the Story Object — this module computes them too (see
build_interrogation_input), but never asks the model to. Only
`challenge`/`confirmation`/`judgment`/`signal_verdict` go through
call_claude_with_tool (card_writer_common.py, Claude Sonnet 5, forced
tool-use) — the same convention already used for citation/numeric-
grounding validation elsewhere in this codebase.

`signal_verdict` (SURVIVES | UNRESOLVED | FAILS, Task 4 in the system
prompt below) answers a question challenge.alternate_explanations
deliberately does not: does the ORIGINAL signal itself survive
scrutiny, independent of what happened to any one alternate
explanation. This is the same array-as-proxy fallacy already found and
fixed once in this codebase (newsletter/evidence_validator.py's
check_interrogation_traceability() used to treat a WEAKENED or
UNRESOLVED alternate-explanation status as grounding for a "survived
scrutiny" narrative claim — wrong, since neither one is evidence the
original signal holds up). signal_verdict exists so downstream
consumers (EPS, Tension) have a real, independently-judged answer to
read instead of inferring one from the array themselves.

OPEN QUESTION, NOT RESOLVED — flagged here deliberately rather than
silently dropped: real, live-model testing (4 separately-constructed
cases) proved signal_verdict=SURVIVES and signal_verdict=UNRESOLVED
each co-occurring with a genuinely all-WEAKENED challenge.alternate_
explanations array, and proved FAILS is independently reachable on its
own — but never produced the specific combination of an all-WEAKENED
array together with FAILS, across 4 real attempts with substantively
different inputs. Every attempt that supplied evidence strong enough to
plausibly justify FAILS also got incorporated by the model into
challenge.alternate_explanations as a genuine rival explanation
(SUPPORTED or UNRESOLVED), rather than staying confirmation-only
material with the tested alternate(s) still WEAKENED. The working
hypothesis — real-world evidence sharp enough to independently fail a
signal tends to double as a plausible rival explanation for it, making
it Task 1 material rather than pure Task 4 material — is PLAUSIBLE, not
proven. Do not treat "all-WEAKENED + FAILS never happens" as an
established fact anywhere downstream (a gate, a test fixture, a default
value); if it later turns out this combination genuinely cannot occur,
that is worth knowing deliberately, not assuming from this note. Any
gate/consumer of signal_verdict should read it as its own field, on its
own terms, and never assume a particular challenge.alternate_
explanations shape accompanies any particular signal_verdict value.

OPEN QUESTION, NOT RESOLVED — market_data=None and signal_verdict:
flagged here deliberately, same as the open question above, rather than
treated as a settled non-issue. Traced directly against this module's
own code (build_interrogation_input, the system prompt above, and this
function's own reshaping): when market_data=None, it is passed through
to the model as an explicit null in the input contract (never omitted,
never silently defaulted — confirmed by a real test in test_story_
interrogation.py), and the system prompt's own Task 2 instruction tells
the model "If market_data is not provided, say it isn't available; do
not guess." No code anywhere in this module branches on market_data is
None, so nothing here actively reinterprets absence as "the market did
not react."

The gap is what's missing, not what's present: unlike the confidence-
escalation and reader-framing checks (scan_for_confidence_escalation,
scan_evidence_significance_for_reader_framing), there is no
deterministic post-generation check enforcing that market_reaction
actually reads as "not available" when market_data was None — the
system prompt sentence above is the only safeguard, and it is untested
against real model output (only the input-contract null itself is
covered by an existing test). More specifically, Task 4's own
signal_verdict guardrail block goes out of its way to warn against
treating an empty or all-WEAKENED challenge.alternate_explanations
array as evidence for SURVIVES, but says nothing equivalent about
confirmation.market_reaction — despite Task 4 explicitly listing
market_reaction as one of the three possible sources of real supporting
evidence for SURVIVES. A missing market_reaction should carry the same
"absence is not evidence" treatment the array already gets; the prompt
does not yet say so.

This is a real, distinct correctness risk, not merely a completeness
note: missing market evidence may honestly reduce what Interrogation
knows, but it must never be interpreted as evidence that the market did
not react, and it must never by itself support a SURVIVES verdict. Not
fixed in this pass (Pass 2.1 was scoped to shelf-order invariance, not
market_data wiring or prompt changes) — reported here so it lives with
the code it affects rather than only in conversation history, and is
prioritized deliberately rather than discovered by accident later.

DETERMINISTIC change/context MAPPING — a real design decision made
here, not dictated verbatim by the spec (§11 explicitly leaves this
"decide per-family during implementation"), flagged plainly:
  - change.what_changed <- story["headline"] (already "plain,
    human-readable claim... no interpretation," per intelligence_
    schema.py's own field semantics doc — exactly what §4 asks for).
  - change.magnitude <- story["hero_metric"]'s before_value/after_value/
    unit, when hero_metric is populated (Universal Card v2 field, real
    on Defensive Trends/Market Intelligence's own output as of this
    session; None on Role Changes/Coaching Trends until their own
    writers set it — see each family's own build_*_stories()). Null,
    not fabricated, when hero_metric is None — matches §4's own
    "historical_normal... leave null rather than fabricating."
  - change.window <- story["time_window"] directly (§3's own explicit
    instruction: reuse rather than duplicate).
  - context.prior_baseline <- hero_metric's before_value + its own
    period_before_label, when available; null otherwise.
  - context.sample_size <- story["sample_size"] directly (§3's own
    explicit instruction, same reuse-not-duplicate reasoning).
  - context.historical_normal <- always null in V1. No family currently
    computes a longer-than-hero_metric baseline (season-to-date vs.
    last-3 is already hero_metric's whole job) — a genuinely different,
    longer read than what's available today, not something to
    approximate from what hero_metric already gives.

supporting_evidence passed to the model is story["supporting_evidence"]
itself (the real list[str] every family already populates), not a
richer per-field dict keyed by internal column names the way the
spec's own worked example illustrates ("e.g. related detector
outputs") — that shape needs the underlying per-row detector data
threaded through from each family's own builder loop, which this
module doesn't have access to from a constructed Story Object alone.
The existing supporting_evidence list is real, already-grounded
evidence text every family already produces uniformly; using it is a
pragmatic, family-agnostic choice, not a literal read of the worked
example's shape. Flagged explicitly, not silently substituted.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "content_writer"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "newsletter"))

from card_writer_common import call_claude_with_tool  # noqa: E402
from evidence_validator import CONFIDENCE_ESCALATING_LANGUAGE  # noqa: E402

INTERROGATION_VERSION = "v1_structured_data"

# card_writer_common.MAX_TOKENS (1024, its own module default) truncates
# a real Story Interrogation response before `judgment` -- confirmed
# directly against a real API call (stop_reason="max_tokens", output
# cut off partway through `confirmation`), not assumed. This is a full
# three-section structured response (potentially several alternate_
# explanations, each five sentence-length fields, plus confirmation and
# judgment), genuinely larger than the single-section prompts that
# constant was tuned for.
MAX_TOKENS = 4096

SYSTEM_PROMPT = """You are populating the `challenge`, `confirmation`, and `judgment` sections of a
Story Interrogation record for a Tasty Pick Ems NFL Intelligence Story Object.

Your only job is to test how well a detected signal survives scrutiny using the
structured data you are given. You are not writing for a reader. Nothing you
produce here will be published directly — it feeds an internal evidence record
that a separate scoring step and a separate editorial step will read later.

## What you have access to

You have only the structured fields provided in the input: the detected change,
its prior context, whatever supporting_evidence/related_players/prior_history/
market_data fields are populated, and nothing else. You do not have access to
beat reporting, press conferences, coach or player quotes, injury report
narrative beyond what appears in supporting_evidence, or any qualitative
reporting of any kind. This is a hard boundary, not a style preference.

If answering a field well would require reporting context you don't have, the
correct output is to say so plainly (an empty alternate_explanations array, a
NOT_TESTABLE status, or a null/short "not available in current data" note) —
never a plausible-sounding sentence that implies you know something you don't.
A confident-sounding fabrication is a worse output than an honest gap.

## Task 1 — challenge.alternate_explanations

Ask: given what changed, what else could explain it besides a genuine,
durable shift?

For each real, checkable alternate explanation visible in the provided data
(not a hypothetical you're inventing to fill the field):
- state the explanation in plain language
- state what evidence in the input actually supports that explanation existing
  (not that it's correct — that it's a real candidate, not a strawman)
- state the specific test that would distinguish "explanation is true" from
  "explanation is false," using only what's in the input
- run that test against the input data and state the result in plain language
- assign exactly one status: SUPPORTED, WEAKENED, UNRESOLVED, or NOT_TESTABLE
  (defined below)

If there is no real alternate explanation visible in the input data, return an
empty array. Do not manufacture one. An empty array is a correct, complete
answer, not a failure to try hard enough.

Status definitions:
- SUPPORTED: the test result favors the alternate explanation over the
  detected signal being a genuine change.
- WEAKENED: the test result works against the alternate explanation — the
  detected signal held up despite the challenge.
- UNRESOLVED: the test was run but the result doesn't clearly favor either
  side.
- NOT_TESTABLE: a real alternate explanation exists but the input data doesn't
  support running the test (e.g. missing prior-week data, sample too small).
  Use this rather than guessing, and rather than silently dropping the
  explanation from the array.

## Task 2 — confirmation

- supporting_signals: other signals in the input data that point the same
  direction as the detected change. Cite what's actually in the data. If
  nothing else in the input supports it, say so plainly rather than leaving
  this vague.
- contradicting_signals: signals in the input data that cut against the
  detected change. State these plainly and completely — do not soften or
  omit a real contradicting signal because it weakens the story. This field
  exists specifically to prevent cherry-picking.
- market_reaction: describe what market_data (if provided) shows about
  whether the relevant price/line has moved. Describe the movement itself —
  do not interpret what the movement means for betting relevance. If
  market_data is not provided, say it isn't available; do not guess.

## Task 3 — judgment

- what_we_know: a tight, defensible summary of what's actually established
  after the challenge and confirmation steps above. This must follow
  directly from Tasks 1 and 2 — do not introduce new claims here.
- what_we_dont_know: the specific, real remaining uncertainty (e.g. a small
  post-challenge sample size). Not a generic hedge like "nothing is
  certain" — name the actual gap.
- evidence_significance: answer exactly one question — what does this
  completed interrogation change about how strongly the detected signal
  should be interpreted? This is about evidence strength only.

  Do NOT write about why a reader, bettor, or fantasy manager should care.
  That is a different, later step's job, and it is off-limits here even if
  it would make the field read better.

  Good example: "The usage increase persisted after the starter returned,
  strengthening the case that this represents a genuine role change."

  Bad example (do not produce output like this): "Fantasy managers and
  bettors should pay attention before the market catches up." — reject any
  draft of this field that reads like this before returning it.

## Task 4 — signal_verdict

Answer a question Task 1 does not: does the ORIGINAL observed signal itself
survive scrutiny? Assign exactly one value: SURVIVES, UNRESOLVED, or FAILS.

- SURVIVES: real, independent evidence in the input actively supports the
  original signal — something in confirmation.supporting_signals,
  confirmation.market_reaction, or the surrounding context genuinely
  corroborates it. Not merely the absence of a successful alternate
  explanation — actual, present, supporting evidence.
- FAILS: real, independent evidence in the input actively undermines or
  contradicts the original signal itself — e.g. a real contradicting
  signal, or a market reaction that argues against it. This is a
  different question from whether any one alternate explanation won;
  it is asking whether the original claim holds up on its own merits.
- UNRESOLVED: neither of the above is supported by real evidence in the
  input. This is the correct default whenever the available confirmation
  or context is thin, ambiguous, or simply silent — not a failure to
  decide, but the honest answer when the input doesn't say enough either
  way.

CRITICAL — READ BEFORE ANSWERING THIS FIELD: signal_verdict must be
determined INDEPENDENTLY of challenge.alternate_explanations' own
statuses. Those statuses answer a completely different question — what
happened to each candidate alternate explanation — not whether the
original signal survives. Specifically:
  - An alternate explanation being WEAKENED (the challenge against it
    failed) does NOT by itself mean the original signal survives. It
    only means that one candidate explanation didn't hold up. You still
    need real, independent supporting evidence to answer SURVIVES.
  - An EMPTY alternate_explanations array (no alternate explanation was
    even found to test) does NOT by itself mean the original signal
    survives either. It means nothing was there to challenge it — that
    is silence, not confirmation.
  - If every alternate explanation you tested came back WEAKENED, or the
    array is empty, and you cannot point to real, independent supporting
    evidence beyond that, the correct answer is UNRESOLVED, not SURVIVES.
    Do not let a clean sweep of rejected alternate explanations stand in
    for evidence the original signal actually holds up — that is exactly
    the mistake this field exists to prevent.

## Language rules (apply to every field above)

- Never use confidence-escalating language — "confirmed," "proven," "settled,"
  "clearly," "definitely" — about any claim in this record, regardless of
  status. A WEAKENED alternate-explanation status means the signal survived
  that specific challenge, not that it is proven true in any absolute sense.
- Every claim you write must trace to a specific field in the provided input.
  If you cannot point to what in the input supports a sentence, do not write
  that sentence.
- Write in plain, direct, unembellished language. No hedging filler
  ("it's worth noting that," "it's important to remember"), no rhetorical
  flourishes, no attempt to sound clever or engaging. This is an internal
  evidence record, not reader-facing copy."""

INTERROGATION_TOOL_SCHEMA = {
    "name": "record_story_interrogation",
    "description": "Records the challenge, confirmation, and judgment sections of a Story Interrogation for one NFL Intelligence Story Object.",
    "input_schema": {
        "type": "object",
        "properties": {
            "challenge": {
                "type": "object",
                "properties": {
                    "alternate_explanations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "explanation": {"type": "string"},
                                "evidence": {"type": "string"},
                                "test": {"type": "string"},
                                "result": {"type": "string"},
                                "status": {
                                    "type": "string",
                                    "enum": ["SUPPORTED", "WEAKENED", "UNRESOLVED", "NOT_TESTABLE"],
                                },
                            },
                            "required": ["explanation", "evidence", "test", "result", "status"],
                        },
                    }
                },
                "required": ["alternate_explanations"],
            },
            "confirmation": {
                "type": "object",
                "properties": {
                    "supporting_signals": {"type": "string"},
                    "contradicting_signals": {"type": "string"},
                    "market_reaction": {"type": "string"},
                },
                "required": ["supporting_signals", "contradicting_signals", "market_reaction"],
            },
            "judgment": {
                "type": "object",
                "properties": {
                    "what_we_know": {"type": "string"},
                    "what_we_dont_know": {"type": "string"},
                    "evidence_significance": {"type": "string"},
                },
                "required": ["what_we_know", "what_we_dont_know", "evidence_significance"],
            },
            "signal_verdict": {
                "type": "string",
                "enum": ["SURVIVES", "UNRESOLVED", "FAILS"],
                "description": "Does the ORIGINAL observed signal survive scrutiny — determined independently of challenge.alternate_explanations' own statuses. See Task 4 in the system prompt.",
            },
        },
        "required": ["challenge", "confirmation", "judgment", "signal_verdict"],
    },
}

# Reader-directed framing that belongs to Betting Relevance/Audience
# Relevance (EPS, downstream), never to evidence_significance — see
# §8's own good/bad example pair. A second, DELIBERATELY SEPARATE word
# list from CONFIDENCE_ESCALATING_LANGUAGE (imported above): these two
# lists catch two different failure modes (overclaiming certainty vs.
# writing for a reader instead of an evidence record), not one list
# reused for two purposes.
READER_FRAMING_LANGUAGE = (
    "bettors should", "fantasy managers", "keep an eye on", "before the market",
    "worth watching for",
)


def _find_phrases(text: str, phrases: tuple) -> list[str]:
    if not text:
        return []
    return [p for p in phrases if re.search(rf"\b{re.escape(p)}\b", text, re.IGNORECASE)]


def _response_string_fields(response: dict) -> list[tuple[str, str]]:
    """Every (field_path, text) pair in a tool-call response, for the confidence-escalation scan and for building a retry message."""
    fields = []
    for i, alt in enumerate(response.get("challenge", {}).get("alternate_explanations", [])):
        for key in ("explanation", "evidence", "test", "result"):
            if alt.get(key):
                fields.append((f"challenge.alternate_explanations[{i}].{key}", alt[key]))
    for key in ("supporting_signals", "contradicting_signals", "market_reaction"):
        value = response.get("confirmation", {}).get(key)
        if value:
            fields.append((f"confirmation.{key}", value))
    for key in ("what_we_know", "what_we_dont_know", "evidence_significance"):
        value = response.get("judgment", {}).get(key)
        if value:
            fields.append((f"judgment.{key}", value))
    return fields


def scan_for_confidence_escalation(response: dict) -> list[dict]:
    """[{field, phrase, text}, ...] for every confidence-escalating phrase found in any string field of the response. Empty list = clean."""
    violations = []
    for field, text in _response_string_fields(response):
        for phrase in _find_phrases(text, CONFIDENCE_ESCALATING_LANGUAGE):
            violations.append({"field": field, "phrase": phrase, "text": text})
    return violations


def scan_evidence_significance_for_reader_framing(response: dict) -> list[dict]:
    """Same shape as scan_for_confidence_escalation, scoped to judgment.evidence_significance only, per §8's guardrail."""
    text = response.get("judgment", {}).get("evidence_significance", "")
    return [
        {"field": "judgment.evidence_significance", "phrase": phrase, "text": text}
        for phrase in _find_phrases(text, READER_FRAMING_LANGUAGE)
    ]


def _entity_display_name(entity: dict | None) -> str:
    if not entity:
        return "unknown entity"
    entity_type = entity.get("type")
    if entity_type == "player":
        return entity.get("player_name") or f"player {entity.get('player_id', '?')}"
    if entity_type == "defense":
        return f"{entity.get('team', '?')} {entity.get('position_group', '?')} defense"
    if entity_type == "team":
        return entity.get("team", "unknown team")
    return "unknown entity"


def build_interrogation_input(story: dict, prior_history: dict = None, market_data: dict = None) -> dict:
    """
    Assembles the family-agnostic input contract (deterministic change/
    context + whatever real evidence is on the Story Object) — see this
    module's own docstring for exactly which fields map from where and
    why. Every key in the contract is always present; a genuinely
    unavailable value is null, never omitted (per the spec's own input-
    contract rule) and never fabricated.
    """
    hero = story.get("hero_metric")
    magnitude = None
    prior_baseline = None
    if hero:
        unit = hero.get("unit") or ""
        before, after = hero.get("before_value"), hero.get("after_value")
        if before is not None and after is not None:
            magnitude = f"{before}{unit} -> {after}{unit}"
        if before is not None:
            before_label = hero.get("period_before_label") or "prior"
            prior_baseline = f"{before_label}: {before}{unit}"

    return {
        "intelligence_family": story.get("intelligence_family"),
        "entity": _entity_display_name(story.get("entity")),
        "change": {
            "what_changed": story.get("headline"),
            "magnitude": magnitude,
            "window": story.get("time_window"),
        },
        "context": {
            "prior_baseline": prior_baseline,
            "sample_size": story.get("sample_size"),
            "historical_normal": None,
        },
        "supporting_evidence": story.get("supporting_evidence") or None,
        "related_players": story.get("related_players") or [],
        "prior_history": prior_history,
        "market_data": market_data,
    }


def _retry_prompt(original_input: dict, violations: list[dict]) -> str:
    quoted = "\n".join(f"- {v['field']}: {v['text']!r} (contains: {v['phrase']!r})" for v in violations)
    return (
        f"{json.dumps(original_input)}\n\n"
        "Your previous response violated the language rules in the system prompt. "
        "Rewrite ONLY the following fields to remove the violating language — "
        "keep the same real claims, grounded in the same input data, just "
        "without these words/phrases:\n"
        f"{quoted}\n\n"
        "Return the complete record again (every field, not just the ones "
        "being fixed) via the same tool."
    )


def interrogate_story(
    story: dict, api_key: str, prior_history: dict = None, market_data: dict = None,
) -> dict | None:
    """
    The real, single entry point: builds the input contract, calls
    Claude (forced tool-use), runs both deterministic post-generation
    checks, retries once per check with the violating text quoted back
    to the model (per the spec's own retry design), and returns a
    complete §3-shaped interrogation object — or None on outright
    failure (API error, or a check still failing after its one retry),
    per the spec's own §12 fallback: interrogation absence should never
    block a Story Object from existing, only make its scoring less
    informed. The caller is responsible for flagging a None result for
    review (this function only returns it; logging/flagging is the
    caller's own concern, same separation intelligence_write.py's
    never-silently-drop convention already uses for sanity failures).
    """
    input_contract = build_interrogation_input(story, prior_history, market_data)
    user_prompt = json.dumps(input_contract)

    try:
        response = call_claude_with_tool(api_key, SYSTEM_PROMPT, user_prompt, INTERROGATION_TOOL_SCHEMA, max_tokens=MAX_TOKENS)
    except ValueError as e:
        print(f"[story_interrogation] API call failed: {e!r}", flush=True)
        return None

    # Two independent checks, each with its own one-shot retry — a
    # response could conceivably clear one and fail the other, so they
    # aren't combined into a single retry pass.
    for scan_fn, label in (
        (scan_for_confidence_escalation, "confidence-escalation"),
        (scan_evidence_significance_for_reader_framing, "reader-framing"),
    ):
        violations = scan_fn(response)
        if not violations:
            continue
        print(f"[story_interrogation] {label} violation(s), retrying once: {violations}", flush=True)
        try:
            response = call_claude_with_tool(
                api_key, SYSTEM_PROMPT, _retry_prompt(input_contract, violations), INTERROGATION_TOOL_SCHEMA,
                max_tokens=MAX_TOKENS,
            )
        except ValueError as e:
            print(f"[story_interrogation] retry API call failed: {e!r}", flush=True)
            return None
        if scan_fn(response):
            print(f"[story_interrogation] {label} violation persisted after retry — returning None, not a violating record.", flush=True)
            return None

    # Re-shaped to exactly the declared tool schema's own keys, not a
    # raw pass-through of response["challenge"]/["confirmation"]/
    # ["judgment"] -- confirmed directly against real API output that
    # the model sometimes adds an extra, unrequested null-valued key
    # (e.g. a stray "explanation_note": null alongside alternate_
    # explanations) despite the schema never declaring it. JSON Schema
    # allows additional properties unless explicitly closed, and the
    # spec's own tool schema doesn't set additionalProperties: false --
    # left exactly as given rather than edited, per the spec's own
    # instruction not to change the declared shape. This strips any
    # such stray key at the boundary instead, so a persisted
    # interrogation record only ever carries the real §3 fields.
    return {
        "interrogation_version": INTERROGATION_VERSION,
        "change": input_contract["change"],
        "context": input_contract["context"],
        "challenge": {
            "alternate_explanations": [
                {k: alt.get(k) for k in ("explanation", "evidence", "test", "result", "status")}
                for alt in response["challenge"]["alternate_explanations"]
            ],
        },
        "confirmation": {
            k: response["confirmation"].get(k)
            for k in ("supporting_signals", "contradicting_signals", "market_reaction")
        },
        "judgment": {
            k: response["judgment"].get(k)
            for k in ("what_we_know", "what_we_dont_know", "evidence_significance")
        },
        "signal_verdict": response.get("signal_verdict"),
    }
