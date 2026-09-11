"""
Tests for nfl_grade_bookmarks_correction.py's read -> grade -> decide ->
write chain, including its System (b) extension (shelf picks ->
nfl_official_pick_results, applying the same old-vs-new decision table
independently per destination).

Run: python3 nfl/api/test_nfl_grade_bookmarks_correction.py
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

from nfl_grade_bookmarks_correction import grade_nfl_bookmarks_for_correction


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


def _patch_nflverse_loaders():
    return (
        patch("nfl_grade_bookmarks_correction.load_pbp", return_value=pd.DataFrame()),
        patch("nfl_grade_bookmarks_correction.load_schedules", return_value=pd.DataFrame()),
        patch("nfl_grade_bookmarks_correction.load_snap_counts", return_value=pd.DataFrame()),
        patch("nfl_grade_bookmarks_correction.load_id_crosswalk", return_value=pd.DataFrame()),
    )


if __name__ == "__main__":
    results = []

    # ------------------------------------------------------------------
    # Bookmark-only chain (no shelf_read_url) -- must behave exactly as
    # before System (b).
    read_body = {
        "ok": True,
        "bookmarks_needing_correction": [
            {"id": "never-graded-won", "player_key": "p1", "event_key": "e1", "result": None},
            {"id": "never-graded-pending", "player_key": "p2", "event_key": "e1", "result": None},
            {"id": "win-confirmed", "player_key": "p3", "event_key": "e2", "result": "win"},
            {"id": "win-overturned-lost", "player_key": "p4", "event_key": "e2", "result": "win"},
            {"id": "win-overturned-void", "player_key": "p5", "event_key": "e3", "result": "win"},
        ],
    }
    grading_results = {
        "bookmark_results": [
            {"id": "never-graded-won", "player_key": "p1", "event_key": "e1", "status": "won", "touchdowns": 1, "reason": "r", "game_final": True},
            {"id": "never-graded-pending", "player_key": "p2", "event_key": "e1", "status": "pending", "touchdowns": None, "reason": "r", "game_final": False},
            {"id": "win-confirmed", "player_key": "p3", "event_key": "e2", "status": "won", "touchdowns": 1, "reason": "r", "game_final": True},
            {"id": "win-overturned-lost", "player_key": "p4", "event_key": "e2", "status": "lost", "touchdowns": 0, "reason": "r", "game_final": True},
            {"id": "win-overturned-void", "player_key": "p5", "event_key": "e3", "status": "void", "touchdowns": 0, "reason": "r", "game_final": True},
        ],
        "shelf_results": [],
        "errors": [],
        "unique_games_graded": 3,
        "picks_graded": 5,
    }
    loader_patches = _patch_nflverse_loaders()
    with patch("nfl_grade_bookmarks_correction.requests.post", return_value=_mock_response(read_body)), \
         loader_patches[0], loader_patches[1], loader_patches[2], loader_patches[3], \
         patch("nfl_grade_bookmarks_correction.grade_nfl_picks_correction", return_value=grading_results) as mock_grade, \
         patch("nfl_grade_bookmarks_correction.forward_to_lovable", return_value={"success": True, "status_code": 200, "error": None}) as mock_forward:
        result = grade_nfl_bookmarks_for_correction("secret", "https://read", "https://write", 2026)

    results.append(check("bookmark-only: reports the real read count", result["picks_needing_correction_count"] == 5))
    results.append(check("bookmark-only: never-graded + new terminal status -> newly graded", result["newly_graded_count"] == 1))
    results.append(check("bookmark-only: old win + new lost/void -> corrected", result["corrected_count"] == 2))
    results.append(check("bookmark-only: old win + new won -> unchanged, not written", result["unchanged_count"] == 1))
    results.append(check("bookmark-only: pending row counted separately, never written", result["still_pending_count"] == 1))
    results.append(check(
        "bookmark-only: grade_nfl_picks_correction receives an empty shelf_picks list when no shelf_read_url is given",
        mock_grade.call_args.args[1] == [],
    ))
    results.append(check(
        "bookmark-only: shelf_* fields are all zeroed, shelf write never attempted",
        result["shelf_picks_needing_correction_count"] == 0 and result["shelf_forwarded"] is None,
    ))

    forwarded_payload = mock_forward.call_args[0][0]
    forwarded_by_id = {r["id"]: r for r in forwarded_payload}
    results.append(check(
        "never-graded row forwarded WITHOUT correction: true",
        forwarded_by_id["never-graded-won"] == {"id": "never-graded-won", "status": "won"},
    ))
    results.append(check(
        "old win overturned to lost forwarded WITH correction: true",
        forwarded_by_id["win-overturned-lost"] == {"id": "win-overturned-lost", "status": "lost", "correction": True},
    ))
    results.append(check("exactly 3 rows forwarded on the bookmark side", len(forwarded_payload) == 3))

    # ------------------------------------------------------------------
    # zero picks / read errors -- unchanged behavior.
    read_body_empty = {"ok": True, "bookmarks_needing_correction": []}
    with patch("nfl_grade_bookmarks_correction.requests.post", return_value=_mock_response(read_body_empty)):
        result = grade_nfl_bookmarks_for_correction("secret", "https://read", "https://write", 2026)
    results.append(check(
        "zero picks needing correction is a normal result, not an error",
        result["error"] is None and result["picks_needing_correction_count"] == 0 and result["forwarded"] is None,
    ))

    read_body_error = {"ok": False, "error": "boom"}
    with patch("nfl_grade_bookmarks_correction.requests.post", return_value=_mock_response(read_body_error)):
        result = grade_nfl_bookmarks_for_correction("secret", "https://read", "https://write", 2026)
    results.append(check(
        "a real read-endpoint error is surfaced in the result, not swallowed",
        result["error"] is not None and "boom" in result["error"],
    ))

    # ------------------------------------------------------------------
    # System (b): shelf_read_url/shelf_write_url given -- the full
    # combined chain, decision table applied independently per side.
    read_body_c = {"ok": True, "bookmarks_needing_correction": [
        {"id": "bk-1", "player_key": "p1", "event_key": "e1", "result": None},
    ]}
    shelf_read_body_c = {"ok": True, "shelf_picks_needing_correction": [
        # Same real pick as bk-1 -- old status None (never graded).
        {"player_id": "p1", "event_id": "e1", "shelf": "RB Trends", "status": None},
        # A shelf pick previously ESPN-confirmed "won", now overturned.
        {"player_id": "p2", "event_id": "e2", "shelf": "WR Trends", "status": "won"},
    ]}
    grading_results_c = {
        "bookmark_results": [
            {"id": "bk-1", "player_key": "p1", "event_key": "e1", "status": "won", "touchdowns": 1, "reason": "r", "game_final": True},
        ],
        "shelf_results": [
            {"player_id": "p1", "event_id": "e1", "shelf": "RB Trends", "status": "won", "touchdowns": 1, "reason": "r", "game_final": True},
            {"player_id": "p2", "event_id": "e2", "shelf": "WR Trends", "status": "lost", "touchdowns": 0, "reason": "r", "game_final": True},
        ],
        "errors": [],
        "unique_games_graded": 2,
        "picks_graded": 3,
    }

    def _post_router(url, **kwargs):
        if url == "https://shelf-read":
            return _mock_response(shelf_read_body_c)
        return _mock_response(read_body_c)

    loader_patches = _patch_nflverse_loaders()
    with patch("nfl_grade_bookmarks_correction.requests.post", side_effect=_post_router) as mock_post_c, \
         loader_patches[0], loader_patches[1], loader_patches[2], loader_patches[3], \
         patch("nfl_grade_bookmarks_correction.grade_nfl_picks_correction", return_value=grading_results_c) as mock_grade_c, \
         patch("nfl_grade_bookmarks_correction.forward_to_lovable", return_value={"success": True, "status_code": 200, "error": None}) as mock_forward_c:
        result = grade_nfl_bookmarks_for_correction(
            "secret", "https://read", "https://write", 2026,
            shelf_read_url="https://shelf-read", shelf_write_url="https://shelf-write",
        )

    results.append(check("combined chain: both read endpoints called", mock_post_c.call_count == 2))
    results.append(check(
        "combined chain: grade_nfl_picks_correction receives both bookmark and shelf picks",
        len(mock_grade_c.call_args.args[0]) == 1 and len(mock_grade_c.call_args.args[1]) == 2,
    ))
    results.append(check(
        "combined chain: bookmark side (never graded -> won) counted as newly graded",
        result["newly_graded_count"] == 1 and result["corrected_count"] == 0,
    ))
    results.append(check(
        "combined chain: shelf side -- the SAME real pick as bk-1 is ALSO newly graded on its own destination",
        result["shelf_newly_graded_count"] == 1,
    ))
    results.append(check(
        "combined chain: shelf side -- p2's prior 'won' overturned to 'lost' is a real correction",
        result["shelf_corrected_count"] == 1,
    ))
    results.append(check(
        "combined chain: forward_to_lovable called twice, once per destination",
        mock_forward_c.call_count == 2,
    ))
    shelf_forward_call = [c for c in mock_forward_c.call_args_list if c.args[2] == "https://shelf-write"][0]
    shelf_payload_by_key = {(r["player_id"], r["event_id"]): r for r in shelf_forward_call.args[0]}
    results.append(check(
        "combined chain: shelf write payload correctly sets correction: true only for the overturned row",
        "correction" not in shelf_payload_by_key[("p1", "e1")]
        and shelf_payload_by_key[("p2", "e2")].get("correction") is True,
    ))

    # A shelf read-endpoint error aborts the whole run.
    shelf_read_body_error = {"ok": False, "error": "shelf boom"}

    def _post_router_err(url, **kwargs):
        if url == "https://shelf-read":
            return _mock_response(shelf_read_body_error)
        return _mock_response(read_body_c)

    with patch("nfl_grade_bookmarks_correction.requests.post", side_effect=_post_router_err):
        result = grade_nfl_bookmarks_for_correction(
            "secret", "https://read", "https://write", 2026,
            shelf_read_url="https://shelf-read", shelf_write_url="https://shelf-write",
        )
    results.append(check(
        "a shelf read-endpoint error is surfaced, not swallowed",
        result["error"] is not None and "shelf boom" in result["error"],
    ))

    # ------------------------------------------------------------------
    # lookback_days is forwarded to BOTH read endpoints identically.
    with patch("nfl_grade_bookmarks_correction.requests.post", return_value=_mock_response({"ok": True, "bookmarks_needing_correction": []})) as mock_post_lb:
        grade_nfl_bookmarks_for_correction("secret", "https://read", "https://write", 2026, lookback_days=14)
    sent_body = mock_post_lb.call_args.kwargs["data"]
    results.append(check(
        "an explicit lookback_days is forwarded to the read endpoint's request body",
        b'"lookback_days":14' in sent_body,
    ))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
