"""
NFL Weekly Brief — Evidence Validator.

Standalone, deterministic checker over drafted newsletter content — not
the Weekly Editor Agent, not wired into any automated job, not a second
LLM call. A pure function over data, same "deterministic where the logic
can be made explicit" preference this whole project already applies
(nfl_tension.py's own module docstring makes the identical case for the
same reason).

THREE SEPARATED RESPONSIBILITIES, stated explicitly so the boundary
stays legible to whoever reads this next — each is a real, distinct
question, not three phrasings of the same check:

  CLAIM VALIDATION (validate_newsletter_story, one story at a time)
    — are factual/narrative claims supported?
      1. Claim traceability — every factual claim in `body` traces to a
         real value in the referenced story objects.
      2. Relationship traceability — a claimed relationship between two
         signals needs BOTH halves grounded, not one real half plus a
         plausible-sounding one.
      3. Evidence-classification alignment — a claim can't sound more
         confident than a "limited"-classified source story supports.

  INTERROGATION VALIDATION (folded into validate_newsletter_story, same
  data shape) — does the prose accurately represent what survived
  scrutiny? Per the Story Interrogation spec's §10:
      4. A "survived scrutiny" claim must trace to a real
         challenge.alternate_explanations[] entry with status WEAKENED
         — not to interrogation's mere existence, and not to SUPPORTED
         (the alternate explanation won, the opposite of survival),
         UNRESOLVED, or NOT_TESTABLE (both inconclusive).
      5. A "market hasn't caught up" claim must trace to
         confirmation.market_reaction actually saying that.
      6. Confidence-escalating language on either kind of claim hard-
         fails, same word list as check 3 above.

  EDITORIAL CONTRACT VALIDATION (validate_editorial_contract, one whole
  issue at a time — genuinely different granularity from the two above,
  which operate on one story) — did EPS leak into prose, and did the
  Editor obey what EPS is authorized to constrain? EPS is not a source
  of reader-facing claims (scoring language is banned outright), so
  this is NOT a traceability question — there is nothing to trace, only
  compliance to check:
      7. Scoring-language leak — dimension names, "EPS," "scored <N>,"
         or equivalent, anywhere in reader-facing prose. Hard-fail.
      8. Gate consistency — a story placed as the Big One or Watchlist
         must have had the corresponding gate true AT DRAFT TIME, read
         from the issue's own frozen eps_scores snapshot (never
         recomputed from raw dimension values — that would have the
         Validator doing exactly what the Editor is forbidden from
         doing, and never re-fetched live either, which would defeat
         the point of a receipts-freeze snapshot).
      9. One-treatment — no intelligence_story_id may appear in more
         than one section's stories[] array in the same issue. This
         automates the exact regression calibration runs 1 and 2 caught
         by hand (see nfl/newsletter/fixture_v2_run{1,2}_results.md) —
         a permanent, mechanical rule instead of relying on an LLM to
         remember the policy correctly every week.

HONEST ABOUT WHAT'S ACTUALLY AUTOMATABLE HERE, not oversold — this is the
same posture generate_nfl_shelf_card_content.validate_no_field_narration
already took ("the cheapest, least-ambiguous slice... not the full
check"): two very different claim SHAPES are checked with two very
different confidence levels.
  - NUMERIC claims (odds prices, percentages, scores, counts) are
    checked with real precision — a number either grounds against the
    real value pool within tolerance or it doesn't. These CAN fail hard.
  - NAMED-ENTITY claims (a capitalized-phrase heuristic, not real NER),
    PURELY QUALITATIVE claims (no extractable number or detected name at
    all), and the SURVIVAL/MARKET-REACTION claim detectors below (a
    fixed phrase list, not real language understanding) are surfaced
    with the same honest limits — a false miss just means a claim
    doesn't get this extra scrutiny (still checked as plain claim
    traceability); a detected claim that fails its trace IS a real hard
    fail, since the trace itself (does a real WEAKENED entry exist, does
    market_reaction actually say this) is fully mechanical
    once the claim is detected at all.
  - The scoring-language-leak check (#7) is deliberately narrower than
    a bare word match for "scored" — this is an NFL newsletter, and
    "he scored a touchdown" is completely ordinary prose. Only "scored"
    followed by a number ("scored 82") is flagged; a real, considered
    scoping decision, not an oversight. Bare dimension-name words
    ("significance," "novelty") ARE flagged as literal phrase matches
    per instruction, with the same honest caveat: ordinary English uses
    of those words in non-scoring contexts would also trip this check.
    Flagged here, not silently narrowed past what was actually asked
    for.

DOES NOT CALL ANY LLM. DOES NOT WRITE TO THE DATABASE. Pure functions.
The CALLER is responsible for fetching the real story objects (e.g.
get_published_nfl_intelligence_stories for the by-week fields, LEFT
JOINed with get_published_nfl_intelligence_latest for evidence_
classification AND interrogation — the exact RPC join this task's own
prior investigation already confirmed is necessary, since the by-week
RPC does not expose either field) and for assembling the issue dict
validate_editorial_contract expects (the Weekly Editor Agent's own real
output shape — see nfl/newsletter/weekly_editor_agent_prompt_v2.md's
Output Format section).
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Numeric extraction / grounding
# ---------------------------------------------------------------------------

# Odds prices ("+310", "-105") -- signed, 2-4 digits. Exactness is the
# claim for a price the same way NFL's own card-writer validator treats
# odds as EXACT_TOLERANCE, not rounded -- a stated +310 for a real +300
# is a real, meaningful misstatement, not reasonable rounding.
_ODDS_PATTERN = re.compile(r"[+-]\d{2,4}\b")

# Percentages ("29.4%", "51%").
_PERCENT_PATTERN = re.compile(r"\b\d+(?:\.\d+)?%")

# Plain decimal/int numbers, excluding anything already caught by the two
# patterns above and excluding small single-digit numbers (same real
# false-positive class NFL's own validate_numeric_grounding already
# excludes: "rank 8 of 20", "his 3rd game" -- common, low-stakes, not
# what this check exists to catch). Threshold set at >=6, slightly wider
# than NFL's own >=5 exclusion, since newsletter prose leans on small
# rank/count numbers ("rank 8 of 20") even more than a shelf card's
# why_reasons does.
_PLAIN_NUMBER_PATTERN = re.compile(r"(?<![+\-.\d%])\b\d{1,3}(?:\.\d+)?\b(?!%)")
_SMALL_NUMBER_FLOOR = 6.0

# A rounding tolerance for plain/percent numbers only -- odds stay exact.
# Matches this project's own established ROUNDING_TOLERANCE convention
# (card_writer_common.py) for percentile/score-shaped values: "58%" is a
# fair, grounded restatement of a real 57.7, not a fabrication.
_ROUNDING_TOLERANCE = 0.6


def _extract_numbers(text: str) -> list[tuple[str, float, bool]]:
    """
    Returns [(matched_text, numeric_value, is_exact_required), ...] for
    every real number-shaped token in `text`. `is_exact_required` is True
    for odds prices (no rounding tolerance), False for percentages/plain
    numbers (rounding tolerance applies). Small plain numbers below
    _SMALL_NUMBER_FLOOR are skipped entirely -- see the pattern's own
    comment for why.
    """
    found: list[tuple[str, float, bool]] = []
    consumed_spans: list[tuple[int, int]] = []

    for m in _ODDS_PATTERN.finditer(text):
        found.append((m.group(), float(m.group()), True))
        consumed_spans.append(m.span())

    def _overlaps_consumed(span: tuple[int, int]) -> bool:
        return any(span[0] < e and span[1] > s for s, e in consumed_spans)

    for m in _PERCENT_PATTERN.finditer(text):
        if _overlaps_consumed(m.span()):
            continue
        value = float(m.group().rstrip("%"))
        found.append((m.group(), value, False))
        consumed_spans.append(m.span())

    for m in _PLAIN_NUMBER_PATTERN.finditer(text):
        if _overlaps_consumed(m.span()):
            continue
        value = float(m.group())
        if value < _SMALL_NUMBER_FLOOR:
            continue
        found.append((m.group(), value, False))

    return found


def _real_number_pool(stories: list[dict]) -> set[float]:
    """
    Every real numeric value reachable from the referenced story objects:
    the top-level scored fields (primary_signal.value, trend_strength,
    completeness, confidence, sample_size), every number embedded in
    each story's own supporting_evidence strings and related_players[].note
    strings (both are real, human-readable facts in this schema -- see
    intelligence_schema.py's own field docs -- and both regularly carry
    the exact numbers a newsletter claim would cite, e.g. "Market value
    score 95/100 -105 (51.2% implied)"), PLUS every number embedded in
    the story's own interrogation record (change.magnitude, context.
    prior_baseline, confirmation.market_reaction, and each challenge.
    alternate_explanations[].result).

    REAL BUG FOUND AND FIXED HERE, not assumed clean: before this
    interrogation half existed, real newsletter prose citing a genuine,
    interrogation-sourced number (e.g. "the price moved from +650 to
    +400," straight out of a real confirmation.market_reaction) failed
    claim_traceability as "looks invented" -- confirmed directly by
    running this check against three real Weekly Editor Agent outputs
    (Calibration Fixture V2 runs 1-3) before writing this fix: every
    single Big One/What Changed story failed on its own real, honestly-
    cited price and percentage numbers. The root cause: market_data
    (fed into story_interrogation.interrogate_story() as a SEPARATE
    input, not part of the Story Object's own supporting_evidence) can
    introduce real numbers that never existed anywhere in the Story
    Object's own pre-interrogation fields at all -- not a test-fixture
    artifact, a structural gap that would have made this check
    systematically false-fail real, honest interrogation-informed prose
    in production.
    """
    pool: set[float] = set()
    for story in stories:
        for key in ("trend_strength", "completeness", "confidence", "sample_size"):
            v = story.get(key)
            if v is not None:
                pool.add(float(v))
        primary = story.get("primary_signal") or {}
        if primary.get("value") is not None:
            pool.add(float(primary["value"]))
        for text in story.get("supporting_evidence") or []:
            for _, value, _ in _extract_numbers(str(text)):
                pool.add(value)
        for rp in story.get("related_players") or []:
            note = rp.get("note")
            if note:
                for _, value, _ in _extract_numbers(str(note)):
                    pool.add(value)

        interrogation = story.get("interrogation") or {}
        change = interrogation.get("change") or {}
        for text in (change.get("magnitude"), change.get("what_changed")):
            if text:
                for _, value, _ in _extract_numbers(str(text)):
                    pool.add(value)
        context = interrogation.get("context") or {}
        if context.get("prior_baseline"):
            for _, value, _ in _extract_numbers(str(context["prior_baseline"])):
                pool.add(value)
        confirmation = interrogation.get("confirmation") or {}
        for text in (confirmation.get("market_reaction"), confirmation.get("supporting_signals"), confirmation.get("contradicting_signals")):
            if text:
                for _, value, _ in _extract_numbers(str(text)):
                    pool.add(value)
        challenge = interrogation.get("challenge") or {}
        for alt in challenge.get("alternate_explanations", []):
            for text in (alt.get("result"), alt.get("evidence")):
                if text:
                    for _, value, _ in _extract_numbers(str(text)):
                        pool.add(value)
    return pool


def _grounds(value: float, exact_required: bool, pool: set[float]) -> bool:
    if exact_required:
        return value in pool
    return any(abs(value - p) <= _ROUNDING_TOLERANCE for p in pool)


# ---------------------------------------------------------------------------
# Named-entity extraction (heuristic — see module docstring's honesty note)
# ---------------------------------------------------------------------------

# A capitalized word, or a run of 2+ capitalized words, that isn't
# sentence-initial (to cut down on ordinary capitalized sentence starts
# being flagged as "names"). Genuinely a heuristic, not real NER --
# every finding from this pattern is surfaced as needs_review, never a
# hard fail (see module docstring).
_NAME_CANDIDATE_PATTERN = re.compile(r"(?<!^)(?<!\. )(?<!\.\s)([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})")

# Common capitalized words that are never a real player/team name --
# filtered out before a candidate is treated as a real name-shaped claim.
_NAME_FALSE_POSITIVES = {
    "The", "A", "An", "This", "That", "These", "Those", "His", "Her", "Their",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
    "Week", "Sunday's", "TPE", "NFL",
}


def _real_name_pool(stories: list[dict]) -> set[str]:
    """Every real player/team name reachable from the referenced story
    objects — entity's own name/team, plus every related_players[]
    display_label."""
    pool: set[str] = set()
    for story in stories:
        entity = story.get("entity") or {}
        if entity.get("player_name"):
            pool.add(str(entity["player_name"]))
        if entity.get("team"):
            pool.add(str(entity["team"]))
        for rp in story.get("related_players") or []:
            if rp.get("display_label"):
                pool.add(str(rp["display_label"]))
    return pool


def _name_grounds(candidate: str, pool: set[str]) -> bool:
    """A candidate name-shaped phrase grounds if it exactly matches a
    real pool name, OR is a real substring/superstring of one (catches a
    last-name-only mention like "Kupp" against a pool entry "Cooper
    Kupp", and a team-name fragment like "Patriots" against "New England
    Patriots")."""
    for real in pool:
        if candidate == real or candidate in real or real in candidate:
            return True
    return False


def _extract_name_candidates(text: str) -> list[str]:
    seen: list[str] = []
    for m in _NAME_CANDIDATE_PATTERN.finditer(text):
        candidate = m.group(1)
        first_word = candidate.split()[0]
        if first_word in _NAME_FALSE_POSITIVES:
            continue
        if candidate not in seen:
            seen.append(candidate)
    return seen


# ---------------------------------------------------------------------------
# Check 1: Claim traceability
# ---------------------------------------------------------------------------


def check_claim_traceability(text: str, stories: list[dict]) -> list[dict]:
    """
    Every numeric and name-shaped claim in `text` checked against the
    real value/name pool drawn from `stories`. Returns one entry per
    checked claim (grounded ones included, not just failures — a human
    reviewer benefits from seeing what DID check out too, same "show the
    receipts" philosophy the rest of this product already applies).

    Each entry: {"check": "claim_traceability", "claim_type": "numeric"
    | "entity", "claim_text": str, "status": "pass" | "fail" |
    "needs_review", "detail": str}.
    """
    results: list[dict] = []
    number_pool = _real_number_pool(stories)
    name_pool = _real_name_pool(stories)

    for matched_text, value, exact_required in _extract_numbers(text):
        grounded = _grounds(value, exact_required, number_pool)
        results.append({
            "check": "claim_traceability",
            "claim_type": "numeric",
            "claim_text": matched_text,
            "status": "pass" if grounded else "fail",
            "detail": (
                f"{matched_text!r} matches a real value in the referenced story objects."
                if grounded else
                f"{matched_text!r} does not match any real value across the referenced story objects "
                f"(checked {'exact' if exact_required else f'within +/-{_ROUNDING_TOLERANCE}'} tolerance) — "
                f"looks invented or misstated."
            ),
        })

    for candidate in _extract_name_candidates(text):
        grounded = _name_grounds(candidate, name_pool)
        results.append({
            "check": "claim_traceability",
            "claim_type": "entity",
            "claim_text": candidate,
            "status": "pass" if grounded else "needs_review",
            "detail": (
                f"{candidate!r} matches a real player/team name in the referenced story objects."
                if grounded else
                f"{candidate!r} does not match any real player/team name in the referenced story objects — "
                f"could be a fabricated reference, or a real name this heuristic detector missed context for. "
                f"Human check needed either way."
            ),
        })

    return results


# ---------------------------------------------------------------------------
# Check 2: Relationship traceability
# ---------------------------------------------------------------------------

# Connectives that signal "this sentence is claiming a relationship
# between two things" — the exact shape the spec's own example targets
# ("the market hasn't caught up with the role"). Deliberately a fixed,
# reviewable list rather than a generic clause-splitter — a false miss
# here just means a relationship claim doesn't get this specific extra
# scrutiny (it still gets checked as plain claim traceability above), not
# a wrong result.
_RELATIONSHIP_CONNECTIVES = (
    "while", "but", "yet", "despite", "even as", "even though",
    "although", "whereas", "on the other hand", "hasn't caught up",
    "has not caught up", "hasn't moved", "has not moved", "diverge",
    "gap between", "not matched by", "without", "still hasn't",
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _split_on_first_connective(sentence: str) -> tuple[str, str, str] | None:
    lowered = sentence.lower()
    best: tuple[int, str] | None = None
    for connective in _RELATIONSHIP_CONNECTIVES:
        idx = lowered.find(connective)
        if idx > 0 and (best is None or idx < best[0]):
            best = (idx, connective)
    if best is None:
        return None
    idx, connective = best
    return sentence[:idx], connective, sentence[idx + len(connective):]


def check_relationship_traceability(text: str, stories: list[dict]) -> list[dict]:
    """
    Finds sentences that claim a relationship between two things (per
    _RELATIONSHIP_CONNECTIVES) and checks BOTH halves independently for a
    grounded numeric claim. Flags a sentence where only one half grounds
    — the exact failure mode the spec names: "not just one confirmed
    half and one plausible-sounding half."

    A sentence where NEITHER half has an extractable number is reported
    as needs_review, not silently skipped or auto-passed — the
    relationship itself may be real and evidenced in prose-only terms
    this check can't mechanically confirm, which is a real limit to
    surface, not paper over.

    REAL, PRE-EXISTING LIMITATION FOUND WHILE TESTING AGAINST REAL DATA
    (not introduced today, just newly visible once the numeric-pool gap
    above was fixed — before that fix, everything failed for a
    different reason and masked this one): "while" is ambiguous.
    "Osei's snap share rose while the starter sat out" is a plain
    temporal/causal clause, not a claimed relationship between two
    quantified signals — the injury is a real supporting fact that
    never needed its own number, not an ungrounded second claim. This
    check currently flags it as one-grounded/one-not anyway, the same
    as it would a genuine two-signal divergence claim. Confirmed
    directly against real Weekly Editor Agent output (Calibration
    Fixture V2 runs 1/2/3, the Osei story, every run) — not fixed here:
    disambiguating "while" used temporally vs. "while" used to claim a
    real divergence between two signals is genuinely hard to do well
    with a fixed connective list, and doing it badly risks losing real
    detection value on the connective this check most needs. Flagged
    as a known, real limitation rather than silently accepted as
    correct or quietly patched around.
    """
    results: list[dict] = []
    number_pool = _real_number_pool(stories)

    for sentence in _SENTENCE_SPLIT.split(text.strip()):
        split = _split_on_first_connective(sentence)
        if split is None:
            continue
        left, connective, right = split

        left_numbers = _extract_numbers(left)
        right_numbers = _extract_numbers(right)
        left_grounded = any(_grounds(v, exact, number_pool) for _, v, exact in left_numbers)
        right_grounded = any(_grounds(v, exact, number_pool) for _, v, exact in right_numbers)

        if not left_numbers and not right_numbers:
            status = "needs_review"
            detail = (
                f"Relationship claim (connective: {connective!r}) has no extractable numeric claim on "
                f"either side — cannot be mechanically confirmed or denied. Human check needed."
            )
        elif left_grounded and right_grounded:
            status = "pass"
            detail = f"Both halves of the relationship claim (connective: {connective!r}) ground to real values."
        elif left_grounded != right_grounded or (left_numbers and not right_numbers) or (right_numbers and not left_numbers):
            status = "fail"
            weak_side = "first" if not left_grounded else "second"
            detail = (
                f"Relationship claim (connective: {connective!r}) has one grounded half and one "
                f"un-grounded or unsupported half (the {weak_side} clause) — exactly the 'one confirmed, "
                f"one plausible-sounding' pattern this check exists to catch."
            )
        else:
            status = "fail"
            detail = f"Relationship claim (connective: {connective!r}) has numbers on both sides but neither grounds to a real value."

        results.append({
            "check": "relationship_traceability",
            "claim_text": sentence.strip(),
            "connective": connective,
            "status": status,
            "detail": detail,
        })

    return results


# ---------------------------------------------------------------------------
# Check 3: Evidence-classification alignment
# ---------------------------------------------------------------------------

# A DIFFERENT axis from banned_language.py's own GUARANTEE_LANGUAGE list
# (that list is about certainty of a BET OUTCOME -- "lock", "sure thing"
# -- a betting-compliance concern). This list is about certainty of a
# CLAIM/FACT being settled -- a real but distinct concern this task asks
# for specifically ("treating a 'limited' story's numbers as settled fact
# rather than an early/thin read"). Not reused from banned_language.py
# because the two lists genuinely mean different things, not because of
# an import-boundary reason.
CONFIDENCE_ESCALATING_LANGUAGE = (
    "confirmed", "clearly", "definitively", "certainly", "settled",
    "proven", "undeniably", "without question", "no doubt", "obviously",
    "is a fact", "already know", "for certain", "conclusively",
)

_ESCALATING_PATTERNS = {
    phrase: re.compile(rf"\b{re.escape(phrase)}\b", re.IGNORECASE)
    for phrase in CONFIDENCE_ESCALATING_LANGUAGE
}


def check_evidence_confidence_alignment(text: str, stories: list[dict]) -> list[dict]:
    """
    If ANY referenced story carries evidence_classification == "limited",
    flag every CONFIDENCE_ESCALATING_LANGUAGE occurrence in `text` — a
    "limited" source cannot honestly support declarative/settled framing
    (see the spec's own example: an early, one-book market read stated as
    settled fact). Stories missing evidence_classification entirely
    (e.g. the caller only fetched the by-week RPC and didn't join the
    "latest" RPC for it — see this module's own header) are treated as
    UNKNOWN, not "not limited" — the alignment check is skipped with an
    explicit note rather than silently passing on missing data.
    """
    results: list[dict] = []
    classifications = [s.get("evidence_classification") for s in stories]

    if not stories:
        return results
    if all(c is None for c in classifications):
        results.append({
            "check": "evidence_confidence_alignment",
            "claim_text": None,
            "status": "needs_review",
            "detail": (
                "None of the referenced story objects carry evidence_classification — likely fetched "
                "from get_published_nfl_intelligence_stories alone without the get_published_nfl_"
                "intelligence_latest join. Cannot confirm confidence alignment without it."
            ),
        })
        return results

    if "limited" not in classifications:
        return results

    for phrase, pattern in _ESCALATING_PATTERNS.items():
        if pattern.search(text):
            results.append({
                "check": "evidence_confidence_alignment",
                "claim_text": phrase,
                "status": "fail",
                "detail": (
                    f"{phrase!r} implies stronger confidence than the referenced evidence supports — "
                    f"at least one referenced story is classified 'limited'. Reframe as an early/"
                    f"observational read, not a settled claim."
                ),
            })

    return results


# ---------------------------------------------------------------------------
# Check 4: Interrogation traceability (Story Interrogation spec §10)
# ---------------------------------------------------------------------------

# A fixed, reviewable phrase list, same "false miss just means no extra
# scrutiny, not a wrong result" posture as _RELATIONSHIP_CONNECTIVES
# above — not a generic "did this sentence claim survival" classifier.
_SURVIVAL_CLAIM_PHRASES = (
    "survived", "held up", "didn't revert", "did not revert",
    "outlasted", "weathered the challenge",
)

# Same posture — the spec's own example phrasing ("the market hasn't
# caught up with the role") plus real variants, several already shared
# with _RELATIONSHIP_CONNECTIVES (that check asks "are both halves of
# THIS sentence grounded"; this one asks the narrower, additional
# question "does confirmation.market_reaction specifically support a
# no-catch-up reading" — genuinely different, not a duplicate check).
_MARKET_NOT_CAUGHT_UP_PHRASES = (
    "hasn't caught up", "has not caught up", "market hasn't moved",
    "market has not moved", "market hasn't reacted", "market has not reacted",
    "market's interpretation hasn't", "market's interpretation has not",
)

# A story's own confirmation.market_reaction text genuinely supporting
# "the market hasn't caught up" — real observed phrasing from this
# session's own live interrogation runs (story_interrogation.py's
# worked output), not guessed. A market_reaction that instead describes
# real movement ("moved from +650 to +400") does NOT match this list —
# movement having happened is a fact against a not-caught-up claim, not
# for it, and this check treats it as ungrounded rather than assuming
# any mention of the market counts.
_NO_CATCHUP_SUPPORT_PHRASES = (
    "no meaningful movement", "no meaningful line movement", "not applicable",
    "no price movement", "not provided", "no movement",
)


def check_interrogation_traceability(text: str, stories: list[dict]) -> list[dict]:
    """
    Per the Story Interrogation spec's §10: narrated claims about
    surviving scrutiny or the market not having caught up must trace to
    real content in the referenced stories' OWN interrogation record —
    not to interrogation's mere existence, and not to a plausible-
    sounding absence of contradicting evidence. Confidence-escalating
    language in the same sentence as either kind of claim hard-fails,
    same word list as check_evidence_confidence_alignment.

    Sentence-scoped (like check_relationship_traceability), not text-
    wide — a survival claim in one sentence and escalating language in
    an unrelated sentence elsewhere in the same story are two separate
    concerns, not conflated into one flag.
    """
    results: list[dict] = []
    alt_statuses = [
        alt.get("status")
        for story in stories
        for alt in ((story.get("interrogation") or {}).get("challenge") or {}).get("alternate_explanations", [])
    ]
    # WEAKENED is the ONLY status that means the original signal survived a
    # real challenge (per story_interrogation.py's own status definitions:
    # "the test result works against the alternate explanation -- the
    # detected signal held up despite the challenge"). SUPPORTED means the
    # opposite -- the alternate explanation won, so the original signal did
    # NOT survive, it was explained away. UNRESOLVED means the test was run
    # but didn't clearly favor either side -- inconclusive, not survival.
    # NOT_TESTABLE means no real test could even be run. None of the other
    # three statuses are evidence a "survived scrutiny" claim is true.
    has_weakened = any(s == "WEAKENED" for s in alt_statuses)

    market_reactions = [
        ((story.get("interrogation") or {}).get("confirmation") or {}).get("market_reaction")
        for story in stories
    ]
    has_no_catchup_support = any(
        mr and any(phrase in mr.lower() for phrase in _NO_CATCHUP_SUPPORT_PHRASES)
        for mr in market_reactions
    )

    for sentence in _SENTENCE_SPLIT.split(text.strip()):
        lowered = sentence.lower()

        survival_phrase = next((p for p in _SURVIVAL_CLAIM_PHRASES if p in lowered), None)
        if survival_phrase:
            status = "pass" if has_weakened else "fail"
            results.append({
                "check": "interrogation_traceability",
                "claim_type": "survived_scrutiny",
                "claim_text": sentence.strip(),
                "status": status,
                "detail": (
                    f"A real challenge.alternate_explanations[] entry with status WEAKENED "
                    f"grounds this claim (matched phrase: {survival_phrase!r})."
                    if status == "pass" else
                    f"Claims a signal {survival_phrase!r} scrutiny, but no referenced story's "
                    f"interrogation record has an alternate_explanations[] entry with status "
                    f"WEAKENED — traces to interrogation's mere existence (or to a status that "
                    f"does not mean survival: SUPPORTED means the alternate explanation won, "
                    f"UNRESOLVED and NOT_TESTABLE are inconclusive), not a real tested-and-held "
                    f"result."
                ),
            })
            for phrase, pattern in _ESCALATING_PATTERNS.items():
                if pattern.search(sentence):
                    results.append({
                        "check": "interrogation_traceability",
                        "claim_type": "confidence_escalation",
                        "claim_text": phrase,
                        "status": "fail",
                        "detail": (
                            f"{phrase!r} applied to a survived-scrutiny claim implies more than a "
                            f"WEAKENED status supports — a challenge that didn't hold up means the "
                            f"signal survived THAT test, not that it's proven true."
                        ),
                    })

        market_phrase = next((p for p in _MARKET_NOT_CAUGHT_UP_PHRASES if p in lowered), None)
        if market_phrase:
            results.append({
                "check": "interrogation_traceability",
                "claim_type": "market_reaction",
                "claim_text": sentence.strip(),
                "status": "pass" if has_no_catchup_support else "fail",
                "detail": (
                    f"A referenced story's confirmation.market_reaction supports a no-catch-up "
                    f"reading (matched phrase: {market_phrase!r})."
                    if has_no_catchup_support else
                    f"Claims the market {market_phrase!r}, but no referenced story's confirmation."
                    f"market_reaction supports that reading — traced to nothing, or to a "
                    f"market_reaction that instead describes real movement, which argues against "
                    f"this claim rather than for it."
                ),
            })
            for phrase, pattern in _ESCALATING_PATTERNS.items():
                if pattern.search(sentence):
                    results.append({
                        "check": "interrogation_traceability",
                        "claim_type": "confidence_escalation",
                        "claim_text": phrase,
                        "status": "fail",
                        "detail": f"{phrase!r} applied to a market-reaction claim overstates what confirmation.market_reaction actually establishes.",
                    })

    return results


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def validate_newsletter_story(
    headline: str,
    body: str,
    intelligence_story_ids: list[str],
    stories_by_id: dict[str, dict],
) -> dict:
    """
    The real entry point. `stories_by_id`: {intelligence_story_id: real
    story object dict} — the caller's own responsibility to fetch (see
    module docstring for the real two-RPC join this needs). Missing ids
    (claimed but not found in stories_by_id) are reported separately,
    never silently dropped from the referenced set.

    Returns:
      {
        "passed": bool,  # no hard "fail" entries AND no missing story ids
        "missing_story_ids": [...],
        "claim_traceability": [...],
        "relationship_traceability": [...],
        "evidence_confidence_alignment": [...],
        "interrogation_traceability": [...],
        "summary": str,
      }

    A genuinely empty `intelligence_story_ids` (not a claimed-but-
    missing id — that's the separate missing_story_ids case below) is
    treated as "not a story-evidence entry at all" and skips every
    claim-shaped check, returning a clean pass — REAL, PRE-EXISTING
    BEHAVIOR THIS MAKES EXPLICIT, not new to interrogation traceability.
    Confirmed directly against real Weekly Editor Agent output before
    adding this guard: `from_the_desk` entries legitimately carry
    intelligence_story_ids=[] per the prompt's own Output Format
    convention, and their prose is genuinely conversational, editorial
    framing about the issue as a whole — not a specific traceable claim
    about any one Story Object's evidence. Without this guard, every
    check here would run against an empty real-value pool and fail
    everything it found (a real number, a capitalized name, a word like
    "survived" used colloquially) — not because the prose is dishonest,
    but because there is structurally nothing to trace it to. Confirmed
    this was a real, live issue by running the actual checks against
    real Fixture V2 output: a from_the_desk passage using "survived"
    conversationally ("only one of them survived me asking why twice")
    hard-failed interrogation_traceability in every one of three real
    calibration runs before this guard existed.
    """
    if not intelligence_story_ids:
        return {
            "passed": True,
            "missing_story_ids": [],
            "claim_traceability": [],
            "relationship_traceability": [],
            "evidence_confidence_alignment": [],
            "interrogation_traceability": [],
            "summary": "No intelligence_story_ids claimed — not a story-evidence entry (e.g. from_the_desk); no claim-shaped checks apply.",
        }

    referenced = [stories_by_id[sid] for sid in intelligence_story_ids if sid in stories_by_id]
    missing_ids = [sid for sid in intelligence_story_ids if sid not in stories_by_id]

    combined_text = f"{headline} {body}"
    claim_results = check_claim_traceability(combined_text, referenced)
    relationship_results = check_relationship_traceability(body, referenced)
    confidence_results = check_evidence_confidence_alignment(combined_text, referenced)
    interrogation_results = check_interrogation_traceability(combined_text, referenced)

    all_results = claim_results + relationship_results + confidence_results + interrogation_results
    hard_fails = [r for r in all_results if r["status"] == "fail"]
    needs_review = [r for r in all_results if r["status"] == "needs_review"]

    return {
        "passed": not hard_fails and not missing_ids,
        "missing_story_ids": missing_ids,
        "claim_traceability": claim_results,
        "relationship_traceability": relationship_results,
        "evidence_confidence_alignment": confidence_results,
        "interrogation_traceability": interrogation_results,
        "summary": (
            f"{len(hard_fails)} hard fail(s), {len(needs_review)} flagged for human review, "
            f"{len(all_results) - len(hard_fails) - len(needs_review)} passed clean"
            + (f", {len(missing_ids)} referenced story id(s) not found" if missing_ids else "")
        ),
    }


# ---------------------------------------------------------------------------
# Editorial Contract Validation — operates on one whole ISSUE, not one
# story. A genuinely different granularity and question from everything
# above: EPS is not a source of reader-facing claims (scoring language
# is banned outright, per the Weekly Editor Agent prompt's own Step 1),
# so this is never a traceability question — there's nothing to trace,
# only compliance to check. See module docstring for the full three-
# responsibility breakdown.
# ---------------------------------------------------------------------------

# Bare dimension-name words, flagged literally per instruction — see
# module docstring's honest note on the accepted false-positive risk
# against ordinary English uses of "significance"/"novelty"/etc.
_EPS_DIMENSION_NAME_PATTERNS = {
    name: re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE)
    for name in (
        "significance", "evidence strength", "betting relevance",
        "novelty", "story tension", "audience relevance",
    )
}

# "scored" is deliberately scoped to "scored <number>", not a bare word
# match — see module docstring for why (this is an NFL newsletter;
# "he scored a touchdown" is completely ordinary prose that would
# otherwise drown out every real leak).
_EPS_JARGON_PATTERNS = {
    "EPS": re.compile(r"\beps\b", re.IGNORECASE),
    "eps_total": re.compile(r"eps_total", re.IGNORECASE),
    "scored <number>": re.compile(r"\bscored\s+(?:an?\s+)?\d", re.IGNORECASE),
    "composite score": re.compile(r"\bcomposite score\b", re.IGNORECASE),
    "dimension score": re.compile(r"\bdimension score\b", re.IGNORECASE),
    "big_one_eligible": re.compile(r"big_one_eligible", re.IGNORECASE),
    "watchlist_eligible": re.compile(r"watchlist_eligible", re.IGNORECASE),
}


def check_scoring_language_leak(text: str) -> list[dict]:
    """
    Hard-fail scan for EPS/scoring-system language in reader-facing
    prose. Not a traceability check — EPS is system state, not a claim
    a reader is ever shown, so there is nothing to substantiate, only
    a leak to catch.
    """
    results: list[dict] = []
    for name, pattern in _EPS_DIMENSION_NAME_PATTERNS.items():
        if pattern.search(text):
            results.append({
                "check": "scoring_language_leak",
                "claim_type": "dimension_name",
                "claim_text": name,
                "status": "fail",
                "detail": f"{name!r} (an EPS dimension name) appears in reader-facing prose — EPS is system state, never reader-facing.",
            })
    for label, pattern in _EPS_JARGON_PATTERNS.items():
        if pattern.search(text):
            results.append({
                "check": "scoring_language_leak",
                "claim_type": "jargon",
                "claim_text": label,
                "status": "fail",
                "detail": f"{label!r}-shaped language appears in reader-facing prose — scoring/gate mechanics must never surface to readers.",
            })
    return results


def check_gate_consistency(issue: dict) -> list[dict]:
    """
    A story placed in the Big One or Watchlist must have had the
    corresponding upstream gate true AT DRAFT TIME. Reads `eps_scores.
    big_one_eligible`/`watchlist_eligible` directly off each story entry
    in `issue` — the caller's own responsibility to have populated these
    from the real upstream `eps.gates` at draft time (or from an
    already-persisted `newsletter_story.eps_scores` row carrying them —
    see the schema migration this check depends on, tastypickems
    20260916160000). NEVER recomputed from the frozen dimension scores
    here — that would have the Validator doing exactly what the Editor
    is forbidden from doing (Step 1's own "read, don't recompute" rule)
    — and never re-fetched from live `nfl_intelligence_stories.eps.
    gates` either, which would defeat the entire point of checking
    against what was true at draft time, not what's true now.
    """
    results: list[dict] = []
    for section in issue.get("sections", []):
        section_type = section.get("section_type")
        if section_type not in ("big_one", "watchlist"):
            continue
        gate_key = "big_one_eligible" if section_type == "big_one" else "watchlist_eligible"
        for story in section.get("stories", []):
            eps_scores = story.get("eps_scores") or {}
            gate_value = eps_scores.get(gate_key)
            story_ids = story.get("intelligence_story_ids", [])
            if gate_value is None:
                results.append({
                    "check": "gate_consistency",
                    "claim_type": section_type,
                    "claim_text": story_ids,
                    "status": "needs_review",
                    "detail": (
                        f"{gate_key!r} is not present in this story's eps_scores snapshot — cannot "
                        f"confirm gate consistency without it (see the schema prerequisite this "
                        f"check depends on)."
                    ),
                })
            elif gate_value is True:
                results.append({
                    "check": "gate_consistency",
                    "claim_type": section_type,
                    "claim_text": story_ids,
                    "status": "pass",
                    "detail": f"{gate_key!r} was true at draft time for this {section_type} placement.",
                })
            else:
                results.append({
                    "check": "gate_consistency",
                    "claim_type": section_type,
                    "claim_text": story_ids,
                    "status": "fail",
                    "detail": f"Placed as {section_type}, but {gate_key!r} was false at draft time — the gate was not obeyed.",
                })
    return results


def check_one_treatment(issue: dict) -> list[dict]:
    """
    No `intelligence_story_id` may appear in more than one section's
    `stories[]` array in the same issue. `cross_references` entries are
    exempt by design — a cross-reference is structurally incapable of
    being a second treatment (no headline/body/eps_scores), the exact
    distinction Calibration Fixture V2 run 3 confirmed the model can
    act on once the schema offers it (see fixture_v2_run3_results.md).
    This automates that same distinction as a permanent, mechanical
    rule rather than relying on an LLM to remember the policy correctly
    every week — the exact regression calibration runs 1 and 2 caught
    by hand.
    """
    results: list[dict] = []
    appearances: dict[str, list[str]] = {}
    for section in issue.get("sections", []):
        section_type = section.get("section_type")
        for story in section.get("stories", []):
            for sid in story.get("intelligence_story_ids", []):
                appearances.setdefault(sid, []).append(section_type)

    for sid, sections in appearances.items():
        if len(sections) > 1:
            results.append({
                "check": "one_treatment",
                "claim_type": "duplicate_full_entry",
                "claim_text": sid,
                "status": "fail",
                "detail": (
                    f"{sid!r} appears as a full stories[] entry in {len(sections)} sections "
                    f"({', '.join(sections)}) — exactly one primary treatment is allowed; any "
                    f"other section wanting to point at it must use cross_references instead."
                ),
            })
        else:
            results.append({
                "check": "one_treatment",
                "claim_type": "single_entry",
                "claim_text": sid,
                "status": "pass",
                "detail": f"{sid!r} appears in exactly one section's stories[] array ({sections[0]}).",
            })
    return results


def validate_editorial_contract(issue: dict) -> dict:
    """
    The real entry point for Editorial Contract Validation — one whole
    issue (the Weekly Editor Agent's own real output shape, see
    weekly_editor_agent_prompt_v2.md's Output Format section), not one
    story. Scans every section's every story's headline+body, and every
    `cross_references[]` entry's `text`, for scoring-language leakage
    (deliberately NOT `notes_for_human_reviewer` — that field is
    explicitly reviewer-only, never reader-facing, and legitimately
    discusses EPS/gates by name); checks gate consistency for every Big
    One/Watchlist placement; checks one-treatment across the whole
    issue.

    Returns {"passed": bool, "scoring_language_leak": [...],
    "gate_consistency": [...], "one_treatment": [...], "summary": str}.
    """
    leak_results: list[dict] = []
    for section in issue.get("sections", []):
        for story in section.get("stories", []):
            combined = f"{story.get('headline', '')} {story.get('body', '')}"
            leak_results.extend(check_scoring_language_leak(combined))
        for xref in section.get("cross_references", []):
            leak_results.extend(check_scoring_language_leak(xref.get("text", "")))

    gate_results = check_gate_consistency(issue)
    treatment_results = check_one_treatment(issue)

    all_results = leak_results + gate_results + treatment_results
    hard_fails = [r for r in all_results if r["status"] == "fail"]
    needs_review = [r for r in all_results if r["status"] == "needs_review"]

    return {
        "passed": not hard_fails,
        "scoring_language_leak": leak_results,
        "gate_consistency": gate_results,
        "one_treatment": treatment_results,
        "summary": (
            f"{len(hard_fails)} hard fail(s), {len(needs_review)} flagged for human review, "
            f"{len(all_results) - len(hard_fails) - len(needs_review)} passed clean"
        ),
    }
