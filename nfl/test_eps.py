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
    # A clean, complete interrogation -- both gates now require one (2026-10-07).
    CLEAN_INTERROGATION = {
        "challenge": {"alternate_explanations": [{"alternate_id": "alt_1", "status": "WEAKENED"}]},
        "primary_alternate": {"alternate_id": "alt_1", "selection_reason": "the one that matters"},
        "signal_verdict": "UNRESOLVED",
    }
    qualifies_dims = {
        "evidence_strength": {"score": 32}, "novelty": {"score": 84}, "story_tension": {"score": 78},
    }
    gates_qualifies = _compute_gates(qualifies_dims, composite_score=63, interrogation=CLEAN_INTERROGATION)
    results.append(check(
        "ACCEPTANCE TEST #4: EPS 63/Evidence 32/Novelty 84/Tension 78 with a clean interrogation -> watchlist_eligible=True, blocked_reason None, matching the spec's own worked example",
        gates_qualifies["watchlist_eligible"] is True and gates_qualifies["watchlist_blocked_reason"] is None,
    ))

    does_not_qualify_dims = {
        "evidence_strength": {"score": 31}, "novelty": {"score": 49}, "story_tension": {"score": 52},
    }
    gates_does_not_qualify = _compute_gates(does_not_qualify_dims, composite_score=57, interrogation=CLEAN_INTERROGATION)
    results.append(check(
        "ACCEPTANCE TEST #4: EPS 57/Evidence 31/Novelty 49/Tension 52 -> watchlist_eligible=False, reason names novelty and tension, matching the spec's own worked example",
        gates_does_not_qualify["watchlist_eligible"] is False and "both below" in gates_does_not_qualify["watchlist_blocked_reason"],
    ))

    # ============================================================
    # Big One gate, second half (2026-10-07): evidence floor AND a
    # completed interrogation (with primary_alternate) AND no FAILS
    # verdict. See eps.py's own comment above _big_one_blocked_reasons.
    # ============================================================
    FAILS_INTERROGATION = dict(CLEAN_INTERROGATION, signal_verdict="FAILS")
    NO_PRIMARY_INTERROGATION = dict(CLEAN_INTERROGATION, primary_alternate=None)
    at_floor = {"evidence_strength": {"score": BIG_ONE_EVIDENCE_FLOOR}, "novelty": {"score": 0}, "story_tension": {"score": 0}}
    below_floor = {"evidence_strength": {"score": BIG_ONE_EVIDENCE_FLOOR - 1}, "novelty": {"score": 100}, "story_tension": {"score": 100}}

    g = _compute_gates(at_floor, composite_score=0, interrogation=CLEAN_INTERROGATION)
    results.append(check(
        f"evidence >= {BIG_ONE_EVIDENCE_FLOOR} with a clean, complete interrogation -> big_one_eligible True, blocked_reason None, independent of composite/novelty/tension",
        g["big_one_eligible"] is True and g["big_one_blocked_reason"] is None,
    ))
    g = _compute_gates(at_floor, composite_score=0, interrogation=None)
    results.append(check(
        "interrogation None -> blocked, reason names the missing interrogation (the evidence floor alone is no longer enough)",
        g["big_one_eligible"] is False and g["big_one_blocked_reason"] == "no completed interrogation",
    ))
    g = _compute_gates(at_floor, composite_score=0, interrogation=FAILS_INTERROGATION)
    results.append(check(
        "signal_verdict FAILS -> blocked, reason names the verdict",
        g["big_one_eligible"] is False and g["big_one_blocked_reason"] == "signal_verdict FAILS",
    ))
    g = _compute_gates(below_floor, composite_score=100, interrogation=CLEAN_INTERROGATION)
    results.append(check(
        f"evidence {BIG_ONE_EVIDENCE_FLOOR - 1} with a clean interrogation -> still blocked, reason names the floor",
        g["big_one_eligible"] is False and g["big_one_blocked_reason"].startswith("evidence_strength") and "below floor" in g["big_one_blocked_reason"],
    ))
    g = _compute_gates(at_floor, composite_score=0, interrogation=NO_PRIMARY_INTERROGATION)
    results.append(check(
        "interrogation present but primary_alternate missing -> blocked, reason says so",
        g["big_one_eligible"] is False and g["big_one_blocked_reason"] == "interrogation has no primary_alternate",
    ))
    g = _compute_gates(below_floor, composite_score=0, interrogation=FAILS_INTERROGATION)
    results.append(check(
        "several failing conditions are all listed, floor first",
        g["big_one_eligible"] is False and g["big_one_blocked_reason"].startswith("evidence_strength") and g["big_one_blocked_reason"].endswith("signal_verdict FAILS"),
    ))
    results.append(check(
        "_compute_gates called the old way (no interrogation argument) is the conservative case: blocked, never eligible",
        _compute_gates(at_floor, composite_score=0)["big_one_eligible"] is False,
    ))
    # The two real Oct 6 2026 dry-run cases the rule exists for (values
    # as computed that day, dimensions abbreviated to the gate inputs).
    atl_dims = {"evidence_strength": {"score": 100.0}, "novelty": {"score": 20}, "story_tension": {"score": 10}}
    g = _compute_gates(atl_dims, composite_score=42.5, interrogation=None)
    results.append(check(
        "real ATL WR-defense case (interrogation returned None, evidence 100): was Big One eligible on the floor alone, now blocked for no interrogation",
        g["big_one_eligible"] is False and g["big_one_blocked_reason"] == "no completed interrogation",
    ))
    allen_interrogation = {
        "challenge": {"alternate_explanations": [{"alternate_id": "alt_1", "status": "UNRESOLVED"}, {"alternate_id": "alt_2", "status": "SUPPORTED"}, {"alternate_id": "alt_3", "status": "SUPPORTED"}]},
        "primary_alternate": {"alternate_id": "alt_2", "selection_reason": "gap already closed at the latest odds reading"},
        "signal_verdict": "FAILS",
    }
    allen_dims = {"evidence_strength": {"score": 67.0}, "novelty": {"score": 35}, "story_tension": {"score": 55}}
    g = _compute_gates(allen_dims, composite_score=41.3, interrogation=allen_interrogation)
    results.append(check(
        "real Braelon Allen Market case (FAILS verdict, evidence 67): was Big One eligible, now blocked for the verdict",
        g["big_one_eligible"] is False and g["big_one_blocked_reason"] == "signal_verdict FAILS",
    ))
    # ============================================================
    # Watchlist gate, same interrogation requirement (2026-10-07).
    # ============================================================
    g = _compute_gates(qualifies_dims, composite_score=63, interrogation=None)
    results.append(check(
        "Watchlist: a story meeting every other condition (EPS 63/Evidence 32/Novelty 84/Tension 78) but with no interrogation -> blocked, the interrogation is the ONLY reason",
        g["watchlist_eligible"] is False and g["watchlist_blocked_reason"] == "no completed interrogation",
    ))
    g = _compute_gates(qualifies_dims, composite_score=63, interrogation=FAILS_INTERROGATION)
    results.append(check(
        "Watchlist: FAILS verdict -> blocked, reason names the verdict",
        g["watchlist_eligible"] is False and g["watchlist_blocked_reason"] == "signal_verdict FAILS",
    ))
    g = _compute_gates(qualifies_dims, composite_score=63, interrogation=NO_PRIMARY_INTERROGATION)
    results.append(check(
        "Watchlist: interrogation without primary_alternate -> blocked",
        g["watchlist_eligible"] is False and g["watchlist_blocked_reason"] == "interrogation has no primary_alternate",
    ))
    g = _compute_gates(qualifies_dims, composite_score=63, interrogation=CLEAN_INTERROGATION)
    results.append(check(
        "Watchlist: clean interrogation with every other condition met -> eligible, reason None",
        g["watchlist_eligible"] is True and g["watchlist_blocked_reason"] is None,
    ))
    g = _compute_gates(does_not_qualify_dims, composite_score=50, interrogation=None)
    results.append(check(
        "Watchlist: every failing condition is listed, composite first, interrogation last",
        g["watchlist_eligible"] is False and g["watchlist_blocked_reason"].startswith("composite_score 50 below floor")
        and "both below" in g["watchlist_blocked_reason"] and g["watchlist_blocked_reason"].endswith("no completed interrogation"),
    ))
    results.append(check(
        "the existing composite/evidence/novelty-or-tension conditions still hold on their own: a clean interrogation does not rescue a story below them",
        _compute_gates({"evidence_strength": {"score": 24}, "novelty": {"score": 90}, "story_tension": {"score": 90}}, composite_score=90, interrogation=CLEAN_INTERROGATION)["watchlist_eligible"] is False
        and _compute_gates({"evidence_strength": {"score": 90}, "novelty": {"score": 64}, "story_tension": {"score": 64}}, composite_score=90, interrogation=CLEAN_INTERROGATION)["watchlist_eligible"] is False,
    ))
    results.append(check(
        "Big One gate is unaffected by the Watchlist change: the ATL/Allen outcomes above still hold and a clean at-floor story is still eligible",
        _compute_gates(at_floor, composite_score=0, interrogation=CLEAN_INTERROGATION)["big_one_eligible"] is True
        and _compute_gates(allen_dims, composite_score=41.3, interrogation=allen_interrogation)["big_one_eligible"] is False,
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

    # ============================================================
    # primary_alternate consumption (interrogation v3_primary_alternate):
    # what the evidence_strength rationale COMMUNICATES changes; the
    # scoring math does not. Pre-v3 records keep the previous wording.
    # ============================================================
    import eps as _eps

    v3_alts = [
        {"alternate_id": "alt_1", "explanation": "coach's stated plan", "evidence": "q", "test": "t", "result": "r", "status": "WEAKENED"},
        {"alternate_id": "alt_2", "explanation": "game script", "evidence": "q", "test": "t", "result": "r", "status": "WEAKENED"},
    ]
    v3_interrogation = {
        "challenge": {"alternate_explanations": v3_alts},
        "primary_alternate": {"alternate_id": "alt_1", "selection_reason": "If true, the split returns next week and the durability claim fails."},
    }
    pre_v3_same_statuses = {"challenge": {"alternate_explanations": [dict(a) for a in v3_alts]}}  # no primary_alternate key at all
    es_v3 = compute_evidence_strength(base_story, v3_interrogation)
    es_pre = compute_evidence_strength(base_story, pre_v3_same_statuses)
    results.append(check(
        "math unchanged: a v3 record scores identically to the same statuses without primary_alternate (count-additive, +8 per WEAKENED)",
        es_v3["score"] == es_pre["score"] == 86.0,
    ))
    results.append(check(
        "v3 rationale LEADS with the primary alternate: alternate_id, status, selection_reason",
        es_v3["rationale"].startswith("primary alternate alt_1 WEAKENED -- If true, the split returns next week and the durability claim fails"),
    ))
    results.append(check(
        "v3 rationale summarizes the remaining alternates by count and status",
        "; 1 other alternate WEAKENED." in es_v3["rationale"],
    ))
    results.append(check(
        "v3 rationale keeps the numeric adjustment total and the per-status notes",
        "adjusted +16 from challenge status(es): WEAKENED (+8), WEAKENED (+8)" in es_v3["rationale"],
    ))
    results.append(check(
        "pre-v3 record (no primary_alternate key): the previous wording, no 'primary alternate' lead, no error",
        es_pre["rationale"].startswith("base (confidence 70.0 + completeness 70.0) / 2 = 70.0, adjusted +16")
        and "primary alternate" not in es_pre["rationale"],
    ))
    mixed = {
        "challenge": {"alternate_explanations": [
            {"alternate_id": "alt_1", "status": "WEAKENED"}, {"alternate_id": "alt_2", "status": "SUPPORTED"},
            {"alternate_id": "alt_3", "status": "NOT_TESTABLE"}, {"alternate_id": "alt_4", "status": "NOT_TESTABLE"},
        ]},
        "primary_alternate": {"alternate_id": "alt_2", "selection_reason": "the one that would most seriously change the read"},
    }
    es_mixed = compute_evidence_strength(base_story, mixed)
    results.append(check(
        "the primary may be a SUPPORTED alternate: the lead reports ITS status; others grouped by count, most frequent first",
        es_mixed["rationale"].startswith(
            "primary alternate alt_2 SUPPORTED -- the one that would most seriously change the read; "
            "2 other alternates NOT_TESTABLE, 1 other alternate WEAKENED."
        ),
    ))
    results.append(check("mixed-status math unchanged (+8 -15 +0 +0 = -7)", es_mixed["score"] == 63.0))
    single = {
        "challenge": {"alternate_explanations": [{"alternate_id": "alt_1", "status": "UNRESOLVED"}]},
        "primary_alternate": {"alternate_id": "alt_1", "selection_reason": "only one"},
    }
    results.append(check(
        "single-alternate v3 record reads 'no other alternates'",
        "primary alternate alt_1 UNRESOLVED -- only one; no other alternates. base (" in compute_evidence_strength(base_story, single)["rationale"],
    ))
    null_primary_empty = {"challenge": {"alternate_explanations": []}, "primary_alternate": None}
    results.append(check(
        "v3 record with an empty array and primary_alternate null: the previous empty-array wording, unchanged",
        compute_evidence_strength(base_story, null_primary_empty)["rationale"] == es_empty["rationale"],
    ))
    dangling = {"challenge": {"alternate_explanations": [dict(a) for a in v3_alts]},
                "primary_alternate": {"alternate_id": "alt_9", "selection_reason": "x"}}
    es_dangling = compute_evidence_strength(base_story, dangling)
    results.append(check(
        "a primary_alternate that resolves to no entry falls back to the previous wording (never describes a pointer it can't resolve), score unchanged",
        "primary alternate" not in es_dangling["rationale"] and es_dangling["score"] == es_pre["score"],
    ))
    all_nt = {"challenge": {"alternate_explanations": [{"alternate_id": "alt_1", "status": "NOT_TESTABLE"}]},
              "primary_alternate": {"alternate_id": "alt_1", "selection_reason": "r"}}
    es_all_nt = compute_evidence_strength(base_story, all_nt)
    results.append(check(
        "v3 record whose only alternate is NOT_TESTABLE: primary lead, then the previous 'no challenge status moved the score' note",
        es_all_nt["rationale"].startswith("primary alternate alt_1 NOT_TESTABLE -- r; no other alternates. base (")
        and "No challenge status moved the score" in es_all_nt["rationale"],
    ))
    es_v3_limited = compute_evidence_strength(limited_story, v3_interrogation)
    results.append(check(
        "evidence_classification=limited still caps the score and still appends its note after the primary-led rationale",
        es_v3_limited["score"] <= LIMITED_EVIDENCE_CAP
        and es_v3_limited["rationale"].startswith("primary alternate alt_1")
        and "hard-caps the score" in es_v3_limited["rationale"],
    ))

    # --- Story Tension rubric (SYSTEM_PROMPT) ---
    P = " ".join(_eps.SYSTEM_PROMPT.split())  # whitespace-normalized: the prompt wraps mid-sentence
    results.append(check(
        "rubric: Story Tension tells the scorer to cite the primary alternate when present, matched by alternate_id",
        "when interrogation.primary_alternate is present and not null, cite THAT one" in P
        and "whose alternate_id matches primary_alternate.alternate_id" in P,
    ))
    results.append(check(
        "rubric: falls back to any tested alternate only when primary_alternate is null (empty array, or a pre-v3 record with no field)",
        "Fall back to citing any tested alternate only when primary_alternate is null" in P
        and "no primary_alternate field at all" in P,
    ))
    results.append(check(
        "rubric: the PROHIBITED clause and the interrogation:null cap are unchanged",
        "You are PROHIBITED from citing anything for Story Tension that is not present in interrogation.challenge or interrogation.confirmation" in P
        and "Cap your Story Tension score at 40 or below" in P,
    ))

    # --- grounding guard now sources primary_alternate text too ---
    terse = {
        "challenge": {"alternate_explanations": [
            {"alternate_id": "alt_1", "explanation": "x", "evidence": "y", "test": "z", "result": "r", "status": "WEAKENED"}]},
        "confirmation": {"supporting_signals": "s", "contradicting_signals": "c", "market_reaction": "m"},
        "primary_alternate": {"alternate_id": "alt_1", "selection_reason": "coach stated workload plan negates durability outright"},
    }
    cites_reason = "The coach's stated workload plan negates durability outright."
    results.append(check(
        "grounding guard: a Story Tension rationale grounded in primary_alternate.selection_reason is recognized as grounded",
        _story_tension_grounded_in_interrogation(cites_reason, terse),
    ))
    results.append(check(
        "grounding guard: the same rationale is NOT grounded against a pre-v3 record lacking that text -- the new source is what grounds it",
        not _story_tension_grounded_in_interrogation(cites_reason, {k: v for k, v in terse.items() if k != "primary_alternate"}),
    ))
    results.append(check(
        "grounding guard: existing grounded / ungrounded / None behavior unchanged",
        _story_tension_grounded_in_interrogation(grounded_rationale, real_interrogation)
        and not _story_tension_grounded_in_interrogation(ungrounded_rationale, real_interrogation)
        and _story_tension_grounded_in_interrogation("anything", None) is False,
    ))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
