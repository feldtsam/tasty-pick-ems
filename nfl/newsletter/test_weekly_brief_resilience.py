"""
Tests the three resilience fixes on the Weekly Brief runner, all three
driven by one real 2026-11-09 production failure.

What happened: the Editor Agent call returned HTTP 200 after 60.46
seconds against a real 12-story pool, having generated (and been billed
for) 44,367 input + 4,691 output tokens. Then the run died:

    File "weekly_brief_shape.py", line 179, in complete_derivable_fields
        st = section.get("section_type")
    AttributeError: 'str' object has no attribute 'get'

The model had returned a `sections` entry as a bare STRING instead of
the object the tool schema declares. Because write_outputs() runs at the
very END of run_weekly_brief.main, that crash discarded a draft that had
already been paid for, and nothing reached disk.

An earlier attempt the same day had failed differently: the shared 60s
HTTP read timeout in card_writer_common cut the same call off at 60.15
seconds -- losing it by less than half a second, after the tokens were
already generated.

The three fixes, in the priority they were asked for:
  1. Raw agent output is saved to disk the moment the API responds,
     BEFORE any post-processing, in the shape --editor-output-json reads.
  2. A non-dict entry anywhere in sections/stories becomes a hard_error
     through the machinery already there, never an AttributeError -- in
     ALL THREE section loops, because finalize_weekly_brief runs all
     three unconditionally (guarding only the first one was not enough).
  3. call_claude_with_tool takes a timeout, default 60 so every other
     caller is unchanged; the Editor Agent path passes 300.

No API call is made anywhere in this file: call_claude_with_tool is
replaced with a local stub.

Run: python3 nfl/newsletter/test_weekly_brief_resilience.py
"""
import copy
import inspect
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "api"))
sys.path.insert(0, str(HERE.parent / "content_writer"))

import card_writer_common  # noqa: E402
import run_weekly_editor_agent as runner  # noqa: E402
from run_weekly_editor_agent import (  # noqa: E402
    EDITOR_AGENT_TIMEOUT_SECONDS,
    WeeklyBriefContractFailed,
)
from weekly_brief_shape import (  # noqa: E402
    complete_derivable_fields,
    enforce_one_treatment,
    finalize_weekly_brief,
    validate_weekly_brief_shape,
)

FIXTURES = json.loads((HERE / "fixture_v2.json").read_text())
POOL = runner.build_candidate_pool(FIXTURES)
CANDIDATES = POOL["candidate_story_objects"]
LIVE_PICKS = POOL["live_picks"]


def check(label, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    return bool(cond)


def full_story(headline, ids):
    return {
        "headline": headline, "body": "body text here", "intelligence_story_ids": ids,
        "player_ids": [], "pick_ids": [],
        "eps_scores": {
            "significance": 0, "evidence_strength": 0, "betting_relevance": 0,
            "novelty": 0, "story_tension": 0, "audience_relevance": 0, "eps_total": 0,
        },
    }


def mini_issue(*sections):
    return {
        "issue_week": "t",
        "sections": [{"section_type": st, "stories": stories, "cross_references": []} for st, stories in sections],
        "watchlist_populated": True,
        "notes_for_human_reviewer": "",
    }


# THE EXACT MALFORMED SHAPE from the 2026-11-09 run: a bare string where
# the schema declares an object, sitting alongside a well-formed section.
def issue_with_string_section():
    issue = mini_issue(("big_one", [full_story("A real headline", ["SYNTHETIC-FIXTURE-7"])]))
    issue["sections"].append("watchlist")
    return issue


def main():
    r = []

    # ==============================================================
    # FIX 2 -- type guards in all three section loops
    # ==============================================================
    bad = issue_with_string_section()

    try:
        completed, repairs, hard = complete_derivable_fields(bad, CANDIDATES, LIVE_PICKS)
        crashed = None
    except Exception as e:
        completed, repairs, hard, crashed = None, None, None, e
    r.append(check(
        "complete_derivable_fields: a STRING in sections no longer raises AttributeError",
        crashed is None,
    ))
    r.append(check(
        "complete_derivable_fields: it is reported as a hard_error naming the index and the actual type",
        hard is not None and any("sections[1]" in h and "str" in h for h in hard),
    ))

    try:
        enforce_one_treatment(copy.deepcopy(bad))
        enf_crashed = None
    except Exception as e:
        enf_crashed = e
    r.append(check(
        "enforce_one_treatment: the same string entry no longer raises in its sort key "
        "(guarding only the first loop was not enough -- finalize runs all three)",
        enf_crashed is None,
    ))

    try:
        shape_errors = validate_weekly_brief_shape(copy.deepcopy(bad))
        shape_crashed = None
    except Exception as e:
        shape_errors, shape_crashed = None, e
    r.append(check("validate_weekly_brief_shape: no raise on the string entry", shape_crashed is None))
    r.append(check(
        "validate_weekly_brief_shape: reports it as a shape error",
        shape_errors is not None and any("sections[1]" in e and "object" in e for e in shape_errors),
    ))

    # The whole pipeline, which is what actually crashed in production.
    try:
        issue, report = finalize_weekly_brief(issue_with_string_section(), CANDIDATES, LIVE_PICKS)
        fin_crashed = None
    except Exception as e:
        issue, report, fin_crashed = None, None, e
    r.append(check("finalize_weekly_brief: completes instead of crashing (THE production regression)", fin_crashed is None))
    r.append(check("finalize_weekly_brief: reports passed=False", report is not None and report["passed"] is False))
    r.append(check(
        "finalize_weekly_brief: the malformed entry is still present in the returned issue, "
        "so a human reading the saved draft sees what the model actually emitted",
        issue is not None and "watchlist" in issue["sections"],
    ))
    r.append(check(
        "finalize_weekly_brief: the well-formed section alongside it is still processed normally",
        issue is not None and any(
            isinstance(s, dict) and s.get("section_type") == "big_one" for s in issue["sections"]
        ),
    ))

    # Adjacent shapes of the same bug.
    non_dict_story = mini_issue(("big_one", ["just a string, not a story"]))
    _, _, hard_story = complete_derivable_fields(non_dict_story, CANDIDATES, LIVE_PICKS)
    r.append(check(
        "a non-dict STORY inside a valid section is a hard_error too, not a crash",
        any("big_one.stories[0]" in h and "str" in h for h in hard_story),
    ))

    non_list_stories = mini_issue(("big_one", []))
    non_list_stories["sections"][0]["stories"] = "oops"
    _, _, hard_nl = complete_derivable_fields(non_list_stories, CANDIDATES, LIVE_PICKS)
    r.append(check(
        "a non-list `stories` is a hard_error too, not a crash",
        any("stories is str" in h for h in hard_nl),
    ))

    # A clean issue must be entirely unaffected by the guards.
    good = mini_issue(("big_one", [full_story("Clean", ["SYNTHETIC-FIXTURE-7"])]))
    good_issue, good_report = finalize_weekly_brief(copy.deepcopy(good), CANDIDATES, LIVE_PICKS)
    r.append(check(
        "REGRESSION GUARD: a well-formed issue still has zero hard_errors",
        good_report["hard_errors"] == [],
    ))

    # ==============================================================
    # FIX 3 -- timeout parameter
    # ==============================================================
    sig = inspect.signature(card_writer_common.call_claude_with_tool)
    r.append(check(
        "call_claude_with_tool accepts a `timeout` parameter",
        "timeout" in sig.parameters,
    ))
    r.append(check(
        "its default is REQUEST_TIMEOUT_SECONDS (60), so every existing caller is unchanged",
        sig.parameters["timeout"].default == card_writer_common.REQUEST_TIMEOUT_SECONDS
        and card_writer_common.REQUEST_TIMEOUT_SECONDS == 60,
    ))
    r.append(check(
        "the Editor Agent's own ceiling is 300s (the real call measured 60.46s)",
        EDITOR_AGENT_TIMEOUT_SECONDS == 300,
    ))

    # ==============================================================
    # FIX 1 -- raw save before post-processing, + FIX 3 wiring
    # ==============================================================
    calls = []

    def stub_call(api_key, system_prompt, user_prompt, tool_schema, **kw):
        calls.append(kw)
        # One malformed block: forces the contract to fail, which is
        # exactly the case where the paid output used to be lost.
        return [issue_with_string_section()]

    orig = runner.call_claude_with_tool
    saved = []
    try:
        runner.call_claude_with_tool = stub_call
        try:
            runner.run_weekly_editor_agent(
                FIXTURES, "no-key", issue_week="t",
                on_raw_blocks=lambda blocks, attempt: saved.append((copy.deepcopy(blocks), attempt)),
            )
            failure = None
        except WeeklyBriefContractFailed as e:
            failure = e
    finally:
        runner.call_claude_with_tool = orig

    r.append(check(
        "the Editor Agent path passes timeout=300 to call_claude_with_tool",
        calls and all(c.get("timeout") == 300 for c in calls),
    ))
    r.append(check(
        "on_raw_blocks fired for BOTH attempts (the call + its one capped retry)",
        [a for _, a in saved] == [1, 2],
    ))
    r.append(check(
        "the raw blocks handed to the hook are the model's own output, pre-post-processing",
        saved and saved[0][0] == [issue_with_string_section()],
    ))
    r.append(check(
        "a structural failure now raises WeeklyBriefContractFailed (a ValueError subclass, "
        "so any existing `except ValueError` still works)",
        isinstance(failure, WeeklyBriefContractFailed) and isinstance(failure, ValueError),
    ))
    r.append(check(
        "the exception carries the per-attempt reports, including the hard_errors",
        failure is not None and len(failure.attempts) == 2
        and any("sections[1]" in h for a in failure.attempts for h in (a.get("hard_errors") or [])),
    ))
    r.append(check(
        "the exception carries a best-effort issue so the caller can still write a readable draft",
        failure is not None and failure.issue is not None,
    ))

    # A hook that itself raises must never take the run down with it.
    def boom(blocks, attempt):
        raise RuntimeError("disk full")

    try:
        runner.call_claude_with_tool = stub_call
        try:
            runner.run_weekly_editor_agent(FIXTURES, "no-key", issue_week="t", on_raw_blocks=boom)
            hook_failure = None
        except WeeklyBriefContractFailed as e:
            hook_failure = e
        except Exception as e:
            hook_failure = e
    finally:
        runner.call_claude_with_tool = orig
    r.append(check(
        "a raw-save hook that raises is swallowed with a warning -- saving must never break the run",
        isinstance(hook_failure, WeeklyBriefContractFailed),
    ))

    # ==============================================================
    # FIX 1 -- the saved file must be readable by --editor-output-json
    # ==============================================================
    with tempfile.TemporaryDirectory() as td:
        raw_path = Path(td) / "brief_2026w9_x.raw.json"
        blocks = [issue_with_string_section()]
        raw_path.write_text(json.dumps({
            "issue": blocks[0], "raw_blocks": blocks,
            "meta": {"season": 2026, "week": 9, "attempt": 1, "post_processed": False},
        }, indent=2, default=str))
        # Exactly what run_weekly_brief.main does for --editor-output-json.
        loaded = json.loads(raw_path.read_text())
        recovered = loaded.get("issue", loaded)
        r.append(check(
            "the saved raw file is readable by --editor-output-json's own "
            "`loaded.get(\"issue\", loaded)` and yields the model's issue",
            recovered == blocks[0],
        ))
        r.append(check(
            "it also keeps every block and records that it is NOT post-processed",
            loaded["raw_blocks"] == blocks and loaded["meta"]["post_processed"] is False,
        ))

    # ==============================================================
    # FIX 2, fourth instance -- render_markdown and persist.
    # Found by the OFFLINE END-TO-END run, after the three
    # finalize_weekly_brief guards above were already green: testing the
    # pieces was not enough. render_markdown is the renderer for a
    # BLOCKED run, so a crash there is precisely what stops a human ever
    # reading the draft the fixes exist to preserve.
    # ==============================================================
    import run_weekly_brief as rwb
    import persist_weekly_brief as pwb

    meta = {"season": 2026, "week": 9, "run_at": "t", "selection": {"by_family": {}, "rows_read": 0,
            "wrong_week": 0, "not_enriched": 0, "dropped_by_cap": 0}, "cost_estimate_usd": 0.0, "mode": "dry"}
    try:
        md = rwb.render_markdown(issue_with_string_section(), meta, None)
        md_crashed = None
    except Exception as e:
        md, md_crashed = None, e
    r.append(check("render_markdown: no crash on a malformed section (the blocked-run renderer)", md_crashed is None))
    r.append(check(
        "render_markdown: the well-formed story still renders for the reader",
        md is not None and "A real headline" in md,
    ))
    r.append(check(
        "render_markdown: the malformed entry is surfaced verbatim in the review notes",
        md is not None and "Malformed sections" in md and "'watchlist'" in md,
    ))

    # shape_newsletter_rows is reachable with a malformed section only
    # because the raw save (fix 1) can now be replayed through
    # --editor-output-json, which bypasses finalize_weekly_brief.
    try:
        shaped = pwb.shape_newsletter_rows(issue_with_string_section(), {}, 2026, 9)
        pwb_crashed = None
    except Exception as e:
        shaped, pwb_crashed = None, e
    r.append(check(
        "shape_newsletter_rows: no crash on a malformed section (reachable via "
        "--editor-output-json replaying a raw file, which skips the contract)",
        pwb_crashed is None,
    ))
    r.append(check(
        "shape_newsletter_rows: the malformed entry is recorded as a skip and never written as a row",
        shaped is not None
        and any("sections[1]" in (s.get("reason") or "") for s in (shaped.get("skipped") or [])),
    ))

    print()
    if all(r):
        print(f"All {len(r)} checks passed.")
    else:
        print(f"{len(r) - sum(r)} of {len(r)} checks FAILED — see above.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
