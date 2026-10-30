"""
Stage 2 — tests for searched_card_writer_entry.generate_searched_card_draft.

MOCKED ANTHROPIC CLIENT ONLY. Every test here patches
searched_card_writer_entry._call_claude directly (the one function that
makes a real network call) — nothing in this file makes, or could make,
a real request to api.anthropic.com. SEARCHED_CARD_WRITER_ENABLED is
monkeypatched True only for the duration of each test that needs the
writer to actually run past the flag check; the real flag in
searched_card_flags.py is never modified by this file.

Run: cd nfl/searched_card && ../.venv/bin/python -m pytest test_searched_card_writer_entry.py -v
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import searched_card_writer_entry as entry  # noqa: E402


def _row(td=70.0, role=70.0, situ=70.0, evq=90.0, price=-150.0, td_c=100.0, role_c=100.0, **extra):
    row = {
        "player_id": "p1",
        "player_name": "Test Player",
        "posteam": "SF",
        "position_group": "RB",
        "td_opportunity": td,
        "role_momentum": role,
        "situation": situ,
        "evidence_quality": evq,
        "consensus_price_american": price,
        "consensus_implied_probability": 0.6,
        "td_opportunity_completeness": td_c,
        "role_momentum_completeness": role_c,
    }
    row.update(extra)
    return row


def _big_pool(n=160, **overrides):
    """A pool well past MIN_POOL_SIZE (150), with varied, non-gated rows
    -- same shaping convention test_pillar_context.py's own _big_pool
    already uses, so a row drawn from this pool classifies as
    supports_expectation or diverges_from_expectation, never
    insufficient_evidence."""
    return [
        _row(td=float(i % 50), role=float(i % 40), situ=float(i % 30), price=-110.0 - i, **overrides)
        for i in range(n)
    ]


def _small_pool(n=5, **overrides):
    """Well under MIN_POOL_SIZE (150) -- deterministically gates every
    row drawn from it to insufficient_evidence via the pool-size gate,
    the simplest real way to exercise the 1-why_reason exception."""
    return [_row(td=float(i), role=float(i), situ=float(i), price=-110.0 - i, **overrides) for i in range(n)]


def _ready_llm_output():
    return {
        "title": "A Real Title Here",
        "story": "x" * 70,
        "why_reasons": [
            {
                "pillar": "td_opportunity",
                "stars": 3,
                "reason_text": "His recent red-zone touchdown production ranks above the median among "
                               "this week's running backs, receivers and tight ends.",
                "source_fact_keys": ["td_opportunity"],
            },
            {
                "pillar": "market_price",
                "stars": 3,
                "reason_text": "The price implies roughly 60% likelihood to score anytime.",
                "source_fact_keys": ["implied_probability_pct"],
            },
        ],
    }


def _one_reason_llm_output():
    """Valid for an insufficient_evidence card specifically -- exactly 1
    why_reason, the market_price one (the one fact that label never
    withholds), per illustrative_sample_cards.md's Card E proposal."""
    return {
        "title": "Not Enough Here To Read",
        "story": "There isn't enough recent usage data to read this one, so this stays modest on purpose.",
        "why_reasons": [
            {
                "pillar": "market_price",
                "stars": 2,
                "reason_text": "The price implies roughly 60% likelihood to score anytime.",
                "source_fact_keys": ["implied_probability_pct"],
            },
        ],
    }


# ---------------------------------------------------------------------------
# Flag off
# ---------------------------------------------------------------------------

def test_flag_off_raises_before_touching_any_row(monkeypatch):
    """The real, un-mocked flag state (False) must raise immediately, and
    must do so before classify_evidence_vs_price (or anything else) is
    ever called -- the same guarantee Stage 1 already had, now re-checked
    against the real Stage 2 implementation."""
    assert entry.SEARCHED_CARD_WRITER_ENABLED is False
    with patch.object(entry, "classify_evidence_vs_price") as mock_classify:
        with pytest.raises(RuntimeError, match="SEARCHED_CARD_WRITER_ENABLED is False"):
            entry.generate_searched_card_draft(_row(), _big_pool(), "fake-api-key")
        mock_classify.assert_not_called()


# ---------------------------------------------------------------------------
# Ready
# ---------------------------------------------------------------------------

def test_ready_returns_assembled_card(monkeypatch):
    monkeypatch.setattr(entry, "SEARCHED_CARD_WRITER_ENABLED", True)
    pool = _big_pool()
    row = pool[0]
    with patch.object(entry, "_call_claude", return_value=_ready_llm_output()) as mock_call:
        result = entry.generate_searched_card_draft(row, pool, "fake-api-key", shelf_context="test_shelf")

    mock_call.assert_called_once()
    assert result["status"] == "ready"
    assert result["card"]["title"] == "A Real Title Here"
    assert result["card"]["writer_type"] == "searched_card"
    assert result["classification"]["label"] in ("supports_expectation", "diverges_from_expectation")
    assert "pillar_context" in result


def test_ready_with_exactly_one_reason_on_an_insufficient_evidence_card(monkeypatch):
    """The Stage 2 schema exception, exercised end to end: a card gated to
    insufficient_evidence (via the pool-size gate here, the simplest real
    trigger) with exactly 1 why_reason must pass validation and reach
    'ready' -- the usual 2-3 requirement must NOT apply to this label."""
    monkeypatch.setattr(entry, "SEARCHED_CARD_WRITER_ENABLED", True)
    pool = _small_pool()
    row = pool[0]
    with patch.object(entry, "_call_claude", return_value=_one_reason_llm_output()):
        result = entry.generate_searched_card_draft(row, pool, "fake-api-key", shelf_context="test_shelf")

    assert result["classification"]["label"] == "insufficient_evidence"
    assert result["status"] == "ready"
    assert len(result["card"]["why_reasons"]) == 1


# ---------------------------------------------------------------------------
# Validator rejection
# ---------------------------------------------------------------------------

def test_validator_rejection_unframed_price_citation(monkeypatch):
    """A why_reason that cites implied_probability_pct without any
    arithmetic-only framing ('the price implies...') must be rejected --
    this exercises validate_searched_card_pricing_claims specifically,
    one of the real validators this task asked to wire up."""
    monkeypatch.setattr(entry, "SEARCHED_CARD_WRITER_ENABLED", True)
    pool = _big_pool()
    row = pool[0]
    bad_output = _ready_llm_output()
    bad_output["why_reasons"][1]["reason_text"] = "He scores anytime touchdowns roughly 60% of the time."

    with patch.object(entry, "_call_claude", return_value=bad_output):
        result = entry.generate_searched_card_draft(row, pool, "fake-api-key", shelf_context="test_shelf")

    assert result["status"] == "validator_rejected"
    assert any(issue["check"] == "price_citation_framing" for issue in result["validation_issues"])
    assert "card" not in result


def test_validator_rejection_schema_shape_wrong_reason_count(monkeypatch):
    """Defense-in-depth re-check: a non-gated card (2-3 required) that
    somehow comes back with only 1 why_reason must be rejected on schema
    shape, independent of the price/value-verdict/comparison-basis checks."""
    monkeypatch.setattr(entry, "SEARCHED_CARD_WRITER_ENABLED", True)
    pool = _big_pool()
    row = pool[0]
    bad_output = _ready_llm_output()
    bad_output["why_reasons"] = bad_output["why_reasons"][:1]

    with patch.object(entry, "_call_claude", return_value=bad_output):
        result = entry.generate_searched_card_draft(row, pool, "fake-api-key", shelf_context="test_shelf")

    assert result["status"] == "validator_rejected"
    assert any(issue["check"] == "schema_shape" for issue in result["validation_issues"])


# ---------------------------------------------------------------------------
# Transport failure
# ---------------------------------------------------------------------------

def test_transport_failure_from_a_raised_exception(monkeypatch):
    """Any exception _call_claude raises -- network error, timeout,
    non-2xx, truncated response, missing tool_use block -- must be caught
    HERE, inside generate_searched_card_draft, and turned into a clean
    transport_failure result, never left to propagate to the caller. This
    is the real behavior the module docstring's 'wrap the Anthropic call
    in try/except inside this function' requirement checks."""
    monkeypatch.setattr(entry, "SEARCHED_CARD_WRITER_ENABLED", True)
    pool = _big_pool()
    row = pool[0]

    with patch.object(entry, "_call_claude", side_effect=ConnectionError("real network failure, simulated")):
        result = entry.generate_searched_card_draft(row, pool, "fake-api-key", shelf_context="test_shelf")

    assert result["status"] == "transport_failure"
    assert "real network failure, simulated" in result["error"]
    assert "classification" in result
    assert "card" not in result
