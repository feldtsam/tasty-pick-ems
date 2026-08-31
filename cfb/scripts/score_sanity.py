"""
Real-data sanity check for cfb/scoring.py.

Pulls a real multi-week slice of the two ingestion tables from the
deployed CFB endpoint (preview_only + full_rows — no writes), assembles
them into whole-season frames, runs score_td_opportunity_cfb /
score_defensive_matchup_cfb, and prints the top/bottom 10 of each score
for the target week WITH the corresponding *_completeness alongside every
row, so "genuinely scores high" can be told apart from "still mostly
neutral-50 fallback".

Usage:
    CFB_URL=https://tasty-pick-ems-cfb.vercel.app \
    CFB_PIPELINE_SECRET=<PIPELINE_INCOMING_SECRET> \
    python3 cfb/scripts/score_sanity.py 2025 1 3    # season, first_week, target_week
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import requests

from scoring import score_defensive_matchup_cfb, score_td_opportunity_cfb

URL = os.environ.get("CFB_URL", "https://tasty-pick-ems-cfb.vercel.app").rstrip("/")
SECRET = os.environ.get("CFB_PIPELINE_SECRET") or os.environ.get("PIPELINE_INCOMING_SECRET")
ENDPOINT = f"{URL}/api/ingest-and-write-redzone"


def fetch_week(season: int, week: int) -> tuple[list[dict], list[dict]]:
    resp = requests.post(
        ENDPOINT,
        json={"season": season, "week": week, "preview_only": True, "full_rows": True},
        headers={"X-Pipeline-Secret": SECRET, "Content-Type": "application/json"},
        timeout=180,
    )
    resp.raise_for_status()
    d = resp.json()
    if d.get("status") != "ok":
        raise RuntimeError(f"week {week}: {d}")
    print(f"  week {week}: {d['games_completed']} games, "
          f"{len(d['player_rows_full'])} player rows, {len(d['defense_rows_full'])} defense rows, "
          f"td unmatched={d['player_diagnostics']['td_attribution']['unmatched']}")
    return d["player_rows_full"], d["defense_rows_full"]


def _fmt(df: pd.DataFrame, score_col: str, comp_col: str, cols: list[str]) -> str:
    show = df[cols + [score_col, comp_col]].copy()
    return show.to_string(index=False)


if __name__ == "__main__":
    if not SECRET:
        sys.exit("set CFB_PIPELINE_SECRET (or PIPELINE_INCOMING_SECRET)")
    season = int(sys.argv[1]) if len(sys.argv) > 1 else 2025
    first_week = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    target_week = int(sys.argv[3]) if len(sys.argv) > 3 else 3

    print(f"Fetching {season} weeks {first_week}-{target_week} from {ENDPOINT} ...")
    p_parts, d_parts = [], []
    for wk in range(first_week, target_week + 1):
        pr, dr = fetch_week(season, wk)
        p_parts += pr
        d_parts += dr

    players = pd.DataFrame(p_parts)
    defense = pd.DataFrame(d_parts)
    print(f"\nAssembled: {len(players)} player-rows, {len(defense)} defense-rows "
          f"across weeks {first_week}-{target_week}\n")

    # ---- score ----
    tdo = score_td_opportunity_cfb(players)
    dmv = score_defensive_matchup_cfb(players, defense)

    tdo_wk = tdo[tdo["week"] == target_week].copy()
    dmv_wk = dmv[dmv["week"] == target_week].copy()

    # ================= TD Opportunity =================
    print("=" * 78)
    print(f"TD OPPORTUNITY — {season} week {target_week}")
    print("=" * 78)
    pc = ["player_name", "position_group", "team", "opponent", "rz_touches", "rz_tds", "rz_touch_share"]

    rb = tdo_wk[tdo_wk["position_group"] == "RB"].sort_values("td_opportunity", ascending=False)
    print("\n--- Top 10 RB by td_opportunity (score, completeness) ---")
    print(_fmt(rb.head(10), "td_opportunity", "td_opportunity_completeness", pc))
    print("\n--- Bottom 10 RB by td_opportunity ---")
    print(_fmt(rb.tail(10), "td_opportunity", "td_opportunity_completeness", pc))

    allpos = tdo_wk.sort_values("td_opportunity", ascending=False)
    print("\n--- Top 10 ALL positions by td_opportunity ---")
    print(_fmt(allpos.head(10), "td_opportunity", "td_opportunity_completeness", pc))

    # ================= Defensive Matchup Vulnerability =================
    print("\n" + "=" * 78)
    print(f"DEFENSIVE MATCHUP VULNERABILITY — {season} week {target_week} (RB rows)")
    print("=" * 78)
    dc = ["opponent", "position_group", "player_name", "team"]
    rb_d = (
        dmv_wk[dmv_wk["position_group"] == "RB"]
        .drop_duplicates(subset=["opponent_team_id", "position_group"])
        .sort_values("defensive_matchup_vulnerability", ascending=False)
    )
    print("\n--- Top 10 defenses most vulnerable to RB (dmv, completeness) — one row per defense ---")
    print(_fmt(rb_d.head(10), "defensive_matchup_vulnerability", "defensive_matchup_completeness",
              ["opponent", "recent_tds_allowed_pct", "conversion_rate_allowed_pct"]))
    print("\n--- Bottom 10 (stingiest to RB) ---")
    print(_fmt(rb_d.tail(10), "defensive_matchup_vulnerability", "defensive_matchup_completeness",
              ["opponent", "recent_tds_allowed_pct", "conversion_rate_allowed_pct"]))

    # ---- quick automated intuition read ----
    print("\n" + "=" * 78)
    print("INTUITION CHECKS")
    print("=" * 78)
    real_rb = rb[rb["td_opportunity_completeness"] >= 50]
    if len(real_rb) >= 4:
        top = real_rb.head(3)["td_opportunity"].mean()
        bot = real_rb.tail(3)["td_opportunity"].mean()
        print(f"  RB td_opportunity (completeness>=50): top-3 mean {top:.1f} vs bottom-3 mean {bot:.1f} "
              f"-> {'PASS' if top - bot >= 10 else 'WEAK'}")
    real_d = rb_d[rb_d["defensive_matchup_completeness"] >= 50]
    if len(real_d) >= 4:
        top = real_d.head(3)["defensive_matchup_vulnerability"].mean()
        bot = real_d.tail(3)["defensive_matchup_vulnerability"].mean()
        print(f"  dmv vs RB (completeness>=50): top-3 mean {top:.1f} vs bottom-3 mean {bot:.1f} "
              f"-> {'PASS' if top - bot >= 10 else 'WEAK'}")
    print(f"\n  median td_opportunity_completeness (RB, wk{target_week}): {rb['td_opportunity_completeness'].median():.0f}")
    print(f"  median dmv_completeness (RB, wk{target_week}): {rb_d['defensive_matchup_completeness'].median():.0f}")
