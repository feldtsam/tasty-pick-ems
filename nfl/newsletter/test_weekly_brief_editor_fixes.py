"""
Tests the three 2026-11-10 fixes to the Weekly Brief editor path.

All three came out of one real run (brief_2026w9_20261110T015547): the
Editor Agent passed the structural contract for the first time, the
Evidence Validator ran for the first time, and it blocked with 1 hard
fail and 32 needs-review flags -- of which almost all turned out to be
the validator's own blind spots rather than problems with the draft.

  PART 1  The candidate pool was dropping team, related_players,
          what_changed and evidence_classification. Consequences, all
          observed: the draft put Makai Lemon (PHI) on "the Lions" and
          four Titans "in Chicago" because it had no team field at all;
          the validator's name pool was missing team and
          related_players, so real names ("Rashid Shaheed", "Detroit")
          flagged as possibly fabricated; and
          check_evidence_confidence_alignment reported
          evidence_classification missing on every story.

  PART 2a The number pool never read interrogation.judgment, which is
          where the per-week usage numbers live, and had no way to match
          a percent claim against a stored fraction. "nearly 90% of
          Detroit's snaps" hard-failed as invented while
          judgment.what_we_know said "snap_share ~0.882".

  PART 2b The name detector produced 26 flags, 21 of them noise:
          sentence openers, Title Case headline fragments, stopword-only
          runs, and "DeVonta" split into "De" + "Vonta Smith".

  PART 3  The prompt's new reader-language rule was ignored by the model
          -- 12 banned phrases survived into the draft -- so the rule is
          now also enforced deterministically, as a contract hard_error
          that reaches attempt 2 through the retry feedback.

No API call is made anywhere in this file.

Run: python3 nfl/newsletter/test_weekly_brief_editor_fixes.py
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "api"))
sys.path.insert(0, str(HERE.parent / "content_writer"))

from evidence_validator import (  # noqa: E402
    _APPROX_TOLERANCE,
    _extract_name_candidates,
    _real_name_pool,
    _story_number_entries,
    check_claim_traceability,
)
from run_weekly_editor_agent import build_candidate_pool  # noqa: E402
from weekly_brief_evidence import validator_stories_by_id  # noqa: E402
from weekly_brief_shape import (  # noqa: E402
    READER_LANGUAGE_BANNED_PHRASES,
    check_reader_language,
    finalize_weekly_brief,
)

FIXTURES = json.loads((HERE / "fixture_v2.json").read_text())
SAVED_RAW = HERE / "outputs" / "brief_2026w9_20261110T015547.raw.json"

NEW_POOL_FIELDS = ("team", "related_players", "what_changed", "evidence_classification")


def check(label, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    return bool(cond)


def story_row(**over):
    """A story-object-shaped candidate, in the pool's own shape."""
    base = {
        "intelligence_story_id": "S1", "player_id": "00-0000001",
        "intelligence_family": "market_intelligence", "entity": "Amon-Ra St. Brown",
        "headline": "h", "story": "s", "time_window": "tw", "sample_size": 4,
        "team": "DET", "related_players": [], "what_changed": [],
        "evidence_classification": "strong", "interrogation": {}, "eps": {},
    }
    base.update(over)
    return base


def main():
    r = []

    # ==============================================================
    # PART 1 -- pool fields
    # ==============================================================
    rows = [{
        "intelligence_story_id": "S1", "player_id": "p", "intelligence_family": "role_changes",
        "entity": "Tory Horton", "headline": "h", "story": "s", "time_window": "tw",
        "sample_size": 7, "interrogation": {}, "eps": {},
        "team": "SEA",
        "related_players": [{"display_label": "Rashid Shaheed", "note": "Injured (Out)"}],
        "what_changed": [{"label": "Sample size", "observation": "Based on 7 real game(s) this season."}],
        "evidence_classification": "strong",
    }]
    pool = build_candidate_pool(rows, "2026 NFL Week 9")
    cand = pool["candidate_story_objects"][0]

    for field in NEW_POOL_FIELDS:
        r.append(check(f"pool carries {field!r}", field in cand))
    r.append(check("team is a plain string, not nested", cand["team"] == "SEA"))
    r.append(check(
        "every pre-existing field is still present",
        all(k in cand for k in ("intelligence_story_id", "player_id", "intelligence_family", "entity",
                                "headline", "story", "time_window", "sample_size", "interrogation", "eps")),
    ))
    r.append(check(
        "related_players and what_changed pass through unchanged",
        cand["related_players"] == rows[0]["related_players"] and cand["what_changed"] == rows[0]["what_changed"],
    ))

    fixture_pool = build_candidate_pool(FIXTURES)
    r.append(check(
        "CALIBRATION FIXTURES still build (they predate all four fields -> None, no KeyError)",
        len(fixture_pool["candidate_story_objects"]) == len(FIXTURES)
        and all(fixture_pool["candidate_story_objects"][0][f] is None for f in NEW_POOL_FIELDS),
    ))

    reshaped = validator_stories_by_id([cand])["S1"]
    r.append(check(
        "validator_stories_by_id carries team into entity so the name pool sees it",
        reshaped["entity"] == {"player_name": "Tory Horton", "team": "SEA"},
    ))
    name_pool = _real_name_pool([reshaped])
    r.append(check(
        "THE REGRESSION: the name pool now holds the team and every related player",
        {"Tory Horton", "SEA", "Rashid Shaheed"} <= name_pool,
    ))

    # ==============================================================
    # PART 2a -- numeric claims
    # ==============================================================
    judgment_story = validator_stories_by_id([story_row(interrogation={
        "judgment": {"what_we_know": "season usage (snap_share ~0.882, targets ~11.4/game) over 5 weeks"}})])["S1"]
    entries = _story_number_entries(judgment_story)
    r.append(check(
        "interrogation.judgment is now read into the number pool (it was the one half being skipped)",
        (11.4, "count") in entries,
    ))
    r.append(check(
        "a stored fraction also enters the pool as a percent (0.882 -> 88.2%)",
        (88.2, "percent") in entries,
    ))
    r.append(check(
        "the conversion is one-directional -- a percent does not become a fraction",
        not any(abs(v - 0.882) < 1e-9 and u == "percent" for v, u in entries),
    ))

    def numeric(text, stories):
        return [x for x in check_claim_traceability(text, stories) if x["claim_type"] == "numeric"]

    res = numeric("playing at nearly 90% of Detroit's snaps", [judgment_story])
    hit = next(x for x in res if x["claim_text"] == "90%")
    r.append(check(
        "THE HARD FAIL FROM THE REAL RUN: 'nearly 90%' against a real 0.882 now passes as 'rounded'",
        hit["status"] == "pass" and hit["grounding"] == "rounded" and hit["rounded_from"] == 88.2,
    ))

    far = validator_stories_by_id([story_row(
        interrogation={"judgment": {"what_we_know": "snap_share ~0.700 over 5 weeks"}})])["S1"]
    hit_far = next(x for x in numeric("playing at nearly 90% of snaps", [far]) if x["claim_text"] == "90%")
    r.append(check(
        "MUST STILL HARD-FAIL: 'nearly 90%' where the nearest real value is 70% "
        f"(outside the {_APPROX_TOLERANCE}-point hedge tolerance)",
        hit_far["status"] == "fail",
    ))
    hit_unhedged = next(x for x in numeric("playing at 90% of snaps", [judgment_story]) if x["claim_text"] == "90%")
    r.append(check(
        "the wider tolerance is unlocked by the hedge word ONLY -- an unhedged '90%' against 88.2 still fails",
        hit_unhedged["status"] == "fail",
    ))

    # ==============================================================
    # PART 2b -- name detector
    # ==============================================================
    r.append(check(
        "sentence-opening words are not names ('So', 'But', 'Here', 'It')",
        _extract_name_candidates("So I looked. But it held. Here is why. It stayed.") == [],
    ))
    r.append(check(
        "a Title Case headline is not sliced into name fragments",
        _extract_name_candidates(
            "Amon-Ra St. Brown Is Priced Like a Different Player Than the One Taking the Field") == [],
    ))
    r.append(check(
        "stopword-only runs are not names ('Caught Up', 'Different Question')",
        _extract_name_candidates("x Caught Up. y Different Question.") == [],
    ))
    devonta = _extract_name_candidates("With DeVonta Smith out, Makai Lemon stepped up.")
    r.append(check(
        "an intra-word capital no longer splits a name: 'DeVonta Smith' is ONE candidate, "
        "not 'De' plus 'Vonta Smith'",
        "DeVonta Smith" in devonta and "De" not in devonta and "Vonta Smith" not in devonta,
    ))
    r.append(check(
        "A REAL UNKNOWN NAME STILL FLAGS: 'Tyreek Hill' in a Detroit story whose rows never mention him",
        any(x["claim_text"] == "Tyreek Hill" and x["status"] == "needs_review"
            for x in check_claim_traceability(
                "Detroit leaned on Tyreek Hill all afternoon.",
                [validator_stories_by_id([story_row()])["S1"]])),
    ))
    r.append(check(
        "a headline fragment containing only KNOWN names grounds, but a fabricated one does not",
        _real_name_pool([validator_stories_by_id([story_row()])["S1"]])
        and not any(x["claim_text"] == "Tyreek Brown" and x["status"] == "pass"
                    for x in check_claim_traceability(
                        "x Tyreek Brown ran it in.",
                        [validator_stories_by_id([story_row()])["S1"]])),
    ))

    # ==============================================================
    # PART 3 -- reader language
    # ==============================================================
    # 19 since the 2026-11-10 revision: tier / tiers / peer tier /
    # peer-tier removed (the app's own Market card says "peer tier", so it
    # is reader vocabulary), role-momentum / role momentum / external
    # opportunity / "i'm not given" / "i wasn't given" added. See
    # test_weekly_brief_malformed_envelope.py for the full list coverage.
    # 18 since "weeks of data" was removed -- three of the four observed
    # blockers were the agent being honest that a sample is thin, which
    # the voice calibration asks for; the prompt now names approved
    # phrasings instead of banning every way of saying it.
    r.append(check(
        "the banned-phrase list is exactly the 18 phrases specified, not widened",
        len(READER_LANGUAGE_BANNED_PHRASES) == 18
        and "evidence" not in READER_LANGUAGE_BANNED_PHRASES
        and "measure" not in READER_LANGUAGE_BANNED_PHRASES
        and "signal" not in READER_LANGUAGE_BANNED_PHRASES
        and "tier" not in READER_LANGUAGE_BANNED_PHRASES,
    ))
    clean = {"sections": [{"section_type": "big_one", "cross_references": [], "stories": [
        {"headline": "A measure of evidence",
         "body": "The evidence is clear. On that specific measure, a legitimate opportunity signal."}]}]}
    r.append(check(
        "MUST NOT FLAG 'evidence', 'measure' or 'signal' used alone",
        check_reader_language(clean) == [],
    ))
    dirty = {"sections": [{"section_type": "big_one", "cross_references": [], "stories": [
        {"headline": "h", "body": "The benchmark says one thing; the role score is maxed out, "
                                  "and external opportunity drove it."}]}]}
    errs = check_reader_language(dirty)
    r.append(check(
        "catches benchmark / role score / maxed out / external opportunity, each named with its story",
        all(any(p in e for e in errs)
            for p in ("'benchmark'", "'role score'", "'maxed out'", "'external opportunity'"))
        and all("big_one.stories[0].body" in e for e in errs),
    ))
    r.append(check(
        "notes_for_human_reviewer is NOT checked -- the agent is asked to explain scoring there",
        check_reader_language({"sections": [], "notes_for_human_reviewer": "eps_total 60, peer tier, benchmark"}) == [],
    ))
    r.append(check(
        "whole-word only: 'benchmarking' and 'frontiers' do not match",
        check_reader_language({"sections": [{"section_type": "big_one", "cross_references": [], "stories": [
            {"headline": "h", "body": "No benchmarking here, just frontiers."}]}]}) == [],
    ))

    # The real saved draft.
    if SAVED_RAW.exists():
        saved = json.loads(SAVED_RAW.read_text())["issue"]
        saved_errs = check_reader_language(saved)
        caught = {p for p in READER_LANGUAGE_BANNED_PHRASES if any(f"{p!r}" in e for e in saved_errs)}
        r.append(check(
            f"on the REAL saved Week 9 draft it catches {len(saved_errs)} violations across "
            f"{len(caught)} distinct phrases",
            len(saved_errs) >= 9
            and {"peer group", "comparison group", "comparison class", "benchmark",
                 "methodology", "snapshot", "usage numbers for", "role-change signal",
                 "maxed out"} <= caught,
        ))
        r.append(check(
            "and it reaches the model: those violations land in the contract's hard_errors, "
            "which is what the retry feedback quotes",
            any("banned reader-facing phrase" in e
                for e in finalize_weekly_brief(saved, build_candidate_pool(FIXTURES)["candidate_story_objects"])[1]["hard_errors"]),
        ))
    else:
        print(f"[SKIP] saved raw output not present at {SAVED_RAW}")

    print()
    if all(r):
        print(f"All {len(r)} checks passed.")
    else:
        print(f"{len(r) - sum(r)} of {len(r)} checks FAILED — see above.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
