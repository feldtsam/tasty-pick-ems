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


def validate_searched_card_pricing_claims(output: dict) -> list:
    """
    The run_all_validators-style aggregate for this gate: find_pricing_
    claim_violations run over title/story/every reason_text (mirroring
    generate_nfl_shelf_card_content.run_all_validators' own banned_
    language loop exactly), plus validate_price_citation_framing over
    why_reasons. Returns a flat list of {"check": "pricing_claim" |
    "price_citation_framing", ...} issue dicts, [] if clean.
    """
    issues = []
    why_reasons = output.get("why_reasons") or []
    source_facts = output.get("_source_facts") or {}

    targets = [("title", output.get("title")), ("story", output.get("story"))]
    targets += [(f"why_reasons[{i}].reason_text", r.get("reason_text")) for i, r in enumerate(why_reasons) if isinstance(r, dict)]
    for field, text in targets:
        found = find_pricing_claim_violations(text)
        if found:
            issues.append({"check": "pricing_claim", "field": field, "phrases": found})

    for v in validate_price_citation_framing(why_reasons, source_facts):
        issues.append({"check": "price_citation_framing", **v})

    return issues
