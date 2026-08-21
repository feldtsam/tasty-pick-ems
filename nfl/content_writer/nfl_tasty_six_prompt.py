"""
NFL Content Generation, Part C — nfl_tasty_six_prompt.py.

NAMED nfl_tasty_six_prompt.py, not tasty_six_prompt.py -- a real bug
found DURING this task's own validation: nfl/ and pipeline/ are both on
sys.path at once for a caller that needs both (as this module's own
callers do), and Python caches imported modules by their BARE name in
sys.modules process-wide -- a same-named "tasty_six_prompt.py" in both
trees meant whichever one got imported FIRST anywhere in the process
silently won for every later `from tasty_six_prompt import ...`
regardless of sys.path order at that later call site (confirmed directly:
NFL's own build_system_prompt("Red Zone Trends", ...) call raised
KeyError from INSIDE MLB's shelf_personalities.py, because MLB's
tasty_six_prompt module -- not NFL's -- had won the naming race). Fixed
by giving every NFL file that collides with an MLB filename here a
distinct name, not by trying to win a sys.path ordering fight that would
stay fragile for the next caller. See nfl_shelf_personalities.py and
nfl_tasty_six_writer_schema.py for the same fix, same reason.

NFL's own version of pipeline/api/content_writer/tasty_six_prompt.py.
Same composition pattern (principles.py's composability rule + the
relevant shelf personality + the relevant confidence-band intensity +
banned_language.py's two lists as explicit constraints), same
avoid_headlines variety mechanism -- only the actual prompt COPY differs:
NFL-flavored, no baseball references, correctly describes this as an NFL
anytime-touchdown (ATTD) pick'em app, and states NFL's real five-pillar
enum instead of MLB's four.

Only a Tasty Six writer exists for NFL, per the approved architecture --
regular (non-Tasty-Six) shelf cards use shelves.py's deterministic
template system (Part A), not this LLM path. No shelf_card_prompt.py
analog is built here.

REUSES intensity_for_band (emotional_intensity.py) and find_banned_
phrases' source lists (banned_language.py) by cross-importing directly
from pipeline/, unmodified -- per the approved reuse-as-is list. Does
NOT reuse principles.py's CONFIDENCE_BAND_THRESHOLDS/confidence_band_
for_score -- those are MLB-tuned numbers this task investigates and
proposes NFL-specific replacements for (see nfl_confidence_bands.py),
PENDING APPROVAL as of this file's writing. This module itself never
calls that scoring function -- it only takes a `confidence_band` STRING
and looks up its (reused, unmodified) assertiveness copy, so it needed no
change either way.

Pure string-building, no network call -- testable in isolation, same as
MLB's version.
"""
import json
import sys
from pathlib import Path

_PIPELINE_VOICE = Path(__file__).resolve().parent.parent.parent / "pipeline" / "api" / "content_writer" / "voice"
sys.path.insert(0, str(_PIPELINE_VOICE))

from banned_language import GUARANTEE_LANGUAGE, LITERAL_BETTING_SLANG  # noqa: E402 -- reused unmodified
from emotional_intensity import intensity_for_band  # noqa: E402 -- reused unmodified

sys.path.insert(0, str(Path(__file__).resolve().parent / "voice"))
from nfl_shelf_personalities import personality_for_shelf  # noqa: E402 -- NFL's own


def build_system_prompt(shelf: str, confidence_band: str, avoid_headlines: list[str] | None = None) -> str:
    """Raises KeyError for an unrecognized shelf or band -- same fail-loud
    reasoning as MLB's version; a typo here should never silently fall
    back to a generic voice.

    `avoid_headlines`: same real-batch repetition mechanism as MLB's
    version -- see its own docstring for the real production case
    (near-identical headlines across independently-generated cards with
    similar source facts) this closes."""
    personality = personality_for_shelf(shelf)
    intensity = intensity_for_band(confidence_band)
    banned_phrases = ", ".join(GUARANTEE_LANGUAGE + LITERAL_BETTING_SLANG)

    variety_block = ""
    if avoid_headlines:
        used = "\n".join(f'- "{h}"' for h in avoid_headlines)
        variety_block = f"""

REAL VARIETY REQUIRED -- these exact headlines/lines have ALREADY been used elsewhere in today's batch:
{used}
Do not reuse any of them, and do not reuse their underlying SENTENCE STRUCTURE with just the player's name swapped in. Find a genuinely different angle, sentence shape, or image for THIS card, even when the underlying stats look similar to a card you've already seen."""

    return f"""You are the Tasty Six card writer for Tasty Pick Ems, an NFL anytime-touchdown (ATTD) pick'em app whose whole differentiator is an honest voice in a space built on overselling "locks" to keep engagement up. Being willing to say "this is genuinely a long shot, here's why we still like it" is a trust move, not a hedge.

You are writing for the "{shelf}" shelf: {personality.description}
Vocabulary/imagery this shelf draws from: {", ".join(personality.imagery_pool)}
Specifically avoid: {" ".join(personality.avoid)}

This candidate's confidence band is "{confidence_band}":
{intensity.assertiveness}
Title register: {intensity.title_register}

HARD RULES -- apply regardless of shelf or confidence band:
- Shelf sets vocabulary/imagery ONLY. Confidence band sets how assertive you sound about the DATA ONLY. A dramatic shelf (like ATTD +700+) never means you should sound more confident than the real confidence band justifies. A high confidence band never means the BET itself is safe -- a card at long-shot odds is still, honestly, a long shot, no matter how confident the underlying data is.
- Never use any of these phrases or close variants of them, in any form: {banned_phrases}.
- Every claim in why_reasons MUST be traceable to a real key in the source facts you are given below. Cite the exact key(s) in source_fact_keys. Never invent a stat, trend, or fact not present in the source data.
- Write 2-3 why_reasons, each tagged with which of the five real pillars (td_opportunity, role_momentum, matchup, environment, market_value) it draws from, and a star rating (1-5) that genuinely reflects that pillar's real score -- not an independent creative choice.
- editorial_sentence must be a sharper, more specific supporting line beneath the title -- grounded in a real fact, not a generic restatement of the title.
- This is football, not baseball -- do not use baseball terminology, imagery, or comparisons anywhere in the card.{variety_block}
"""


def build_user_prompt(source_facts: dict) -> str:
    facts_json = json.dumps(source_facts, indent=2, sort_keys=True, default=str)
    return f"""Here are the ONLY real facts you may reference or cite for this candidate. Do not use any number, name, or claim that isn't present here:

{facts_json}

Write the Tasty Six card now using the emit_nfl_tasty_six_card tool."""
