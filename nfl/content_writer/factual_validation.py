"""
Factual validation for NFL shelf cards -- the HARD gate (Item 3, 2026-10).

WHY THIS MODULE EXISTS: a card's title or story can make a claim the row
it sits on cannot support. The real case that forced this (week 5, 2026):
seven live Red Zone Trends titles said a player's usage was "climbing"
while every trend input on the row was masked (not enough games to read
a trend at all) and every role-signal delta was 0. Nothing on the
publishing path checked that. The writer was handed trend VOCABULARY
(the shelf voice pool) without any trend DATA, and the existing
validators only ever checked that cited numbers exist -- a title with no
number in it sailed through, and a reviewer could approve it.

TWO KINDS OF VALIDATION, kept visibly separate here and in every
validation_issues entry via the "category" key:

  factual   -- the text claims something the row does not support, or
               the claim is not traceable to the row at all. These are
               hard gates: curate_home_shelves' write boundary replaces
               the card's LLM text with the deterministic fallback before
               the row is written, so approval has nothing to override.
               Checks: schema_shape (a malformed draft has no verifiable
               claims), citation, numeric_grounding, star_consistency,
               pillar_field_consistency, and this module's trend_claim.
  stylistic -- voice/policy problems that do not assert a false fact:
               banned_language (guarantee language, betting slang, price-
               movement forecasts) and field_narration (prose that reads
               a JSON field aloud). These keep today's behavior exactly:
               they still set validation_passed=False and flag the card
               for review, and a reviewer can still approve with a note.
  warning   -- story_length, column_name, stock_phrase: never block,
               never flag (run_all_warnings' own list, unchanged).

THE TREND-CLAIM RULE: a trend word (climbing, rising, growing, trending,
surging, outrunning, ... see _UP/_DOWN/_NEUTRAL below) is allowed in a
title or story ONLY when the row has at least one UNMASKED trend field
for that shelf AND that field's delta is non-zero in the claimed
direction. Verbs and adjectives only -- the nouns "trend"/"trends" are
never matched, so a shelf name like "Red Zone Trends" in a title cannot
trip the rule.

MISSING EVIDENCE vs NO CHANGE are different facts and the issue says
which one applied:
  reason "masked"          -- every trend field is masked: cannot say.
  reason "no_change"       -- an unmasked field exists but its delta is 0.
  reason "wrong_direction" -- an unmasked, non-zero delta points the
                              other way from the word used.

A real structural note behind "no_change": add_rolling_windows'
_last3 (rolling 3, min_periods=1, shift 1) and _season_avg (expanding
mean, shift 1) are IDENTICAL until a player's fifth game, so no trend
delta can be non-zero before then -- the 40-of-40 zero deltas on the
live week-5 Red Zone cards were structural, not a coincidence.

Pure functions, no I/O, no network. Reused by generate_nfl_shelf_card_
content (prompt-side prevention + draft validation) and by curate_home_
shelves (the real gate at the write boundary).
"""
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from editorial_lenses import is_masked_fallback  # noqa: E402

# --------------------------------------------------------------------
# Lexicon. Verbs/adjectives only. Word-boundary, case-insensitive.
# "trend"/"trends" (nouns) deliberately absent; "trending"/"trended"
# are the only forms of that stem matched.
# --------------------------------------------------------------------
_UP = (
    r"climb(?:s|ed|ing)?", r"keeps? climbing", r"ris(?:es|ing|en)", r"on the rise", r"grow(?:s|n|ing)",
    r"surg(?:es|ed|ing)", r"expand(?:s|ed|ing)", r"outrun(?:s|ning)", r"outpac(?:es|ed|ing)",
    r"trending (?:up|upward|higher)", r"trend(?:s|ed) (?:up|upward|higher)", r"ramping(?: up)?",
    r"heating up", r"ticking up", r"picking up", r"taking off", r"mounting", r"swelling", r"ballooning",
    r"soaring", r"spiking", r"accelerating", r"escalating", r"climbing faster",
)
_DOWN = (
    r"falling", r"fading", r"shrink(?:s|ing)", r"declin(?:es|ed|ing)", r"slipping", r"dropping", r"sinking",
    r"cooling(?: off)?", r"dwindl(?:es|ed|ing)", r"eroding", r"receding", r"trending (?:down|downward|lower)",
    r"trend(?:s|ed) (?:down|downward|lower)", r"tailing off", r"drying up",
)
# Direction-free: asserts movement without saying which way. Allowed
# only when SOME unmasked field has a non-zero delta.
_NEUTRAL = (r"trending", r"trended", r"on the move", r"shifting", r"changing fast")

# Longest alternatives first so "climbing faster" / "trending up" win
# over their bare forms inside one scan.
_PATTERN = re.compile(
    r"\b(?:" + "|".join(sorted(_UP + _DOWN + _NEUTRAL, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)
_UP_RE = re.compile(r"^(?:" + "|".join(_UP) + r")$", re.IGNORECASE)
_DOWN_RE = re.compile(r"^(?:" + "|".join(_DOWN) + r")$", re.IGNORECASE)


def find_trend_claims(text) -> list:
    """[{phrase, direction}] for every trend verb/adjective in `text`,
    direction in {"up", "down", "neutral"}. [] for a non-string."""
    if not isinstance(text, str) or not text:
        return []
    found = []
    for m in _PATTERN.finditer(text):
        phrase = m.group(0)
        if _UP_RE.match(phrase):
            direction = "up"
        elif _DOWN_RE.match(phrase):
            direction = "down"
        else:
            direction = "neutral"
        found.append({"phrase": phrase, "direction": direction})
    return found


# --------------------------------------------------------------------
# Evidence: which trend fields a shelf is allowed to lean on, and the
# raw rolling columns each one's delta comes from. Same column names
# shelves._trend_candidate reads (raw_col_last3 - raw_col_season_avg,
# gated by the percentile-trend column's own masking). target_share_
# trend (WR/TE) is itself a delta with no mask column -- masked only
# when absent (same accepted trade-off shelves._target_share_trend_
# candidate already documents).
# --------------------------------------------------------------------
_RED_ZONE_FIELDS = {
    "touch_share_trend_pct": "rz_touch_share",
    "touch_volume_trend_pct": "rz_touches",
    "snap_share_trend_pct": "snap_share",
}
_ROLE_FIELDS = {
    "touch_share_trend_pct_role": "rz_touch_share",
    "snap_share_trend_pct_role": "snap_share",
}
_TARGET_SHARE_TREND = {"target_share_trend": None}

TREND_FIELDS_BY_SHELF = {
    "Red Zone Trends": dict(_RED_ZONE_FIELDS),
    "RB Trends": dict(_ROLE_FIELDS),
    "WR Trends": {**_ROLE_FIELDS, **_TARGET_SHARE_TREND},
    "TE Trends": {**_ROLE_FIELDS, **_TARGET_SHARE_TREND},
}
# Any other shelf (the three ATTD odds bands, Around the League
# divisions): a trend claim there still needs real trend evidence, so
# the union of every trend field is consulted.
_ALL_TREND_FIELDS = {**_RED_ZONE_FIELDS, **_ROLE_FIELDS, **_TARGET_SHARE_TREND}


def _num(v):
    try:
        if v is None or pd.isna(v):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def trend_evidence_for_row(row, shelf: str) -> dict:
    """
    Reads the row's real trend state for `shelf`. `row` is a dict or
    pandas Series (both support .get). Returns:
      {
        "shelf": shelf,
        "fields": {field: {"masked": bool, "delta": float|None, "sign": -1|0|1|None}},
        "has_unmasked": bool,
        "positive": [field, ...],   # unmasked, delta > 0
        "negative": [field, ...],   # unmasked, delta < 0
        "status": "masked" | "no_change" | "up" | "down" | "mixed",
      }
    delta sign comes from the raw rolling columns when both are present;
    when they are absent (a partial row) it falls back to the percentile
    read itself (above/below the neutral 50 means up/down), which is the
    same information one step downstream. A masked field never
    contributes a sign.
    """
    fields = TREND_FIELDS_BY_SHELF.get(shelf, _ALL_TREND_FIELDS)
    out = {}
    for field, raw in fields.items():
        if raw is None:  # target_share_trend: itself a delta, masked only when absent
            delta = _num(row.get(field))
            masked = delta is None
        else:
            masked = bool(is_masked_fallback(row, field))
            last, avg = _num(row.get(f"{raw}_last3")), _num(row.get(f"{raw}_season_avg"))
            if last is not None and avg is not None:
                delta = last - avg
            else:
                pct = _num(row.get(field))
                delta = None if pct is None else (pct - 50.0)
        sign = None
        if not masked and delta is not None:
            sign = 1 if delta > 0 else (-1 if delta < 0 else 0)
        out[field] = {"masked": masked, "delta": delta, "sign": sign}

    unmasked = [f for f, v in out.items() if not v["masked"]]
    positive = [f for f in unmasked if out[f]["sign"] == 1]
    negative = [f for f in unmasked if out[f]["sign"] == -1]
    if not unmasked:
        status = "masked"
    elif positive and negative:
        status = "mixed"
    elif positive:
        status = "up"
    elif negative:
        status = "down"
    else:
        status = "no_change"
    return {
        "shelf": shelf, "fields": out, "has_unmasked": bool(unmasked),
        "positive": positive, "negative": negative, "status": status,
    }


def validate_trend_claims(texts: dict, evidence: dict) -> list:
    """
    texts: {field_name: text_or_None} -- e.g. {"title": ..., "story": ...}.
    Returns [] when every trend word is backed by the evidence, else one
    issue per offending field:
      {"check": "trend_claim", "category": "factual", "field": ...,
       "phrases": [...], "reason": "masked" | "no_change" | "wrong_direction",
       "evidence_status": ...}
    """
    issues = []
    status = evidence["status"]
    for field, text in texts.items():
        claims = find_trend_claims(text)
        if not claims:
            continue
        bad, reason = [], None
        for c in claims:
            if status == "masked":
                bad.append(c["phrase"]); reason = reason or "masked"
            elif status == "no_change":
                bad.append(c["phrase"]); reason = reason or "no_change"
            elif c["direction"] == "up" and not evidence["positive"]:
                bad.append(c["phrase"]); reason = reason or "wrong_direction"
            elif c["direction"] == "down" and not evidence["negative"]:
                bad.append(c["phrase"]); reason = reason or "wrong_direction"
            # neutral with any non-zero delta (status up/down/mixed): allowed
        if bad:
            issues.append({
                "check": "trend_claim", "category": "factual", "field": field,
                "phrases": bad, "reason": reason, "evidence_status": status,
            })
    return issues


# --------------------------------------------------------------------
# Factual vs stylistic split for the EXISTING validators.
# --------------------------------------------------------------------
FACTUAL_CHECKS = frozenset({
    "schema_shape", "citation", "numeric_grounding", "star_consistency",
    "pillar_field_consistency", "trend_claim", "factual_gate",
})
STYLISTIC_CHECKS = frozenset({"banned_language", "field_narration"})
WARNING_CHECKS = frozenset({"story_length", "column_name", "stock_phrase"})


def categorize_issue(issue: dict) -> dict:
    """Returns the same dict with a "category" key: factual | stylistic |
    warning | unknown (an unrecognized check is treated as factual by
    the gate -- failing closed is the whole point of this module)."""
    check = issue.get("check")
    if check in FACTUAL_CHECKS:
        cat = "factual"
    elif check in STYLISTIC_CHECKS:
        cat = "stylistic"
    elif check in WARNING_CHECKS:
        cat = "warning"
    else:
        cat = "unknown"
    return {**issue, "category": issue.get("category", cat)}


def categorize_issues(issues) -> list:
    return [categorize_issue(i) for i in (issues or []) if isinstance(i, dict)]


def split_issues(issues) -> dict:
    """{"factual": [...], "stylistic": [...], "other": [...]} -- "other"
    (warning/unknown-tagged) is reported, never used to decide anything
    here; "unknown" checks are gated as factual by is_factual_failure."""
    tagged = categorize_issues(issues)
    return {
        "factual": [i for i in tagged if i["category"] in ("factual", "unknown")],
        "stylistic": [i for i in tagged if i["category"] == "stylistic"],
        "other": [i for i in tagged if i["category"] == "warning"],
    }


# --------------------------------------------------------------------
# Fallback copy. Says ONLY what the evidence supports and contains no
# trend word, so it passes validate_trend_claims by construction
# (asserted in test_factual_validation.py, not assumed).
# --------------------------------------------------------------------
FALLBACK_TITLE_MASKED = "The early role hasn't shown enough yet to call a direction."
FALLBACK_TITLE_NO_CHANGE = "The role is holding steady so far."
FALLBACK_TITLE_GENERIC = "One of the model's top-graded picks this week."


def safe_fallback_title(evidence: dict) -> str:
    """Last-resort title when a deterministic fallback itself fails the
    trend rule. masked -> "cannot say"; no_change -> "no change"; a row
    with a real non-zero delta that still failed (wrong direction) gets
    the generic level claim rather than a guessed direction."""
    if evidence["status"] == "masked":
        return FALLBACK_TITLE_MASKED
    if evidence["status"] == "no_change":
        return FALLBACK_TITLE_NO_CHANGE
    return FALLBACK_TITLE_GENERIC


# --------------------------------------------------------------------
# Prompt-side prevention (used by nfl_shelf_card_prompt).
# --------------------------------------------------------------------
TREND_UNAVAILABLE_PROMPT_LINE = (
    "Trend data for this player is not available yet. Do not describe his role, touches, or chances as "
    "climbing, rising, growing, trending, or outrunning anything. Describe the current level only."
)


def strip_trend_phrases(phrases) -> tuple:
    """Drops every voice-pool phrase that contains a trend word."""
    return tuple(p for p in phrases if not find_trend_claims(p))
