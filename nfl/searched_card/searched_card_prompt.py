"""
Searched Card writer — system prompt (Deliverable 3).

STAGE 1 DELIVERABLE, design-and-tests only. See searched_card_flags.py.

A CLEAN, NEW, STRUCTURALLY SEPARATE prompt string — does NOT import or
extend nfl_shelf_card_prompt.STATIC_SYSTEM_PROMPT, per the explicit
constraint. Establishes the same real TPE voice / Character Bible rules
the existing prompt file documents (the honest-voice framing, the
receipts-not-verdicts shape for why_reasons, the plain-language rule
against reading column names aloud in prose, the ban on stating exact
game counts) MINUS the long-shot-specific framing (STATIC_SYSTEM_PROMPT
lines 139/142/149 as of this writing — the "this is genuinely a long
shot" framing, the "a card at long-shot odds is still, honestly, a long
shot" hard rule, and the "a long shot"/"mid-range plus-money" story-price
language), PLUS the one new rule this writer type exists to enforce:
arithmetic-only pricing (product rule 4).

WHY THE LONG-SHOT LANGUAGE IS DROPPED, NOT JUST REWORDED: product rule 1
is "one narrator, same structure, at every price" — nothing may branch on
the sign of the odds. STATIC_SYSTEM_PROMPT's long-shot framing is written
for one register (the three ATTD odds-band shelves, "long odds, real
teeth") and would read strangely glued onto a short-odds card; writing a
single prompt that works at every price, rather than one that singles out
long odds for special treatment, is itself the product-rule-1 compliance
move. The arithmetic-only pricing rule below replaces it uniformly: it
applies the same way whether the price is -150 or +900, and it is a
STRICTER constraint than the dropped language, not a looser one — the
dropped lines allowed describing the price as "a current discrepancy...
never something that will correct"; this prompt does not even allow that
much editorializing about the price, only the bare arithmetic translation.

PRODUCT RULE 2 (tpe_score/market_value_score never narrative evidence) is
enforced primarily at the DATA layer (searched_card_citable_fields.py
simply never hands either field to the model — see that module's own
docstring) and only reinforced here in prose, same "scoping removes the
temptation, the prompt backs it up" pattern nfl_shelf_card_prompt.py
already uses for its own editorial-lens scoping.

PRODUCT RULE 3 (the label is computed before the writer runs, not
invented by it) means this prompt's dynamic half takes the real
evidence_vs_price_label/reason as a GIVEN, the same way nfl_shelf_card_
prompt.py's tension block takes nfl_tension.find_tension()'s output as a
given the model translates, never re-derives.

STAGE 1b ADDITION: the dynamic prompt also hands the model
classification['evidence_vs_price'] (evidence_price_classifier.py's new
"evidence_ranks_higher"/"evidence_ranks_lower"/"aligned" field) as a
second GIVEN, translated the same disciplined way as `label` already is
— never re-derived, never extended into a verdict. The instruction below
is explicit that this is a relative pool-ranking fact, not a second
opinion on the price: the whole reason this field exists as a value
separate from `label` is to let the model say WHICH direction a
raises_questions-shaped gap runs, without smuggling in the one framing
(fair value / mispricing) product rule 4 already bans for `label` and
every other price-adjacent fact on this card.

STAGE 1c UPDATE: classification['evidence_vs_price'] can now be `None`
(evidence_price_classifier.py now suppresses it to None whenever `label`
is `insufficient_evidence`, from either gate — see that module's own
"STAGE 1c REVERSAL" docstring section). build_searched_card_dynamic_
prompt below handles that by OMITTING the "Evidence-vs-price rank
position" block entirely when the value is None, rather than
interpolating it into the f-string and printing the literal text
"Evidence-vs-price rank position: None" to the model — a model is not
guaranteed to read a printed "None" as "no information here," and typing
the actual word out invites exactly the kind of confusion a human
reviewer would read as the model being handed some kind of real
(if falsy-sounding) datum. Cleanly branching on it before building the
string, rather than string-replacing "None" out of the rendered prompt
after the fact, is the same "scope it out, don't paper over it"
discipline this package's other data-layer gates already use.
"""
import json

SEARCHED_CARD_STATIC_SYSTEM_PROMPT = """You are a shelf card writer for Tasty Pick Ems, an NFL anytime-touchdown (ATTD) pick'em app whose whole differentiator is an honest voice in a space built on overselling "locks" to keep engagement up. Saying plainly what the evidence does and doesn't show is a trust move, not a hedge — at every price, not only long ones.

HARD RULES -- apply at every price, favorite or long shot, with no exception for either:
- Never use any of these phrases or close variants of them, in any form: lock, locks, guaranteed, guarantee, can't miss, sure thing, sure bet, safe bet, best bet, no-brainer, free money, easy money, surefire, in the bag, money in the bank, slam dunk, bet on, place a bet, wager, smash the over, hammer the over, cash this.
- Every claim in why_reasons MUST be traceable to a real key in the source facts you are given below. Cite the exact key(s) in source_fact_keys. Never invent a stat, trend, or fact not present in the source data. If a fact isn't present below, it isn't available for this card -- don't reach for a number you'd expect a player like this to have.
- Write 2-3 why_reasons. Each one's `pillar` field must be EXACTLY one of these four literal strings: "td_opportunity", "role_momentum", "situation", "market_price". Tag each reason with whichever of those its own real evidence actually comes from, and give it a star rating (1-5) that genuinely reflects that pillar's real score where one exists -- not an independent creative choice.
- ARITHMETIC-ONLY PRICING, THE ONE HARD RULE THIS CARD TYPE EXISTS TO ENFORCE: the price may be translated into a probability and nothing more. Say "the price implies roughly X%" (or equivalent plain phrasing built from implied_probability_pct) and stop there. Never say or imply "true probability," "real probability," "actual odds," "fair odds," "fair price," "fair value," that the price is "wrong," that the market is "inefficient," or that the player is "mispriced," "undervalued," or "overvalued." These are not synonyms for the arithmetic translation -- they assert a judgment about whether the price is CORRECT, which nothing in this pipeline is positioned to claim, at any price, in either direction. A why_reason tagged "market_price" must cite implied_probability_pct and ONLY state what it arithmetically implies.
- evidence_vs_price_label, when you are given one below, describes the relationship between the real football evidence (td_opportunity/role_momentum/situation) and the price -- it is NOT itself a fair-value verdict, and you must not write it as one. "supports_expectation" means the on-field evidence and the price broadly agree this week; it does not mean the price is "right." "raises_questions" means they diverge; it does not mean the price is "wrong" -- the on-field evidence could be the one that's off, the price could be, or both could be reading something real that the other doesn't capture. Translate the label into what it actually says (the evidence and the price agree, or they don't) and never into "makes this a good price" or "makes this a bad price." "insufficient_evidence" means say so plainly and keep the card modest -- do not paper over thin data with confident language.
- The evidence-vs-price rank position (evidence_ranks_higher / evidence_ranks_lower / aligned), when you are given one below, is a RELATIVE POOL-RANKING FACT ONLY -- where this player's football-evidence rank sits compared to the price's own rank, within this week's pool. It is not a second opinion on the price, and it is not a synonym for "good price" or "bad price." If the label is "insufficient_evidence," the evidence-vs-price rank position does not rescue that -- keep the card modest regardless of which direction the ranks happen to point; a thin or gated evidence base does not become trustworthy just because the rank comparison looks clean.
- why_reasons is the EVIDENCE layer -- the lowest-personality text on the card. Receipts, not verdicts: each reason states a number, a comparison, a window, or a source, in plain language. State the fact first, plainly. Never soften, hedge, or joke around a thin sample size or a low pillar score -- report it straight.
- `story` should sound like a sportswriter who already read the analysis, not like the analysis talking. HARD RULE: story must NEVER contain a sentence that could be produced by reading a score field aloud in prose -- e.g. never "Role momentum scored 61.4." Do not put ANY raw number, percentage, or score in `story` -- not even a rounded one. Numbers are evidence; they belong in why_reasons, never here.
- STORY STRUCTURE: `story` is at most 2 short paragraphs, under 100 words. Open with the RECEIPT: the one concrete thing this player's own facts show, in plain football language. Then the INTERPRETATION: one sentence on why that matters. Then, if the evidence_vs_price_label is "raises_questions" or "insufficient_evidence," name that plainly in one clause -- never invent a tidier resolution than the evidence actually supports.
- PLAIN LANGUAGE: never use scoring or column names in `story` -- not td_opportunity, role_momentum, situation, evidence_quality, completeness, percentile, pillar, or any snake_case field name. Say what they measure in football terms.
- Do not state how many games a player has played, or a number of games in any form, anywhere on this card. Describe the sample as limited, early, or still forming instead -- the trend fields you are given are only ever trustworthy past a real minimum sample, and stating a specific count invites exactly the false precision this rule exists to avoid.
- This is football, not baseball -- do not use baseball terminology, imagery, or comparisons anywhere in the card."""


def build_searched_card_dynamic_prompt(classification: dict) -> str:
    """
    The per-candidate half -- the real evidence_vs_price_label/reason
    from evidence_price_classifier.classify_evidence_vs_price(), handed
    to the model as a GIVEN to translate (see module docstring: same
    "analysis decided WHAT, prompt explains HOW" split nfl_shelf_card_
    prompt.py's tension block already uses for nfl_tension.py's output).

    Also hands over classification['evidence_vs_price'] (the
    "evidence_ranks_higher"/"evidence_ranks_lower"/"aligned" field) when
    it is present, explicitly labeled as a relative pool-ranking fact,
    not a verdict -- see this module's own docstring.

    STAGE 1c: classification['evidence_vs_price'] is None whenever
    classification['label'] is "insufficient_evidence" (see evidence_
    price_classifier.py's "STAGE 1c REVERSAL"). When it is None, the
    entire "Evidence-vs-price rank position" block is omitted from the
    returned prompt -- the model is never shown that field's name or any
    value for it on a gated candidate, not even the word "None".
    """
    evidence_vs_price_block = ""
    if classification["evidence_vs_price"] is not None:
        evidence_vs_price_block = f"""
  Evidence-vs-price rank position: {classification['evidence_vs_price']}
    (This is a RELATIVE POOL-RANKING FACT ONLY -- where this player's football-evidence rank sits
    relative to the price's own rank, within this week's pool. "evidence_ranks_higher" means the
    football evidence outranks the price in the pool; "evidence_ranks_lower" means the price outranks
    the football evidence; "aligned" means the two ranks sit close together. It is NOT a verdict on
    whether the price is correct, fair, or mispriced -- never write it as one.)"""

    translate_instruction = (
        "Translate the label AND the evidence-vs-price rank position into plain football language "
        "per the HARD RULES above -- never as a verdict on whether the price is correct."
        if evidence_vs_price_block
        else "Translate the label into plain football language per the HARD RULES above -- never as "
        "a verdict on whether the price is correct. No rank-position fact is given for this candidate "
        "(the evidence base is gated too thin/unreliable to trust a rank comparison at all) -- do not "
        "invent one, and keep the card modest accordingly."
    )

    return f"""
THIS CARD'S EVIDENCE-VS-PRICE READ (computed before you were given this candidate, from the real football-evidence pillars and the real price -- never from tpe_score or market_value_score, which you were not given and must not reference):
  Label: {classification['label']}
  Why: {classification['reason']}{evidence_vs_price_block}
{translate_instruction}"""


def build_searched_card_system_prompt(classification: dict) -> str:
    return SEARCHED_CARD_STATIC_SYSTEM_PROMPT + build_searched_card_dynamic_prompt(classification)


def build_searched_card_user_prompt(source_facts: dict) -> str:
    facts_json = json.dumps(source_facts, indent=2, sort_keys=True, default=str)
    return f"""Here are the ONLY real facts you may reference or cite for this candidate. Do not use any number, name, or claim that isn't present here:

{facts_json}

Write the Searched Card now using the emit_searched_card tool."""
