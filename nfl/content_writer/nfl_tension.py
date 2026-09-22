"""
NFL Content Generation — Editorial Voice Spec Addition, "Find the
Tension" — nfl_tension.py.

New governing hierarchy: Truth -> Insight -> Tension -> Story ->
Entertainment. Tension is the meaningful relationship between signals
that gives a story its reason to exist — the question the old hierarchy
skipped: what turns an insight into a story worth telling? This module
is the new stage the spec inserts between Analyze (scoring.py +
editorial_lenses.py, both unmodified by this task) and Tell the Story
(the existing Claude prompt/writer call in generate_nfl_shelf_card_
content.py). New generation sequence: Detect -> Analyze -> Find the
tension (this module) -> Tell the story -> Show the receipts.

DETERMINISTIC, NOT A SECOND LLM CALL. The spec's own "Build decision" is
explicit: "the analysis stage should output an actual Tension Object ...
that the writing agent then consumes" — a structured, inspectable,
storable OUTPUT of analysis, not a second creative pass. Every real
number/threshold below is checked against this pipeline's own already-
scored Story Object fields (the same `candidate` dict build_nfl_writer_
candidate() already produces for the prompt), matching this codebase's
existing preference for deterministic, testable classification
(story_archetype.py's FLOOR-gated resolver is the direct precedent) over
a second model call wherever the logic can be made explicit. The
resulting object's own phrasing is deliberately plain and mechanical,
not polished prose — "the Story Object and Tension Object can stay as
technical and rigorous as they need to be. The reader gets the
translation, not the fields" (per the spec). Turning this into a
genuinely readable sentence is the WRITER's job (nfl_shelf_card_prompt.py
consumes this object as an input alongside source_facts), not this
module's.

GUARDRAIL — NO MANUFACTURED TENSION: for every candidate find_tension()
actually analyzes, it ALWAYS returns a real Tension Object (never None
for these — every card needs one to proceed to Tell the Story), but a
"nothing genuinely stands out" case resolves to tension_type=
"convergence" with a modest, quietly-agreeing editorial_claim, not an
invented contradiction. GAP_THRESHOLD/CHANGE_THRESHOLD/NOTICE_THRESHOLD
below exist specifically so "nothing real" has a real, checkable
definition rather than being whatever's left over after every other
branch fails to match. The ONE deliberate exception, added when
signal_verdict was wired in: a candidate whose Interrogation result
says FAILS or UNRESOLVED is gated out before this function's own
analysis ever runs — see find_tension()'s own STOP GATE section below.
That's not this function learning to manufacture "no tension" as an
analysis outcome; it's a pre-check that skips the analysis entirely,
same as the function never running at all for a candidate this module
was never asked to look at.

SIGNAL_VERDICT INTEGRATION (added after Pass 1-4 of the candidate-level
Interrogation effort): signal_verdict is CANDIDATE-level (Interrogation's
own output, shared across every placement of one real candidate);
everything find_tension() itself computes is PLACEMENT-level (per-shelf,
using the shared signal_verdict as one input among several). This
boundary is load-bearing and must not be violated — find_tension() reads
interrogation_result, it never writes back to or mutates it, and nothing
here re-derives or second-guesses signal_verdict itself.

Three real interrogation states reach this function without being
gated out: "complete" (a real signal_verdict exists; SURVIVES always
proceeds to the alternate-explanation reduction below; UNRESOLVED
proceeds too, but ONLY when relationship_established == True -- see
"FIELD ADDITION" below; FAILS, and UNRESOLVED with relationship_
established False/missing, never reach here at all -- see the STOP
GATE), "not_selected" (Pass 3's own capacity gate chose not to spend a
call on this candidate this run -- a resource-allocation decision, not
an epistemic one), and "failed" (selected, attempted, Pass 4's own
bounded retries didn't produce a usable result). A 4th case,
"no_interrogation", covers a caller that passes no interrogation_result
at all (interrogation_result=None, the default) -- a genuinely
different case from Pass 3 deliberately not selecting a real candidate,
kept distinct rather than conflated with "not_selected". For every case
that isn't "complete" with a real, gate-clearing verdict, the
pre-existing seven legacy fields (tension_type/primary_signal/
counter_signal/editorial_claim/story_angle/evidence_strength/
uncertainty) compute EXACTLY as they always have -- this is NOT a
scrutiny claim about the candidate ("resource allocation must never
masquerade as evidence evaluation," Pass 3's own governing principle,
applies here too) -- it's the same gap-based classification tier that
predates Interrogation entirely, running because nothing deeper is
available, not because something deeper was checked and passed.

FIELD ADDITION — relationship_established: a real, hand-validated
24-case investigation (see story_interrogation.py's own module
docstring for the full write-up) found UNRESOLVED conflates two
genuinely different situations -- 15 of 24 real cases (62.5%) had a
clearly real underlying relationship (a genuine, sizeable gap visible
in the raw signal magnitudes) where only the EXPLANATION or durability
was unresolved, the same shape as the Golden worked example this whole
module was calibrated against, not a genuine absence-of-evidence case.
Blanket STOP-on-UNRESOLVED was gating these out for no real reason.
relationship_established (judged directly from raw signal magnitudes by
the model, per that field's own guardrail -- confirmed by the same
investigation that alternate_explanations' own status shape has ZERO
reliable correlation with the real answer, so this function must never
attempt to re-derive it from that array either) is the one thing that
now distinguishes "STOP, not enough evidence this is even real" from
"PROCEED as Discovery, the relationship is real but its meaning isn't."

GUARDRAIL — TENSION HAS TO BE EARNED: a real gap that clears GAP_
THRESHOLD/CHANGE_THRESHOLD is classified normally (divergence/
contradiction/change) REGARDLESS of evidence strength — thin evidence
does not reclassify a real relationship into "uncertainty" (confirmed
against the spec's own Matthew Golden worked example: market_value 72.9
vs. a ~53-57 blended internal read is still type="divergence" even
though evidence_quality was only 45/100 and td_opportunity_completeness
only 30%). What thin evidence changes is `uncertainty` — a real,
non-null hedge the writer must use to frame the CLAIM as early/
observational rather than declarative, never the TYPE. "uncertainty" as
its own TYPE is reserved for the genuinely lowest-confidence case: no
relationship clears the confident threshold, evidence is thin, but
something still clears the much lower NOTICE_THRESHOLD (a real, if weak,
movement — not nothing).

CALIBRATION: GAP_THRESHOLD=18.0 is set directly against that same
Matthew Golden example — the one real, concrete case the spec hands
this module to calibrate against — market_value (72.9) vs. an internal
read blending td_opportunity (57.1) and role_momentum (50.0) sits
comfortably above it. Not independently re-derived from a real
population distribution the way e.g. story_archetype.py's FLOOR=55 was —
flagged here honestly as a single-example calibration, not a broad
statistical one, same distinction that module's own docstring draws
between "reused, pre-vetted" and "reasoned fresh."

SCOPE, HONEST NOT ASPIRATIONAL: NFL's Story Object today has no team-
level offense trend distinct from the player's own signals (`situation`/
defensive_matchup_vulnerability describes the OPPONENT's defense, not
this player's own team's scoring trend) — so the spec's "Mismatch" type
(team trend vs. individual player) is NOT detectable from real NFL
fields yet and is deliberately not implemented as a guess. Five of the
spec's six types are real here: divergence, contradiction, change,
convergence, uncertainty. Each sport/family is explicitly allowed to
define which tension types it's capable of detecting (per the spec's own
"Where this lives" section) — this is that decision for NFL, not an
oversight.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from editorial_lenses import signal_phrase  # noqa: E402

TENSION_TYPES = ("divergence", "contradiction", "change", "convergence", "uncertainty")

# information_value is a pure function of tension_type ALONE, by design
# -- independent of evidence_strength (a thin-evidence divergence is
# still a HIGH-information relationship; the hedge belongs in
# uncertainty/allowed_claim_strength, not here). "mismatch" is included
# for v2 schema completeness only -- it can never actually be produced
# by this module (see SCOPE docstring above: not detectable from real
# NFL fields today).
_INFORMATION_VALUE_BY_TYPE = {
    "divergence": "HIGH", "contradiction": "HIGH", "mismatch": "HIGH",
    "change": "MEDIUM", "convergence": "MEDIUM", "uncertainty": "MEDIUM",
}

# reader_question_type is a pure function of story_mode ALONE. Only two
# real story_modes are produced today (discovery/explanation) -- other
# v2 reader_question_type values are reserved, not generated yet, per
# the approved design.
_READER_QUESTION_TYPE_BY_STORY_MODE = {"discovery": "why", "explanation": "what_it_means"}

GAP_THRESHOLD = 18.0        # confident-classify gap between two signals' percentile scores -- see module docstring's calibration note
CHANGE_THRESHOLD = 18.0     # same bar, applied to a trend-vs-level gap within one signal
NOTICE_THRESHOLD = 8.0      # a smaller gap still worth naming as "uncertainty" rather than flat agreement
THIN_EVIDENCE_FLOOR = 50.0  # evidence_quality below this reads "thin" -- matches this project's real neutral-50 fill_neutral sentinel (a genuine below-neutral reading, not an arbitrary new cutoff)

# Internal (non-market) signals compared against each other for
# "contradiction", and against market_value for "divergence".
_INTERNAL_SIGNALS = ("td_opportunity", "role_momentum", "situation")


def _real(value) -> float | None:
    """None for missing/NaN, the real float value otherwise -- same
    honest-None convention as every other numeric-safety helper already
    in this codebase (cfb/story_archetype.py's _real, nfl/story_
    archetype.py's own version)."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN != NaN


def _level_word(value: float) -> str:
    """Plain descriptor for a 0-100 percentile-style score -- used only
    inside this module's own phrase construction, not shown to the
    reader (the writer re-expresses these in its own words)."""
    if value >= 75:
        return "well above average"
    if value >= 60:
        return "above average"
    if value >= 40:
        return "right around average"
    if value >= 25:
        return "below average"
    return "well below average"


def _evidence_strength(candidate: dict) -> str:
    """"thin" or "strong" -- driven by evidence_quality, this pipeline's
    own already-computed aggregate confidence measure (how much the
    other real signals agree with each other AND how complete they are),
    rather than re-deriving a fresh weighted average of raw completeness
    fields here."""
    eq = _real(candidate.get("evidence_quality"))
    return "thin" if eq is None or eq < THIN_EVIDENCE_FLOOR else "strong"


def _uncertainty_note(candidate: dict, strength: str) -> str | None:
    """Non-null exactly when evidence_strength == 'thin' -- the real
    hedge the writer must use to frame its claim as early/observational
    rather than declarative (see module docstring's "tension has to be
    earned" guardrail). Names WHICH real field is thin when one clearly
    is, rather than a generic "evidence is thin" -- concrete beats vague
    even in an intermediate object nobody but a reviewer/writer sees."""
    if strength != "thin":
        return None
    thinnest = None
    thinnest_value = 101.0
    for key in ("td_opportunity_completeness", "role_momentum_completeness", "situation_completeness", "market_value_completeness"):
        v = _real(candidate.get(key))
        if v is not None and v < thinnest_value:
            thinnest, thinnest_value = key, v
    if thinnest is not None and thinnest_value < 50.0:
        return f"{signal_phrase(thinnest.replace('_completeness', ''))} is only {thinnest_value:.0f}% complete, so the reason behind this reading isn't fully clear yet."
    return "The evidence behind this reading isn't strong enough yet to say confidently why."


def _interrogation_status_for(interrogation_result: dict | None) -> str:
    """One of Pass 3/4's own three real states ("complete"/"not_selected"/
    "failed"), or "no_interrogation" for a caller that passed nothing at
    all -- distinct from "not_selected" on purpose: Pass 3 deliberately
    choosing not to spend a call on a real candidate is a different fact
    than this function simply never being told about an interrogation
    result in the first place (a caller outside the Pass 2 dedup/fan-out
    path, or a legacy call). A malformed/unrecognized status string
    degrades to "no_interrogation" too -- honest unknown, not a guess at
    which real state was probably meant."""
    if not interrogation_result:
        return "no_interrogation"
    status = interrogation_result.get("interrogation_status")
    return status if status in ("complete", "not_selected", "failed") else "no_interrogation"


def _story_mode_for_survives(interrogation_result: dict) -> tuple[str, str, bool]:
    """Only called when interrogation_status == 'complete' and
    signal_verdict == 'SURVIVES' -- the alternate-explanation reduction
    from the approved gate design, a pure function of challenge.
    alternate_explanations' own real statuses (Pass 1's own output,
    read here, never re-derived). Returns (story_mode,
    allowed_claim_strength, force_evidence_thin):
      - any status == SUPPORTED -> ("explanation", "confident", False) --
        a real, confirmed rival explanation exists; tell the reader why.
      - non-empty, all WEAKENED -> ("discovery", "confident", False) --
        every challenge failed; the original signal itself survived a
        real test, confidently, even with no single settled "why".
      - empty array -> ("discovery", "confident", False) -- nothing was
        even there to challenge; same confident-discovery treatment.
      - any UNRESOLVED/NOT_TESTABLE present (no SUPPORTED) ->
        ("discovery", "tentative", True) -- the challenge itself
        couldn't cleanly resolve, so the claim is hedged even though
        signal_verdict itself already says SURVIVES; force_evidence_thin
        tells the caller to treat evidence_strength as "thin" from here
        on, keeping the returned object's own evidence_strength/
        uncertainty fields internally consistent with this hedge rather
        than contradicting it.
    """
    alt_explanations = (interrogation_result.get("result") or {}).get("challenge", {}).get("alternate_explanations") or []
    statuses = [alt.get("status") for alt in alt_explanations]
    if "SUPPORTED" in statuses:
        return "explanation", "confident", False
    if not statuses or all(s == "WEAKENED" for s in statuses):
        return "discovery", "confident", False
    return "discovery", "tentative", True


def _tension_object(
    type_: str, primary_signal: str, counter_signal: str | None,
    editorial_claim: str, story_angle: str, strength: str, uncertainty: str | None,
    interrogation_status: str, story_mode: str | None,
    reader_question_type: str | None, allowed_claim_strength: str | None,
) -> dict:
    return {
        "tension_type": type_,
        "primary_signal": primary_signal,
        "counter_signal": counter_signal,
        "evidence_strength": strength,
        "editorial_claim": editorial_claim,
        "uncertainty": uncertainty,
        "story_angle": story_angle,
        "information_value": _INFORMATION_VALUE_BY_TYPE[type_],
        "interrogation_status": interrogation_status,
        "story_mode": story_mode,
        "reader_question_type": reader_question_type,
        "allowed_claim_strength": allowed_claim_strength,
    }


def find_tension(candidate: dict, lens: dict | None = None, interrogation_result: dict | None = None) -> dict | None:
    """
    The real "Find the Tension" stage. Pure function: candidate (build_
    nfl_writer_candidate's own output, or any dict with the same real
    keys) -> a Tension Object (see module docstring for the exact shape
    and the guardrails governing it), or None for a gated-out candidate
    (see STOP GATE below). `lens` is accepted for a future caller that
    wants the primary editorial signal to weight which relationship gets
    checked first, but is not required by any check below today -- every
    real signal pair is compared regardless of lens, since a real
    tension between two signals is real whether or not this shelf's own
    lens happens to lead with either of them.

    `interrogation_result`: the CANDIDATE-level Interrogation result
    (curate_home_shelves._interrogate_unique_candidates' own three-state
    output: {"interrogation_status": ..., "result": ...|None}) already
    computed once for this candidate and shared across every placement
    of it -- see module docstring's own "SIGNAL_VERDICT INTEGRATION"
    section for the full real/gated/legacy-fallback split. Optional,
    defaulting to None (a caller outside the Interrogation path, or one
    that simply hasn't been updated to pass it) -- treated the same as
    "no_interrogation" below, never a crash.

    STOP GATE, checked BEFORE any of this function's own analysis runs:
      - signal_verdict == FAILS -> always STOP (return None immediately).
      - signal_verdict == UNRESOLVED -> STOP unless relationship_
        established == True. UNRESOLVED alone conflates two real,
        different situations (see story_interrogation.py's own Task 4b):
        the claimed relationship may not be real at all (relationship_
        established False or None/missing -- STOP, same as before this
        field existed), or the relationship IS real and only its
        explanation/durability is unresolved (relationship_established
        True -- PROCEEDS as a Discovery story, see below). A real, hand-
        validated 24-case sample found this second shape is the MAJORITY
        of real UNRESOLVED verdicts (15/24, 62.5%) -- the prior blanket
        STOP-on-UNRESOLVED was gating out real, Golden-shaped candidates
        for no real reason.
      - signal_verdict == SURVIVES -> never gated here.
    A gated-out candidate does not get a Tension Object, and (per the
    caller's own contract) does not get a shelf card at all. This is a
    pre-check, not this function learning to sometimes manufacture "no
    tension" as an analysis outcome after starting one -- for every
    input that clears the gate (SURVIVES, UNRESOLVED+established,
    not_selected, failed, or no_interrogation), the existing "always
    returns a real object" guardrail holds exactly as before.
    """
    interrogation_status = _interrogation_status_for(interrogation_result)

    signal_verdict = None
    relationship_established = None
    if interrogation_status == "complete":
        result = interrogation_result.get("result") or {}
        signal_verdict = result.get("signal_verdict")
        if signal_verdict == "FAILS":
            return None
        if signal_verdict == "UNRESOLVED":
            relationship_established = result.get("relationship_established")
            if not relationship_established:  # False or None/missing both STOP -- never assume established
                return None

    story_mode = reader_question_type = allowed_claim_strength = None
    force_thin = False
    if interrogation_status == "complete" and signal_verdict == "SURVIVES":
        story_mode, allowed_claim_strength, force_thin = _story_mode_for_survives(interrogation_result)
        reader_question_type = _READER_QUESTION_TYPE_BY_STORY_MODE[story_mode]
    elif interrogation_status == "complete" and signal_verdict == "UNRESOLVED":
        # Only reachable with relationship_established == True -- the
        # gate above already returned None for every other case. Per the
        # locked gate design: always Discovery, always tentative, always
        # forced-thin -- this is NOT run through _story_mode_for_survives
        # (there is no alternate-explanation reduction for an UNRESOLVED
        # verdict; SURVIVES's own reduction logic doesn't apply here).
        story_mode = "discovery"
        allowed_claim_strength = "tentative"
        force_thin = True
        reader_question_type = _READER_QUESTION_TYPE_BY_STORY_MODE[story_mode]

    def _build(type_, primary_signal, counter_signal, editorial_claim, story_angle, strength, uncertainty):
        return _tension_object(
            type_, primary_signal, counter_signal, editorial_claim, story_angle, strength, uncertainty,
            interrogation_status=interrogation_status, story_mode=story_mode,
            reader_question_type=reader_question_type, allowed_claim_strength=allowed_claim_strength,
        )

    mv = _real(candidate.get("market_value_score"))
    td = _real(candidate.get("td_opportunity"))
    rm = _real(candidate.get("role_momentum"))
    sit = _real(candidate.get("situation"))
    rm_trend = _real(candidate.get("role_trend"))
    proven = _real(candidate.get("proven_heat"))
    emerging = _real(candidate.get("emerging_heat"))

    strength = _evidence_strength(candidate)
    # force_thin (only ever True for a SURVIVES candidate whose own
    # alternate-explanation testing came back UNRESOLVED/NOT_TESTABLE)
    # overrides the raw evidence_quality-derived reading BEFORE
    # uncertainty is computed from it, so the returned object stays
    # internally consistent -- evidence_strength says "thin" and
    # uncertainty is a real, non-null hedge together, never one without
    # the other. Does NOT change which tension_type gets classified
    # below (see module docstring's "TENSION HAS TO BE EARNED"
    # guardrail -- thin evidence never reclassifies a real gap, it only
    # changes the fallback branch's own uncertainty-vs-convergence
    # choice, exactly as it already did before this override existed).
    if force_thin:
        strength = "thin"
    uncertainty = _uncertainty_note(candidate, strength)

    internal = {"td_opportunity": td, "role_momentum": rm, "situation": sit}
    internal_present = {k: v for k, v in internal.items() if v is not None}

    best_gap = 0.0  # tracks the strongest real relationship found, for the uncertainty/convergence fallback

    # --- 1. Divergence: market_value vs. the blended internal read ---
    if mv is not None and internal_present:
        internal_avg = sum(internal_present.values()) / len(internal_present)
        gap = mv - internal_avg
        best_gap = max(best_gap, abs(gap))
        if abs(gap) >= GAP_THRESHOLD:
            internal_names = ", ".join(signal_phrase(k) for k in internal_present)
            if gap > 0:
                return _build(
                    "divergence",
                    primary_signal=f"{signal_phrase('market_value')} reads {_level_word(mv)} ({mv:.1f}/100)",
                    counter_signal=f"the player's own signals ({internal_names}) read {_level_word(internal_avg)} by comparison ({internal_avg:.1f}/100 blended)",
                    editorial_claim="The market is showing more conviction in this player than his own recent on-field signals do.",
                    story_angle="The market may be seeing something that hasn't shown up clearly in this player's usage yet.",
                    strength=strength, uncertainty=uncertainty,
                )
            return _build(
                "divergence",
                primary_signal=f"the player's own signals ({internal_names}) read {_level_word(internal_avg)} ({internal_avg:.1f}/100 blended)",
                counter_signal=f"{signal_phrase('market_value')} hasn't caught up to that yet ({mv:.1f}/100, {_level_word(mv)})",
                editorial_claim="This player's own signals are stronger than the market currently gives him credit for.",
                story_angle="If the market catches up to what the on-field signals already show, the price won't stay where it is.",
                strength=strength, uncertainty=uncertainty,
            )

    # --- 2. Contradiction: the two most divergent INTERNAL signals ---
    if len(internal_present) >= 2:
        pairs = [(a, b) for i, a in enumerate(internal_present) for b in list(internal_present)[i + 1:]]
        a_name, b_name = max(pairs, key=lambda p: abs(internal_present[p[0]] - internal_present[p[1]]))
        gap = internal_present[a_name] - internal_present[b_name]
        best_gap = max(best_gap, abs(gap))
        if abs(gap) >= GAP_THRESHOLD:
            hi, lo = (a_name, b_name) if gap > 0 else (b_name, a_name)
            return _build(
                "contradiction",
                primary_signal=f"{signal_phrase(hi)} reads {_level_word(internal_present[hi])} ({internal_present[hi]:.1f}/100)",
                counter_signal=f"{signal_phrase(lo)} reads {_level_word(internal_present[lo])} ({internal_present[lo]:.1f}/100) -- a real mismatch with that",
                editorial_claim=f"This player's own signals are pulling in different directions ({hi} vs. {lo}).",
                story_angle="One of these two signals is probably about to move toward the other -- the question is which one.",
                strength=strength, uncertainty=uncertainty,
            )

    # --- 3. Change: a trend sub-component moving away from its own level ---
    for level_val, trend_val, level_name, trend_name in (
        (rm, rm_trend, "role_momentum", "its own recent trend"),
        (proven, emerging, "the season-long (proven) read", "the recent (emerging) read"),
    ):
        if level_val is not None and trend_val is not None:
            gap = trend_val - level_val
            best_gap = max(best_gap, abs(gap))
            if abs(gap) >= CHANGE_THRESHOLD:
                return _build(
                    "change",
                    primary_signal=f"the season-long picture ({level_name}) reads {_level_word(level_val)} ({level_val:.1f}/100)",
                    counter_signal=f"{trend_name} reads {_level_word(trend_val)} instead ({trend_val:.1f}/100) -- a real recent move",
                    editorial_claim="The season-long numbers haven't caught up to what's happened the last couple of weeks.",
                    story_angle="The recent trend is the more current read here -- the season-long number is already stale.",
                    strength=strength, uncertainty=uncertainty,
                )

    # --- 4/5. Nothing cleared GAP/CHANGE_THRESHOLD: uncertainty or convergence ---
    present_all = {**internal_present, **({"market_value": mv} if mv is not None else {})}
    if strength == "thin" and best_gap >= NOTICE_THRESHOLD:
        return _build(
            "uncertainty",
            primary_signal="something in this player's profile is moving",
            counter_signal="the evidence backing it is still too thin to say confidently why",
            editorial_claim="There's a real signal here, but the evidence isn't strong enough yet to say what's driving it.",
            story_angle="Worth watching, not yet worth a confident claim.",
            strength=strength, uncertainty=uncertainty,
        )

    if present_all:
        avg = sum(present_all.values()) / len(present_all)
        names = ", ".join(signal_phrase(k) for k in present_all)
        claim = (
            "Every real signal here is quietly pointing the same direction."
            if avg >= 60 or avg <= 40 else
            "Every real signal here is sitting in the same unremarkable middle range -- nothing dramatic, but nothing contradicting itself either."
        )
        return _build(
            "convergence",
            primary_signal=f"{names} are all reading {_level_word(avg)} within a real band of each other",
            counter_signal=None,
            editorial_claim=claim,
            story_angle="The story here is agreement itself, not a single standout number.",
            strength=strength, uncertainty=uncertainty,
        )

    # No real signals present at all -- the honest degenerate case.
    return _build(
        "convergence",
        primary_signal="no real signal is available yet for this player",
        counter_signal=None,
        editorial_claim="There isn't enough real data yet to say anything specific about this player.",
        story_angle="Too early to tell a real story here.",
        strength="thin", uncertainty="Every real underlying signal is missing or incomplete for this player.",
    )
