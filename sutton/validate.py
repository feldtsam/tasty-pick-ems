"""Output contract for Sutton's one LLM call.

SPEC.md "The validator rejects the output, falls back to deterministic text,
and logs H2 if any of these hold":

  - not valid JSON, or missing/extra keys
  - more than 80 words across all fields
  - any number not present in the packet's facts   (anti-fabrication)
  - any time/effort-cost claim not in facts

Rejection is not an error state. It is the designed path: the email still
goes out, carrying deterministic facts instead of prose, and H2 records that
the interpretation was dropped. Code owns tier and status either way, so a
rejected interpretation can never change what Sutton reports.

Pure functions. No network, no LLM.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from packet import fact_numbers

__all__ = ["MAX_WORDS", "REQUIRED_KEYS", "VALID_CONFIDENCE", "validate_interpretation"]

REQUIRED_KEYS = frozenset(
    {"headline", "interpretation", "recommended_investigation", "confidence"}
)
VALID_CONFIDENCE = frozenset({"low", "medium", "high"})
MAX_WORDS = 80

_NUMBER = re.compile(r"\d+(?:\.\d+)?")

# Time and effort cost. SPEC.md forbids these unless the number is in facts,
# because Sutton is not allowed to estimate what a problem cost anyone.
_COST_PHRASES = re.compile(
    r"""
    (?:\b\d+(?:\.\d+)?\s*(?:hours?|hrs?|minutes?|mins?|days?|weeks?|months?)\b)
  | \b(?:man|person|engineer|dev|developer)[-\s]?(?:hours?|days?|weeks?)\b
  | \b(?:hours?|minutes?|days?|weeks?)\s+(?:of|spent|lost|wasted|burned|debugging)\b
  | \b(?:spent|lost|wasted|burned|cost\w*)\s+(?:\w+\s+){0,2}
        (?:hours?|minutes?|days?|weeks?|time|effort)\b
  | \b(?:time|effort|engineering|maintenance|opportunity)\s+cost\b
  | \b(?:your|his|her|their|our)\s+time\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"\s+", text.strip()) if w]


def _number_allowed(value: float, allowed: set[float]) -> bool:
    return any(math.isclose(value, a, rel_tol=1e-9, abs_tol=1e-9) for a in allowed)


def validate_interpretation(raw: Any, packet: dict) -> tuple[bool, dict | None, str | None]:
    """Return (ok, parsed, rejection_reason).

    `raw` may be the model's text or an already-parsed dict. On rejection,
    parsed is None and the reason is a short machine-ish string for the H2
    signal's facts.
    """
    # --- valid JSON -------------------------------------------------------
    if isinstance(raw, (dict, list)):
        parsed = raw
    else:
        if raw is None:
            return False, None, "EMPTY_OUTPUT"
        text = str(raw).strip()
        if not text:
            return False, None, "EMPTY_OUTPUT"
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            return False, None, "INVALID_JSON"

    if not isinstance(parsed, dict):
        return False, None, "NOT_A_JSON_OBJECT"

    # --- exact key set ----------------------------------------------------
    keys = set(parsed.keys())
    missing = sorted(REQUIRED_KEYS - keys)
    extra = sorted(keys - REQUIRED_KEYS)
    if missing:
        return False, None, f"MISSING_KEYS:{','.join(missing)}"
    if extra:
        # This is what stops the LLM claiming a tier, status or colour.
        return False, None, f"EXTRA_KEYS:{','.join(extra)}"

    for key in ("headline", "interpretation", "recommended_investigation", "confidence"):
        if not isinstance(parsed[key], str):
            return False, None, f"NON_STRING_FIELD:{key}"

    if parsed["confidence"].strip().lower() not in VALID_CONFIDENCE:
        return False, None, "INVALID_CONFIDENCE"

    combined = " ".join(
        parsed[k]
        for k in ("headline", "interpretation", "recommended_investigation", "confidence")
    )

    # --- word budget ------------------------------------------------------
    word_count = len(_words(combined))
    if word_count > MAX_WORDS:
        return False, None, f"TOO_LONG:{word_count}"

    # --- anti-fabrication -------------------------------------------------
    allowed = fact_numbers(packet)
    for token in _NUMBER.findall(combined):
        try:
            value = float(token)
        except ValueError:
            continue
        if not _number_allowed(value, allowed):
            return False, None, f"NUMBER_NOT_IN_FACTS:{token}"

    # --- time / effort cost ----------------------------------------------
    match = _COST_PHRASES.search(combined)
    if match:
        phrase = match.group(0)
        nums = _NUMBER.findall(phrase)
        if not nums or not all(_number_allowed(float(n), allowed) for n in nums):
            return False, None, "COST_CLAIM_NOT_IN_FACTS"

    return True, {
        "headline": parsed["headline"].strip(),
        "interpretation": parsed["interpretation"].strip(),
        "recommended_investigation": parsed["recommended_investigation"].strip(),
        "confidence": parsed["confidence"].strip().lower(),
    }, None
