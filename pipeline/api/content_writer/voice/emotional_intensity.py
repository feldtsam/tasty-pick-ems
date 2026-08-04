"""
Voice Engine, Layer 1 — emotional_intensity.py.

NEW, UNREVIEWED DESIGN WORK (2026-08-03) — same status as shelf_
personalities.py: referenced by principles.py as "Phase 2, not yet
built," written now for Phase 5's first vertical slice, grounded in the
blueprint's real material (§0's honest-voice positioning, §6's "no
minimum confidence threshold... confidence meter must read honestly low
rather than being dressed up") rather than a recovery of the original
master prompt's own numbered sections, which weren't preserved as a file.

WHAT THIS FILE IS FOR, PER principles.py'S RULE: confidence band sets how
ASSERTIVE the copy is allowed to be about what the underlying data shows
— declarative vs. observational, how bold the title can be. It is set
exclusively by final_score via confidence_band_for_score() — never by
shelf, never by odds. See principles.py's NEVER_IMPLIES_LOW_ODDS_ARE_SAFE:
even the most assertive band never implies the BET is safe, only that the
DATA is clear. That distinction is enforced at validation time
(banned_language.py) uniformly across every band — this file only
controls how confidently the data itself gets described.

quiet_signal is deliberately NOT written as an apology or a shrug. The
blueprint is explicit that a thin day still gets shown, honestly, not
dressed up — "always show something, never inflate it." The honest
version of a quiet_signal card is a real, measured observation, not a
throwaway.
"""
from typing import NamedTuple


class IntensityProfile(NamedTuple):
    assertiveness: str        # how confidently the copy may state what the data shows
    title_register: str        # how bold/declarative the cinematic title is allowed to be
    example_opening_frames: tuple  # illustrative sentence STARTS showing the register, not full lines to copy verbatim


EMOTIONAL_INTENSITY = {
    "quiet_signal": IntensityProfile(
        assertiveness=(
            "Observational, not declarative. Present the data as a real, honest signal worth "
            "noting — never inflate a modest case into something it isn't, but also never "
            "write it as an apology or a hedge-everything shrug. 'Here's something genuinely "
            "worth a look' is the right register, not 'this probably won't work.'"
        ),
        title_register="Measured. No superlatives, no exclamation-point energy.",
        example_opening_frames=(
            "There's a quiet case for...",
            "Worth a second look:",
            "Not the loudest signal on the slate, but...",
        ),
    ),
    "developing_angle": IntensityProfile(
        assertiveness=(
            "Building conviction, still honestly qualified. The case is taking real shape — "
            "write like something is genuinely forming, not fully arrived."
        ),
        title_register="A little more shape and motion than quiet_signal, still short of a bold claim.",
        example_opening_frames=(
            "The case is building for...",
            "Something's taking shape here:",
            "This one's starting to click:",
        ),
    ),
    "strong_setup": IntensityProfile(
        assertiveness=(
            "Confident and declarative about what the data shows. This is where the copy can "
            "sound genuinely sure of the ANALYSIS — real conviction language is earned here."
        ),
        title_register="Bold, declarative — this is a real headline, not a hedge.",
        example_opening_frames=(
            "Everything here points the same direction:",
            "This is as clean a setup as the slate offers:",
            "The numbers aren't subtle about this one:",
        ),
    ),
    "premium_signal": IntensityProfile(
        assertiveness=(
            "Maximum legitimate conviction about the data — write with full confidence in the "
            "analysis. Still governed by the same hard rule as every other band: sounding sure "
            "about the numbers is never the same as promising the outcome. A premium_signal "
            "card at Going Nuclear odds is still, honestly, a long shot — see principles.py."
        ),
        title_register="The most declarative register available — but never crosses into "
                        "outcome-guarantee language (that boundary is fixed, not scaled by band).",
        example_opening_frames=(
            "Every pillar says the same thing tonight:",
            "This is the clearest signal on the board:",
            "If there's one case to trust on this slate, it's this:",
        ),
    ),
}


def intensity_for_band(band: str) -> IntensityProfile:
    """Raises KeyError for an unrecognized band — same fail-loud reasoning
    as shelf_personalities.personality_for_shelf(): a typo'd band name
    should never silently fall back to a default voice."""
    return EMOTIONAL_INTENSITY[band]
