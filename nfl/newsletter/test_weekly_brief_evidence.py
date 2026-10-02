"""
Tests for weekly_brief_evidence.py and its wiring in run_weekly_editor_
agent.py -- the Evidence Validator as the last step before the runner
returns anything, with block-and-surface failure behavior. No network:
the model is mocked; the candidate pool is the real fixture_v2.json.

Run: python3 nfl/newsletter/test_weekly_brief_evidence.py
"""
import copy
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "content_writer"))

import run_weekly_editor_agent as runner  # noqa: E402
from weekly_brief_evidence import (  # noqa: E402
    EvidenceValidationFailed,
    enrich_gates_for_validation,
    run_evidence_validation,
    validator_stories_by_id,
)
from weekly_brief_shape import EPS_SCORE_KEYS, zero_eps_scores  # noqa: E402


def check(label, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    return bool(cond)


FIXTURES = json.loads((HERE / "fixture_v2.json").read_text())
CANDIDATES = runner.build_candidate_pool(FIXTURES)["candidate_story_objects"]
RAW_RUN7 = json.loads((HERE / "fixture_v2_run7_raw.json").read_text())  # structurally malformed real output


def story(headline, body, ids):
    return {"headline": headline, "body": body, "intelligence_story_ids": ids,
            "player_ids": [], "pick_ids": [], "eps_scores": zero_eps_scores()}


def sec(section_type, stories, xrefs=None):
    return {"section_type": section_type, "stories": stories, "cross_references": xrefs or []}


def issue(*sections):
    return {"issue_week": "t", "sections": list(sections), "watchlist_populated": False, "notes_for_human_reviewer": ""}


# A clean issue: every number in the Big One body is a real value from
# fixture 1's own interrogation record (24% -> 58%, +650 -> +400), the
# name grounds against entity "RB Dorsey Keane", no relationship
# connectives, no confidence-escalating words. Big One placement is
# gate-legal (fixture 1: big_one_eligible true).
def clean_issue():
    return issue(
        sec("from_the_desk", [story("From the desk", "A quiet week, mostly.", [])]),
        sec("big_one", [story("Keane's share moved first",
                              "Keane's goal-line share rose from 24% to 58% over three games, and the price moved from +650 to +400.",
                              ["SYNTHETIC-FIXTURE-1"])]),
    )


calls = {"n": 0}


def install(responses):
    calls["n"] = 0

    def fake(api_key, system_prompt, user_prompt, tool_schema, max_tokens=None, return_all_tool_use_blocks=False):
        out = responses[calls["n"]]
        calls["n"] += 1
        return out
    runner.call_claude_with_tool = fake


def run_expect_block(responses):
    install(responses)
    try:
        runner.run_weekly_editor_agent(FIXTURES, "key")
    except EvidenceValidationFailed as e:
        return e
    return None


if __name__ == "__main__":
    r = []
    real_call = runner.call_claude_with_tool
    try:
        # ============================================================
        # Unit: the two mirrored contracts
        # ============================================================
        sbi = validator_stories_by_id(CANDIDATES)
        r.append(check("validator_stories_by_id reshapes entity str -> {player_name} for the name pool",
                       sbi["SYNTHETIC-FIXTURE-1"]["entity"] == {"player_name": "RB Dorsey Keane"}
                       and sbi["SYNTHETIC-FIXTURE-1"]["interrogation"] is CANDIDATES[0]["interrogation"]))
        enriched = enrich_gates_for_validation(clean_issue(), CANDIDATES)
        big = [s for x in enriched["sections"] if x["section_type"] == "big_one" for s in x["stories"]][0]
        r.append(check("enrich_gates_for_validation attaches upstream eps.gates to a single-source entry (fixture 1: both true)",
                       big["eps_scores"]["big_one_eligible"] is True and big["eps_scores"]["watchlist_eligible"] is True))
        multi = enrich_gates_for_validation(issue(sec("who_it_affects", [story("h", "b", ["SYNTHETIC-FIXTURE-1", "SYNTHETIC-FIXTURE-7"])])), CANDIDATES)
        r.append(check("a multi-source entry gets NO gates attached (no single authoritative gate) -- left for needs_review, never guessed",
                       "big_one_eligible" not in multi["sections"][0]["stories"][0]["eps_scores"]))
        r.append(check("enrich_gates_for_validation never mutates its input", "big_one_eligible" not in clean_issue()["sections"][1]["stories"][0]["eps_scores"]))

        # ============================================================
        # Unit: run_evidence_validation report shape + pass/needs_review semantics
        # ============================================================
        rep = run_evidence_validation(clean_issue(), CANDIDATES)
        r.append(check("clean issue passes the full suite", rep["passed"] is True and rep["hard_fails"] == []))
        r.append(check("report carries per-story results for every stories[] entry (from_the_desk included) and the issue-level contract",
                       len(rep["per_story"]) == 2 and rep["editorial_contract"]["passed"] is True))
        gates = rep["editorial_contract"]["gate_consistency"]
        r.append(check("gate consistency actually RAN against upstream gates: the Big One has a real 'pass' gate entry, not needs_review",
                       any(g["status"] == "pass" and g["claim_type"] == "big_one" for g in gates)))
        r.append(check("needs_review findings do NOT block (fixture candidates carry no evidence_classification -> alignment is needs_review)",
                       rep["needs_review_count"] > 0 and rep["passed"] is True))
        r.append(check("summary starts with PASS on a passing issue", rep["summary"].startswith("PASS")))

        # ============================================================
        # Runner: pass path carries receipts, eps_scores untouched
        # ============================================================
        install([[clean_issue()]])
        out = runner.run_weekly_editor_agent(FIXTURES, "key")
        r.append(check("runner: a clean draft is returned with _evidence_validation attached and passed=True",
                       out["_evidence_validation"]["passed"] is True and "_validation" in out and calls["n"] == 1))
        big_out = [s for x in out["sections"] if x["section_type"] == "big_one" for s in x["stories"]][0]
        r.append(check("runner: the returned issue's eps_scores stay the 7-key editor shape -- gates were read for validation on a copy, not written back",
                       sorted(big_out["eps_scores"].keys()) == sorted(EPS_SCORE_KEYS)))
        r.append(check("runner: both receipts use the underscore (inspection-only) convention",
                       all(k.startswith("_") for k in ("_validation", "_evidence_validation")) and "_evidence_validation" in out))

        # ============================================================
        # BLOCK: an ungrounded 'survived scrutiny' claim
        # ============================================================
        # Fixture 4's only alternate is UNRESOLVED -- there is no WEAKENED
        # entry, so "held up" claims a survival the interrogation record
        # does not support. No numbers (nothing else can fail), name
        # grounds against the entity, no connectives, no escalating words:
        # the ONLY thing wrong with this draft is the survival claim.
        survival = clean_issue()
        survival["sections"].append(sec("what_changed", [story(
            "A slot problem that keeps showing up",
            "Carolina's slot problem held up under a second look.",
            ["SYNTHETIC-FIXTURE-4"],
        )]))
        e = run_expect_block([[survival]])
        r.append(check("BLOCKED: a draft with an ungrounded survival claim raises EvidenceValidationFailed (not returned)", e is not None))
        r.append(check("...the block is specifically interrogation_traceability, on that sentence, in what_changed",
                       e is not None and any(f["check"] == "interrogation_traceability" and f["where"] == "what_changed.stories[0]"
                                             and "held up" in f["claim_text"] for f in e.report["hard_fails"])))
        r.append(check("...no retry and no rewrite: exactly one model call was made",
                       calls["n"] == 1))
        r.append(check("...the exception carries the finalized issue with BOTH receipts attached, passed=False",
                       e is not None and e.issue["_evidence_validation"]["passed"] is False and "_validation" in e.issue
                       and e.report is e.issue["_evidence_validation"]))
        r.append(check("...and the message tells the human where to look", e is not None and "_evidence_validation" in str(e)))

        # ============================================================
        # BLOCK: scoring language inside a cross_reference (the fidelity
        # gap the acceptance harness documented -- closed here, since
        # the issue keeps its cross_references instead of being rebuilt
        # from flat rows)
        # ============================================================
        leak = clean_issue()
        leak["sections"][1]["cross_references"] = [{"text": "See What Changed -- it scored 82 on tension.",
                                                    "refers_to_intelligence_story_id": "SYNTHETIC-FIXTURE-5"}]
        e = run_expect_block([[leak]])
        r.append(check("BLOCKED: scoring language in a cross_reference's text is caught (cross_references are scanned, not lost)",
                       e is not None and any(f["check"] == "scoring_language_leak" and f["claim_text"] == "scored <number>"
                                             for f in e.report["hard_fails"])))

        # ============================================================
        # BLOCK: a gate violation -- Big One placement of a story whose
        # upstream big_one_eligible is false (fixture 3). Body numbers
        # all ground (41% / 67% from its own interrogation), so the gate
        # is the only failure -- proves gates are read from upstream.
        # ============================================================
        gate = issue(sec("big_one", [story("Denver's red-zone split",
                                           "Denver's red-zone pass rate ran 67% against a 41% season average.",
                                           ["SYNTHETIC-FIXTURE-3"])]))
        e = run_expect_block([[gate]])
        r.append(check("BLOCKED: a Big One citing a story with big_one_eligible=false fails gate_consistency",
                       e is not None and any(f["check"] == "gate_consistency" and f["claim_text"] == ["SYNTHETIC-FIXTURE-3"]
                                             for f in e.report["hard_fails"])))

        # ============================================================
        # BLOCK: a fabricated number
        # ============================================================
        fab = clean_issue()
        fab["sections"][1]["stories"][0]["body"] = "Keane's goal-line share rose from 24% to 91% over three games."
        e = run_expect_block([[fab]])
        r.append(check("BLOCKED: a number that matches nothing in the referenced story (91%) fails claim_traceability",
                       e is not None and any(f["check"] == "claim_traceability" and f["claim_text"] == "91%" for f in e.report["hard_fails"])))

        # ============================================================
        # ORDER: structural contract first, evidence second
        # ============================================================
        # First response is the REAL run-7 output (structurally malformed:
        # Big One missing its ids) -> structural retry fires -> second
        # response is clean -> evidence runs on the finalized issue.
        install([[RAW_RUN7], [clean_issue()]])
        out = runner.run_weekly_editor_agent(FIXTURES, "key")
        r.append(check("order: structural retry happens BEFORE evidence validation, and evidence then runs on the finalized issue",
                       calls["n"] == 2 and out["_validation"]["retry_fired"] is True and out["_evidence_validation"]["passed"] is True))
        # ...whereas an evidence failure never triggers the structural retry.
        e = run_expect_block([[survival], [clean_issue()]])
        r.append(check("order: an evidence failure does NOT fall through to the structural retry (second response never requested)",
                       e is not None and calls["n"] == 1))
    finally:
        runner.call_claude_with_tool = real_call

    print()
    p = sum(r)
    print(f"{p}/{len(r)} checks passed")
    raise SystemExit(0 if p == len(r) else 1)
