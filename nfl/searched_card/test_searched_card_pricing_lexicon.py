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
