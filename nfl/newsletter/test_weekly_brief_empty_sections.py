"""
Empty-section behaviour of the Weekly Brief path -- VERIFY, don't change.
What do finalize_weekly_brief (wrapper), validate_weekly_brief_shape,
enforce_one_treatment, run_evidence_validation and validate_editorial_
contract do when an issue has zero Watchlist stories (section omitted,
or present and empty), and separately zero Big One stories? Expected: an
empty section is omitted or left empty without error, nothing is padded,
nothing is forced in. Any check that fails here is a finding to report,
not something this file fixes. No model call. Run:
    python3 nfl/newsletter/test_weekly_brief_empty_sections.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evidence_validator import check_gate_consistency, check_one_treatment, validate_editorial_contract
from weekly_brief_evidence import run_evidence_validation
from weekly_brief_shape import enforce_one_treatment, finalize_weekly_brief, validate_weekly_brief_shape


def check(label, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    return bool(cond)


def candidate(sid, verdict="UNRESOLVED", big_one=True, watchlist=False):
    return {
        "intelligence_story_id": sid, "player_id": f"p-{sid}", "intelligence_family": "market_intelligence",
        "entity": "Some Player", "headline": f"Headline {sid}", "story": f"Story {sid}", "time_window": "w", "sample_size": 3,
        "interrogation": {"challenge": {"alternate_explanations": []}, "primary_alternate": {"alternate_id": "alt_1", "selection_reason": "r"},
                          "confirmation": {}, "judgment": {}, "signal_verdict": verdict},
        "eps": {"dimensions": {k: {"score": 50} for k in ("significance", "evidence_strength", "betting_relevance", "novelty", "story_tension", "audience_relevance")},
                "composite_score": 50.0, "gates": {"big_one_eligible": big_one, "watchlist_eligible": watchlist}},
    }


def entry(sid, headline="A plain headline", body="A plain body with no numbers and no names."):
    return {"headline": headline, "body": body, "intelligence_story_ids": [sid], "player_ids": [f"p-{sid}"], "pick_ids": [], "eps_scores": None}


def issue(sections):
    return {"issue_week": "2026 Week 5", "sections": sections}


if __name__ == "__main__":
    r = []
    cands = [candidate("S1"), candidate("S2", big_one=False)]
    desk = {"section_type": "from_the_desk", "stories": [{"headline": "Opening", "body": "A short opening.", "intelligence_story_ids": [], "player_ids": [], "pick_ids": [], "eps_scores": None}]}

    # ------------------------------------------------------------
    # Zero Watchlist stories: (a) section omitted, (b) present and empty
    # ------------------------------------------------------------
    omitted = issue([desk, {"section_type": "big_one", "stories": [entry("S1")]}, {"section_type": "what_changed", "stories": [entry("S2")]}])
    fin, rep = finalize_weekly_brief(omitted, cands)
    r.append(check("wrapper: an issue with the watchlist section OMITTED passes the structural contract",
                   rep["passed"] is True and rep["hard_errors"] == [] and rep["shape_errors"] == []))
    r.append(check("wrapper: it does not add a watchlist section or any story (no padding)",
                   [s["section_type"] for s in fin["sections"]] == ["from_the_desk", "big_one", "what_changed"]
                   and all(len(s["stories"]) == 1 for s in fin["sections"])))
    r.append(check("shape check: omitted watchlist produces no shape errors", validate_weekly_brief_shape(fin) == []))
    ev = run_evidence_validation(fin, cands)
    r.append(check("Evidence Validator: omitted watchlist -> no hard fail and no gate_consistency finding at all",
                   ev["passed"] is True and ev["hard_fails"] == [] and check_gate_consistency(fin) == [
                       x for x in check_gate_consistency(fin) if x["claim_type"] == "big_one"]))
    r.append(check("editorial contract: omitted watchlist passes; one_treatment and scoring_language_leak clean",
                   validate_editorial_contract(fin)["passed"] is True and check_one_treatment(fin) == [
                       x for x in check_one_treatment(fin) if x["status"] == "pass"]))

    empty_wl = issue([desk, {"section_type": "big_one", "stories": [entry("S1")]}, {"section_type": "watchlist", "stories": []}])
    fin2, rep2 = finalize_weekly_brief(empty_wl, cands)
    r.append(check("wrapper: a PRESENT-but-empty watchlist section passes the structural contract",
                   rep2["passed"] is True and rep2["hard_errors"] == [] and rep2["shape_errors"] == []))
    r.append(check("wrapper: the empty watchlist is left empty (stories [], cross_references []), nothing forced in",
                   next(s for s in fin2["sections"] if s["section_type"] == "watchlist")["stories"] == []
                   and next(s for s in fin2["sections"] if s["section_type"] == "watchlist")["cross_references"] == []))
    r.append(check("shape check: empty watchlist section is not an error", validate_weekly_brief_shape(fin2) == []))
    ev2 = run_evidence_validation(fin2, cands)
    r.append(check("Evidence Validator: empty watchlist -> pass, zero hard fails, no needs_review from gate_consistency",
                   ev2["passed"] is True and ev2["hard_fails"] == [] and not any(x["claim_type"] == "watchlist" for x in check_gate_consistency(fin2))))
    r.append(check("one-treatment enforcement leaves an empty watchlist alone (no repairs)",
                   enforce_one_treatment(fin2)[1] == []))

    # ------------------------------------------------------------
    # Zero Big One stories: (a) omitted, (b) present and empty
    # ------------------------------------------------------------
    no_big = issue([desk, {"section_type": "what_changed", "stories": [entry("S1")]}, {"section_type": "watchlist", "stories": [entry("S2")]}])
    cands_wl = [candidate("S1", big_one=False), candidate("S2", big_one=False, watchlist=True)]
    fin3, rep3 = finalize_weekly_brief(no_big, cands_wl)
    r.append(check("wrapper: an issue with NO Big One section passes the structural contract and is not padded",
                   rep3["passed"] is True and [s["section_type"] for s in fin3["sections"]] == ["from_the_desk", "what_changed", "watchlist"]))
    ev3 = run_evidence_validation(fin3, cands_wl)
    r.append(check("Evidence Validator: no Big One section -> pass; the watchlist placement's gate reads true",
                   ev3["passed"] is True and ev3["hard_fails"] == []
                   and any(x["claim_type"] == "watchlist" and x["status"] == "pass" for x in check_gate_consistency(
                       __import__("weekly_brief_evidence").enrich_gates_for_validation(fin3, cands_wl)))))
    empty_big = issue([desk, {"section_type": "big_one", "stories": []}, {"section_type": "what_changed", "stories": [entry("S1")]}])
    fin4, rep4 = finalize_weekly_brief(empty_big, cands)
    r.append(check("wrapper: a PRESENT-but-empty Big One section passes and stays empty",
                   rep4["passed"] is True and next(s for s in fin4["sections"] if s["section_type"] == "big_one")["stories"] == []))
    r.append(check("Evidence Validator + contract: empty Big One -> pass, no gate_consistency finding",
                   run_evidence_validation(fin4, cands)["passed"] is True and validate_editorial_contract(fin4)["passed"] is True
                   and check_gate_consistency(fin4) == []))

    # ------------------------------------------------------------
    # The opposite failure the gates exist for still fires: a story
    # placed in the watchlist whose gate is false is a hard fail.
    # ------------------------------------------------------------
    forced = issue([desk, {"section_type": "watchlist", "stories": [entry("S1")]}])
    evf = run_evidence_validation(forced, cands)  # S1 is watchlist_eligible False
    r.append(check("control: a story FORCED into the watchlist against a false gate is still a hard fail (gate_consistency)",
                   evf["passed"] is False and any(f["check"] == "gate_consistency" for f in evf["hard_fails"])))

    print()
    p = sum(r)
    print(f"{p}/{len(r)} checks passed")
    raise SystemExit(0 if p == len(r) else 1)
