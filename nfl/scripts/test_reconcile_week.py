"""
Regression coverage for reconcile_week()'s secret-handling behavior --
the 2026-09-28 fix for the silent-skip-on-missing-secret bug (see that
function's own docstring/comments). No prior test file existed for
reconcile_week() at all; this is new coverage, not a rewrite.

run_pipeline (the one real, heavy data-loading call reconcile_week()
makes) is monkeypatched to return a tiny synthetic frame, so these
tests never touch real nflverse data or the network -- the only thing
under test is the secret-gated control flow around the persistence
write and the stub-flag update.
"""
import os
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import reconcile_week as rw


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    return condition


def _fake_run_pipeline(*args, **kwargs):
    weekly = pd.DataFrame([
        {
            "player_id": "00-TEST1", "season": 2026, "week": 3,
            "posteam": "SF", "defteam": "SEA", "position_group": "WR",
            "role_momentum": 55.0, "role_trend": 50.0, "external_opportunity": 0.0,
            "role_momentum_completeness": 1.0, "depth_rank": 1,
            "ahead_injury_statuses": [], "ahead_injured_teammates": [],
            "defensive_matchup_vulnerability": 50.0, "defensive_matchup_completeness": 1.0,
            # Not a typed column (see NFL_PLAYER_REDZONE_WEEKLY_TYPED_COLUMNS) --
            # included here so run_real_secret_persists_and_flags can confirm
            # it actually survives into the persisted row's `extra`, the same
            # real thing add_carries()/run_pipeline() produce on this field
            # in production (test_redzone_carries.py covers the join itself).
            "carries": 3,
        }
    ])
    return weekly, weekly.iloc[0:0].copy()


def _fake_market_value_snapshot(season, week, secret, url=None):
    return pd.DataFrame(columns=[
        "player_id", "season", "week", "market_value_score", "market_value_completeness",
        "consensus_implied_probability", "best_price",
    ])


# reconcile_week() only calls the real (network-hitting) load_pbp/
# load_snap_counts/etc. loaders when the corresponding param is None --
# passing trivial non-None placeholders for all of them, alongside the
# run_pipeline patch above, keeps this test fully offline. run_pipeline
# itself is mocked and ignores these values, so their real content
# doesn't matter, only that they're not None.
_OFFLINE_LOAD_KWARGS = dict(
    pbp=pd.DataFrame(), snap_counts=pd.DataFrame(), id_crosswalk=pd.DataFrame(),
    depth_charts=pd.DataFrame(), injuries=pd.DataFrame(), seasonal_rosters=pd.DataFrame(),
    schedules=pd.DataFrame(), weekly_stats=pd.DataFrame(),
)


def _install_common_fakes(monkeypatch_targets: dict):
    originals = {}
    for name, fn in monkeypatch_targets.items():
        originals[name] = getattr(rw, name)
        setattr(rw, name, fn)
    return originals


def _restore(originals: dict):
    for name, fn in originals.items():
        setattr(rw, name, fn)


def run_missing_secret_raises():
    print("\n" + "=" * 70)
    print("MISSING SECRET -- persistence write must raise, never silently skip")
    print("=" * 70)

    def _write_should_never_be_called(*args, **kwargs):
        raise AssertionError("write_player_redzone_weekly_rows was called despite a falsy secret")

    def _flag_should_never_be_called(*args, **kwargs):
        raise AssertionError("mark_stub_week_reconciled was called despite a falsy secret")

    originals = _install_common_fakes({
        "run_pipeline": _fake_run_pipeline,
        "market_value_snapshot_for_reconciliation": _fake_market_value_snapshot,
        "write_player_redzone_weekly_rows": _write_should_never_be_called,
        "mark_stub_week_reconciled": _flag_should_never_be_called,
    })
    env_had_secret = "NFL_PIPELINE_WEBHOOK_SECRET" in os.environ
    old_env_secret = os.environ.pop("NFL_PIPELINE_WEBHOOK_SECRET", None)

    raised = None
    try:
        rw.reconcile_week(2026, 3, secret=None, **_OFFLINE_LOAD_KWARGS)
    except RuntimeError as e:
        raised = e
    except Exception as e:
        raised = e
    finally:
        _restore(originals)
        if env_had_secret:
            os.environ["NFL_PIPELINE_WEBHOOK_SECRET"] = old_env_secret

    ok = True
    ok &= check("a falsy secret raises (not a silent warn-and-continue)", raised is not None)
    ok &= check(
        "it's a RuntimeError, not some other exception type (e.g. the write-call AssertionErrors above)",
        isinstance(raised, RuntimeError),
    )
    if isinstance(raised, RuntimeError):
        msg = str(raised)
        ok &= check("the message names the real target table", "nfl_player_redzone_weekly" in msg)
        ok &= check("the message names the real week", "2026 Week 3" in msg)
    return ok


def run_real_secret_persists_and_flags():
    print("\n" + "=" * 70)
    print("REAL SECRET -- both the persistence write and the stub-flag update actually run")
    print("=" * 70)

    calls = {"write": None, "flag": None, "season_evidence": None}

    def _fake_write(rows, secret, url=None):
        calls["write"] = (rows, secret)
        return {"success": True, "status_code": 200, "error": None}

    def _fake_flag(season, week, secret, url=None):
        calls["flag"] = (season, week, secret)
        return {"success": True, "status_code": 200, "error": None, "response_body": "{}"}

    def _fake_season_evidence_write(rows, secret, url=None):
        calls["season_evidence"] = (rows, secret)
        return {"success": True, "status_code": 200, "error": None}

    originals = _install_common_fakes({
        "run_pipeline": _fake_run_pipeline,
        "market_value_snapshot_for_reconciliation": _fake_market_value_snapshot,
        "write_player_redzone_weekly_rows": _fake_write,
        "mark_stub_week_reconciled": _fake_flag,
        "write_player_season_evidence_rows": _fake_season_evidence_write,
    })

    raised = None
    result = None
    try:
        result = rw.reconcile_week(2026, 3, secret="real-test-secret", **_OFFLINE_LOAD_KWARGS)
    except Exception as e:
        raised = e
    finally:
        _restore(originals)

    ok = True
    ok &= check("no exception when a real secret is supplied", raised is None)
    ok &= check("reconcile_week() still returns the real reconciled DataFrame", result is not None and len(result) == 1)
    ok &= check("the persistence write actually ran (not skipped)", calls["write"] is not None)
    ok &= check("the stub-flag update actually ran (not skipped, and not dead code)", calls["flag"] is not None)
    if calls["write"] is not None:
        ok &= check("the real secret was threaded through to the write call", calls["write"][1] == "real-test-secret")
    if calls["flag"] is not None:
        ok &= check("the real secret was threaded through to the flag call", calls["flag"][2] == "real-test-secret")
    if calls["write"] is not None:
        written_rows = calls["write"][0]
        ok &= check("exactly one row was shaped for the write", len(written_rows) == 1)
        if written_rows:
            extra = written_rows[0].get("extra", {})
            ok &= check(
                "carries (not a typed column) survived into the persisted row's extra jsonb, "
                "same mechanism as snap_share/targets/rz_touches/gl_touches",
                extra.get("carries") == 3,
            )
            ok &= check("carries is NOT one of the typed top-level keys (by design, see redzone_carries fix)", "carries" not in written_rows[0])
    ok &= check("nfl_player_season_evidence write also ran, derived from the same rows (Table 1)", calls["season_evidence"] is not None)
    if calls["season_evidence"] is not None:
        se_rows, se_secret = calls["season_evidence"]
        ok &= check("one season_evidence row for the one reconciled row", len(se_rows) == 1)
        ok &= check("the real secret threaded through to the season_evidence write too", se_secret == "real-test-secret")
        if se_rows:
            ok &= check("season_evidence row carries the real carries value (via extra)", se_rows[0]["carries"] == 3)
            ok &= check("period_type/period_index shaped correctly for V1", se_rows[0]["period_type"] == "game" and se_rows[0]["period_index"] == 3)
    return ok


def run_season_evidence_write_failure_is_not_fatal():
    print("\n" + "=" * 70)
    print("Table 1 write failing does NOT fail an otherwise-successful reconcile_week() run")
    print("=" * 70)

    def _fake_write(rows, secret, url=None):
        return {"success": True, "status_code": 200, "error": None}

    def _fake_flag(season, week, secret, url=None):
        return {"success": True, "status_code": 200, "error": None, "response_body": "{}"}

    def _fake_season_evidence_write_fails(rows, secret, url=None):
        return {"success": False, "status_code": 500, "error": "simulated: nfl_player_season_evidence write failed"}

    originals = _install_common_fakes({
        "run_pipeline": _fake_run_pipeline,
        "market_value_snapshot_for_reconciliation": _fake_market_value_snapshot,
        "write_player_redzone_weekly_rows": _fake_write,
        "mark_stub_week_reconciled": _fake_flag,
        "write_player_season_evidence_rows": _fake_season_evidence_write_fails,
    })

    raised = None
    result = None
    try:
        result = rw.reconcile_week(2026, 3, secret="real-test-secret", **_OFFLINE_LOAD_KWARGS)
    except Exception as e:
        raised = e
    finally:
        _restore(originals)

    ok = True
    ok &= check(
        "a FAILED nfl_player_season_evidence write does not raise -- unlike the "
        "nfl_player_redzone_weekly write, this table has no reader yet",
        raised is None,
    )
    ok &= check("reconcile_week() still returns the real reconciled DataFrame despite the Table 1 failure", result is not None and len(result) == 1)
    return ok


def run_no_real_rows_still_raises_value_error():
    print("\n" + "=" * 70)
    print("NO REAL ROWS FOR (season, week) -- unaffected, pre-existing behavior")
    print("=" * 70)

    originals = _install_common_fakes({"run_pipeline": _fake_run_pipeline})
    raised = None
    try:
        # week 99 has no rows in the synthetic frame at all.
        rw.reconcile_week(2026, 99, secret="irrelevant-never-reached", **_OFFLINE_LOAD_KWARGS)
    except ValueError as e:
        raised = e
    except Exception as e:
        raised = e
    finally:
        _restore(originals)

    ok = True
    ok &= check("a week with zero real rows still raises ValueError, not the new RuntimeError", isinstance(raised, ValueError))
    return ok


if __name__ == "__main__":
    results = [
        run_missing_secret_raises(),
        run_real_secret_persists_and_flags(),
        run_no_real_rows_still_raises_value_error(),
        run_season_evidence_write_failure_is_not_fatal(),
    ]
    print()
    if all(results):
        print("All checks passed.")
    else:
        print("Some checks FAILED -- see above.")
        raise SystemExit(1)
