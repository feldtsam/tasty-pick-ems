"""
Fixture-based unit tests for cfb/redzone.py's two aggregations.

Same discipline / shape as nfl/test_*.py: a plain check()/__main__ script
(no pytest dependency), run with

    python3 cfb/test_redzone.py

The fixture is a hand-built one-week, two-game /plays/stats slice that
exercises every branch the two aggregations have: red-zone band
boundaries (rz/i10/gl), touch = Rush + Target (Reception ignored),
playId+athleteId TD attribution, QB red-zone rushing stashed in
extra.qb_rz (not typed rows), an unrostered athlete kept as a
NULL-position row, defense grouping by opponent + position, and the
unclassified (QB + unrostered) pool on the allowed table.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from redzone import aggregate_redzone_allowed_cfb, aggregate_redzone_game_cfb

GAMES = [
    {
        "id": 1, "season": 2025, "week": 3, "completed": True,
        "homeTeam": "Team A", "homeId": 100, "awayTeam": "Team B", "awayId": 200,
        "venueId": 55, "venue": "A Stadium", "neutralSite": False,
    }
]

RAW_POS = {"rb1": "RB", "wr1": "WR", "qb1": "QB", "rb2": "RB"}  # x1 deliberately absent


def _row(team, opp, play_id, athlete_id, stat_type, ytg):
    return {
        "gameId": 1, "season": 2025, "week": 3, "team": team, "opponent": opp,
        "conference": "Test", "playId": play_id, "athleteId": athlete_id,
        "athleteName": athlete_id.upper(), "statType": stat_type,
        "yardsToGoal": ytg, "stat": 1,
    }


PLAY_STATS = [
    # ---- Team A offense (defense faced = Team B) ----
    _row("Team A", "Team B", "p1", "rb1", "Rush", 8),      # rz + i10 (not gl)
    _row("Team A", "Team B", "p1", "rb1", "Touchdown", 8),
    _row("Team A", "Team B", "p2", "rb1", "Rush", 18),     # rz only
    _row("Team A", "Team B", "p3", "wr1", "Target", 5),    # rz + i10 + gl
    _row("Team A", "Team B", "p3", "wr1", "Reception", 5), # NOT a touch — must be ignored
    _row("Team A", "Team B", "p3", "wr1", "Touchdown", 5),
    _row("Team A", "Team B", "p4", "wr1", "Target", 45),   # outside the red zone — excluded
    _row("Team A", "Team B", "p5", "qb1", "Rush", 3),      # QB — excluded from typed rows
    _row("Team A", "Team B", "p5", "qb1", "Touchdown", 3),
    _row("Team A", "Team B", "p6", "x1", "Target", 12),    # unrostered — NULL position row
    # ---- Team B offense (defense faced = Team A) ----
    _row("Team B", "Team A", "p7", "rb2", "Rush", 4),      # rz + i10 + gl
    _row("Team B", "Team A", "p7", "rb2", "Touchdown", 4),
]


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return bool(condition)


if __name__ == "__main__":
    results = []

    player_rows, pdiag = aggregate_redzone_game_cfb(
        PLAY_STATS, GAMES, RAW_POS, season=2025, week=3
    )
    by_id = {r["player_id"]: r for r in player_rows}

    # ---- Aggregation A: cfb_player_redzone_weekly --------------------------
    results.append(check("player rows are rb1 / wr1 / x1 / rb2 — QB excluded",
                         set(by_id) == {"rb1", "wr1", "x1", "rb2"}))
    results.append(check("qb1 is NOT a typed player row", "qb1" not in by_id))

    rb1 = by_id.get("rb1", {})
    results.append(check("rb1 rz_touches = 2 (both rushes, ytg<=20)", rb1.get("rz_touches") == 2))
    results.append(check("rb1 rz_rush_touches = 2 / rz_target_touches = 0",
                         rb1.get("rz_rush_touches") == 2 and rb1.get("rz_target_touches") == 0))
    results.append(check("rb1 i10_touches = 1 (only ytg 8)", rb1.get("i10_touches") == 1))
    results.append(check("rb1 gl_touches = 0 (ytg 8 is not <=5)", rb1.get("gl_touches") == 0))
    results.append(check("rb1 rz_tds = 1, i10_tds = 1, gl_tds = 0",
                         rb1.get("rz_tds") == 1 and rb1.get("i10_tds") == 1 and rb1.get("gl_tds") == 0))
    results.append(check("rb1 position_group = RB", rb1.get("position_group") == "RB"))
    results.append(check("rb1 team_id = 100 (Team A), opponent_team_id = 200",
                         rb1.get("team_id") == 100 and rb1.get("opponent_team_id") == 200))

    wr1 = by_id.get("wr1", {})
    results.append(check("wr1 rz/i10/gl_touches all = 1 (ytg 5), ytg-45 target excluded",
                         wr1.get("rz_touches") == 1 and wr1.get("i10_touches") == 1 and wr1.get("gl_touches") == 1))
    results.append(check("wr1 rz_target_touches = 1 (Reception row ignored)", wr1.get("rz_target_touches") == 1))
    results.append(check("wr1 gl_tds = 1", wr1.get("gl_tds") == 1))

    x1 = by_id.get("x1", {})
    results.append(check("x1 position_group is NULL (unrostered)", x1.get("position_group") is None))
    results.append(check("x1 extra.unresolved is True", x1.get("extra", {}).get("unresolved") is True))
    results.append(check("x1 rz_touches = 1, i10_touches = 0 (ytg 12)",
                         x1.get("rz_touches") == 1 and x1.get("i10_touches") == 0))

    results.append(check("Team A team_rz_touches = 5 (rb1 2 + wr1 1 + x1 1 + qb1 1 — full pool, Step 1 Q5)",
                         rb1.get("team_rz_touches") == 5))
    results.append(check("rb1 rz_touch_share = 0.4 (2 / 5)", rb1.get("rz_touch_share") == 0.4))
    results.append(check("x1 rz_touch_share = 0.2 (1 / 5)", x1.get("rz_touch_share") == 0.2))

    qb_rz = rb1.get("extra", {}).get("qb_rz")
    results.append(check("rb1.extra.qb_rz present with rz_rush_touches = 1, rz_tds = 1",
                         isinstance(qb_rz, dict) and qb_rz.get("rz_rush_touches") == 1 and qb_rz.get("rz_tds") == 1))
    results.append(check("qb_rz gl_touches = 1 (ytg 3)", (qb_rz or {}).get("gl_touches") == 1))
    results.append(check("qb_rz athlete_ids = ['qb1']", (qb_rz or {}).get("athlete_ids") == ["qb1"]))

    rb2 = by_id.get("rb2", {})
    results.append(check("rb2 (Team B offense) rz/gl_touches = 1, rz_tds = 1",
                         rb2.get("rz_touches") == 1 and rb2.get("gl_touches") == 1 and rb2.get("rz_tds") == 1))
    results.append(check("rb2 team_id = 200, opponent_team_id = 100",
                         rb2.get("team_id") == 200 and rb2.get("opponent_team_id") == 100))
    results.append(check("rb2 has no extra.qb_rz (Team B had no QB red-zone touch)",
                         "qb_rz" not in rb2.get("extra", {})))

    results.append(check("player diag: TD attribution fully matched (unmatched = 0)",
                         pdiag["td_attribution"]["unmatched"] == 0))
    results.append(check("player diag: unresolved_athlete_ids = ['x1']",
                         pdiag["unresolved_athlete_ids"] == ["x1"]))

    # ---- Aggregation B: cfb_defense_redzone_allowed_weekly ----------------
    def_rows, ddiag = aggregate_redzone_allowed_cfb(
        PLAY_STATS, GAMES, RAW_POS, season=2025, week=3
    )
    by_def = {(r["team_id"], r["position_group"]): r for r in def_rows}

    results.append(check("defense rows keyed by (team_id, position_group) — only RB/WR/TE",
                         all(pg in ("RB", "WR", "TE") for (_, pg) in by_def)))
    results.append(check("Team B (200) allowed rows for RB and WR",
                         (200, "RB") in by_def and (200, "WR") in by_def))
    results.append(check("Team A (100) allowed a row for RB only",
                         (100, "RB") in by_def and (100, "WR") not in by_def))

    tb_rb = by_def.get((200, "RB"), {})
    results.append(check("Team B vs RB: rz_touches_allowed = 2, rz_tds_allowed = 1",
                         tb_rb.get("rz_touches_allowed") == 2 and tb_rb.get("rz_tds_allowed") == 1))
    results.append(check("Team B vs RB: opponent_team_id = 100 (offense faced)",
                         tb_rb.get("opponent_team_id") == 100))

    tb_wr = by_def.get((200, "WR"), {})
    results.append(check("Team B vs WR: rz_touches_allowed = 1 (ytg-45 excluded), gl_tds_allowed = 1",
                         tb_wr.get("rz_touches_allowed") == 1 and tb_wr.get("gl_tds_allowed") == 1))

    unclassified = tb_rb.get("extra", {}).get("unclassified")
    results.append(check("Team B extra.unclassified pools x1 + qb1: rz_touches = 2, rz_tds = 1",
                         isinstance(unclassified, dict)
                         and unclassified.get("rz_touches") == 2
                         and unclassified.get("rz_tds") == 1))
    qb_allowed = tb_rb.get("extra", {}).get("qb_rz_allowed")
    results.append(check("Team B extra.qb_rz_allowed present: rz_rush_touches = 1",
                         isinstance(qb_allowed, dict) and qb_allowed.get("rz_rush_touches") == 1))

    ta_rb = by_def.get((100, "RB"), {})
    results.append(check("Team A vs RB: rz/i10/gl_touches_allowed = 1, rz_tds_allowed = 1",
                         ta_rb.get("rz_touches_allowed") == 1
                         and ta_rb.get("i10_touches_allowed") == 1
                         and ta_rb.get("gl_touches_allowed") == 1
                         and ta_rb.get("rz_tds_allowed") == 1))

    # ---- TD attribution canary: a Touchdown row crediting a non-toucher --
    poisoned = PLAY_STATS + [_row("Team A", "Team B", "p8", "ghost", "Touchdown", 2)]
    _, pdiag2 = aggregate_redzone_game_cfb(poisoned, GAMES, RAW_POS, season=2025, week=3)
    results.append(check("TD canary: a Touchdown with no matching touch shows as unmatched = 1",
                         pdiag2["td_attribution"]["unmatched"] == 1))

    # ---- empty input ----------------------------------------------------
    empty_rows, ediag = aggregate_redzone_game_cfb([], GAMES, RAW_POS, season=2025, week=3)
    results.append(check("empty play_stats -> [] rows, no crash", empty_rows == []))

    print()
    passed = sum(results)
    print(f"{passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)
