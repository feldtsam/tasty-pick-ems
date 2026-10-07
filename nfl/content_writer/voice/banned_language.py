"""
COPIED from pipeline/api/content_writer/voice/banned_language.py, not
cross-imported — this Vercel project's Root Directory is `nfl`, so
pipeline/ is never in the deployed function's filesystem. See card_
writer_common.py's own header (in this same nfl/content_writer/
directory) for the full reasoning. If this changes on the MLB side,
update this copy by hand.

Voice Engine, Layer 1 — banned_language.py.

NEW, UNREVIEWED DESIGN WORK (2026-08-03) — same status as the other two
files built alongside this one tonight. Referenced by principles.py as
the enforcement mechanism for NEVER_IMPLIES_LOW_ODDS_ARE_SAFE ("the same
certainty-language banned list... high confidence is never a license to
loosen that check"). Grounded directly in the blueprint's own real
guardrails, not the original master prompt (not recoverable — see
shelf_personalities.py's docstring for why):

  - §0 (why the app exists): "people chasing high-odds long-shot bets
    mostly do it on borrowed hype — 'my friend said it's a lock' —
    rather than their own research... an honest voice in a space built on
    overselling 'locks.'" GUARANTEE_LANGUAGE exists specifically to keep
    the product from becoming the thing it was built to be an alternative
    to.
  - §14 (App Store / legal risk): "public-facing copy uses analysis/
    insight language ('Strong Value Target,' 'Confidence: High,' 'Lean
    Over') instead of literal betting phrasing ('Bet Over 1.5 Bases').
    ... avoiding explicit 'bet'/'wager' language ... reduces both App
    Store misclassification risk and touting-adjacent legal risk."
    LITERAL_BETTING_SLANG exists for this — a real compliance concern,
    not just a style preference.
  - §14 disclaimer language: "past performance does not guarantee future
    results" — the same "never guarantee" discipline this list enforces
    mechanically, card by card.

TWO SEPARATE LISTS, TWO SEPARATE REASONS — kept distinct rather than one
merged "bad words" list, since they fail for different reasons and a
future maintainer should be able to tell which concern a match is about:
  - GUARANTEE_LANGUAGE: implies the OUTCOME is certain. A trust/brand-
    promise concern (§0) — this is what principles.py's
    NEVER_IMPLIES_LOW_ODDS_ARE_SAFE rule is actually checking for.
  - LITERAL_BETTING_SLANG: imperative gambling-instruction phrasing. A
    compliance/App-Store-classification concern (§14) — the fix isn't
    "sound less confident," it's "phrase it as analysis, not an
    instruction to wager." "Lean Over" is explicitly blueprint-approved;
    "Bet Over" is explicitly the flagged bad example.

MATCHING APPROACH: whole-word regex boundaries for single words (`\\block\\b`
matches "lock"/"locks" but deliberately NOT "locked" or "locking" — the
adjective/verb sense of "locked in" is a legitimate baseball idiom already
used in shelf_personalities.py's Hot Hitters imagery pool, and must not
false-positive against the noun sense of "a lock" meaning a sure thing).
Multi-word phrases use plain case-insensitive substring matching, since a
full phrase match has effectively no realistic collision risk here.

Deliberately excluded despite being guarantee-adjacent in general English:
"automatic" — real collision risk with MLB's actual "automatic runner"
extra-innings rule, a legitimate baseball term this content could
genuinely need to reference. Left out rather than accepting a predictable
false-positive source; revisit if it never actually comes up in practice.
"""
import re

GUARANTEE_LANGUAGE = (
    "lock",
    "locks",
    "guaranteed",
    "guarantee",
    "can't miss",
    "cant miss",
    "can't lose",
    "cant lose",
    "sure thing",
    "sure bet",
    "safe bet",
    "best bet",
    "no-brainer",
    "no brainer",
    "free money",
    "easy money",
    "surefire",
    "sure-fire",
    "can't fail",
    "cant fail",
    "guaranteed winner",
    "in the bag",
    "money in the bank",
    "slam dunk",
)

LITERAL_BETTING_SLANG = (
    "bet on",
    "bet the over",
    "bet the under",
    "bet over",
    "bet under",
    "place a bet",
    "place your bet",
    "wager",
    "wager on",
    "smash the over",
    "hammer the over",
    "cash this",
    "cash in on this",
    "bet the house",
)

# Real card found with this exact problem (NFL Red Zone Trends, thin-
# sample/"forming" cards, 2026-09-30): a title or story on a card backed
# by fewer than 3 real games asserted what the betting MARKET would do
# next ("outrunning his price", "room to move", "before the market
# catches up") -- a prediction this pipeline has no real basis for and
# never should have made (describing the real gap between opportunity
# and price is fine; forecasting which way that gap resolves is not).
# Distinct from GUARANTEE_LANGUAGE/LITERAL_BETTING_SLANG above (those are
# about overselling CONFIDENCE in a pick; this is about predicting WHERE
# a price goes) -- kept as its own list so each stays independently
# reviewable and named for what it actually catches.
PRICE_MOVEMENT_PREDICTION_PHRASES = (
    "outrunning his price",
    "outrunning her price",
    "outrunning the price",
    "before the market catches up",
    "before the market corrects",
    "room to move",
    "the price hasn't caught up",
    "the market hasn't caught up",
    "the odds will move",
    "the price will move",
    "the number will move",
    "due for a correction",
    "primed to shorten",
    "primed to move",
)

# Stock phrases (2026-10-06): the filler the shelf-card writer reaches
# for when it runs out of evidence -- every one of these showed up in
# more than one real Week 5 2026 story, often restating a tension the
# headline had already stated. WARN-ONLY, on purpose: a card is not
# flagged out of review for style, so these are deliberately NOT part of
# the three lists find_banned_phrases() checks, and never change
# validation_passed. They are surfaced through find_stock_phrases()
# below (generate_nfl_shelf_card_content.run_all_warnings) and named in
# the prompt. "the price hasn't caught up" is not here because it is
# already on PRICE_MOVEMENT_PREDICTION_PHRASES above, where it blocks.
# "we don't fully know yet" is matched as the prefix "we don't fully
# know" so the "why yet" variant the old prompt used to suggest is
# caught too.
STOCK_PHRASES = (
    "something is building here",
    "we don't fully know yet",
    "early, honest signal",
    "honest watch",
    "that's the whole intrigue",
    "that's the real tension",
    "that's the puzzle",
)

_STOCK_MATCH_TEXT = {
    "we don't fully know yet": "we don't fully know",
}


def _normalize_apostrophes(text: str) -> str:
    return text.replace("\u2019", "'").replace("\u2018", "'")


def find_stock_phrases(text: str) -> list[str]:
    """
    Returns every STOCK_PHRASES entry found in `text` (case-insensitive,
    curly apostrophes normalized), in list order, or [] if clean. Same
    deterministic "say which phrase" shape as find_banned_phrases(), but
    kept as a separate function so a caller can never confuse a style
    warning with a blocking validation failure.
    """
    haystack = _normalize_apostrophes(text or "").lower()
    return [
        phrase for phrase in STOCK_PHRASES
        if _STOCK_MATCH_TEXT.get(phrase, phrase) in haystack
    ]


_SINGLE_WORD_PATTERNS = {
    phrase: re.compile(rf"\b{re.escape(phrase)}\b", re.IGNORECASE)
    for phrase in GUARANTEE_LANGUAGE + LITERAL_BETTING_SLANG + PRICE_MOVEMENT_PREDICTION_PHRASES
    if " " not in phrase and "-" not in phrase
}


def find_banned_phrases(text: str) -> list[str]:
    """
    Returns every banned phrase found in `text` (from any of the three
    lists, deduplicated in the order the lists are defined), or an empty
    list if clean. Deliberately returns WHICH phrases matched, not just a
    pass/fail boolean — the whole point of this being deterministic is
    that a validation failure should be immediately explainable, not a
    black box the human reviewer has to reverse-engineer.
    """
    found = []
    for phrase in GUARANTEE_LANGUAGE + LITERAL_BETTING_SLANG + PRICE_MOVEMENT_PREDICTION_PHRASES:
        pattern = _SINGLE_WORD_PATTERNS.get(phrase)
        if pattern is not None:
            if pattern.search(text):
                found.append(phrase)
        elif phrase.lower() in text.lower():
            found.append(phrase)
    return found
