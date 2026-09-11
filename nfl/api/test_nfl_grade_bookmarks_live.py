"""
Tests for nfl_grade_bookmarks_live.py's read -> grade -> write chain.

Run: python3 nfl/api/test_nfl_grade_bookmarks_live.py
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

from nfl_grade_bookmarks_live import grade_nfl_bookmarks_for_pending


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


def _mock_response(json_body, status_code=200):
    resp = MagicMock()
    resp.json.return_value = json_body
    resp.status_code = status_code
    resp.raise_for_status.return_value = None
    return resp


if __name__ == "__main__":
    results = []

    # ------------------------------------------------------------------
    read_body = {
        "ok": True,
        "sport": "NFL",
        "bookmarks_needing_grading": [
            {"id": "bk-1", "player_key": "p1", "event_key": "e1", "player": "Player One", "team": "NE"},
            {"id": "bk-2", "player_key": "p2", "event_key": "e1", "player": "Player Two", "team": "SEA"},
        ],
    }
    with patch("nfl_grade_bookmarks_live.requests.post", return_value=_mock_response(read_body)) as mock_post, \
         patch("nfl_grade_bookmarks_live.load_schedules", return_value=pd.DataFrame()) as mock_schedules, \
         patch(
             "nfl_grade_bookmarks_live.grade_nfl_bookmarks_live",
             return_value={
                 "results": [
                     {"id": "bk-1", "player_key": "p1", "event_key": "e1", "status": "won", "touchdowns": 1, "reason": "r", "espn_event_id": 1},
                     {"id": "bk-2", "player_key": "p2", "event_key": "e1", "status": "pending", "touchdowns": None, "reason": "r2", "espn_event_id": 1},
                 ],
                 "errors": [],
                 "unique_games_graded": 2,
                 "picks_graded": 2,
             },
         ), \
         patch("nfl_grade_bookmarks_live.forward_to_lovable", return_value={"success": True, "status_code": 200, "error": None}) as mock_forward:
        result = grade_nfl_bookmarks_for_pending("secret", "https://read", "https://write", 2026)

    results.append(check(
        "full chain: schedules loaded for exactly the season passed in",
        mock_schedules.call_args.args == ([2026],),
    ))
    results.append(check("full chain: reports the real read count", result["picks_needing_grading_count"] == 2))
    results.append(check("full chain: only the confirmed win is counted as graded", result["graded_count"] == 1))
    results.append(check("full chain: the pending row is counted separately, not dropped silently", result["still_pending_count"] == 1))

    forwarded_payload = mock_forward.call_args[0][0]
    results.append(check(
        "full chain: only the 'won' row is ever forwarded to bookmark-results-write -- 'pending' never reaches it",
        forwarded_payload == [{"id": "bk-1", "status": "won"}],
    ))

    sent_body = mock_post.call_args.kwargs["data"]
    results.append(check(
        "full chain: the read call explicitly asks for sport: NFL",
        b'"sport":"NFL"' in sent_body,
    ))

    # ------------------------------------------------------------------
    read_body_empty = {"ok": True, "sport": "NFL", "bookmarks_needing_grading": []}
    with patch("nfl_grade_bookmarks_live.requests.post", return_value=_mock_response(read_body_empty)):
        result = grade_nfl_bookmarks_for_pending("secret", "https://read", "https://write", 2026)
    results.append(check(
        "zero picks needing grading is a normal result, not an error, and nothing gets forwarded",
        result["error"] is None and result["picks_needing_grading_count"] == 0 and result["forwarded"] is None,
    ))

    # ------------------------------------------------------------------
    read_body_error = {"ok": False, "error": "boom"}
    with patch("nfl_grade_bookmarks_live.requests.post", return_value=_mock_response(read_body_error)):
        result = grade_nfl_bookmarks_for_pending("secret", "https://read", "https://write", 2026)
    results.append(check(
        "a real read-endpoint error is surfaced in the result, not swallowed",
        result["error"] is not None and "boom" in result["error"],
    ))

    # ------------------------------------------------------------------
    read_body_one_pending = {
        "ok": True,
        "bookmarks_needing_grading": [
            {"id": "bk-1", "player_key": "p1", "event_key": "e1", "player": "Player One", "team": "NE"},
        ],
    }
    with patch("nfl_grade_bookmarks_live.requests.post", return_value=_mock_response(read_body_one_pending)), \
         patch("nfl_grade_bookmarks_live.load_schedules", return_value=pd.DataFrame()), \
         patch(
             "nfl_grade_bookmarks_live.grade_nfl_bookmarks_live",
             return_value={
                 "results": [{"id": "bk-1", "player_key": "p1", "event_key": "e1", "status": "pending", "touchdowns": None, "reason": "r", "espn_event_id": None}],
                 "errors": [],
                 "unique_games_graded": 1,
                 "picks_graded": 1,
             },
         ), \
         patch("nfl_grade_bookmarks_live.forward_to_lovable") as mock_forward_2:
        result = grade_nfl_bookmarks_for_pending("secret", "https://read", "https://write", 2026)

    results.append(check(
        "an all-pending batch never calls forward_to_lovable at all",
        mock_forward_2.call_count == 0,
    ))
    results.append(check(
        "an all-pending batch reports zero graded, one still pending",
        result["forwarded"] is None and result["graded_count"] == 0 and result["still_pending_count"] == 1,
    ))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
