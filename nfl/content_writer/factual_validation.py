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
    r"surg(?:es|ed|ing)", r"expand(?:s|ed|ing)", r"outrun(?:s|ning)", r"outpac(?:e|es|ed|ing)",
    r"trending (?:up|upward|higher)", r"trend(?:s|ed) (?:up|upward|higher)", r"ramping(?: up)?",
    r"heating up", r"heated up", r"ticking up", r"picking up", r"taking off", r"mounting", r"swelling", r"ballooning",
    r"soaring", r"spiking", r"accelerating", r"escalating", r"climbing faster",
    # Narrow extension (2026-10-09): the heat / direction words the vocab
    # sample reached for once "climbing" was gated ("Goal-Line Work Reads
    # Hot", "Chances Run Hot"). "hot", "heated", "building" and the whole
    # outpace family are SUBJECT-GATED below (see _SUBJECT_GATED): they
    # only count as a trend claim when the clause is about the player's
    # own role / usage / touches / share / chances.
    r"hot", r"heated", r"building", r"on fire",
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


# --------------------------------------------------------------------
# Clause-level classification (Item 3 refinement, 2026-10-09). A trend
# word is only a CLAIM when its own clause asserts it. The first dry run
# found every story-level flag sitting inside a sentence that declined
# to make the claim ("too early to say his usage is climbing, fading, or
# holding steady") -- legitimate uncertainty the prompt itself asks for.
# Classification is per CLAUSE, never per sentence, so a disclaimer in
# one clause cannot excuse an affirmative claim in the next ("isn't
# clear yet, but his touches are climbing" -> the second clause fails).
#
#   negated   -- the clause declines to make the claim: a negation or
#                "cannot say" cue BEFORE the hit in the same clause, a
#                "remains unclear" cue AFTER it, or an enumeration of
#                opposite directions ("rising, falling, or holding
#                steady"). Passes everywhere.
#   ambiguous -- hedged ("may be climbing", "looks like it's rising") or
#                a question. Fails in a title, warns in a story.
#   affirmative -- everything else. Fails wherever the evidence does not
#                back it.
# --------------------------------------------------------------------
# Dashes are deliberately NOT clause breaks: a dash usually attaches a
# qualifier to the claim it follows ("how his role has been trending --
# that read isn't established"), and splitting there would strand the
# qualifier. Contrasting conjunctions ("but", "however", ...) are.
_CLAUSE_SPLIT = re.compile(
    r"(?<=[.!?])\s+|;\s*|\s*\(|\)\s*"
    r"|\s+(?:but|however|while|whereas|although|though|except)\s+",
    re.IGNORECASE,
)
# Cues that must come BEFORE the hit inside the clause.
_NEGATION_BEFORE = re.compile(
    r"\b(?:not|no|never|neither|nor|without|nothing|n't|cannot|can't|too early|too soon|hard to say|unclear|"
    r"isn't clear|is not clear|not clear|no way to|whether|unknown|unsure|uncertain|no reliable read|not enough|"
    r"isn't enough|hasn't|haven't|hadn't|doesn't|don't|didn't|isn't|aren't|wasn't|weren't|won't|wouldn't|"
    r"impossible to|remains to be seen|yet to|has yet|question (?:is|of)|rather than|instead of)\b",
    re.IGNORECASE,
)
# Cues that may come AFTER the hit and still mean "declines to say":
# only the explicitly declining ones, so "...are climbing, which isn't
# surprising" is NOT excused.
_DECLINE_AFTER = re.compile(
    r"\b(?:isn't clear|is not clear|not clear|unclear|isn't established|is not established|not established|"
    r"isn't known|unknown|remains to be seen|isn't certain|uncertain|hasn't firmed|hasn't settled|hasn't shown|"
    r"is (?:still )?fuzzy|still fuzzy|too early to (?:say|call|tell|read)|can't (?:say|call|tell|read)|"
    r"cannot (?:say|call|tell|read)|no reliable read|isn't reliable|not reliable|yet to (?:show|settle|firm))\b",
    re.IGNORECASE,
)
_HEDGE = re.compile(r"\b(?:may|might|could|possibly|perhaps|maybe|seems?|appears?|looks? like|likely|if)\b", re.IGNORECASE)

# --------------------------------------------------------------------
# Subject gating (narrow lexicon extension, 2026-10-09). The nine words
# the extension adds or re-examines -- hot, heating up, heated, outpace,
# outpacing, ramping, building, surging, on fire -- can each describe
# something other than a player's trend over time: a matchup or defense
# ("a hot matchup", "a defense that's heating up"), a noun or unrelated
# sense ("building a case", "a heated rivalry"), or a comparison of two
# level facts ("outpaces the league average of 1.4 per game"). The rule
# must reject unsupported TREND claims, not every occurrence of a word.
#
# Decision per hit, after the clause-level negation checks above:
#   usage subject   -- the nearest listed noun before the hit (or the
#                      noun the word modifies directly after it) is the
#                      player's role / usage / touches / share / chances:
#                      a trend claim, classified exactly as every other
#                      lexicon word.
#   other subject   -- that noun is a matchup, defense, price, rivalry,
#                      case, ...: a different claim, never a trend claim
#                      here (kind "not_trend", passes this rule).
#   unknown subject -- no listed noun either side: FAIL CLOSED on a row
#                      that cannot back a trend (kind "ambiguous_subject",
#                      hard in title AND story), surfaced as its own kind
#                      so the cost of failing closed stays visible.
# The outpace family has one more pass: a clause that names a comparison
# baseline AND states a figure is a level comparison, not a trend.
# Everything outside _SUBJECT_GATED behaves exactly as before.
# --------------------------------------------------------------------
_SUBJECT_GATED = re.compile(
    r"^(?:hot|heating up|heated up|heated|outpac(?:e|es|ed|ing)|ramping(?: up)?|building|surg(?:es|ed|ing)|on fire)$",
    re.IGNORECASE,
)
_OUTPACE = re.compile(r"^outpac", re.IGNORECASE)
_USAGE_NOUN = re.compile(
    r"\b(?:role|usage|touch(?:es)?|share|chances?|looks?|work|reps?|targets?|carries|snaps?|opportunit(?:y|ies)|"
    r"involvement|volume|workload|production|numbers|hand|streak|start|form|output|scoring|"
    r"red[- ]zone|goal[- ]line|inside[- ](?:the[- ])?(?:10|ten|20|twenty))\b",
    re.IGNORECASE,
)
_OTHER_NOUN = re.compile(
    r"\b(?:matchups?|defenses?|secondary|front|unit|opponents?|team|offense|rivalry|case|block|debate|argument|"
    r"exchange|battle|moment|market|price|line|odds|books?|weather|game|crowd|pace|slate|shelf|week|schedule)\b",
    re.IGNORECASE,
)
# "building a case" / "building toward" / "the building": the verb-with-
# object or noun sense, never a trend.
_BUILDING_NON_TREND = re.compile(r"^\s*(?:a|an|the|toward|towards|on|out|up a|up the|block)\b", re.IGNORECASE)
_COMPARISON_BASELINE = re.compile(r"\b(?:league|average|avg|baseline|median|peers?|the field|benchmark)\b", re.IGNORECASE)
_SKIP_AFTER = re.compile(r"^\s*(?:a|an|the|his|her|their|its|this|that|up|very|really|still|now|right now)\b\s*", re.IGNORECASE)

# Shelf names that contain a lexicon word. Blanked (same length, so hit
# spans stay aligned) before scanning: "Red Zone Rising" is the display
# name of the Red Zone Trends shelf and must never trip "rising".
_SHELF_NAME_PHRASES = ("Red Zone Rising", "Red Zone Trends", "RB Trends", "WR Trends", "TE Trends", "Hot Hitters", "Heat Check")
_SHELF_NAME_RE = re.compile("|".join(re.escape(p) for p in _SHELF_NAME_PHRASES), re.IGNORECASE)


def _blank_shelf_names(text: str) -> str:
    return _SHELF_NAME_RE.sub(lambda m: " " * len(m.group(0)), text)


def _subject_of_hit(clause: str, hit_start: int, hit_end: int, phrase: str) -> str:
    """'usage' | 'other' | 'unknown' | 'comparison' for a subject-gated hit."""
    before, after = clause[:hit_start], clause[hit_end:]
    if _OUTPACE.match(phrase) and _COMPARISON_BASELINE.search(after) and re.search(r"\d", clause):
        return "comparison"
    if phrase.lower() == "building":
        if _BUILDING_NON_TREND.match(after) or re.search(r"\b(?:a|an|the)\s*$", before, re.IGNORECASE):
            return "other"
    # The noun the word modifies directly ("a hot matchup", "hot hand").
    tail = _SKIP_AFTER.sub("", after, count=1)
    head = re.match(r"\s*([A-Za-z][A-Za-z'\-]*(?:\s+[A-Za-z][A-Za-z'\-]*)?)", tail)
    if head:
        if _OTHER_NOUN.match(head.group(1).split()[0]):
            return "other"
        if _USAGE_NOUN.search(head.group(1)):
            return "usage"
    # Otherwise the nearest listed noun before the hit (subject position).
    nearest = None
    for kind, rx in (("usage", _USAGE_NOUN), ("other", _OTHER_NOUN)):
        for m in rx.finditer(before):
            if nearest is None or m.start() > nearest[1]:
                nearest = (kind, m.start())
    return nearest[0] if nearest else "unknown"


def _clauses(text: str) -> list:
    """[(start, end, clause_text)] -- clause spans of `text`."""
    out, pos = [], 0
    for m in _CLAUSE_SPLIT.finditer(text):
        if m.start() > pos:
            out.append((pos, m.start(), text[pos:m.start()]))
        pos = m.end()
    if pos < len(text):
        out.append((pos, len(text), text[pos:]))
    return out


def _classify_hit(clause: str, hit_start: int, hit_end: int, hits_in_clause: list) -> str:
    before, after = clause[:hit_start], clause[hit_end:]
    if _NEGATION_BEFORE.search(before):
        return "negated"
    if _DECLINE_AFTER.search(after):
        return "negated"
    directions = {h["direction"] for h in hits_in_clause}
    if "up" in directions and "down" in directions and re.search(r"\bor\b", clause, re.IGNORECASE):
        return "negated"  # "rising, falling, or holding steady": lists possibilities, asserts none
    if _HEDGE.search(before) or clause.strip().endswith("?"):
        return "ambiguous"
    return "affirmative"


def find_trend_claims(text) -> list:
    """[{phrase, direction, kind, clause}] for every trend verb/adjective
    in `text`: direction in {"up", "down", "neutral"}, kind in
    {"affirmative", "negated", "ambiguous"} (see the block comment
    above). [] for a non-string."""
    if not isinstance(text, str) or not text:
        return []
    found = []
    for c_start, c_end, clause in _clauses(_blank_shelf_names(text)):
        hits = []
        for m in _PATTERN.finditer(clause):
            phrase = m.group(0)
            if _UP_RE.match(phrase):
                direction = "up"
            elif _DOWN_RE.match(phrase):
                direction = "down"
            else:
                direction = "neutral"
            hits.append({"phrase": phrase, "direction": direction, "_span": (m.start(), m.end())})
        for h in hits:
            kind = _classify_hit(clause, h["_span"][0], h["_span"][1], hits)
            subject = None
            if _SUBJECT_GATED.match(h["phrase"]):
                subject = _subject_of_hit(clause, h["_span"][0], h["_span"][1], h["phrase"])
                if kind != "negated":
                    if subject in ("other", "comparison"):
                        kind = "not_trend"
                    elif subject == "unknown":
                        kind = "ambiguous_subject"
            entry = {"phrase": h["phrase"], "direction": h["direction"], "kind": kind, "clause": clause.strip()}
            if subject is not None:
                entry["subject"] = subject
            found.append(entry)
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


def _unsupported_reason(claim: dict, evidence: dict):
    """Why this claim is unsupported by the evidence, or None if it is backed."""
    status = evidence["status"]
    if status == "masked":
        return "masked"
    if status == "no_change":
        return "no_change"
    if claim["direction"] == "up" and not evidence["positive"]:
        return "wrong_direction"
    if claim["direction"] == "down" and not evidence["negative"]:
        return "wrong_direction"
    return None  # neutral with any non-zero delta, or a backed direction


def _trend_findings(texts: dict, evidence: dict) -> tuple:
    """(hard_issues, warnings) per the title/story policy:
       title: affirmative or ambiguous unsupported -> hard; negated -> pass.
       story / editorial_sentence: affirmative unsupported -> hard;
       negated -> pass; ambiguous unsupported -> warning only."""
    hard, warn = [], []
    for field, text in texts.items():
        bad, ambiguous, reason, ok, unknown_subject = [], [], None, [], []
        for c in find_trend_claims(text):
            why = _unsupported_reason(c, evidence)
            if why is None or c["kind"] in ("negated", "not_trend"):
                ok.append(c["phrase"]); continue
            if c["kind"] == "ambiguous" and field != "title":
                ambiguous.append(c["phrase"]); reason = reason or why; continue
            if c["kind"] == "ambiguous_subject":
                # Subject-gated word with no readable subject: fail closed
                # in every field, and say so (see _subject_of_hit).
                unknown_subject.append(c["phrase"])
            bad.append(c["phrase"]); reason = reason or why
        if bad:
            issue = {"check": "trend_claim", "category": "factual", "field": field, "phrases": bad,
                     "kind": "affirmative" if field != "title" else "affirmative_or_ambiguous",
                     "reason": reason, "evidence_status": evidence["status"]}
            if unknown_subject:
                issue["failed_closed_on_unknown_subject"] = unknown_subject
            hard.append(issue)
        if ambiguous:
            warn.append({"check": "trend_claim_ambiguous", "category": "warning", "field": field, "phrases": ambiguous,
                         "reason": reason, "evidence_status": evidence["status"]})
    return hard, warn


def validate_trend_claims(texts: dict, evidence: dict) -> list:
    """
    texts: {field_name: text_or_None} -- e.g. {"title": ..., "story": ...}.
    Returns the HARD issues only (see _trend_findings for the policy):
      {"check": "trend_claim", "category": "factual", "field": ...,
       "phrases": [...], "kind": ..., "reason": "masked" | "no_change" |
       "wrong_direction", "evidence_status": ...}
    A clause that declines to make the claim ("too early to say his usage
    is climbing") never appears here. Ambiguous story hits are warnings:
    see trend_claim_warnings().
    """
    return _trend_findings(texts, evidence)[0]


def trend_claim_warnings(texts: dict, evidence: dict) -> list:
    """Warn-only findings: hedged ("may be climbing") trend language in a
    story/editorial_sentence on a row that cannot back it."""
    return _trend_findings(texts, evidence)[1]


# --------------------------------------------------------------------
# Factual vs stylistic split for the EXISTING validators.
# --------------------------------------------------------------------
FACTUAL_CHECKS = frozenset({
    "schema_shape", "citation", "numeric_grounding", "star_consistency",
    "pillar_field_consistency", "trend_claim", "factual_gate", "receipts_missing",
})
STYLISTIC_CHECKS = frozenset({"banned_language", "field_narration", "receipts_below_minimum"})
WARNING_CHECKS = frozenset({"story_length", "column_name", "stock_phrase", "trend_claim_ambiguous", "receipts_trimmed"})

# ====================================================================
# TEMPORARY -- numeric_grounding warn-only window. REVIEW REQUIRED
# BEFORE EXTENDING. Remove this block (and the two call sites that read
# it: categorize_issue below and annotate_numeric_grounding) to make
# numeric_grounding a plain hard gate again.
#
# Why (dry run, 2026-10-09): numeric_grounding could not be replayed
# faithfully over stored cards (source facts had drifted), and its only
# surviving hits were unconfirmed. Until this date a numeric_grounding
# issue still flags the card (validation_passed=False, overridable in
# review) and is recorded in the STORED validation_issues with the claim
# text, the evidence reference, and would_have_gated=True -- it is never
# treated as supported. After this date the gate is hard automatically.
# ====================================================================
from datetime import date as _date  # noqa: E402

NUMERIC_GROUNDING_WARN_ONLY_UNTIL = _date(2026, 10, 16)
NUMERIC_GROUNDING_WARN_ONLY_REASON = (
    "numeric_grounding could not be replayed faithfully in the 2026-10-09 dry run; overridable until "
    f"{NUMERIC_GROUNDING_WARN_ONLY_UNTIL.isoformat()}, hard gate after. Review required before extending."
)


def numeric_grounding_is_hard_gate(today=None) -> bool:
    """True once the warn-only window has expired (strictly after the date)."""
    return (today or _date.today()) > NUMERIC_GROUNDING_WARN_ONLY_UNTIL


def categorize_issue(issue: dict, today=None) -> dict:
    """Returns the same dict with a "category" key: factual | stylistic |
    warning | deferred | unknown. "deferred" is numeric_grounding inside
    its warn-only window (see NUMERIC_GROUNDING_WARN_ONLY_UNTIL): it
    still flags the card and is stored, but the gate does not act on it.
    An unrecognized check is treated as factual by the gate -- failing
    closed is the whole point of this module."""
    check = issue.get("check")
    if check == "numeric_grounding" and not numeric_grounding_is_hard_gate(today):
        cat = "deferred"
    elif check in FACTUAL_CHECKS:
        cat = "factual"
    elif check in STYLISTIC_CHECKS:
        cat = "stylistic"
    elif check in WARNING_CHECKS:
        cat = "warning"
    else:
        cat = "unknown"
    out = {**issue, "category": issue.get("category", cat)}
    if check == "numeric_grounding" and out["category"] == "deferred":
        out.setdefault("would_have_gated", True)
        out.setdefault("warn_only_until", NUMERIC_GROUNDING_WARN_ONLY_UNTIL.isoformat())
    return out


_NUMBER_IN_ISSUE = re.compile(r"number ([\-\d.]+) in reason_text")


def annotate_numeric_grounding(issues: list, why_reasons: list) -> list:
    """Adds claim_text and evidence_reference to every numeric_grounding
    issue so the STORED validation_issues payload carries what was
    claimed and what it was checked against -- required during the
    warn-only window, harmless after it."""
    out = []
    for i in issues:
        if i.get("check") == "numeric_grounding":
            idx = i.get("reason_index")
            reason = why_reasons[idx] if isinstance(idx, int) and isinstance(why_reasons, list) and idx < len(why_reasons) and isinstance(why_reasons[idx], dict) else {}
            m = _NUMBER_IN_ISSUE.search(i.get("issue") or "")
            i = {**i, "claim_text": reason.get("reason_text"),
                 "evidence_reference": {"cited_keys": list(reason.get("source_fact_keys") or []), "number": m.group(1) if m else None}}
        out.append(i)
    return out


def categorize_issues(issues) -> list:
    return [categorize_issue(i) for i in (issues or []) if isinstance(i, dict)]


def split_issues(issues) -> dict:
    """{"factual": [...], "stylistic": [...], "deferred": [...], "other": [...]}.
    "factual" (incl. unknown checks -- fail closed) is what the gate acts
    on; "deferred" is numeric_grounding inside its warn-only window
    (flags the card, stored, not gated); "other" is warnings."""
    tagged = categorize_issues(issues)
    return {
        "factual": [i for i in tagged if i["category"] in ("factual", "unknown")],
        "stylistic": [i for i in tagged if i["category"] == "stylistic"],
        "deferred": [i for i in tagged if i["category"] == "deferred"],
        "other": [i for i in tagged if i["category"] == "warning"],
    }


# --------------------------------------------------------------------
# Receipts (why_reasons) -- Item 3 refinement. The writer contract is
# 2-3 receipts. Counts outside it are handled by rule, never by
# replacing the whole card for a formatting miss:
#   > 3 : keep the first three in the WRITER'S OWN ORDER (there is no
#         existing ranking of reasons to reuse; the writer lists its
#         strongest first per the prompt's structure) and warn.
#   = 3 : no change.   1-2 : keep them; 1 is below the contract minimum
#         and flags for review (stylistic); never invent more.
#   = 0 : factual failure (an evidence-free card) -- the gate swaps in
#         the row's own deterministic receipts if they exist, otherwise
#         withholds the row. See curate_home_shelves._enforce_factual_gate.
# --------------------------------------------------------------------
MAX_RECEIPTS = 3
MIN_RECEIPTS = 2
_SCHEMA_COUNT_ERROR_PREFIX = "why_reasons must have"


def normalize_receipts(output: dict) -> tuple:
    """(output, warnings): trims a > MAX_RECEIPTS why_reasons list to its
    first MAX_RECEIPTS entries (writer order) and returns a
    receipts_trimmed warning; anything else passes through untouched."""
    reasons = output.get("why_reasons") if isinstance(output, dict) else None
    if isinstance(reasons, list) and len(reasons) > MAX_RECEIPTS:
        trimmed = dict(output)
        trimmed["why_reasons"] = reasons[:MAX_RECEIPTS]
        return trimmed, [{"check": "receipts_trimmed", "category": "warning", "kept": MAX_RECEIPTS,
                          "dropped": len(reasons) - MAX_RECEIPTS, "rule": "writer order, first three kept"}]
    return output, []


def receipt_count_issues(output: dict) -> list:
    """The count-based issues that replace the schema's own
    'why_reasons must have 2-3 items' error: 0 -> receipts_missing
    (factual), 1 -> receipts_below_minimum (stylistic)."""
    reasons = output.get("why_reasons") if isinstance(output, dict) else None
    if not isinstance(reasons, list):
        return []
    n = len([r for r in reasons if isinstance(r, dict)])
    if n == 0:
        return [{"check": "receipts_missing", "category": "factual", "count": 0, "issue": "no why_reasons at all -- an evidence-free card"}]
    if n < MIN_RECEIPTS:
        return [{"check": "receipts_below_minimum", "category": "stylistic", "count": n, "issue": f"{n} why_reason(s), contract minimum is {MIN_RECEIPTS}"}]
    return []


def is_schema_count_error(issue: dict) -> bool:
    return issue.get("check") == "schema_shape" and str(issue.get("issue") or "").startswith(_SCHEMA_COUNT_ERROR_PREFIX)


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
