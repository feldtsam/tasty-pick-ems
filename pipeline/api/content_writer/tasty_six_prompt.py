"""
Content Writer, Phase 5 -- tasty_six_prompt.py.

Builds the real prompt sent to Claude for one Tasty Six card. The system
prompt assembles principles.py's composability rule, the relevant
shelf_personalities.py entry, the relevant emotional_intensity.py band,
and banned_language.py's two lists as EXPLICIT constraints -- steering
generation away from violations up front, not just catching them after
the fact. Both layers matter, but only one is actually trusted: the
system prompt reduces how often generation produces something that needs
rejecting; the deterministic validators in tasty_six_writer_schema.py and
banned_language.py are what this whole design relies on, since a prompt
instruction is a request, not a guarantee.

EDITORIAL VOICE SPEC -- EVIDENCE LAYER (2026-09): the why_reasons HARD
RULE below carries an explicit ~2/10 personality ceiling per Section 5 /
Section 7 of the locked TPE Editorial Voice Spec -- receipts, not
verdicts; the fact leads, personality never does; a dry aside only if it
genuinely fits and never load-bearing. This is scoped to why_reasons
ONLY. The intensity_for_band assertiveness language and the shelf
personality still govern `title` and `editorial_sentence` at their full
register -- those are separate voice tiers (Headline / Story) with their
own later rollout; the new ceiling narrows why_reasons down from that
inherited voice and touches nothing else. Direct MLB counterpart of the
NFL change in f449f1f.

Pure string-building, no network call -- testable in isolation.
"""
import json

from banned_language import GUARANTEE_LANGUAGE, LITERAL_BETTING_SLANG
from emotional_intensity import intensity_for_band
from shelf_personalities import personality_for_shelf


BANNED_PHRASES = ", ".join(GUARANTEE_LANGUAGE + LITERAL_BETTING_SLANG)

# PROMPT CACHING -- the frozen half of the system prompt, hoisted to a module
# constant so it is byte-for-byte identical on every call in every batch.
# card_writer_common.system_blocks() puts the cache_control breakpoint at the
# end of this text, which means everything here plus TASTY_SIX_TOOL_SCHEMA
# (tools render ahead of system) is the cached prefix.
#
# NOTHING per-candidate may move into this string -- no shelf, no confidence
# band, no date, no player, and never avoid_headlines, which grows on every
# call within a batch and would therefore change the prefix on every call.
# BANNED_PHRASES is safe: it is two literal lists from banned_language.py
# concatenated in a fixed order, so it renders the same bytes every time.
STATIC_SYSTEM_PROMPT = f"""You are the Tasty Six card writer for Tasty Pick Ems, an MLB home-run-prop pick'em app whose whole differentiator is an honest voice in a space built on overselling "locks" to keep engagement up. Being willing to say "this is genuinely a long shot, here's why we still like it" is a trust move, not a hedge.

HARD RULES -- apply regardless of shelf or confidence band:
- Shelf sets vocabulary/imagery ONLY. Confidence band sets how assertive you sound about the DATA ONLY. A dramatic shelf (like Going Nuclear) never means you should sound more confident than the real confidence band justifies. A high confidence band never means the BET itself is safe -- a card at long-shot odds is still, honestly, a long shot, no matter how confident the underlying data is.
- Never use any of these phrases or close variants of them, in any form: {BANNED_PHRASES}.
- Every claim in why_reasons MUST be traceable to a real key in the source facts you are given below. Cite the exact key(s) in source_fact_keys. Never invent a stat, trend, or fact not present in the source data.
- Write 2-3 why_reasons, each tagged with which of the four real pillars (skill, matchup, environment, opportunity) it draws from, and a star rating (1-5) that genuinely reflects that pillar's real score -- not an independent creative choice.
- why_reasons is the EVIDENCE layer -- the lowest-personality text on the card (about 2 on a 0-10 scale), and it stays there no matter how dramatic the shelf is or how high the confidence band. Receipts, not verdicts: each reason states a number, a comparison, a window, a sample size, or a source, in plain language. The reader opened this to verify the pick, not to be entertained -- the title already made the argument, so a reason just shows the stats under it. State the fact first, plainly; only then consider whether the material supports any personality at all, and it usually will not. No joke is required or expected here. At most one dry aside across all of the reasons, only if it genuinely fits, and never load-bearing -- the point has to stand completely without it. Never soften, hedge, or joke around a thin sample size or a low pillar score -- report it straight.
- editorial_sentence must be a sharper, more specific supporting line beneath the title -- grounded in a real fact, not a generic restatement of the title.
"""


def build_dynamic_system_prompt(shelf: str, confidence_band: str, avoid_headlines: list[str] | None = None) -> str:
    """The per-candidate half of the system prompt -- everything that legitimately
    differs call to call. Goes AFTER the cache breakpoint, so none of it can
    invalidate the cached prefix.

    Raises KeyError for an unrecognized shelf or band -- same fail-loud
    reasoning as personality_for_shelf()/intensity_for_band() themselves;
    a typo here should never silently fall back to a generic voice.

    `avoid_headlines`: real titles (and editorial sentences) already
    generated elsewhere in the SAME batch -- see shelf_card_prompt.py's
    build_dynamic_system_prompt for the real production repetition case that
    motivated this (Hot Hitters, Cold Pitchers to Attack) and
    content_draft_generation_live.py, the only real caller that populates
    it. Same mechanism here: a Tasty Six card and its own source shelf
    card can independently converge on near-identical language for the
    same real candidate/stats if nothing says otherwise. This list is also
    the single most important reason the static/dynamic split exists: it is
    appended to on every iteration of the batch loop, so a prompt that
    carried it in the cached prefix would miss the cache on every call.
    """
    personality = personality_for_shelf(shelf)
    intensity = intensity_for_band(confidence_band)

    variety_block = ""
    if avoid_headlines:
        used = "\n".join(f'- "{h}"' for h in avoid_headlines)
        variety_block = f"""

REAL VARIETY REQUIRED -- these exact headlines/lines have ALREADY been used elsewhere in today's batch:
{used}
Do not reuse any of them, and do not reuse their underlying SENTENCE STRUCTURE with just the player's name swapped in -- e.g. if "X's swing is humming right now" is above, "Y's bat is heating up right now" for a different player is still a structural repeat, not real variety. Find a genuinely different angle, sentence shape, or image for THIS card, even when the underlying stats look similar to a card you've already seen."""

    return f"""
You are writing for the "{shelf}" shelf: {personality.description}
Vocabulary/imagery this shelf draws from: {", ".join(personality.imagery_pool)}
Specifically avoid: {" ".join(personality.avoid)}

This candidate's confidence band is "{confidence_band}":
{intensity.assertiveness}
Title register: {intensity.title_register}{variety_block}
"""


def build_system_prompt(shelf: str, confidence_band: str, avoid_headlines: list[str] | None = None) -> str:
    """The two halves concatenated -- the exact text the model reads, minus the
    block boundary. Kept so the existing test suite and any caller that only
    wants to inspect the finished prompt do not have to know about caching;
    real calls go through build_dynamic_system_prompt() + system_blocks()
    instead, because a single joined string has nowhere to put a breakpoint.
    """
    return STATIC_SYSTEM_PROMPT + build_dynamic_system_prompt(shelf, confidence_band, avoid_headlines)


def build_user_prompt(source_facts: dict) -> str:
    facts_json = json.dumps(source_facts, indent=2, sort_keys=True, default=str)
    return f"""Here are the ONLY real facts you may reference or cite for this candidate. Do not use any number, name, or claim that isn't present here:

{facts_json}

Write the Tasty Six card now using the emit_tasty_six_card tool."""
