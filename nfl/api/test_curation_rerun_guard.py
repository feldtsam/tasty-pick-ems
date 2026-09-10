"""
Tests the Phase C re-run guard on /api/curate-and-write-drafts:
the endpoint pre-flight (409 status=locked when review has started, no
curation run, LLM never invoked) and its force override, plus the
read_content_draft_review_states() helper in isolation.

Also tests this endpoint's half of the force_review_reset fix (real
behavioral bug, confirmed via a real production run: a forced re-run's
regenerated content used to silently inherit an already-approved row's
review_status) -- specifically, that force_review_reset=True is set on
every row this endpoint hands to write_content_draft_rows() when (and
only when) force is true.

The DB-level backstop (the protect_nfl_content_draft_review_status
trigger, including the force_review_reset exception) is verified
separately by supabase/tests/nfl_content_drafts_review_guard.test.sql
against a real Postgres — it can't be exercised from Python.

ALSO tests the multi-run-duplication fix built on top of this same
force:true path (real live incident, confirmed via the published RPC:
attd_500_699/attd_700_plus each carrying two full sets of rank-1-
through-6 approved rows from two separate runs) -- compute_stale_
approved_targets() in isolation (pure function), supersede_stale_
approved_rows() in isolation (network-mocked, same pattern as read_
content_draft_review_states() above), and the endpoint's own gating:
supersession only ever runs on force:true + a successful new-row write
+ an UNSCOPED write (no player_ids_to_write/max_rows_to_write).

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
from curate_home_shelves import (
    compute_stale_approved_targets,
    read_content_draft_review_states,
    supersede_stale_approved_rows,
)

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
    # compute_stale_approved_targets() — pure function, no I/O
    # ------------------------------------------------------------------
    existing_rows = [
        {"player_id": "survivor", "event_id": "e1", "shelf": "attd_500_699", "writer_type": "shelf_card", "review_status": "approved"},
        {"player_id": "stale_a", "event_id": "e2", "shelf": "attd_500_699", "writer_type": "shelf_card", "review_status": "approved"},
        {"player_id": "still_pending", "event_id": "e3", "shelf": "attd_500_699", "writer_type": "shelf_card", "review_status": "pending_review"},
        {"player_id": "already_rejected", "event_id": "e4", "shelf": "attd_500_699", "writer_type": "shelf_card", "review_status": "rejected"},
        {"player_id": "tasty_six_row", "event_id": "e5", "shelf": "attd_500_699", "writer_type": "tasty_six", "review_status": "approved"},
        {"player_id": "other_shelf_stale", "event_id": "e6", "shelf": "attd_700_plus", "writer_type": "shelf_card", "review_status": "approved"},
    ]
    new_rows = [
        {"player_id": "survivor", "shelf": "attd_500_699", "writer_type": "shelf_card"},
        {"player_id": "new_player", "shelf": "attd_500_699", "writer_type": "shelf_card"},
        # attd_700_plus has NO surviving rows this run at all.
    ]
    targets = compute_stale_approved_targets(new_rows, existing_rows)
    target_ids = {t["player_id"] for t in targets}
    results.append(check(
        "compute_stale_approved_targets: a player still surviving this run's cap is NOT flagged stale",
        "survivor" not in target_ids,
    ))
    results.append(check(
        "compute_stale_approved_targets: an approved player who no longer survives IS flagged stale",
        "stale_a" in target_ids,
    ))
    results.append(check(
        "compute_stale_approved_targets: a still-pending_review row is never touched (not approved yet)",
        "still_pending" not in target_ids,
    ))
    results.append(check(
        "compute_stale_approved_targets: an already-rejected row is left alone",
        "already_rejected" not in target_ids,
    ))
    results.append(check(
        "compute_stale_approved_targets: a Tasty Six row is out of scope regardless of status",
        "tasty_six_row" not in target_ids,
    ))
    results.append(check(
        "compute_stale_approved_targets: a shelf with ZERO survivors this run supersedes every prior approved row on it",
        "other_shelf_stale" in target_ids,
    ))
    results.append(check(
        "compute_stale_approved_targets: exactly the 2 real stale rows, nothing extra",
        len(targets) == 2 and target_ids == {"stale_a", "other_shelf_stale"},
    ))
    a_target = next(t for t in targets if t["player_id"] == "stale_a")
    results.append(check(
        "compute_stale_approved_targets: a target carries the real natural key (event_id/shelf/writer_type), not just player_id",
        a_target == {"player_id": "stale_a", "event_id": "e2", "shelf": "attd_500_699", "writer_type": "shelf_card"},
    ))

    # ------------------------------------------------------------------
    # supersede_stale_approved_rows() — network-mocked, same pattern as
    # read_content_draft_review_states() above
    # ------------------------------------------------------------------
    results.append(check(
        "supersede_stale_approved_rows: empty targets is a no-op, no network call made",
        supersede_stale_approved_rows([], 2026, 5, "s", write_url="https://x.test/supersede") == {
            "ok": True, "error": None, "status_code": None, "requested": 0, "superseded": 0,
        },
    ))

    orig_post = lovable_forward.requests.post
    try:
        lovable_forward.requests.post = lambda url, **kw: _FakeResp(200, {"ok": True, "requested": 2, "superseded": 2, "results": []})
        r = supersede_stale_approved_rows(
            [{"player_id": "a", "event_id": "e", "shelf": "attd_500_699", "writer_type": "shelf_card"}],
            2026, 5, "s", write_url="https://x.test/supersede",
        )
        results.append(check("supersede_stale_approved_rows: a real 200 -> ok=True, requested/superseded passed through",
                             r == {"ok": True, "error": None, "status_code": 200, "requested": 2, "superseded": 2}))

        lovable_forward.requests.post = lambda url, **kw: _FakeResp(500, {"ok": False, "error": "boom"})
        r = supersede_stale_approved_rows(
            [{"player_id": "a", "event_id": "e", "shelf": "attd_500_699", "writer_type": "shelf_card"}],
            2026, 5, "s", write_url="https://x.test/supersede",
        )
        results.append(check("supersede_stale_approved_rows: a transport/route failure -> ok=False", r["ok"] is False))

        lovable_forward.requests.post = lambda url, **kw: _FakeResp(200, "<html>not json</html>")
        r = supersede_stale_approved_rows(
            [{"player_id": "a", "event_id": "e", "shelf": "attd_500_699", "writer_type": "shelf_card"}],
            2026, 5, "s", write_url="https://x.test/supersede",
        )
        results.append(check("supersede_stale_approved_rows: a non-JSON body -> ok=False", r["ok"] is False))
    finally:
        lovable_forward.requests.post = orig_post

    # ------------------------------------------------------------------
    # endpoint pre-flight — curate_nfl_shelves / stub read / write all
    # monkeypatched; the point is purely the guard's control flow.
    # ------------------------------------------------------------------
    calls = {}

    def fake_curate(weekly, season, week, **kw):
        calls["curate"] = calls.get("curate", 0) + 1
        # A real, minimal content-ready row (title present, matching the
        # content_ready_rows filter) -- so rows_to_write is non-empty and
        # force_review_reset's real assignment logic actually runs, not
        # just skipped because there was nothing to write.
        return {
            "content_draft_rows": [{
                "player_id": "00-0000001", "title": "Test headline",
                "why_reasons": [], "is_tasty_six": False,
            }],
            "shelf_signal_history_rows": [],
            # Around the League wiring: real curate_nfl_shelves now always
            # returns this key too (see its own docstring) -- empty here
            # since this fake is about the rerun-guard's own control flow,
            # not Around the League content itself.
            "around_the_league_rows": [],
        }

    def fake_write(rows, secret, write_url=None):
        calls["write"] = calls.get("write", 0) + 1
        calls["write_rows"] = rows
        return {"success": True, "status_code": 200, "error": None, "response_body": "{}"}

    orig_curate = idx.curate_nfl_shelves
    orig_write = idx.write_content_draft_rows
    orig_snap = idx.stub_week_snapshot
    orig_pre = idx.read_content_draft_review_states
    orig_compute = idx.compute_stale_approved_targets
    orig_supersede = idx.supersede_stale_approved_rows
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

        # 6. force_review_reset -- real behavioral fix, confirmed via a
        # real production run: a forced re-run's regenerated content was
        # silently inheriting an already-approved row's review_status
        # (root cause: the DB trigger protect_nfl_content_draft_review_
        # status() blocks EVERY reset to pending_review on a reviewed row,
        # with no way to tell an accidental re-run apart from an
        # explicit, human-authorized force:true one). force_review_reset
        # is the new signal that closes that gap on the DB side -- these
        # two checks cover this endpoint's half of it: is the flag
        # actually set on the rows this endpoint writes, only when force
        # is true. (The trigger's own behavior -- that setting the flag
        # actually unblocks the reset, and that a normal write still gets
        # blocked -- is verified separately against a real Postgres by
        # supabase/tests/nfl_content_drafts_review_guard.test.sql, same
        # split as the rest of this file's own docstring already
        # describes for the guard itself.)
        #
        # NOT preview_only this time -- preview_only never reaches
        # write_content_draft_rows at all, so it can't exercise this.
        calls.clear()
        idx.read_content_draft_review_states = lambda s, w, secret: {"ok": True, "reviewed_count": 0, "rows": [], "error": None, "status_code": 200}
        r = client.post("/api/curate-and-write-drafts", headers=AUTH, json={"season": 2026, "week": 5, "force": True})
        written_rows = calls.get("write_rows") or []
        results.append(check(
            "endpoint: force:true -> every row handed to write_content_draft_rows carries force_review_reset=True",
            r.status_code == 200 and len(written_rows) > 0 and all(row.get("force_review_reset") is True for row in written_rows),
        ))

        # 6b. the same real row, written WITHOUT force -> no flag at all,
        # matching today's default upsert payload exactly (never a
        # regression on the normal path).
        calls.clear()
        r = client.post("/api/curate-and-write-drafts", headers=AUTH, json={"season": 2026, "week": 5})
        written_rows = calls.get("write_rows") or []
        results.append(check(
            "endpoint: a normal (non-forced) write never sets force_review_reset on any row",
            r.status_code == 200 and len(written_rows) > 0 and all("force_review_reset" not in row for row in written_rows),
        ))

        # ------------------------------------------------------------------
        # 7. Supersede gating — the multi-run-duplication fix built on this
        # same force:true path. A real properly-shaped curated row this
        # time (shelf/writer_type present) — fake_curate above omits them,
        # which is fine for force_review_reset's own checks but would make
        # every supersede check here vacuously pass (compute_stale_
        # approved_targets silently finds nothing without a real shelf).
        # ------------------------------------------------------------------
        def fake_curate_with_shelf(weekly, season, week, **kw):
            calls["curate"] = calls.get("curate", 0) + 1
            return {
                "content_draft_rows": [{
                    "player_id": "new_player", "shelf": "attd_500_699", "writer_type": "shelf_card",
                    "title": "Test headline", "why_reasons": [], "is_tasty_six": False,
                }],
                "shelf_signal_history_rows": [],
                "around_the_league_rows": [],
            }
        idx.curate_nfl_shelves = fake_curate_with_shelf

        supersede_calls = {}

        def spy_supersede(targets, season, week, secret, write_url=None):
            supersede_calls["n"] = supersede_calls.get("n", 0) + 1
            supersede_calls["targets"] = targets
            return {"ok": True, "error": None, "status_code": 200, "requested": len(targets), "superseded": len(targets)}
        idx.supersede_stale_approved_rows = spy_supersede

        # An existing approved row for a DIFFERENT player on the same
        # shelf — "new_player" (this run's own survivor) is not it, so
        # this should come back as exactly one real stale target.
        stale_existing_rows = {
            "ok": True, "reviewed_count": 1, "error": None, "status_code": 200,
            "rows": [{"player_id": "old_player", "event_id": "e1", "shelf": "attd_500_699",
                      "writer_type": "shelf_card", "review_status": "approved"}],
        }

        # 7a. force:true, real write success, unscoped -> supersede runs
        # and is called with exactly the real stale target.
        calls.clear()
        supersede_calls.clear()
        idx.read_content_draft_review_states = lambda s, w, secret: dict(stale_existing_rows)
        r = client.post("/api/curate-and-write-drafts", headers=AUTH, json={"season": 2026, "week": 5, "force": True})
        body = r.get_json()
        results.append(check(
            "endpoint 7a: force:true unscoped run -> supersede_stale_approved_rows called once with the real stale target",
            r.status_code == 200 and supersede_calls.get("n") == 1
            and supersede_calls["targets"] == [{"player_id": "old_player", "event_id": "e1", "shelf": "attd_500_699", "writer_type": "shelf_card"}],
        ))
        results.append(check(
            "endpoint 7a: response echoes the supersede result (requested/superseded)",
            body["supersede_stale_approved"] == {"ok": True, "error": None, "status_code": 200, "requested": 1, "superseded": 1},
        ))

        # 7b. force:true but the new-row write itself FAILED -> supersede
        # must NOT run (superseding first and having the write fail would
        # leave the shelf with fewer rows than before, not a fix).
        calls.clear()
        supersede_calls.clear()
        idx.write_content_draft_rows = lambda rows, secret, write_url=None: {"success": False, "status_code": 500, "error": "boom", "response_body": None}
        r = client.post("/api/curate-and-write-drafts", headers=AUTH, json={"season": 2026, "week": 5, "force": True})
        results.append(check(
            "endpoint 7b: force:true but the new-row write failed -> supersede never called",
            "n" not in supersede_calls,
        ))
        idx.write_content_draft_rows = fake_write  # restore the success stub for the remaining cases

        # 7c. force:true + player_ids_to_write set -> supersede never runs
        # (compute_stale_approved_targets' own caller contract requires
        # the FULL curated set; a scoped test write would wrongly look
        # like every non-included player fell off the shelf).
        calls.clear()
        supersede_calls.clear()
        r = client.post("/api/curate-and-write-drafts", headers=AUTH,
                        json={"season": 2026, "week": 5, "force": True, "player_ids_to_write": ["new_player"]})
        results.append(check(
            "endpoint 7c: force:true + player_ids_to_write -> supersede never called (scoped write)",
            "n" not in supersede_calls,
        ))

        # 7d. force:true + max_rows_to_write set -> same reasoning as 7c.
        calls.clear()
        supersede_calls.clear()
        r = client.post("/api/curate-and-write-drafts", headers=AUTH,
                        json={"season": 2026, "week": 5, "force": True, "max_rows_to_write": 1})
        results.append(check(
            "endpoint 7d: force:true + max_rows_to_write -> supersede never called (scoped write)",
            "n" not in supersede_calls,
        ))

        # 7e. NOT force -> supersede never runs, even with 0 reviewed rows
        # (so the normal path proceeds past the 409 guard at all).
        calls.clear()
        supersede_calls.clear()
        idx.read_content_draft_review_states = lambda s, w, secret: {"ok": True, "reviewed_count": 0, "rows": [], "error": None, "status_code": 200}
        r = client.post("/api/curate-and-write-drafts", headers=AUTH, json={"season": 2026, "week": 5})
        results.append(check(
            "endpoint 7e: a normal (non-forced) run never calls supersede_stale_approved_rows",
            r.status_code == 200 and "n" not in supersede_calls,
        ))

        # 7f. force:true + preview_only -> supersede never runs (nothing
        # was actually written for anything to be superseded against).
        calls.clear()
        supersede_calls.clear()
        idx.read_content_draft_review_states = lambda s, w, secret: dict(stale_existing_rows)
        r = client.post("/api/curate-and-write-drafts", headers=AUTH, json={"season": 2026, "week": 5, "force": True, "preview_only": True})
        results.append(check(
            "endpoint 7f: force:true + preview_only -> supersede never called",
            r.status_code == 200 and "n" not in supersede_calls,
        ))

    finally:
        idx.curate_nfl_shelves = orig_curate
        idx.write_content_draft_rows = orig_write
        idx.stub_week_snapshot = orig_snap
        idx.read_content_draft_review_states = orig_pre
        idx.compute_stale_approved_targets = orig_compute
        idx.supersede_stale_approved_rows = orig_supersede
        idx.nfl.import_schedules = orig_sched
        idx.nfl.import_pbp_data = orig_pbp

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
