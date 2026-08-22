"""
COPIED from pipeline/api/content_writer/voice/principles.py, not
cross-imported — same Vercel Root Directory constraint, see card_
writer_common.py's header in this same nfl/content_writer/ directory.

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

# (low_inclusive, high_exclusive, band) — CONTINUOUS, not integer-gapped.
# Real finding (2026-08-04): final_score is a float, and the original
# integer-edged bounds (25-39, 40-59, 60-74, 75-90) left real gaps at
# every boundary — a genuine 39.6 fell between quiet_signal's 39 and
# developing_angle's 40 and got no band at all, discovered via a real
# candidate (Andres Gimenez, Cold Pitchers to Attack) rather than a
# hypothetical. Every band here is now [low, high) — half-open, so a
# boundary value like 39.9 or 59.9 or 74.9 always resolves to exactly one
# band, with no gap and no double-coverage. The top band is the one
# exception: it stays closed at 90 (upper-inclusive), matching the
# original "scores above 90 need deliberate handling" cutoff exactly —
# below is still the real intended extreme-score case, not a new gap.
#
# final_score outside 25-90 still deliberately returns no band (see
# confidence_band_for_score below) — that part of the original design is
# unchanged. A real score above 90 or below 25 is a genuine outlier this
# pipeline hasn't seen in practice; forcing it into the nearest band would
# quietly justify overconfident (or overly muted) copy at an extreme this
# voice hasn't actually been designed for yet, so it's surfaced as "no
# band" for deliberate human handling rather than escalating the copy's
# drama indefinitely as scores climb, or apologizing indefinitely as they
# fall.
CONFIDENCE_BAND_THRESHOLDS = (
    (25, 40, "quiet_signal"),
    (40, 60, "developing_angle"),
    (60, 75, "strong_setup"),
    (75, 90, "premium_signal"),
)


def confidence_band_for_score(final_score: float) -> Optional[str]:
    """None for scores below 25 or above 90 — those need deliberate
    handling (see the reference doc's "scores outside normal bands"
    guidance), not a silent nearest-band fallback that could quietly
    justify overconfident copy at the extremes. Every score from 25 up to
    and including 90 resolves to exactly one band — see
    CONFIDENCE_BAND_THRESHOLDS above for why the top band alone keeps an
    inclusive upper bound."""
    last_index = len(CONFIDENCE_BAND_THRESHOLDS) - 1
    for i, (lo, hi, band) in enumerate(CONFIDENCE_BAND_THRESHOLDS):
        if i == last_index:
            if lo <= final_score <= hi:
                return band
        elif lo <= final_score < hi:
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
