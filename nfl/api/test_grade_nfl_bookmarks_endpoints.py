"""
Route-level tests for /api/grade-nfl-bookmarks-live and
/api/grade-nfl-bookmarks-correction (index.py), in the style of
test_phase_d_prerequisites.py / test_curation_rerun_guard.py.

REGRESSION COVERAGE FOR A REAL, CONFIRMED PRODUCTION BUG: both routes'
`except requests.exceptions.RequestException` clauses referenced
`requests` without index.py ever importing it at module level. Every
prior test of these two routes (test_nfl_grade_bookmarks_live.py /
test_nfl_grade_bookmarks_correction.py) exercises the orchestration
modules directly, and every prior route-level check here (via the Flask
test client) only ever reached the auth/validation short-circuits, which
return BEFORE the try/except -- so nothing in this repo's test suite
ever actually executed that except clause, and the NameError went live.
Confirmed for real via Make.com's own "Run once" test against the
deployed endpoint (see Vercel function logs: `NameError: name 'requests'
is not defined` at index.py's `except requests.exceptions.RequestException`
line) before this file/fix existed. These tests force the read call to
actually raise, so the except clause's own `requests.exceptions.
RequestException` reference is genuinely evaluated -- the one thing no
earlier test did.

Run: python3 nfl/api/test_grade_nfl_bookmarks_endpoints.py
"""
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vendor"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

os.environ.setdefault("PIPELINE_INCOMING_SECRET", "test-incoming")
os.environ.setdefault("PIPELINE_WEBHOOK_SECRET", "test-outbound")

import requests

import api.index as idx

AUTH = {"X-Pipeline-Secret": "test-incoming"}


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


if __name__ == "__main__":
    results = []
    client = idx.app.test_client()

    # ------------------------------------------------------------------
    # THE REAL BUG: a genuine RequestException raised by the read call
    # must be caught and turned into a clean 502 JSON response, never an
    # uncaught NameError / generic Werkzeug 500 page.
    with patch(
        "nfl_grade_bookmarks_live.requests.post",
        side_effect=requests.exceptions.HTTPError("401 Client Error: Unauthorized"),
    ):
        r = client.post("/api/grade-nfl-bookmarks-live", json={"season": 2026}, headers=AUTH)
    results.append(check(
        "live route: a real HTTPError from the read call is caught, not an uncaught NameError",
        r.status_code == 502 and r.get_json().get("error") == "Network error reaching ESPN or the read endpoint.",
    ))

    with patch(
        "nfl_grade_bookmarks_correction.requests.post",
        side_effect=requests.exceptions.HTTPError("401 Client Error: Unauthorized"),
    ):
        r = client.post("/api/grade-nfl-bookmarks-correction", json={"season": 2026}, headers=AUTH)
    results.append(check(
        "correction route: a real HTTPError from the read call is caught, not an uncaught NameError",
        r.status_code == 502 and r.get_json().get("error") == "Network error reaching nflverse or the read endpoint.",
    ))

    # A connection-level failure (no HTTP response at all) is a different
    # RequestException subclass -- same guard, worth its own check.
    with patch(
        "nfl_grade_bookmarks_live.requests.post",
        side_effect=requests.exceptions.ConnectionError("connection refused"),
    ):
        r = client.post("/api/grade-nfl-bookmarks-live", json={"season": 2026}, headers=AUTH)
    results.append(check(
        "live route: a connection-level failure is also caught cleanly",
        r.status_code == 502,
    ))

    # ------------------------------------------------------------------
    # Auth / validation short-circuits (unchanged behavior, confirmed
    # still correct after the import fix).
    r = client.post("/api/grade-nfl-bookmarks-live", json={"season": 2026})
    results.append(check("live route: no auth header -> 401", r.status_code == 401))

    r = client.post("/api/grade-nfl-bookmarks-live", json={}, headers=AUTH)
    results.append(check("live route: missing season -> 400", r.status_code == 400))

    r = client.post("/api/grade-nfl-bookmarks-correction", json={"season": "not-an-int"}, headers=AUTH)
    results.append(check("correction route: non-integer season -> 400", r.status_code == 400))

    r = client.post("/api/grade-nfl-bookmarks-correction", json={"season": 2026, "lookback_days": "x"}, headers=AUTH)
    results.append(check("correction route: non-integer lookback_days -> 400", r.status_code == 400))

    r = client.get("/api/grade-nfl-bookmarks-live")
    results.append(check("live route: GET health check -> 200", r.status_code == 200))

    r = client.get("/api/grade-nfl-bookmarks-correction")
    results.append(check("correction route: GET health check -> 200", r.status_code == 200))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
