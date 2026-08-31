"""
Fixture-based unit tests for cfb/redzone.py's two aggregations.

Same discipline / shape as nfl/test_*.py: a plain check()/__main__ script
(no pytest dependency), run with

    python3 cfb/test_redzone.py

The fixture is a hand-built one-week, two-game /plays/stats slice that
exercises every branch: red-zone band boundaries (rz/i10/gl); touch =
Rush + Target + Reception (spec §8a — `Target` is an incomplete target,
`Reception` a completed one, both are pass "targets"); TD attribution
from an offensive-TD playId set (spec §8a — /plays, not
statType='Touchdown'), where only a rush or reception on a TD play
scores; QB red-zone rushing stashed in extra.qb_rz; an unrostered
athlete kept as a NULL-position row; defense grouping by opponent +
position with the unclassified (QB + unrostered) pool.
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

RAW_POS = {"rb1": "RB", "wr1": "WR", "wr2": "WR", "qb1": "QB", "rb2": "RB"}  # x1 deliberately absent

# offensive-TD playIds, as fetch_scoring_td_play_ids() would return them
TD_PLAY_IDS = {"p1", "p3", "p6", "p8"}


def _row(team, opp, play_id, athlete_id, stat_type, ytg):
    return {
        "gameId": 1, "season": 2025, "week": 3, "team": team, "opponent": opp,
        "conference": "Test", "playId": play_id, "athleteId": athlete_id,
        "athleteName": athlete_id.upper(), "statType": stat_type,
        "yardsToGoal": ytg, "stat": 1,
    }


PLAY_STATS = [
    # ---- Team A offense (defense faced = Team B) ----
    _row("Team A", "Team B", "p1", "rb1", "Rush", 8),        # rz + i10 ; TD play -> rb1 scores
    _row("Team A", "Team B", "p2", "rb1", "Rush", 18),       # rz only ; not a TD play
    _row("Team A", "Team B", "p3", "wr1", "Reception", 4),   # rz + i10 + gl ; TD play -> wr1 scores
    _row("Team A", "Team B", "p3", "qb1", "Completion", 4),  # QB side of the same play — never a touch
    _row("Team A", "Team B", "p4", "wr1", "Target", 6),      # rz + i10 ; incomplete target, cannot score
    _row("Team A", "Team B", "p5", "wr2", "Reception", 45),  # outside the red zone — excluded from bands
    _row("Team A", "Team B", "p6", "qb1", "Rush", 3),        # QB — excluded from typed rows ; TD play
    _row("Team A", "Team B", "p7", "x1", "Target", 12),      # unrostered — NULL position row
    # ---- Team B offense (defense faced = Team A) ----
    _row("Team B", "Team A", "p8", "rb2", "Rush", 4),        # rz + i10 + gl ; TD play -> rb2 scores
]


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return bool(condition)


if __name__ == "__main__":
    results = []

    player_rows, pdiag = aggregate_redzone_game_cfb(
        PLAY_STATS, GAMES, RAW_POS, TD_PLAY_IDS, season=2025, week=3
    )
    by_id = {r["player_id"]: r for r in player_rows}

    # ---- Aggregation A: cfb_player_redzone_weekly ------------------------
    results.append(check("player rows are rb1 / wr1 / x1 / rb2 — QB excluded, wr2 (outside RZ) absent",
                         set(by_id) == {"rb1", "wr1", "x1", "rb2"}))

    rb1 = by_id.get("rb1", {})
    results.append(check("rb1 rz_touches = 2 (both rushes)", rb1.get("rz_touches") == 2))
    results.append(check("rb1 i10_touches = 1 / gl_touches = 0 (ytg 8)",
                         rb1.get("i10_touches") == 1 and rb1.get("gl_touches") == 0))
    results.append(check("rb1 rz_tds = 1, i10_tds = 1, gl_tds = 0 (p1 is a TD play, ytg 8)",
                         rb1.get("rz_tds") == 1 and rb1.get("i10_tds") == 1 and rb1.get("gl_tds") == 0))

    wr1 = by_id.get("wr1", {})
    results.append(check("wr1 rz_touches = 2 (Reception + Target both count as touches — spec §8a)",
                         wr1.get("rz_touches") == 2))
    results.append(check("wr1 rz_rush_touches = 0 / rz_target_touches = 2",
                         wr1.get("rz_rush_touches") == 0 and wr1.get("rz_target_touches") == 2))
    results.append(check("wr1 i10_touches = 2 (ytg 4 + ytg 6), gl_touches = 1 (ytg 4 only)",
                         wr1.get("i10_touches") == 2 and wr1.get("gl_touches") == 1))
    results.append(check("wr1 rz_tds = 1 / gl_tds = 1 (p3 Reception on a TD play)",
                         wr1.get("rz_tds") == 1 and wr1.get("gl_tds") == 1))
    results.append(check("wr1 the incomplete Target (p4) did NOT score",
                         wr1.get("i10_tds") == 1))  # only p3 scored, and p3 is ytg4 <=10

    x1 = by_id.get("x1", {})
    results.append(check("x1 position_group NULL + extra.unresolved",
                         x1.get("position_group") is None and x1.get("extra", {}).get("unresolved") is True))
    results.append(check("x1 rz_touches = 1 (Target), i10_touches = 0 (ytg 12)",
                         x1.get("rz_touches") == 1 and x1.get("i10_touches") == 0))

    results.append(check("Team A team_rz_touches = 6 (rb1 2 + wr1 2 + x1 1 + qb1 1 — full pool incl QB, ytg<=20)",
                         rb1.get("team_rz_touches") == 6))
    results.append(check("rb1 rz_touch_share = 0.333 (2 / 6)", rb1.get("rz_touch_share") == 0.333))

    qb_rz = rb1.get("extra", {}).get("qb_rz")
    results.append(check("rb1.extra.qb_rz: rz_rush_touches = 1, rz_tds = 1 (p6 TD play)",
                         isinstance(qb_rz, dict) and qb_rz.get("rz_rush_touches") == 1 and qb_rz.get("rz_tds") == 1))

    rb2 = by_id.get("rb2", {})
    results.append(check("rb2 (Team B) rz/i10/gl_touches = 1, rz_tds = 1",
                         rb2.get("rz_touches") == 1 and rb2.get("gl_touches") == 1 and rb2.get("rz_tds") == 1))

    td = pdiag["td_attribution"]
    results.append(check("td_attribution.td_plays = 4", td["td_plays"] == 4))
    results.append(check("td_attribution.matched_to_a_touch = 4", td["matched_to_a_touch"] == 4))
    results.append(check("td_attribution.unmatched = 0 (every TD play has a rush/reception touch)",
                         td["unmatched"] == 0))
    results.append(check("stat_type_distribution present in diagnostics",
                         isinstance(pdiag.get("stat_type_distribution"), dict)
                         and pdiag["stat_type_distribution"].get("Rush") == 4))

    # ---- Aggregation B: cfb_defense_redzone_allowed_weekly --------------
    def_rows, ddiag = aggregate_redzone_allowed_cfb(
        PLAY_STATS, GAMES, RAW_POS, TD_PLAY_IDS, season=2025, week=3
    )
    by_def = {(r["team_id"], r["position_group"]): r for r in def_rows}

    results.append(check("defense rows only RB/WR/TE", all(pg in ("RB", "WR", "TE") for (_, pg) in by_def)))
    tb_rb = by_def.get((200, "RB"), {})
    results.append(check("Team B vs RB: rz_touches_allowed = 2, rz_tds_allowed = 1",
                         tb_rb.get("rz_touches_allowed") == 2 and tb_rb.get("rz_tds_allowed") == 1))
    tb_wr = by_def.get((200, "WR"), {})
    results.append(check("Team B vs WR: rz_touches_allowed = 2 (Reception+Target; ytg-45 excluded), gl_tds_allowed = 1",
                         tb_wr.get("rz_touches_allowed") == 2 and tb_wr.get("gl_tds_allowed") == 1))

    unclassified = tb_rb.get("extra", {}).get("unclassified")
    results.append(check("Team B extra.unclassified pools x1 + qb1: rz_touches = 2, rz_tds = 1 (qb1 p6)",
                         isinstance(unclassified, dict)
                         and unclassified.get("rz_touches") == 2
                         and unclassified.get("rz_tds") == 1))
    results.append(check("Team B extra.qb_rz_allowed present",
                         isinstance(tb_rb.get("extra", {}).get("qb_rz_allowed"), dict)))

    ta_rb = by_def.get((100, "RB"), {})
    results.append(check("Team A vs RB: rz/i10/gl_touches_allowed = 1, rz_tds_allowed = 1",
                         ta_rb.get("rz_touches_allowed") == 1
                         and ta_rb.get("i10_touches_allowed") == 1
                         and ta_rb.get("gl_touches_allowed") == 1
                         and ta_rb.get("rz_tds_allowed") == 1))

    # ---- TD canary: a TD play with no matching rush/reception touch -----
    _, pdiag2 = aggregate_redzone_game_cfb(
        PLAY_STATS, GAMES, RAW_POS, TD_PLAY_IDS | {"pGHOST"}, season=2025, week=3
    )
    results.append(check("TD canary: an offensive-TD playId with no touch shows unmatched = 1",
                         pdiag2["td_attribution"]["unmatched"] == 1))

    # ---- a Target-only play on a TD playId still cannot score ----------
    only_target = [_row("Team A", "Team B", "pT", "wr1", "Target", 3)]
    tr, _ = aggregate_redzone_game_cfb(only_target, GAMES, RAW_POS, {"pT"}, season=2025, week=3)
    results.append(check("an incomplete Target on a TD play scores 0",
                         tr and tr[0]["rz_tds"] == 0 and tr[0]["rz_touches"] == 1))

    # ---- empty input --------------------------------------------------
    empty_rows, _ = aggregate_redzone_game_cfb([], GAMES, RAW_POS, set(), season=2025, week=3)
    results.append(check("empty play_stats -> [] rows, no crash", empty_rows == []))

    print()
    passed = sum(results)
    print(f"{passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)
