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

PROMPT REVISION — tension-type-aware market_reaction weighting (Task 4's
own new "WHAT KIND OF CLAIM IS ACTUALLY BEING MADE" block): a real,
measured finding, not a speculative fix. A live 36-candidate sample (3
measurement rounds, ruling out data noise and evidentiary-basis
ambiguity as the cause first) found signal_verdict gating out ~92-100%
of conclusive cases, 28 of 31 (90%) driven by genuine market-vs-
internal-signal disagreement. Root cause: the prompt was treating
market disagreement as uniform evidence against survival regardless of
what the detected signal was actually claiming. For a divergence-shaped
claim (the player's own signals vs. the market), market disagreement
IS the claim, not a threat to it — the prior design structurally
guaranteed these fail scrutiny by design, independent of whether the
claimed gap was real. Task 4 now distinguishes three real claim shapes
(market-vs-internal, cross-pillar internal split, same-signal temporal
drift) and gives market_reaction a different, deliberate role in each —
CONSTITUTIVE for the first, INCIDENTAL for the second, genuinely
corroborating for the third. See this repo's own Interrogation
semantics matrix (design doc, not code) for the full six-tension-type
reasoning this revision is built from; nfl_tension.py's own convergence
classification needed NO equivalent correction despite also being a
"market constitutive" case (agreement across every signal including
market IS convergence's own claim, so the prior uniform-weighting design
already matched what convergence needs by coincidence) — only divergence
and contradiction had a real mismatch between the claim being tested
and how market evidence was being weighed.

OPEN QUESTION, NOT RESOLVED — mismatch: nfl_tension.py does not (and, as
of this writing, cannot) produce tension_type="mismatch" — real NFL
data has no team-level offense trend distinct from an individual
player's own signals yet, so this type has never been exercised against
real data, in this module or in find_tension()'s own classification.
Task 4's three claim-shape categories above do not attempt to cover it.
If/when mismatch is ever implemented, its own claim shape (team-level
trend vs. individual player) and market's role in it need to be worked
out and verified against real data before being folded into this
section — not assumed to fit one of the three existing categories by
default.

OPEN QUESTION, NOT RESOLVED — uncertainty and the STOP gate: uncertainty
is nfl_tension.py's own reserved lowest-confidence type — by definition,
the case where evidence is too thin to classify anything more
confidently. Downstream, curate_home_shelves.py's STOP gate defaults to
gating out any candidate whose signal_verdict is UNRESOLVED. Since
UNRESOLVED is close to uncertainty's own expected resting state (the
type exists specifically for genuinely-thin-evidence cases), a blanket
STOP-on-UNRESOLVED policy may be systematically wrong for this
tension_type specifically — gating out nearly all uncertainty-type
candidates isn't obviously different from never having built the type
at all. Currently moot in production: Pass 3's own selection gate ranks
candidates by best_gap within each price band and keeps only the top
65%, and uncertainty (like convergence) is by construction a low-
best_gap classification — structurally unreachable under today's
selection parameters. Worth having on record for if/when those
selection parameters change, not resolved here — this is a gate-design
question (curate_home_shelves.py), not a prompt-wording one, and out of
this revision's own scope.

FIELD ADDITION — relationship_established (Task 4b), added to partially
answer the concern the open question above raises, though NOT a full
resolution of it: a real, hand-classified 24-case sample of live
UNRESOLVED verdicts (the fourth measurement) found 15 of 24 (62.5%)
were cases where the underlying relationship was clearly real in the
raw signal magnitudes and only its explanation/durability was actually
unresolved -- a shape structurally identical to Golden's own worked
example, not a genuine absence-of-evidence case. Blanket STOP-on-
UNRESOLVED was gating these out too, the same overreach the uncertainty
open question above already flagged, just not limited to the
uncertainty tension_type. The investigation that produced this finding
also checked, directly against the real 24-case sample, whether the
distinction could be recovered deterministically from what Interrogation
already outputs -- alternate_explanations' own status pattern (uniform
NOT_TESTABLE/UNRESOLVED, presence of SUPPORTED/WEAKENED), market_data
presence, best_gap, and confirmation field length all showed ZERO
reliable correlation with the real hand-classified answer (see that
investigation's own report for the full per-signal breakdown). No
deterministic derivation was possible without either a second LLM call
or a brittle heuristic tuned to one 24-case sample -- relationship_
established exists because the model's own reasoning clearly already
knows this distinction (several real cases state it explicitly, e.g.
"a real and testable tension... cannot be resolved" vs. "cannot be
distinguished from noise or data artifacts") but the prior schema never
asked for it directly, folding a real, meaningful distinction into one
undifferentiated UNRESOLVED bucket.

FIX — relationship_established/evidence_significance consistency scan
(scan_relationship_established_for_contradiction): a real, measured
validation of relationship_established itself (13 comparable cases from
the fourth measurement's real 24-candidate UNRESOLVED population) found
2 of 5 disagreements between the model's own hand-checked answer and a
human classifier were not genuine judgment calls -- they were the
STRUCTURED relationship_established=True field directly contradicting
the model's OWN evidence_significance prose in the same response (e.g.
"...too thin and internally ambiguous to treat this as an established
shift in either direction" alongside relationship_established=True).
This is the identical failure shape CONFIDENCE_ESCALATING_LANGUAGE and
READER_FRAMING_LANGUAGE already exist to catch (a structured/textual
mismatch within one response), so it gets the same treatment: a
deterministic post-generation scan, RELATIONSHIP_NOT_ESTABLISHED_
LANGUAGE built from the two real cases that surfaced it (not guessed at
in the abstract), checked only when relationship_established is True,
with the same one-shot retry already wired for the other two scans.

FIX — wrong-type crash in the reshaping/scanning code (_dict_field): a
real, once-observed, non-reproduced failure during that same validation
session -- one live call returned challenge, confirmation, or judgment
as a plain string instead of a dict, and neither response.get(key, {})
nor response.get(key) or {} catches that (the key IS present, holding
the wrong type; a non-empty string is truthy). Every real call site
touching these three fields -- _response_string_fields, the two
existing scans, this new third scan, and the final reshaping block --
now goes through _dict_field, which treats a non-dict value the same as
a missing one. Narrow and isolated: this does not attempt to guard
every possible nested type mismatch in the response (e.g. alternate_
explanations holding a string instead of a list is a different,
unobserved failure shape, left alone).

OPEN QUESTION, DOCUMENTED NOT CHASED — borderline-magnitude ambiguity
in relationship_established: the same real validation found that
moderate internal splits (roughly 20-30 points apart on the real 0-100
scales -- e.g. a situation score of 22.0 against a cluster reading
47.9-57.2) are genuinely ambiguous for this field, in a way a starker
split (90 vs. 20) is not. This isn't only a model limitation -- re-
reading the same real cases by hand a second time did not produce
fully self-consistent hand-classifications at this same magnitude
either (two cases with nearly identical ~25-28-point gaps were hand-
classified differently on first pass). This is treated as inherent
ambiguity in the underlying judgment call, not a defect to keep tuning
the prompt or the scan against -- there is a real, honest boundary
region where "is this gap real" doesn't have a single correct answer
from the raw magnitudes alone, the same way GAP_THRESHOLD itself
(nfl_tension.py) is a real, calibrated-not-derived cutoff rather than a
provably correct one. Not blocking; not resolved further here.

OPEN QUESTION, DOCUMENTED NOT CHASED — market/confirmation ambiguity
bleeding into relationship_established: a SEPARATE, distinct pattern
from the magnitude ambiguity above, found in a second, larger real
validation round (32 real UNRESOLVED cases, a different sample than the
one the magnitude finding came from) after the consistency-scan and
_dict_field fixes both landed. Overall agreement with hand-
classification rose to 29/32 (90.6%), and the specific "structured
field contradicts its own evidence_significance prose" failure the
consistency scan was built to catch did not recur even once in this
round -- that fix is working. The 3 real remaining disagreements are a
DIFFERENT, narrower issue: all 3 involved large, UNAMBIGUOUS raw-
magnitude splits (55-63 points apart -- e.g. situation 70.9 vs.
season-long heat 7.6), not borderline ones, yet the model answered
relationship_established=False. Reading its own reasoning in these
cases directly, the pattern is not self-contradiction (the consistency
scan correctly found nothing to flag) but a wrong CRITERION -- e.g. one
case's own evidence_significance reasoned about whether "a clear
internal signal" existed "to match the market shift," answering
whether the market corroborates the split rather than whether the
split itself is real. Task 4b's own guardrail explicitly warns against
inferring relationship_established from challenge.alternate_
explanations' shape, but says nothing equivalent about confirmation.
market_reaction's own ambiguity leaking into this same judgment --
this is that gap, low-volume (3/32, 9.4% of this round's real sample)
but real and distinguishable from ordinary noise, since these 3 cases
are not near GAP_THRESHOLD-style boundary magnitudes at all. A real
candidate for a future guardrail addition to Task 4b (explicitly
stating that market/confirmation ambiguity is not a valid basis for
relationship_established=False either, mirroring the existing
alternate_explanations warning) -- not implemented in this pass, same
"document, don't chase" treatment as the magnitude-ambiguity finding
above, since the volume here doesn't yet justify a third prompt
revision without more real data on whether it's a stable pattern or
this round's own sampling noise.

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

from card_writer_common import call_claude_with_tool, system_blocks  # noqa: E402
from evidence_validator import CONFIDENCE_ESCALATING_LANGUAGE  # noqa: E402

# Bumped from "v1_structured_data" (2026-09) -- real, substantive changes
# to this schema/contract had shipped without ever bumping this: tension-
# type-aware market_reaction weighting (divergence constitutive/
# contradiction incidental), the relationship_established field itself
# (a real schema addition, not just a reasoning change) plus its STOP-
# gate wiring in nfl_tension.py, and the consistency-check/_dict_field
# type-guard fixes. Confirmed via grep before bumping: no real code
# anywhere compares against this string's exact value (only present as
# representative fixture data in newsletter/'s own test fixtures, never
# gated on) -- safe to change. Bump this again on the next real prompt/
# schema change; this string existing is only useful if it's actually
# kept current, which it wasn't from v1 through everything above.
INTERROGATION_VERSION = "v2_relationship_established"

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
  Stay purely descriptive here regardless of what kind of claim the
  detected signal is making — WHETHER a real movement counts as support
  for or against the signal is decided in Task 4 below, not here.

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

CRITICAL — WHAT KIND OF CLAIM IS ACTUALLY BEING MADE, read this BEFORE
deciding what weight confirmation.market_reaction carries: the detected
signal is built from the player's own real scored numbers in
supporting_evidence. Before treating market_reaction as evidence for or
against signal_verdict, work out what relationship those numbers are
actually claiming — market evidence plays a genuinely different role
depending on the answer, not one uniform role every time.

- MARKET-VS-INTERNAL CLAIM: the scored numbers read broadly consistent
  with EACH OTHER (no sharp split between genuinely different signals —
  see the cross-pillar split case below), and the real question the
  signal poses is whether that consistent internal read matches what
  the real market currently prices. This is the shape of claim a
  divergence between a player's own numbers and the market IS — market
  data here is CONSTITUTIVE, not corroborating: it is literally part of
  what the signal claims, the same way a claimed price gap is only real
  if both sides of the gap are real. Do NOT treat the market
  disagreeing with the internal read as evidence against the signal for
  this shape of claim — that disagreement is the substance being
  tested, not a threat to it. Ask instead whether the gap itself looks
  real and durable: is market_data based on more than a single stale or
  thin reading, and does odds_history (when more than one real
  checkpoint is present) show the gap holding or widening, rather than
  already closing? A gap that has already reconverged by the most
  recent real reading argues for FAILS or UNRESOLVED — not because the
  market "disagreed," but because the specific gap being claimed no
  longer holds.
- CROSS-PILLAR INTERNAL-SPLIT CLAIM: two or more DIFFERENT underlying
  signals — genuinely distinct concepts, e.g. TD opportunity vs. role
  momentum vs. situation — point sharply in different directions from
  EACH OTHER, independent of market context. This is what a
  contradiction between a player's own signals IS: entirely about those
  signals disagreeing with each other. Market_reaction here is
  INCIDENTAL, not decisive — do NOT use it to argue FAILS or SURVIVES
  for this shape of claim. The real test is whether one side of the
  split is explainable as a sample-size or ceiling-effect artifact —
  exactly what Task 1's alternate_explanations mechanism already exists
  to test. A market that hasn't reacted is consistent with "nobody has
  noticed this internal tension yet," not evidence the tension isn't
  real.
- SAME-SIGNAL TEMPORAL-DRIFT CLAIM: a signal's own recent-vs-established
  pairing reads differently from itself — e.g. role momentum (the
  season-long level) vs. role trend (its own recent read), or
  season-long heat vs. emerging heat — the SAME underlying concept at
  two different time horizons, not two different concepts. This is a
  different kind of claim from a cross-pillar split: it's a trend that
  hasn't caught up to its own level yet (or vice versa), not two
  distinct signals disagreeing. Market_reaction remains a genuine,
  independent check here, the same as always — both sides of this claim
  are already internal/temporal, so the market is a real third data
  point, and disagreement between the claimed recent shift and the
  market's own (faster-updating) read is real, relevant evidence
  against durability.
- If you genuinely can't tell which of these shapes the signal is
  claiming, or the evidence mixes more than one of them at once, reason
  about each real relationship you can identify using the rule that
  applies to it — do not default to treating market_reaction as
  automatically decisive, and do not default to ignoring it either.

## Task 4b — relationship_established (ONLY when signal_verdict is UNRESOLVED)

UNRESOLVED itself does not distinguish two genuinely different real
situations, and downstream routing needs to know which one you mean:

- The claimed relationship/gap may not be REAL at all — the raw scored
  numbers themselves don't clearly show it existing (e.g. values sitting
  close together, no clear split, no clear gap between what's being
  compared). There isn't enough here to say the relationship exists,
  let alone explain it.
- OR the claimed relationship/gap clearly IS real and visible in the raw
  numbers — a genuine, sizeable split or gap is plainly there — and what
  stays unresolved is its explanation, durability, or significance, not
  its existence.

When signal_verdict is UNRESOLVED, assign relationship_established:
true or false, answering exactly one question: is the underlying
relationship/gap itself real, independent of whether you can explain
it? When signal_verdict is SURVIVES or FAILS, leave relationship_
established null — that question is already answered by signal_verdict
itself in those cases.

- true: the raw signal magnitudes show a real, sizeable gap or split —
  e.g. two values roughly 90 and 20 out of 100 is clearly a real split;
  what's unresolved is why, whether it's durable, or what it means.
- false: the raw signal magnitudes don't show a clear gap at all — e.g.
  two values roughly 57 and 48 out of 100 is not a real, established
  split, it's noise-level closeness; there isn't enough here to say a
  relationship exists in the first place.

CRITICAL — judge this directly from the raw signal magnitudes
themselves, the same numbers you already cited in confirmation.
supporting_signals/contradicting_signals. Do NOT infer relationship_
established from the shape of challenge.alternate_explanations' own
statuses — a uniform NOT_TESTABLE or UNRESOLVED array, or the presence
of a SUPPORTED or WEAKENED status, tells you what happened to specific
candidate explanations, not whether the underlying relationship is
real. Those two questions are independent, the same way signal_verdict
itself must be judged independently of alternate_explanations' statuses
(see Task 4's own CRITICAL block above) — this is the identical
guardrail, applied one level deeper.

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

# PROMPT CACHING -- this module's SYSTEM_PROMPT is a frozen module constant
# with no per-call interpolation at all, so the whole thing is the cached
# prefix: one block, one cache_control breakpoint, and the tool schema ahead
# of it caches too (tools render before system). Built once here rather than
# per call so every call site below sends byte-identical bytes.
CACHED_SYSTEM_BLOCKS = system_blocks(SYSTEM_PROMPT)


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
            "relationship_established": {
                "type": ["boolean", "null"],
                "description": "ONLY when signal_verdict is UNRESOLVED: is the underlying claimed relationship/gap itself real (true, judged from raw signal magnitudes) even though its explanation/durability/meaning isn't, or is there insufficient evidence the relationship exists at all (false)? null when signal_verdict is SURVIVES or FAILS. See Task 4b in the system prompt.",
            },
        },
        "required": ["challenge", "confirmation", "judgment", "signal_verdict", "relationship_established"],
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

# relationship_established=True structurally contradicting the model's
# OWN evidence_significance prose on the same response -- a real,
# observed failure mode (2 of 13 comparable cases in a real 24-candidate
# validation sample, not a hypothetical), not the "genuine borderline
# magnitude" ambiguity this module's own docstring documents separately.
# Built directly from the two real cases that surfaced it, same "grow
# the list from real observed violations" convention CONFIDENCE_
# ESCALATING_LANGUAGE/READER_FRAMING_LANGUAGE already follow -- not
# guessed at in the abstract.
RELATIONSHIP_NOT_ESTABLISHED_LANGUAGE = (
    "in either direction",
    "indicate a real",
    "too thin and internally ambiguous",
)


def _dict_field(response: dict, key: str) -> dict:
    """response[key] if it's genuinely a dict, {} otherwise -- missing,
    None, or a wrong-type value (e.g. a stray string) all degrade to the
    same honest empty shape, never a crash. Real, observed failure mode:
    a malformed response returned challenge/confirmation/judgment as a
    plain string instead of a dict on one real live call, which
    response.get(key, {}) and response.get(key) or {} both still crash
    on -- the key IS present, just holding the wrong type, so neither
    the default-arg form nor the `or {}` fallback ever triggers. This is
    the actual, narrow fix -- every real call site touching these three
    fields goes through this one function now, not a repeated ad hoc
    guard."""
    value = response.get(key)
    return value if isinstance(value, dict) else {}


def _find_phrases(text: str, phrases: tuple) -> list[str]:
    if not text:
        return []
    return [p for p in phrases if re.search(rf"\b{re.escape(p)}\b", text, re.IGNORECASE)]


def _response_string_fields(response: dict) -> list[tuple[str, str]]:
    """Every (field_path, text) pair in a tool-call response, for the confidence-escalation scan and for building a retry message."""
    fields = []
    for i, alt in enumerate(_dict_field(response, "challenge").get("alternate_explanations", [])):
        for key in ("explanation", "evidence", "test", "result"):
            if alt.get(key):
                fields.append((f"challenge.alternate_explanations[{i}].{key}", alt[key]))
    for key in ("supporting_signals", "contradicting_signals", "market_reaction"):
        value = _dict_field(response, "confirmation").get(key)
        if value:
            fields.append((f"confirmation.{key}", value))
    for key in ("what_we_know", "what_we_dont_know", "evidence_significance"):
        value = _dict_field(response, "judgment").get(key)
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
    text = _dict_field(response, "judgment").get("evidence_significance", "")
    return [
        {"field": "judgment.evidence_significance", "phrase": phrase, "text": text}
        for phrase in _find_phrases(text, READER_FRAMING_LANGUAGE)
    ]


def scan_relationship_established_for_contradiction(response: dict) -> list[dict]:
    """[{field, phrase, text}, ...] -- catches relationship_established
    == True directly contradicting the model's OWN evidence_significance
    prose on the same response (e.g. "...too thin and internally
    ambiguous to treat this as an established shift in either
    direction" alongside relationship_established=True -- a real case
    from a real validation sample, not a hypothetical). Only checked
    when relationship_established is True -- a False/None value already
    means "not established," so this specific contradiction can't occur
    there; this scan is not a general relationship_established
    validator, just the one real, observed inconsistency shape. Empty
    list = clean, same convention as the other two scans."""
    if response.get("relationship_established") is not True:
        return []
    text = _dict_field(response, "judgment").get("evidence_significance", "")
    return [
        {"field": "judgment.evidence_significance", "phrase": phrase, "text": text}
        for phrase in _find_phrases(text, RELATIONSHIP_NOT_ESTABLISHED_LANGUAGE)
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
        response = call_claude_with_tool(api_key, CACHED_SYSTEM_BLOCKS, user_prompt, INTERROGATION_TOOL_SCHEMA, max_tokens=MAX_TOKENS)
    except ValueError as e:
        print(f"[story_interrogation] API call failed: {e!r}", flush=True)
        return None

    # Three independent checks, each with its own one-shot retry — a
    # response could conceivably clear some and fail others, so they
    # aren't combined into a single retry pass.
    for scan_fn, label in (
        (scan_for_confidence_escalation, "confidence-escalation"),
        (scan_evidence_significance_for_reader_framing, "reader-framing"),
        (scan_relationship_established_for_contradiction, "relationship-established-contradiction"),
    ):
        violations = scan_fn(response)
        if not violations:
            continue
        print(f"[story_interrogation] {label} violation(s), retrying once: {violations}", flush=True)
        try:
            response = call_claude_with_tool(
                api_key, CACHED_SYSTEM_BLOCKS, _retry_prompt(input_contract, violations), INTERROGATION_TOOL_SCHEMA,
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
    # _dict_field(response, ...) throughout this block, NOT direct
    # indexing or a bare .get(key, {})/.get(key) or {} -- two real,
    # separately-confirmed malformed-response shapes: (1) a top-level
    # key the schema requires can be missing entirely despite forced
    # tool-use (confirmed live: 2 of 36 real calls in one measurement
    # session), which a raw response["confirmation"]-style index turns
    # into an uncaught KeyError; (2) a present key can hold the WRONG
    # TYPE -- a plain string instead of a dict (confirmed live: 1 of 24
    # real calls in a later validation session), which neither
    # response.get(key, {}) nor response.get(key) or {} catches, since
    # the key IS present and a non-empty string is truthy. _dict_field
    # treats both as the same honest empty shape -- never a crash,
    # rather than the same graceful None every OTHER documented failure
    # path here already returns (API error, persisted language-rule
    # violation).
    return {
        "interrogation_version": INTERROGATION_VERSION,
        "change": input_contract["change"],
        "context": input_contract["context"],
        "challenge": {
            "alternate_explanations": [
                {k: alt.get(k) for k in ("explanation", "evidence", "test", "result", "status")}
                for alt in _dict_field(response, "challenge").get("alternate_explanations") or []
            ],
        },
        "confirmation": {
            k: _dict_field(response, "confirmation").get(k)
            for k in ("supporting_signals", "contradicting_signals", "market_reaction")
        },
        "judgment": {
            k: _dict_field(response, "judgment").get(k)
            for k in ("what_we_know", "what_we_dont_know", "evidence_significance")
        },
        "signal_verdict": response.get("signal_verdict"),
        "relationship_established": response.get("relationship_established"),
    }
