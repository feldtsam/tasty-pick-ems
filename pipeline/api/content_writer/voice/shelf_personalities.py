"""
Voice Engine, Layer 1 — shelf_personalities.py.

NEW, UNREVIEWED DESIGN WORK (2026-08-03) — not previously approved content.
Referenced by principles.py as "Phase 2, not yet built"; this is that file,
written now as part of Phase 5's first vertical slice. Grounded in what's
actually documented — the product blueprint's §6 (AI Systems Architecture)
and the shelf descriptions already shipped in the real app's sitemap/mock
content — NOT the original AI Content Writer master prompt's own Section
5, which was pasted directly into an earlier conversation turn and never
saved to a file; that verbatim text did not survive context compaction.
Treat this file's content as freshly synthesized from primary sources,
not a recovery of that original spec.

WHAT THIS FILE IS FOR, PER principles.py'S OWN RULE: shelf sets the
VOCABULARY POOL / IMAGERY FLAVOR only — which words and images a card
draws from. It never sets how confident or assertive the copy is allowed
to sound; that's emotional_intensity.py's job, driven by final_score via
confidence_band_for_score(), never by shelf and never by odds. A "Going
Nuclear" card and a "Weather Factors" card at the exact same confidence
band should sound EQUALLY confident about the underlying data — they
should just reach for different words to say it. Mixing these two axes
is the exact mistake principles.py's Riley Greene example exists to
prevent.

Each shelf's `imagery_pool` is a flavor guide for the model's prompt, not
a fixed set of phrases to insert verbatim — the writer still has to write
a genuine sentence about this specific real candidate, not mad-lib a
template. `avoid` calls out the one or two nearby-shelf traps most likely
to bleed in (e.g. writing a "Weather Factors" card with Going-Nuclear
boom-or-bust drama just because the price happens to be long).
"""
from typing import NamedTuple


class ShelfPersonality(NamedTuple):
    description: str          # one-line editorial framing (real, ships in-app)
    subject_is_batter: bool    # every HR-prop shelf is about the batter's own card, even when the story angle is about the opposing pitcher
    imagery_pool: tuple        # words/images the prompt may draw from — flavor, not a fill-in-the-blank template
    avoid: tuple                # the specific nearby-shelf drift this shelf is most prone to


SHELF_PERSONALITIES = {
    "Hot Hitters": ShelfPersonality(
        description="Bats on fire right now.",
        subject_is_batter=True,
        imagery_pool=(
            "heat", "streak", "can't cool off", "seeing it well", "barreling everything",
            "locked in", "timing is there", "swing is humming", "on a run",
        ),
        avoid=(
            "Don't borrow Going Nuclear's boom-or-bust chaos imagery just because a hot "
            "streak feels exciting — this shelf's excitement is about sustained recent form, "
            "not variance.",
        ),
    ),
    "Cold Pitchers to Attack": ShelfPersonality(
        description="Arms leaking home runs.",
        subject_is_batter=True,
        imagery_pool=(
            "leaking", "vulnerable", "cracks showing", "exposed", "there for the taking",
            "struggling to miss bats", "damage waiting to happen", "on the ropes",
        ),
        avoid=(
            "The card is still about the BATTER's home-run prop, not a pitcher profile — the "
            "opposing pitcher's struggles are the reason, not the subject. Don't write a card "
            "that reads as being about the pitcher.",
        ),
    ),
    "Weather Factors": ShelfPersonality(
        description="HR environments unlocked.",
        subject_is_batter=True,
        imagery_pool=(
            "carry", "thin air", "wind at his back", "launch conditions", "the ball travels tonight",
            "atmosphere is doing some of the work", "altitude", "a longer fly ball plays here",
        ),
        avoid=(
            "Don't reach for Going Nuclear's volatile/thrill-ride drama — the story here is "
            "physical and atmospheric (real wind, real air density, real park dimensions), not "
            "emotional variance. Keep it closer to meteorologist-with-a-point-of-view than "
            "hype-man. Also: per the blueprint's backtest findings, wind blowing IN does not "
            "reliably suppress real home-run rates — it's fair color, never a reason the pick "
            "is weaker.",
        ),
    ),
    "+300-499": ShelfPersonality(
        description="The sweet spot.",
        subject_is_batter=True,
        imagery_pool=(
            "value", "the math lines up", "worth a longer look", "a real angle at a real price",
            "not obvious, not a reach",
        ),
        avoid=(
            "This is the most even-keeled of the three odds-tier shelves — don't inflate it "
            "with +500-699 or Going Nuclear's bigger-swing language just to make it feel more "
            "exciting than the price actually is.",
        ),
    ),
    "+500-699": ShelfPersonality(
        description="Bigger swings, bigger payoffs.",
        subject_is_batter=True,
        imagery_pool=(
            "bigger swing", "real payoff if it hits", "a step further out",
            "worth the extra reach", "a bolder price with real backing",
        ),
        avoid=(
            "One notch more drama than +300-499, a clear notch less than Going Nuclear — don't "
            "let it collapse into either neighbor's register.",
        ),
    ),
    "Going Nuclear": ShelfPersonality(
        description="+700 and up. Buckle in.",
        subject_is_batter=True,
        imagery_pool=(
            "buckle in", "boom or bust", "the long shot with real teeth", "swing for it",
            "volatile", "live long shot", "chaos with a real case behind it",
        ),
        avoid=(
            "The single most important discipline on this shelf: maximal VOCABULARY drama here "
            "must never be read as maximal CONFIDENCE. A high-confidence Going Nuclear card "
            "(real case, per principles.py's Riley Greene example) is allowed to sound sure "
            "about the DATA — never sure about the OUTCOME. This shelf's whole premise is "
            "'genuinely a long shot, here's why we still like it,' not 'this long shot is "
            "secretly a lock.' Regardless of confidence band, this shelf's copy is checked "
            "against the exact same certainty-language rules as every other shelf — see "
            "principles.py's NEVER_IMPLIES_LOW_ODDS_ARE_SAFE and banned_language.py.",
        ),
    ),
}


def personality_for_shelf(shelf: str) -> ShelfPersonality:
    """Raises KeyError for an unrecognized shelf name — a typo here should
    fail loud during prompt construction, never silently fall back to a
    generic voice for a shelf nobody actually asked for."""
    return SHELF_PERSONALITIES[shelf]
