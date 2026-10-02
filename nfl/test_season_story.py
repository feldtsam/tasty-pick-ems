"""
Regression coverage for season_story.py -- eligibility, the deterministic
material-change rule, and the per-run cap. Fully offline, synthetic
fixtures only; no network, no writes, no production calls.

Run: python3 nfl/test_season_story.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from player_season_history import build_player_history_package
from season_story import (
    SEASON_STORY_MATERIAL_CHANGE_CONFIG,
    eligible_players_for_season_story,
    is_material_change,
    rank_and_cap_material_changes,
)


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    return condition


def _row(player_id, week, season=2026, snap_share=0.5, targets=2, carries=2, red_zone_opportunities=1, goal_line_opportunities=0):
    return {
        "player_id": player_id, "season": season, "week": week,
        "snap_share": snap_share, "targets": targets, "carries": carries,
        "red_zone_opportunities": red_zone_opportunities, "goal_line_opportunities": goal_line_opportunities,
    }


def run_eligibility_from_picks():
    print("\n" + "=" * 70)
    print("Eligibility: a player on an approved Picks shelf is eligible")
    print("=" * 70)

    drafts = [
        {"player_id": "P1", "review_status": "approved"},
        {"player_id": "P2", "review_status": "pending_review"},  # not yet approved -- NOT eligible from this source
        {"player_id": "P3", "review_status": "rejected"},
    ]
    eligible = eligible_players_for_season_story(drafts, [])
    ok = True
    ok &= check("approved player is eligible", "P1" in eligible)
    ok &= check("pending_review player is NOT eligible (not yet published)", "P2" not in eligible)
    ok &= check("rejected player is NOT eligible", "P3" not in eligible)
    return ok


def run_eligibility_from_intelligence():
    print("\n" + "=" * 70)
    print("Eligibility: a player in a visible Intelligence story is eligible")
    print("=" * 70)

    stories = [
        {"entity": {"type": "player", "player_id": "P4"}, "is_visible": True, "sanity_check_passed": True},
        {"entity": {"type": "player", "player_id": "P5"}, "is_visible": False, "sanity_check_passed": True},  # not visible
        {"entity": {"type": "player", "player_id": "P6"}, "is_visible": True, "sanity_check_passed": False},  # failed sanity
        {"entity": {"type": "defense", "team": "SEA", "position_group": "WR"}, "is_visible": True, "sanity_check_passed": True},  # not a player
        {"entity": {"type": "team", "team": "SEA"}, "is_visible": True, "sanity_check_passed": True},  # not a player
    ]
    eligible = eligible_players_for_season_story([], stories)
    ok = True
    ok &= check("visible, sanity-passed player story makes the player eligible", "P4" in eligible)
    ok &= check("not-visible story does NOT make the player eligible", "P5" not in eligible)
    ok &= check("sanity-failed story does NOT make the player eligible", "P6" not in eligible)
    ok &= check("a defense entity never contributes a player_id", "SEA" not in eligible)
    ok &= check("exactly one real eligible player from this source", eligible == {"P4"})
    return ok


def run_eligibility_union_and_dedup():
    print("\n" + "=" * 70)
    print("Eligibility: picks and intelligence sources union and de-duplicate")
    print("=" * 70)

    drafts = [{"player_id": "P1", "review_status": "approved"}]
    stories = [{"entity": {"type": "player", "player_id": "P1"}, "is_visible": True, "sanity_check_passed": True},
               {"entity": {"type": "player", "player_id": "P7"}, "is_visible": True, "sanity_check_passed": True}]
    eligible = eligible_players_for_season_story(drafts, stories)
    ok = True
    ok &= check("a player eligible from BOTH sources appears exactly once", eligible == {"P1", "P7"})
    return ok


def run_no_previous_story():
    print("\n" + "=" * 70)
    print("Material change: no previous story -- always fires (first appearance)")
    print("=" * 70)

    rows = [_row("P1", w) for w in (1, 2, 3)]
    pkg = build_player_history_package("P1", 2026, 3, rows, reconciled_weeks=[1, 2, 3])
    result = is_material_change(pkg, None, SEASON_STORY_MATERIAL_CHANGE_CONFIG)
    ok = True
    ok &= check("fires is True with no previous story", result["fires"] is True)
    ok &= check("magnitude is infinite -- always ranks first under a cap", result["magnitude"] == float("inf"))
    ok &= check("reason names it a first appearance", "first appearance" in result["reason"])
    return ok


def run_one_new_week_under_threshold():
    print("\n" + "=" * 70)
    print("Material change: one real new week exists, but nothing crosses threshold")
    print("=" * 70)

    rows_v1 = [_row("P1", w, targets=4, carries=4) for w in (1, 2, 3)]
    prev = build_player_history_package("P1", 2026, 3, rows_v1, reconciled_weeks=[1, 2, 3])
    rows_v2 = rows_v1 + [_row("P1", 4, targets=4, carries=4)]
    new = build_player_history_package("P1", 2026, 4, rows_v2, reconciled_weeks=[1, 2, 3, 4])
    result = is_material_change(new, prev, SEASON_STORY_MATERIAL_CHANGE_CONFIG)
    ok = True
    ok &= check("a steady real week does not fire", result["fires"] is False)
    ok &= check("reason says no threshold crossed, not 'no new weeks' (a new week DID arrive)", result["reason"] == "no threshold crossed")
    return ok


def run_metric_over_threshold():
    print("\n" + "=" * 70)
    print("Material change: season_baseline for one metric moves past its threshold")
    print("=" * 70)

    rows_v1 = [_row("P1", w, targets=2) for w in (1, 2, 3)]
    prev = build_player_history_package("P1", 2026, 3, rows_v1, reconciled_weeks=[1, 2, 3])
    rows_v2 = rows_v1 + [_row("P1", 4, targets=10)]
    new = build_player_history_package("P1", 2026, 4, rows_v2, reconciled_weeks=[1, 2, 3, 4])
    result = is_material_change(new, prev, SEASON_STORY_MATERIAL_CHANGE_CONFIG)
    ok = True
    ok &= check("a real season_baseline shift past the configured threshold fires", result["fires"] is True)
    ok &= check("magnitude is a real, finite ratio > 1.0", 1.0 < result["magnitude"] < float("inf"))
    ok &= check("reason cites the real metric that moved", "targets" in result["reason"])
    return ok


def run_outlier_week():
    print("\n" + "=" * 70)
    print("Material change: season_baseline barely moves, but this week alone is a real outlier")
    print("=" * 70)

    rows_v1 = [_row("P1", w, carries=3) for w in (1, 2, 3)]
    prev = build_player_history_package("P1", 2026, 3, rows_v1, reconciled_weeks=[1, 2, 3])
    rows_v2 = rows_v1 + [_row("P1", 4, carries=20)]
    new = build_player_history_package("P1", 2026, 4, rows_v2, reconciled_weeks=[1, 2, 3, 4])
    result = is_material_change(new, prev, SEASON_STORY_MATERIAL_CHANGE_CONFIG)
    ok = True
    ok &= check("a single real outlier week fires on its own, independent of season_baseline movement", result["fires"] is True)
    ok &= check("reason names current_week, not season_baseline, as the trigger", "current_week" in result["reason"])
    return ok


def run_absent_week_never_fires():
    print("\n" + "=" * 70)
    print("Material change: a player with NO new real row cannot fire -- absence is not data")
    print("=" * 70)

    rows_v1 = [_row("P1", w, targets=9) for w in (1, 2, 3)]  # a real, high season_baseline
    prev = build_player_history_package("P1", 2026, 3, rows_v1, reconciled_weeks=[1, 2, 3])
    # No new row at all for week 4 -- a real bye or no real red-zone touch.
    # rows passed to the "new" package are UNCHANGED from v1 -- as_of_week
    # moves to 4, but there's genuinely nothing new for this player.
    new = build_player_history_package("P1", 2026, 4, rows_v1, reconciled_weeks=[1, 2, 3, 4])
    result = is_material_change(new, prev, SEASON_STORY_MATERIAL_CHANGE_CONFIG)
    ok = True
    ok &= check("an absent week never fires the rule, regardless of how extreme the OLD season_baseline was", result["fires"] is False)
    ok &= check("reason correctly attributes this to no new real weeks, not 'no threshold crossed'", result["reason"] == "no new real weeks since the last version")
    ok &= check("magnitude is 0.0 -- never computed from stale data", result["magnitude"] == 0.0)
    return ok


def run_cap():
    print("\n" + "=" * 70)
    print("Per-run cap: more real fires than the cap allows -- ranked by magnitude, not silently dropped")
    print("=" * 70)

    evaluations = {
        "BIGGEST": {"fires": True, "reason": "big move", "magnitude": 5.0},
        "FIRST_APPEARANCE": {"fires": True, "reason": "first appearance", "magnitude": float("inf")},
        "MIDDLE": {"fires": True, "reason": "medium move", "magnitude": 2.0},
        "SMALLEST_FIRED": {"fires": True, "reason": "small move", "magnitude": 1.1},
        "DID_NOT_FIRE": {"fires": False, "reason": "no threshold crossed", "magnitude": 0.3},
    }
    config = {**SEASON_STORY_MATERIAL_CHANGE_CONFIG, "max_new_versions_per_run": 2}
    result = rank_and_cap_material_changes(evaluations, config)

    ok = True
    ok &= check("first appearance (magnitude=inf) is never capped -- always ranks first", result["FIRST_APPEARANCE"]["capped"] is False)
    ok &= check("the next-largest real fire also makes the cap of 2", result["BIGGEST"]["capped"] is False)
    ok &= check("exactly 2 candidates made the cap, matching max_new_versions_per_run", sum(1 for r in result.values() if r["fires"] and not r["capped"]) == 2)
    ok &= check("the weaker real fires are capped, not discarded -- still fires=True", result["MIDDLE"]["fires"] is True and result["MIDDLE"]["capped"] is True)
    ok &= check("the smallest real fire is also capped", result["SMALLEST_FIRED"]["capped"] is True)
    ok &= check("a candidate that never fired is never marked capped (capped means 'deferred', not 'rejected')", result["DID_NOT_FIRE"]["capped"] is False)
    ok &= check("every original key is still present, none dropped by the cap", set(result.keys()) == set(evaluations.keys()))
    return ok


def run_cap_does_not_bind_when_under_limit():
    print("\n" + "=" * 70)
    print("Per-run cap: fewer real fires than the cap -- nobody capped")
    print("=" * 70)

    evaluations = {
        "A": {"fires": True, "reason": "x", "magnitude": 2.0},
        "B": {"fires": False, "reason": "no threshold crossed", "magnitude": 0.1},
    }
    result = rank_and_cap_material_changes(evaluations, SEASON_STORY_MATERIAL_CHANGE_CONFIG)
    ok = True
    ok &= check("the real provisional default (40) does not bind on 1 real fire", result["A"]["capped"] is False)
    return ok


if __name__ == "__main__":
    results = [
        run_eligibility_from_picks(),
        run_eligibility_from_intelligence(),
        run_eligibility_union_and_dedup(),
        run_no_previous_story(),
        run_one_new_week_under_threshold(),
        run_metric_over_threshold(),
        run_outlier_week(),
        run_absent_week_never_fires(),
        run_cap(),
        run_cap_does_not_bind_when_under_limit(),
    ]
    print()
    if all(results):
        print("All checks passed.")
    else:
        print("Some checks FAILED -- see above.")
        raise SystemExit(1)
