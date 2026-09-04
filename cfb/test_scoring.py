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
    score_role_momentum_cfb,
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
    # committee: steady mid usage, no scores (rz_t=3 keeps its cumulative
    # above min_cumulative_rz_touches_for_rate so it's scored on a real
    # proven_heat, not the thin-sample gate — the workhorse-vs-committee
    # differential is the point of this fixture)
    for wk in range(1, weeks + 1):
        rows.append(_player_row("cm", "Committee Back", "RB", 11, 21, 2025, wk,
                                rz_t=3, rz_td=0, gl_t=1, gl_td=0, i10_t=1, i10_td=0, share=0.20))
    # spot-touch back: tiny volume, scores most weeks -> pre-gate this rode
    # a 2-recent-TD count + a great TD/touch rate to the top of the board;
    # the thin-sample rate gate must neutralize it (the London Montgomery case)
    for wk in range(1, weeks + 1):
        rows.append(_player_row("spot", "Spot Back", "RB", 13, 23, 2025, wk,
                                rz_t=2, rz_td=1, gl_t=1, gl_td=1, i10_t=1, i10_td=1, share=0.15))
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


# --------------------------------------------------------------------------
# Role & Momentum synthetic season
# --------------------------------------------------------------------------
def _role_row(pid, name, pos, team_id, opp_id, season, week, *, touch_share, ppa, team_touches=50, is_returning=True):
    return {
        "player_id": pid, "player_name": name, "position_group": pos,
        "team_id": team_id, "team": f"T{team_id}", "opponent_team_id": opp_id, "opponent": f"T{opp_id}",
        "season": season, "week": week, "game_id": 3000 + week,
        "touches": round((touch_share or 0) * team_touches), "team_touches": team_touches,
        "touch_share": touch_share, "ppa": ppa, "is_returning": is_returning, "extra": {},
    }


# a rising touch-share trajectory shared by several test players
_RISING_TS = [0.10, 0.12, 0.15, 0.18, 0.22, 0.26, 0.30, 0.34]


def build_role_season(weeks=8):
    rows = []
    for w in range(1, weeks + 1):
        ts = _RISING_TS[w - 1]
        # A: rising touch share AND rising PPA
        rows.append(_role_row("A", "Rising Both", "RB", 10, 90, 2025, w, touch_share=ts, ppa=0.05 + 0.10 * w))
        # B: SAME rising touch share, PPA declining
        rows.append(_role_row("B", "Rising Touch Flat PPA", "RB", 11, 91, 2025, w, touch_share=ts, ppa=0.95 - 0.10 * w))
        # C vs D: identical moderate-rising line, only is_returning differs
        rows.append(_role_row("C_ret", "Returning Guy", "WR", 12, 92, 2025, w, touch_share=0.08 + 0.020 * w, ppa=0.20 + 0.05 * w, is_returning=True))
        rows.append(_role_row("D_new", "New To Team Guy", "WR", 13, 93, 2025, w, touch_share=0.08 + 0.020 * w, ppa=0.20 + 0.05 * w, is_returning=False))
        # E: rising touches, no PPA attributed ANY game -> renorm fallback
        rows.append(_role_row("E_noppa", "No PPA Guy", "RB", 14, 94, 2025, w, touch_share=ts, ppa=None))
    # fillers so the percentile scale spans a real range of both trends
    traj = [
        (lambda w: 0.32 - 0.02 * w, lambda w: 0.85 - 0.09 * w),   # falling / falling
        (lambda w: 0.20, lambda w: 0.30),                          # flat / flat
        (lambda w: 0.10 + 0.015 * w, lambda w: 0.10 + 0.05 * w),   # mild rising / rising
        (lambda w: 0.26 - 0.012 * w, lambda w: 0.50),              # mild falling / flat
        (lambda w: 0.15, lambda w: 0.95 - 0.08 * w),               # flat / falling
        (lambda w: 0.04 + 0.030 * w, lambda w: 0.25),              # rising / flat
        (lambda w: 0.22, lambda w: 0.05 + 0.06 * w),               # flat / rising
    ]
    for i, (tf, pf) in enumerate(traj):
        for w in range(1, weeks + 1):
            rows.append(_role_row(f"f{i}", f"Filler {i}", "WR" if i % 2 else "RB", 30 + i, 40 + i, 2025, w,
                                  touch_share=max(0.01, tf(w)), ppa=pf(w)))
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

    # pillar-wide thin-sample gate: the spot-touch back (cum rz_touches 10
    # < 15 by wk6, scores nearly every week) contributes NOTHING — BOTH
    # halves neutral-50, td_opportunity EXACTLY 50, completeness 0, gate
    # flag set. A score-sorted view must not surface it.
    r.append(check("spot-touch back proven_heat == 50.0 exactly (wk6)",
                   wk6.loc["spot", "proven_heat"] == 50.0))
    r.append(check("spot-touch back emerging_heat == 50.0 exactly (wk6) — gate is pillar-wide",
                   wk6.loc["spot", "emerging_heat"] == 50.0))
    r.append(check("spot-touch back td_opportunity == 50.0 exactly (not 57.5 from the combine)",
                   wk6.loc["spot", "td_opportunity"] == 50.0))
    r.append(check("spot-touch back completeness == 0.0 (every input gated)",
                   wk6.loc["spot", "td_opportunity_completeness"] == 0.0))
    r.append(check("spot-touch back td_opportunity_gated is True",
                   bool(wk6.loc["spot", "td_opportunity_gated"])))
    r.append(check("spot-touch back does NOT outscore the workhorse",
                   wk6.loc["spot", "td_opportunity"] < wk6.loc["wh", "td_opportunity"]))
    r.append(check("workhorse (cum rz_touches 25 by wk6) NOT gated — flag False, td_opp != 50",
                   (not bool(wk6.loc["wh", "td_opportunity_gated"]))
                   and abs(wk6.loc["wh", "td_opportunity"] - 50.0) > 1e-6))

    # sample-size context columns
    r.append(check("player_games_played: workhorse's wk6 row is game 6",
                   int(wk6.loc["wh", "player_games_played"]) == 6))
    r.append(check("cum_rz_touches_prior: spot back at wk6 = 2 touches x 5 prior games = 10",
                   int(wk6.loc["spot", "cum_rz_touches_prior"]) == 10))
    r.append(check("cum_rz_touches_prior: committee back at wk6 = 3 x 5 = 15 (at the gate, NOT gated)",
                   int(wk6.loc["cm", "cum_rz_touches_prior"]) == 15
                   and (not bool(wk6.loc["cm", "td_opportunity_gated"]))))

    # a player with zero history (debut week) -> mostly fallback, low completeness
    debut = build_player_season(weeks=6)
    debut = pd.concat([debut, pd.DataFrame([_player_row("new", "Debut Guy", "RB", 14, 24, 2025, 6, rz_t=8, rz_td=3, share=0.7)])], ignore_index=True)
    ds = score_td_opportunity_cfb(debut)
    newrow = ds[(ds["player_id"] == "new") & (ds["week"] == 6)].iloc[0]
    r.append(check("debut-week player: completeness low (<= 40) despite a big raw line",
                   newrow["td_opportunity_completeness"] <= 40))

    # ---- FBS-opponent filter ----------------------------------------
    from scoring import drop_non_fbs_opponent_rows
    mixed = build_player_season(weeks=6)
    fcs_row = _player_row("fcsgame", "vs FCS", "RB", 10, 999, 2025, 3, rz_t=6, rz_td=4, share=0.8)  # opp 999 = FCS
    mixed = pd.concat([mixed, pd.DataFrame([fcs_row])], ignore_index=True)
    filtered = drop_non_fbs_opponent_rows(mixed, fbs_team_ids={10, 11, 13, 20, 21, 23, 30, 31, 32, 33, 34, 35, 36, 37, 40, 41, 42, 43, 44, 45, 46, 47})
    r.append(check("drop_non_fbs_opponent_rows removes the FCS-opponent (opp 999) row",
                   not ((filtered["player_id"] == "fcsgame")).any()))
    r.append(check("drop_non_fbs_opponent_rows keeps every FBS-vs-FBS row",
                   len(filtered) == len(build_player_season(weeks=6))))

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
    r.append(check("v1 Situation: situation == defensive_matchup_vulnerability exactly (Environment deferred to v2)",
                   bool((dscored["situation"] == dscored["defensive_matchup_vulnerability"]).all())))
    r.append(check("v1 Situation: situation_completeness == defensive_matchup_completeness (no environment input)",
                   bool((dscored["situation_completeness"] == dscored["defensive_matchup_completeness"]).all())))
    r.append(check("v1 Situation: no environment_score column emitted",
                   "environment_score" not in dscored.columns))
    r.append(check("Situation blend renormalizes over present sub-components (0.7 weight -> 1.0), not a hardcoded pass-through",
                   bool(CONFIG["situation"]["sub_weights"]["defensive_matchup_vulnerability"] == 0.7
                        and "environment_score" in CONFIG["situation"]["sub_weights"])))
    r.append(check("dmv completeness climbs across the season (wk1 < wk6 for a stable matchup)",
                   dscored[(dscored.player_id == "vs_weak") & (dscored.week == 1)]["defensive_matchup_completeness"].iloc[0]
                   < dscored[(dscored.player_id == "vs_weak") & (dscored.week == 6)]["defensive_matchup_completeness"].iloc[0]))
    r.append(check("every RB facing weakD in wk6 shares one dmv value (matchup property, not player)",
                   dwk6[dwk6["opponent_team_id"] == 500]["defensive_matchup_vulnerability"].nunique() == 1))

    # ---- Role & Momentum --------------------------------------------
    rm = score_role_momentum_cfb(build_role_season(weeks=8))
    rwk8 = rm[rm["week"] == 8].set_index("player_id")
    rwk2 = rm[rm["week"] == 2].set_index("player_id")

    # football intuition: rising touch share AND rising PPA should score
    # meaningfully higher than the SAME rising touch share with fading PPA
    r.append(check(
        "rising touch+PPA (A) outscores rising-touch / fading-PPA (B) at wk8",
        rwk8.loc["A", "role_momentum"] > rwk8.loc["B", "role_momentum"],
    ))
    r.append(check(
        "  ...and the gap is material (>= 8 points)",
        rwk8.loc["A", "role_momentum"] - rwk8.loc["B", "role_momentum"] >= 8,
    ))
    r.append(check(
        "A and B share an IDENTICAL touch_share_trend percentile (same touch line)",
        abs(rwk8.loc["A", "_rm_touch_share_trend_pct"] - rwk8.loc["B", "_rm_touch_share_trend_pct"]) < 1e-6,
    ))
    r.append(check(
        "  ...so the whole gap comes from the PPA term (A's ppa_trend_pct >> B's)",
        rwk8.loc["A", "_rm_ppa_trend_pct"] - rwk8.loc["B", "_rm_ppa_trend_pct"] >= 30,
    ))

    # completeness modifier: returning vs new-to-team, identical line
    r.append(check(
        "returning (C) and new-to-team (D) with an identical line score IDENTICALLY",
        abs(rwk8.loc["C_ret", "role_momentum"] - rwk8.loc["D_new", "role_momentum"]) < 1e-6,
    ))
    r.append(check(
        "  ...but new-to-team completeness is the returning one * new_team_completeness_factor",
        abs(rwk8.loc["D_new", "role_momentum_completeness"]
            - rwk8.loc["C_ret", "role_momentum_completeness"]
            * CONFIG["role_momentum"]["new_team_completeness_factor"]) < 0.15,
    ))

    # renorm fallback: a player with no PPA at all -> 100% touch_share_trend
    r.append(check(
        "no-PPA player (E) is flagged _rm_ppa_renormed at wk8",
        bool(rwk8.loc["E_noppa", "_rm_ppa_renormed"]),
    ))
    r.append(check(
        "  ...and E's role_momentum == its touch_share_trend percentile exactly",
        abs(rwk8.loc["E_noppa", "role_momentum"] - rwk8.loc["E_noppa", "_rm_touch_share_trend_pct"]) < 1e-6,
    ))
    r.append(check(
        "renorm is rare — it fires for NO other player on this fixture",
        int(rm[rm["player_id"] != "E_noppa"]["_rm_ppa_renormed"].sum()) == 0,
    ))

    # cold start: both trends are _trend_delta, so weeks 1-4 are neutral-50
    r.append(check(
        "wk2: role_momentum is ~50 for everyone (both trends masked, no signal yet)",
        bool((rm[rm["week"] == 2]["role_momentum"].between(49.9, 50.1)).all()),
    ))
    r.append(check(
        "wk2: role_momentum_completeness is ~0 (no real trend input exists yet)",
        bool((rm[rm["week"] == 2]["role_momentum_completeness"] <= 1e-6).all()),
    ))
    r.append(check(
        "completeness climbs across the season (A: wk2 < wk8)",
        rwk2.loc["A", "role_momentum_completeness"] < rwk8.loc["A", "role_momentum_completeness"],
    ))
    r.append(check(
        "a both-trends-real returning player reaches full completeness (~100) by wk8",
        rwk8.loc["A", "role_momentum_completeness"] >= 99.0,
    ))

    # degrade cleanly if the ingestion layer omits a column
    bare = build_role_season(weeks=8).drop(columns=["ppa", "is_returning"])
    bs = score_role_momentum_cfb(bare)
    r.append(check(
        "no `ppa` column at all -> every row renorms to touch_share, nothing crashes",
        bool(bs[bs["week"] >= 5]["_rm_ppa_renormed"].all()),
    ))
    r.append(check(
        "no `is_returning` column -> no completeness discount (unknown != False)",
        bs[bs["week"] == 8].set_index("player_id").loc["A", "role_momentum_completeness"] >= 69.0,
    ))

    print()
    p = sum(r)
    print(f"{p}/{len(r)} checks passed")
    raise SystemExit(0 if p == len(r) else 1)
