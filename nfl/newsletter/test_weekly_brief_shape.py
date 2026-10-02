"""
Tests for weekly_brief_shape.py and the runner wiring in run_weekly_
editor_agent.py -- the structural contract fixes for the two real bugs
Calibration Fixture V2 run 7 produced (see weekly_brief_shape.py's own
module docstring). No network: the model is mocked, and the "before"
input is the REAL saved run-7 output (fixture_v2_run7_raw.json), so the
tests are grounded in the exact malformed shape that shipped, not a
guess at it.

Run: python3 nfl/newsletter/test_weekly_brief_shape.py
"""
import copy
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "content_writer"))

from evidence_validator import check_one_treatment  # noqa: E402
import run_weekly_editor_agent as runner  # noqa: E402
from weekly_brief_shape import (  # noqa: E402
    EPS_SCORE_KEYS,
    SECTION_PRIORITY,
    complete_derivable_fields,
    enforce_one_treatment,
    finalize_weekly_brief,
    validate_weekly_brief_shape,
    zero_eps_scores,
)


def check(label, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    return bool(cond)


RAW_RUN7 = json.loads((HERE / "fixture_v2_run7_raw.json").read_text())
FIXTURES = json.loads((HERE / "fixture_v2.json").read_text())
POOL = runner.build_candidate_pool(FIXTURES)
CANDIDATES = POOL["candidate_story_objects"]
F7 = next(f for f in FIXTURES if f["intelligence_story_id"] == "SYNTHETIC-FIXTURE-7")


def section(issue, name):
    return next(s for s in issue["sections"] if s["section_type"] == name)


def patched_run7():
    """Run 7 with the ONE non-derivable gap closed: the Big One told
    which story it treats. Every other missing field stays missing, so
    the derivation path is what the tests exercise."""
    issue = copy.deepcopy(RAW_RUN7)
    section(issue, "big_one")["stories"][0]["intelligence_story_ids"] = ["SYNTHETIC-FIXTURE-7"]
    return issue


def mini_issue(*sections):
    """sections: (section_type, [story dicts]) in the order they should
    appear in the sections array."""
    return {
        "issue_week": "t",
        "sections": [{"section_type": st, "stories": stories, "cross_references": []} for st, stories in sections],
        "watchlist_populated": True,
        "notes_for_human_reviewer": "",
    }


def full_story(headline, ids, eps=None):
    return {
        "headline": headline, "body": "body text here", "intelligence_story_ids": ids,
        "player_ids": [], "pick_ids": [], "eps_scores": eps or zero_eps_scores(),
    }


if __name__ == "__main__":
    r = []

    # ============================================================
    # Single source of truth: schema enum == one-treatment priority
    # ============================================================
    enum = runner.WEEKLY_BRIEF_TOOL_SCHEMA["input_schema"]["properties"]["sections"]["items"]["properties"]["section_type"]["enum"]
    r.append(check("tool schema section enum is exactly SECTION_PRIORITY (one source of truth)", enum == list(SECTION_PRIORITY)))

    # ============================================================
    # Bug 1 -- shape validator catches the REAL run-7 omission
    # ============================================================
    errs = validate_weekly_brief_shape(RAW_RUN7)
    r.append(check(
        "shape validator flags big_one.stories[0] missing all four required fields on the real run-7 output",
        all(f"big_one.stories[0] missing required field {f!r}" in errs for f in ("intelligence_story_ids", "player_ids", "pick_ids", "eps_scores")),
    ))
    r.append(check(
        "shape validator flags from_the_desk.stories[0] the same way",
        any(e.startswith("from_the_desk.stories[0] missing required field") for e in errs),
    ))
    r.append(check(
        "shape validator does NOT flag the run-7 entries that were complete (what_changed, who_it_affects, watchlist)",
        not any(e.startswith(("what_changed.", "who_it_affects.", "watchlist.")) for e in errs),
    ))
    partial_eps = mini_issue(("what_changed", [full_story("h", ["SYNTHETIC-FIXTURE-1"], eps={"significance": 1})]))
    r.append(check(
        "shape validator flags an eps_scores object missing keys, key by key",
        any("eps_scores missing 'eps_total'" in e for e in validate_weekly_brief_shape(partial_eps)),
    ))
    r.append(check("shape validator flags an unknown section_type", any("unknown section_type" in e for e in validate_weekly_brief_shape(mini_issue(("nope", []))))))

    # ============================================================
    # Bug 1 -- completion: derive what's derivable, refuse what isn't
    # ============================================================
    done, repairs, hard = complete_derivable_fields(RAW_RUN7, CANDIDATES)
    r.append(check(
        "raw run 7: a Big One with no intelligence_story_ids is a HARD error, never silently zero-sourced",
        any(h.startswith("big_one.stories[0] has no intelligence_story_ids") for h in hard),
    ))
    desk = section(done, "from_the_desk")["stories"][0]
    r.append(check(
        "raw run 7: from_the_desk missing everything is filled per the prompt's own contract ([] ids, zero eps, [] player/pick)",
        desk["intelligence_story_ids"] == [] and desk["eps_scores"] == zero_eps_scores() and desk["player_ids"] == [] and desk["pick_ids"] == []
        and any(x["where"] == "from_the_desk.stories[0]" and x["action"] == "filled_empty_per_contract" for x in repairs),
    ))

    done, repairs, hard = complete_derivable_fields(patched_run7(), CANDIDATES)
    big = section(done, "big_one")["stories"][0]
    expected_eps = {k: F7["eps"]["dimensions"][k]["score"] for k in EPS_SCORE_KEYS if k != "eps_total"}
    expected_eps["eps_total"] = F7["eps"]["composite_score"]
    r.append(check("patched run 7: no hard errors once the Big One cites its story", hard == []))
    r.append(check(
        "Big One eps_scores derived copy-not-compute from fixture 7's own eps block (74/66/79/70/86/50, total 73.4)",
        big["eps_scores"] == expected_eps and big["eps_scores"]["eps_total"] == 73.4,
    ))
    r.append(check("Big One player_ids derived from the cited candidate's player_id", big["player_ids"] == ["SYNTHETIC-PLAYER-7"]))
    r.append(check("Big One pick_ids filled with [] (no pick is derivable from a story id)", big["pick_ids"] == []))
    who = section(done, "who_it_affects")["stories"][0]
    r.append(check(
        "a two-story entry (who_it_affects citing 7 and 1) gets ZERO eps_scores -- the same rule persist applies, not a blend",
        who["eps_scores"] == zero_eps_scores(),
    ))
    r.append(check("completion never mutates its input", "intelligence_story_ids" not in section(RAW_RUN7, "big_one")["stories"][0]))

    wrong_copy = mini_issue(("what_changed", [full_story("h", ["SYNTHETIC-FIXTURE-1"], eps={**zero_eps_scores(), "significance": 99})]))
    done_wc, repairs_wc, _ = complete_derivable_fields(wrong_copy, CANDIDATES)
    r.append(check(
        "a model-mis-copied eps_scores is replaced with the deterministic copy AND the mismatch is recorded, not trusted",
        section(done_wc, "what_changed")["stories"][0]["eps_scores"]["significance"] == 78
        and any(x["action"] == "replaced_model_copy" and x["model_value"]["significance"] == 99 for x in repairs_wc),
    ))
    unknown = mini_issue(("what_changed", [full_story("h", ["NOT-A-REAL-ID"])]))
    r.append(check("citing an id outside this week's candidate pool is a hard error", any("unknown intelligence_story_id" in h for h in complete_derivable_fields(unknown, CANDIDATES)[2])))
    empty_ids = mini_issue(("watchlist", [full_story("h", [])]))
    r.append(check("an EMPTY ids list outside from_the_desk is a hard error too (a treatment of nothing)", complete_derivable_fields(empty_ids, CANDIDATES)[2] != []))

    # ============================================================
    # Bug 2 -- one-treatment enforcement on the REAL run-7 shape
    # ============================================================
    completed, _, _ = complete_derivable_fields(patched_run7(), CANDIDATES)
    r.append(check(
        "before enforcement, check_one_treatment fails on run 7 (fixture 7 in big_one AND who_it_affects; fixture 1 in what_changed AND who_it_affects)",
        {x["claim_text"] for x in check_one_treatment(completed) if x["status"] == "fail"} == {"SYNTHETIC-FIXTURE-7", "SYNTHETIC-FIXTURE-1"},
    ))
    enforced, t_repairs = enforce_one_treatment(completed)
    r.append(check("after enforcement, check_one_treatment has zero failures", not any(x["status"] == "fail" for x in check_one_treatment(enforced))))
    r.append(check("Big One keeps its full treatment of fixture 7, untouched", section(enforced, "big_one")["stories"] == section(completed, "big_one")["stories"]))
    r.append(check("What Changed keeps its full treatments (fixtures 1 and 5), untouched", section(enforced, "what_changed")["stories"] == section(completed, "what_changed")["stories"]))
    who_sec = section(enforced, "who_it_affects")
    orig_who = section(completed, "who_it_affects")["stories"][0]
    r.append(check(
        "who_it_affects entry (both its ids already treated elsewhere) is DEMOTED: removed from stories[]",
        who_sec["stories"] == [],
    ))
    r.append(check(
        "...and replaced by exactly two cross_references pointers (7 and 1) whose text is the entry's OWN headline, never fabricated",
        sorted(x["refers_to_intelligence_story_id"] for x in who_sec["cross_references"]) == ["SYNTHETIC-FIXTURE-1", "SYNTHETIC-FIXTURE-7"]
        and all(x["text"] == orig_who["headline"] for x in who_sec["cross_references"]),
    ))
    r.append(check(
        "the demoted entry's full original content is preserved in the repair record (nothing silently discarded)",
        any(x["action"] == "demoted_entry_to_cross_references" and x["removed_entry"]["body"] == orig_who["body"] for x in t_repairs),
    ))
    r.append(check("the pre-existing what_changed cross_reference to fixture 7 (a legitimate pointer) is left alone", any(x["refers_to_intelligence_story_id"] == "SYNTHETIC-FIXTURE-7" for x in section(enforced, "what_changed")["cross_references"])))
    r.append(check("enforcement never mutates its input", len(section(completed, "who_it_affects")["stories"]) == 1))

    # --- partial strip: an entry that still has a story of its own
    partial = mini_issue(("what_changed", [full_story("A story", ["A"])]), ("watchlist", [full_story("B with A", ["A", "B"])]))
    enf, reps = enforce_one_treatment(partial)
    wl = section(enf, "watchlist")
    r.append(check(
        "an entry citing one treated id and one fresh id keeps its full treatment of the fresh one and pointers the treated one",
        wl["stories"][0]["intelligence_story_ids"] == ["B"] and [x["refers_to_intelligence_story_id"] for x in wl["cross_references"]] == ["A"]
        and reps[0]["action"] == "stripped_duplicate_ids_to_cross_references",
    ))

    # --- priority is schema order, not array order
    reversed_order = mini_issue(("watchlist", [full_story("in watchlist", ["A"])]), ("what_changed", [full_story("in what_changed", ["A"])]))
    enf, _ = enforce_one_treatment(reversed_order)
    r.append(check(
        "priority follows SECTION_PRIORITY (what_changed beats watchlist) even when watchlist comes first in the array",
        section(enf, "what_changed")["stories"] and section(enf, "watchlist")["stories"] == [],
    ))
    r.append(check("big_one outranks what_changed", section(enforce_one_treatment(mini_issue(("what_changed", [full_story("x", ["A"])]), ("big_one", [full_story("y", ["A"])])))[0], "big_one")["stories"] != []))

    # --- idempotent
    again, reps_again = enforce_one_treatment(enforced)
    r.append(check("enforcement is idempotent: a compliant issue comes back unchanged with no repairs", again == enforced and reps_again == []))
    clean, reps_clean = enforce_one_treatment(mini_issue(("big_one", [full_story("a", ["A"])]), ("what_changed", [full_story("b", ["B"])])))
    r.append(check("a compliant issue produces zero repairs on first pass too", reps_clean == []))

    # ============================================================
    # finalize: the one call the runner makes
    # ============================================================
    _, rep_raw = finalize_weekly_brief(RAW_RUN7, CANDIDATES)
    r.append(check("finalize(raw run 7) does NOT pass (Big One has no story id) -- a retry/failure, never a silent return", rep_raw["passed"] is False and rep_raw["hard_errors"]))
    fin, rep_ok = finalize_weekly_brief(patched_run7(), CANDIDATES)
    r.append(check("finalize(patched run 7) passes, shape-clean, one-treatment-clean", rep_ok["passed"] is True and validate_weekly_brief_shape(fin) == [] and not any(x["status"] == "fail" for x in check_one_treatment(fin))))
    r.append(check("finalize's report carries every repair from both steps", any(x["field"] == "eps_scores" for x in rep_ok["repairs"] if "field" in x) and any(x["action"] == "demoted_entry_to_cross_references" for x in rep_ok["repairs"])))

    # ============================================================
    # Runner wiring: free block recovery, one capped retry, loud failure
    # ============================================================
    calls = {"n": 0, "kwargs": []}
    real_call = runner.call_claude_with_tool
    # These checks exercise the STRUCTURAL layer (finalize + capped retry)
    # in isolation. The Evidence Validator now runs after it in the real
    # runner and would block run-7 content on its own merits (real
    # ungrounded claims in that draft) -- that behavior has its own suite,
    # test_weekly_brief_evidence.py. Stubbed here so a structural assertion
    # can't fail for an evidence reason.
    real_evidence = runner.run_evidence_validation
    runner.run_evidence_validation = lambda issue, candidates: {
        "passed": True, "hard_fails": [], "needs_review_count": 0, "per_story": [],
        "editorial_contract": {"passed": True}, "gates_source": "stubbed (structural tests)", "summary": "stubbed",
    }

    def install(responses):
        calls["n"] = 0
        calls["kwargs"] = []

        def fake(api_key, system_prompt, user_prompt, tool_schema, max_tokens=None, return_all_tool_use_blocks=False):
            calls["kwargs"].append({"max_tokens": max_tokens, "return_all_tool_use_blocks": return_all_tool_use_blocks, "tool": tool_schema["name"]})
            out = responses[calls["n"]]
            calls["n"] += 1
            return out
        runner.call_claude_with_tool = fake

    try:
        # first call: the real malformed run 7; retry: a passing block
        install([[RAW_RUN7], [patched_run7()]])
        out = runner.run_weekly_editor_agent(FIXTURES, "key")
        r.append(check("runner: malformed first response -> exactly one retry -> returns the passing, finalized issue", calls["n"] == 2 and out["_validation"]["retry_fired"] is True and out["_validation"]["passed"] is True))
        r.append(check("runner: returned issue is shape-clean and one-treatment-clean", validate_weekly_brief_shape(out) == [] and not any(x["status"] == "fail" for x in check_one_treatment(out))))
        r.append(check("runner: every call asks for ALL tool_use blocks at the newsletter max_tokens", all(k["return_all_tool_use_blocks"] is True and k["max_tokens"] == runner.MAX_TOKENS for k in calls["kwargs"])))

        # free recovery: a later block in the SAME response passes -> no retry
        install([[RAW_RUN7, patched_run7()]])
        out = runner.run_weekly_editor_agent(FIXTURES, "key")
        r.append(check("runner: a passing later block in the same response is used with NO extra call", calls["n"] == 1 and out["_validation"]["retry_fired"] is False))

        # both attempts malformed -> raises, capped at two calls total
        install([[RAW_RUN7], [RAW_RUN7]])
        raised = False
        try:
            runner.run_weekly_editor_agent(FIXTURES, "key")
        except ValueError as e:
            raised = "structural contract" in str(e)
        r.append(check("runner: retry also malformed -> raises (never returns a brief that would persist zero-sourced), exactly two calls, no loop", raised and calls["n"] == 2))
    finally:
        runner.call_claude_with_tool = real_call
        runner.run_evidence_validation = real_evidence

    print()
    p = sum(r)
    print(f"{p}/{len(r)} checks passed")
    raise SystemExit(0 if p == len(r) else 1)
