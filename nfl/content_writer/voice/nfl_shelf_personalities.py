"""
NFL Content Generation, Part C — voice/nfl_shelf_personalities.py.

NAMED nfl_shelf_personalities.py, not shelf_personalities.py — a real
module-name collision was found during this task's own validation (see
nfl_tasty_six_prompt.py's module docstring for the full story: nfl/ and
pipeline/ both land on sys.path for a caller that needs both, and Python
caches imports by bare name process-wide, so a same-named file in both
trees can silently resolve to the wrong one depending on import order
elsewhere in the process — not something a local sys.path.insert fix can
reliably prevent). Renamed rather than fought.

NFL's own version of pipeline/api/content_writer/voice/shelf_
personalities.py — NOT reused/cross-imported (unlike principles.py/
emotional_intensity.py/banned_language.py), since the actual content here
is genuinely sport-specific: baseball imagery ("barreling everything,"
"the ball travels tonight") has no meaning for an NFL anytime-touchdown
pick. Reuses the SHAPE (the ShelfPersonality NamedTuple itself, imported
directly from MLB's file rather than redefined — the STRUCTURE is
sport-agnostic even though every instance's content isn't).

WHAT THIS FILE IS FOR, same rule as MLB's version (see that file's own
docstring): shelf sets the VOCABULARY POOL / IMAGERY FLAVOR only — which
words and images a card draws from. It never sets how confident the copy
sounds; that's emotional_intensity.py's job (reused directly from MLB,
unmodified — see tasty_six_prompt.py), driven by tpe_score via a
confidence-band mapping that is PENDING APPROVAL as of this file's
writing (see nfl_confidence_bands.py's own docstring) — not by shelf and
not by odds.

Seven real shelves (curate_home_shelves.SHELF_ORDER): four trend shelves
(what's driving the opportunity) and three ATTD odds-band shelves (how
long a shot the price is). Grounded in each shelf's REAL scored meaning
(see nfl/shelves.py's own docstring and story functions — red_zone_story/
position_story/odds_band_story), not invented from scratch: Red Zone
Trends is about proven-vs-emerging touchdown opportunity; RB/WR/TE Trends
are about a role/workload changing hands; the three odds bands mirror
MLB's own three-tier odds structure (sweet spot -> bigger swing -> long
shot with real teeth) translated to anytime-touchdown framing instead of
home-run framing.
"""
import sys
from pathlib import Path

_PIPELINE_VOICE = Path(__file__).resolve().parent.parent.parent.parent / "pipeline" / "api" / "content_writer" / "voice"
sys.path.insert(0, str(_PIPELINE_VOICE))

from shelf_personalities import ShelfPersonality  # noqa: E402 -- shape reused, content is NFL's own below

NFL_SHELF_PERSONALITIES = {
    "Red Zone Trends": ShelfPersonality(
        description="Who's getting the real scoring chances.",
        subject_is_batter=True,  # inherited field name from the MLB shape (see ShelfPersonality's own definition) -- means "the card is about this player's own touchdown case," not the opposing defense; kept as-is rather than renamed, a cosmetic mismatch not worth forking the shared NamedTuple over
        imagery_pool=(
            "goal-line work", "inside the ten", "the opportunities are climbing", "getting the ball near paydirt",
            "red-zone real estate", "the touches are trending the right way", "close enough to matter",
        ),
        avoid=(
            "This shelf is about REAL red-zone opportunity (touches, targets, and trend), not raw box-score "
            "hype -- don't borrow a bigger-payoff odds-shelf's drama just because a name sounds exciting.",
        ),
    ),
    "RB Trends": ShelfPersonality(
        description="A backfield role that's genuinely on the move.",
        subject_is_batter=True,
        imagery_pool=(
            "the workload is shifting", "carving out real touches", "climbing the depth chart",
            "a role that's opening up", "taking on more of the load", "the usage is trending his way",
        ),
        avoid=(
            "The story here is a REAL role/usage change (snap share, touch share, depth-chart movement, or a "
            "teammate's injury clearing a path) -- not generic 'he's a good back' hype untethered from a real "
            "trend.",
        ),
    ),
    "WR Trends": ShelfPersonality(
        description="A target share that's genuinely on the move.",
        subject_is_batter=True,
        imagery_pool=(
            "the targets are trending up", "carving out a bigger share", "climbing the pecking order",
            "a role that's opening up", "seeing more of the field", "the usage is trending his way",
        ),
        avoid=(
            "Same discipline as RB Trends -- a real usage/role trend, not generic talent praise. Don't reach for "
            "a long-shot odds shelf's boom-or-bust language just because a receiver is exciting.",
        ),
    ),
    "TE Trends": ShelfPersonality(
        description="A tight end role that's genuinely on the move.",
        subject_is_batter=True,
        imagery_pool=(
            "the role is expanding", "seeing more of the field", "a bigger part of the game plan",
            "climbing the pecking order", "the usage is trending his way", "carving out real touches",
        ),
        avoid=(
            "Same discipline as RB/WR Trends -- a genuine usage/role trend, not generic praise for a tight end "
            "having a good season.",
        ),
    ),
    "ATTD +300-499": ShelfPersonality(
        description="The sweet spot.",
        subject_is_batter=True,
        imagery_pool=(
            "value", "the math lines up", "worth a longer look", "a real angle at a real price",
            "not obvious, not a reach",
        ),
        avoid=(
            "The most even-keeled of the three odds-tier shelves -- don't inflate it with +500-699 or +700+'s "
            "bigger-swing language just to make it feel more exciting than the price actually is.",
        ),
    ),
    "ATTD +500-699": ShelfPersonality(
        description="Bigger swings, bigger payoffs.",
        subject_is_batter=True,
        imagery_pool=(
            "bigger swing", "real payoff if it hits", "a step further out",
            "worth the extra reach", "a bolder price with real backing",
        ),
        avoid=(
            "One notch more drama than ATTD +300-499, a clear notch less than +700+ -- don't let it collapse "
            "into either neighbor's register.",
        ),
    ),
    "ATTD +700+": ShelfPersonality(
        description="Long odds, real teeth.",
        subject_is_batter=True,
        imagery_pool=(
            "a genuine long shot with real backing", "the price is long, the case isn't thin",
            "live long shot", "worth the reach", "a real angle at a real long price",
        ),
        avoid=(
            "The single most important discipline on this shelf, same as MLB's own longest-odds shelf: maximal "
            "VOCABULARY drama here must never be read as maximal CONFIDENCE. A high-confidence +700+ card is "
            "allowed to sound sure about the DATA -- never sure about the OUTCOME. This shelf's whole premise is "
            "'genuinely a long shot, here's why we still like it,' not 'this long shot is secretly a lock.' "
            "Regardless of confidence band, this shelf's copy is checked against the exact same certainty-"
            "language rules as every other shelf -- see principles.py's NEVER_IMPLIES_LOW_ODDS_ARE_SAFE and "
            "banned_language.py (both reused directly from MLB, unmodified).",
        ),
    ),
}


def personality_for_shelf(shelf: str) -> ShelfPersonality:
    """Raises KeyError for an unrecognized shelf name -- same fail-loud
    reasoning as MLB's own personality_for_shelf(): a typo here should
    fail loud during prompt construction, never silently fall back to a
    generic voice for a shelf nobody actually asked for."""
    return NFL_SHELF_PERSONALITIES[shelf]
