"""
Real-data sanity check for cfb/scoring.py.

Pulls a real multi-week slice of the two ingestion tables from the
deployed CFB endpoint (preview_only + full_rows — no writes), drops
non-FBS-opponent rows (drop_non_fbs_opponent_rows — CFDB blowout stats
kept out of the reference / rolling windows), assembles them into
whole-season frames, runs score_td_opportunity_cfb /
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

from ids import fbs_team_ids
from scoring import CONFIG, drop_non_fbs_opponent_rows, score_defensive_matchup_cfb, score_td_opportunity_cfb

CONFIG_MIN = CONFIG["min_cumulative_rz_touches_for_rate"]

URL = os.environ.get("CFB_URL", "https://tasty-pick-ems-cfb.vercel.app").rstrip("/")
SECRET = os.environ.get("CFB_PIPELINE_SECRET") or os.environ.get("PIPELINE_INCOMING_SECRET")
ENDPOINT = f"{URL}/api/ingest-and-write-redzone"


def fetch_week(season: int, week: int, attempts: int = 4) -> tuple[list[dict], list[dict]]:
    last = None
    for a in range(1, attempts + 1):
        try:
            resp = requests.post(
                ENDPOINT,
                json={"season": season, "week": week, "preview_only": True, "full_rows": True},
                headers={"X-Pipeline-Secret": SECRET, "Content-Type": "application/json"},
                timeout=180,
            )
            if resp.status_code in (502, 503, 504):
                last = f"HTTP {resp.status_code}"
                print(f"  week {week}: {last} (attempt {a}/{attempts}) — retrying")
                continue
            resp.raise_for_status()
            d = resp.json()
            break
        except requests.RequestException as e:
            last = f"{type(e).__name__}: {e}"
            print(f"  week {week}: {last} (attempt {a}/{attempts}) — retrying")
    else:
        raise RuntimeError(f"week {week} failed after {attempts} attempts — {last}")
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
    raw_p, raw_d = len(players), len(defense)

    fbs = fbs_team_ids(season)
    players = drop_non_fbs_opponent_rows(players, fbs)
    defense = drop_non_fbs_opponent_rows(defense, fbs)
    print(f"\nFBS filter: player-rows {raw_p} -> {len(players)} "
          f"({raw_p - len(players)} non-FBS-opponent dropped), "
          f"defense-rows {raw_d} -> {len(defense)}")
    print(f"Assembled: {len(players)} player-rows, {len(defense)} defense-rows "
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
    pc = ["player_name", "position_group", "team", "opponent", "rz_touches", "rz_tds",
          "player_games_played", "cum_rz_touches_prior", "td_opportunity_gated"]

    rb = tdo_wk[tdo_wk["position_group"] == "RB"].sort_values("td_opportunity", ascending=False)
    ungated = rb[~rb["td_opportunity_gated"]]
    print(f"\n  RB rows this week: {len(rb)}  |  gated (cum RZ touches < "
          f"{CONFIG_MIN}): {int(rb['td_opportunity_gated'].sum())}  |  scored: {len(ungated)}")
    print("\n--- Top 10 UNGATED RB by td_opportunity (the view to actually use) ---")
    print(_fmt(ungated.head(10), "td_opportunity", "td_opportunity_completeness", pc))
    print("\n--- Bottom 10 UNGATED RB ---")
    print(_fmt(ungated.tail(10), "td_opportunity", "td_opportunity_completeness", pc))

    allpos = tdo_wk[~tdo_wk["td_opportunity_gated"]].sort_values("td_opportunity", ascending=False)
    print("\n--- Top 10 UNGATED, all positions ---")
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
    real_rb = ungated
    if len(real_rb) >= 4:
        top = real_rb.head(3)["td_opportunity"].mean()
        bot = real_rb.tail(3)["td_opportunity"].mean()
        corr = real_rb[["td_opportunity", "rz_touches"]].corr().iloc[0, 1]
        print(f"  RB td_opportunity (ungated only, n={len(real_rb)}): top-3 mean {top:.1f} vs "
              f"bottom-3 mean {bot:.1f} -> {'PASS' if top - bot >= 10 else 'WEAK'}   "
              f"corr(score, rz_touches) = {corr:+.2f}")
    real_d = rb_d[rb_d["defensive_matchup_completeness"] >= 50]
    if len(real_d) >= 4:
        top = real_d.head(3)["defensive_matchup_vulnerability"].mean()
        bot = real_d.tail(3)["defensive_matchup_vulnerability"].mean()
        print(f"  dmv vs RB (completeness>=50): top-3 mean {top:.1f} vs bottom-3 mean {bot:.1f} "
              f"-> {'PASS' if top - bot >= 10 else 'WEAK'}")
    print(f"\n  median td_opportunity_completeness (RB, wk{target_week}): {rb['td_opportunity_completeness'].median():.0f}")
    print(f"  median dmv_completeness (RB, wk{target_week}): {rb_d['defensive_matchup_completeness'].median():.0f}")
