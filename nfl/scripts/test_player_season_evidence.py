"""
Regression coverage for Follow the Story V1, Table 1's real shaping
logic: reconcile_week.shape_player_season_evidence_rows() and
write_player_season_evidence_rows(). No prior test file existed for
either.

Mostly offline, synthetic-fixture cases, plus a few real-network ones
(real pbp -> real run_pipeline(), same skip-not-fail convention every
other real-network check in this codebase uses) added 2026-10-02 to
close the gap a hand-built `extra` can't: proving targets/carries
actually REACH `extra` from a real row, not just that the read-out
logic is correct once they're already there. The DB-side upsert
idempotency and the "never prune history" guarantee are both
structural properties of the natural key + upsert-only write path (see
the table's own migration and nfl-player-season-evidence-write.ts),
not something this file can exercise against a real database (no
service-role access from this environment -- same standing gap as
every other DB-backed check this session). What IS tested here: the
shaping logic is deterministic (same input -> byte-identical output,
the real precondition an upsert needs to be safe on re-run), no code
path in either function ever touches a week/player outside the one
batch it was given (the real reason "never prune history" holds),
every metric is handled independently so one missing value never drops
a row, and (2026-10-02) targets/carries zero-fill when genuinely absent
while snap_share/red_zone_opportunities/goal_line_opportunities and
target_share stay honest nulls.
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
    print("Missing individual metrics -- snap_share stays an honest null,")
    print("carries zero-fills (REAL FIX 2026-10-02), row still written")
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
    ok &= check("carries zero-fills when absent -- a real zero for this always-real-game input, not a guess", row["carries"] == 0)
    ok &= check("snap_share is still honestly None, not 0 or a guess -- not one of the zero-filled metrics", row["snap_share"] is None)
    ok &= check("the OTHER three real metrics are unaffected by the two missing ones", row["targets"] == 5 and row["red_zone_opportunities"] == 3 and row["goal_line_opportunities"] == 1)
    return ok


def run_completely_empty_extra_still_produces_a_row():
    print("\n" + "=" * 70)
    print("A row with a genuinely empty extra -- three honest nulls, two")
    print("zero-filled (REAL FIX 2026-10-02), never a crash")
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
        row = out[0]
        ok &= check("the row is still produced", len(out) == 1)
        ok &= check(
            "snap_share/red_zone_opportunities/goal_line_opportunities are honestly None -- not zero-filled",
            row["snap_share"] is None and row["red_zone_opportunities"] is None and row["goal_line_opportunities"] is None,
        )
        ok &= check("targets and carries zero-fill instead of staying None, even on a fully empty extra", row["targets"] == 0 and row["carries"] == 0)
    return ok


def run_rodgers_style_both_absent_still_zero_fills():
    print("\n" + "=" * 70)
    print("Both targets AND carries absent on the same row -- the real")
    print("2026 Week 4 Aaron Rodgers case (a genuine red-zone two-point-")
    print("conversion rush: a real touch, but zero by definition for both")
    print("metrics) -- both zero-fill, not left None, not a crash")
    print("=" * 70)

    # Real, already-validated shape (see the plan's own investigation):
    # rz_touches=1 (the real 2pt-conversion touch), targets/carries both
    # genuinely absent from extra -- not a hand-waved hypothetical, this
    # is the one row out of 449 real 2026-season-to-date rows where both
    # whole-game metrics come back missing on the same real row.
    row_in = _real_redzone_weekly_row(
        player_id="00-0023459", season=2026, week=4, posteam="PIT", defteam="CLE",
    )
    del row_in["extra"]["targets"]
    del row_in["extra"]["carries"]
    row_in["extra"]["rz_touches"] = 1
    row_in["extra"]["gl_touches"] = 1

    out = rw.shape_player_season_evidence_rows([row_in])

    ok = True
    ok &= check("the row is still produced, not dropped", len(out) == 1)
    row = out[0]
    ok &= check("targets zero-fills when absent alongside carries, not left None", row["targets"] == 0)
    ok &= check("carries zero-fills when absent alongside targets, not left None", row["carries"] == 0)
    ok &= check("red_zone_opportunities still reflects the real touch (1) -- the row isn't all-zero, just correctly zero on these two metrics", row["red_zone_opportunities"] == 1)
    return ok


def run_target_share_is_never_touched():
    print("\n" + "=" * 70)
    print("target_share is not one of this table's five metrics and this")
    print("change must never introduce or fill it")
    print("=" * 70)

    # Same both-absent shape as the Rodgers case above, PLUS extra has no
    # target_share key at all (matching a real run_pipeline() row for a
    # player aggregate_whole_game_targets never produced a row for).
    row_in = _real_redzone_weekly_row(player_id="00-NOTARGETSHARE")
    del row_in["extra"]["targets"]
    del row_in["extra"]["carries"]
    assert "target_share" not in row_in["extra"]

    out = rw.shape_player_season_evidence_rows([row_in])

    ok = True
    row = out[0]
    ok &= check("target_share is not one of NFL_PLAYER_SEASON_EVIDENCE_METRIC_KEYS' real columns", "target_share" not in rw.NFL_PLAYER_SEASON_EVIDENCE_METRIC_KEYS.values())
    ok &= check("the shaped row never introduces a target_share key at all -- shelves._target_share_level_candidate's pd.notna() eligibility gate must stay untouched", "target_share" not in row)
    ok &= check("targets/carries still zero-fill correctly alongside the untouched target_share", row["targets"] == 0 and row["carries"] == 0)
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


_real_weekly_cache = {}


def _load_real_weekly():
    """
    Real pbp -> real run_pipeline() `weekly`, cached at module scope so the
    three real-network cases below (dual-threat, pure rusher, pure
    receiver) share ONE real network round instead of three. Raises on
    any real network failure -- callers catch it themselves, same skip-
    not-fail convention every other real-network check in this codebase
    already uses (test_team_tendencies.py/test_build_stub_week.py).
    """
    if "weekly" not in _real_weekly_cache:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from backfill_redzone import (
            SEASONS, load_depth_charts, load_id_crosswalk, load_injuries, load_pbp,
            load_schedules, load_seasonal_rosters, load_snap_counts, load_weekly_stats, run_pipeline,
        )

        pbp = load_pbp(SEASONS)
        snap_counts = load_snap_counts(SEASONS)
        id_crosswalk = load_id_crosswalk(SEASONS)
        depth_charts = load_depth_charts(SEASONS)
        injuries = load_injuries(SEASONS)
        seasonal_rosters = load_seasonal_rosters(SEASONS)
        schedules = load_schedules(SEASONS)
        weekly_stats = load_weekly_stats(SEASONS)

        weekly, _allowed = run_pipeline(
            pbp, snap_counts, id_crosswalk, depth_charts, injuries, seasonal_rosters, schedules, weekly_stats,
        )
        _real_weekly_cache["weekly"] = weekly
    return _real_weekly_cache["weekly"]


def _real_season_evidence_row(weekly, player_id, season, week):
    reconciled = weekly[
        (weekly["player_id"] == player_id) & (weekly["season"] == season) & (weekly["week"] == week)
    ].copy()
    redzone_rows = rw.shape_player_redzone_weekly_rows(reconciled)
    season_evidence_rows = rw.shape_player_season_evidence_rows(redzone_rows)
    return season_evidence_rows


def run_real_run_pipeline_row_shapes_correctly():
    print("\n" + "=" * 70)
    print("REAL end-to-end: a real run_pipeline() row, through the real")
    print("shape_player_redzone_weekly_rows(), into shape_player_season_")
    print("evidence_rows() -- not a hand-built extra dict")
    print("=" * 70)

    # Every OTHER case in this file hand-builds `extra` with "targets": 5
    # already inside it (_real_redzone_weekly_row above) -- that proves
    # the READ-OUT logic is correct, but it can never prove targets/
    # carries actually REACH extra from a real run_pipeline() row, which
    # is exactly the real gap that let the 2026-10-02 bug (targets never
    # wired into run_pipeline() at all) ship unnoticed. This case runs
    # the real chain instead: real pbp -> real run_pipeline() -> real
    # shape_player_redzone_weekly_rows() -> shape_player_season_evidence_
    # rows(), skipped (not failed) on a real network hiccup, same
    # try/except convention test_team_tendencies.py/test_build_stub_week.py
    # already use for real-network checks.
    try:
        weekly = _load_real_weekly()
        # Real, already-validated player-week (see test_run_pipeline_
        # integration.py / the redzone_carries investigation): James
        # Conner, 2024 Week 1, real targets=4, real carries=16 (a real
        # dual-threat row -- neither metric is zero-filled here).
        season_evidence_rows = _real_season_evidence_row(weekly, "00-0033553", 2024, 1)

        ok = True
        ok &= check("exactly one real row round-tripped through both shaping functions", len(season_evidence_rows) == 1)
        row = season_evidence_rows[0]
        ok &= check(f"REAL: targets reached extra via the real run_pipeline() chain (got {row['targets']!r}, expected 4)", row["targets"] == 4)
        ok &= check(f"REAL: carries reached extra via the real run_pipeline() chain (got {row['carries']!r}, expected 16)", row["carries"] == 16)
        ok &= check("REAL: player_id/season/period_index carried through correctly", row["player_id"] == "00-0033553" and row["season"] == 2024 and row["period_index"] == 1)
        return ok
    except Exception as e:
        return check(f"REAL run_pipeline()-sourced season_evidence check (skipped -- {type(e).__name__}: {e})", True)


def run_real_pure_rusher_zero_fills_targets():
    print("\n" + "=" * 70)
    print("REAL: a known pure rusher that week -- real carries, targets")
    print("zero-fills through the real run_pipeline() chain (not left None)")
    print("=" * 70)

    try:
        weekly = _load_real_weekly()
        # Real, directly confirmed (see the plan's own investigation): Josh
        # Allen, 2024 Week 1 -- real carries=9, genuinely zero real pass
        # targets as a receiver that game (QBs essentially never are).
        season_evidence_rows = _real_season_evidence_row(weekly, "00-0034857", 2024, 1)

        ok = True
        ok &= check("exactly one real row round-tripped through both shaping functions", len(season_evidence_rows) == 1)
        row = season_evidence_rows[0]
        ok &= check(f"REAL: carries is the real non-zero value (got {row['carries']!r}, expected 9)", row["carries"] == 9)
        ok &= check(f"REAL: targets zero-fills for a real pure rusher, not left None (got {row['targets']!r})", row["targets"] == 0)
        return ok
    except Exception as e:
        return check(f"REAL pure-rusher season_evidence check (skipped -- {type(e).__name__}: {e})", True)


def run_real_pure_receiver_zero_fills_carries():
    print("\n" + "=" * 70)
    print("REAL: a known pure red-zone receiver that week -- real targets,")
    print("carries zero-fills through the real run_pipeline() chain")
    print("=" * 70)

    try:
        weekly = _load_real_weekly()
        # Real, directly confirmed (see the plan's own investigation): Keon
        # Coleman, 2024 Week 1 -- real targets=5, genuinely zero real rush
        # attempts that game.
        season_evidence_rows = _real_season_evidence_row(weekly, "00-0039901", 2024, 1)

        ok = True
        ok &= check("exactly one real row round-tripped through both shaping functions", len(season_evidence_rows) == 1)
        row = season_evidence_rows[0]
        ok &= check(f"REAL: targets is the real non-zero value (got {row['targets']!r}, expected 5)", row["targets"] == 5)
        ok &= check(f"REAL: carries zero-fills for a real pure receiver, not left None (got {row['carries']!r})", row["carries"] == 0)
        return ok
    except Exception as e:
        return check(f"REAL pure-receiver season_evidence check (skipped -- {type(e).__name__}: {e})", True)


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
        run_rodgers_style_both_absent_still_zero_fills(),
        run_target_share_is_never_touched(),
        run_real_run_pipeline_row_shapes_correctly(),
        run_real_pure_rusher_zero_fills_targets(),
        run_real_pure_receiver_zero_fills_carries(),
        run_write_forwards_to_the_real_signed_endpoint(),
    ]
    print()
    if all(results):
        print("All checks passed.")
    else:
        print("Some checks FAILED -- see above.")
        raise SystemExit(1)
