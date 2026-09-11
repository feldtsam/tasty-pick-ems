"""
Tests for nfl_bookmark_grading.py's fan-out/dedup orchestration.

Run: python3 nfl/test_nfl_bookmark_grading.py
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

from nfl_bookmark_grading import grade_nfl_bookmarks_correction, grade_nfl_bookmarks_live


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


if __name__ == "__main__":
    results = []

    # ------------------------------------------------------------------
    # grade_nfl_bookmarks_live (ESPN)
    # ------------------------------------------------------------------
    picks = [
        {"id": "a", "player_key": "p1", "event_key": "e1", "player": "Player One", "team": "NE"},
        {"id": "b", "player_key": "p2", "event_key": "e1", "player": "Player Two", "team": "SEA"},
        {"id": "c", "player_key": "p1", "event_key": "e1", "player": "Player One", "team": "NE"},  # same pick, different user
    ]
    with patch(
        "nfl_bookmark_grading.grade_pick_espn",
        return_value={"status": "won", "reason": "r", "touchdowns": 1, "espn_event_id": 1},
    ) as mock_grade:
        result = grade_nfl_bookmarks_live(picks, pd.DataFrame())

    results.append(check(
        "live grading: two real unique (player_key, event_key) pairs graded exactly once each, "
        f"even with a third pick duplicating one of them (got {mock_grade.call_count} real calls)",
        mock_grade.call_count == 2,
    ))
    results.append(check("live grading: all three input picks produce a result", result["picks_graded"] == 3))
    results.append(check(
        "live grading: results preserve input order by id",
        [r["id"] for r in result["results"]] == ["a", "b", "c"],
    ))
    results.append(check(
        "live grading: a won ESPN grade fans out to every bookmark sharing the same real pick",
        all(r["status"] == "won" for r in result["results"]),
    ))

    with patch(
        "nfl_bookmark_grading.grade_pick_espn",
        return_value={"status": "pending", "reason": "not found", "touchdowns": None, "espn_event_id": 1},
    ):
        result = grade_nfl_bookmarks_live(
            [{"id": "a", "player_key": "p1", "event_key": "e1", "player": "Someone", "team": "NE"}],
            pd.DataFrame(),
        )
    results.append(check(
        "live grading: a pending ESPN grade passes through as pending (grade_pick_espn can never return lost/void anyway)",
        result["results"][0]["status"] == "pending",
    ))

    def _fake_grade_espn(player_key, player_name, team, event_key, schedules):
        if event_key == "e1":
            raise ValueError("network error")
        return {"status": "won", "reason": "ok", "touchdowns": 1, "espn_event_id": 2}

    with patch("nfl_bookmark_grading.grade_pick_espn", side_effect=_fake_grade_espn):
        result = grade_nfl_bookmarks_live(
            [
                {"id": "a", "player_key": "p1", "event_key": "e1", "player": "P1", "team": "NE"},
                {"id": "b", "player_key": "p2", "event_key": "e2", "player": "P2", "team": "SEA"},
            ],
            pd.DataFrame(),
        )
    results.append(check(
        "live grading: one game erroring is recorded in errors, doesn't sink the whole batch",
        len(result["errors"]) == 1 and result["errors"][0]["event_key"] == "e1",
    ))
    results.append(check(
        "live grading: the OTHER pick still grades successfully despite the sibling error",
        result["picks_graded"] == 1 and result["results"][0]["id"] == "b",
    ))

    # ------------------------------------------------------------------
    # grade_nfl_bookmarks_correction (nflverse)
    # ------------------------------------------------------------------
    with patch(
        "nfl_bookmark_grading.grade_pick_nflverse",
        return_value={"status": "lost", "reason": "r", "touchdowns": 0, "game_final": True},
    ) as mock_grade:
        result = grade_nfl_bookmarks_correction(
            [{"id": "a", "player_key": "p1", "event_key": "e1"}, {"id": "b", "player_key": "p1", "event_key": "e1"}],
            pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
        )
    results.append(check(
        "correction grading: two bookmarks sharing one real pick still trigger exactly one nflverse lookup",
        mock_grade.call_count == 1,
    ))
    results.append(check(
        "correction grading: game_final carries through to every fanned-out result",
        all(r["game_final"] is True for r in result["results"]),
    ))

    def _fake_grade_nflverse(player_key, event_key, pbp, schedules, snap_counts, id_crosswalk):
        return {"status": "void" if player_key == "p2" else "lost", "reason": "r", "touchdowns": 0, "game_final": True}

    with patch("nfl_bookmark_grading.grade_pick_nflverse", side_effect=_fake_grade_nflverse):
        result = grade_nfl_bookmarks_correction(
            [{"id": "a", "player_key": "p1", "event_key": "e1"}, {"id": "b", "player_key": "p2", "event_key": "e1"}],
            pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
        )
    statuses = {r["id"]: r["status"] for r in result["results"]}
    results.append(check(
        "correction grading: unlike live grading, this source can produce real lost AND void verdicts",
        statuses == {"a": "lost", "b": "void"},
    ))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
