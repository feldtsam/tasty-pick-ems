"""
Tests for eps.py's own deterministic pieces — Evidence Strength,
Audience Relevance, gate computation, and the Story Tension grounding
guard. All purely synthetic, no network dependency. The semantic call
itself (compute_eps -> _score_semantic_dimensions) is validated
separately, against real live data (three already-interrogated real
Story Objects from this session's own Story Interrogation validation
runs: two Market Intelligence LIMITED-evidence rows, one Role Changes
injury-driven-opportunity row) — see the session's own real-data runs
for that half, not reproduced here as a mock.

Run: python3 nfl/test_eps.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "content_writer"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "newsletter"))

from eps import (
    BIG_ONE_EVIDENCE_FLOOR,
    LIMITED_EVIDENCE_CAP,
    _compute_gates,
    _story_tension_grounded_in_interrogation,
    _valid_semantic_response_shape,
    compute_audience_relevance,
    compute_evidence_strength,
    scan_semantic_response_for_confidence_escalation,
)


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


if __name__ == "__main__":
    results = []

    # ============================================================
    # Evidence Strength -- §7, acceptance test #1.
    # ============================================================
    base_story = {"completeness": 70.0, "confidence": 70.0, "evidence_classification": "strong"}

    weakened_interrogation = {"challenge": {"alternate_explanations": [
        {"explanation": "x", "evidence": "y", "test": "z", "result": "r", "status": "WEAKENED"},
    ]}}
    empty_interrogation = {"challenge": {"alternate_explanations": []}}

    es_weakened = compute_evidence_strength(base_story, weakened_interrogation)
    es_empty = compute_evidence_strength(base_story, empty_interrogation)
    results.append(check(
        "ACCEPTANCE TEST #1: identical completeness, WEAKENED status vs. empty alternate_explanations[] score differently",
        es_weakened["score"] != es_empty["score"],
    ))
    results.append(check(
        "WEAKENED moves the score UP relative to no challenge at all -- a real, passed test is stronger evidence",
        es_weakened["score"] > es_empty["score"],
    ))

    supported_interrogation = {"challenge": {"alternate_explanations": [
        {"explanation": "x", "evidence": "y", "test": "z", "result": "r", "status": "SUPPORTED"},
    ]}}
    es_supported = compute_evidence_strength(base_story, supported_interrogation)
    results.append(check(
        "SUPPORTED moves the score DOWN -- a competing explanation being favored is a real strike against the signal",
        es_supported["score"] < es_empty["score"],
    ))

    unresolved_interrogation = {"challenge": {"alternate_explanations": [
        {"explanation": "x", "evidence": "y", "test": "z", "result": "r", "status": "UNRESOLVED"},
    ]}}
    es_unresolved = compute_evidence_strength(base_story, unresolved_interrogation)
    results.append(check(
        "UNRESOLVED's downward adjustment is real but smaller than SUPPORTED's -- 'tested, inconclusive' is weaker than 'tested, and lost'",
        es_empty["score"] > es_unresolved["score"] > es_supported["score"],
    ))

    not_testable_interrogation = {"challenge": {"alternate_explanations": [
        {"explanation": "x", "evidence": "y", "test": "z", "result": "r", "status": "NOT_TESTABLE"},
    ]}}
    es_not_testable = compute_evidence_strength(base_story, not_testable_interrogation)
    results.append(check(
        "NOT_TESTABLE makes no adjustment -- absence of a test isn't evidence either way, per §7",
        es_not_testable["score"] == es_empty["score"],
    ))

    # ACCEPTANCE TEST #1's second half: evidence_classification=limited
    # hard-caps BOTH the WEAKENED and empty cases regardless.
    limited_story = {**base_story, "evidence_classification": "limited"}
    es_limited_weakened = compute_evidence_strength(limited_story, weakened_interrogation)
    es_limited_empty = compute_evidence_strength(limited_story, empty_interrogation)
    results.append(check(
        "ACCEPTANCE TEST #1: evidence_classification=\"limited\" hard-caps the WEAKENED case at the real cap value",
        es_limited_weakened["score"] <= LIMITED_EVIDENCE_CAP,
    ))
    results.append(check(
        "evidence_classification=\"limited\" hard-caps the empty-challenge case too, regardless of interrogation content",
        es_limited_empty["score"] <= LIMITED_EVIDENCE_CAP,
    ))

    es_null_interrogation = compute_evidence_strength(base_story, None)
    es_no_interrogation_equiv = (float(base_story["completeness"]) + float(base_story["confidence"])) / 2.0
    results.append(check(
        "interrogation=None computes EXACTLY the pre-interrogation base value -- no adjustment attempted, matching §7's explicit instruction",
        es_null_interrogation["score"] == round(es_no_interrogation_equiv, 1),
    ))
    results.append(check(
        "a missing completeness/confidence degrades to a real neutral 50, never raises",
        compute_evidence_strength({}, None)["score"] == 50.0,
    ))

    # ============================================================
    # Audience Relevance -- §6, acceptance test #5.
    # ============================================================
    qb_story = {"entity": {"type": "player", "position_group": "QB"}}
    ar_qb = compute_audience_relevance(qb_story)
    results.append(check(
        "a known position_group (QB) uses a real signal, not the midpoint default",
        ar_qb["score"] != 50.0 and "position_group=QB" in ar_qb["rationale"],
    ))

    defense_story = {"entity": {"type": "defense", "team": "SEA", "position_group": "RB"}}
    ar_defense = compute_audience_relevance(defense_story)
    results.append(check(
        "ACCEPTANCE TEST #5: an entity with no known position_group score defaults to the real midpoint (50), not a guess",
        ar_defense["score"] == 50.0,
    ))
    results.append(check(
        "ACCEPTANCE TEST #5: the rationale names which inputs were real signals vs. defaulted -- inspectable, not silent (§6)",
        "national-broadcast" in ar_defense["rationale"] and "midpoint" in ar_defense["rationale"],
    ))

    # ============================================================
    # Gates -- §9, acceptance test #4, the exact two worked contrasts
    # from the spec's own text.
    # ============================================================
    qualifies_dims = {
        "evidence_strength": {"score": 32}, "novelty": {"score": 84}, "story_tension": {"score": 78},
    }
    gates_qualifies = _compute_gates(qualifies_dims, composite_score=63)
    results.append(check(
        "ACCEPTANCE TEST #4: EPS 63/Evidence 32/Novelty 84/Tension 78 -> watchlist_eligible=True, matching the spec's own worked example",
        gates_qualifies["watchlist_eligible"] is True,
    ))

    does_not_qualify_dims = {
        "evidence_strength": {"score": 31}, "novelty": {"score": 49}, "story_tension": {"score": 52},
    }
    gates_does_not_qualify = _compute_gates(does_not_qualify_dims, composite_score=57)
    results.append(check(
        "ACCEPTANCE TEST #4: EPS 57/Evidence 31/Novelty 49/Tension 52 -> watchlist_eligible=False, matching the spec's own worked example",
        gates_does_not_qualify["watchlist_eligible"] is False,
    ))

    results.append(check(
        f"big_one_eligible is a real >= {BIG_ONE_EVIDENCE_FLOOR} threshold on evidence_strength alone, independent of composite/novelty/tension",
        _compute_gates({"evidence_strength": {"score": BIG_ONE_EVIDENCE_FLOOR}, "novelty": {"score": 0}, "story_tension": {"score": 0}}, composite_score=0)["big_one_eligible"] is True
        and _compute_gates({"evidence_strength": {"score": BIG_ONE_EVIDENCE_FLOOR - 1}, "novelty": {"score": 100}, "story_tension": {"score": 100}}, composite_score=100)["big_one_eligible"] is False,
    ))

    # ============================================================
    # Semantic-response confidence-escalation scan (reused pattern from
    # story_interrogation.py, applied to the 4-dimension rationale shape).
    # ============================================================
    clean_semantic = {
        "significance": {"score": 60, "rationale": "a real, grounded sentence"},
        "betting_relevance": {"score": 50, "rationale": "another grounded sentence"},
        "novelty": {"score": 40, "rationale": "cites prior_history directly"},
        "story_tension": {"score": 30, "rationale": "cites a real challenge entry"},
    }
    results.append(check(
        "a clean semantic response produces zero confidence-escalation violations",
        scan_semantic_response_for_confidence_escalation(clean_semantic) == [],
    ))
    dirty_semantic = {**clean_semantic, "significance": {"score": 80, "rationale": "This is clearly a major, confirmed shift."}}
    violations = scan_semantic_response_for_confidence_escalation(dirty_semantic)
    results.append(check(
        "confidence-escalation scan catches a violation in a semantic dimension's own rationale field",
        {"clearly", "confirmed"} <= {v["phrase"] for v in violations},
    ))

    # ============================================================
    # Story Tension grounding guard -- §8's hard mechanism, acceptance
    # tests #2 and #3.
    # ============================================================
    real_interrogation = {
        "challenge": {"alternate_explanations": [
            {"explanation": "injury absence", "evidence": "depth chart status", "test": "persistence check",
             "result": "role increase tied directly to the injury", "status": "SUPPORTED"},
        ]},
        "confirmation": {"supporting_signals": "target share also rising", "contradicting_signals": "none identified", "market_reaction": "price moved toward favorite"},
    }
    grounded_rationale = "The role increase is directly tied to the injury absence per the challenge result, and target share also rising supports the same direction."
    results.append(check(
        "ACCEPTANCE TEST #3: a Story Tension rationale that genuinely cites real challenge/confirmation content is recognized as grounded",
        _story_tension_grounded_in_interrogation(grounded_rationale, real_interrogation),
    ))

    ungrounded_rationale = "This story has a compelling narrative arc that readers will find genuinely exciting and dramatic."
    results.append(check(
        "a Story Tension rationale with no real overlap to challenge/confirmation content is NOT recognized as grounded -- catches manufactured tension",
        not _story_tension_grounded_in_interrogation(ungrounded_rationale, real_interrogation),
    ))

    results.append(check(
        "ACCEPTANCE TEST #2: grounding check is unconditionally False when interrogation is None -- nothing can be cited from nothing",
        _story_tension_grounded_in_interrogation("any rationale at all", None) is False,
    ))

    # ============================================================
    # Semantic response shape validation -- defense-in-depth against a
    # real, observed failure (a dimension came back as a plain string
    # instead of {score, rationale} on one live API call).
    # ============================================================
    results.append(check(
        "a well-shaped semantic response passes validation",
        _valid_semantic_response_shape(clean_semantic),
    ))
    results.append(check(
        "a dimension holding a plain string instead of {score, rationale} is rejected -- the real failure mode this guard exists for",
        not _valid_semantic_response_shape({**clean_semantic, "story_tension": "a bare string, not an object"}),
    ))
    results.append(check(
        "a missing dimension key entirely is rejected",
        not _valid_semantic_response_shape({k: v for k, v in clean_semantic.items() if k != "novelty"}),
    ))
    results.append(check(
        "a non-dict response (e.g. a bare list) is rejected without raising",
        not _valid_semantic_response_shape(["not", "a", "dict"]),
    ))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
