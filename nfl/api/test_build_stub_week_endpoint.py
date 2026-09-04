"""
Tests the /api/build-stub-week endpoint and the rewired stub-read path in
/api/curate-and-write-drafts (Phase A of the NFL weekly-automation plan).

Endpoint plumbing only — auth gate, body validation, preview_only, the
table-write call, the stub_csv escape hatch, and the "0 rows -> 404 /
read failure -> 500" split. build_stub_week()'s pipeline and
curate_nfl_shelves()'s curation are monkeypatched out; their real
behaviour is covered by scripts/test_stub_store.py and
api/test_curate_home_shelves.py respectively.

Run: python3 nfl/api/test_build_stub_week_endpoint.py
"""
import json
import os
import sys
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
STUB_CSV = Path(__file__).resolve().parent.parent / "data" / "stub_weeks" / "2026_wk1.csv"
SYNTH_CSV = Path(__file__).resolve().parent.parent / "data" / "stub_weeks" / "synthetic_smoke_test.csv"


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


def _toy_stub_frame():
    return pd.DataFrame(
        [
            {"player_id": "00-0000001", "season": 2026, "week": 3, "posteam": "KC",
             "position_group": "WR", "td_opportunity": 71.2, "role_momentum": 55.0,
             "situation": 60.1, "market_value_score": None, "tpe_score": 64.4, "x_extra": 1},
        ]
    )


if __name__ == "__main__":
    results = []
    client = idx.app.test_client()

    # ------------------------------------------------------------------
    # /api/build-stub-week
    # ------------------------------------------------------------------
    r = client.post("/api/build-stub-week", json={"season": 2026, "week": 3})
    results.append(check("build-stub-week: 401 without the X-Pipeline-Secret header", r.status_code == 401))

    r = client.post("/api/build-stub-week", headers=AUTH, json={"season": "notanint"})
    results.append(check("build-stub-week: 400 on a non-integer season/week", r.status_code == 400))

    calls = {}

    def fake_build(season, week, historical_seasons=None):
        calls["build"] = {"season": season, "week": week, "historical_seasons": historical_seasons}
        return _toy_stub_frame()

    def fake_write(rows, secret, write_url=None):
        calls["write"] = {"n": len(rows), "secret": secret, "rows": rows}
        return {"success": True, "status_code": 200, "error": None,
                "response_body": json.dumps({"ok": True, "upserted": len(rows)})}

    orig_build, orig_write = idx.build_stub_week, idx.write_stub_rows
    idx.build_stub_week, idx.write_stub_rows = fake_build, fake_write
    try:
        r = client.post("/api/build-stub-week", headers=AUTH,
                        json={"season": 2026, "week": 3, "preview_only": True})
        body = r.get_json()
        results.append(check(
            "build-stub-week: preview_only builds + shapes but never writes",
            r.status_code == 200 and body["status"] == "preview" and body["row_count"] == 1
            and "write" not in calls and len(body["sample_rows"]) == 1,
        ))
        results.append(check(
            "build-stub-week: build includes the prior season as a fallback "
            "(historical_seasons=[season - 1, season], fixed 2026-09-04 — a real Week 1 "
            "with no pbp published yet used to crash on [season] alone)",
            calls["build"]["historical_seasons"] == [2025, 2026],
        ))

        calls.clear()
        r = client.post("/api/build-stub-week", headers=AUTH, json={"season": 2026, "week": 3})
        body = r.get_json()
        results.append(check(
            "build-stub-week: default path writes to the table and reports success",
            r.status_code == 200 and body["status"] == "success" and body["forwarded"] is True
            and calls["write"]["n"] == 1 and calls["write"]["secret"] == "test-webhook",
        ))

        # stub_csv escape hatch: skips build_stub_week entirely
        if STUB_CSV.exists():
            calls.clear()
            r = client.post("/api/build-stub-week", headers=AUTH,
                            json={"season": 2026, "week": 1, "stub_csv": "2026_wk1.csv", "preview_only": True})
            body = r.get_json()
            results.append(check(
                "build-stub-week: stub_csv reads the fixture and skips build_stub_week()",
                r.status_code == 200 and "build" not in calls and body["row_count"] == 772,
            ))

            # stub_csv rebinds season/week to the request body — 2026_wk1.csv
            # (season=2026, week=1 in its rows) seeds an isolated week 2.
            calls.clear()
            r = client.post("/api/build-stub-week", headers=AUTH,
                            json={"season": 2026, "week": 2, "stub_csv": "2026_wk1.csv"})
            written = calls["write"]["rows"]
            results.append(check(
                "build-stub-week: stub_csv rebinds every row's season/week to the request body (week 1 fixture -> week 2)",
                r.status_code == 200
                and len(written) == 772
                and all(w["season"] == 2026 and w["week"] == 2 for w in written)
                and all("week" not in w["extra"] for w in written),  # week is a typed col, never in extra
            ))

            # THE COLLISION FIX: game_id (-> event_id in curation) is also
            # rebound, so a rebound fixture's curated rows can never upsert
            # onto a real production row for the fixture's ORIGINAL week.
            src_gids = {g for g in pd.read_csv(STUB_CSV)["game_id"].dropna().astype(str)}
            src_weeks = {g.split("_")[1] for g in src_gids if len(g.split("_")) == 4}
            rebound_gids = [w["extra"]["game_id"] for w in written if w["extra"].get("game_id")]
            results.append(check(
                "build-stub-week: stub_csv rebinds every game_id's {season}_{week} prefix too "
                f"(source weeks {sorted(src_weeks)} -> all rebound to 2026_02_*)",
                len(rebound_gids) == 772
                and all(g.startswith("2026_02_") for g in rebound_gids)
                and not (set(rebound_gids) & src_gids),  # zero overlap with the source fixture's real game_ids
            ))

        # the synthetic fixture: unmistakably fake identifiers survive the rebind
        if SYNTH_CSV.exists():
            calls.clear()
            r = client.post("/api/build-stub-week", headers=AUTH,
                            json={"season": 2026, "week": 5, "stub_csv": "synthetic_smoke_test.csv"})
            written = calls["write"]["rows"]
            results.append(check(
                "build-stub-week: synthetic_smoke_test.csv stays collision-proof after rebind "
                "(TEST- player_ids, TS-team game_ids, no real gsis ids or team codes)",
                r.status_code == 200 and len(written) == 772
                and all(str(w["player_id"]).startswith("TEST-") for w in written)
                and all(w["extra"]["game_id"].startswith("2026_05_TS") for w in written if w["extra"].get("game_id")),
            ))

        # a failed downstream write surfaces as 502
        idx.write_stub_rows = lambda rows, secret, write_url=None: {
            "success": False, "status_code": 500, "error": "lovable boom", "response_body": None}
        r = client.post("/api/build-stub-week", headers=AUTH, json={"season": 2026, "week": 3})
        results.append(check("build-stub-week: a failed table write surfaces as 502", r.status_code == 502))
    finally:
        idx.build_stub_week, idx.write_stub_rows = orig_build, orig_write

    # health check
    r = client.get("/api/build-stub-week")
    results.append(check("build-stub-week: GET health check is 200 + deployed_via",
                         r.status_code == 200 and r.get_json()["deployed_via"] == "github-auto-deploy"))

    # ------------------------------------------------------------------
    # /api/curate-and-write-drafts — rewired stub read
    # ------------------------------------------------------------------
    seen = {}

    def fake_curate(weekly, season, week, **kw):
        seen["rows"] = len(weekly)
        seen["cols"] = set(weekly.columns)
        return {"content_draft_rows": [], "shelf_signal_history_rows": []}

    orig_curate = idx.curate_nfl_shelves
    orig_snap = idx.stub_week_snapshot
    orig_preflight = idx.read_content_draft_review_states
    idx.curate_nfl_shelves = fake_curate
    # Phase C re-run guard pre-flight: stub out to "nothing reviewed" so
    # these Phase A stub-read tests exercise the path they mean to.
    idx.read_content_draft_review_states = lambda s, w, secret: {
        "ok": True, "reviewed_count": 0, "rows": [], "error": None, "status_code": 200}
    try:
        # table path
        idx.stub_week_snapshot = lambda s, w, secret: _toy_stub_frame()
        r = client.post("/api/curate-and-write-drafts", headers=AUTH,
                        json={"season": 2026, "week": 3, "preview_only": True})
        results.append(check(
            "curate: reads the week from stub_week_snapshot() and feeds it to curate_nfl_shelves()",
            r.status_code == 200 and seen["rows"] == 1,
        ))

        # empty snapshot -> 404
        idx.stub_week_snapshot = lambda s, w, secret: _toy_stub_frame().iloc[0:0]
        r = client.post("/api/curate-and-write-drafts", headers=AUTH,
                        json={"season": 2026, "week": 3, "preview_only": True})
        results.append(check("curate: an empty stub snapshot -> 404 (build hasn't run)", r.status_code == 404))

        # read failure -> 500 status=error
        def boom(s, w, secret):
            raise RuntimeError("stub read transport failure")

        idx.stub_week_snapshot = boom
        r = client.post("/api/curate-and-write-drafts", headers=AUTH,
                        json={"season": 2026, "week": 3, "preview_only": True})
        results.append(check("curate: a stub-read transport failure -> 500 status=error",
                             r.status_code == 500 and r.get_json().get("status") == "error"))

        # stub_csv escape hatch bypasses the table entirely
        if STUB_CSV.exists():
            idx.stub_week_snapshot = boom  # must NOT be called
            seen.clear()
            r = client.post("/api/curate-and-write-drafts", headers=AUTH,
                            json={"season": 2026, "week": 1, "stub_csv": "2026_wk1.csv", "preview_only": True})
            results.append(check(
                "curate: stub_csv reads the fixture, never touches the table",
                r.status_code == 200 and seen["rows"] == 772,
            ))
    finally:
        idx.curate_nfl_shelves = orig_curate
        idx.stub_week_snapshot = orig_snap
        idx.read_content_draft_review_states = orig_preflight

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
