"""
Tests for nfl_grade_bookmarks_live.py's read -> grade -> write chain,
including its System (b) extension (shelf picks -> nfl_official_pick_results,
"one shared grading determination, two destinations").

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
    # Bookmark-only chain (no shelf_read_url) -- must behave exactly as
    # it did before System (b): a caller that never passes shelf URLs
    # gets the same bookmark-only behavior, all shelf_* fields zeroed.
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
             "nfl_grade_bookmarks_live.grade_nfl_picks_live",
             return_value={
                 "bookmark_results": [
                     {"id": "bk-1", "player_key": "p1", "event_key": "e1", "status": "won", "touchdowns": 1, "reason": "r", "espn_event_id": 1},
                     {"id": "bk-2", "player_key": "p2", "event_key": "e1", "status": "pending", "touchdowns": None, "reason": "r2", "espn_event_id": 1},
                 ],
                 "shelf_results": [],
                 "errors": [],
                 "unique_games_graded": 2,
                 "picks_graded": 2,
             },
         ) as mock_grade, \
         patch("nfl_grade_bookmarks_live.forward_to_lovable", return_value={"success": True, "status_code": 200, "error": None}) as mock_forward:
        result = grade_nfl_bookmarks_for_pending("secret", "https://read", "https://write", 2026)

    results.append(check(
        "bookmark-only: schedules loaded for exactly the season passed in",
        mock_schedules.call_args.args == ([2026],),
    ))
    results.append(check("bookmark-only: reports the real read count", result["picks_needing_grading_count"] == 2))
    results.append(check("bookmark-only: only the confirmed win is counted as graded", result["graded_count"] == 1))
    results.append(check("bookmark-only: the pending row is counted separately, not dropped silently", result["still_pending_count"] == 1))
    results.append(check(
        "bookmark-only: grade_nfl_picks_live is called with an empty shelf_picks list when no shelf_read_url is given",
        mock_grade.call_args.args[1] == [],
    ))
    results.append(check(
        "bookmark-only: shelf_* fields are all zeroed, shelf write never attempted",
        result["shelf_picks_needing_grading_count"] == 0 and result["shelf_forwarded"] is None,
    ))

    forwarded_payload = mock_forward.call_args[0][0]
    results.append(check(
        "bookmark-only: only the 'won' row is ever forwarded to bookmark-results-write -- 'pending' never reaches it",
        forwarded_payload == [{"id": "bk-1", "status": "won"}],
    ))

    sent_body = mock_post.call_args.kwargs["data"]
    results.append(check(
        "bookmark-only: the read call explicitly asks for sport: NFL",
        b'"sport":"NFL"' in sent_body,
    ))

    # ------------------------------------------------------------------
    # zero picks needing grading is a normal result, not an error.
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
             "nfl_grade_bookmarks_live.grade_nfl_picks_live",
             return_value={
                 "bookmark_results": [{"id": "bk-1", "player_key": "p1", "event_key": "e1", "status": "pending", "touchdowns": None, "reason": "r", "espn_event_id": None}],
                 "shelf_results": [],
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

    # ------------------------------------------------------------------
    # System (b): shelf_read_url/shelf_write_url given -- the full combined
    # chain, including a shelf pick with NO matching bookmark at all.
    read_body_combined = {
        "ok": True,
        "bookmarks_needing_grading": [
            {"id": "bk-1", "player_key": "p1", "event_key": "e1", "player": "Player One", "team": "NE"},
        ],
    }
    shelf_read_body = {
        "ok": True,
        "shelf_picks_needing_grading": [
            {"player_id": "p1", "event_id": "e1", "shelf": "RB Trends", "player_name": "Player One", "team": "NE"},
            {"player_id": "p2", "event_id": "e2", "shelf": "WR Trends", "player_name": "Player Two", "team": "SEA"},
        ],
    }

    def _post_router(url, **kwargs):
        if url == "https://shelf-read":
            return _mock_response(shelf_read_body)
        return _mock_response(read_body_combined)

    with patch("nfl_grade_bookmarks_live.requests.post", side_effect=_post_router) as mock_post_c, \
         patch("nfl_grade_bookmarks_live.load_schedules", return_value=pd.DataFrame()), \
         patch(
             "nfl_grade_bookmarks_live.grade_nfl_picks_live",
             return_value={
                 "bookmark_results": [{"id": "bk-1", "player_key": "p1", "event_key": "e1", "status": "won", "touchdowns": 1, "reason": "r", "espn_event_id": 1}],
                 "shelf_results": [
                     {"player_id": "p1", "event_id": "e1", "shelf": "RB Trends", "status": "won", "touchdowns": 1, "reason": "r", "espn_event_id": 1},
                     {"player_id": "p2", "event_id": "e2", "shelf": "WR Trends", "status": "pending", "touchdowns": None, "reason": "r2", "espn_event_id": 2},
                 ],
                 "errors": [],
                 "unique_games_graded": 2,
                 "picks_graded": 3,
             },
         ) as mock_grade_c, \
         patch("nfl_grade_bookmarks_live.forward_to_lovable", return_value={"success": True, "status_code": 200, "error": None}) as mock_forward_c:
        result = grade_nfl_bookmarks_for_pending(
            "secret", "https://read", "https://write", 2026,
            shelf_read_url="https://shelf-read", shelf_write_url="https://shelf-write",
        )

    results.append(check(
        "combined chain: both read endpoints are called",
        mock_post_c.call_count == 2,
    ))
    results.append(check(
        "combined chain: grade_nfl_picks_live receives both bookmark and shelf picks",
        len(mock_grade_c.call_args.args[0]) == 1 and len(mock_grade_c.call_args.args[1]) == 2,
    ))
    results.append(check(
        "combined chain: bookmark side reports its own won/pending counts",
        result["graded_count"] == 1 and result["still_pending_count"] == 0,
    ))
    results.append(check(
        "combined chain: shelf side reports its own won/pending counts, independently",
        result["shelf_graded_count"] == 1 and result["shelf_still_pending_count"] == 1,
    ))
    results.append(check(
        "combined chain: forward_to_lovable is called twice -- once per destination",
        mock_forward_c.call_count == 2,
    ))
    shelf_forward_call = [c for c in mock_forward_c.call_args_list if c.args[2] == "https://shelf-write"][0]
    results.append(check(
        "combined chain: only the shelf 'won' row is forwarded to the shelf write endpoint, shaped correctly",
        shelf_forward_call.args[0] == [
            {"player_id": "p1", "event_id": "e1", "shelf": "RB Trends", "status": "won", "touchdowns": 1, "reason": "r"},
        ],
    ))

    # A shelf read-endpoint error aborts the whole run (same discipline as
    # a bookmark read-endpoint error) -- it's surfaced, not silently
    # dropped in favor of bookmark-only results.
    shelf_read_body_error = {"ok": False, "error": "shelf boom"}

    def _post_router_err(url, **kwargs):
        if url == "https://shelf-read":
            return _mock_response(shelf_read_body_error)
        return _mock_response(read_body_combined)

    with patch("nfl_grade_bookmarks_live.requests.post", side_effect=_post_router_err):
        result = grade_nfl_bookmarks_for_pending(
            "secret", "https://read", "https://write", 2026,
            shelf_read_url="https://shelf-read", shelf_write_url="https://shelf-write",
        )
    results.append(check(
        "a shelf read-endpoint error is surfaced, not swallowed",
        result["error"] is not None and "shelf boom" in result["error"],
    ))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
