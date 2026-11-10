"""
Tests that a reader-language violation no longer throws away a finished
brief.

Three consecutive real runs died on language alone while every
structural and factual check was already clean:

  2026-11-10 run 3: 11 language hard_errors (tier/peer-tier family),
                    0 shape errors, 0 provenance errors
  2026-11-10 run 4: attempt 1 'snapshot' x1; attempt 2 'weeks of data' x1
  -- in each case the prose was correct, the provenance complete, the
  teams and depth-chart ranks all right, and the whole draft was
  discarded over one phrase a human fixes in ten seconds.

So: language hits are tagged kind="reader_language", and on the FINAL
attempt only, a brief whose ONLY remaining problems are language hits
proceeds to the Evidence Validator carrying them as review items.
Attempt 1 still fails on language so the retry gets its chance. Any
non-language hard error, or any shape error, blocks exactly as before.

No API call is made anywhere in this file.

Run: python3 nfl/newsletter/test_weekly_brief_language_review.py
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

import run_weekly_brief as rwb  # noqa: E402
import run_weekly_editor_agent as runner  # noqa: E402
from run_weekly_editor_agent import MAX_ATTEMPTS, build_candidate_pool  # noqa: E402
from weekly_brief_evidence import EvidenceValidationFailed  # noqa: E402
from weekly_brief_shape import (  # noqa: E402
    READER_LANGUAGE_BANNED_PHRASES,
    READER_LANGUAGE_KIND,
    check_reader_language,
    finalize_weekly_brief,
    reader_language_hits,
)

FIXTURES = json.loads((HERE / "fixture_v2.json").read_text())
CANDIDATES = build_candidate_pool(FIXTURES)["candidate_story_objects"]
F1 = FIXTURES[0]["intelligence_story_id"]
EPS = {k: 0 for k in ("significance", "evidence_strength", "betting_relevance",
                      "novelty", "story_tension", "audience_relevance", "eps_total")}


def check(label, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    return bool(cond)


def issue(body, ids=(F1,), headline="A clean headline"):
    return {"issue_week": "t", "watchlist_populated": False, "notes_for_human_reviewer": "n",
            "sections": [{"section_type": "big_one", "cross_references": [], "stories": [
                {"headline": headline, "body": body, "intelligence_story_ids": list(ids),
                 "player_ids": [], "pick_ids": [], "eps_scores": dict(EPS)}]}]}


LANGUAGE_ONLY = issue("The price rests on a single snapshot of the line.")
LANGUAGE_PLUS_OTHER = issue("The price rests on a single snapshot.", ids=())  # empty ids = provenance error
CLEAN = issue("He has one other game to compare against, and his track record is thin.")


class Stub:
    """Scripted transport. Records prompts; returns one block per call."""

    def __init__(self, per_call):
        self.per_call = per_call
        self.user_prompts = []

    def __call__(self, api_key, system_prompt, user_prompt, tool_schema, **kw):
        self.user_prompts.append(user_prompt)
        return [copy.deepcopy(self.per_call[min(len(self.user_prompts) - 1, len(self.per_call) - 1)])]


def run(stub):
    orig = runner.call_claude_with_tool
    try:
        runner.call_claude_with_tool = stub
        try:
            return runner.run_weekly_editor_agent(FIXTURES, "no-key", issue_week="t"), None
        except (runner.WeeklyBriefContractFailed, EvidenceValidationFailed) as e:
            return None, e
    finally:
        runner.call_claude_with_tool = orig


def main():
    r = []

    # ==============================================================
    # PART 1 -- the list and its tail text
    # ==============================================================
    r.append(check(
        "'weeks of data' is no longer banned",
        "weeks of data" not in READER_LANGUAGE_BANNED_PHRASES,
    ))
    r.append(check(
        "'snapshot' and 'timestamp' are still banned -- they describe the pipeline, not the football",
        "snapshot" in READER_LANGUAGE_BANNED_PHRASES and "timestamp" in READER_LANGUAGE_BANNED_PHRASES,
    ))
    r.append(check(
        "the list is 18 phrases now",
        len(READER_LANGUAGE_BANNED_PHRASES) == 18,
    ))
    r.append(check(
        "the approved thin-history phrasings pass: 'only one other game to compare against', "
        "'his track record is thin', 'one game so far', \"I'd want a second game\"",
        check_reader_language(issue(
            "There's only one other game to compare against; his track record is thin. "
            "One game so far, and I'd want a second game before believing it.")) == [],
    ))
    prompt = (HERE / "weekly_editor_agent_prompt_v2.md").read_text()
    r.append(check(
        "the prompt's Reader language rule now names the approved phrasings",
        "only one other game to compare against" in prompt and "his track record is thin" in prompt,
    ))
    # Scoped to the Reader language bullet: "weeks of data" still appears
    # in Hard Rule 2 ("the number of games or weeks of data behind a
    # read"), which is a legitimate, unrelated use -- that rule is about
    # where to SOURCE a count, not about banning a reader phrase.
    reader_rule = next(ln for ln in prompt.splitlines() if ln.startswith("- **Reader language."))
    r.append(check(
        "prompt and check agree: the Reader language rule no longer lists 'weeks of data' as banned",
        "weeks of data" not in reader_rule and "snapshots" in reader_rule,
    ))

    # ==============================================================
    # PART 2 -- structured hits
    # ==============================================================
    hits = reader_language_hits(LANGUAGE_ONLY)
    r.append(check(
        "hits are structured and tagged kind='reader_language'",
        len(hits) == 1 and hits[0]["kind"] == READER_LANGUAGE_KIND,
    ))
    r.append(check(
        "each hit carries where / field / phrase / count / message",
        all(k in hits[0] for k in ("where", "field", "phrase", "count", "message"))
        and hits[0]["phrase"] == "snapshot" and hits[0]["field"] == "body",
    ))
    r.append(check(
        "check_reader_language still returns plain message strings (every old caller unchanged)",
        check_reader_language(LANGUAGE_ONLY) == [hits[0]["message"]],
    ))

    # finalize's classification
    _, rep_lang = finalize_weekly_brief(copy.deepcopy(LANGUAGE_ONLY), CANDIDATES)
    r.append(check(
        "language-only: passed=False but language_only_failure=True",
        rep_lang["passed"] is False and rep_lang["language_only_failure"] is True
        and len(rep_lang["language_hits"]) == 1,
    ))
    _, rep_both = finalize_weekly_brief(copy.deepcopy(LANGUAGE_PLUS_OTHER), CANDIDATES)
    r.append(check(
        "language + a provenance error: language_only_failure=False",
        rep_both["language_only_failure"] is False and len(rep_both["hard_errors"]) >= 2,
    ))
    _, rep_clean = finalize_weekly_brief(copy.deepcopy(CLEAN), CANDIDATES)
    r.append(check(
        "a clean brief: passed=True, no hits, language_only_failure=False",
        rep_clean["passed"] is True and rep_clean["language_hits"] == []
        and rep_clean["language_only_failure"] is False,
    ))

    # ==============================================================
    # PART 2 -- the runner's final-attempt behaviour
    # ==============================================================
    stub = Stub([LANGUAGE_ONLY, LANGUAGE_ONLY])
    result, err = run(stub)
    r.append(check(
        "language-only on BOTH attempts: the retry still fires (attempt 1 is not excused)",
        len(stub.user_prompts) == MAX_ATTEMPTS,
    ))
    r.append(check(
        "attempt 1's rejection is quoted back to attempt 2",
        "snapshot" in stub.user_prompts[1] and "REJECTED" in stub.user_prompts[1],
    ))
    reached_validator = result is not None or isinstance(err, EvidenceValidationFailed)
    r.append(check(
        "THE CHANGE: after the final attempt it is NOT blocked by the contract — "
        "it proceeds to the Evidence Validator",
        reached_validator and not isinstance(err, runner.WeeklyBriefContractFailed),
    ))
    accepted = result if result is not None else err.issue
    r.append(check(
        "the hits are carried forward on the issue as _language_review",
        (accepted.get("_language_review") or []) and
        accepted["_language_review"][0]["phrase"] == "snapshot",
    ))
    r.append(check(
        "_validation records it was accepted with language review, and that it did not cleanly pass",
        accepted["_validation"]["accepted_with_language_review"] is True
        and accepted["_validation"]["passed"] is False,
    ))
    r.append(check(
        "the Evidence Validator actually ran on it",
        "_evidence_validation" in accepted,
    ))

    # language + another error must STILL block
    stub_both = Stub([LANGUAGE_PLUS_OTHER, LANGUAGE_PLUS_OTHER])
    result_both, err_both = run(stub_both)
    r.append(check(
        "language PLUS a non-language hard error still blocks on the contract, as before",
        result_both is None and isinstance(err_both, runner.WeeklyBriefContractFailed),
    ))

    # a clean pass is unchanged
    stub_clean = Stub([CLEAN])
    result_clean, err_clean = run(stub_clean)
    r.append(check(
        "a clean brief still passes on attempt 1 with ONE call and no language review",
        len(stub_clean.user_prompts) == 1
        and (result_clean is not None or isinstance(err_clean, EvidenceValidationFailed)),
    ))
    clean_issue = result_clean if result_clean is not None else err_clean.issue
    r.append(check(
        "a clean pass carries an EMPTY language review and passed=True",
        (clean_issue.get("_language_review") or []) == []
        and clean_issue["_validation"]["passed"] is True
        and clean_issue["_validation"]["accepted_with_language_review"] is False,
    ))

    # a brief that fails language on attempt 1 and is CLEAN on attempt 2
    stub_fixed = Stub([LANGUAGE_ONLY, CLEAN])
    result_fixed, err_fixed = run(stub_fixed)
    fixed_issue = result_fixed if result_fixed is not None else err_fixed.issue
    r.append(check(
        "the retry fixing the phrase yields a genuinely clean pass, not a language review",
        len(stub_fixed.user_prompts) == 2 and (fixed_issue.get("_language_review") or []) == []
        and fixed_issue["_validation"]["passed"] is True,
    ))

    # ==============================================================
    # PART 2 -- the .md Language review section
    # ==============================================================
    md = rwb.render_markdown(
        accepted,
        {"season": 2026, "week": 9, "run_at": "t", "cost_estimate_usd": 0.0, "mode": "dry",
         "selection": {"by_family": {}, "rows_read": 0, "wrong_week": 0,
                       "not_enriched": 0, "dropped_by_cap": 0}},
        accepted.get("_evidence_validation"))
    r.append(check(
        "the .md carries a clearly labelled 'Language review' heading listing each hit",
        "### Language review" in md and "'snapshot'" in md,
    ))
    r.append(check(
        "the .md says plainly that the draft was NOT blocked for them",
        "NOT blocked" in md,
    ))
    md_clean = rwb.render_markdown(
        clean_issue,
        {"season": 2026, "week": 9, "run_at": "t", "cost_estimate_usd": 0.0, "mode": "dry",
         "selection": {"by_family": {}, "rows_read": 0, "wrong_week": 0,
                       "not_enriched": 0, "dropped_by_cap": 0}},
        clean_issue.get("_evidence_validation"))
    r.append(check(
        "a clean pass renders NO Language review section",
        "### Language review" not in md_clean,
    ))

    print()
    if all(r):
        print(f"All {len(r)} checks passed.")
    else:
        print(f"{len(r) - sum(r)} of {len(r)} checks FAILED — see above.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
