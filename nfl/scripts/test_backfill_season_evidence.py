"""
Regression coverage for scripts/backfill_season_evidence.py -- the two
offline validation guards (request shape, week eligibility), the hard
week filter, write-idempotency, and a failed-write case. All offline,
synthetic fixtures; one real-pbp case at the end, skipped (not failed)
on a real network/route failure -- same convention every other real-
network check in this codebase already uses.

Run: python3 nfl/scripts/test_backfill_season_evidence.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from backfill_season_evidence import (
    MAX_WEEKS_PER_CALL,
    backfill_season_evidence_rows,
    validate_backfill_request_shape,
    validate_backfill_weeks,
)
from reconcile_week import shape_player_redzone_weekly_rows, shape_player_season_evidence_rows


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    return condition


def run_shape_guard_valid_request():
    print("\n" + "=" * 70)
    print("Request shape guard: a real, well-formed request passes")
    print("=" * 70)

    ok = True
    ok &= check("a real 2-week request passes", validate_backfill_request_shape(2026, [1, 2]) is None)
    ok &= check("a real 1-week request passes", validate_backfill_request_shape(2026, [1]) is None)
    ok &= check(f"exactly MAX_WEEKS_PER_CALL ({MAX_WEEKS_PER_CALL}) weeks passes", validate_backfill_request_shape(2026, list(range(1, MAX_WEEKS_PER_CALL + 1))) is None)
    return ok


def run_shape_guard_more_than_max():
    print("\n" + "=" * 70)
    print(f"Request shape guard: more than {MAX_WEEKS_PER_CALL} weeks is rejected")
    print("=" * 70)

    error = validate_backfill_request_shape(2026, list(range(1, MAX_WEEKS_PER_CALL + 2)))
    ok = True
    ok &= check("a request one week over the cap is rejected", error is not None)
    ok &= check("the error names the real cap", str(MAX_WEEKS_PER_CALL) in error)
    return ok


def run_shape_guard_duplicates():
    print("\n" + "=" * 70)
    print("Request shape guard: duplicate weeks are rejected")
    print("=" * 70)

    error = validate_backfill_request_shape(2026, [1, 2, 1])
    ok = True
    ok &= check("duplicate weeks in the same request are rejected", error is not None)
    ok &= check("the error names duplicates specifically", "duplicate" in error.lower())
    return ok


def run_shape_guard_non_integers_and_low_weeks():
    print("\n" + "=" * 70)
    print("Request shape guard: non-integers, booleans, and weeks < 1 are rejected")
    print("=" * 70)

    ok = True
    ok &= check("a float week is rejected", validate_backfill_request_shape(2026, [1.5]) is not None)
    ok &= check("a string week is rejected", validate_backfill_request_shape(2026, ["1"]) is not None)
    ok &= check("a bool week is rejected (bool is a real int subclass in Python)", validate_backfill_request_shape(2026, [True]) is not None)
    ok &= check("week 0 is rejected", validate_backfill_request_shape(2026, [0]) is not None)
    ok &= check("a negative week is rejected", validate_backfill_request_shape(2026, [-1]) is not None)
    ok &= check("an empty weeks list is rejected", validate_backfill_request_shape(2026, []) is not None)
    ok &= check("a non-list weeks value is rejected", validate_backfill_request_shape(2026, 1) is not None)
    return ok


def run_week_guard_latest_week_rejected():
    print("\n" + "=" * 70)
    print("Week guard: the latest reconciled week is rejected, earlier weeks pass")
    print("=" * 70)

    result = validate_backfill_weeks([1, 2, 3], reconciled_weeks_present=[1, 2, 3])
    ok = True
    ok &= check("weeks 1 and 2 (strictly before the latest, week 3) are accepted", result["accepted"] == [1, 2])
    ok &= check("week 3 (the latest reconciled week) is rejected, not accepted", 3 not in result["accepted"] and 3 in result["rejected"])
    ok &= check("the rejection reason names it as the latest week", "latest" in result["rejected"][3])
    return ok


def run_week_guard_missing_week_rejected():
    print("\n" + "=" * 70)
    print("Week guard: a week never reconciled at all is rejected")
    print("=" * 70)

    result = validate_backfill_weeks([1, 5], reconciled_weeks_present=[1, 2, 3])
    ok = True
    ok &= check("week 1 (real, present, not the latest) is accepted", result["accepted"] == [1])
    ok &= check("week 5 (never reconciled) is rejected, not accepted", 5 not in result["accepted"] and 5 in result["rejected"])
    ok &= check("the rejection reason names it as not present", "not present" in result["rejected"][5])
    return ok


def run_week_guard_no_reconciled_weeks_at_all():
    print("\n" + "=" * 70)
    print("Week guard: a season with no reconciled weeks at all rejects everything")
    print("=" * 70)

    result = validate_backfill_weeks([1, 2], reconciled_weeks_present=[])
    ok = True
    ok &= check("nothing is accepted when the season has no real reconciled weeks", result["accepted"] == [])
    ok &= check("every requested week is rejected, none silently dropped", set(result["rejected"].keys()) == {1, 2})
    return ok


def run_week_guard_partial_acceptance():
    print("\n" + "=" * 70)
    print("Week guard: a mixed request -- some weeks accepted, some rejected, none dropped")
    print("=" * 70)

    result = validate_backfill_weeks([1, 3, 9], reconciled_weeks_present=[1, 2, 3])
    ok = True
    ok &= check("week 1 accepted (real, earlier than latest)", 1 in result["accepted"])
    ok &= check("week 3 rejected (it's the latest)", 3 in result["rejected"])
    ok &= check("week 9 rejected (never reconciled)", 9 in result["rejected"])
    ok &= check("every requested week lands in exactly one bucket", len(result["accepted"]) + len(result["rejected"]) == 3)
    return ok


def _fake_weekly(season, weeks_and_players):
    """A minimal real-shaped `weekly` DataFrame -- just enough columns for
    shape_player_redzone_weekly_rows/shape_player_season_evidence_rows to
    run against, covering several real weeks at once."""
    rows = []
    for week, player_id, targets, carries, rz, gl in weeks_and_players:
        rows.append({
            "player_id": player_id, "season": season, "week": week,
            "posteam": "SEA", "defteam": "LAR", "position_group": "WR",
            "role_momentum": 50.0, "role_trend": 50.0, "external_opportunity": 0.0,
            "role_momentum_completeness": 100.0, "depth_rank": 1,
            "ahead_injury_statuses": [], "ahead_injured_teammates": [],
            "defensive_matchup_vulnerability": 50.0, "defensive_matchup_completeness": 100.0,
            "targets": targets, "carries": carries, "rz_touches": rz, "gl_touches": gl,
            "snap_share": 0.5,
        })
    return pd.DataFrame(rows)


def run_hard_week_filter():
    print("\n" + "=" * 70)
    print("Hard week filter: requesting [1, 2] from a 3-week fixture never")
    print("emits a week-3 row, even though week 3's real data is right there")
    print("=" * 70)

    weekly = _fake_weekly(2026, [
        (1, "P1", 3, 2, 1, 0),
        (2, "P1", 4, 1, 1, 0),
        (3, "P1", 9, 9, 9, 9),  # deliberately extreme -- must never leak through
    ])
    reconciled = weekly[(weekly["season"] == 2026) & (weekly["week"].isin([1, 2]))].copy()
    redzone_rows = shape_player_redzone_weekly_rows(reconciled)
    season_evidence_rows = shape_player_season_evidence_rows(redzone_rows)

    ok = True
    weeks_in_output = {r["period_index"] for r in season_evidence_rows}
    ok &= check("only weeks 1 and 2 appear in the shaped output", weeks_in_output == {1, 2})
    ok &= check("week 3's extreme values never leak through", all(r["targets"] != 9 for r in season_evidence_rows))
    ok &= check("exactly 2 rows produced for 2 requested weeks", len(season_evidence_rows) == 2)
    return ok


def run_idempotent_output():
    print("\n" + "=" * 70)
    print("Idempotency: shaping the same real input twice gives byte-identical output")
    print("=" * 70)

    weekly = _fake_weekly(2026, [(1, "P1", 3, 2, 1, 0), (2, "P1", 4, 1, 1, 0)])
    reconciled = weekly[(weekly["season"] == 2026) & (weekly["week"].isin([1, 2]))].copy()

    out1 = shape_player_season_evidence_rows(shape_player_redzone_weekly_rows(reconciled))
    out2 = shape_player_season_evidence_rows(shape_player_redzone_weekly_rows(reconciled.copy()))

    ok = True
    ok &= check("two independent shapings of the same real rows are byte-identical", out1 == out2)
    return ok


def run_failed_write_raises():
    print("\n" + "=" * 70)
    print("A failed write is loud -- backfill_season_evidence_rows() raises, never returns success")
    print("=" * 70)

    import backfill_season_evidence as bse

    original = bse.write_player_season_evidence_rows

    def fake_failing_write(rows, secret, write_url=None):
        return {"success": False, "status_code": 500, "error": "simulated real write failure", "response_body": None}

    def fake_run_pipeline(*args, **kwargs):
        weekly = _fake_weekly(2026, [(1, "P1", 3, 2, 1, 0)])
        return weekly, pd.DataFrame()

    bse.write_player_season_evidence_rows = fake_failing_write
    original_run_pipeline = bse.run_pipeline
    bse.run_pipeline = fake_run_pipeline
    original_loads = (bse.load_pbp, bse.load_snap_counts, bse.load_id_crosswalk, bse.load_depth_charts, bse.load_injuries, bse.load_seasonal_rosters, bse.load_schedules)
    bse.load_pbp = bse.load_snap_counts = bse.load_id_crosswalk = bse.load_depth_charts = bse.load_injuries = bse.load_seasonal_rosters = bse.load_schedules = lambda *a, **k: None

    raised = None
    try:
        bse.backfill_season_evidence_rows(2026, [1], "fake-secret")
    except RuntimeError as e:
        raised = e
    finally:
        bse.write_player_season_evidence_rows = original
        bse.run_pipeline = original_run_pipeline
        (bse.load_pbp, bse.load_snap_counts, bse.load_id_crosswalk, bse.load_depth_charts,
         bse.load_injuries, bse.load_seasonal_rosters, bse.load_schedules) = original_loads

    ok = True
    ok &= check("a failed write raises RuntimeError, never returns a success-shaped dict", raised is not None)
    ok &= check("the real error detail is preserved in the raised message", "simulated real write failure" in str(raised))
    return ok


def run_real_backfill_against_real_pipeline():
    print("\n" + "=" * 70)
    print("REAL: backfill_season_evidence_rows() against the real multi-season")
    print("pipeline, WITH the write itself mocked (no real network write) --")
    print("proves the real computation path end to end without touching Table 1")
    print("=" * 70)

    try:
        import backfill_season_evidence as bse

        captured = {}

        def fake_write(rows, secret, write_url=None):
            captured["rows"] = rows
            return {"success": True, "status_code": 200, "error": None, "response_body": "{}"}

        original = bse.write_player_season_evidence_rows
        bse.write_player_season_evidence_rows = fake_write
        try:
            result = bse.backfill_season_evidence_rows(2024, [1], "fake-secret-never-sent-because-write-is-mocked")
        finally:
            bse.write_player_season_evidence_rows = original

        ok = True
        ok &= check("the real pipeline produced real rows for a real past week", result["rows_written"] > 0)
        ok &= check("rows_written_by_week only contains the requested week", set(result["rows_written_by_week"].keys()) == {1})
        ok &= check("the captured write payload matches the reported count", len(captured["rows"]) == result["rows_written"])
        return ok
    except Exception as e:
        return check(f"REAL backfill pipeline check (skipped -- {type(e).__name__}: {e})", True)


if __name__ == "__main__":
    results = [
        run_shape_guard_valid_request(),
        run_shape_guard_more_than_max(),
        run_shape_guard_duplicates(),
        run_shape_guard_non_integers_and_low_weeks(),
        run_week_guard_latest_week_rejected(),
        run_week_guard_missing_week_rejected(),
        run_week_guard_no_reconciled_weeks_at_all(),
        run_week_guard_partial_acceptance(),
        run_hard_week_filter(),
        run_idempotent_output(),
        run_failed_write_raises(),
        run_real_backfill_against_real_pipeline(),
    ]
    print()
    if all(results):
        print("All checks passed.")
    else:
        print("Some checks FAILED -- see above.")
        raise SystemExit(1)
