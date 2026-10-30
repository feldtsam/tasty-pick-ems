"""
Searched Card writer — the arithmetic-only pricing gate (Deliverable 4).

STAGE 1 DELIVERABLE, design-and-tests only. See searched_card_flags.py.

THE GAP THIS CLOSES: factual_validation.validate_numeric_grounding (via
card_writer_common.validate_numeric_grounding) checks that a cited
number exists somewhere among the real source facts, within tolerance.
It has no idea WHAT KIND of claim the text is making about that number —
"the price implies roughly 24%" and "his true probability is 24%" cite
the identical number and would both pass numeric grounding identically,
even though the second asserts something (a TRUE rate, not an
arithmetic translation of a quoted price) nothing in this pipeline can
back. validate_citations has the same blind spot: the cited key exists,
full stop. Neither distinguishes an honest arithmetic claim from a
fabricated certainty claim riding on the same real number. This module
is the new check that does.

TWO SEPARATE MECHANISMS, kept distinct the same way factual_validation.py
keeps "factual" and "stylistic" checks distinct (see that module's own
docstring):

  find_pricing_claim_violations(text) -- a lexicon-style BLOCK list,
  structured like factual_validation.find_trend_claims: a fixed phrase
  set, case-insensitive, word-boundary-anchored for single words. Catches
  the explicit forbidden framings (true/real/actual probability, fair
  odds/price/value, "the market is wrong," mispriced/mispricing,
  under/overvalued, under/overpriced) wherever they appear, regardless of
  whether a number is nearby — these phrases are never acceptable in a
  Searched Card, numbered claim or not.

  validate_price_citation_framing(why_reasons, source_facts) -- the
  POSITIVE requirement: any why_reason that cites implied_probability_pct
  must use an arithmetic-framing marker ("implies", "the price
  suggests...") somewhere in its own reason_text. A reason that cites the
  price number but states it as a bare fact ("he scores 24% of the time")
  with no blocked phrase AND no arithmetic framing either would sail
  through find_pricing_claim_violations clean -- this is the check that
  still catches it, because citing that field without ANY arithmetic
  framing is itself the violation (stating a market-implied number as if
  it were an observed rate, with no qualifying language at all, is the
  same fabricated-certainty failure mode in a quieter disguise).
"""
import re

BLOCKED_PRICING_PHRASES = (
    "true probability",
    "real probability",
    "actual probability",
    "real odds are",
    "actual odds are",
    "fair odds",
    "fair price",
    "fair value",
    "the market is wrong",
    "market is inefficient",
    "mispriced",
    "mispricing",
    "undervalued",
    "undervalues",
    "undervaluing",
    "overvalued",
    "overvalues",
    "overvaluing",
    "underpriced",
    "overpriced",
)

# Every phrase above happens to need no word-boundary care beyond what a
# plain case-insensitive substring search already gives (no short/common-
# word collision risk the way banned_language.py's "lock" has against
# "locked"/"locking") -- still anchored with \b for defense in depth and
# so a future addition to this tuple doesn't silently need re-auditing.
_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(p) for p in BLOCKED_PRICING_PHRASES) + r")\b",
    re.IGNORECASE,
)


def find_pricing_claim_violations(text) -> list:
    """
    Returns every BLOCKED_PRICING_PHRASES entry found in `text` (case-
    insensitive, in the order they're defined, deduplicated), or [] if
    clean. [] for a non-string, same tolerant-input convention as
    factual_validation.find_trend_claims. Deterministic and explainable,
    same "say which phrase" discipline as banned_language.
    find_banned_phrases -- a validation failure should name exactly what
    tripped it, never read as a black box.
    """
    if not isinstance(text, str) or not text:
        return []
    found = []
    for phrase in BLOCKED_PRICING_PHRASES:
        if re.search(rf"\b{re.escape(phrase)}\b", text, re.IGNORECASE) and phrase not in found:
            found.append(phrase)
    return found


# Arithmetic-only framing markers -- what IS allowed, and the minimum a
# citation of implied_probability_pct must clear. Anchored on the
# "impli-" stem (implies/implied/implying/implication) per the product
# rule's own example phrasing ("the price implies roughly X%"), plus two
# explicit synonyms seen as reasonable equivalents in real sports-betting
# commentary ("the price/odds suggests...", "priced at/around...").
# Deliberately NARROW -- this is the one place this module is permissive,
# and a narrow allowlist is the safer failure direction (a legitimate
# phrasing getting flagged for human review is recoverable; a disguised
# certainty claim slipping through because the allowlist was too generous
# is not).
_ALLOWED_FRAMING_RE = re.compile(
    r"\b(?:impli(?:es|ed|ying|cation)|"
    r"(?:price|odds|market)\s+(?:suggests?|puts? him|puts? her|points? to)|"
    r"pric(?:e|ed|ing)\s+(?:at|around))\b",
    re.IGNORECASE,
)


def validate_price_citation_framing(why_reasons: list, source_facts: dict) -> list:
    """
    Every why_reason that cites "implied_probability_pct" in its
    source_fact_keys must use an arithmetic-framing marker (see
    _ALLOWED_FRAMING_RE) somewhere in its own reason_text. This is
    deliberately scoped to THIS reason's own text, not the whole card --
    the same "a comparative claim is checked against what IT claims to
    be about" scoping card_writer_common._comparative_claim_satisfied
    already uses, for the identical reason: a story elsewhere on the card
    using "implies" correctly must not launder an unframed citation in a
    different why_reason.

    Returns a list of violation dicts (empty = clean), same shape as
    every other validator in this pipeline.

    Deliberately does NOT require source_facts to actually contain
    implied_probability_pct -- that is validate_citations' own job
    (card_writer_common.validate_citations, unmodified, reused as-is for
    this writer). This function answers a narrower, different question:
    GIVEN that a reason cited this specific field, did it frame the
    number honestly.
    """
    violations = []
    for i, reason in enumerate(why_reasons):
        cited = reason.get("source_fact_keys") or []
        if "implied_probability_pct" not in cited:
            continue
        text = reason.get("reason_text") or ""
        if not _ALLOWED_FRAMING_RE.search(text):
            violations.append({
                "reason_index": i,
                "issue": (
                    "why_reasons[{}] cites implied_probability_pct but reason_text has no arithmetic-only "
                    "framing (e.g. 'the price implies ...') -- a number from the price field must never be "
                    "stated as a bare fact without that framing".format(i)
                ),
            })
    return violations


# ---------------------------------------------------------------------------
# STAGE 1h, Task 3 -- a below-median (or above-median) PILLAR reading is a
# descriptive fact only, per the approved product decision: it must never
# become a value/pricing verdict about the PLAYER. This is a separate list
# from BLOCKED_PRICING_PHRASES, not a merge into it: BLOCKED_PRICING_PHRASES
# bans claims about what the price REALLY IS (true probability, fair odds);
# this list bans claims about whether the price is a GOOD DEAL, which is a
# related but distinct failure mode a pillar-standing claim specifically
# invites now that pillar_context.py exists to supply "above/below median"
# facts the writer could otherwise be tempted to editorialize past.
#
# DELIBERATELY NARROW, same reasoning _ALLOWED_FRAMING_RE's own comment
# already gives for erring permissive-text/narrow-blocklist: bare "value"
# and bare "opportunity" are NOT banned (per the original product rule --
# "do NOT globally ban ordinary editorial words such as 'overlooked' or
# 'value'" -- and because "opportunity" is an ordinary, necessary football
# word this very pipeline's own td_opportunity pillar is named for: "a
# scoring opportunity," "a red-zone opportunity," and plain "an opportunity
# to see more work" are all legitimate, common football phrasing that must
# keep working). Only the specific multi-word phrases below -- compounds
# that couple "value"/"opportunity" to an explicit price/betting judgment,
# or that name a price-direction verdict directly -- are blocked.
BLOCKED_VALUE_VERDICT_PHRASES = (
    "poor value",
    "bad value",
    "good value",
    "great value",
    "a bargain",
    "a steal",
    "the price is too high",
    "the price is too low",
    "priced too high",
    "priced too low",
    "odds are too high",
    "odds are too low",
    "a betting opportunity",
    "a value opportunity",
    "an opportunity at this price",
    "makes this a good price",
    "makes this a bad price",
)

_VALUE_VERDICT_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(p) for p in BLOCKED_VALUE_VERDICT_PHRASES) + r")\b",
    re.IGNORECASE,
)


def find_value_verdict_violations(text) -> list:
    """
    Same shape/contract as find_pricing_claim_violations -- returns every
    BLOCKED_VALUE_VERDICT_PHRASES entry found in `text`, [] if clean or
    non-string. Kept as its own function (not folded into find_pricing_
    claim_violations) so a caller/test can address "is this a mispricing
    claim" and "is this a value-verdict-from-a-pillar claim" as the two
    separate questions they actually are.
    """
    if not isinstance(text, str) or not text:
        return []
    found = []
    for phrase in BLOCKED_VALUE_VERDICT_PHRASES:
        if re.search(rf"\b{re.escape(phrase)}\b", text, re.IGNORECASE) and phrase not in found:
            found.append(phrase)
    return found


# ---------------------------------------------------------------------------
# STAGE 1h, Task 3 (ORIGINAL) / STAGE [this revision] (APPROVED CHANGE) --
# the comparison-basis citation check. Per the approved hard rule:
# "diverges"/pillar-standing claims describe football evidence relative to
# an EXPLICITLY STATED comparison basis. "His production ranks below the
# median" with no stated basis is not a real citation -- it reads as
# "below average," full stop, inviting exactly the unsupported-verdict
# reading this task exists to prevent.
#
# APPROVED CHANGE (this revision), per the real comparison population this
# package's own classifier/pillar_context actually use (priced,
# completeness-gate-passing rows, pooled across RB/WR/TE -- confirmed
# read-only against the real week-7 data; see illustrative_sample_cards.md
# for the investigation): the ORIGINAL rule required the single literal
# phrase "comparison group" in the SAME sentence as "median"/"ranks," every
# time. Two problems with that: (1) it forced repeating "comparison group"
# in every sentence, which reads worse than stating the basis once and
# referring to it naturally afterward; (2) "comparison group" was the only
# recognized phrase, so an equally-accurate, QB-free phrasing like "among
# this week's running backs, receivers and tight ends" failed for no real
# reason. This revision keeps the check lexical and deliberately simple
# (same philosophy as before -- a false positive is recoverable, a missed
# unsupported comparison is not), but:
#   (a) recognizes a short, named list of approved basis phrases
#       (APPROVED_COMPARISON_BASIS_PHRASES) instead of one hardcoded string
#       -- "at his position"/"other players at his position" is
#       DELIBERATELY NOT on this list: the real comparison population is
#       pooled across positions, not position-matched, so that phrasing
#       would be a false comparison-basis claim, not just an unapproved
#       synonym.
#   (b) requires the basis to appear in the same sentence as, OR IN ANY
#       SENTENCE BEFORE, the FIRST sentence that trips a trigger word --
#       not in every such sentence. Once the basis is established before
#       or at that first sentence, later sentences using "ranks"/"median"/
#       "average" again are not separately re-checked -- the reader
#       already knows what's being compared against.
#   (c) adds "average" to the trigger-word list (a pillar described as
#       "below average" implies the identical unsupported-comparison
#       problem as "below the median"). "typical" was considered and
#       deliberately NOT added -- no approved product decision covers it,
#       and adding trigger words speculatively is the wrong direction to
#       err in for a check whose whole job is flagging UNDER-specified
#       claims, not over-flagging adjacent words nobody asked to cover.
# A story with NO trigger word anywhere keeps the exact old behavior:
# clean, no violations, no cost to check.
# ---------------------------------------------------------------------------
APPROVED_COMPARISON_BASIS_PHRASES = (
    "comparison group",
    "this week's players",
    "this week's pool",
    "this week's full player pool",
    "this week's running backs, receivers and tight ends",
)

_COMPARISON_WORD_RE = re.compile(r"\b(?:medians?|ranks?|averages?)\b", re.IGNORECASE)
_COMPARISON_BASIS_MARKER_RE = re.compile(
    "|".join(re.escape(p) for p in APPROVED_COMPARISON_BASIS_PHRASES),
    re.IGNORECASE,
)


def _split_into_sentences(text: str) -> list:
    """Simple, good-enough sentence splitter for this one check -- splits
    on ./!/? followed by whitespace. Not a general-purpose NLP tool; this
    module has no third-party dependency and doesn't need one for a
    lexical, not grammatical, check."""
    pieces = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p for p in pieces if p]


def validate_comparison_basis_cited(story) -> list:
    """
    Scoped to `story` specifically, per the task's own wording. Finds the
    FIRST sentence containing a trigger word ("median"/"ranks"/"average");
    if none exists, returns [] (no change from the original check's
    behavior for a story with no comparison language at all). If one
    exists, returns [] only if an approved basis phrase (see
    APPROVED_COMPARISON_BASIS_PHRASES) appears in that sentence or any
    sentence before it -- otherwise returns a single violation naming that
    first trigger sentence. Sentences AFTER the first trigger sentence are
    never separately checked, by design (see module comment above, point b).
    """
    if not isinstance(story, str) or not story:
        return []
    sentences = _split_into_sentences(story)

    first_trigger_index = None
    for i, sentence in enumerate(sentences):
        if _COMPARISON_WORD_RE.search(sentence):
            first_trigger_index = i
            break

    if first_trigger_index is None:
        return []

    basis_established = any(
        _COMPARISON_BASIS_MARKER_RE.search(sentences[j]) for j in range(first_trigger_index + 1)
    )
    if basis_established:
        return []

    return [{
        "sentence": sentences[first_trigger_index],
        "issue": (
            "this is the first sentence citing 'median'/'ranks'/'average', and no approved comparison-basis "
            "phrase (see APPROVED_COMPARISON_BASIS_PHRASES) appears in this sentence or any sentence before "
            "it -- a comparison claim must state its basis once before or at the first such mention, not "
            "just assert 'below/above median'/'average' on its own"
        ),
    }]


def validate_searched_card_pricing_claims(output: dict) -> list:
    """
    The run_all_validators-style aggregate for this gate: find_pricing_
    claim_violations AND find_value_verdict_violations, both run over
    title/story/every reason_text (mirroring generate_nfl_shelf_card_
    content.run_all_validators' own banned_language loop exactly), plus
    validate_price_citation_framing over why_reasons, plus validate_
    comparison_basis_cited over story. Returns a flat list of {"check":
    "pricing_claim" | "value_verdict" | "price_citation_framing" |
    "comparison_basis", ...} issue dicts, [] if clean.
    """
    issues = []
    why_reasons = output.get("why_reasons") or []
    source_facts = output.get("_source_facts") or {}
    story = output.get("story")

    targets = [("title", output.get("title")), ("story", story)]
    targets += [(f"why_reasons[{i}].reason_text", r.get("reason_text")) for i, r in enumerate(why_reasons) if isinstance(r, dict)]
    for field, text in targets:
        found = find_pricing_claim_violations(text)
        if found:
            issues.append({"check": "pricing_claim", "field": field, "phrases": found})
        found_verdict = find_value_verdict_violations(text)
        if found_verdict:
            issues.append({"check": "value_verdict", "field": field, "phrases": found_verdict})

    for v in validate_price_citation_framing(why_reasons, source_facts):
        issues.append({"check": "price_citation_framing", **v})

    for v in validate_comparison_basis_cited(story):
        issues.append({"check": "comparison_basis", **v})

    return issues
