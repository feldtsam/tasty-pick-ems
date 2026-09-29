"""
Regression coverage for Follow the Story V1, Table 1's real shaping
logic: reconcile_week.shape_player_season_evidence_rows() and
write_player_season_evidence_rows(). No prior test file existed for
either.

Offline, synthetic-fixture only -- the DB-side upsert idempotency and
the "never prune history" guarantee are both structural properties of
the natural key + upsert-only write path (see the table's own
migration and nfl-player-season-evidence-write.ts), not something this
file can exercise against a real database (no service-role access from
this environment -- same standing gap as every other DB-backed check
this session). What IS tested here: the shaping logic is deterministic
(same input -> byte-identical output, the real precondition an upsert
needs to be safe on re-run), no code path in either function ever
touches a week/player outside the one batch it was given (the real
reason "never prune history" holds), and every metric is handled
independently so one missing value never drops a row.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))

import reconcile_week as rw


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    return condition


def _real_redzone_weekly_row(player_id="00-0034827", season=2026, week=3, **extra_overrides):
    """One shaped row exactly as shape_player_redzone_weekly_rows() would
    produce it: typed core + extra, real metric key names."""
    extra = {
        "snap_share": 0.62, "targets": 5, "carries": 2,
        "rz_touches": 3, "gl_touches": 1,
        "role_momentum": 71.0,  # a real non-metric extra key, ignored by this function
    }
    extra.update(extra_overrides)
    return {
        "player_id": player_id, "season": season, "week": week,
        "posteam": "BUF", "defteam": "SEA", "position_group": "WR",
        "role_momentum": 71.0, "extra": extra,
    }


def run_real_row_shapes_correctly():
    print("\n" + "=" * 70)
    print("A real redzone-weekly row shapes into the correct season_evidence row")
    print("=" * 70)

    out = rw.shape_player_season_evidence_rows([_real_redzone_weekly_row()])

    ok = True
    ok &= check("exactly one row out for one row in", len(out) == 1)
    row = out[0]
    ok &= check("player_id carried through", row["player_id"] == "00-0034827")
    ok &= check("season carried through", row["season"] == 2026)
    ok &= check("period_type is 'game' for V1", row["period_type"] == "game")
    ok &= check("period_index is the real week number, not a separate 'week' column", row["period_index"] == 3)
    ok &= check("snap_share read from extra", row["snap_share"] == 0.62)
    ok &= check("targets read from extra", row["targets"] == 5)
    ok &= check("carries read from extra", row["carries"] == 2)
    ok &= check("red_zone_opportunities is extra's rz_touches, renamed", row["red_zone_opportunities"] == 3)
    ok &= check("goal_line_opportunities is extra's gl_touches, renamed", row["goal_line_opportunities"] == 1)
    ok &= check(
        "a real, unrelated extra key (role_momentum) is NOT pulled in -- only the five named metrics",
        "role_momentum" not in row,
    )
    return ok


def run_missing_metrics_dont_block_the_row():
    print("\n" + "=" * 70)
    print("Missing individual metrics -- honest null, row still written")
    print("=" * 70)

    # carries and snap_share genuinely absent from extra (e.g. a real PFR
    # crosswalk miss for snap_share; a pure receiver who never rushed).
    row_in = _real_redzone_weekly_row(player_id="00-RECEIVER")
    del row_in["extra"]["carries"]
    del row_in["extra"]["snap_share"]

    out = rw.shape_player_season_evidence_rows([row_in])

    ok = True
    ok &= check("the row is still produced, not dropped", len(out) == 1)
    row = out[0]
    ok &= check("carries is honestly None, not 0 or a guess", row["carries"] is None)
    ok &= check("snap_share is honestly None, not 0 or a guess", row["snap_share"] is None)
    ok &= check("the OTHER three real metrics are unaffected by the two missing ones", row["targets"] == 5 and row["red_zone_opportunities"] == 3 and row["goal_line_opportunities"] == 1)
    return ok


def run_completely_empty_extra_still_produces_a_row():
    print("\n" + "=" * 70)
    print("A row with a genuinely empty extra -- still one honestly-null row, never a crash")
    print("=" * 70)

    row_in = {"player_id": "00-EMPTY", "season": 2026, "week": 5, "extra": {}}
    raised = None
    out = None
    try:
        out = rw.shape_player_season_evidence_rows([row_in])
    except Exception as e:
        raised = e

    ok = True
    ok &= check("no crash on an empty extra", raised is None)
    if out is not None:
        ok &= check("the row is still produced", len(out) == 1)
        ok &= check("every one of the five metrics is honestly None", all(out[0][c] is None for c in rw.NFL_PLAYER_SEASON_EVIDENCE_METRIC_KEYS.values()))
    return ok


def run_shaping_is_deterministic_same_input_same_output():
    print("\n" + "=" * 70)
    print("Determinism -- the real precondition a server-side upsert needs to be safe on re-run")
    print("=" * 70)

    rows_in = [_real_redzone_weekly_row(), _real_redzone_weekly_row(player_id="00-0039868", week=3)]
    out1 = rw.shape_player_season_evidence_rows(rows_in)
    out2 = rw.shape_player_season_evidence_rows(rows_in)

    ok = True
    ok &= check("two calls on the identical input produce byte-identical output", out1 == out2)
    return ok


def run_batch_never_references_another_week_or_player():
    print("\n" + "=" * 70)
    print("No pruning capability exists -- this function only ever touches the batch it was given")
    print("=" * 70)

    # A real multi-player, single-week batch, exactly what one reconcile_
    # week() run passes in. Nothing about processing week 3 should ever
    # produce output referencing week 2, or a player not in the input.
    rows_in = [
        _real_redzone_weekly_row(player_id="00-A", week=3),
        _real_redzone_weekly_row(player_id="00-B", week=3),
    ]
    out = rw.shape_player_season_evidence_rows(rows_in)

    ok = True
    ok &= check("output row count exactly matches input row count, no rows added or dropped", len(out) == len(rows_in))
    ok &= check("every output week matches an input week (3) -- nothing references another period", {r["period_index"] for r in out} == {3})
    ok &= check("every output player_id matches an input player_id -- nothing references another player", {r["player_id"] for r in out} == {"00-A", "00-B"})
    return ok


def run_write_forwards_to_the_real_signed_endpoint():
    print("\n" + "=" * 70)
    print("write_player_season_evidence_rows forwards via the same real signed-POST mechanism")
    print("=" * 70)

    calls = {}
    original = rw.forward_to_lovable if hasattr(rw, "forward_to_lovable") else None

    # forward_to_lovable is imported locally inside the function (lazy
    # import, matching write_player_redzone_weekly_rows' own established
    # shape) -- patch it on the real lovable_forward module so the local
    # import picks up the fake.
    import lovable_forward
    original_forward = lovable_forward.forward_to_lovable

    def fake_forward(rows, secret, url):
        calls["rows"] = rows
        calls["secret"] = secret
        calls["url"] = url
        return {"success": True, "status_code": 200, "error": None, "response_body": "{}"}

    lovable_forward.forward_to_lovable = fake_forward
    try:
        shaped = rw.shape_player_season_evidence_rows([_real_redzone_weekly_row()])
        result = rw.write_player_season_evidence_rows(shaped, "real-test-secret")
    finally:
        lovable_forward.forward_to_lovable = original_forward

    ok = True
    ok &= check("the call succeeded", result["success"] is True)
    ok &= check("the real shaped rows were passed through unchanged", calls.get("rows") == shaped)
    ok &= check("the real secret was threaded through", calls.get("secret") == "real-test-secret")
    ok &= check("resolved to the real nfl-player-season-evidence-write URL", calls.get("url") == rw.DEFAULT_NFL_PLAYER_SEASON_EVIDENCE_WRITE_URL)
    return ok


if __name__ == "__main__":
    results = [
        run_real_row_shapes_correctly(),
        run_missing_metrics_dont_block_the_row(),
        run_completely_empty_extra_still_produces_a_row(),
        run_shaping_is_deterministic_same_input_same_output(),
        run_batch_never_references_another_week_or_player(),
        run_write_forwards_to_the_real_signed_endpoint(),
    ]
    print()
    if all(results):
        print("All checks passed.")
    else:
        print("Some checks FAILED -- see above.")
        raise SystemExit(1)
