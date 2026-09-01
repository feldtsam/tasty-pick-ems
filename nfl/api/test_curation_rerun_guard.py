"""
Tests the Phase C re-run guard on /api/curate-and-write-drafts:
the endpoint pre-flight (409 status=locked when review has started, no
curation run, LLM never invoked) and its force override, plus the
read_content_draft_review_states() helper in isolation.

The DB-level backstop (the protect_nfl_content_draft_review_status
trigger) is verified separately by
supabase/tests/nfl_content_drafts_review_guard.test.sql against a real
Postgres — it can't be exercised from Python.

Run: python3 nfl/api/test_curation_rerun_guard.py
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
from curate_home_shelves import read_content_draft_review_states

AUTH = {"X-Pipeline-Secret": "test-incoming"}


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


def _toy_stub_frame():
    return pd.DataFrame([{
        "player_id": "00-0000001", "season": 2026, "week": 5, "posteam": "KC",
        "position_group": "WR", "td_opportunity": 71.2, "role_momentum": 55.0,
        "situation": 60.1, "market_value_score": None, "tpe_score": 64.4,
    }])


class _FakeResp:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.text = body if isinstance(body, str) else json.dumps(body)


if __name__ == "__main__":
    results = []
    client = idx.app.test_client()

    # ------------------------------------------------------------------
    # read_content_draft_review_states() — helper in isolation
    # ------------------------------------------------------------------
    import lovable_forward
    orig_post = lovable_forward.requests.post
    try:
        lovable_forward.requests.post = lambda url, **kw: _FakeResp(200, {
            "ok": True, "row_count": 3, "reviewed_count": 2,
            "content_drafts": [
                {"player_id": "a", "review_status": "approved"},
                {"player_id": "b", "review_status": "rejected"},
                {"player_id": "c", "review_status": "pending_review"},
            ],
        })
        h = read_content_draft_review_states(2026, 5, "s", read_url="https://x.test/read")
        results.append(check("helper: ok=True, reviewed_count passed through from the route (2)",
                             h["ok"] is True and h["reviewed_count"] == 2 and len(h["rows"]) == 3))

        lovable_forward.requests.post = lambda url, **kw: _FakeResp(200, {
            "ok": True, "content_drafts": [
                {"player_id": "a", "review_status": "approved"},
                {"player_id": "b", "review_status": "pending_review"},
            ],
        })
        h = read_content_draft_review_states(2026, 5, "s", read_url="https://x.test/read")
        results.append(check("helper: reviewed_count computed locally when the route omits it (1)",
                             h["ok"] is True and h["reviewed_count"] == 1))

        lovable_forward.requests.post = lambda url, **kw: _FakeResp(200, {"ok": True, "content_drafts": [], "reviewed_count": 0})
        h = read_content_draft_review_states(2026, 5, "s", read_url="https://x.test/read")
        results.append(check("helper: a week with zero rows is a valid ok=True result (reviewed_count 0)",
                             h["ok"] is True and h["reviewed_count"] == 0))

        lovable_forward.requests.post = lambda url, **kw: _FakeResp(500, {"ok": False, "error": "boom"})
        h = read_content_draft_review_states(2026, 5, "s", read_url="https://x.test/read")
        results.append(check("helper: a transport/query failure -> ok=False", h["ok"] is False))

        lovable_forward.requests.post = lambda url, **kw: _FakeResp(200, "<html>not json</html>")
        h = read_content_draft_review_states(2026, 5, "s", read_url="https://x.test/read")
        results.append(check("helper: a non-JSON body -> ok=False", h["ok"] is False))
    finally:
        lovable_forward.requests.post = orig_post

    # ------------------------------------------------------------------
    # endpoint pre-flight — curate_nfl_shelves / stub read / write all
    # monkeypatched; the point is purely the guard's control flow.
    # ------------------------------------------------------------------
    calls = {}

    def fake_curate(weekly, season, week, **kw):
        calls["curate"] = calls.get("curate", 0) + 1
        return {"content_draft_rows": [], "shelf_signal_history_rows": []}

    def fake_write(rows, secret, write_url=None):
        calls["write"] = calls.get("write", 0) + 1
        return {"success": True, "status_code": 200, "error": None, "response_body": "{}"}

    orig_curate = idx.curate_nfl_shelves
    orig_write = idx.write_content_draft_rows
    orig_snap = idx.stub_week_snapshot
    orig_pre = idx.read_content_draft_review_states
    orig_sched = idx.nfl.import_schedules
    orig_pbp = idx.nfl.import_pbp_data
    idx.curate_nfl_shelves = fake_curate
    idx.write_content_draft_rows = fake_write
    idx.stub_week_snapshot = lambda s, w, secret: _toy_stub_frame()
    idx.nfl.import_schedules = lambda seasons: None
    idx.nfl.import_pbp_data = lambda *a, **k: None
    try:
        # 1. no existing rows -> proceeds
        calls.clear()
        idx.read_content_draft_review_states = lambda s, w, secret: {"ok": True, "reviewed_count": 0, "rows": [], "error": None, "status_code": 200}
        r = client.post("/api/curate-and-write-drafts", headers=AUTH, json={"season": 2026, "week": 5, "preview_only": True})
        results.append(check("endpoint: no existing rows -> 200, curation runs",
                             r.status_code == 200 and calls.get("curate") == 1))

        # 2. all still pending_review -> proceeds
        calls.clear()
        idx.read_content_draft_review_states = lambda s, w, secret: {"ok": True, "reviewed_count": 0, "rows": [{"review_status": "pending_review"}], "error": None, "status_code": 200}
        r = client.post("/api/curate-and-write-drafts", headers=AUTH, json={"season": 2026, "week": 5, "preview_only": True})
        results.append(check("endpoint: all rows still pending_review -> 200, curation runs (re-curate before review is allowed)",
                             r.status_code == 200 and calls.get("curate") == 1))

        # 3. >=1 reviewed -> 409, no curation, no write
        calls.clear()
        idx.read_content_draft_review_states = lambda s, w, secret: {"ok": True, "reviewed_count": 2, "rows": [], "error": None, "status_code": 200}
        r = client.post("/api/curate-and-write-drafts", headers=AUTH, json={"season": 2026, "week": 5})
        body = r.get_json()
        results.append(check(
            "endpoint: >=1 reviewed row -> 409 status=locked, reviewed_rows echoed, hint present",
            r.status_code == 409 and body["status"] == "locked" and body["reviewed_rows"] == 2 and "force" in body["hint"],
        ))
        results.append(check("endpoint: 409 short-circuits BEFORE curate_nfl_shelves (LLM never invoked)", "curate" not in calls))
        results.append(check("endpoint: 409 writes nothing", "write" not in calls))

        # 3b. guard fires for preview_only too
        calls.clear()
        r = client.post("/api/curate-and-write-drafts", headers=AUTH, json={"season": 2026, "week": 5, "preview_only": True})
        results.append(check("endpoint: guard fires even for preview_only (409, no curation)",
                             r.status_code == 409 and "curate" not in calls))

        # 4. same scenario + force:true -> proceeds, pre-flight skipped entirely
        calls.clear()
        pre_called = {"n": 0}
        def spy_pre(s, w, secret):
            pre_called["n"] += 1
            return {"ok": True, "reviewed_count": 99, "rows": [], "error": None, "status_code": 200}
        idx.read_content_draft_review_states = spy_pre
        r = client.post("/api/curate-and-write-drafts", headers=AUTH, json={"season": 2026, "week": 5, "force": True, "preview_only": True})
        results.append(check("endpoint: force:true -> 200, curation runs, pre-flight not even called",
                             r.status_code == 200 and calls.get("curate") == 1 and pre_called["n"] == 0))

        # 5. pre-flight read failure -> 502 preflight_failed, no curation
        calls.clear()
        idx.read_content_draft_review_states = lambda s, w, secret: {"ok": False, "reviewed_count": 0, "rows": [], "error": "route down", "status_code": 503}
        r = client.post("/api/curate-and-write-drafts", headers=AUTH, json={"season": 2026, "week": 5})
        results.append(check("endpoint: pre-flight read failure fails closed -> 502 status=preflight_failed, no curation",
                             r.status_code == 502 and r.get_json()["status"] == "preflight_failed" and "curate" not in calls))

        # 5b. ...but force:true still bypasses even a broken pre-flight
        calls.clear()
        r = client.post("/api/curate-and-write-drafts", headers=AUTH, json={"season": 2026, "week": 5, "force": True, "preview_only": True})
        results.append(check("endpoint: force:true proceeds even when the pre-flight route is down",
                             r.status_code == 200 and calls.get("curate") == 1))
    finally:
        idx.curate_nfl_shelves = orig_curate
        idx.write_content_draft_rows = orig_write
        idx.stub_week_snapshot = orig_snap
        idx.read_content_draft_review_states = orig_pre
        idx.nfl.import_schedules = orig_sched
        idx.nfl.import_pbp_data = orig_pbp

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
