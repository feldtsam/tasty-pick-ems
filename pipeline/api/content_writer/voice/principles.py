"""
Voice Engine, Layer 1 — principles.py.

SCOPE OF THIS FILE, RIGHT NOW: only the shelf-voice / confidence-band
composability rule below has actually been decided and reviewed. The rest
of the reference doc's Section 5 voice principles (story > stat,
anticipation not recap, one memorable phrase, receipts not verdicts,
etc.) are real project decisions but haven't been codified as structured
config yet — that's real, separate Phase 2 work, not yet authorized. This
file's current narrowness reflects what's actually been decided, not what
still needs deciding.
"""
from typing import Optional

SHELF_NAMES = (
    "Hot Hitters",
    "Cold Pitchers to Attack",
    "Weather Factors",
    "+300-499",
    "+500-699",
    "Going Nuclear",
)

CONFIDENCE_BANDS = (
    "quiet_signal",       # final_score 25-39
    "developing_angle",   # final_score 40-59
    "strong_setup",       # final_score 60-74
    "premium_signal",     # final_score 75-90
)

# (low, high, band) — final_score outside 25-90 deliberately returns no
# band (see confidence_band_for_score below), rather than being forced
# into the nearest one.
CONFIDENCE_BAND_THRESHOLDS = (
    (25, 39, "quiet_signal"),
    (40, 59, "developing_angle"),
    (60, 74, "strong_setup"),
    (75, 90, "premium_signal"),
)


def confidence_band_for_score(final_score: float) -> Optional[str]:
    """None for scores below 25 or above 90 — those need deliberate
    handling (see the reference doc's "scores outside normal bands"
    guidance), not a silent nearest-band fallback that could quietly
    justify overconfident copy at the extremes."""
    for lo, hi, band in CONFIDENCE_BAND_THRESHOLDS:
        if lo <= final_score <= hi:
            return band
    return None


# ---------------------------------------------------------------------------
# PRINCIPLE: shelf voice and confidence band are two independent,
# composable axes — never one collapsed setting.
# ---------------------------------------------------------------------------
#
# Real case this corrects, caught while drafting gold-standard examples:
# Riley Greene (DET, 2026-07-28) had final_score=72.9 — Strong Setup by any
# real measure — while sitting at +900 odds, squarely in the "Going
# Nuclear" shelf. Writing him with muted, hedgy language just because
# "Going Nuclear" sounds like a long-shot-only shelf would misrepresent
# what the data actually says about him. Writing him with maximum
# Going-Nuclear drama-language intensity because the shelf name is
# dramatic would misrepresent nothing about the data, but conflating "this
# shelf is dramatic" with "this pick is highly confident" is still the
# wrong mental model — the two facts (shelf, confidence) are independent
# and happened to both be true here for unrelated reasons.
#
#   SHELF            -> which vocabulary pool / imagery flavor the copy
#                        draws from (e.g. Going Nuclear pulls from
#                        volatile/boom-or-bust imagery; Weather Factors
#                        pulls from atmospheric/meteorologist imagery).
#                        Set by shelf_assignments.shelf. See
#                        shelf_personalities.py (Phase 2, not yet built).
#
#   CONFIDENCE BAND   -> how ASSERTIVE the copy is allowed to be about the
#                        underlying data itself — declarative vs.
#                        observational, title intensity, how many dramatic
#                        words. Set by final_score via
#                        confidence_band_for_score() above — never by
#                        shelf, never by odds. See emotional_intensity.py
#                        (Phase 2, not yet built).
#
# A writer builds a card by looking up BOTH independently and composing
# them — never by picking one setting off what looks like a single dial.


# ---------------------------------------------------------------------------
# PRINCIPLE: a long-shot price is never described as safer than it is,
# regardless of confidence band.
# ---------------------------------------------------------------------------
#
# A Strong Setup or Premium Signal card at Going Nuclear odds is allowed to
# sound confident ABOUT THE DATA. It is never allowed to imply the BET
# itself is safe, likely, or a "lock" — that would misrepresent what a
# +700 price means no matter how good the underlying skill/matchup numbers
# are. High data confidence and a genuinely long bet-outcome price are not
# in tension — a card can and should hold both truthfully at once
# ("everything about this matchup says he's live, and the price still
# reflects a real long shot"). This holds at every confidence band, not
# just the low ones — a Premium Signal long shot is still a long shot.
#
# This is a validation-time constraint, not only a writing-time one:
# regardless of confidence band, copy for a candidate at Going Nuclear
# odds must be checked against the same certainty-language banned list
# (see banned_language.py, Phase 2, not yet built) as every other shelf —
# high confidence is never a license to loosen that check.
NEVER_IMPLIES_LOW_ODDS_ARE_SAFE = True  # hard constraint, not a style preference — the source of truth for why this gets enforced in validation (Phase 6)
