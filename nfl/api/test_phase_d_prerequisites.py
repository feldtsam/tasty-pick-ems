"""
Phase D code prerequisites:
  1. shelf-signal-history wiring into /api/curate-and-write-drafts
     (read prior_assignments before curation, write signal history after)
  2. GET /api/nfl-current-week target-week resolver

The stickiness STATE MACHINE is already covered by test_stickiness.py;
this file only confirms the ENDPOINT calls both functions correctly, in
the style of test_curation_rerun_guard.py. The resolver is tested with a
synthetic schedule (monkeypatched nfl.import_schedules) — no network.

Run: python3 nfl/api/test_phase_d_prerequisites.py
"""
import json
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vendor"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pandas as pd

os.environ.setdefault("PIPELINE_INCOMING_SECRET", "test-incoming")
os.environ.setdefault("NFL_PIPELINE_WEBHOOK_SECRET", "test-webhook")

import api.index as idx

AUTH = {"X-Pipeline-Secret": "test-incoming"}


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


def _toy_stub_frame():
    return pd.DataFrame([
        {"player_id": "00-A", "season": 2026, "week": 5, "posteam": "KC", "position_group": "WR",
         "td_opportunity": 71.0, "role_momentum": 55.0, "situation": 60.0,
         "market_value_score": None, "tpe_score": 64.0},
        {"player_id": "00-B", "season": 2026, "week": 5, "posteam": "SF", "position_group": "RB",
         "td_opportunity": 40.0, "role_momentum": 50.0, "situation": 45.0,
         "market_value_score": None, "tpe_score": 41.0},
    ])


def _synthetic_schedule(season, weeks_played, weeks_upcoming, week1_first="09-07"):
    """A minimal import_schedules-shaped frame: `weeks_played` have real
    scores + past gamedays, `weeks_upcoming` have null scores + future
    gamedays. 16 games/week."""
    rows = []
    for wk in list(weeks_played) + list(weeks_upcoming):
        played = wk in weeks_played
        # week N gameday ≈ (week1_first + (N-1) weeks), Sun+Mon
        base = date.fromisoformat(f"{season}-{week1_first}")
        gd = (base.toordinal() + (wk - 1) * 7)
        for g in range(16):
            rows.append({
                "season": season, "week": wk, "game_type": "REG" if wk <= 18 else "WC",
                "gameday": date.fromordinal(gd + (g % 3)).isoformat(),
                "home_team": f"H{g}", "away_team": f"A{g}",
                "home_score": 21.0 if played else None,
                "away_score": 17.0 if played else None,
            })
    return pd.DataFrame(rows)


if __name__ == "__main__":
    results = []

    # ==================================================================
    # 1. shelf-signal-history wiring
    # ==================================================================
    client = idx.app.test_client()
    calls = {}

    def fake_curate(weekly, season, week, prior_assignments=None, **kw):
        calls["curate_prior_assignments"] = prior_assignments
        return {
            "content_draft_rows": [
                {"player_id": "00-A", "title": "t", "is_tasty_six": False, "shelf": "attd_500_699"},
            ],
            "shelf_signal_history_rows": [
                {"player_id": "00-A", "season": season, "week": week, "home_shelf": "attd_500_699",
                 "qualifying_signals": {"attd_500_699": 55.0}, "pending_shelf": None, "pending_run_count": 0},
                {"player_id": "00-B", "season": season, "week": week, "home_shelf": "attd_300_499",
                 "qualifying_signals": {"attd_300_499": 40.0}, "pending_shelf": None, "pending_run_count": 0},
            ],
        }

    def fake_prior(season, week, player_ids, secret):
        calls["prior_args"] = {"season": season, "week": week, "n_ids": len(player_ids), "secret": secret}
        return {"00-A": {"home_shelf": "attd_700_plus", "pending_shelf": None, "pending_run_count": 0}}

    def fake_ssh_write(rows, secret, write_url=None):
        calls["ssh_write"] = {"n": len(rows), "secret": secret, "rows": rows}
        return {"success": True, "status_code": 200, "error": None, "response_body": "{}"}

    def fake_content_write(rows, secret, write_url=None):
        calls["content_write"] = {"n": len(rows)}
        return {"success": True, "status_code": 200, "error": None, "response_body": "{}"}

    orig = {
        "curate": idx.curate_nfl_shelves, "prior": idx.build_prior_state_with_walkback,
        "ssh": idx.write_shelf_signal_history_rows, "content": idx.write_content_draft_rows,
        "snap": idx.stub_week_snapshot, "pre": idx.read_content_draft_review_states,
        "mv": idx.market_value_snapshot_for_curation,
        "sched": idx.nfl.import_schedules, "pbp": idx.nfl.import_pbp_data,
    }
    idx.curate_nfl_shelves = fake_curate
    idx.build_prior_state_with_walkback = fake_prior
    idx.write_shelf_signal_history_rows = fake_ssh_write
    idx.write_content_draft_rows = fake_content_write
    idx.stub_week_snapshot = lambda s, w, secret: _toy_stub_frame()
    idx.read_content_draft_review_states = lambda s, w, secret: {"ok": True, "reviewed_count": 0, "rows": [], "error": None, "status_code": 200}
    idx.market_value_snapshot_for_curation = lambda s, w, secret: pd.DataFrame(
        columns=["player_id", "season", "week"] + idx.CURATION_MARKET_VALUE_COLUMNS)
    idx.nfl.import_schedules = lambda seasons: None
    idx.nfl.import_pbp_data = lambda *a, **k: None
    try:
        # real (non-preview) run
        calls.clear()
        r = client.post("/api/curate-and-write-drafts", headers=AUTH, json={"season": 2026, "week": 5})
        body = r.get_json()
        results.append(check(
            "curate reads prior state: build_prior_state_with_walkback called with (season, week, all stub player_ids)",
            calls.get("prior_args", {}).get("season") == 2026 and calls["prior_args"]["week"] == 5
            and calls["prior_args"]["n_ids"] == 2,
        ))
        results.append(check(
            "curate passes prior_assignments into curate_nfl_shelves",
            calls.get("curate_prior_assignments") == {"00-A": {"home_shelf": "attd_700_plus", "pending_shelf": None, "pending_run_count": 0}},
        ))
        results.append(check(
            "curate writes shelf_signal_history AFTER content: write_shelf_signal_history_rows called with ALL "
            "home-assigned players (2), not just the 1 content-ready row",
            calls.get("ssh_write", {}).get("n") == 2 and calls["ssh_write"]["secret"] == "test-webhook",
        ))
        results.append(check(
            "response 'stickiness' block reports the wiring",
            body["stickiness"]["prior_assignments_players"] == 1
            and body["stickiness"]["signal_history_rows"] == 2
            and body["stickiness"]["signal_history_written"] is True
            and body["stickiness"]["signal_history_write_ok"] is True,
        ))

        # preview_only: reads prior state (affects curation) but writes nothing
        calls.clear()
        r = client.post("/api/curate-and-write-drafts", headers=AUTH, json={"season": 2026, "week": 5, "preview_only": True})
        body = r.get_json()
        results.append(check(
            "preview_only: prior state still read (curation output must reflect a real run)",
            "prior_args" in calls,
        ))
        results.append(check(
            "preview_only: shelf_signal_history NOT written",
            "ssh_write" not in calls and body["stickiness"]["signal_history_written"] is False,
        ))

        # ssh write failure is logged, not fatal (still 200, still reports content result)
        calls.clear()
        idx.write_shelf_signal_history_rows = lambda rows, secret, write_url=None: {
            "success": False, "status_code": 500, "error": "lovable boom", "response_body": None}
        r = client.post("/api/curate-and-write-drafts", headers=AUTH, json={"season": 2026, "week": 5})
        body = r.get_json()
        results.append(check(
            "shelf_signal_history write failure -> logged, NOT a 502; run still succeeds",
            r.status_code == 200 and body["stickiness"]["signal_history_write_ok"] is False
            and body["forwarded"] is True,
        ))
        idx.write_shelf_signal_history_rows = fake_ssh_write

        # week 1 / no prior state -> {} -> first-appearance, no crash
        calls.clear()
        idx.build_prior_state_with_walkback = lambda s, w, ids, secret: {}
        r = client.post("/api/curate-and-write-drafts", headers=AUTH, json={"season": 2026, "week": 5, "preview_only": True})
        results.append(check(
            "empty prior state -> prior_assignments={} passed through (first-appearance mode)",
            r.status_code == 200 and calls.get("curate_prior_assignments") == {},
        ))
        idx.build_prior_state_with_walkback = fake_prior

        # prior-state read failure -> {} fallback, curation still runs
        calls.clear()
        def boom_prior(s, w, ids, secret):
            raise RuntimeError("read route down")
        idx.build_prior_state_with_walkback = boom_prior
        r = client.post("/api/curate-and-write-drafts", headers=AUTH, json={"season": 2026, "week": 5, "preview_only": True})
        results.append(check(
            "prior-state read failure -> caught, prior_assignments={}, curation proceeds",
            r.status_code == 200 and calls.get("curate_prior_assignments") == {},
        ))
    finally:
        idx.curate_nfl_shelves = orig["curate"]
        idx.build_prior_state_with_walkback = orig["prior"]
        idx.write_shelf_signal_history_rows = orig["ssh"]
        idx.write_content_draft_rows = orig["content"]
        idx.stub_week_snapshot = orig["snap"]
        idx.read_content_draft_review_states = orig["pre"]
        idx.market_value_snapshot_for_curation = orig["mv"]
        idx.nfl.import_schedules = orig["sched"]
        idx.nfl.import_pbp_data = orig["pbp"]

    # ==================================================================
    # 2. /api/nfl-current-week resolver
    # ==================================================================
    orig_sched = idx.nfl.import_schedules
    try:
        # Synthetic 2026 season: weeks 1-4 played, 5-18 upcoming (first game 2026-09-07)
        sched_2026 = _synthetic_schedule(2026, weeks_played=range(1, 5), weeks_upcoming=range(5, 19), week1_first="09-07")
        idx.nfl.import_schedules = lambda seasons: sched_2026 if 2026 in seasons else pd.DataFrame()

        r = idx._resolve_nfl_target_weeks(date(2026, 10, 6))  # Tue after week 4
        results.append(check(
            "resolver: Tue after Wk4 -> curate_target Wk5, reconcile_target Wk4",
            r["curate_target"]["week"] == 5 and r["reconcile_target"]["week"] == 4
            and r["status"] == "in_season" and r["reconcile_target_is_recent"] is True,
        ))

        r = idx._resolve_nfl_target_weeks(date(2026, 8, 25))  # ~2 weeks before Wk1
        results.append(check(
            "resolver: 2 weeks pre-Wk1 -> curate_target Wk1, reconcile_target null, status preseason",
            r["curate_target"]["week"] == 1 and r["reconcile_target"] is None and r["status"] == "preseason",
        ))

        r = idx._resolve_nfl_target_weeks(date(2026, 4, 1))  # deep offseason
        results.append(check(
            "resolver: April -> status offseason (Wk1 > 40 days out)",
            r["status"] == "offseason" and r["curate_target"]["week"] == 1,
        ))

        # all weeks played -> no curate target
        sched_done = _synthetic_schedule(2026, weeks_played=range(1, 19), weeks_upcoming=[], week1_first="09-07")
        idx.nfl.import_schedules = lambda seasons: sched_done if 2026 in seasons else pd.DataFrame()
        r = idx._resolve_nfl_target_weeks(date(2027, 1, 20))
        results.append(check(
            "resolver: season fully played, next not published -> curate_target null, status offseason",
            r["curate_target"] is None and r["status"] == "offseason",
        ))

        # schedule unavailable
        idx.nfl.import_schedules = lambda seasons: pd.DataFrame()
        r = idx._resolve_nfl_target_weeks(date(2030, 9, 1))
        results.append(check(
            "resolver: no schedule for either season -> status schedule_unavailable, both targets null",
            r["status"] == "schedule_unavailable" and r["curate_target"] is None and r["reconcile_target"] is None,
        ))

        # import_schedules raising -> _nfl_week_windows returns [], resolver still returns 200-shaped dict
        def raiser(seasons):
            raise RuntimeError("nflverse down")
        idx.nfl.import_schedules = raiser
        r = idx._resolve_nfl_target_weeks(date(2026, 10, 6))
        results.append(check(
            "resolver: import_schedules raises -> schedule_unavailable, no exception bubbles",
            r["status"] == "schedule_unavailable",
        ))

        # endpoint: bad as_of -> 400; good as_of -> 200
        idx.nfl.import_schedules = lambda seasons: sched_2026 if 2026 in seasons else pd.DataFrame()
        client = idx.app.test_client()
        r = client.get("/api/nfl-current-week?as_of=not-a-date")
        results.append(check("endpoint: bad as_of -> 400", r.status_code == 400))
        r = client.get("/api/nfl-current-week?as_of=2026-10-06")
        body = r.get_json()
        results.append(check(
            "endpoint: GET (no auth) -> 200, curate/reconcile targets + status",
            r.status_code == 200 and body["curate_target"]["week"] == 5
            and body["reconcile_target"]["week"] == 4 and body["status"] == "in_season",
        ))
        # no X-Pipeline-Secret header needed
        results.append(check("endpoint: no auth header required (it's read-only public schedule math)", "error" not in body))
    finally:
        idx.nfl.import_schedules = orig_sched

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
