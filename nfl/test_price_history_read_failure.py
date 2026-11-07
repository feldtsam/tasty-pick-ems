"""
Tests the nfl_price_history read-failure path for
/api/curate-and-write-drafts.

The defect being closed: market_value_snapshot_for_curation handed
read_price_history()'s result["rows"] straight on without ever checking
result["ok"], so a non-2xx response, a network error or an unparseable
body all produced rows=[] -- byte-identical to a genuine "no polls yet
this week." An empty snapshot zeroes _attd_eligible_overall (it gates on
a non-null consensus_price_american), so the three ATTD shelves came
back empty with an HTTP 200 and nothing anywhere said the read had
failed.

Confirmed live on 2026-11-06: the read route returned Postgres 57014
("canceling statement due to statement timeout") on 2 of 6 consecutive
reads of (2026, 9) -- a week holding 23,000+ real poll rows -- which is
what produced price_history_rows=0 / rows_curated=0 for the ATTD call.

Focus here: a failed read RAISES PriceHistoryReadError, a genuinely
empty week still does NOT, and the success path is unchanged.

Note on coverage: the endpoint's own 503 branch is NOT exercised here.
nfl/api/index.py is not importable in this environment (nfl_data_py
pulls `appdirs`, which is absent), so the attd_in_scope predicate is
re-stated below and checked against the REAL curate_home_shelves
constants and the REAL slug lists the two live Make.com modules send.
That covers the seam that would actually break -- a slug/Title-Case
mismatch -- but not the Flask response itself.

Run: python3 nfl/test_price_history_read_failure.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "api"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))

import requests

from curate_home_shelves import ODDS_SHELVES, TREND_SHELVES, _SLUG_TO_SHELF
from market_value import (
    CURATION_MARKET_VALUE_COLUMNS,
    PriceHistoryReadError,
    market_value_snapshot_for_curation,
)


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


class _FakeResp:
    """Matches what lovable_forward.forward_to_lovable reads off a response."""

    def __init__(self, status_code, body):
        self.status_code = status_code
        self.text = body if isinstance(body, str) else json.dumps(body)
        self.content = self.text.encode()

    def json(self):
        return json.loads(self.text)


# The real body the route returns on a statement timeout, copied from a
# live 2026-11-06 response, not invented.
_57014 = {
    "ok": False,
    "error": "Query failed",
    "details": "canceling statement due to statement timeout",
    "code": "57014",
}

_REAL_ROW = {
    "player_id": "TEST-000001", "season": None, "week": None,
    "poll_timestamp": "2099-09-01T12:00:00Z", "event_id": "e1",
    "commence_time": "2099-09-07T17:00:00Z", "home_team": "TS01", "away_team": "TS02",
    "player_name_raw": "Test", "team": "TS01", "position_group": "WR", "matched": True,
    "match_issue_type": None, "n_books": 3, "best_price": 350, "best_book": "B",
    "consensus_implied_probability": 0.32, "consensus_price_american": 320,
}


def _raises_read_error(post_impl):
    """Run the snapshot with a patched transport; return (raised?, exc)."""
    import lovable_forward

    orig = lovable_forward.requests.post
    try:
        lovable_forward.requests.post = post_impl
        try:
            market_value_snapshot_for_curation(2099, 1, "s", read_url="https://x.test")
            return False, None
        except PriceHistoryReadError as e:
            return True, e
    finally:
        lovable_forward.requests.post = orig


def main():
    results = []
    import lovable_forward

    # --------------------------------------------------------------
    # 1. The live failure: HTTP 500 / Postgres 57014
    # --------------------------------------------------------------
    raised, exc = _raises_read_error(lambda url, **kw: _FakeResp(500, _57014))
    results.append(check(
        "57014 statement timeout (HTTP 500) raises PriceHistoryReadError, "
        "NOT a silent empty frame",
        raised,
    ))
    results.append(check(
        "the raised error names season, week and the status code, so the log line is actionable",
        raised and "season=2099" in str(exc) and "week=1" in str(exc) and "500" in str(exc),
    ))

    # --------------------------------------------------------------
    # 2. Other real failure shapes must behave identically
    # --------------------------------------------------------------
    raised_401, _ = _raises_read_error(lambda url, **kw: _FakeResp(401, {"ok": False, "error": "bad signature"}))
    results.append(check("HTTP 401 (bad signature) raises", raised_401))

    raised_html, _ = _raises_read_error(lambda url, **kw: _FakeResp(502, "<html>Bad Gateway</html>"))
    results.append(check("non-JSON body (502 HTML) raises", raised_html))

    def _boom(url, **kw):
        raise requests.exceptions.ReadTimeout("read timeout=10")

    raised_timeout, exc_t = _raises_read_error(_boom)
    results.append(check(
        "client-side ReadTimeout (the 10s REQUEST_TIMEOUT_SECONDS ceiling) raises",
        raised_timeout,
    ))

    # --------------------------------------------------------------
    # 3. A GENUINELY empty week must still NOT raise — this is the
    #    normal state until Wed/Thu and must keep degrading quietly.
    # --------------------------------------------------------------
    orig = lovable_forward.requests.post
    try:
        lovable_forward.requests.post = lambda url, **kw: _FakeResp(200, {"ok": True, "price_history": []})
        snap = market_value_snapshot_for_curation(2099, 1, "s", read_url="https://x.test")
        results.append(check(
            "ok=true with zero rows (honest 'no polls yet') still returns a zero-row frame and does NOT raise",
            len(snap) == 0
            and list(snap.columns) == ["player_id", "season", "week"] + CURATION_MARKET_VALUE_COLUMNS,
        ))

        # ----------------------------------------------------------
        # 4. Success path unchanged (regression guard)
        # ----------------------------------------------------------
        lovable_forward.requests.post = lambda url, **kw: _FakeResp(200, {"ok": True, "price_history": [_REAL_ROW]})
        snap2 = market_value_snapshot_for_curation(2099, 1, "s", read_url="https://x.test")
        results.append(check(
            "success path unchanged: one real poll row -> one scored row carrying consensus_price_american",
            len(snap2) == 1
            and snap2.iloc[0]["consensus_price_american"] == 320
            and "market_value_score" in snap2.columns,
        ))
        results.append(check(
            "success path unchanged: season/week are stamped from the arguments",
            len(snap2) == 1
            and int(snap2.iloc[0]["season"]) == 2099
            and int(snap2.iloc[0]["week"]) == 1,
        ))
    finally:
        lovable_forward.requests.post = orig

    # --------------------------------------------------------------
    # 5. The endpoint's scope predicate, against the REAL constants and
    #    the REAL slug lists the two live Make.com modules send.
    #    Mirrors nfl/api/index.py's attd_in_scope (see module docstring
    #    for why it is re-stated rather than imported).
    # --------------------------------------------------------------
    def attd_in_scope(shelves_to_process):
        return shelves_to_process is None or any(s in ODDS_SHELVES for s in shelves_to_process)

    attd_slugs = ["attd_300_499", "attd_500_699", "attd_700_plus"]
    trend_slugs = ["red_zone_trends", "rb_trends", "wr_trends", "te_trends"]

    results.append(check(
        "every ATTD slug the live Make module sends is a known slug (no silent typo)",
        all(s in _SLUG_TO_SHELF for s in attd_slugs),
    ))
    results.append(check(
        "every trend slug the live Make module sends is a known slug",
        all(s in _SLUG_TO_SHELF for s in trend_slugs),
    ))
    results.append(check(
        "the live ATTD call (module 5) IS in scope -> a failed read hard-fails it",
        attd_in_scope([_SLUG_TO_SHELF[s] for s in attd_slugs]),
    ))
    results.append(check(
        "the live trend call (module 10) is NOT in scope -> it still degrades to 3 pillars",
        not attd_in_scope([_SLUG_TO_SHELF[s] for s in trend_slugs]),
    ))
    results.append(check(
        "an unscoped call (shelves_to_process=None, every shelf) IS in scope",
        attd_in_scope(None),
    ))
    results.append(check(
        "a mixed call is in scope as soon as one ATTD band is present",
        attd_in_scope([_SLUG_TO_SHELF["wr_trends"], _SLUG_TO_SHELF["attd_500_699"]]),
    ))
    results.append(check(
        "ODDS_SHELVES and TREND_SHELVES are disjoint, so the predicate can never be ambiguous",
        not (set(ODDS_SHELVES) & set(TREND_SHELVES)),
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
