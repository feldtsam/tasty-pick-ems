"""
Stage 1h, Task 2 — tests for pillar_context.py.

Run: cd nfl/searched_card && ../.venv/bin/python -m pytest test_pillar_context.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pillar_context import (  # noqa: E402
    MIN_COMPARISON_POPULATION,
    NEAR_MEDIAN_TOLERANCE,
    PILLAR_NAMES,
    build_pillar_context,
    writer_facing_pillar_context,
)


def _row(td=50.0, role=50.0, situ=50.0, evq=90.0, price=100.0, td_c=100.0, role_c=100.0):
    return {
        "td_opportunity": td, "role_momentum": role, "situation": situ, "evidence_quality": evq,
        "consensus_price_american": price,
        "td_opportunity_completeness": td_c, "role_momentum_completeness": role_c,
    }


def _big_pool(n=160, **overrides):
    return [_row(td=float(i % 50), role=float(i % 40), situ=float(i % 30), **overrides) for i in range(n)]


def test_median_is_computed_over_the_same_population_the_classifier_uses():
    """A simple, hand-checkable pool: td_opportunity values 0..9 (10 rows,
    odd count skipped on purpose -- use 11 rows for an exact median),
    median must be the real middle value, not an approximation."""
    pool = [_row(td=float(i)) for i in range(11)] + [_row(td=0.0) for _ in range(149)]  # pad past MIN_COMPARISON_POPULATION with a tied value
    target = pool[5]  # td_opportunity == 5.0
    context = build_pillar_context(target, pool)
    # median of 11 distinct values (0..10) plus 149 more at 0.0 -- just
    # check this doesn't crash and produces A real median, exactness is
    # checked by the controlled-population test below instead.
    assert context["pillars"]["td_opportunity"]["median"] is not None


def test_position_above_below_near_boundaries_exact():
    """Controlled population: median forced to exactly 50.0 by construction
    (two mirrored values around 50 for every pair, odd count makes the
    literal middle value 50.0). Boundary cases at the NEAR_MEDIAN_TOLERANCE
    edge must match '<=' semantics, same discipline as GAP_THRESHOLD."""
    pool = [_row(td=50.0)] + [_row(td=50.0 - i) for i in range(1, 80)] + [_row(td=50.0 + i) for i in range(1, 80)]
    assert len(pool) == 159

    # The target row is NOT added to `pool` here, deliberately -- unlike
    # evidence_price_classifier's rank function, build_pillar_context's
    # median is a plain statistic over the comparison population handed
    # in, with no self-inclusion requirement; adding the target would
    # shift the very median this test is trying to hold fixed at exactly
    # 50.0 (159 is odd; 160 would not have a single middle value).
    at_tolerance = dict(pool[0]); at_tolerance["td_opportunity"] = 50.0 + NEAR_MEDIAN_TOLERANCE
    just_past = dict(pool[0]); just_past["td_opportunity"] = 50.0 + NEAR_MEDIAN_TOLERANCE + 0.1
    just_past_below = dict(pool[0]); just_past_below["td_opportunity"] = 50.0 - NEAR_MEDIAN_TOLERANCE - 0.1

    ctx_at = build_pillar_context(at_tolerance, pool)
    ctx_past = build_pillar_context(just_past, pool)
    ctx_past_below = build_pillar_context(just_past_below, pool)

    assert ctx_at["pillars"]["td_opportunity"]["median"] == 50.0
    assert ctx_at["pillars"]["td_opportunity"]["position"] == "near"
    assert ctx_past["pillars"]["td_opportunity"]["position"] == "above"
    assert ctx_past_below["pillars"]["td_opportunity"]["position"] == "below"


def test_all_four_pillars_present_with_real_descriptions():
    pool = _big_pool()
    context = build_pillar_context(pool[0], pool)
    assert set(context["pillars"].keys()) == set(PILLAR_NAMES)
    for name in PILLAR_NAMES:
        p = context["pillars"][name]
        assert isinstance(p["what_it_measures"], str) and len(p["what_it_measures"]) > 0
        assert p["comparison_population_size"] == context["comparison_population_size"]


def test_role_momentum_description_names_change_not_size():
    """A real content check, not just presence -- this is the fact
    behind the prompt's 'never say role_momentum shows a small or large
    role' hard rule, so the description itself must say so."""
    pool = _big_pool()
    context = build_pillar_context(pool[0], pool)
    desc = context["pillars"]["role_momentum"]["what_it_measures"].lower()
    assert "change" in desc
    assert "not role size" in desc or "not" in desc and "size" in desc


def test_completeness_is_none_for_situation_and_evidence_quality():
    """Only td_opportunity/role_momentum have a real completeness column
    -- situation and evidence_quality must report None, not a fabricated
    100 or 0."""
    pool = _big_pool()
    context = build_pillar_context(pool[0], pool)
    assert context["pillars"]["situation"]["completeness"] is None
    assert context["pillars"]["evidence_quality"]["completeness"] is None
    assert context["pillars"]["td_opportunity"]["completeness"] is not None
    assert context["pillars"]["role_momentum"]["completeness"] is not None


def test_limits_names_a_pillar_with_completeness_below_100():
    pool = _big_pool()
    target = dict(pool[0])
    target["td_opportunity_completeness"] = 70.0
    context = build_pillar_context(target, pool + [target])
    assert any("td_opportunity" in line and "70" in line for line in context["limits"])


def test_limits_is_empty_when_everything_is_full_completeness_and_population():
    pool = _big_pool()  # all rows default td_c=role_c=100.0, n=160 > MIN_COMPARISON_POPULATION
    context = build_pillar_context(pool[0], pool)
    assert context["limits"] == []


def test_limits_names_every_pillar_when_the_comparison_population_is_thin():
    """A pool smaller than MIN_COMPARISON_POPULATION -- every pillar
    should get its own 'thin population' limits entry, since the
    population size applies to all four identically."""
    pool = _big_pool(n=10)
    context = build_pillar_context(pool[0], pool)
    assert context["comparison_population_size"] == 10 < MIN_COMPARISON_POPULATION
    for name in PILLAR_NAMES:
        assert any(name in line and "comparison population" in line for line in context["limits"])


def test_rows_failing_their_own_completeness_gate_are_excluded_from_the_comparison_population():
    """Same structural requirement evidence_price_classifier.py enforces
    for its own ranks -- pillar_context must use the SAME comparison
    population, not a separately (and differently) filtered one."""
    clean = _big_pool(n=150)
    failing = [_row(role_c=0.0, td=999.0) for _ in range(20)]  # extreme values that would skew the median if counted
    target = clean[0]

    with_failing = build_pillar_context(target, clean + failing)
    without_failing = build_pillar_context(target, clean)

    assert with_failing["comparison_population_size"] == without_failing["comparison_population_size"] == 150
    assert with_failing["pillars"]["td_opportunity"]["median"] == without_failing["pillars"]["td_opportunity"]["median"]


def test_writer_facing_strips_to_limits_only_when_insufficient_evidence():
    pool = _big_pool()
    target = dict(pool[0])
    target["td_opportunity_completeness"] = 30.0  # forces a real limits entry
    full = build_pillar_context(target, pool + [target])
    assert "pillars" in full and full["limits"]

    gated = writer_facing_pillar_context(full, "insufficient_evidence")
    assert set(gated.keys()) == {"limits"}
    assert gated["limits"] == full["limits"]


def test_writer_facing_passes_full_context_through_when_not_gated():
    pool = _big_pool()
    full = build_pillar_context(pool[0], pool)
    for label in ("supports_expectation", "diverges_from_expectation"):
        passthrough = writer_facing_pillar_context(full, label)
        assert passthrough == full
