"""
Tests the nfl_player_redzone_weekly read pagination and its failure path.

The defect being closed: read_player_redzone_weekly_rows issued ONE
unpaginated signed POST against a route that was an unordered
season-scoped select, so Supabase's default db-max-rows cap (1,000) cut
the response off silently with no way to reach the rest. Because an
unordered scan of an upsert-append table comes back oldest-first, the
slice lost was always the NEWEST week.

Measured live on 2026-11-06 for season 2026: exactly 1,000 rows came
back, weeks 1-7 filling 999 of them and week 8 contributing a single
row. build_role_changes_stories() for week 8 therefore saw one player
and correctly produced zero stories -- which read as "the outage ate
week 8" rather than as a truncated read. A real backfill call that day
(HTTP 200, stories_generated 0) wrote nothing for exactly this reason.

Compounding it, role_defensive_weekly_snapshot took result["rows"]
without checking result["ok"], so a hard read failure produced the same
empty frame as a genuinely unreconciled season.

Focus here: the read pages to completion, a failure or a non-advancing
cursor on ANY page fails the whole call rather than returning a partial
season, and the snapshot raises instead of silently returning empty.

Run: python3 nfl/test_redzone_weekly_pagination.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "api"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))

import requests

from reconcile_week import (
    RedzoneWeeklyReadError,
    read_player_redzone_weekly_rows,
    role_defensive_weekly_snapshot,
)


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


class _FakeResp:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.text = body if isinstance(body, str) else json.dumps(body)
        self.content = self.text.encode()

    def json(self):
        return json.loads(self.text)


def _row(week, i):
    """One redzone-weekly row in the route's own response shape."""
    return {
        "id": f"{week:02d}{i:06d}-0000-4000-8000-000000000000",
        "player_id": f"00-00{week:02d}{i:03d}",
        "season": 2026,
        "week": week,
        "extra": {"player_name": f"W{week}P{i}"},
    }


class _PagingServer:
    """
    Serves a fixed row list through the route's (week DESC, id DESC)
    keyset contract, so the test exercises the REAL cursor protocol
    rather than a counter. Records every payload it was sent.
    """

    def __init__(self, rows, page_size, fail_on_page=None, fail_status=500, freeze_cursor=False):
        # Route order: week DESC, id DESC.
        self.rows = sorted(rows, key=lambda r: (r["week"], r["id"]), reverse=True)
        self.page_size = page_size
        self.fail_on_page = fail_on_page
        self.fail_status = fail_status
        self.freeze_cursor = freeze_cursor
        self.payloads = []

    def __call__(self, url, **kw):
        payload = json.loads(kw["data"].decode())
        self.payloads.append(payload)
        page_no = len(self.payloads)
        if self.fail_on_page == page_no:
            return _FakeResp(self.fail_status, {"ok": False, "error": "Query failed", "code": "57014"})

        start = 0
        bw, bid = payload.get("before_week"), payload.get("before_id")
        if bw is not None and bid is not None:
            for i, r in enumerate(self.rows):
                if (r["week"], r["id"]) < (bw, bid):
                    start = i
                    break
            else:
                start = len(self.rows)
        page = self.rows[start:start + self.page_size]
        if self.freeze_cursor and page_no > 1:
            # Pathological route: keeps returning the same full page, so
            # the cursor never advances.
            page = self.rows[:self.page_size]
        return _FakeResp(200, {"ok": True, "season": 2026, "row_count": len(page), "player_redzone_weekly": page})


def _with_transport(impl, fn):
    import lovable_forward

    orig = lovable_forward.requests.post
    try:
        lovable_forward.requests.post = impl
        return fn()
    finally:
        lovable_forward.requests.post = orig


def main():
    results = []
    PAGE = 50
    # 7 full weeks + a newest week -- the real 2026 shape in miniature.
    rows = [_row(w, i) for w in range(1, 8) for i in range(20)] + [_row(8, i) for i in range(15)]
    assert len(rows) == 155

    # --------------------------------------------------------------
    # 1. Pages to completion
    # --------------------------------------------------------------
    srv = _PagingServer(rows, PAGE)
    res = _with_transport(srv, lambda: read_player_redzone_weekly_rows(
        2026, "s", read_url="https://x.test"))
    results.append(check(
        "pages to completion: every row returned, not just the first page",
        res["ok"] and len(res["rows"]) == 155,
    ))
    results.append(check(
        "pages to completion: paged until an EMPTY page, with no knowledge of the server's cap",
        len(srv.payloads) == 5,  # 50 + 50 + 50 + 5 + 0
    ))
    results.append(check(
        "first request sends NO cursor (backward compatible with the old call)",
        "before_week" not in srv.payloads[0] and "before_id" not in srv.payloads[0]
        and srv.payloads[0] == {"season": 2026},
    ))
    results.append(check(
        "subsequent requests send both cursor fields together",
        all("before_week" in p and "before_id" in p for p in srv.payloads[1:]),
    ))
    results.append(check(
        "no row is duplicated or skipped across pages",
        len({r["id"] for r in res["rows"]}) == 155,
    ))
    results.append(check(
        "THE REGRESSION: the newest week survives (all 15 week-8 rows present, not 1)",
        sum(1 for r in res["rows"] if r["week"] == 8) == 15,
    ))

    # --------------------------------------------------------------
    # 2. The exact pre-fix failure, reproduced: one page only
    # --------------------------------------------------------------
    one_page = _PagingServer(rows, PAGE)("u", data=json.dumps({"season": 2026}).encode()).json()
    page_weeks = {r["week"] for r in one_page["player_redzone_weekly"]}
    results.append(check(
        "a SINGLE un-paged request (what the old code did) is still capped, but now holds the "
        "NEWEST weeks instead of the oldest — the target week survives even without paging",
        len(one_page["player_redzone_weekly"]) == PAGE
        and 8 in page_weeks and 1 not in page_weeks
        and sum(1 for r in one_page["player_redzone_weekly"] if r["week"] == 8) == 15,
    ))

    # --------------------------------------------------------------
    # 3. Failure on a later page fails the WHOLE call
    # --------------------------------------------------------------
    srv_fail = _PagingServer(rows, PAGE, fail_on_page=3)
    res_fail = _with_transport(srv_fail, lambda: read_player_redzone_weekly_rows(
        2026, "s", read_url="https://x.test"))
    results.append(check(
        "a 500 on page 3 fails the whole call -- a partial season is never passed off as complete",
        res_fail["ok"] is False and res_fail["rows"] == [],
    ))
    results.append(check(
        "the failure carries the route's status code",
        res_fail["status_code"] == 500,
    ))

    def _boom(url, **kw):
        raise requests.exceptions.ReadTimeout("read timeout=10")

    res_to = _with_transport(_boom, lambda: read_player_redzone_weekly_rows(
        2026, "s", read_url="https://x.test"))
    results.append(check("a client ReadTimeout fails the call", res_to["ok"] is False))

    # --------------------------------------------------------------
    # 4. Pathological cursor / runaway protection
    # --------------------------------------------------------------
    srv_frozen = _PagingServer(rows, PAGE, freeze_cursor=True)
    res_frozen = _with_transport(srv_frozen, lambda: read_player_redzone_weekly_rows(
        2026, "s", read_url="https://x.test"))
    results.append(check(
        "a cursor that fails to advance fails the call, names the stuck row, and stops "
        "after a bounded number of requests instead of looping",
        res_frozen["ok"] is False
        and res_frozen["rows"] == []
        and "cannot page past" in (res_frozen["error"] or "")
        and len(srv_frozen.payloads) <= 3,
    ))

    srv_many = _PagingServer(rows, 10)
    res_many = _with_transport(srv_many, lambda: read_player_redzone_weekly_rows(
        2026, "s", read_url="https://x.test", max_pages=3))
    results.append(check(
        "exceeding max_pages fails rather than returning a partial season",
        res_many["ok"] is False and res_many["rows"] == [] and "max_pages" in (res_many["error"] or ""),
    ))

    # --------------------------------------------------------------
    # 5. A genuinely empty season is still a valid, non-raising outcome
    # --------------------------------------------------------------
    empty = lambda url, **kw: _FakeResp(200, {"ok": True, "season": 2026, "row_count": 0, "player_redzone_weekly": []})
    res_empty = _with_transport(empty, lambda: read_player_redzone_weekly_rows(
        2026, "s", read_url="https://x.test"))
    results.append(check(
        "a genuinely empty season is ok=True with zero rows, not an error",
        res_empty["ok"] is True and res_empty["rows"] == [],
    ))

    # --------------------------------------------------------------
    # 6. role_defensive_weekly_snapshot: raise vs. honest empty
    # --------------------------------------------------------------
    def _snapshot(impl):
        try:
            return _with_transport(impl, lambda: role_defensive_weekly_snapshot(
                2026, "s", read_url="https://x.test")), None
        except RedzoneWeeklyReadError as e:
            return None, e

    frame, exc = _snapshot(lambda url, **kw: _FakeResp(500, {"ok": False, "error": "Query failed", "code": "57014"}))
    results.append(check(
        "snapshot RAISES RedzoneWeeklyReadError on a failed read, instead of an empty frame "
        "that reads as 'nothing reconciled yet'",
        exc is not None,
    ))
    results.append(check(
        "the raised error names the season and the status code",
        exc is not None and "season=2026" in str(exc) and "500" in str(exc),
    ))

    frame_empty, exc_empty = _snapshot(empty)
    results.append(check(
        "snapshot does NOT raise for a genuinely empty season -- still a zero-row frame",
        exc_empty is None and frame_empty is not None and len(frame_empty) == 0,
    ))

    srv_ok = _PagingServer(rows, PAGE)
    frame_ok, exc_ok = _snapshot(srv_ok)
    results.append(check(
        "snapshot success path unchanged: all 155 rows, with `extra` unpacked back onto each row",
        exc_ok is None and frame_ok is not None and len(frame_ok) == 155
        and "player_name" in frame_ok.columns,
    ))
    results.append(check(
        "snapshot carries the newest week through (15 week-8 rows)",
        exc_ok is None and int((frame_ok["week"] == 8).sum()) == 15,
    ))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
