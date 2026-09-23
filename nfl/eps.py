"""
Editorial Priority Score V1 — a standalone evaluation of one already-
interrogated Story Object, run after Story Interrogation and before any
editorial surface composes an issue from it (see this module's own
architecture: Story Object -> Interrogation -> EPS -> editorial
surfaces).

INVESTIGATED BEFORE BUILDING (per standing practice): no "existing EPS
rubric implementation" or rubric doc exists anywhere in this codebase —
confirmed by a full grep for "Editorial Priority Score" / EPS_WEIGHTS /
"Weekly Editor Agent" / big_one_eligible / watchlist_eligible across
every .py and .md file before writing this; the only real hits were
this session's own prior work (story_interrogation.py, evidence_
validator.py). The spec's own weights/bands/worked gate examples in its
own text are the only real ground truth available to build against —
treated as authoritative, nothing here assumes a document that isn't
actually in this repo. Evidence Strength's deterministic base value
uses (confidence + completeness) / 2 — not invented for this module,
but the SAME real formula every family's own _evidence_classification_
for_row already uses, confirmed directly against role_changes.py's own
comment ("Same real formula as Defensive Trends, confirmed directly
from Lovable's own trustIndicator()") — the one genuinely "existing"
evidence-strength-shaped computation already live in this codebase.

Like interrogation, EPS is never computed inside a family's own
build_*_stories() loop (every family already sets story["eps"] = None
as a schema slot, same precedent as interrogation) — it requires
interrogation as an input, so it only ever runs as a separate,
explicitly-invoked pass over an already-built, already-interrogated
story, via compute_eps() below.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "content_writer"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "newsletter"))

from card_writer_common import call_claude_with_tool, system_blocks  # noqa: E402
from evidence_validator import CONFIDENCE_ESCALATING_LANGUAGE  # noqa: E402

EPS_VERSION = "v1"

# Unchanged from the spec's own stated rubric -- not recomputed or
# rederived here, just the real constants the composite formula needs.
DIMENSION_WEIGHTS = {
    "significance": 0.25,
    "evidence_strength": 0.20,
    "betting_relevance": 0.20,
    "novelty": 0.15,
    "story_tension": 0.15,
    "audience_relevance": 0.05,
}

BIG_ONE_EVIDENCE_FLOOR = 40
WATCHLIST_COMPOSITE_FLOOR = 55
WATCHLIST_EVIDENCE_FLOOR = 25
WATCHLIST_NOVELTY_OR_TENSION_FLOOR = 65


# ============================================================
# Evidence Strength -- deterministic, §7.
# ============================================================

# Per-alternate-explanation adjustment magnitudes -- an implementation-
# tuning decision the spec explicitly leaves open ("calibrate against
# the acceptance-test cases... rather than guessing constants up
# front"). Calibrated against this session's own real interrogation
# output (Jake Bobo/A.J. Brown, Market Intelligence, both NOT_TESTABLE-
# only; Michael Mayer, Role Changes, one SUPPORTED + one NOT_TESTABLE) —
# see test_eps.py's own real-data-shaped fixtures for the worked
# numbers. WEAKENED is the only status that should meaningfully move a
# score upward (a real, passed challenge); SUPPORTED is a real strike
# against the signal, weighted the heaviest of the three non-neutral
# statuses; UNRESOLVED is deliberately small, per the spec's own
# "small downward adjustment" wording, not equal to SUPPORTED's full
# penalty -- "tested, inconclusive" is a materially weaker finding than
# "tested, and a competing explanation won."
WEAKENED_ADJUSTMENT = 8
SUPPORTED_ADJUSTMENT = -15
UNRESOLVED_ADJUSTMENT = -3
NOT_TESTABLE_ADJUSTMENT = 0

_STATUS_ADJUSTMENTS = {
    "WEAKENED": WEAKENED_ADJUSTMENT,
    "SUPPORTED": SUPPORTED_ADJUSTMENT,
    "UNRESOLVED": UNRESOLVED_ADJUSTMENT,
    "NOT_TESTABLE": NOT_TESTABLE_ADJUSTMENT,
}

LIMITED_EVIDENCE_CAP = 35


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def compute_evidence_strength(story: dict, interrogation: dict | None) -> dict:
    """
    {score, rationale, computation: "deterministic"} — pure function,
    no LLM call, per §7. `interrogation: None` (population failed, per
    the interrogation spec's own §12 fallback) computes EXACTLY the
    pre-interrogation base value with no adjustment -- EPS degrades
    gracefully here, never blocks, matching §10's own "less informed,
    not absent" instruction.
    """
    completeness = story.get("completeness")
    confidence = story.get("confidence")
    if completeness is None or confidence is None:
        base = 50.0
        base_note = "completeness/confidence missing on the Story Object -- neutral 50 base, not fabricated"
    else:
        base = (float(completeness) + float(confidence)) / 2.0
        base_note = f"base (confidence {confidence} + completeness {completeness}) / 2 = {base:.1f}"

    if interrogation is None:
        score = _clamp(base)
        rationale = f"{base_note}. No interrogation available -- scored exactly as it would have been before interrogation existed."
    else:
        adjustment = 0.0
        adj_notes = []
        for alt in interrogation.get("challenge", {}).get("alternate_explanations", []):
            status = alt.get("status")
            delta = _STATUS_ADJUSTMENTS.get(status, 0)
            adjustment += delta
            if delta != 0:
                adj_notes.append(f"{status} ({delta:+d})")
        score = _clamp(base + adjustment)
        if adj_notes:
            rationale = f"{base_note}, adjusted {adjustment:+.0f} from challenge status(es): {', '.join(adj_notes)}."
        else:
            rationale = f"{base_note}. No challenge status moved the score (empty alternate_explanations, or all NOT_TESTABLE)."

    evidence_classification = story.get("evidence_classification")
    if evidence_classification == "limited":
        capped = min(score, LIMITED_EVIDENCE_CAP)
        if capped != score:
            rationale += f" evidence_classification=\"limited\" hard-caps the score at {LIMITED_EVIDENCE_CAP} (was {score:.1f})."
        score = capped

    return {"score": round(score, 1), "rationale": rationale, "computation": "deterministic"}


# ============================================================
# Audience Relevance -- deterministic heuristic, §6, honestly provisional.
# ============================================================

AUDIENCE_RELEVANCE_MIDPOINT = 50.0

# Position-group prominence, the one real, reliably-derivable signal
# available on a Story Object today (per §6: "position if it correlates
# with fantasy relevance"). Deliberately coarse and explicitly
# provisional -- a real fantasy-relevance/ADP-driven ranking would be a
# genuinely better signal, not built here, per §6's own instruction not
# to invent a prominence-scoring system for a 5%-weight dimension. QB
# scores highest (fantasy's highest-profile, highest-scoring position);
# RB/WR close behind (the two positions this pipeline's own Anytime-TD
# product is built around); TE lower (real, but a real step down in
# typical fantasy/broadcast attention); defense/team entities (Defensive
# Trends, Coaching Trends) have no comparable position-group prominence
# concept at all -- midpoint, not guessed.
_POSITION_GROUP_SCORE = {
    "QB": 70.0,
    "RB": 60.0,
    "WR": 60.0,
    "TE": 50.0,
}

# National-broadcast flag: the spec names this as a real candidate
# signal ("national-broadcast flag if that data exists in the schedule
# data already used elsewhere in the pipeline") -- investigated, not
# assumed: nfl_data_py.import_schedules() does carry real network-name
# columns (gameday network info), but no NFL Intelligence family
# currently threads game-level schedule data onto a Story Object at
# all (confirmed by reading every real build_*_stories() call site --
# none accept or attach a schedules frame). Wiring that through is a
# real, separate data-plumbing task, not a one-line addition here --
# left as a documented gap, not silently faked with a fixed multiplier.
NATIONAL_BROADCAST_SIGNAL_AVAILABLE = False


def compute_audience_relevance(story: dict) -> dict:
    """
    {score, rationale, computation: "deterministic"} per §6. Every
    input actually used, and every input that defaulted, is named
    explicitly in the rationale string -- inspectable per §12 test #5,
    not silent about its own provisional nature.
    """
    entity = story.get("entity") or {}
    position_group = entity.get("position_group")
    used_signals = []
    defaulted_signals = []

    # position_group only means player-fantasy-prominence on a real
    # PLAYER entity. On a defense entity it names which position GROUP
    # a matchup pertains to (e.g. "RB" = "this defense's vulnerability
    # to running backs") -- a completely different concept that must
    # never be looked up against this table. Real bug caught directly
    # by this module's own test (a defense-entity fixture with
    # position_group="RB" incorrectly matched the RB player score
    # before this type guard was added) -- fixed, not assumed correct.
    if entity.get("type") == "player" and position_group in _POSITION_GROUP_SCORE:
        score = _POSITION_GROUP_SCORE[position_group]
        used_signals.append(f"position_group={position_group}")
    else:
        score = AUDIENCE_RELEVANCE_MIDPOINT
        if entity.get("type") == "player":
            defaulted_signals.append(
                f"position_group={position_group!r} not in the known set {sorted(_POSITION_GROUP_SCORE)} -- midpoint default"
            )
        else:
            defaulted_signals.append(
                f"entity type={entity.get('type')!r} has no player-prominence concept -- midpoint default"
            )

    defaulted_signals.append("national-broadcast flag: not currently available on any Story Object -- see module docstring")

    rationale = (
        f"V1 provisional heuristic (§6) -- real signals used: {', '.join(used_signals) or 'none'}. "
        f"Defaulted: {'; '.join(defaulted_signals)}."
    )
    return {"score": round(score, 1), "rationale": rationale, "computation": "deterministic"}


# ============================================================
# Bounded semantic evaluation -- Significance, Betting Relevance,
# Novelty, Story Tension. One call_claude_with_tool() invocation, §8.
# ============================================================

MAX_TOKENS = 3072

SYSTEM_PROMPT = """You are scoring four dimensions of Editorial Priority for one NFL
Intelligence Story Object that has already been through Story Interrogation:
Significance, Betting Relevance, Novelty, and Story Tension.

You are not deciding what goes in a newsletter, how stories relate to each other,
or how anything gets written. You are producing four independent 0-100 scores,
each with a rationale that names the specific input field(s) it is grounded in.
Nothing you produce here is reader-facing.

Two scores are provided to you as CONTEXT, already computed deterministically:
evidence_strength_computed and audience_relevance_computed. Do not re-score or
override these — they are inputs you may use to inform Significance (e.g. weak
evidence behind an otherwise dramatic-sounding change should temper how
significant you judge it), not values you are being asked to produce.

Score each dimension on this exact 0-100 band scale, reproduced here so you use
it consistently:
  0-20   noise / not meaningfully different from normal variance
  21-40  small
  41-60  meaningful
  61-80  material
  81-100 major

## Significance

How large is the detected change, in real terms, relative to what's normal for
this kind of signal? A goal-line share swing, a market price move, and a
play-calling tendency shift are different kinds of things — judge each against
its own kind of normal, not a single numeric formula. Ground your rationale in
the actual magnitude/window fields from change and context, and in
evidence_strength_computed where weak evidence should temper the score.

## Betting Relevance

Judge whether the INTERPRETATION of a price or opportunity should change, not
merely whether a market moved. confirmation.market_reaction describing a price
move is a fact you may cite, but a market having moved is not by itself
sufficient justification for a high score — state specifically why the
detected change should update how a bettor reads this player/market, or score
it low if you cannot.

## Novelty

Judge whether this is genuinely new information relative to prior_history, not
whether a number crossed some threshold. If you score above the "small" band
(41+), you must cite something specific in prior_history that this is new
relative to. If prior_history is null or empty, you have nothing to compare
against — score in the noise/small range and say so; do not treat "no prior
data" as itself evidence of novelty.

## Story Tension

This is the dimension most directly grounded in Story Interrogation. Your
rationale must cite specific content from interrogation.challenge or
interrogation.confirmation — a real alternate explanation that was tested, a
real contradicting signal, a real ambiguity. You are PROHIBITED from citing
anything for Story Tension that is not present in interrogation.challenge or
interrogation.confirmation. Do not manufacture tension from the headline,
story text, or your own sense that something seems interesting — if
interrogation didn't surface it, it does not belong in this dimension's
rationale.

If interrogation is null, you have nothing to ground Story Tension in. Cap
your Story Tension score at 40 or below, and your rationale must state
plainly that no interrogation was available — do not invent a justification
to score higher.

## Language rules (apply to every rationale)

- Never use confidence-escalating language — "confirmed," "proven," "settled,"
  "clearly," "definitely" — about any claim, regardless of score.
- Every claim must trace to a specific field in the provided input. If you
  cannot point to what supports a sentence, do not write it.
- Plain, direct, unembellished language. No hedging filler, no rhetorical
  flourish. This is an internal scoring record, not reader-facing copy."""

# PROMPT CACHING -- this module's SYSTEM_PROMPT is a frozen module constant
# with no per-call interpolation at all, so the whole thing is the cached
# prefix: one block, one cache_control breakpoint, and the tool schema ahead
# of it caches too (tools render before system). Built once here rather than
# per call so every call site below sends byte-identical bytes.
CACHED_SYSTEM_BLOCKS = system_blocks(SYSTEM_PROMPT)


EPS_SEMANTIC_TOOL_SCHEMA = {
    "name": "record_eps_semantic_scores",
    "description": "Records Significance, Betting Relevance, Novelty, and Story Tension scores for one NFL Intelligence Story Object.",
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "significance": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"score": {"type": "integer", "minimum": 0, "maximum": 100}, "rationale": {"type": "string"}},
                "required": ["score", "rationale"],
            },
            "betting_relevance": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"score": {"type": "integer", "minimum": 0, "maximum": 100}, "rationale": {"type": "string"}},
                "required": ["score", "rationale"],
            },
            "novelty": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"score": {"type": "integer", "minimum": 0, "maximum": 100}, "rationale": {"type": "string"}},
                "required": ["score", "rationale"],
            },
            "story_tension": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"score": {"type": "integer", "minimum": 0, "maximum": 100}, "rationale": {"type": "string"}},
                "required": ["score", "rationale"],
            },
        },
        "required": ["significance", "betting_relevance", "novelty", "story_tension"],
    },
}


def _find_phrases(text: str, phrases: tuple) -> list[str]:
    if not text:
        return []
    return [p for p in phrases if re.search(rf"\b{re.escape(p)}\b", text, re.IGNORECASE)]


_SEMANTIC_DIMENSIONS = ("significance", "betting_relevance", "novelty", "story_tension")


def _valid_semantic_response_shape(response: dict) -> bool:
    """
    Defense-in-depth against a real, observed failure mode: despite
    the tool schema declaring each dimension as {score, rationale} with
    additionalProperties: false, a real API response came back once
    with a dimension key holding a plain string instead of that object
    shape -- confirmed directly (an AttributeError surfaced deep inside
    the confidence-escalation scan, not caught by call_claude_with_tool
    itself, since tool_choice-forced tool-use guarantees A tool_use
    block exists, never that its own inner shape matches the schema in
    practice). Checked once, right after each real API call, so a
    malformed shape fails the same way an API error does (loud, then
    None) rather than crashing deep inside a scan function far from the
    real cause.
    """
    if not isinstance(response, dict):
        return False
    for dim in _SEMANTIC_DIMENSIONS:
        entry = response.get(dim)
        if not isinstance(entry, dict) or not isinstance(entry.get("score"), (int, float)) or not isinstance(entry.get("rationale"), str):
            return False
    return True


def _semantic_response_string_fields(response: dict) -> list[tuple[str, str]]:
    fields = []
    for dim in ("significance", "betting_relevance", "novelty", "story_tension"):
        rationale = response.get(dim, {}).get("rationale")
        if rationale:
            fields.append((f"{dim}.rationale", rationale))
    return fields


def scan_semantic_response_for_confidence_escalation(response: dict) -> list[dict]:
    violations = []
    for field, text in _semantic_response_string_fields(response):
        for phrase in _find_phrases(text, CONFIDENCE_ESCALATING_LANGUAGE):
            violations.append({"field": field, "phrase": phrase, "text": text})
    return violations


def _story_tension_grounded_in_interrogation(rationale: str, interrogation: dict | None) -> bool:
    """
    Real, mechanical enforcement of §8's "prohibited from citing
    anything not present in interrogation.challenge or .confirmation"
    rule -- not just a prompt instruction. Checks that the rationale's
    own words overlap with real text actually present in challenge/
    confirmation, rather than trusting the model self-policed. A crude,
    conservative check (shared-substring on quoted-looking fragments
    would be more precise; this uses a simpler real-word-overlap
    heuristic, consistent with this codebase's existing appetite for
    "cheapest, least-ambiguous slice" checks over exhaustive ones — see
    story_interrogation.py's own scans for the same posture).
    """
    if interrogation is None:
        return False
    source_text = json.dumps(interrogation.get("challenge", {})) + " " + json.dumps(interrogation.get("confirmation", {}))
    source_words = set(re.findall(r"[a-z]{5,}", source_text.lower()))
    rationale_words = set(re.findall(r"[a-z]{5,}", rationale.lower()))
    overlap = source_words & rationale_words
    return len(overlap) >= 3


def build_eps_semantic_input(
    story: dict, interrogation: dict | None, prior_history: dict, evidence_strength_computed: float,
    audience_relevance_computed: float,
) -> dict:
    story_object_fields = {
        "headline": story.get("headline"),
        "story": story.get("story"),
        "primary_signal": story.get("primary_signal"),
        "trend_direction": story.get("trend_direction"),
        "trend_strength": story.get("trend_strength"),
        "related_players": story.get("related_players") or [],
        "time_window": story.get("time_window"),
    }
    return {
        "intelligence_family": story.get("intelligence_family"),
        "story_object": story_object_fields,
        "interrogation": interrogation,
        "prior_history": prior_history,
        "evidence_strength_computed": evidence_strength_computed,
        "audience_relevance_computed": audience_relevance_computed,
    }


def _retry_prompt(original_input: dict, violations: list[dict]) -> str:
    quoted = "\n".join(f"- {v['field']}: {v['text']!r} (contains: {v['phrase']!r})" for v in violations)
    return (
        f"{json.dumps(original_input)}\n\n"
        "Your previous response violated the language rules in the system prompt. "
        "Rewrite ONLY the following fields to remove the violating language — "
        "keep the same real judgment, grounded in the same input data, just "
        "without these words/phrases:\n"
        f"{quoted}\n\n"
        "Return the complete record again (every field, not just the ones "
        "being fixed) via the same tool."
    )


def _score_semantic_dimensions(
    story: dict, interrogation: dict | None, prior_history: dict, evidence_strength: dict,
    audience_relevance: dict, api_key: str,
) -> dict | None:
    """Returns the raw {significance, betting_relevance, novelty, story_tension} response, or None on failure after retry."""
    input_contract = build_eps_semantic_input(
        story, interrogation, prior_history, evidence_strength["score"], audience_relevance["score"],
    )
    user_prompt = json.dumps(input_contract)

    try:
        response = call_claude_with_tool(
            api_key, CACHED_SYSTEM_BLOCKS, user_prompt, EPS_SEMANTIC_TOOL_SCHEMA, max_tokens=MAX_TOKENS,
        )
    except ValueError as e:
        print(f"[eps] semantic scoring API call failed: {e!r}", flush=True)
        return None

    if not _valid_semantic_response_shape(response):
        print(f"[eps] malformed semantic response shape (a dimension wasn't a real {{score, rationale}} object), retrying once: {response!r}", flush=True)
        try:
            response = call_claude_with_tool(
                api_key, CACHED_SYSTEM_BLOCKS, user_prompt, EPS_SEMANTIC_TOOL_SCHEMA, max_tokens=MAX_TOKENS,
            )
        except ValueError as e:
            print(f"[eps] retry API call failed: {e!r}", flush=True)
            return None
        if not _valid_semantic_response_shape(response):
            print("[eps] malformed shape persisted after retry — returning None rather than crashing downstream.", flush=True)
            return None

    violations = scan_semantic_response_for_confidence_escalation(response)
    if violations:
        print(f"[eps] confidence-escalation violation(s), retrying once: {violations}", flush=True)
        try:
            response = call_claude_with_tool(
                api_key, CACHED_SYSTEM_BLOCKS, _retry_prompt(input_contract, violations), EPS_SEMANTIC_TOOL_SCHEMA,
                max_tokens=MAX_TOKENS,
            )
        except ValueError as e:
            print(f"[eps] retry API call failed: {e!r}", flush=True)
            return None
        if not _valid_semantic_response_shape(response):
            print("[eps] retry response also malformed — returning None.", flush=True)
            return None
        if scan_semantic_response_for_confidence_escalation(response):
            print("[eps] confidence-escalation violation persisted after retry — returning None, not a violating record.", flush=True)
            return None

    # Story Tension grounding is enforced mechanically, not just by
    # prompt instruction (§8) -- a real, hard rule, checked after the
    # language scan rather than trusted from the model's own compliance.
    tension_rationale = response.get("story_tension", {}).get("rationale", "")
    if interrogation is None:
        if response.get("story_tension", {}).get("score", 0) > 40:
            print("[eps] story_tension exceeded the interrogation:null cap (40) — clamping, not trusting the model's own number.", flush=True)
            response["story_tension"]["score"] = 40
    elif not _story_tension_grounded_in_interrogation(tension_rationale, interrogation):
        print("[eps] story_tension rationale did not overlap real interrogation content — capping at 40 as an ungrounded-tension guard.", flush=True)
        response["story_tension"]["score"] = min(response.get("story_tension", {}).get("score", 40), 40)

    return response


# ============================================================
# Composite + gates, §9.
# ============================================================

def _compute_composite(dimensions: dict) -> float:
    total = sum(dimensions[name]["score"] * weight for name, weight in DIMENSION_WEIGHTS.items())
    return round(total, 1)


def _compute_gates(dimensions: dict, composite_score: float) -> dict:
    evidence = dimensions["evidence_strength"]["score"]
    novelty = dimensions["novelty"]["score"]
    tension = dimensions["story_tension"]["score"]
    return {
        "big_one_eligible": evidence >= BIG_ONE_EVIDENCE_FLOOR,
        "watchlist_eligible": (
            composite_score >= WATCHLIST_COMPOSITE_FLOOR
            and evidence >= WATCHLIST_EVIDENCE_FLOOR
            and (novelty >= WATCHLIST_NOVELTY_OR_TENSION_FLOOR or tension >= WATCHLIST_NOVELTY_OR_TENSION_FLOOR)
        ),
    }


def compute_eps(
    story: dict, interrogation: dict | None, prior_history: dict, api_key: str,
) -> dict | None:
    """
    The real, single entry point — mirrors story_interrogation.
    interrogate_story()'s own shape. Runs the two deterministic
    dimensions first (no LLM call, never fails), then the one semantic
    call for the other four. Returns a complete §9-shaped eps object,
    or None if the semantic call fails outright after its retry — a
    STRICTER fallback than interrogation's own (§10: EPS directly gates
    publication decisions, so a guessed score is worse than an excluded
    story; the caller is expected to exclude a Story Object with
    eps: None from candidate selection, never treat it as score-zero).
    """
    evidence_strength = compute_evidence_strength(story, interrogation)
    audience_relevance = compute_audience_relevance(story)

    semantic = _score_semantic_dimensions(story, interrogation, prior_history, evidence_strength, audience_relevance, api_key)
    if semantic is None:
        return None

    dimensions = {
        "significance": semantic["significance"],
        "evidence_strength": evidence_strength,
        "betting_relevance": semantic["betting_relevance"],
        "novelty": semantic["novelty"],
        "story_tension": semantic["story_tension"],
        "audience_relevance": audience_relevance,
    }
    composite_score = _compute_composite(dimensions)
    gates = _compute_gates(dimensions, composite_score)

    return {
        "eps_version": EPS_VERSION,
        "computed_at": None,  # real timestamp stamped by the caller at write time, not guessed here (pure function, no I/O, no clock read)
        "dimensions": dimensions,
        "composite_score": composite_score,
        "gates": gates,
    }
