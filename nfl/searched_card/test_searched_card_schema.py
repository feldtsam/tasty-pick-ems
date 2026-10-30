"""
Stage 1c, Task 2 — a focused check that searched_card_schema.build_
searched_card() still works correctly now that evidence_price_classifier
can hand it classification["evidence_vs_price"] == None (the new Stage
1c gating behavior -- see evidence_price_classifier.py's "STAGE 1c
REVERSAL"). No test file covered build_searched_card directly before
this revision; this one exists specifically to verify the None case the
task asked to confirm, not to duplicate coverage of the rest of that
function's behavior (schema validation, review_status, etc.), which
remains out of scope here.

Run: cd nfl/searched_card && ../.venv/bin/python -m pytest test_searched_card_schema.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from searched_card_schema import build_searched_card  # noqa: E402


def _llm_output() -> dict:
    return {
        "title": "A real title",
        "story": "x" * 70,
        "why_reasons": [
            {"pillar": "td_opportunity", "stars": 3, "reason_text": "r1", "source_fact_keys": ["k1"]},
            {"pillar": "situation", "stars": 4, "reason_text": "r2", "source_fact_keys": ["k2"]},
        ],
    }


def _gated_classification() -> dict:
    return {
        "label": "insufficient_evidence",
        "reason": "gated for this test",
        "evidence_vs_price": None,
        "evidence_rank_pct": 50.0,
        "price_rank_pct": 50.0,
        "implied_probability_pct": 42.3,
    }


def test_build_searched_card_carries_none_evidence_vs_price_through_unmodified():
    """The thing Task 2 asked to confirm: a None evidence_vs_price must
    reach the assembled card's own evidence_vs_price field as None,
    exactly as given -- not coerced to a string, not defaulted to
    "aligned" or any other value that would misrepresent a gated row as
    having a real rank-position fact."""
    card = build_searched_card(
        _llm_output(), _gated_classification(), player_id="p1", shelf_context="test_shelf",
    )
    assert card["evidence_vs_price"] is None
    assert card["evidence_vs_price_label"] == "insufficient_evidence"
    # the raw rank numbers are unaffected by the None-suppression --
    # still real values on the assembled card, same as on the classifier's
    # own return dict.
    assert card["evidence_rank_pct"] == 50.0
    assert card["price_rank_pct"] == 50.0


def test_build_searched_card_still_carries_a_real_evidence_vs_price_when_not_gated():
    """Sanity check alongside the None case above: an ungated row's real
    evidence_vs_price value must still pass through unchanged -- the None
    handling must not have accidentally made this field None-ish or
    stringified in the non-gated path either."""
    classification = dict(_gated_classification())
    classification["label"] = "supports_expectation"
    classification["evidence_vs_price"] = "aligned"
    card = build_searched_card(
        _llm_output(), classification, player_id="p1", shelf_context="test_shelf",
    )
    assert card["evidence_vs_price"] == "aligned"
