"""
Tests for nfl_grade_bookmarks_correction.py's read -> grade -> decide ->
write chain, in particular the old-result-vs-new-status table that decides
whether a row is written at all and whether it needs correction: true.

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
    # Full chain, one row of each real old-result/new-status combination.
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
        "results": [
            {"id": "never-graded-won", "player_key": "p1", "event_key": "e1", "status": "won", "touchdowns": 1, "reason": "r", "game_final": True},
            {"id": "never-graded-pending", "player_key": "p2", "event_key": "e1", "status": "pending", "touchdowns": None, "reason": "r", "game_final": False},
            {"id": "win-confirmed", "player_key": "p3", "event_key": "e2", "status": "won", "touchdowns": 1, "reason": "r", "game_final": True},
            {"id": "win-overturned-lost", "player_key": "p4", "event_key": "e2", "status": "lost", "touchdowns": 0, "reason": "r", "game_final": True},
            {"id": "win-overturned-void", "player_key": "p5", "event_key": "e3", "status": "void", "touchdowns": 0, "reason": "r", "game_final": True},
        ],
        "errors": [],
        "unique_games_graded": 3,
        "picks_graded": 5,
    }
    loader_patches = _patch_nflverse_loaders()
    with patch("nfl_grade_bookmarks_correction.requests.post", return_value=_mock_response(read_body)), \
         loader_patches[0], loader_patches[1], loader_patches[2], loader_patches[3], \
         patch("nfl_grade_bookmarks_correction.grade_nfl_bookmarks_correction", return_value=grading_results), \
         patch("nfl_grade_bookmarks_correction.forward_to_lovable", return_value={"success": True, "status_code": 200, "error": None}) as mock_forward:
        result = grade_nfl_bookmarks_for_correction("secret", "https://read", "https://write", 2026)

    results.append(check(
        "full chain: reports the real read count",
        result["picks_needing_correction_count"] == 5,
    ))
    results.append(check(
        "never-graded + new terminal status -> counted as newly graded, not corrected",
        result["newly_graded_count"] == 1,
    ))
    results.append(check(
        "old win + new lost/void -> counted as corrected",
        result["corrected_count"] == 2,
    ))
    results.append(check(
        "old win + new won (nflverse agrees with ESPN) -> counted unchanged, not written",
        result["unchanged_count"] == 1,
    ))
    results.append(check(
        "a nflverse-pending row is counted separately and never written",
        result["still_pending_count"] == 1,
    ))

    forwarded_payload = mock_forward.call_args[0][0]
    forwarded_by_id = {r["id"]: r for r in forwarded_payload}
    results.append(check(
        "never-graded row is forwarded WITHOUT correction: true (nothing to overwrite)",
        forwarded_by_id["never-graded-won"] == {"id": "never-graded-won", "status": "won"},
    ))
    results.append(check(
        "old win overturned to lost is forwarded WITH correction: true",
        forwarded_by_id["win-overturned-lost"] == {"id": "win-overturned-lost", "status": "lost", "correction": True},
    ))
    results.append(check(
        "old win overturned to void is forwarded WITH correction: true",
        forwarded_by_id["win-overturned-void"] == {"id": "win-overturned-void", "status": "void", "correction": True},
    ))
    results.append(check(
        "win-confirmed (unchanged) and the still-pending row are never forwarded",
        "win-confirmed" not in forwarded_by_id and "never-graded-pending" not in forwarded_by_id,
    ))
    results.append(check(
        "exactly 3 rows are forwarded -- unchanged and pending rows are excluded",
        len(forwarded_payload) == 3,
    ))

    # ------------------------------------------------------------------
    # An all-unchanged/all-pending batch never calls forward_to_lovable.
    read_body_2 = {
        "ok": True,
        "bookmarks_needing_correction": [
            {"id": "a", "player_key": "p1", "event_key": "e1", "result": "win"},
            {"id": "b", "player_key": "p2", "event_key": "e2", "result": None},
        ],
    }
    grading_results_2 = {
        "results": [
            {"id": "a", "player_key": "p1", "event_key": "e1", "status": "won", "touchdowns": 1, "reason": "r", "game_final": True},
            {"id": "b", "player_key": "p2", "event_key": "e2", "status": "pending", "touchdowns": None, "reason": "r", "game_final": False},
        ],
        "errors": [],
        "unique_games_graded": 2,
        "picks_graded": 2,
    }
    loader_patches = _patch_nflverse_loaders()
    with patch("nfl_grade_bookmarks_correction.requests.post", return_value=_mock_response(read_body_2)), \
         loader_patches[0], loader_patches[1], loader_patches[2], loader_patches[3], \
         patch("nfl_grade_bookmarks_correction.grade_nfl_bookmarks_correction", return_value=grading_results_2), \
         patch("nfl_grade_bookmarks_correction.forward_to_lovable") as mock_forward_2:
        result = grade_nfl_bookmarks_for_correction("secret", "https://read", "https://write", 2026)
    results.append(check(
        "nothing to write at all -> forward_to_lovable is never called",
        mock_forward_2.call_count == 0 and result["forwarded"] is None,
    ))

    # ------------------------------------------------------------------
    # zero picks needing correction is a normal result, not an error.
    read_body_empty = {"ok": True, "bookmarks_needing_correction": []}
    with patch("nfl_grade_bookmarks_correction.requests.post", return_value=_mock_response(read_body_empty)):
        result = grade_nfl_bookmarks_for_correction("secret", "https://read", "https://write", 2026)
    results.append(check(
        "zero picks needing correction is a normal result, not an error",
        result["error"] is None and result["picks_needing_correction_count"] == 0 and result["forwarded"] is None,
    ))

    # ------------------------------------------------------------------
    # a real read-endpoint error is surfaced, not swallowed.
    read_body_error = {"ok": False, "error": "boom"}
    with patch("nfl_grade_bookmarks_correction.requests.post", return_value=_mock_response(read_body_error)):
        result = grade_nfl_bookmarks_for_correction("secret", "https://read", "https://write", 2026)
    results.append(check(
        "a real read-endpoint error is surfaced in the result, not swallowed",
        result["error"] is not None and "boom" in result["error"],
    ))

    # ------------------------------------------------------------------
    # lookback_days, when passed, reaches the read endpoint's request body.
    with patch("nfl_grade_bookmarks_correction.requests.post", return_value=_mock_response(read_body_empty)) as mock_post:
        grade_nfl_bookmarks_for_correction("secret", "https://read", "https://write", 2026, lookback_days=14)
    sent_body = mock_post.call_args.kwargs["data"]
    results.append(check(
        "an explicit lookback_days is forwarded to the read endpoint's request body",
        b'"lookback_days":14' in sent_body,
    ))
    with patch("nfl_grade_bookmarks_correction.requests.post", return_value=_mock_response(read_body_empty)) as mock_post_default:
        grade_nfl_bookmarks_for_correction("secret", "https://read", "https://write", 2026)
    sent_body_default = mock_post_default.call_args.kwargs["data"]
    results.append(check(
        "omitting lookback_days sends an empty body, letting the endpoint apply its own default",
        sent_body_default == b"{}",
    ))

    # ------------------------------------------------------------------
    # nflverse data is loaded for exactly the season passed in.
    with patch("nfl_grade_bookmarks_correction.requests.post", return_value=_mock_response(read_body)), \
         patch("nfl_grade_bookmarks_correction.load_pbp", return_value=pd.DataFrame()) as mock_pbp, \
         patch("nfl_grade_bookmarks_correction.load_schedules", return_value=pd.DataFrame()) as mock_sched, \
         patch("nfl_grade_bookmarks_correction.load_snap_counts", return_value=pd.DataFrame()) as mock_snaps, \
         patch("nfl_grade_bookmarks_correction.load_id_crosswalk", return_value=pd.DataFrame()) as mock_crosswalk, \
         patch("nfl_grade_bookmarks_correction.grade_nfl_bookmarks_correction", return_value=grading_results), \
         patch("nfl_grade_bookmarks_correction.forward_to_lovable", return_value={"success": True, "status_code": 200, "error": None}):
        grade_nfl_bookmarks_for_correction("secret", "https://read", "https://write", 2031)
    results.append(check(
        "all four nflverse data sources are loaded for exactly the season passed in",
        mock_pbp.call_args.args == ([2031],)
        and mock_sched.call_args.args == ([2031],)
        and mock_snaps.call_args.args == ([2031],)
        and mock_crosswalk.call_args.args == ([2031],),
    ))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
