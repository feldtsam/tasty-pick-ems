"""
Fixture unit tests for cfb/api/curate_cfb_shelves.py.

    python3 cfb/api/test_curate_cfb_shelves.py

Pure-function tests only — curate_cfb_shelves/shape_cfb_shelf_score_rows
take real DataFrames in, no network/signed-read calls involved (those are
exercised by _snapshot/_read_rows separately, and can't be tested without
a real secret + a real Lovable read route, neither of which exist yet —
see the module's own docstring). Reuses cfb/test_scoring.py's synthetic
season builders so every score this test checks is produced by the SAME
real scoring math test_scoring.py already validates, not a second,
possibly-drifting fixture.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from curate_cfb_shelves import (
    CFB_SHELF_ORDER,
    CFB_SHELF_SCORE_COLUMNS,
    add_shelf_convergence,
    assign_cfb_shelves,
    curate_cfb_shelves,
    select_cfb_tasty_six,
    shape_cfb_shelf_score_rows,
)
from test_scoring import build_defense_season, build_player_season, build_role_season


def check(label, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    return bool(cond)


def _fbs_ids(players, defense):
    """Every team_id/opponent_team_id already in the fixtures — the
    fixtures are internally FBS-only, so this makes drop_non_fbs_
    opponent_rows a no-op for these tests unless a test explicitly
    narrows it (see the FBS-filter check below)."""
    ids = set()
    for df in (players, defense):
        for col in ("team_id", "opponent_team_id"):
            if col in df.columns:
                ids |= set(int(x) for x in df[col].dropna().unique())
    return frozenset(ids)


if __name__ == "__main__":
    r = []

    players = build_player_season(weeks=8)
    defense = build_defense_season(weeks=8)
    roles = build_role_season(weeks=8)
    fbs_ids = _fbs_ids(players, defense)

    # ---- happy path: real columns, right shape --------------------------
    result = curate_cfb_shelves(players, defense, roles, season=2025, week=8, fbs_ids=fbs_ids)
    r.append(check(
        "curate_cfb_shelves returns scored/week_rows/shelf_score_rows",
        set(result.keys()) == {"scored", "week_rows", "shelf_score_rows"},
    ))
    r.append(check(
        "week_rows is only week 8 real rows",
        bool((result["week_rows"]["week"] == 8).all()) and len(result["week_rows"]) > 0,
    ))
    r.append(check(
        "scored carries every real pillar + evidence + tpe column",
        {"td_opportunity", "situation", "role_momentum", "evidence_quality", "core_score", "tpe_score"}
        <= set(result["scored"].columns),
    ))
    r.append(check(
        "shelf_score_rows count matches week_rows count",
        len(result["shelf_score_rows"]) == len(result["week_rows"]),
    ))
    r.append(check(
        "every shelf_score_row has exactly the typed columns + extra, nothing missing/extra",
        all(set(row.keys()) == set(CFB_SHELF_SCORE_COLUMNS + ["extra"]) for row in result["shelf_score_rows"]),
    ))

    # ---- Role & Momentum gap 2: genuinely empty role_weekly -------------
    empty_roles = pd.DataFrame(columns=roles.columns)
    result_no_rm = curate_cfb_shelves(players, defense, empty_roles, season=2025, week=8, fbs_ids=fbs_ids)
    r.append(check(
        "empty cfb_player_role_weekly -> role_momentum column present but entirely absent, no crash",
        "role_momentum" in result_no_rm["scored"].columns and result_no_rm["scored"]["role_momentum"].isna().all(),
    ))
    row = result_no_rm["scored"][result_no_rm["scored"]["week"] == 8].iloc[0]
    expected_core = round((row["td_opportunity"] * 53 + row["situation"] * 35) / 88, 1)
    r.append(check(
        "core_score renormalizes over td_opportunity(53)+situation(35)=88 when role_momentum is wholly absent",
        abs(row["core_score"] - expected_core) < 0.05,
    ))
    r.append(check(
        "tpe_score = core_score * confidence_multiplier even with role_momentum absent",
        abs(row["tpe_score"] - round(row["core_score"] * row["confidence_multiplier"], 1)) < 0.05,
    ))

    # ---- Role & Momentum present: real merge, all 3 pillars combine -----
    roles_matched = roles.copy()
    overlap_pid = players["player_id"].iloc[0]
    roles_matched.loc[roles_matched["player_id"] == roles_matched["player_id"].iloc[0], "player_id"] = overlap_pid
    result_rm = curate_cfb_shelves(players, defense, roles_matched, season=2025, week=8, fbs_ids=fbs_ids)
    matched_rows = result_rm["scored"][result_rm["scored"]["player_id"] == overlap_pid]
    r.append(check(
        "a player present in both the redzone AND role tables gets a real, non-null role_momentum",
        matched_rows["role_momentum"].notna().any(),
    ))
    real_rm_row = matched_rows[matched_rows["role_momentum"].notna()].iloc[0]
    expected_core_3pillar = round(
        (real_rm_row["td_opportunity"] * 53 + real_rm_row["situation"] * 35 + real_rm_row["role_momentum"] * 12) / 100, 1,
    )
    r.append(check(
        "core_score is the real 53/35/12 weighted blend when all 3 pillars are present",
        abs(real_rm_row["core_score"] - expected_core_3pillar) < 0.05,
    ))

    # ---- FBS-opponent filter really runs before scoring ------------------
    narrow_ids = frozenset(int(x) for x in players["team_id"].dropna().unique())  # excludes every real opponent_team_id
    result_narrow = curate_cfb_shelves(players, defense, roles, season=2025, week=8, fbs_ids=narrow_ids)
    r.append(check(
        "narrowing fbs_ids to exclude every opponent drops every row (drop_non_fbs_opponent_rows really ran)",
        len(result_narrow["week_rows"]) == 0,
    ))

    # ---- empty inputs degrade honestly, never crash ----------------------
    empty_players = pd.DataFrame(columns=players.columns)
    empty_defense = pd.DataFrame(columns=defense.columns)
    try:
        empty_result = curate_cfb_shelves(empty_players, empty_defense, empty_roles, season=2025, week=8, fbs_ids=fbs_ids)
        empty_ok = len(empty_result["week_rows"]) == 0 and empty_result["shelf_score_rows"] == []
    except Exception as e:  # noqa: BLE001 — the check itself is "did this raise"
        empty_ok = False
    r.append(check("wholly empty player/defense/role inputs degrade to zero rows, not a crash", empty_ok))

    # ---- Phase 5: 8-shelf assignment / convergence / Tasty Six -----------
    # Hand-built week_rows fixture, not run through the full scoring
    # chain -- isolates assign_cfb_shelves/add_shelf_convergence/
    # select_cfb_tasty_six's OWN logic from scoring.py's math (already
    # covered by test_scoring.py), same "test this layer directly" choice
    # test_redzone.py makes for its aggregations.
    def _wk_row(pid, name, team_id, team, pos, *, td_opp, td_gated, rm, rm_comp, tmag, tmag_gated, tpe):
        return {
            "player_id": pid, "player_name": name, "team_id": team_id, "team": team,
            "position_group": pos, "td_opportunity": td_opp, "td_opportunity_gated": td_gated,
            "role_momentum": rm, "role_momentum_completeness": rm_comp,
            "target_magnets": tmag, "target_magnets_gated": tmag_gated, "tpe_score": tpe,
        }

    shelf_rows = pd.DataFrame([
        # Goal-Line Favorites pool (2 ungated, 1 gated-excluded)
        _wk_row("glf1", "GLF One", 100, "TeamA", "RB", td_opp=90, td_gated=False, rm=10, rm_comp=100, tmag=10, tmag_gated=True, tpe=40),
        _wk_row("glf2", "GLF Two", 100, "TeamA", "RB", td_opp=70, td_gated=False, rm=10, rm_comp=100, tmag=10, tmag_gated=True, tpe=35),
        _wk_row("glf_gated", "GLF Gated", 100, "TeamA", "WR", td_opp=99, td_gated=True, rm=10, rm_comp=100, tmag=10, tmag_gated=True, tpe=5),
        # Workhorses pool (2 real, 1 thin-history-excluded via completeness==0)
        _wk_row("wh1", "WH One", 101, "TeamB", "RB", td_opp=10, td_gated=True, rm=95, rm_comp=100, tmag=10, tmag_gated=True, tpe=30),
        _wk_row("wh2", "WH Two", 101, "TeamB", "RB", td_opp=10, td_gated=True, rm=80, rm_comp=90, tmag=10, tmag_gated=True, tpe=25),
        _wk_row("wh_thin", "WH Thin", 101, "TeamB", "RB", td_opp=10, td_gated=True, rm=99, rm_comp=0, tmag=10, tmag_gated=True, tpe=20),
        # Target Magnets pool (2 real, 1 gated-excluded)
        _wk_row("tm1", "TM One", 102, "TeamC", "WR", td_opp=10, td_gated=True, rm=10, rm_comp=100, tmag=92, tmag_gated=False, tpe=45),
        _wk_row("tm2", "TM Two", 102, "TeamC", "WR", td_opp=10, td_gated=True, rm=10, rm_comp=100, tmag=88, tmag_gated=False, tpe=42),
        _wk_row("tm_gated", "TM Gated", 102, "TeamC", "WR", td_opp=10, td_gated=True, rm=10, rm_comp=100, tmag=99, tmag_gated=True, tpe=15),
        # "dual" -- ALSO Goal-Line Favorites' #1 by td_opportunity AND this
        # week's #1 AP-ranked-team player by tpe_score -- forces both a
        # real convergence hit AND a Tasty Six fallback (glf1 would
        # otherwise be claimed twice).
        _wk_row("dual", "Dual Star", 200, "RankedTeam", "RB", td_opp=95, td_gated=False, rm=10, rm_comp=100, tmag=10, tmag_gated=True, tpe=99),
        # SEC-conference player, not otherwise on any shelf
        _wk_row("sec1", "SEC One", 300, "SecTeam", "WR", td_opp=10, td_gated=True, rm=10, rm_comp=100, tmag=10, tmag_gated=True, tpe=60),
        # a team/conference this fixture never assigns -- must never appear anywhere
        _wk_row("nowhere", "Nowhere Guy", 999, "NoConfTeam", "RB", td_opp=10, td_gated=True, rm=10, rm_comp=100, tmag=10, tmag_gated=True, tpe=1),
    ])

    ap_ranks = {200: {"rank": 1, "school": "RankedTeam", "conference": "Independent"}}
    team_conference = {100: "Big Ten", 101: "Big Ten", 102: "ACC", 200: "Independent", 300: "SEC"}

    shelves = assign_cfb_shelves(shelf_rows, ap_ranks, team_conference, shelf_size=2)

    r.append(check(
        "assign_cfb_shelves returns exactly the 8 real shelf names",
        set(shelves.keys()) == set(CFB_SHELF_ORDER) and len(CFB_SHELF_ORDER) == 8,
    ))
    r.append(check(
        "goal_line_favorites: capped at 2, gated row excluded, ranked by td_opportunity",
        list(shelves["goal_line_favorites"]["player_id"]) == ["dual", "glf1"],
    ))
    r.append(check(
        "workhorses: thin-history (completeness==0) row excluded, ranked by role_momentum",
        list(shelves["workhorses"]["player_id"]) == ["wh1", "wh2"],
    ))
    r.append(check(
        "target_magnets: gated row excluded, ranked by target_magnets",
        list(shelves["target_magnets"]["player_id"]) == ["tm1", "tm2"],
    ))
    r.append(check(
        "top25_td_watch: only the AP-ranked team's player is eligible",
        list(shelves["top25_td_watch"]["player_id"]) == ["dual"],
    ))
    r.append(check(
        "sec_td_watch: only the real SEC-conference player is eligible",
        list(shelves["sec_td_watch"]["player_id"]) == ["sec1"],
    ))
    r.append(check(
        "big_ten_td_watch: TeamA/TeamB players eligible (Big Ten), ranked by tpe_score",
        list(shelves["big_ten_td_watch"]["player_id"]) == ["glf1", "glf2"],
    ))
    r.append(check(
        "acc_td_watch: TeamC players eligible (ACC)",
        set(shelves["acc_td_watch"]["player_id"]) == {"tm1", "tm2"},
    ))
    r.append(check(
        "big12_td_watch: no team in this fixture is Big 12 -- empty, not an error",
        shelves["big12_td_watch"].empty,
    ))
    r.append(check(
        "'nowhere' (no conference entry, not ranked, gated everywhere) appears on NO shelf",
        all("nowhere" not in shelves[s]["player_id"].values for s in CFB_SHELF_ORDER),
    ))
    r.append(check(
        "NO cross-shelf dedup: 'dual' legitimately appears on 2 different shelves at once",
        ("dual" in shelves["goal_line_favorites"]["player_id"].values)
        and ("dual" in shelves["top25_td_watch"]["player_id"].values),
    ))

    conv = add_shelf_convergence(shelf_rows, shelves)
    conv_by_id = conv.set_index("player_id")
    r.append(check(
        "convergence: 'dual' is on exactly 2 shelves, named correctly",
        conv_by_id.loc["dual", "shelf_count"] == 2
        and set(conv_by_id.loc["dual", "shelves"]) == {"goal_line_favorites", "top25_td_watch"},
    ))
    r.append(check(
        "convergence: a single-shelf player (glf2) shows shelf_count 1",
        conv_by_id.loc["glf2", "shelf_count"] == 1,
    ))
    r.append(check(
        "convergence: a player on no shelf ('nowhere') still gets a row -- shelf_count 0, not dropped",
        conv_by_id.loc["nowhere", "shelf_count"] == 0 and conv_by_id.loc["nowhere", "shelves"] == [],
    ))
    r.append(check(
        "convergence: total row count is unchanged (informational overlay, not a filter)",
        len(conv) == len(shelf_rows),
    ))

    tasty_six = select_cfb_tasty_six(shelves, n=6)
    tasty_ids = [p["player_id"] for p in tasty_six]
    r.append(check(
        "Tasty Six: no duplicate player across the six picks",
        len(tasty_ids) == len(set(tasty_ids)),
    ))
    r.append(check(
        "Tasty Six: 'dual' is claimed by goal_line_favorites (first in CFB_SHELF_ORDER) ...",
        any(p["player_id"] == "dual" and p["tasty_six_source"] == "goal_line_favorites" for p in tasty_six),
    ))
    r.append(check(
        "... so top25_td_watch's own pick falls back to its NEXT-eligible row -- "
        "but that shelf has only 'dual' in this fixture, so it contributes nothing (not backfilled)",
        not any(p["tasty_six_source"] == "top25_td_watch" for p in tasty_six),
    ))
    r.append(check(
        "Tasty Six: every OTHER shelf's own top pick is present (glf's runner-up wasn't needed)",
        {"glf1", "wh1", "tm1", "sec1"} <= set(tasty_ids),
    ))

    print()
    p = sum(r)
    print(f"{p}/{len(r)} checks passed")
    raise SystemExit(0 if p == len(r) else 1)
