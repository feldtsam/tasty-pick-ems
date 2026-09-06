"""
NFL Content Generation V1, Part 1 — nfl_shelf_card_prompt.py.

NAMED nfl_shelf_card_prompt.py, not shelf_card_prompt.py — same real
module-name-collision fix nfl_tasty_six_prompt.py's own docstring
documents (see that file for the full story).

NFL's own version of pipeline/api/content_writer/shelf_card_prompt.py:
same composition pattern (nfl_shelf_personalities.py's shelf voice +
emotional_intensity.py's confidence-band intensity + banned_language.py's
two lists as explicit constraints), same title+why_reasons-only contract
(no editorial_sentence — see nfl_shelf_card_writer_schema.py), same
avoid_headlines variety mechanism, PLUS two things regular shelf cards
need that neither MLB's version nor NFL's own Tasty Six writer has:

1. THE EDITORIAL LENS — the real reason this file exists rather than
   just reusing nfl_tasty_six_prompt.py with a smaller schema. Per the
   approved design: the same player can qualify for two different
   shelves in one week, and without an explicit lens both cards would
   read from the identical five-pillar picture and tell the same story
   twice. `editorial_lens` (see editorial_lenses.py) names this card's
   real primary signal and its real supporting signals; the prompt
   below instructs the model to lead with the primary signal's own
   numbers specifically, not the full picture — backed by generate_
   nfl_shelf_card_content.py actually SCOPING source_facts down to the
   lens's own eligible fields before this prompt is even built, so the
   instruction has teeth: the other pillars' numbers usually aren't
   sitting in front of the model to begin with, not just off-limits by
   request.

2. avoid_opening_phrases — a SECOND, complementary variety mechanism to
   avoid_headlines, per explicit instruction. REAL PROBLEM avoid_
   headlines alone doesn't close: it stops an exact-title repeat, but
   six independently-generated WR Trends cards can each land on a
   DIFFERENT literal sentence that all open the same way ("His role
   keeps growing...", "The role keeps expanding...", "His workload is
   trending up...") — a real repeat at the ANGLE level even though no
   two titles are byte-identical, which avoid_headlines' own "don't
   reuse this exact string" framing has no way to catch. avoid_opening_
   phrases tracks the actual opening clause already used (see generate_
   nfl_shelf_card_content.py's _opening_phrase()), grown across the same
   batch exactly the way avoid_headlines already is, and named to the
   model as its own explicit category of repetition to avoid — not
   folded into the same avoid_headlines block, so the model sees "these
   exact titles are taken" and "these opening angles are taken" as two
   distinct instructions rather than one blurred together.

Pure string-building, no network call — testable in isolation, same as
every other prompt module in this codebase.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "voice"))

from banned_language import GUARANTEE_LANGUAGE, LITERAL_BETTING_SLANG  # noqa: E402 -- reused unmodified
from emotional_intensity import intensity_for_band  # noqa: E402 -- reused unmodified
from nfl_shelf_personalities import personality_for_shelf  # noqa: E402 -- NFL's own

# Natural-language framing per real signal name, used only to tell the
# model IN WORDS which real evidence to lead with — the actual field
# scoping happens upstream (editorial_lenses.citable_fields_for_lens),
# this is just the matching plain-English instruction so the model
# understands WHY certain facts are present and others aren't.
_SIGNAL_DESCRIPTIONS = {
    "td_opportunity": "how much real red-zone/goal-line opportunity this player has actually been getting",
    "role_momentum": "how this player's real on-field role/usage has been trending",
    "situation": "the real matchup/environment context this player is stepping into this week",
    "evidence_quality": "how much the model's other real signals agree with each other on this player",
    "market_value": "where the real betting market itself currently prices this player",
}


def _signal_phrase(signal_name: str) -> str:
    return _SIGNAL_DESCRIPTIONS.get(signal_name, signal_name)


def build_system_prompt(
    shelf: str, confidence_band: str, editorial_lens: dict,
    avoid_headlines: list[str] | None = None, avoid_opening_phrases: list[str] | None = None,
) -> str:
    """
    Raises KeyError for an unrecognized shelf or band — same fail-loud
    reasoning as personality_for_shelf()/intensity_for_band() themselves.

    `editorial_lens`: {"primary": signal_name, "supporting": (signal_name,
    ...)} — see editorial_lenses.resolve_editorial_lens(). Required, not
    optional: every real shelf has one, and a card written with no lens
    at all defeats the entire real point of this task (two shelves for
    the same player reading as two different stories).

    `avoid_headlines`/`avoid_opening_phrases`: same real-batch-variety
    mechanism as nfl_tasty_six_prompt.py's avoid_headlines, extended per
    explicit instruction — see this module's own docstring for the real
    angle-level repetition avoid_headlines alone doesn't catch.
    """
    personality = personality_for_shelf(shelf)
    intensity = intensity_for_band(confidence_band)
    banned_phrases = ", ".join(GUARANTEE_LANGUAGE + LITERAL_BETTING_SLANG)

    primary_phrase = _signal_phrase(editorial_lens["primary"])
    supporting = editorial_lens["supporting"]
    supporting_block = ""
    if supporting:
        supporting_phrases = "; ".join(_signal_phrase(s) for s in supporting)
        supporting_block = f" You may use the supporting evidence below ({supporting_phrases}) only to back up that primary read, never to replace it as the card's main point."

    variety_block = ""
    if avoid_headlines:
        used = "\n".join(f'- "{h}"' for h in avoid_headlines)
        variety_block += f"""

REAL VARIETY REQUIRED -- these exact headlines have ALREADY been used elsewhere in today's batch:
{used}
Do not reuse any of them, and do not reuse their underlying SENTENCE STRUCTURE with just the player's name swapped in -- e.g. if "X's role keeps growing" is above, "Y's workload is trending up" for a different player is still a structural repeat, not real variety."""
    if avoid_opening_phrases:
        used_openers = "\n".join(f'- "{o}"' for o in avoid_opening_phrases)
        variety_block += f"""

OPENING ANGLES ALREADY USED -- these are the OPENING clause/angle of headlines already written elsewhere in today's batch (even where the full sentence differs, don't lead with the same angle again):
{used_openers}
Find a genuinely different way to open THIS headline -- a different angle, image, or sentence shape -- even when this player's own numbers look similar to a card you've already seen."""

    return f"""You are a shelf card writer for Tasty Pick Ems, an NFL anytime-touchdown (ATTD) pick'em app whose whole differentiator is an honest voice in a space built on overselling "locks" to keep engagement up. Being willing to say "this is genuinely a long shot, here's why we still like it" is a trust move, not a hedge.

You are writing for the "{shelf}" shelf: {personality.description}
Vocabulary/imagery this shelf draws from: {", ".join(personality.imagery_pool)}
Specifically avoid: {" ".join(personality.avoid)}

This candidate's confidence band is "{confidence_band}":
{intensity.assertiveness}
Title register: {intensity.title_register}

THE REAL STORY HERE is {primary_phrase} -- write the title and every why_reason from THAT evidence specifically, using its own real numbers and deltas.{supporting_block} This player may qualify for more than one shelf this week; a card written from a different shelf's own lens is a genuinely different story about the same real numbers, not a rewrite of this one -- don't reach for the full five-pillar picture just because it might exist elsewhere.

HARD RULES -- apply regardless of shelf or confidence band:
- Shelf sets vocabulary/imagery ONLY. Confidence band sets how assertive you sound about the DATA ONLY. A dramatic shelf (like ATTD +700+) never means you should sound more confident than the real confidence band justifies. A high confidence band never means the BET itself is safe -- a card at long-shot odds is still, honestly, a long shot, no matter how confident the underlying data is.
- Never use any of these phrases or close variants of them, in any form: {banned_phrases}.
- Every claim in why_reasons MUST be traceable to a real key in the source facts you are given below. Cite the exact key(s) in source_fact_keys. Never invent a stat, trend, or fact not present in the source data. If a fact isn't present below, it isn't available for this card -- don't reach for a number you'd expect a player like this to have.
- Write 2-3 why_reasons. Each one's `pillar` field must be EXACTLY one of these five literal strings, spelled exactly as written here -- never a shortened or paraphrased version (not "opportunity", not "role", not "market"): "td_opportunity", "role_momentum", "matchup", "environment", "market_value". Tag each reason with whichever of those five its own real evidence actually comes from, and give it a star rating (1-5) that genuinely reflects that pillar's real score -- not an independent creative choice. At least one why_reason must be tagged with this card's own primary lens's pillar (matchup or environment if the lens is "situation").
- why_reasons is the EVIDENCE layer -- the lowest-personality text on the card (about 2 on a 0-10 scale), and it stays there no matter how dramatic the shelf is or how high the confidence band. Receipts, not verdicts: each reason states a number, a comparison, a window, a sample size, or a source, in plain language. The reader opened this to verify the pick, not to be entertained -- the title already made the argument, so a reason just shows the math under it. State the fact first, plainly; only then consider whether the material supports any personality at all, and it usually will not. No joke is required or expected here. At most one dry aside across all of the reasons, only if it genuinely fits, and never load-bearing -- the point has to stand completely without it. Never soften, hedge, or joke around a thin sample size or a low pillar score -- report it straight.
- This is a regular shelf card: title and why_reasons only. There is no separate editorial sentence field here -- that hybrid format is specific to the Tasty Six.
- This is football, not baseball -- do not use baseball terminology, imagery, or comparisons anywhere in the card.{variety_block}
"""


def build_user_prompt(source_facts: dict) -> str:
    facts_json = json.dumps(source_facts, indent=2, sort_keys=True, default=str)
    return f"""Here are the ONLY real facts you may reference or cite for this candidate. Do not use any number, name, or claim that isn't present here:

{facts_json}

Write the shelf card now using the emit_nfl_shelf_card tool."""
