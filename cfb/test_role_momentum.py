"""
Fixture unit tests for cfb/role_momentum.py — the box-score + PPA ingest
that assembles `cfb_player_role_weekly`.

    python3 cfb/test_role_momentum.py

No network: a synthetic one-game /games/players payload exercises the
touch extraction (CAR + REC summed per athlete), the team_touches
denominator (QB keepers in, " Team" pseudo-athlete out), the RB/WR/TE
row filter, the PPA join, and the returning-player id-continuity check.

The real-data pull + scoring sanity check lives in
cfb/scripts/role_momentum_sanity.py.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from role_momentum import (
    _all_box_score_athletes,
    _touches_from_game,
    build_role_momentum_weekly,
    role_weekly_frame,
)


def check(label, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    return bool(cond)


# one synthetic game: Team A (id 10) vs Team B (id 20)
def _cat(name, stat, athletes):
    return {"name": name, "types": [{"name": stat, "athletes": athletes}]}


GAME = {
    "id": 555,
    "teams": [
        {"team": "Aggies", "homeAway": "home", "categories": [
            _cat("rushing", "CAR", [
                {"id": "rb1", "name": "Bell Cow", "stat": "20"},
                {"id": "wr1", "name": "Jet Sweep", "stat": "2"},
                {"id": "qb1", "name": "Scrambler", "stat": "8"},        # QB keepers -> denominator only
                {"id": "-6047", "name": " Team", "stat": "3"},           # pseudo-athlete -> excluded
            ]),
            _cat("receiving", "REC", [
                {"id": "rb1", "name": "Bell Cow", "stat": "4"},          # same athlete, both cats -> summed
                {"id": "wr1", "name": "Jet Sweep", "stat": "6"},
                {"id": "te1", "name": "Seam Runner", "stat": "5"},
            ]),
            _cat("defensive", "TOT", [{"id": "lb1", "name": "Mike Backer", "stat": "11"}]),
        ]},
        {"team": "Utes", "homeAway": "away", "categories": [
            _cat("rushing", "CAR", [{"id": "rb9", "name": "Other Back", "stat": "15"}]),
            _cat("receiving", "REC", [{"id": "wr9", "name": "Other Wr", "stat": "7"}]),
        ]},
    ],
}
NAME_MAP = {"Aggies": 10, "Utes": 20}


if __name__ == "__main__":
    r = []

    tr = _touches_from_game(GAME, NAME_MAP)
    by_id = {x["player_id"]: x for x in tr if x["team_id"] == 10}

    r.append(check("rb1 touches = 20 CAR + 4 REC = 24", by_id["rb1"]["touches"] == 24))
    r.append(check("wr1 touches = 2 CAR + 6 REC = 8", by_id["wr1"]["touches"] == 8))
    r.append(check("te1 touches = 5 REC", by_id["te1"]["touches"] == 5))
    r.append(check("qb1 keeper touches counted (8)", by_id["qb1"]["touches"] == 8))
    r.append(check("' Team' pseudo-athlete is NOT a row", "-6047" not in by_id))
    r.append(check(
        "team_touches = 24 + 8 + 5 + 8 = 45 (QB in, Team pseudo out), same on every row",
        {x["team_touches"] for x in tr if x["team_id"] == 10} == {45},
    ))
    r.append(check("opponent_team_id resolved to the other team (20)", by_id["rb1"]["opponent_team_id"] == 20))
    r.append(check("Utes team_touches = 15 + 7 = 22", {x["team_touches"] for x in tr if x["team_id"] == 20} == {22}))

    # _all_box_score_athletes — every category, both teams
    allath = _all_box_score_athletes([GAME], NAME_MAP)
    r.append(check(
        "returning-set for team 10 includes the LB (any category) but not ' Team'",
        allath[10] == {"rb1", "wr1", "qb1", "te1", "lb1"},
    ))

    # end-to-end build with an injected fetch layer (monkeypatch the 3 fetchers)
    import role_momentum as RM

    RM.fetch_games = lambda s, w, **k: [
        {"id": 555, "homeId": 10, "homeTeam": "Aggies", "awayId": 20, "awayTeam": "Utes", "completed": True}
    ]
    RM.completed_games = lambda games: [g for g in games if g.get("completed")]
    RM.fetch_player_game_stats = lambda s, w, **k: [GAME]
    RM.fetch_player_ppa = lambda s, w, **k: {"rb1": 0.42, "wr1": 0.10, "rb9": 0.05, "wr9": 0.30}  # te1 missing PPA
    prior = {10: {"rb1", "te1"}, 20: {"rb9", "wr9"}}  # team 10: rb1/te1 returning, wr1 new; team 20 both returning

    rows, diag = build_role_momentum_weekly(
        2025, [3], prior_team_athletes=prior,
        position_lookup={"rb1": "RB", "wr1": "WR", "te1": "TE", "qb1": "QB", "rb9": "RB", "wr9": "WR"},
    )
    team10 = {x["player_id"]: x for x in rows if x["team_id"] == 10}

    r.append(check("only RB/WR/TE get rows for team 10 (qb1 excluded)", set(team10) == {"rb1", "wr1", "te1"}))
    r.append(check("both teams scored (5 rows: 3 + 2)", len(rows) == 5))
    r.append(check("rb1 touch_share = 24/45 = 0.5333", abs(team10["rb1"]["touch_share"] - 0.5333) < 1e-3))
    r.append(check("rb1 ppa joined (0.42)", team10["rb1"]["ppa"] == 0.42))
    r.append(check("te1 ppa is None (no PPA row)", team10["te1"]["ppa"] is None))
    r.append(check("rb1 is_returning True (in prior set)", team10["rb1"]["is_returning"] is True))
    r.append(check("wr1 is_returning False (not in prior set)", team10["wr1"]["is_returning"] is False))
    r.append(check("diagnostics ppa_join_rate = 4/5 (te1 the only miss)", abs(diag["ppa_join_rate"] - 0.8) < 1e-3))
    r.append(check("diagnostics returning_rate = 4/5 (only wr1 is new)", abs(diag["returning_rate"] - 0.8) < 1e-3))

    df = role_weekly_frame(rows)
    r.append(check("role_weekly_frame: te1 ppa is NaN (not None) after coercion", bool(df.set_index("player_id").loc["te1", "ppa"] != df.set_index("player_id").loc["te1", "ppa"])))

    print()
    p = sum(r)
    print(f"{p}/{len(r)} checks passed")
    raise SystemExit(0 if p == len(r) else 1)
