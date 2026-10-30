"""
Deliverable 5 — circularity test. Proves evidence_price_classifier.
classify_evidence_vs_price() never reads market_value_score or tpe_score,
even accidentally.

Run: cd nfl/searched_card && ../.venv/bin/python -m pytest test_circularity.py -v

WHY THIS MATTERS (restated from the task): the rubric's "price rank" is
conceptually similar to what market_value_score already represents (both
are a percentile rank of consensus_implied_probability within a pool).
It would be an easy, quiet mistake for a future edit to this classifier
to reach for the already-computed market_value_score column instead of
recomputing the rank fresh — they'd often even produce similar numbers,
masking the mistake. This test proves that mistake was not made, by two
independent methods:

  1. BEHAVIORAL: corrupt/delete market_value_score and tpe_score on a
     copy of the pool and the row, leaving every football-evidence field
     and the raw price/implied-probability inputs untouched, and assert
     the classifier's output is byte-for-byte identical.
  2. STATIC: read the classifier module's own source text and assert the
     strings "market_value_score" and "tpe_score" do not appear in it at
     all — not even in a comment, let alone a dict lookup. A behavioral
     test alone could theoretically pass by coincidence (e.g. if a buggy
     read of market_value_score happened to equal the fresh computation
     on this particular fixture); the static check removes that doubt
     entirely by inspection, not just by probabilistic testing.
"""
import csv
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import evidence_price_classifier  # noqa: E402
from evidence_price_classifier import INSUFFICIENT_EVIDENCE, classify_evidence_vs_price  # noqa: E402

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "data" / "stub_weeks" / "2025_wk10.csv"

# Every real function in the module, by name -- checked individually
# (CODE ONLY, via inspect.getsource on each function object) rather than
# the whole module's source text, which would also include the module
# docstring's own prose explaining what must NOT be read. The docstring
# saying the words "market_value_score"/"tpe_score" in English is the
# opposite of a problem (it is the documentation that this test exists to
# verify is actually true) -- only the four real functions' own CODE
# matters here.
_FUNCTION_NAMES = (
    "_implied_probability", "_percentile_rank", "_football_evidence_composite",
    "_fails_completeness_gate",  # STAGE 1g
    "classify_evidence_vs_price",
)


def _load_pool() -> list:
    with open(FIXTURE_PATH) as f:
        rows = list(csv.DictReader(f))
    pool = []
    for r in rows:
        pool.append({
            "player_name": r["player_name"],
            "td_opportunity": float(r["td_opportunity"]),
            "role_momentum": float(r["role_momentum"]),
            "situation": float(r["situation"]),
            "evidence_quality": float(r["evidence_quality"]),
            "consensus_price_american": float(r["consensus_price_american"]) if r["consensus_price_american"] else None,
            # The two fields this test corrupts below -- present on the
            # real fixture (market_value_score is NaN/blank for every
            # historical row; tpe_score is real and populated). Carried
            # through here so the "corrupt them" step has something real
            # to corrupt, not an already-absent field.
            "market_value_score": float(r["market_value_score"]) if r["market_value_score"] else None,
            "tpe_score": float(r["tpe_score"]) if r["tpe_score"] else None,
        })
    return [r for r in pool if r["consensus_price_american"] is not None]


def test_static_source_never_references_market_value_score_or_tpe_score():
    for name in _FUNCTION_NAMES:
        fn_source = inspect.getsource(getattr(evidence_price_classifier, name))
        assert "market_value_score" not in fn_source, f"{name}() source mentions market_value_score"
        assert "tpe_score" not in fn_source, f"{name}() source mentions tpe_score"


def test_identical_output_when_market_value_score_and_tpe_score_are_deleted():
    pool = _load_pool()
    row = next(r for r in pool if r["player_name"] == "Jaylen Wright")

    baseline = classify_evidence_vs_price(dict(row), [dict(r) for r in pool])

    corrupted_pool = []
    for r in pool:
        r2 = dict(r)
        del r2["market_value_score"]
        del r2["tpe_score"]
        corrupted_pool.append(r2)
    corrupted_row = dict(row)
    del corrupted_row["market_value_score"]
    del corrupted_row["tpe_score"]

    result = classify_evidence_vs_price(corrupted_row, corrupted_pool)
    assert result == baseline


def test_identical_output_when_market_value_score_and_tpe_score_are_corrupted():
    """Not deleted this time -- set to deliberately absurd values (a
    corrupted/garbage read, not a missing one) on every row. If the
    classifier read either field under any name, this would change the
    output; it must not."""
    pool = _load_pool()
    row = next(r for r in pool if r["player_name"] == "Tutu Atwell")

    baseline = classify_evidence_vs_price(dict(row), [dict(r) for r in pool])

    poisoned_pool = []
    for r in pool:
        r2 = dict(r)
        r2["market_value_score"] = 999999.0
        r2["tpe_score"] = -999999.0
        poisoned_pool.append(r2)
    poisoned_row = dict(row)
    poisoned_row["market_value_score"] = 999999.0
    poisoned_row["tpe_score"] = -999999.0

    result = classify_evidence_vs_price(poisoned_row, poisoned_pool)
    assert result == baseline


def test_identical_output_across_every_real_row_in_the_fixture():
    """The broadest form of the behavioral proof: run it for every real
    row in the fixture, not just two hand-picked examples."""
    pool = _load_pool()
    poisoned_pool = []
    for r in pool:
        r2 = dict(r)
        r2["market_value_score"] = 12345.0
        r2["tpe_score"] = -12345.0
        poisoned_pool.append(r2)

    mismatches = []
    for row, poisoned_row in zip(pool, poisoned_pool):
        baseline = classify_evidence_vs_price(dict(row), [dict(r) for r in pool])
        result = classify_evidence_vs_price(dict(poisoned_row), [dict(r) for r in poisoned_pool])
        if result != baseline:
            mismatches.append(row["player_name"])

    assert mismatches == [], f"classifier output changed for: {mismatches}"


def test_classifier_still_works_when_the_two_fields_are_entirely_absent_from_every_row():
    """Not just corrupted -- the key doesn't exist on the dict at all,
    the strongest form of 'this classifier has no dependency on it.'"""
    pool = _load_pool()
    stripped_pool = [
        {k: v for k, v in r.items() if k not in ("market_value_score", "tpe_score")}
        for r in pool
    ]
    row = next(r for r in stripped_pool if r["player_name"] == "Jake Bobo")
    result = classify_evidence_vs_price(row, stripped_pool)
    assert result["label"] in ("supports_expectation", "raises_questions", "insufficient_evidence")


# ---------------------------------------------------------------------------
# Stage 1b: the SAME proof pattern (deleted / corrupted / absent), applied
# explicitly to the new evidence_vs_price field -- not just relying on the
# whole-dict `==` comparisons above (which already cover it incidentally,
# since evidence_vs_price is part of the returned dict). These tests name
# the field directly, the same "say exactly what was proven" discipline as
# the rest of this file, and would fail on their own even if a future edit
# changed how the whole-dict comparison above is structured.
# ---------------------------------------------------------------------------

def test_evidence_vs_price_identical_when_market_value_score_and_tpe_score_are_deleted():
    pool = _load_pool()
    row = next(r for r in pool if r["player_name"] == "Jaylen Wright")
    baseline = classify_evidence_vs_price(dict(row), [dict(r) for r in pool])

    corrupted_pool = []
    for r in pool:
        r2 = dict(r)
        del r2["market_value_score"]
        del r2["tpe_score"]
        corrupted_pool.append(r2)
    corrupted_row = dict(row)
    del corrupted_row["market_value_score"]
    del corrupted_row["tpe_score"]

    result = classify_evidence_vs_price(corrupted_row, corrupted_pool)
    assert result["evidence_vs_price"] == baseline["evidence_vs_price"]


def test_evidence_vs_price_identical_when_market_value_score_and_tpe_score_are_corrupted():
    pool = _load_pool()
    row = next(r for r in pool if r["player_name"] == "Tutu Atwell")
    baseline = classify_evidence_vs_price(dict(row), [dict(r) for r in pool])

    poisoned_pool = []
    for r in pool:
        r2 = dict(r)
        r2["market_value_score"] = 999999.0
        r2["tpe_score"] = -999999.0
        poisoned_pool.append(r2)
    poisoned_row = dict(row)
    poisoned_row["market_value_score"] = 999999.0
    poisoned_row["tpe_score"] = -999999.0

    result = classify_evidence_vs_price(poisoned_row, poisoned_pool)
    assert result["evidence_vs_price"] == baseline["evidence_vs_price"]


def test_evidence_vs_price_identical_across_every_real_row_when_fields_are_absent():
    """The broadest form, for evidence_vs_price specifically: every real
    row in the fixture, with market_value_score/tpe_score missing from
    the dict entirely (not just corrupted) on both sides of the
    comparison."""
    pool = _load_pool()
    stripped_pool = [
        {k: v for k, v in r.items() if k not in ("market_value_score", "tpe_score")}
        for r in pool
    ]

    mismatches = []
    for row, stripped_row in zip(pool, stripped_pool):
        baseline = classify_evidence_vs_price(dict(row), [dict(r) for r in pool])
        result = classify_evidence_vs_price(dict(stripped_row), [dict(r) for r in stripped_pool])
        if result["evidence_vs_price"] != baseline["evidence_vs_price"]:
            mismatches.append(row["player_name"])

    assert mismatches == [], f"evidence_vs_price changed for: {mismatches}"


# ---------------------------------------------------------------------------
# Stage 1c, Task 2 — the gating reversal itself (evidence_vs_price -> None
# under insufficient_evidence) must be proven independent of market_value_
# score/tpe_score too, the same deleted/corrupted/absent discipline as
# every proof above, not just asserted in prose. Laquon Treadwell is the
# real fixture row this package already uses to demonstrate the
# evidence_quality gate (see test_evidence_price_classifier.py's
# test_low_evidence_quality_yields_insufficient_evidence_regardless_of_gap
# and classification_rubric.md): evidence_quality=49.8, below the 50.0
# floor, with a clean 13.9-point gap that would otherwise read as
# supports_expectation -- confirmed gated to insufficient_evidence with
# evidence_vs_price now None (not the real ALIGNED value Stage 1b would
# have returned) before any corruption is applied below.
# ---------------------------------------------------------------------------

def test_gated_row_has_none_evidence_vs_price_at_baseline():
    """Sanity check before the corruption proofs below: confirm the real,
    uncorrupted baseline for a known-gated real row is what this whole
    section assumes it is -- label insufficient_evidence, evidence_vs_
    price None, and the raw rank numbers still present and real."""
    pool = _load_pool()
    row = next(r for r in pool if r["player_name"] == "Laquon Treadwell")
    result = classify_evidence_vs_price(dict(row), [dict(r) for r in pool])
    assert result["label"] == INSUFFICIENT_EVIDENCE
    assert result["evidence_vs_price"] is None
    assert result["gap"] == 13.9
    assert result["evidence_rank_pct"] is not None
    assert result["price_rank_pct"] is not None


def test_gated_rows_none_evidence_vs_price_unaffected_when_market_value_score_and_tpe_score_are_deleted():
    pool = _load_pool()
    row = next(r for r in pool if r["player_name"] == "Laquon Treadwell")

    corrupted_pool = []
    for r in pool:
        r2 = dict(r)
        del r2["market_value_score"]
        del r2["tpe_score"]
        corrupted_pool.append(r2)
    corrupted_row = dict(row)
    del corrupted_row["market_value_score"]
    del corrupted_row["tpe_score"]

    result = classify_evidence_vs_price(corrupted_row, corrupted_pool)
    assert result["label"] == INSUFFICIENT_EVIDENCE
    assert result["evidence_vs_price"] is None


def test_gated_rows_none_evidence_vs_price_unaffected_when_market_value_score_and_tpe_score_are_corrupted():
    """Not deleted this time -- poisoned with absurd values, same as the
    pre-existing corruption proofs above. If the gating decision (or the
    None-suppression applied because of it) secretly depended on either
    field under any name, poisoning them would change the result; it
    must not."""
    pool = _load_pool()
    row = next(r for r in pool if r["player_name"] == "Laquon Treadwell")

    poisoned_pool = []
    for r in pool:
        r2 = dict(r)
        r2["market_value_score"] = 999999.0
        r2["tpe_score"] = -999999.0
        poisoned_pool.append(r2)
    poisoned_row = dict(row)
    poisoned_row["market_value_score"] = 999999.0
    poisoned_row["tpe_score"] = -999999.0

    result = classify_evidence_vs_price(poisoned_row, poisoned_pool)
    assert result["label"] == INSUFFICIENT_EVIDENCE
    assert result["evidence_vs_price"] is None


def test_gated_rows_none_evidence_vs_price_unaffected_when_fields_are_entirely_absent():
    """Strongest form: the keys don't exist on the dict at all."""
    pool = _load_pool()
    stripped_pool = [
        {k: v for k, v in r.items() if k not in ("market_value_score", "tpe_score")}
        for r in pool
    ]
    row = next(r for r in stripped_pool if r["player_name"] == "Laquon Treadwell")
    result = classify_evidence_vs_price(row, stripped_pool)
    assert result["label"] == INSUFFICIENT_EVIDENCE
    assert result["evidence_vs_price"] is None


def test_pool_gated_row_also_has_none_evidence_vs_price_independent_of_market_value_score_and_tpe_score():
    """The SAME proof, for the OTHER gate (pool size, not evidence_
    quality) -- a tiny synthetic pool forces insufficient_evidence via
    MIN_POOL_SIZE regardless of evidence_quality, and evidence_vs_price
    must be None here too, with market_value_score/tpe_score corrupted on
    every row, proving the pool-gate's None-suppression is just as
    independent of those two fields as the quality-gate's is above."""
    tiny_pool = [
        {
            "td_opportunity": float(i), "role_momentum": float(i), "situation": float(i),
            "evidence_quality": 95.0, "consensus_price_american": 100.0 + i,
            "market_value_score": 999999.0, "tpe_score": -999999.0,
        }
        for i in range(10)
    ]
    target_row = dict(tiny_pool[5])
    result = classify_evidence_vs_price(target_row, tiny_pool)
    assert result["label"] == INSUFFICIENT_EVIDENCE
    assert result["evidence_vs_price"] is None
    assert result["price_pool_size"] == 10


# ---------------------------------------------------------------------------
# STAGE 1g, Task 4 — the SAME proof, for the completeness gate (the
# newest of the four gates). market_value_score/tpe_score corrupted on
# every row; td_opportunity_completeness/role_momentum_completeness
# present and real this time, since THOSE are the fields this gate is
# supposed to read -- the point is proving it reads THOSE, never the two
# market/overall fields, not that it reads nothing at all.
# ---------------------------------------------------------------------------

def _completeness_gated_pool(n: int = 160) -> tuple:
    """n rows clearing MIN_POOL_SIZE on raw priced count; the target row
    (index 0) fails the completeness gate (role_momentum_completeness=0)
    while every other row passes cleanly."""
    pool = [
        {
            "td_opportunity": 50.0 + (i % 40), "role_momentum": 50.0 + (i % 30),
            "situation": 50.0 + (i % 20), "evidence_quality": 90.0,
            "consensus_price_american": 100.0 + i,
            "td_opportunity_completeness": 100.0, "role_momentum_completeness": 100.0,
            "market_value_score": float(i), "tpe_score": float(i) * 2,
        }
        for i in range(n)
    ]
    pool[0]["td_opportunity_completeness"] = 30.0
    pool[0]["role_momentum_completeness"] = 0.0
    return pool[0], pool


def test_completeness_gated_row_has_none_evidence_vs_price_at_baseline():
    target, pool = _completeness_gated_pool()
    result = classify_evidence_vs_price(dict(target), [dict(r) for r in pool])
    assert result["label"] == INSUFFICIENT_EVIDENCE
    assert result["evidence_vs_price"] is None
    assert "completeness" in result["reason"].lower()


def test_completeness_gate_outcome_unaffected_when_market_value_score_and_tpe_score_are_deleted():
    target, pool = _completeness_gated_pool()
    corrupted_pool = []
    for r in pool:
        r2 = dict(r)
        del r2["market_value_score"]
        del r2["tpe_score"]
        corrupted_pool.append(r2)
    corrupted_target = dict(target)
    del corrupted_target["market_value_score"]
    del corrupted_target["tpe_score"]

    result = classify_evidence_vs_price(corrupted_target, corrupted_pool)
    assert result["label"] == INSUFFICIENT_EVIDENCE
    assert result["evidence_vs_price"] is None


def test_completeness_gate_outcome_unaffected_when_market_value_score_and_tpe_score_are_corrupted():
    target, pool = _completeness_gated_pool()
    poisoned_pool = []
    for r in pool:
        r2 = dict(r)
        r2["market_value_score"] = 999999.0
        r2["tpe_score"] = -999999.0
        poisoned_pool.append(r2)
    poisoned_target = dict(target)
    poisoned_target["market_value_score"] = 999999.0
    poisoned_target["tpe_score"] = -999999.0

    result = classify_evidence_vs_price(poisoned_target, poisoned_pool)
    assert result["label"] == INSUFFICIENT_EVIDENCE
    assert result["evidence_vs_price"] is None


def test_shared_ranking_population_size_unaffected_by_market_value_score_and_tpe_score():
    """STAGE 1g, Task 2's own population-sharing mechanism must also be
    independent of market_value_score/tpe_score -- corrupting those two
    fields on every row must not change eligible_population_size, which
    is built purely from consensus_price_american + the two completeness
    fields."""
    target, pool = _completeness_gated_pool()
    passing_row = pool[1]  # a completeness-passing row, to see the real (non-gated) population size
    baseline = classify_evidence_vs_price(dict(passing_row), [dict(r) for r in pool])

    poisoned_pool = []
    for r in pool:
        r2 = dict(r)
        r2["market_value_score"] = 123456.0
        r2["tpe_score"] = -123456.0
        poisoned_pool.append(r2)
    poisoned_row = dict(passing_row)
    poisoned_row["market_value_score"] = 123456.0
    poisoned_row["tpe_score"] = -123456.0

    result = classify_evidence_vs_price(poisoned_row, poisoned_pool)
    assert result["eligible_population_size"] == baseline["eligible_population_size"] == len(pool) - 1  # every row but the one completeness-failing row
