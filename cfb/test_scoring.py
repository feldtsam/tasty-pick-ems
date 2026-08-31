"""
Fixture unit tests for cfb/scoring.py.

    python3 cfb/test_scoring.py

Micro-tests on the copied math helpers (shrinkage identity, the trend
mask, the completeness = fallback-fraction contract, the permanent
snap_share fallback) + a small synthetic season that checks the two
football-intuition orderings: a workhorse red-zone back outscores a
committee back on td_opportunity, and a defense that has bled red-zone
TDs to RBs shows an elevated defensive_matchup_vulnerability for RB rows.

The real-data sanity check (2025 wk1-3) lives in
cfb/scripts/score_sanity.py — it needs live CFBD data and is run
separately.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd

from scoring import (
    CONFIG,
    _shrink_rate,
    _trend_delta,
    add_rolling_windows,
    score_defensive_matchup_cfb,
    score_td_opportunity_cfb,
)


def check(label, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    return bool(cond)


# --------------------------------------------------------------------------
# synthetic season builders
# --------------------------------------------------------------------------
def _player_row(pid, name, pos, team_id, opp_id, season, week, *, rz_t, rz_td, gl_t=0, gl_td=0, i10_t=0, i10_td=0, share=None):
    rz_rush = rz_t
    return {
        "player_id": pid, "player_name": name, "position_group": pos,
        "team_id": team_id, "team": f"T{team_id}", "opponent_team_id": opp_id, "opponent": f"T{opp_id}",
        "season": season, "week": week, "game_id": 1000 + week,
        "rz_touches": rz_t, "rz_rush_touches": rz_rush, "rz_target_touches": 0, "rz_tds": rz_td,
        "i10_touches": i10_t, "i10_rush_touches": i10_t, "i10_target_touches": 0, "i10_tds": i10_td,
        "gl_touches": gl_t, "gl_rush_touches": gl_t, "gl_target_touches": 0, "gl_tds": gl_td,
        "team_rz_touches": rz_t * 2, "rz_touch_share": share if share is not None else 0.5,
    }


def _allowed_row(team_id, pos, season, week, *, rz_t, rz_td, gl_t=0, gl_td=0, i10_t=0, i10_td=0):
    return {
        "team_id": team_id, "team": f"T{team_id}", "position_group": pos,
        "season": season, "week": week, "game_id": 2000 + week,
        "opponent_team_id": 999, "opponent": "T999",
        "rz_touches_allowed": rz_t, "rz_rush_touches_allowed": rz_t, "rz_target_touches_allowed": 0, "rz_tds_allowed": rz_td,
        "i10_touches_allowed": i10_t, "i10_rush_touches_allowed": i10_t, "i10_target_touches_allowed": 0, "i10_tds_allowed": i10_td,
        "gl_touches_allowed": gl_t, "gl_rush_touches_allowed": gl_t, "gl_target_touches_allowed": 0, "gl_tds_allowed": gl_td,
    }


def build_player_season(weeks=6):
    rows = []
    # workhorse: rising usage, real RZ TD production
    for wk in range(1, weeks + 1):
        rows.append(_player_row("wh", "Workhorse Back", "RB", 10, 20, 2025, wk,
                                rz_t=2 + wk, rz_td=1 + wk // 3, gl_t=1 + wk // 3, gl_td=wk // 3,
                                i10_t=1 + wk // 2, i10_td=wk // 3, share=0.62))
    # committee: flat low usage, no scores
    for wk in range(1, weeks + 1):
        rows.append(_player_row("cm", "Committee Back", "RB", 11, 21, 2025, wk,
                                rz_t=2, rz_td=0, gl_t=1, gl_td=0, i10_t=1, i10_td=0, share=0.20))
    # filler skill players so the percentile scale spans a real range
    for i, (rzt, rztd) in enumerate([(1, 0), (3, 1), (4, 0), (5, 1), (6, 2), (2, 0), (7, 2), (3, 0)]):
        for wk in range(1, weeks + 1):
            rows.append(_player_row(f"f{i}", f"Filler {i}", "WR" if i % 2 else "RB", 30 + i, 40 + i, 2025, wk,
                                    rz_t=rzt, rz_td=rztd, gl_t=rzt // 3, gl_td=rztd // 2,
                                    i10_t=rzt // 2, i10_td=rztd, share=0.25 + 0.03 * i))
    return pd.DataFrame(rows)


def build_defense_season(weeks=6):
    """weakD (500) bleeds RB RZ TDs; stingyD (600) allows none. Fillers span the scale."""
    rows = []
    for wk in range(1, weeks + 1):
        rows.append(_allowed_row(500, "RB", 2025, wk, rz_t=10, rz_td=2, gl_t=4, gl_td=2, i10_t=6, i10_td=2))
        rows.append(_allowed_row(600, "RB", 2025, wk, rz_t=8, rz_td=0, gl_t=3, gl_td=0, i10_t=4, i10_td=0))
        for j, (rzt, rztd) in enumerate([(9, 1), (7, 1), (11, 3), (6, 0), (8, 1), (10, 2)]):
            rows.append(_allowed_row(700 + j, "RB", 2025, wk, rz_t=rzt, rz_td=rztd,
                                     gl_t=rzt // 3, gl_td=rztd // 2, i10_t=rzt // 2, i10_td=rztd))
        # a WR row per defense so position filtering is exercised
        rows.append(_allowed_row(500, "WR", 2025, wk, rz_t=6, rz_td=1, gl_t=2, gl_td=0, i10_t=3, i10_td=1))
    return pd.DataFrame(rows)


if __name__ == "__main__":
    r = []

    # ---- micro-tests on the copied helpers -----------------------------
    zero = _shrink_rate(pd.Series([0.0]), pd.Series([0.0]), league_avg_rate=0.3, k=6)
    r.append(check("shrinkage identity: 0 tds / 0 touches -> exactly league_avg (0.3)", abs(zero.iloc[0] - 0.3) < 1e-9))
    some = _shrink_rate(pd.Series([3.0]), pd.Series([6.0]), league_avg_rate=0.2, k=6)
    r.append(check("shrinkage: 3/6 with k=6 @ 0.2 -> (3+1.2)/12 = 0.35", abs(some.iloc[0] - 0.35) < 1e-9))

    tdf = build_player_season(weeks=6).copy()
    tdf = add_rolling_windows(tdf, metrics=["rz_touch_share"], group_cols=["player_id", "season"])
    tdf = tdf.sort_values(["player_id", "season", "week"])
    d3 = _trend_delta(tdf, "rz_touch_share", 3)
    wh3 = d3[(tdf["player_id"] == "wh") & (tdf["week"] == 3)]
    wh6 = d3[(tdf["player_id"] == "wh") & (tdf["week"] == 6)]
    r.append(check("_trend_delta: masked to NaN at week 3 (<=3 prior games)", bool(wh3.isna().all())))
    r.append(check("_trend_delta: real value at week 6 (>3 prior games)", bool(wh6.notna().all())))

    # ---- TD Opportunity -----------------------------------------------
    scored = score_td_opportunity_cfb(build_player_season(weeks=6))
    wk6 = scored[scored["week"] == 6].set_index("player_id")

    r.append(check("snap_share_trend_pct is exactly 50.0 for every row (permanent fallback)",
                   bool((scored["snap_share_trend_pct"] == 50.0).all())))
    r.append(check("td_opportunity_completeness never exceeds 90 (snap_share always fallback)",
                   bool((scored["td_opportunity_completeness"] <= 90.0 + 1e-9).all())))
    r.append(check("workhorse td_opportunity > committee td_opportunity (wk6)",
                   wk6.loc["wh", "td_opportunity"] > wk6.loc["cm", "td_opportunity"]))
    r.append(check("workhorse proven_heat clearly higher than committee (wk6)",
                   wk6.loc["wh", "proven_heat"] - wk6.loc["cm", "proven_heat"] >= 15))
    r.append(check("workhorse completeness is a real, non-trivial number at wk6 (>= 60)",
                   wk6.loc["wh", "td_opportunity_completeness"] >= 60))

    # a player with zero history (debut week) -> mostly fallback, low completeness
    debut = build_player_season(weeks=6)
    debut = pd.concat([debut, pd.DataFrame([_player_row("new", "Debut Guy", "RB", 12, 22, 2025, 6, rz_t=8, rz_td=3, share=0.7)])], ignore_index=True)
    ds = score_td_opportunity_cfb(debut)
    newrow = ds[(ds["player_id"] == "new") & (ds["week"] == 6)].iloc[0]
    r.append(check("debut-week player: completeness low (<= 40) despite a big raw line",
                   newrow["td_opportunity_completeness"] <= 40))

    # ---- Defensive Matchup Vulnerability -----------------------------
    players = build_player_season(weeks=6)
    # add RB rows explicitly facing weakD (500) and stingyD (600) at wk6
    extra = pd.DataFrame([
        _player_row("vs_weak", "RB vs WeakD", "RB", 80, 500, 2025, w, rz_t=4, rz_td=1, share=0.5) for w in range(1, 7)
    ] + [
        _player_row("vs_stingy", "RB vs StingyD", "RB", 81, 600, 2025, w, rz_t=4, rz_td=1, share=0.5) for w in range(1, 7)
    ])
    players = pd.concat([players, extra], ignore_index=True)
    dscored = score_defensive_matchup_cfb(players, build_defense_season(weeks=6))
    dwk6 = dscored[dscored["week"] == 6].set_index("player_id")

    r.append(check("RB facing the TD-bleeding defense has higher dmv than RB facing the stingy one (wk6)",
                   dwk6.loc["vs_weak", "defensive_matchup_vulnerability"]
                   > dwk6.loc["vs_stingy", "defensive_matchup_vulnerability"]))
    r.append(check("that gap is material (>= 15)",
                   dwk6.loc["vs_weak", "defensive_matchup_vulnerability"]
                   - dwk6.loc["vs_stingy", "defensive_matchup_vulnerability"] >= 15))
    r.append(check("situation_completeness == defensive_matchup_completeness (placeholder)",
                   bool((dscored["situation_completeness"] == dscored["defensive_matchup_completeness"]).all())))
    r.append(check("dmv completeness climbs across the season (wk1 < wk6 for a stable matchup)",
                   dscored[(dscored.player_id == "vs_weak") & (dscored.week == 1)]["defensive_matchup_completeness"].iloc[0]
                   < dscored[(dscored.player_id == "vs_weak") & (dscored.week == 6)]["defensive_matchup_completeness"].iloc[0]))
    r.append(check("every RB facing weakD in wk6 shares one dmv value (matchup property, not player)",
                   dwk6[dwk6["opponent_team_id"] == 500]["defensive_matchup_vulnerability"].nunique() == 1))

    print()
    p = sum(r)
    print(f"{p}/{len(r)} checks passed")
    raise SystemExit(0 if p == len(r) else 1)
