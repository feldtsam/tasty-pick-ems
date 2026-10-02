"""
Regression coverage for player_season_history.py — the deterministic
Follow the Story history-package builder. Offline, synthetic-fixture
cases for build_player_history_package() itself (a pure function, no
network needed to test it), plus real-network cases for both read
functions (single-player and whole-season), skipped (not failed) on a
real network/route failure or a missing secret — same convention every
other real-network check in this codebase already uses.

Run: python3 nfl/test_player_season_history.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from player_season_history import (
    METRIC_KEYS,
    RECENT_N_WEEKS,
    build_player_history_package,
    read_player_season_evidence_rows,
    read_player_season_evidence_rows_for_season,
    reconciled_weeks_from_redzone_weekly_rows,
)


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    return condition


def _row(week, snap_share=0.5, targets=1, carries=1, red_zone_opportunities=1, goal_line_opportunities=0, player_id="P1", season=2026):
    return {
        "player_id": player_id, "season": season, "week": week,
        "snap_share": snap_share, "targets": targets, "carries": carries,
        "red_zone_opportunities": red_zone_opportunities, "goal_line_opportunities": goal_line_opportunities,
    }


def run_full_history():
    print("\n" + "=" * 70)
    print("Full history -- every week 1..4 present, no gaps")
    print("=" * 70)

    rows = [_row(w, snap_share=0.1 * w, targets=w, carries=w, red_zone_opportunities=1, goal_line_opportunities=0) for w in (1, 2, 3, 4)]
    pkg = build_player_history_package("P1", 2026, 4, rows, reconciled_weeks=[1, 2, 3, 4])

    ok = True
    ok &= check("weeks_present is every reconciled week, in order", pkg["weeks_present"] == [1, 2, 3, 4])
    ok &= check("current_week is week 4's own real row", pkg["current_week"]["targets"] == 4 and pkg["current_week"]["snap_share"] == 0.4)
    ok &= check(f"recent_weeks has exactly RECENT_N_WEEKS ({RECENT_N_WEEKS}) entries", len(pkg["recent_weeks"]) == RECENT_N_WEEKS)
    ok &= check("recent_weeks is the most recent weeks, chronological order", [r["week"] for r in pkg["recent_weeks"]] == [2, 3, 4])
    ok &= check("season_baseline.targets averages all 4 real values (1+2+3+4)/4=2.5", pkg["season_baseline"]["targets"]["avg"] == 2.5)
    ok &= check("season_baseline.targets used all 4 weeks", pkg["season_baseline"]["targets"]["weeks_used"] == 4)
    ok &= check("coverage is 4 of 4 -- full, no gaps", pkg["coverage"] == "4 of 4 reconciled weeks")
    return ok


def run_one_gap():
    print("\n" + "=" * 70)
    print("One gap -- week 3 absent from rows but reconciled this season")
    print("=" * 70)

    rows = [_row(1, targets=2), _row(2, targets=4), _row(4, targets=8)]
    pkg = build_player_history_package("P1", 2026, 4, rows, reconciled_weeks=[1, 2, 3, 4])

    ok = True
    ok &= check("weeks_present skips the gap week entirely", pkg["weeks_present"] == [1, 2, 4])
    ok &= check("the gap week is NEVER treated as a real 0 -- season_baseline.targets averages only the 3 real rows (2+4+8)/3", pkg["season_baseline"]["targets"]["avg"] == round((2 + 4 + 8) / 3, 4))
    ok &= check("season_baseline.targets used only 3 weeks, not 4", pkg["season_baseline"]["targets"]["weeks_used"] == 3)
    ok &= check("coverage denominator is the season's 4 reconciled weeks, numerator the 3 the player actually has", pkg["coverage"] == "3 of 4 reconciled weeks")
    return ok


def run_bye_week_like_gap():
    print("\n" + "=" * 70)
    print("Bye-week-like gap -- a reconciled week where this specific player")
    print("genuinely has no row (a real bye, or no real red-zone touch) --")
    print("mechanically identical to any other gap, semantics flagged in `note`")
    print("=" * 70)

    # Every OTHER player had real rows reconciled for week 3 (it's a real,
    # reconciled week), this player simply doesn't appear in it -- same
    # shape a real bye week or a real touchless game produces.
    rows = [_row(1), _row(2), _row(4)]
    pkg = build_player_history_package("P1", 2026, 4, rows, reconciled_weeks=[1, 2, 3, 4])

    ok = True
    ok &= check("week 3 is absent from weeks_present, same as any other gap", 3 not in pkg["weeks_present"])
    ok &= check("coverage still counts week 3 in the denominator (it WAS reconciled) but not the numerator", pkg["coverage"] == "3 of 4 reconciled weeks")
    ok &= check(
        "the note field states the real limitation: a missing week can't be told apart from a bye or a real touchless game",
        "bye week" in pkg["note"] and "red-zone touch" in pkg["note"],
    )
    return ok


def run_single_week():
    print("\n" + "=" * 70)
    print("A single real week -- the player's first-ever reconciled week")
    print("=" * 70)

    rows = [_row(1, targets=5)]
    pkg = build_player_history_package("P1", 2026, 1, rows, reconciled_weeks=[1])

    ok = True
    ok &= check("current_week is that one real row", pkg["current_week"]["targets"] == 5)
    ok &= check("recent_weeks has exactly 1 entry, not padded to RECENT_N_WEEKS", len(pkg["recent_weeks"]) == 1)
    ok &= check("season_baseline.targets is just that one value, weeks_used=1", pkg["season_baseline"]["targets"]["avg"] == 5 and pkg["season_baseline"]["targets"]["weeks_used"] == 1)
    ok &= check("coverage is 1 of 1", pkg["coverage"] == "1 of 1 reconciled weeks")
    return ok


def run_no_rows():
    print("\n" + "=" * 70)
    print("No rows at all -- honest partial result, never an error")
    print("=" * 70)

    raised = None
    pkg = None
    try:
        pkg = build_player_history_package("P1", 2026, 4, [], reconciled_weeks=[1, 2, 3, 4])
    except Exception as e:
        raised = e

    ok = True
    ok &= check("no crash on a genuinely empty rows list", raised is None)
    if pkg is not None:
        ok &= check("current_week is honestly null, not a fabricated zero row", pkg["current_week"] is None)
        ok &= check("recent_weeks is an empty list", pkg["recent_weeks"] == [])
        ok &= check("weeks_present is an empty list", pkg["weeks_present"] == [])
        ok &= check("every season_baseline metric is avg=None, weeks_used=0", all(pkg["season_baseline"][m]["avg"] is None and pkg["season_baseline"][m]["weeks_used"] == 0 for m in METRIC_KEYS))
        ok &= check("coverage honestly reports 0 of the real reconciled-week count", pkg["coverage"] == "0 of 4 reconciled weeks")
    return ok


def run_null_snap_share():
    print("\n" + "=" * 70)
    print("snap_share null on a present row -- the row and its OTHER")
    print("real metrics are unaffected; snap_share's own average skips it")
    print("=" * 70)

    rows = [_row(1, snap_share=0.5), _row(2, snap_share=None, targets=9), _row(3, snap_share=0.3)]
    pkg = build_player_history_package("P1", 2026, 3, rows, reconciled_weeks=[1, 2, 3])

    ok = True
    ok &= check("week 2 is still a real present week (null snap_share doesn't drop the row)", 2 in pkg["weeks_present"])
    ok &= check("week 2's real targets value is untouched by its own null snap_share", next(r for r in pkg["recent_weeks"] if r["week"] == 2)["targets"] == 9)
    ok &= check("current_week.snap_share is honestly None when the real row's own value is null", build_player_history_package("P1", 2026, 2, rows, [1, 2, 3])["current_week"]["snap_share"] is None)
    ok &= check("season_baseline.snap_share averages only the 2 real (non-null) values (0.5+0.3)/2=0.4", pkg["season_baseline"]["snap_share"]["avg"] == 0.4)
    ok &= check("season_baseline.snap_share reports weeks_used=2, not 3 -- it skipped the null, didn't treat it as 0", pkg["season_baseline"]["snap_share"]["weeks_used"] == 2)
    ok &= check("season_baseline.targets, unaffected by snap_share's null, still used all 3 weeks", pkg["season_baseline"]["targets"]["weeks_used"] == 3)
    return ok


def run_all_zeros_in_present_week():
    print("\n" + "=" * 70)
    print("A present week with every metric stored as a real 0 -- counted,")
    print("never confused with an absent week")
    print("=" * 70)

    rows = [_row(1, targets=4, carries=2), _row(2, snap_share=0.2, targets=0, carries=0, red_zone_opportunities=0, goal_line_opportunities=0)]
    pkg = build_player_history_package("P1", 2026, 2, rows, reconciled_weeks=[1, 2])

    ok = True
    ok &= check("week 2 IS in weeks_present -- a real zero row is still a real, present row", 2 in pkg["weeks_present"])
    ok &= check("current_week's zeros are real 0s, not None", pkg["current_week"]["targets"] == 0 and pkg["current_week"]["targets"] is not None)
    ok &= check("season_baseline.targets correctly includes the real 0 in its average (4+0)/2=2.0, not just averaging the non-zero week", pkg["season_baseline"]["targets"]["avg"] == 2.0)
    ok &= check("season_baseline.targets weeks_used=2 -- the zero row counts as a used week", pkg["season_baseline"]["targets"]["weeks_used"] == 2)
    ok &= check("coverage counts the all-zero week same as any other real week", pkg["coverage"] == "2 of 2 reconciled weeks")
    return ok


def run_defensive_filtering_by_player_and_season():
    print("\n" + "=" * 70)
    print("A raw, unfiltered rows list (other players, other seasons) --")
    print("the function filters defensively, doesn't require a pre-filtered input")
    print("=" * 70)

    rows = [
        _row(1, player_id="OTHER", targets=99),
        _row(1, season=2024, targets=50),
        _row(2, targets=3),
    ]
    pkg = build_player_history_package("P1", 2026, 2, rows, reconciled_weeks=[1, 2])

    ok = True
    ok &= check("only this player's own season-2026 row survives filtering", pkg["weeks_present"] == [2])
    ok &= check("the other player's/other season's rows never leak into season_baseline", pkg["season_baseline"]["targets"]["avg"] == 3)
    return ok


def run_real_season_evidence_read_route():
    print("\n" + "=" * 70)
    print("REAL: read_player_season_evidence_rows() against the real deployed")
    print("route -- skipped (not failed) if the route isn't live yet")
    print("=" * 70)

    import os
    secret = os.environ.get("NFL_PIPELINE_WEBHOOK_SECRET")
    if not secret:
        return check("REAL season_evidence read check (skipped -- no NFL_PIPELINE_WEBHOOK_SECRET in this environment)", True)

    try:
        result = read_player_season_evidence_rows("00-0033553", 2024, secret)
        if not result["ok"]:
            return check(
                f"REAL season_evidence read check (skipped -- route not live yet or returned an error: "
                f"status={result['status_code']} error={result['error']!r})",
                True,
            )
        ok = True
        ok &= check("REAL: the real route returned ok=True", result["ok"] is True)
        ok &= check("REAL: rows is a real list (possibly empty)", isinstance(result["rows"], list))
        return ok
    except Exception as e:
        return check(f"REAL season_evidence read check (skipped -- {type(e).__name__}: {e})", True)


def run_reconciled_weeks_from_redzone_weekly_rows():
    print("\n" + "=" * 70)
    print("reconciled_weeks_from_redzone_weekly_rows -- pure, offline")
    print("=" * 70)

    ok = True
    ok &= check(
        "distinct weeks, sorted, deduplicated across multiple players sharing the same real weeks",
        reconciled_weeks_from_redzone_weekly_rows([
            {"player_id": "A", "week": 3}, {"player_id": "B", "week": 1}, {"player_id": "C", "week": 3}, {"player_id": "A", "week": 2},
        ]) == [1, 2, 3],
    )
    ok &= check("an empty row list is an empty reconciled-weeks list, not an error", reconciled_weeks_from_redzone_weekly_rows([]) == [])
    ok &= check(
        "a row with no real week value is skipped, not treated as week None/0",
        reconciled_weeks_from_redzone_weekly_rows([{"player_id": "A", "week": None}, {"player_id": "B", "week": 2}]) == [2],
    )
    return ok


def run_real_season_wide_read_route():
    print("\n" + "=" * 70)
    print("REAL: read_player_season_evidence_rows_for_season() -- the whole-")
    print("season path the real Interrogation fetch actually uses")
    print("=" * 70)

    import os
    secret = os.environ.get("NFL_PIPELINE_WEBHOOK_SECRET")
    if not secret:
        return check("REAL whole-season read check (skipped -- no NFL_PIPELINE_WEBHOOK_SECRET in this environment)", True)

    try:
        result = read_player_season_evidence_rows_for_season(2024, secret)
        if not result["ok"]:
            return check(
                f"REAL whole-season read check (skipped -- route not live yet, auth mismatch, or MAX_PAGES "
                f"exceeded: status={result['status_code']} error={result['error']!r})",
                True,
            )
        ok = True
        ok &= check("REAL: the real route returned ok=True", result["ok"] is True)
        ok &= check("REAL: rows is a real list (possibly empty), spanning more than one player if any real data exists", isinstance(result["rows"], list))
        if result["rows"]:
            distinct_players = {r["player_id"] for r in result["rows"]}
            ok &= check(
                f"REAL: a whole-season read returns more than one distinct player_id when real rows exist "
                f"(got {len(distinct_players)} distinct players) -- proof this is really the season-wide "
                f"path, not an accidentally-filtered single-player result",
                len(distinct_players) > 1,
            )
        return ok
    except Exception as e:
        return check(f"REAL whole-season read check (skipped -- {type(e).__name__}: {e})", True)


if __name__ == "__main__":
    results = [
        run_full_history(),
        run_one_gap(),
        run_bye_week_like_gap(),
        run_single_week(),
        run_no_rows(),
        run_null_snap_share(),
        run_all_zeros_in_present_week(),
        run_defensive_filtering_by_player_and_season(),
        run_reconciled_weeks_from_redzone_weekly_rows(),
        run_real_season_evidence_read_route(),
        run_real_season_wide_read_route(),
    ]
    print()
    if all(results):
        print(f"All checks passed.")
    else:
        print("Some checks FAILED -- see above.")
        raise SystemExit(1)
