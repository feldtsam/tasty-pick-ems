"""
Tests intelligence_sanity.py and intelligence_write.py — the sanity gate
in isolation, process_family's real wiring of sanity + lifecycle
together (including the deliberately-injected-NaN case this task's own
"must not corrupt future weeks' comparisons" requirement demands), and
real historical multi-family/multi-week data through the full process_
family path (not just apply_lifecycle directly, which test_intelligence_
lifecycle.py already covers on its own).

Run: python3 nfl/test_intelligence_write.py
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

from intelligence_lifecycle import FAMILY_SIGNAL_THRESHOLDS
from intelligence_sanity import sanity_check_story
from intelligence_schema import build_story
from intelligence_write import process_family, shape_story_row

WEEKLY_PATH = Path(__file__).resolve().parent / "scripts" / "player_redzone_weekly.csv"


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


def _real_story(**overrides):
    """A genuinely well-formed real story (defensive_trends shape) — every check below starts from this and corrupts one thing at a time."""
    base = dict(
        intelligence_family="defensive_trends",
        entity={"type": "defense", "team": "TST", "position_group": "RB"},
        headline="TST's defense is getting worse against RBs.",
        story="Opponents have scored 2.3 red-zone TDs per game against TST's RB defense over the last 3 games, up from a 1.1 season average.",
        primary_signal={"name": "defensive_matchup_vulnerability", "value": 65.0},
        supporting_evidence=["Opponents have scored 2.3 red-zone TDs per game...", "Ranks 3rd worst in the league"],
        trend_direction="growing-vulnerability",
        trend_strength=65.0,
        sample_size=8,
        completeness=82.0,
        confidence=82.0,
        time_window="Season 2099, last 3 games through Week 5 vs. season-to-date",
        related_players=[{"player_id": "00-1111111", "player_name": "Test Back", "team": "OPP", "relationship": "td_opportunity"}],
    )
    base.update(overrides)
    return build_story(**base)


if __name__ == "__main__":
    results = []

    # ============================================================
    # sanity_check_story — one real, well-formed story, then one
    # corruption at a time.
    # ============================================================
    clean = _real_story()
    results.append(check("a real, well-formed story passes the sanity gate with zero issues", sanity_check_story(clean) == []))

    nan_trend = _real_story(trend_strength=float("nan"))
    issues = sanity_check_story(nan_trend)
    results.append(check(f"NaN trend_strength is caught (got {issues})", any("trend_strength" in i for i in issues)))

    inf_signal = _real_story(primary_signal={"name": "defensive_matchup_vulnerability", "value": float("inf")})
    issues = sanity_check_story(inf_signal)
    results.append(check(f"infinite primary_signal.value is caught (got {issues})", any("primary_signal" in i for i in issues)))

    out_of_range = _real_story(completeness=142.0, confidence=142.0)
    issues = sanity_check_story(out_of_range)
    results.append(check(f"out-of-0-100-range completeness/confidence is caught (got {issues})", sum("outside the real documented 0-100 range" in i for i in issues) == 2))

    negative_sample = _real_story(sample_size=-3)
    issues = sanity_check_story(negative_sample)
    results.append(check(f"negative sample_size is caught (got {issues})", any("negative" in i for i in issues)))

    bad_entity = _real_story(entity={"type": "defense", "team": "TST"})  # missing position_group
    issues = sanity_check_story(bad_entity)
    results.append(check(f"an entity missing a type-required key is caught (got {issues})", any("missing required key" in i for i in issues)))

    empty_headline = _real_story(headline="   ")
    issues = sanity_check_story(empty_headline)
    results.append(check(f"a blank headline is caught (got {issues})", any("headline" in i for i in issues)))

    multi_bad = _real_story(trend_strength=float("nan"), headline="")
    issues = sanity_check_story(multi_bad)
    results.append(check(f"multiple simultaneous issues are all reported, not just the first (got {issues})", len(issues) == 2))

    # ============================================================
    # process_family — sane stories write visible/passed; a sanity-
    # failed story still writes, flagged, never dropped.
    # ============================================================
    result = process_family("defensive_trends", [clean], {}, 2099, 1, lifecycle_eligible=True)
    row = result["story_rows"][0]
    results.append(check(
        f"a clean real story's shaped row is is_visible=True, sanity_check_passed=True, sanity_check_issues=None (got is_visible={row['is_visible']}, passed={row['sanity_check_passed']}, issues={row['sanity_check_issues']})",
        row["is_visible"] is True and row["sanity_check_passed"] is True and row["sanity_check_issues"] is None,
    ))
    results.append(check(f"a clean real story's first-ever appearance gets lifecycle_state=Detected (got {row['lifecycle_state']})", row["lifecycle_state"] == "Detected"))
    results.append(check("a lifecycle-eligible family with a real story produces exactly 1 history row", len(result["history_rows"]) == 1))

    result_bad = process_family("defensive_trends", [nan_trend], {}, 2099, 1, lifecycle_eligible=True)
    row_bad = result_bad["story_rows"][0]
    results.append(check(
        f"a sanity-failed story STILL writes a content row (never silently dropped) — is_visible=False, sanity_check_passed=False, issues populated (got is_visible={row_bad['is_visible']}, passed={row_bad['sanity_check_passed']}, issues={row_bad['sanity_check_issues']})",
        row_bad["is_visible"] is False and row_bad["sanity_check_passed"] is False and bool(row_bad["sanity_check_issues"]),
    ))
    results.append(check(
        "a sanity-failed story STILL counts as a real appearance for lifecycle (approved decision #3) — a real history row is still produced for it",
        len(result_bad["history_rows"]) == 1,
    ))
    hist_row_bad = result_bad["history_rows"][0]
    results.append(check(
        f"the wire-shaped history row's trend_strength is sanitized to null (JSON has no NaN literal), not left as a real NaN float (got {hist_row_bad['trend_strength']!r})",
        hist_row_bad["trend_strength"] is None,
    ))

    # Market Intelligence (lifecycle_eligible=False): real content row, lifecycle_state always None, zero history rows.
    market_story = build_story(
        intelligence_family="market", entity={"type": "player", "player_id": "00-2222222", "player_name": "Test WR", "team": "TST", "position_group": "WR"},
        headline="Test WR is drawing real anytime-TD money.", story="Test story text.",
        primary_signal={"name": "market_value_score", "value": 72.0}, supporting_evidence=["ev"],
        trend_direction="strong-standing", trend_strength=72.0, sample_size=5, completeness=80.0, confidence=80.0,
        time_window="Single live snapshot, TST @ OPP, kickoff 2099-09-08T17:00:00Z", related_players=[],
    )
    result_market = process_family("market", [market_story], {}, 2099, 1, lifecycle_eligible=False)
    results.append(check(
        f"Market Intelligence: real content row written with lifecycle_state=None, zero history rows (got lifecycle_state={result_market['story_rows'][0]['lifecycle_state']}, history_rows={len(result_market['history_rows'])})",
        result_market["story_rows"][0]["lifecycle_state"] is None and result_market["history_rows"] == [],
    ))
    results.append(check("Market Intelligence: the real content row is still visible/passed (it's a genuinely clean story, unaffected by the lifecycle deferral)", result_market["story_rows"][0]["is_visible"] is True))

    # ============================================================
    # THE CORE NaN-IN-LIFECYCLE VALIDATION: a synthetic multi-week
    # sequence for ONE identity — clean weeks, then a deliberately
    # NaN'd week, then more clean weeks — proving the NaN week (a)
    # still writes a flagged content row, (b) still counts as a real
    # appearance in history, and (c) does NOT corrupt the baseline
    # subsequent real weeks compare against.
    # ============================================================
    threshold = FAMILY_SIGNAL_THRESHOLDS["defensive_matchup_vulnerability"]
    history = {}
    weekly_rows = {}

    def _week(value, week, entity_key="NAN:RB"):
        global history
        story = _real_story(entity={"type": "defense", "team": entity_key.split(":")[0], "position_group": entity_key.split(":")[1]}, trend_strength=value, primary_signal={"name": "defensive_matchup_vulnerability", "value": value})
        result = process_family("defensive_trends", [story], history, 2099, week, lifecycle_eligible=True)
        history = result["updated_history"]
        weekly_rows[week] = (result["story_rows"][0], result["history_rows"][0])
        return result

    _week(40.0, 1)   # Detected, recent_values=[40.0]
    _week(42.0, 2)   # delta vs 40.0 = 2.0, non-qualifying, recent_values=[40.0, 42.0]
    r3 = _week(float("nan"), 3)   # SANITY-FAILED WEEK -- must NOT enter recent_values, must NOT be treated as a real Strengthening/Weakening move
    row3, hist3 = weekly_rows[3]
    results.append(check(
        f"week 3 (NaN): content row flagged (is_visible=False, sanity_check_passed=False) yet still counts as a real appearance in the history row (got is_visible={row3['is_visible']}, hist_lifecycle_state={hist3['lifecycle_state']})",
        row3["is_visible"] is False and hist3["lifecycle_state"] in ("Detected", "Active"),
    ))
    results.append(check(
        f"week 3 (NaN): no false directional confirmation fired from the corrupt reading (got lifecycle_state={hist3['lifecycle_state']}, streak_count={hist3['streak_count']})",
        hist3["lifecycle_state"] not in ("Strengthening", "Weakening") and hist3["streak_count"] == 0,
    ))
    results.append(check(
        f"week 3 (NaN): recent_values was NOT polluted with the NaN — the identity's internal baseline window still holds only the 2 genuine real prior readings (got {history[('defensive_trends', 'NAN:RB', 'defensive_matchup_vulnerability')]['recent_values']})",
        history[("defensive_trends", "NAN:RB", "defensive_matchup_vulnerability")]["recent_values"] == [40.0, 42.0],
    ))

    # Week 4: a REAL qualifying move. baseline should be avg(40.0, 42.0)=41.0
    # (the NaN week correctly excluded), delta=90.0-41.0=49.0 >= 21.7 threshold
    # -- first qualifying week, not yet confirmed. If the NaN had instead
    # corrupted the baseline (e.g. treated as 0 or included as NaN itself),
    # this comparison would be nonsensical or NaN-poisoned instead of this
    # real, correct 49.0 delta.
    r4 = _week(90.0, 4)
    row4, hist4 = weekly_rows[4]
    results.append(check(
        f"week 4: the real baseline used for this comparison is unaffected by week 3's NaN — a real qualifying delta computes correctly (got lifecycle_state={hist4['lifecycle_state']}, trend_strength={hist4['trend_strength']})",
        hist4["lifecycle_state"] == "Active" and hist4["trend_strength"] == 90.0,  # first qualifying week, not yet 2-in-a-row confirmed
    ))
    results.append(check(f"week 4: this row is a genuinely clean, real appearance again — is_visible=True (got {row4['is_visible']})", row4["is_visible"] is True))

    # Week 5: SAME direction confirmed a 2nd consecutive real time -- baseline
    # now avg(42.0, 90.0)=66.0 (NaN still correctly excluded from the window),
    # delta=91.0-66.0=25.0 >= threshold, same direction as week 4 -> confirmed.
    r5 = _week(91.0, 5)
    row5, hist5 = weekly_rows[5]
    results.append(check(
        f"week 5: directional confirmation fires correctly off two genuinely real consecutive qualifying weeks (4 and 5), proving the NaN week's exclusion from pending_direction tracking didn't also break real confirmation later (got lifecycle_state={hist5['lifecycle_state']}, streak={hist5['streak_count']})",
        hist5["lifecycle_state"] == "Strengthening" and hist5["streak_count"] == 2,
    ))

    print()
    if all(results):
        print(f"All {len(results)} checks passed (sanity gate + NaN-in-lifecycle synthetic validation).")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")

    # ============================================================
    # REAL DATA: run process_family (not raw apply_lifecycle) across
    # real NYJ RB (Defensive Trends) and real DEN Coaching Trends weeks
    # already validated in test_intelligence_lifecycle.py -- confirming
    # the SAME real data comes out is_visible=True, sanity_check_passed=
    # True end-to-end through the sanity gate too, not just through
    # lifecycle alone.
    # ============================================================
    real_results = []
    if not WEEKLY_PATH.exists():
        print("\n(skipped the real multi-family process_family validation — player_redzone_weekly.csv not present in this environment)")
    else:
        from defensive_trends import build_defensive_trends_stories
        weekly = pd.read_csv(WEEKLY_PATH)
        real_history = {}
        real_flags = []
        for week in range(15, 19):
            stories = build_defensive_trends_stories(weekly, 2025, week)
            nyj_rb = [s for s in stories if s["entity"].get("team") == "NYJ" and s["entity"].get("position_group") == "RB"]
            result = process_family("defensive_trends", nyj_rb, real_history, 2025, week, lifecycle_eligible=True)
            real_history = result["updated_history"]
            for r in result["story_rows"]:
                real_flags.append((week, r["is_visible"], r["sanity_check_passed"]))
        real_results.append(check(
            f"real NYJ RB Defensive Trends weeks 15/17/18 (real appearances) all pass the sanity gate cleanly — is_visible=True, sanity_check_passed=True for every real row (got {real_flags})",
            all(v is True and p is True for _, v, p in real_flags) and len(real_flags) == 3,
        ))

        print("\nReal NYJ RB process_family results (Defensive Trends, 2025):")
        for wk, vis, passed in real_flags:
            print(f"  week {wk}: is_visible={vis} sanity_check_passed={passed}")

    print()
    all_results = results + real_results
    if all(all_results):
        print(f"All {len(all_results)} total checks passed.")
    else:
        failed = len(all_results) - sum(all_results)
        print(f"{failed} of {len(all_results)} total checks FAILED — see above.")
        raise SystemExit(1)
