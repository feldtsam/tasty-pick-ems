"""
NFL Weekly Brief — Evidence Validator (data-layer task, Part 2 of 2).

Standalone, deterministic checker: takes a drafted newsletter story entry
(headline + body + the `intelligence_story_ids` it claims to draw from)
and checks it against the REAL underlying nfl_intelligence_stories rows
before a human reviewer ever sees it. NOT the Weekly Editor Agent, not
wired into any automated job, not a second LLM call — a pure function
over data, same "deterministic where the logic can be made explicit"
preference this whole project already applies (nfl_tension.py's own
module docstring makes the identical case for the same reason).

THREE CHECKS, per the task's own spec:
  1. Claim traceability — every factual claim in `body` must trace to a
     real value in the referenced story objects.
  2. Relationship traceability — a claimed relationship between two
     signals ("the market hasn't caught up with the role") needs BOTH
     halves grounded, not one real half plus a plausible-sounding one.
  3. Evidence-classification alignment — a claim can't sound more
     confident than a "limited"-classified source story supports.

HONEST ABOUT WHAT'S ACTUALLY AUTOMATABLE HERE, not oversold — this is the
same posture generate_nfl_shelf_card_content.validate_no_field_narration
already took ("the cheapest, least-ambiguous slice... not the full
check"): two very different claim SHAPES are checked with two very
different confidence levels.
  - NUMERIC claims (odds prices, percentages, scores, counts) are
    checked with real precision — a number either grounds against the
    real value pool within tolerance or it doesn't. These CAN fail hard.
  - NAMED-ENTITY claims (a capitalized-phrase heuristic, not real NER)
    and PURELY QUALITATIVE claims (no extractable number or detected
    name at all) are surfaced as `needs_review`, never a hard fail — a
    heuristic name detector has real false-positive/false-negative risk,
    and a claim with nothing extractable at all genuinely can't be
    mechanically confirmed or denied without an LLM read. The validator
    tells a human reviewer WHERE to look; it doesn't pretend to replace
    their judgment on prose that has no checkable number or name in it.

DOES NOT CALL ANY LLM. DOES NOT WRITE TO THE DATABASE. Pure function:
(headline, body, intelligence_story_ids, stories_by_id) -> a structured
report. The CALLER is responsible for fetching the real story objects
(e.g. get_published_nfl_intelligence_stories for the by-week fields, LEFT
JOINed with get_published_nfl_intelligence_latest for evidence_
classification — the exact two-RPC join this task's own prior
investigation already confirmed is necessary, since the by-week RPC does
not expose evidence_classification at all).
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
    completeness, confidence, sample_size) plus every number embedded in
    each story's own supporting_evidence strings and related_players[].note
    strings (both are real, human-readable facts in this schema -- see
    intelligence_schema.py's own field docs -- and both regularly carry
    the exact numbers a newsletter claim would cite, e.g. "Market value
    score 95/100 -105 (51.2% implied)").
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
        "summary": str,
      }
    """
    referenced = [stories_by_id[sid] for sid in intelligence_story_ids if sid in stories_by_id]
    missing_ids = [sid for sid in intelligence_story_ids if sid not in stories_by_id]

    combined_text = f"{headline} {body}"
    claim_results = check_claim_traceability(combined_text, referenced)
    relationship_results = check_relationship_traceability(body, referenced)
    confidence_results = check_evidence_confidence_alignment(combined_text, referenced)

    all_results = claim_results + relationship_results + confidence_results
    hard_fails = [r for r in all_results if r["status"] == "fail"]
    needs_review = [r for r in all_results if r["status"] == "needs_review"]

    return {
        "passed": not hard_fails and not missing_ids,
        "missing_story_ids": missing_ids,
        "claim_traceability": claim_results,
        "relationship_traceability": relationship_results,
        "evidence_confidence_alignment": confidence_results,
        "summary": (
            f"{len(hard_fails)} hard fail(s), {len(needs_review)} flagged for human review, "
            f"{len(all_results) - len(hard_fails) - len(needs_review)} passed clean"
            + (f", {len(missing_ids)} referenced story id(s) not found" if missing_ids else "")
        ),
    }
