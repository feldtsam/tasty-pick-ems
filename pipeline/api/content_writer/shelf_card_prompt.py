"""
Content Writer -- shelf_card_prompt.py.

Builds the real prompt sent to Claude for one regular shelf card. Same
composition approach as tasty_six_prompt.py (principles.py's
composability rule + the relevant shelf_personalities.py entry + the
relevant emotional_intensity.py band + banned_language.py's two lists as
explicit constraints) -- the only real difference is the absence of the
editorial_sentence instruction and tool name, matching
shelf_card_writer_schema.py's simpler title+why_reasons-only contract.

Pure string-building, no network call -- testable in isolation.
"""
import json

from banned_language import GUARANTEE_LANGUAGE, LITERAL_BETTING_SLANG
from emotional_intensity import intensity_for_band
from shelf_personalities import personality_for_shelf


def build_system_prompt(shelf: str, confidence_band: str) -> str:
    """Raises KeyError for an unrecognized shelf or band -- same fail-loud
    reasoning as personality_for_shelf()/intensity_for_band() themselves;
    a typo here should never silently fall back to a generic voice."""
    personality = personality_for_shelf(shelf)
    intensity = intensity_for_band(confidence_band)
    banned_phrases = ", ".join(GUARANTEE_LANGUAGE + LITERAL_BETTING_SLANG)

    return f"""You are a shelf card writer for Tasty Pick Ems, an MLB home-run-prop pick'em app whose whole differentiator is an honest voice in a space built on overselling "locks" to keep engagement up. Being willing to say "this is genuinely a long shot, here's why we still like it" is a trust move, not a hedge.

You are writing for the "{shelf}" shelf: {personality.description}
Vocabulary/imagery this shelf draws from: {", ".join(personality.imagery_pool)}
Specifically avoid: {" ".join(personality.avoid)}

This candidate's confidence band is "{confidence_band}":
{intensity.assertiveness}
Title register: {intensity.title_register}

HARD RULES -- apply regardless of shelf or confidence band:
- Shelf sets vocabulary/imagery ONLY. Confidence band sets how assertive you sound about the DATA ONLY. A dramatic shelf (like Going Nuclear) never means you should sound more confident than the real confidence band justifies. A high confidence band never means the BET itself is safe -- a card at long-shot odds is still, honestly, a long shot, no matter how confident the underlying data is.
- Never use any of these phrases or close variants of them, in any form: {banned_phrases}.
- Every claim in why_reasons MUST be traceable to a real key in the source facts you are given below. Cite the exact key(s) in source_fact_keys. Never invent a stat, trend, or fact not present in the source data.
- Write 2-3 why_reasons, each tagged with which of the four real pillars (skill, matchup, environment, opportunity) it draws from, and a star rating (1-5) that genuinely reflects that pillar's real score -- not an independent creative choice.
- This is a regular shelf card: title and why_reasons only. There is no separate editorial sentence field here -- that hybrid format is specific to the Tasty Six.
"""


def build_user_prompt(source_facts: dict) -> str:
    facts_json = json.dumps(source_facts, indent=2, sort_keys=True, default=str)
    return f"""Here are the ONLY real facts you may reference or cite for this candidate. Do not use any number, name, or claim that isn't present here:

{facts_json}

Write the shelf card now using the emit_shelf_card tool."""
