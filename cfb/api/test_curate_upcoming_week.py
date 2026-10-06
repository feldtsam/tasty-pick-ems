"""
Fixture unit tests for the upcoming-week mode in cfb/api/curate_cfb_shelves.py.

    python3 cfb/api/test_curate_upcoming_week.py

Pure-function tests: curate_cfb_upcoming_week / build_upcoming_skeletons /
qualifying_upcoming_games / kickoff_utc_from_game take real DataFrames and
plain /games dicts in -- no network, no CFBD, no signed reads or writes.
Everything runs on a synthetic season 2099 (never a real season) so no
check can be confused with production data.

Also hosts the fixture builders the endpoint test
(test_curate_upcoming_endpoint.py) imports, and the function-level
backtest: score a fully-played week the normal way, then re-score it
through the upcoming path with its own real rows dropped, and compare.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from curate_cfb_shelves import (
    CFB_DEFENSE_REDZONE_ALLOWED_WEEKLY_TYPED_COLUMNS,
    CFB_PLAYER_REDZONE_WEEKLY_TYPED_COLUMNS,
    CFB_SHELF_ORDER,
    CFB_SHELF_SCORE_COLUMNS,
    UpcomingWeekConflict,
    assign_cfb_shelves,
    build_upcoming_skeletons,
    curate_cfb_shelves,
    curate_cfb_upcoming_week,
    kickoff_utc_from_game,
    qualifying_upcoming_games,
    shape_cfb_shelf_placement_rows,
)

SEASON = 2099  # synthetic, never real
TEAMS = {10: "Alpha", 11: "Bravo", 12: "Charlie", 13: "Delta", 14: "Echo", 15: "Golf", 16: "Hotel"}
NON_FBS = {99: "Fcs State"}
FBS_IDS = frozenset(TEAMS)
CONFERENCES = {10: "SEC", 12: "SEC", 15: "Big Ten", 16: "Big Ten", 11: "ACC", 13: "ACC", 14: "Big 12"}
AP_RANKS = {10: {"rank": 1, "school": "Alpha", "conference": "SEC"}, 15: {"rank": 5, "school": "Golf", "conference": "Big Ten"}}


def check(label, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    return bool(cond)


# --------------------------------------------------------------------------
# synthetic season 2099 -- 7 FBS teams, round-robin pairings, one bye/week
# --------------------------------------------------------------------------
def week_pairs(week: int) -> list:
    """Deterministic round-robin: rotate the team list by week, pair the
    ends inward, the middle team sits out. Returns [(home_id, away_id), ...]."""
    ids = sorted(TEAMS)
    k = week % len(ids)
    rotated = ids[k:] + ids[:k]
    return [(rotated[i], rotated[-1 - i]) for i in range(len(ids) // 2)]


def game_id_for(week: int, pair_index: int) -> int:
    return 7000 + week * 10 + pair_index


def _player_profiles(team_id: int) -> list:
    return [
        (f"p{team_id}_RB1", f"RB One {TEAMS[team_id]}", "RB", "volume"),
        (f"p{team_id}_RB2", f"RB Two {TEAMS[team_id]}", "RB", "thin"),
        (f"p{team_id}_WR1", f"WR One {TEAMS[team_id]}", "WR", "mid"),
    ]


def _counts(profile: str, team_id: int, week: int) -> dict:
    if profile == "volume":
        rz_t = 3 + (team_id % 3) + (week % 2)
        rz_td = 1 if week % 2 == 0 else 0
    elif profile == "thin":
        rz_t = 1 + (1 if week % 3 == 0 else 0)
        rz_td = 0
    else:
        rz_t = 2 + (week % 2)
        rz_td = 1 if week % 3 == 0 else 0
    gl_t = rz_t // 3
    i10_t = rz_t // 2
    return {
        "rz_touches": rz_t, "rz_rush_touches": rz_t if profile != "mid" else 0,
        "rz_target_touches": 0 if profile != "mid" else rz_t, "rz_tds": rz_td,
        "i10_touches": i10_t, "i10_rush_touches": i10_t if profile != "mid" else 0,
        "i10_target_touches": 0 if profile != "mid" else i10_t, "i10_tds": min(rz_td, i10_t),
        "gl_touches": gl_t, "gl_rush_touches": gl_t if profile != "mid" else 0,
        "gl_target_touches": 0 if profile != "mid" else gl_t, "gl_tds": min(rz_td, gl_t),
    }


def build_season(weeks: int) -> tuple:
    """(player_weekly, allowed_weekly) real rows for weeks 1..weeks, shaped
    exactly like the two raw tables' typed columns."""
    player_rows, allowed_rows = [], []
    for wk in range(1, weeks + 1):
        for idx, (home, away) in enumerate(week_pairs(wk)):
            gid = game_id_for(wk, idx)
            for team_id, opp_id in ((home, away), (away, home)):
                team_total = 0
                prows = []
                for pid, name, pos, profile in _player_profiles(team_id):
                    c = _counts(profile, team_id, wk)
                    team_total += c["rz_touches"]
                    prows.append({
                        "player_id": pid, "season": SEASON, "week": wk, "game_id": gid,
                        "team_id": team_id, "team": TEAMS[team_id],
                        "opponent_team_id": opp_id, "opponent": TEAMS[opp_id],
                        "player_name": name, "position_group": pos, **c,
                    })
                for r in prows:
                    r["team_rz_touches"] = team_total
                    r["rz_touch_share"] = round(r["rz_touches"] / team_total, 3)
                    player_rows.append(r)
                for pos in ("RB", "WR", "TE"):
                    rz_t = 4 + (team_id % 4) + (3 if pos == "RB" else 0) + (wk % 2)
                    rz_td = (team_id % 2) + (1 if wk % 3 == 0 else 0)
                    allowed_rows.append({
                        "team_id": team_id, "team": TEAMS[team_id], "position_group": pos,
                        "season": SEASON, "week": wk, "game_id": gid,
                        "opponent_team_id": opp_id, "opponent": TEAMS[opp_id],
                        "rz_touches_allowed": rz_t, "rz_rush_touches_allowed": rz_t, "rz_target_touches_allowed": 0,
                        "rz_tds_allowed": rz_td,
                        "i10_touches_allowed": rz_t // 2, "i10_rush_touches_allowed": rz_t // 2,
                        "i10_target_touches_allowed": 0, "i10_tds_allowed": min(rz_td, rz_t // 2),
                        "gl_touches_allowed": rz_t // 3, "gl_rush_touches_allowed": rz_t // 3,
                        "gl_target_touches_allowed": 0, "gl_tds_allowed": min(rz_td, rz_t // 3),
                    })
    players = pd.DataFrame(player_rows, columns=CFB_PLAYER_REDZONE_WEEKLY_TYPED_COLUMNS)
    allowed = pd.DataFrame(allowed_rows, columns=CFB_DEFENSE_REDZONE_ALLOWED_WEEKLY_TYPED_COLUMNS)
    return players, allowed


def games_for_week(week: int, *, completed: bool = False, start_date: str | None = "2099-10-24T19:30:00.000Z") -> list:
    """That week's round-robin pairs as CFBD-shaped /games dicts."""
    out = []
    for idx, (home, away) in enumerate(week_pairs(week)):
        out.append({
            "id": game_id_for(week, idx), "season": SEASON, "week": week, "seasonType": "regular",
            "completed": completed, "startDate": start_date, "startTimeTBD": start_date is None,
            "homeId": home, "homeTeam": TEAMS[home], "awayId": away, "awayTeam": TEAMS[away],
        })
    return out


# The week-8 games the main scenario uses: one normal, one already played,
# one against an FCS team, one with a TBD kickoff.
UPCOMING_WEEK = 8
UPCOMING_GAMES = [
    {"id": 8001, "completed": False, "startDate": "2099-10-24T19:30:00.000Z", "startTimeTBD": False,
     "homeId": 10, "homeTeam": "Alpha", "awayId": 12, "awayTeam": "Charlie"},
    {"id": 8002, "completed": True, "startDate": "2099-10-24T16:00:00.000Z", "startTimeTBD": False,
     "homeId": 11, "homeTeam": "Bravo", "awayId": 13, "awayTeam": "Delta"},
    {"id": 8003, "completed": False, "startDate": "2099-10-24T20:00:00.000Z", "startTimeTBD": False,
     "homeId": 14, "homeTeam": "Echo", "awayId": 99, "awayTeam": "Fcs State"},
    {"id": 8004, "completed": False, "startDate": "2099-10-24T00:00:00.000Z", "startTimeTBD": True,
     "homeId": 15, "homeTeam": "Golf", "awayId": 16, "awayTeam": "Hotel"},
]
EXPECTED_KICKOFF_8001 = "2099-10-24T19:30:00+00:00"


def empty_roles() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "player_id", "player_name", "position_group", "team_id", "team", "opponent_team_id", "opponent",
        "season", "week", "game_id", "touches", "team_touches", "touch_share", "ppa", "is_returning",
    ])


def run_backtest(weeks: int = 8) -> dict:
    """Score the fully-played final week normally, then through the upcoming
    path with that week's real rows dropped, and compare per player."""
    players, allowed = build_season(weeks)
    target = weeks
    real = curate_cfb_shelves(players, allowed, empty_roles(), SEASON, target, FBS_IDS)
    stub = curate_cfb_upcoming_week(
        players, allowed, empty_roles(), SEASON, target, FBS_IDS,
        games_for_week(target), allow_existing_target_rows=True,
    )
    cols = ["td_opportunity", "situation", "evidence_quality", "core_score", "tpe_score"]
    r = real["week_rows"].set_index("player_id")[cols + ["td_opportunity_gated"]]
    s = stub["week_rows"].set_index("player_id")[cols + ["td_opportunity_gated"]]
    common = sorted(set(r.index) & set(s.index))
    r, s = r.loc[common], s.loc[common]
    metrics = {"players_real": len(real["week_rows"]), "players_stub": len(stub["week_rows"]), "players_common": len(common)}
    for c in cols:
        a, b = r[c].astype(float).to_numpy(), s[c].astype(float).to_numpy()
        corr = float(np.corrcoef(a, b)[0, 1]) if np.std(a) > 0 and np.std(b) > 0 else float("nan")
        metrics[c] = {
            "corr": round(corr, 4), "mean_abs_diff": round(float(np.mean(np.abs(a - b))), 3),
            "max_abs_diff": round(float(np.max(np.abs(a - b))), 3),
            "within_0_5": int(np.sum(np.abs(a - b) <= 0.5)),
        }
    metrics["gated_agreement"] = int((r["td_opportunity_gated"] == s["td_opportunity_gated"]).sum())
    real_shelves = assign_cfb_shelves(real["week_rows"], AP_RANKS, CONFERENCES)
    stub_shelves = assign_cfb_shelves(stub["week_rows"], AP_RANKS, CONFERENCES)
    overlap = {}
    for name in CFB_SHELF_ORDER:
        a, b = set(real_shelves[name]["player_id"]), set(stub_shelves[name]["player_id"])
        overlap[name] = {"real": len(a), "stub": len(b), "same": len(a & b)}
    metrics["shelf_overlap"] = overlap
    return metrics


if __name__ == "__main__":
    r = []
    players, allowed = build_season(UPCOMING_WEEK - 1)  # weeks 1..7 real, week 8 unplayed
    roles = empty_roles()
    players_before = players.copy()
    allowed_before = allowed.copy()

    # ---- normal mode is untouched -----------------------------------------
    normal = curate_cfb_shelves(players, allowed, roles, SEASON, 7, FBS_IDS)
    normal_shelves = assign_cfb_shelves(normal["week_rows"], AP_RANKS, CONFERENCES)
    normal_rows = shape_cfb_shelf_placement_rows(normal_shelves)
    r.append(check(
        "normal mode: placement rows carry exactly the typed columns + extra -- no kickoff_utc key",
        len(normal_rows) > 0 and all(set(row.keys()) == set(CFB_SHELF_SCORE_COLUMNS + ["extra"]) for row in normal_rows),
    ))
    r.append(check(
        "normal mode: curate_cfb_shelves on a played week returns that week's real rows (rz_touches present)",
        len(normal["week_rows"]) > 0 and normal["week_rows"]["rz_touches"].notna().all(),
    ))

    # ---- kickoff helper ---------------------------------------------------
    r.append(check("kickoff: startDate + startTimeTBD false -> ISO UTC with offset",
                   kickoff_utc_from_game(UPCOMING_GAMES[0]) == EXPECTED_KICKOFF_8001))
    r.append(check("kickoff: startTimeTBD true -> None", kickoff_utc_from_game(UPCOMING_GAMES[3]) is None))
    r.append(check("kickoff: missing startDate -> None", kickoff_utc_from_game({"id": 1, "startTimeTBD": False}) is None))
    r.append(check("kickoff: unparseable startDate -> None, not a crash",
                   kickoff_utc_from_game({"startDate": "not a date", "startTimeTBD": False}) is None))
    r.append(check("kickoff: snake_case spelling (start_date/start_time_tbd) is also read",
                   kickoff_utc_from_game({"start_date": "2099-10-24T19:30:00Z", "start_time_tbd": False}) == EXPECTED_KICKOFF_8001))

    # ---- game qualification ----------------------------------------------
    qualifying, gdiag = qualifying_upcoming_games(UPCOMING_GAMES, FBS_IDS)
    r.append(check("games: completed game is skipped", gdiag["games_skipped_completed"] == 1 and 8002 not in {g["id"] for g in qualifying}))
    r.append(check("games: FBS-vs-FCS game is skipped", gdiag["games_skipped_non_fbs"] == 1 and 8003 not in {g["id"] for g in qualifying}))
    r.append(check("games: exactly the 2 real FBS-vs-FBS unplayed games qualify (8001, 8004)",
                   {g["id"] for g in qualifying} == {8001, 8004} and gdiag["games_in_week"] == 4))
    _, mdiag = qualifying_upcoming_games([{"id": 1, "completed": False}], FBS_IDS)
    r.append(check("games: a game missing homeId/awayId is counted malformed, not a crash", mdiag["games_skipped_malformed"] == 1))

    # ---- skeleton construction ------------------------------------------
    pskel, dskel, sdiag = build_upcoming_skeletons(players, qualifying, SEASON, UPCOMING_WEEK)
    r.append(check(
        "player skeletons: only players on the 4 playing teams (10,12,15,16), 3 each = 12",
        set(pskel["team_id"].astype(int)) == {10, 12, 15, 16} and len(pskel) == 12,
    ))
    r.append(check(
        "player skeletons: no player from the completed game's teams (11,13) or the FCS game's team (14)",
        not pskel["team_id"].astype(int).isin([11, 13, 14]).any(),
    ))
    r.append(check(
        "player skeletons: every count column is NaN, identity/schedule columns are filled",
        pskel[[c for c in CFB_PLAYER_REDZONE_WEEKLY_TYPED_COLUMNS if c.endswith(("_touches", "_tds"))]].isna().all().all()
        and pskel["rz_touch_share"].isna().all() and pskel["team_rz_touches"].isna().all()
        and pskel["opponent_team_id"].notna().all() and pskel["game_id"].notna().all()
        and (pskel["week"] == UPCOMING_WEEK).all() and (pskel["season"] == SEASON).all(),
    ))
    alpha_rb1 = pskel[pskel["player_id"] == "p10_RB1"].iloc[0]
    r.append(check(
        "player skeletons: Alpha's RB1 is slotted into game 8001 against Charlie (12)",
        int(alpha_rb1["game_id"]) == 8001 and int(alpha_rb1["opponent_team_id"]) == 12 and alpha_rb1["opponent"] == "Charlie",
    ))
    r.append(check(
        "defense skeletons: both teams of each qualifying game x RB/WR/TE = 12 rows, allowed counts NaN",
        len(dskel) == 12 and set(dskel["team_id"].astype(int)) == {10, 12, 15, 16}
        and sorted(set(dskel["position_group"])) == ["RB", "TE", "WR"]
        and dskel[[c for c in CFB_DEFENSE_REDZONE_ALLOWED_WEEKLY_TYPED_COLUMNS if c.endswith("_allowed")]].isna().all().all(),
    ))
    r.append(check("skeleton columns match the raw tables' typed columns exactly",
                   list(pskel.columns) == CFB_PLAYER_REDZONE_WEEKLY_TYPED_COLUMNS
                   and list(dskel.columns) == CFB_DEFENSE_REDZONE_ALLOWED_WEEKLY_TYPED_COLUMNS))

    # ---- refusal when the target week already has real rows ---------------
    players8, allowed8 = build_season(UPCOMING_WEEK)
    try:
        curate_cfb_upcoming_week(players8, allowed8, roles, SEASON, UPCOMING_WEEK, FBS_IDS, UPCOMING_GAMES)
        refused = False
    except UpcomingWeekConflict:
        refused = True
    r.append(check("upcoming refuses (UpcomingWeekConflict) when the target week already has real rows", refused))
    forced = curate_cfb_upcoming_week(
        players8, allowed8, roles, SEASON, UPCOMING_WEEK, FBS_IDS, UPCOMING_GAMES, allow_existing_target_rows=True,
    )
    r.append(check(
        "allow_existing_target_rows=True (tests only) drops the real target rows and reports how many",
        forced["upcoming"]["target_week_rows_dropped"]["players"] > 0
        and forced["week_rows"]["rz_touches"].isna().all(),
    ))

    # ---- the real upcoming run (weeks 1-7 real, week 8 stubbed) -----------
    up = curate_cfb_upcoming_week(players, allowed, roles, SEASON, UPCOMING_WEEK, FBS_IDS, UPCOMING_GAMES)
    wk = up["week_rows"]
    r.append(check("upcoming: result carries curate's keys plus upcoming diagnostics, kickoff map and games",
                   {"scored", "week_rows", "shelf_score_rows", "upcoming", "kickoff_by_game_id", "games"} <= set(up.keys())))
    r.append(check("upcoming: week_rows are exactly the 12 skeleton players, all with NaN current-game counts",
                   len(wk) == 12 and wk["rz_touches"].isna().all() and (wk["week"] == UPCOMING_WEEK).all()))
    r.append(check("upcoming: diagnostics report 12 player and 12 defense skeletons, 1 completed + 1 non-FBS skipped",
                   up["upcoming"]["player_skeletons"] == 12 and up["upcoming"]["defense_skeletons"] == 12
                   and up["upcoming"]["games_skipped_completed"] == 1 and up["upcoming"]["games_skipped_non_fbs"] == 1))
    r.append(check("upcoming: every stub row was scored (td_opportunity, situation, evidence_quality, tpe_score all real numbers)",
                   wk[["td_opportunity", "situation", "evidence_quality", "core_score", "tpe_score"]].notna().all().all()))
    r.append(check("upcoming: the opponent-defense join found a row at the target week (allowed_rz_tds_last1 is real on every stub row)",
                   "allowed_rz_tds_last1" in wk.columns and wk["allowed_rz_tds_last1"].notna().all()))

    # shift(1) semantics: the stub row inherits week 7's real values
    hist = players[players["player_id"] == "p10_RB1"].sort_values("week")
    stub_row = wk[wk["player_id"] == "p10_RB1"].iloc[0]
    r.append(check("upcoming: stub row's rz_touches_last1 equals the player's real week-7 rz_touches (shift(1), no leak of its own NaN)",
                   float(stub_row["rz_touches_last1"]) == float(hist["rz_touches"].iloc[-1])))
    r.append(check("upcoming: stub row's cum_rz_touches_prior equals the sum of weeks 1-7",
                   int(stub_row["cum_rz_touches_prior"]) == int(hist["rz_touches"].sum())))
    volume = wk[wk["player_id"].str.endswith("_RB1")]
    thin = wk[wk["player_id"].str.endswith("_RB2")]
    r.append(check("upcoming: cold-start gate still applies -- high-volume backs are ungated after 7 real weeks, thin backs stay gated at exactly 50",
                   (~volume["td_opportunity_gated"]).all() and thin["td_opportunity_gated"].all() and (thin["td_opportunity"] == 50.0).all()))

    # ---- nothing persisted / nothing mutated ------------------------------
    r.append(check("no leak: the input player frame is byte-identical after the upcoming run (no skeletons added to it)",
                   players.equals(players_before) and len(players) == len(players_before)))
    r.append(check("no leak: the input defense frame is byte-identical after the upcoming run",
                   allowed.equals(allowed_before)))
    r.append(check("no leak: shelf_score_rows (the only row output besides placements) contain only week-8 skeleton players, never raw-table rows",
                   all(row["week"] == UPCOMING_WEEK and (row.get("extra") or {}).get("rz_touches") is None
                       for row in up["shelf_score_rows"])))

    # ---- placements + kickoff -------------------------------------------
    shelves = assign_cfb_shelves(wk, AP_RANKS, CONFERENCES)
    placements = shape_cfb_shelf_placement_rows(shelves, kickoff_by_game_id=up["kickoff_by_game_id"])
    g8001 = [p for p in placements if p["game_id"] == 8001]
    g8004 = [p for p in placements if p["game_id"] == 8004]
    r.append(check("placements: rows exist for both qualifying games", len(g8001) > 0 and len(g8004) > 0))
    r.append(check("placements: game 8001 rows carry kickoff_utc from startDate",
                   all(p.get("kickoff_utc") == EXPECTED_KICKOFF_8001 for p in g8001)))
    r.append(check("placements: game 8004 rows (startTimeTBD) OMIT kickoff_utc entirely -- no null sent",
                   all("kickoff_utc" not in p for p in g8004)))
    r.append(check("placements: every row still has game_id, shelf and archetype",
                   all(p["game_id"] is not None and p["shelf"] in CFB_SHELF_ORDER and p["archetype"] for p in placements)))
    r.append(check("placements: without a kickoff map the same shelves shape identically to the normal mode (no kickoff_utc key)",
                   all("kickoff_utc" not in p for p in shape_cfb_shelf_placement_rows(shelves))))
    r.append(check("placements: Workhorses and Target Magnets stay empty (role/receiving ingestion absent), identity shelves fill",
                   shelves["workhorses"].empty and shelves["target_magnets"].empty
                   and len(shelves["sec_td_watch"]) > 0 and len(shelves["big_ten_td_watch"]) > 0 and len(shelves["top25_td_watch"]) > 0))

    # ---- rows after the target week (backtest-only situation) --------------
    players9, allowed9 = build_season(9)
    later = curate_cfb_upcoming_week(players9, allowed9, roles, SEASON, 8, FBS_IDS, games_for_week(8), allow_existing_target_rows=True)
    r.append(check("rows after the target week are dropped before scoring and counted",
                   later["upcoming"]["rows_after_target_dropped"]["players"] > 0 and (later["scored"]["week"] <= 8).all()))

    # ---- backtest ----------------------------------------------------------
    bt = run_backtest(8)
    print()
    print("BACKTEST (synthetic 2099, week 8 real vs. week 8 stubbed from weeks 1-7):")
    for k, v in bt.items():
        print(f"  {k}: {v}")
    print()
    r.append(check("backtest: every real week-8 player got a stub row", bt["players_common"] == bt["players_real"] == bt["players_stub"]))
    r.append(check("backtest: tpe_score correlation real vs stub >= 0.95", bt["tpe_score"]["corr"] >= 0.95))
    r.append(check("backtest: td_opportunity_gated agrees on every player", bt["gated_agreement"] == bt["players_common"]))

    print()
    p = sum(r)
    print(f"{p}/{len(r)} checks passed")
    raise SystemExit(0 if p == len(r) else 1)
