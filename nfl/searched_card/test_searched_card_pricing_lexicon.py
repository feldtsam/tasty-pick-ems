"""
Deliverable 4 — pytest tests for searched_card_pricing_lexicon.py.

Run: cd nfl/searched_card && ../.venv/bin/python -m pytest test_searched_card_pricing_lexicon.py -v
(or: ../.venv/bin/pytest test_searched_card_pricing_lexicon.py -v)

Zero network, zero API calls, zero DB access -- pure string/regex checks
against a module with no I/O of its own.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from searched_card_pricing_lexicon import (  # noqa: E402
    find_pricing_claim_violations,
    find_value_verdict_violations,
    validate_comparison_basis_cited,
    validate_price_citation_framing,
    validate_searched_card_pricing_claims,
)

# ---------------------------------------------------------------------------
# find_pricing_claim_violations -- honest phrasings must pass (return []).
# ---------------------------------------------------------------------------

def test_honest_arithmetic_framing_passes():
    assert find_pricing_claim_violations("The price implies roughly 24% for this week.") == []


def test_honest_arithmetic_framing_with_mid_range_price_passes():
    assert find_pricing_claim_violations("At +175, the price implies a touch under 36%.") == []


def test_honest_arithmetic_framing_short_price_passes():
    assert find_pricing_claim_violations("A -150 price implies roughly 60%, well inside what the role alone would suggest.") == []


def test_plain_receipt_with_no_pricing_language_passes():
    assert find_pricing_claim_violations("His snap share has been climbing for three straight weeks.") == []


# ---------------------------------------------------------------------------
# find_pricing_claim_violations -- dishonest phrasings must fail (non-empty).
# ---------------------------------------------------------------------------

def test_true_probability_blocked():
    assert "true probability" in find_pricing_claim_violations("His true probability of scoring is closer to 30%.")


def test_real_probability_blocked():
    assert "real probability" in find_pricing_claim_violations("The real probability here is higher than the price shows.")


def test_actual_probability_blocked():
    assert "actual probability" in find_pricing_claim_violations("The actual probability is above what's implied.")


def test_real_odds_are_blocked():
    assert "real odds are" in find_pricing_claim_violations("The real odds are shorter than what's posted.")


def test_actual_odds_are_blocked():
    assert "actual odds are" in find_pricing_claim_violations("The actual odds are much better than listed.")


def test_fair_odds_blocked():
    assert "fair odds" in find_pricing_claim_violations("Fair odds here would be closer to even money.")


def test_fair_price_blocked():
    assert "fair price" in find_pricing_claim_violations("A fair price would be shorter than this.")


def test_fair_value_blocked():
    assert "fair value" in find_pricing_claim_violations("This isn't fair value at this number.")


def test_market_is_wrong_blocked():
    assert "the market is wrong" in find_pricing_claim_violations("The market is wrong about his role this week.")


def test_market_is_inefficient_blocked():
    assert "market is inefficient" in find_pricing_claim_violations("This market is inefficient on backup tight ends.")


def test_mispriced_blocked():
    assert "mispriced" in find_pricing_claim_violations("He looks mispriced given the role change.")


def test_mispricing_blocked():
    assert "mispricing" in find_pricing_claim_violations("This is a clear mispricing by the books.")


def test_undervalued_blocked():
    assert "undervalued" in find_pricing_claim_violations("The market has him undervalued given the role.")


def test_overvalued_blocked():
    assert "overvalued" in find_pricing_claim_violations("He looks overvalued at this number.")


def test_underpriced_blocked():
    assert "underpriced" in find_pricing_claim_violations("This price is underpriced relative to his workload.")


def test_overpriced_blocked():
    assert "overpriced" in find_pricing_claim_violations("At this number he's overpriced for what he's shown.")


# ---------------------------------------------------------------------------
# Tricky near-miss cases -- the ones that need real judgment, not a
# keyword match, to get right.
# ---------------------------------------------------------------------------

def test_value_used_in_non_pricing_sense_passes():
    """'value' on its own, used editorially rather than as a pricing
    verdict, must NOT false-positive -- the explicit near-miss case named
    in the task."""
    assert find_pricing_claim_violations("This is a value pick for a different reason entirely.") == []


def test_worth_a_look_style_editorial_word_passes():
    assert find_pricing_claim_violations("There's real value in how often he's seeing the ball inside the ten.") == []


def test_true_used_in_non_probability_sense_passes():
    """'true' alone, not paired with 'probability', is ordinary language
    ('a true threat') and must not trip the 'true probability' phrase."""
    assert find_pricing_claim_violations("He looks like a true threat near the goal line this week.") == []


def test_price_used_without_fair_or_true_passes():
    assert find_pricing_claim_violations("The price moved this week, but the role is what carries this card.") == []


def test_implied_probability_reintroducing_mispricing_judgment_fails():
    """The task's own tricky example: starts from the allowed arithmetic
    frame ('the implied probability suggests') but then reintroduces a
    market-judgment claim ('undervalues') on top of it. This must still
    fail -- using the honest frame as a launching point for a dishonest
    conclusion is exactly the failure mode this gate exists to catch, not
    a loophole the arithmetic framing should excuse."""
    text = "The implied probability suggests the market undervalues him given his role this week."
    violations = find_pricing_claim_violations(text)
    assert "undervalued" not in violations  # the exact word used is "undervalues"
    assert "undervalues" in violations


def test_overvalues_verb_form_also_blocked():
    assert "overvalues" in find_pricing_claim_violations("The price overvalues him relative to the role.")


# ---------------------------------------------------------------------------
# validate_price_citation_framing -- a number cited from the price field
# passes grounding ONLY when framed as implied probability.
# ---------------------------------------------------------------------------

def test_price_citation_with_implies_framing_passes():
    why_reasons = [{
        "pillar": "market_price", "stars": 3,
        "reason_text": "The price implies roughly 24% for this week.",
        "source_fact_keys": ["implied_probability_pct"],
    }]
    assert validate_price_citation_framing(why_reasons, {"implied_probability_pct": 24.0}) == []


def test_price_citation_with_no_framing_at_all_fails():
    """Same cited field, same real number, but stated as a bare fact with
    no arithmetic framing anywhere in the text -- must fail even though
    no BLOCKED_PRICING_PHRASES phrase is present either."""
    why_reasons = [{
        "pillar": "market_price", "stars": 3,
        "reason_text": "He scores a touchdown 24% of the time.",
        "source_fact_keys": ["implied_probability_pct"],
    }]
    violations = validate_price_citation_framing(why_reasons, {"implied_probability_pct": 24.0})
    assert len(violations) == 1
    assert violations[0]["reason_index"] == 0


def test_price_citation_with_price_suggests_framing_passes():
    why_reasons = [{
        "pillar": "market_price", "stars": 2,
        "reason_text": "The price suggests a touch under 24% this week.",
        "source_fact_keys": ["implied_probability_pct"],
    }]
    assert validate_price_citation_framing(why_reasons, {"implied_probability_pct": 24.0}) == []


def test_reason_not_citing_price_field_is_untouched_by_framing_check():
    """A reason that never cites implied_probability_pct at all is simply
    not this check's concern, no matter how it's worded."""
    why_reasons = [{
        "pillar": "td_opportunity", "stars": 4,
        "reason_text": "His touchdown opportunity grades out well above average this week.",
        "source_fact_keys": ["td_opportunity"],
    }]
    assert validate_price_citation_framing(why_reasons, {"td_opportunity": 70.0}) == []


def test_only_the_reason_that_cites_price_is_checked_not_the_whole_card():
    """Scoping check: one reason uses 'implies' correctly elsewhere on the
    card, but a DIFFERENT reason cites implied_probability_pct with no
    framing of its own -- the second reason must still fail. Honest
    framing in one reason must never launder an unframed citation in
    another."""
    why_reasons = [
        {
            "pillar": "td_opportunity", "stars": 4,
            "reason_text": "This implies a real role, backed by touches inside the ten.",
            "source_fact_keys": ["td_opportunity"],
        },
        {
            "pillar": "market_price", "stars": 2,
            "reason_text": "He is a 24% scorer this week.",
            "source_fact_keys": ["implied_probability_pct"],
        },
    ]
    violations = validate_price_citation_framing(why_reasons, {"td_opportunity": 70.0, "implied_probability_pct": 24.0})
    assert len(violations) == 1
    assert violations[0]["reason_index"] == 1


# ---------------------------------------------------------------------------
# validate_searched_card_pricing_claims -- the aggregate, over a full draft.
# ---------------------------------------------------------------------------

def test_aggregate_clean_card_has_no_issues():
    output = {
        "title": "The Role Explains the Attention",
        "story": "He is seeing more work inside the ten over a real stretch, and the matchup is generous. The price implies roughly 36% this week, which lines up with a role that is still forming.",
        "why_reasons": [
            {"pillar": "td_opportunity", "stars": 4, "reason_text": "His touchdown opportunity reading is well above average.", "source_fact_keys": ["td_opportunity"]},
            {"pillar": "market_price", "stars": 3, "reason_text": "The price implies roughly 36% this week.", "source_fact_keys": ["implied_probability_pct"]},
        ],
        "_source_facts": {"td_opportunity": 70.0, "implied_probability_pct": 36.0},
    }
    assert validate_searched_card_pricing_claims(output) == []


def test_aggregate_catches_a_blocked_phrase_in_the_story():
    output = {
        "title": "A Role Worth Watching",
        "story": "He is seeing more work inside the ten, and the market is wrong about how often that leads to scores.",
        "why_reasons": [
            {"pillar": "td_opportunity", "stars": 4, "reason_text": "His touchdown opportunity reading is well above average.", "source_fact_keys": ["td_opportunity"]},
        ],
        "_source_facts": {"td_opportunity": 70.0},
    }
    issues = validate_searched_card_pricing_claims(output)
    assert any(i["check"] == "pricing_claim" and "the market is wrong" in i["phrases"] for i in issues)


def test_aggregate_catches_unframed_price_citation():
    output = {
        "title": "The Role Explains the Attention",
        "story": "He is seeing more work inside the ten this stretch.",
        "why_reasons": [
            {"pillar": "market_price", "stars": 3, "reason_text": "He is a 36% scorer this week.", "source_fact_keys": ["implied_probability_pct"]},
        ],
        "_source_facts": {"implied_probability_pct": 36.0},
    }
    issues = validate_searched_card_pricing_claims(output)
    assert any(i["check"] == "price_citation_framing" for i in issues)


# ---------------------------------------------------------------------------
# STAGE 1h, Task 3 -- find_value_verdict_violations. A below/above-median
# pillar reading must never become a value/pricing verdict. Near-miss
# cases run BOTH ways: the honest pillar-standing phrasing must pass, and
# ordinary non-price uses of "value"/"opportunity"/"too low"/"too high"
# must also pass -- only the specific compound verdict phrasings fail.
# ---------------------------------------------------------------------------

def test_honest_pillar_standing_statement_passes_the_value_verdict_check():
    """The exact example phrasing the task itself gives as what must stay
    allowed."""
    text = "His recent red-zone touchdown production ranks below the median of this week's comparison group."
    assert find_value_verdict_violations(text) == []


def test_poor_value_blocked():
    assert "poor value" in find_value_verdict_violations("That makes him poor value at this price.")


def test_bad_value_blocked():
    assert "bad value" in find_value_verdict_violations("A bad value play this week.")


def test_good_value_blocked():
    """Banned in BOTH directions -- a below-median reading must not
    become 'poor value' and an above-median reading must not become
    'good value' either; the hard rule bans turning the comparison into
    a verdict at all, not just the negative direction."""
    assert "good value" in find_value_verdict_violations("That makes him good value at this price.")


def test_a_bargain_blocked():
    assert "a bargain" in find_value_verdict_violations("He is a bargain at these odds.")


def test_the_price_is_too_high_blocked():
    assert "the price is too high" in find_value_verdict_violations("The price is too high given his recent production.")


def test_the_price_is_too_low_blocked():
    assert "the price is too low" in find_value_verdict_violations("The price is too low for what the role shows.")


def test_a_betting_opportunity_blocked():
    assert "a betting opportunity" in find_value_verdict_violations("This looks like a betting opportunity this week.")


# --- near-miss, the OTHER direction: ordinary football language, not a
# value verdict, must NOT be flagged ---

def test_non_price_too_low_passes():
    """'too low' on its own, with no price/odds word nearby, is ordinary
    football language (a role claim), not a value verdict."""
    text = "His touch share has been too low to count on week to week."
    assert find_value_verdict_violations(text) == []


def test_bare_opportunity_in_football_sense_passes():
    """Bare 'opportunity' -- including the exact phrasing a human would
    use about td_opportunity itself -- must stay legal; only the specific
    betting/value-compound phrasings are banned (see module docstring)."""
    text = "This is a real opportunity for him to see more red-zone work down the stretch."
    assert find_value_verdict_violations(text) == []


def test_red_zone_opportunity_phrase_passes():
    text = "His red-zone opportunity has been building for three weeks."
    assert find_value_verdict_violations(text) == []


def test_bare_value_word_in_non_pricing_sense_passes():
    """Per the original product rule, bare 'value' stays legal -- only
    the specific compound verdict phrases (poor/bad/good/great value) are
    blocked."""
    text = "This is a value pick for a completely different reason -- his role, not his price."
    assert find_value_verdict_violations(text) == []


# ---------------------------------------------------------------------------
# STAGE 1h, Task 3 -- validate_comparison_basis_cited. Scoped to `story`.
# ---------------------------------------------------------------------------

def test_comparison_basis_cited_passes_when_the_group_is_named():
    story = "His recent red-zone touchdown production ranks below the median of this week's comparison group. The matchup is a real question mark too."
    assert validate_comparison_basis_cited(story) == []


def test_comparison_basis_missing_is_flagged():
    story = "His recent red-zone touchdown production ranks below the median. The matchup is a real question mark too."
    violations = validate_comparison_basis_cited(story)
    assert len(violations) == 1
    assert "ranks below the median" in violations[0]["sentence"]


def test_median_word_without_comparison_group_also_flagged():
    story = "He sits right at the median, for what that is worth."
    violations = validate_comparison_basis_cited(story)
    assert len(violations) == 1


def test_only_the_offending_sentence_is_named_not_the_whole_story():
    """A story with ONE clean, basis-stated sentence and ONE bare one --
    only the bare one should be flagged, proving this is scoped per
    sentence, not per story."""
    story = (
        "His recent production ranks below the median of this week's comparison group. "
        "His role also ranks near the bottom of the group this week."
    )
    violations = validate_comparison_basis_cited(story)
    assert len(violations) == 1
    assert "His role also ranks near the bottom" in violations[0]["sentence"]


def test_story_with_neither_median_nor_ranks_is_clean():
    story = "He has been quiet in the red zone for three straight weeks, and the matchup does not help."
    assert validate_comparison_basis_cited(story) == []


def test_aggregate_catches_a_value_verdict_phrase():
    output = {
        "title": "The Role Explains the Attention",
        "story": "That makes him poor value at this price, even with a tough matchup.",
        "why_reasons": [
            {"pillar": "situation", "stars": 2, "reason_text": "The matchup is difficult this week.", "source_fact_keys": ["situation"]},
        ],
        "_source_facts": {"situation": 20.0},
    }
    issues = validate_searched_card_pricing_claims(output)
    assert any(i["check"] == "value_verdict" and "poor value" in i["phrases"] for i in issues)


def test_aggregate_catches_a_missing_comparison_basis():
    output = {
        "title": "The Role Explains the Attention",
        "story": "His recent production ranks below the median this week, but the role is real.",
        "why_reasons": [
            {"pillar": "td_opportunity", "stars": 2, "reason_text": "Recent production has been thin.", "source_fact_keys": ["td_opportunity"]},
        ],
        "_source_facts": {"td_opportunity": 30.0},
    }
    issues = validate_searched_card_pricing_claims(output)
    assert any(i["check"] == "comparison_basis" for i in issues)
