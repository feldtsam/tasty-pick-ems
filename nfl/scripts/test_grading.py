"""
Tests for grading.py -- both real data sources, exercised against
schema-accurate fixtures (ESPN's real response shape, confirmed against a
real completed game this session; nflverse's real pbp/schedules/snap-count
column names, all already proven elsewhere in this codebase).

Run: python3 nfl/scripts/test_grading.py
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

from grading import grade_pick_espn, grade_pick_nflverse


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


SCHEDULES = pd.DataFrame(
    [
        {
            "game_id": "2026_01_NE_SEA",
            "home_team": "SEA",
            "away_team": "NE",
            "espn": 401872656,
            "home_score": 13.0,
            "away_score": 10.0,
        },
        {
            "game_id": "2026_01_NO_DET",
            "home_team": "DET",
            "away_team": "NO",
            "espn": None,
            "home_score": None,
            "away_score": None,
        },
    ]
)


def _espn_response(completed=True, description="Final"):
    return {
        "header": {
            "competitions": [
                {
                    "status": {"type": {"completed": completed, "description": description}},
                    "competitors": [
                        {"homeAway": "home", "team": {"id": "26"}},
                        {"homeAway": "away", "team": {"id": "17"}},
                    ],
                }
            ]
        },
        "boxscore": {
            "players": [
                {
                    "team": {"id": "17"},
                    "statistics": [
                        {
                            "name": "rushing",
                            "labels": ["CAR", "YDS", "AVG", "TD", "LONG"],
                            "athletes": [
                                {"athlete": {"id": "111", "displayName": "Rhamondre Stevenson"}, "stats": ["18", "51", "2.8", "1", "12"]},
                            ],
                        },
                        {
                            "name": "receiving",
                            "labels": ["REC", "YDS", "AVG", "TD", "LONG", "TGTS"],
                            "athletes": [
                                {"athlete": {"id": "222", "displayName": "Eli Raridon"}, "stats": ["3", "22", "7.3", "1", "10", "4"]},
                            ],
                        },
                    ],
                },
                {"team": {"id": "26"}, "statistics": []},
            ]
        },
    }


class _FakeResponse:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


PBP = pd.DataFrame(
    [
        {"game_id": "2026_01_NE_SEA", "rusher_player_id": "00-RUSHER", "receiver_player_id": None,
         "rush_touchdown": 1, "pass_touchdown": 0},
        {"game_id": "2026_01_NE_SEA", "rusher_player_id": "00-RUSHER", "receiver_player_id": None,
         "rush_touchdown": 0, "pass_touchdown": 0},
        {"game_id": "2026_01_NE_SEA", "rusher_player_id": None, "receiver_player_id": "00-RECEIVER",
         "rush_touchdown": 0, "pass_touchdown": 1},
        # A defensive/return score on the SAME game -- must never count for
        # the offensive player who happened to fumble; note it never sets
        # rush_touchdown/pass_touchdown for any offensive player_id at all,
        # which is the entire mechanism, not a separate exclusion filter.
        {"game_id": "2026_01_NE_SEA", "rusher_player_id": "00-RUSHER", "receiver_player_id": None,
         "rush_touchdown": 0, "pass_touchdown": 0},
    ]
)

SCHEDULES_NFLVERSE = pd.DataFrame(
    [
        {"game_id": "2026_01_NE_SEA", "home_score": 13.0, "away_score": 10.0},
        {"game_id": "2026_01_NOT_FINAL", "home_score": None, "away_score": None},
    ]
)

SNAP_COUNTS = pd.DataFrame(
    [
        {"game_id": "2026_01_NE_SEA", "pfr_player_id": "PFR-PLAYED", "offense_snaps": 40},
        {"game_id": "2026_01_NE_SEA", "pfr_player_id": "PFR-ZERO-SNAPS", "offense_snaps": 0},
    ]
)

ID_CROSSWALK = pd.DataFrame(
    [
        {"gsis_id": "00-RUSHER", "pfr_id": "PFR-RUSHER"},
        {"gsis_id": "00-PLAYED-NO-TD", "pfr_id": "PFR-PLAYED"},
        {"gsis_id": "00-INACTIVE", "pfr_id": "PFR-ZERO-SNAPS"},
    ]
)


if __name__ == "__main__":
    results = []

    # ------------------------------------------------------------------
    # grade_pick_espn
    # ------------------------------------------------------------------
    with patch("grading.requests.get", return_value=_FakeResponse(_espn_response())) as mock_get:
        r = grade_pick_espn("00-1111", "Rhamondre Stevenson", "NE", "2026_01_NE_SEA", SCHEDULES)
    results.append(check(
        "ESPN: a real rushing touchdown in the box score grades 'won' with the right touchdown count",
        r == {
            "status": "won",
            "reason": "1 real rushing/receiving touchdown(s), confirmed via ESPN box score",
            "touchdowns": 1,
            "espn_event_id": 401872656,
        },
    ))
    results.append(check("ESPN: exactly one summary fetch for one pick", mock_get.call_count == 1))

    with patch("grading.requests.get", return_value=_FakeResponse(_espn_response())):
        r = grade_pick_espn("00-2222", "Eli Raridon", "NE", "2026_01_NE_SEA", SCHEDULES)
    results.append(check(
        "ESPN: a real receiving touchdown also grades 'won'",
        r["status"] == "won" and r["touchdowns"] == 1,
    ))

    with patch("grading.requests.get", return_value=_FakeResponse(_espn_response())):
        r = grade_pick_espn("00-3333", "Someone Else", "NE", "2026_01_NE_SEA", SCHEDULES)
    results.append(check(
        "ESPN: a player not found in the box score is 'pending', never guessed as lost/void",
        r["status"] == "pending" and "not found" in r["reason"],
    ))

    with patch("grading.requests.get", return_value=_FakeResponse(_espn_response(completed=False, description="In Progress"))):
        r = grade_pick_espn("00-1111", "Rhamondre Stevenson", "NE", "2026_01_NE_SEA", SCHEDULES)
    results.append(check(
        "ESPN: an in-progress game stays 'pending', citing the real status text",
        r["status"] == "pending" and "In Progress" in r["reason"],
    ))

    r = grade_pick_espn("00-9999", "Someone", "DET", "2026_01_NO_DET", SCHEDULES)
    results.append(check(
        "ESPN: no espn event id mapped yet for this game_id -> pending, no network call attempted",
        r["status"] == "pending" and "no ESPN event id mapped" in r["reason"],
    ))

    r = grade_pick_espn("00-9999", "Someone", "NE", "2026_99_XX_YY", SCHEDULES)
    results.append(check("ESPN: unknown game_id -> pending, not a crash", r["status"] == "pending"))

    r = grade_pick_espn("00-9999", "Someone", "KC", "2026_01_NE_SEA", SCHEDULES)
    results.append(check(
        "ESPN: a saved team matching neither the home nor away side -> pending, never guesses a side",
        r["status"] == "pending" and "matches neither" in r["reason"],
    ))

    ambiguous_body = _espn_response()
    ambiguous_body["boxscore"]["players"][0]["statistics"][0]["athletes"].append(
        {"athlete": {"id": "999", "displayName": "Rhamondre Stevenson"}, "stats": ["1", "2", "2.0", "0", "2"]}
    )
    with patch("grading.requests.get", return_value=_FakeResponse(ambiguous_body)):
        r = grade_pick_espn("00-1111", "Rhamondre Stevenson", "NE", "2026_01_NE_SEA", SCHEDULES)
    results.append(check(
        "ESPN: two distinct athlete ids sharing one name on the same team -> pending, never a guessed match",
        r["status"] == "pending" and "ambiguous" in r["reason"],
    ))

    # ------------------------------------------------------------------
    # grade_pick_nflverse
    # ------------------------------------------------------------------
    r = grade_pick_nflverse("00-RUSHER", "2026_01_NE_SEA", PBP, SCHEDULES_NFLVERSE, SNAP_COUNTS, ID_CROSSWALK)
    results.append(check("nflverse: a real rush_touchdown row grades 'won'", r["status"] == "won" and r["touchdowns"] == 1))

    r = grade_pick_nflverse("00-RECEIVER", "2026_01_NE_SEA", PBP, SCHEDULES_NFLVERSE, SNAP_COUNTS, ID_CROSSWALK)
    results.append(check("nflverse: a real pass_touchdown (as receiver) row grades 'won'", r["status"] == "won"))

    r = grade_pick_nflverse("00-RUSHER", "2026_01_NOT_FINAL", PBP, SCHEDULES_NFLVERSE, SNAP_COUNTS, ID_CROSSWALK)
    results.append(check(
        "nflverse: a game with no final score in schedules stays 'pending'",
        r["status"] == "pending",
    ))

    r = grade_pick_nflverse("00-PLAYED-NO-TD", "2026_01_NE_SEA", PBP, SCHEDULES_NFLVERSE, SNAP_COUNTS, ID_CROSSWALK)
    results.append(check(
        "nflverse: zero TDs but real offense_snaps > 0 -> 'lost', not void",
        r["status"] == "lost" and r["touchdowns"] == 0,
    ))

    r = grade_pick_nflverse("00-INACTIVE", "2026_01_NE_SEA", PBP, SCHEDULES_NFLVERSE, SNAP_COUNTS, ID_CROSSWALK)
    results.append(check(
        "nflverse: zero TDs and zero recorded offense_snaps -> 'void', matching MLB's own DNP convention",
        r["status"] == "void" and "did not play" in r["reason"],
    ))

    r = grade_pick_nflverse("00-UNKNOWN", "2026_01_NE_SEA", PBP, SCHEDULES_NFLVERSE, SNAP_COUNTS, ID_CROSSWALK)
    results.append(check(
        "nflverse: no pfr crosswalk entry at all -> 'void' (can't confirm participation), not guessed as lost",
        r["status"] == "void" and "crosswalk" in r["reason"],
    ))

    r = grade_pick_nflverse("00-RUSHER", "2026_01_NE_SEA", PBP, SCHEDULES_NFLVERSE, SNAP_COUNTS, ID_CROSSWALK)
    results.append(check(
        "nflverse: a same-game DEF/ST-style non-scoring row for the same rusher never inflates the real TD count",
        r["touchdowns"] == 1,
    ))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
