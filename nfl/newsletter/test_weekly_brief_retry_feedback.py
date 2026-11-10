"""
Tests that the Weekly Editor Agent's one capped retry actually carries
feedback.

The defect: run_weekly_editor_agent built `user_prompt` once, before the
attempt loop, and passed it unchanged to both calls. Attempt 2 was
therefore a byte-identical re-sample that learned nothing about why
attempt 1 was rejected.

Confirmed live 2026-11-09 against the real Week 9 pool: the two attempts
failed in two DIFFERENT ways -- attempt 1 omitted all four provenance
fields on the Big One, attempt 2 cited an empty intelligence_story_ids
on What Changed. Two independent draws from one prompt, which is exactly
what a retry with no feedback produces.

Focus: attempt 1's input is unchanged, attempt 2's input carries the
first attempt's errors VERBATIM (all of them, not just the first), and a
passing first attempt sends no feedback at all because there is no
second call.

No API call is made anywhere in this file: call_claude_with_tool is
replaced with a local stub that records the prompts it was handed.

Run: python3 nfl/newsletter/test_weekly_brief_retry_feedback.py
"""
import copy
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "api"))
sys.path.insert(0, str(HERE.parent / "content_writer"))

import run_weekly_editor_agent as runner  # noqa: E402
from run_weekly_editor_agent import (  # noqa: E402
    RETRY_INSTRUCTION,
    WeeklyBriefContractFailed,
    build_candidate_pool,
    build_retry_feedback,
)
from weekly_brief_evidence import EvidenceValidationFailed  # noqa: E402

FIXTURES = json.loads((HERE / "fixture_v2.json").read_text())
ISSUE_WEEK = "2026 NFL Week 9"
POOL = build_candidate_pool(FIXTURES, ISSUE_WEEK)
EXPECTED_ATTEMPT1_PROMPT = json.dumps(POOL, indent=2)
F7 = next(f for f in FIXTURES if f["intelligence_story_id"] == "SYNTHETIC-FIXTURE-7")


def check(label, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    return bool(cond)


def eps_from(fixture):
    dims = fixture["eps"]["dimensions"]
    out = {k: dims[k]["score"] for k in
           ("significance", "evidence_strength", "betting_relevance",
            "novelty", "story_tension", "audience_relevance")}
    out["eps_total"] = fixture["eps"]["composite_score"]
    return out


def story(headline, ids, players, eps):
    return {"headline": headline, "body": "body text here", "intelligence_story_ids": ids,
            "player_ids": players, "pick_ids": [], "eps_scores": eps}


def issue(sections):
    return {"issue_week": ISSUE_WEEK,
            "sections": [{"section_type": st, "stories": s, "cross_references": []} for st, s in sections],
            "watchlist_populated": False, "notes_for_human_reviewer": "notes"}


def failing_issue():
    """The 2026-11-09 attempt-1 shape: a Big One with no provenance
    fields at all, which yields one hard_error plus four shape_errors."""
    return {"issue_week": ISSUE_WEEK,
            "sections": [{"section_type": "big_one",
                          "stories": [{"headline": "h", "body": "b"}],
                          "cross_references": []}],
            "watchlist_populated": False, "notes_for_human_reviewer": "notes"}


def passing_issue():
    return issue([("big_one", [story("Clean lead", [F7["intelligence_story_id"]],
                                     [F7["player_id"]], eps_from(F7))])])


class Recorder:
    """Stub transport: records every (system, user) prompt it is handed
    and returns a scripted block per call."""

    def __init__(self, blocks_per_call):
        self.blocks_per_call = blocks_per_call
        self.system_prompts = []
        self.user_prompts = []

    def __call__(self, api_key, system_prompt, user_prompt, tool_schema, **kw):
        self.system_prompts.append(system_prompt)
        self.user_prompts.append(user_prompt)
        return copy.deepcopy(self.blocks_per_call[len(self.user_prompts) - 1])


def run_with(rec):
    orig = runner.call_claude_with_tool
    try:
        runner.call_claude_with_tool = rec
        try:
            return runner.run_weekly_editor_agent(FIXTURES, "no-key", issue_week=ISSUE_WEEK), None
        except (WeeklyBriefContractFailed, EvidenceValidationFailed) as e:
            return None, e
    finally:
        runner.call_claude_with_tool = orig


def main():
    r = []

    # ==============================================================
    # 1. Attempt 1's input is UNCHANGED, and attempt 2 is the only
    #    call that carries feedback.
    # ==============================================================
    rec = Recorder([[failing_issue()], [failing_issue()]])
    _, failure = run_with(rec)

    r.append(check("two calls were made -- still exactly one capped retry", len(rec.user_prompts) == 2))
    r.append(check(
        "ATTEMPT 1's user prompt is byte-identical to the serialized pool (unchanged behaviour)",
        rec.user_prompts[0] == EXPECTED_ATTEMPT1_PROMPT,
    ))
    r.append(check(
        "attempt 1 carries NO feedback text",
        RETRY_INSTRUCTION not in rec.user_prompts[0] and "REJECTED" not in rec.user_prompts[0],
    ))
    r.append(check(
        "the system prompt is identical across both calls (stays the cached prefix)",
        rec.system_prompts[0] == rec.system_prompts[1],
    ))
    r.append(check(
        "ATTEMPT 2 still contains the whole original pool, unmodified, as its prefix",
        rec.user_prompts[1].startswith(EXPECTED_ATTEMPT1_PROMPT),
    ))
    r.append(check(
        "attempt 2 is strictly longer than attempt 1 -- the pool plus a follow-up in the same turn",
        len(rec.user_prompts[1]) > len(rec.user_prompts[0]),
    ))

    # ==============================================================
    # 2. Attempt 2's input contains attempt 1's errors VERBATIM --
    #    every one of them, not just the first.
    # ==============================================================
    attempt1 = [a for a in failure.attempts if a["attempt"] == 1]
    hard = [e for a in attempt1 for e in a["hard_errors"]]
    shape = [e for a in attempt1 for e in a["shape_errors"]]
    feedback = rec.user_prompts[1][len(EXPECTED_ATTEMPT1_PROMPT):]

    r.append(check(
        f"attempt 1 really did produce multiple errors ({len(hard)} hard, {len(shape)} shape) "
        "-- otherwise 'all of them' proves nothing",
        len(hard) >= 1 and len(shape) >= 2,
    ))
    r.append(check(
        "every hard_error from attempt 1 appears VERBATIM in attempt 2's input",
        all(e in feedback for e in hard),
    ))
    r.append(check(
        "every shape_error from attempt 1 appears VERBATIM in attempt 2's input",
        all(e in feedback for e in shape),
    ))
    r.append(check(
        "the errors are labelled by kind so the model can tell them apart",
        all(f"- hard_error: {e}" in feedback for e in hard)
        and all(f"- shape_error: {e}" in feedback for e in shape),
    ))
    r.append(check(
        "the required instruction follows the error list, verbatim",
        RETRY_INSTRUCTION in feedback
        and feedback.index(RETRY_INSTRUCTION) > feedback.index(hard[0]),
    ))
    r.append(check(
        "the instruction is exactly the specified wording",
        RETRY_INSTRUCTION == (
            "Your previous attempt was rejected for the reasons above. Return the complete brief again "
            "with every error fixed. Do not shorten or drop sections to fix it."
        ),
    ))
    r.append(check(
        "no third call is ever made, feedback or not",
        len(rec.user_prompts) == 2,
    ))

    # ==============================================================
    # 3. A PASSING attempt 1 sends no feedback -- there is no attempt 2.
    # ==============================================================
    rec_ok = Recorder([[passing_issue()], [failing_issue()]])
    result_ok, failure_ok = run_with(rec_ok)
    returned = result_ok is not None or isinstance(failure_ok, EvidenceValidationFailed)

    r.append(check(
        "a passing attempt 1 makes exactly ONE call -- no retry, so no feedback",
        len(rec_ok.user_prompts) == 1,
    ))
    r.append(check(
        "that single call's input is the unmodified pool",
        rec_ok.user_prompts[0] == EXPECTED_ATTEMPT1_PROMPT,
    ))
    r.append(check(
        "it contains no feedback text of any kind",
        RETRY_INSTRUCTION not in rec_ok.user_prompts[0] and "REJECTED" not in rec_ok.user_prompts[0],
    ))
    r.append(check(
        "the run got past the structural contract (returned an issue, or was stopped by the "
        "Evidence Validator -- either way the contract passed)",
        returned,
    ))

    # ==============================================================
    # 4. build_retry_feedback in isolation
    # ==============================================================
    r.append(check(
        "build_retry_feedback returns None when there is nothing to report "
        "(so 'no feedback' is distinguishable from 'empty feedback')",
        build_retry_feedback([{"hard_errors": [], "shape_errors": []}]) is None
        and build_retry_feedback([]) is None,
    ))
    multi = build_retry_feedback([
        {"hard_errors": ["h1"], "shape_errors": ["s1", "s2"]},
        {"hard_errors": ["h1", "h2"], "shape_errors": ["s2"]},
    ])
    r.append(check(
        "errors from EVERY block of the attempt are included, de-duplicated, in order",
        multi.count("- hard_error: h1") == 1 and "- hard_error: h2" in multi
        and multi.count("- shape_error: s2") == 1 and "- shape_error: s1" in multi,
    ))
    r.append(check(
        "nothing is summarised or truncated -- the error strings are passed through as given",
        "- hard_error: h1" in multi and RETRY_INSTRUCTION in multi,
    ))

    print()
    if all(r):
        print(f"All {len(r)} checks passed.")
    else:
        print(f"{len(r) - sum(r)} of {len(r)} checks FAILED — see above.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
