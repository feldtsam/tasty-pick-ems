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
    CFB_SHELF_SCORE_COLUMNS,
    curate_cfb_shelves,
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

    print()
    p = sum(r)
    print(f"{p}/{len(r)} checks passed")
    raise SystemExit(0 if p == len(r) else 1)
