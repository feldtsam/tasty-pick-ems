"""
Real-data sanity check for cfb/role_momentum.py + score_role_momentum_cfb.

Builds `cfb_player_role_weekly` for one real team-season straight from
CFBD (no deployed endpoint for this ingest yet), scores it, and prints
the checks that matter: the PPA join rate, the returning/new split, a
football-intuition ordering on real players, how often the PPA renorm
fires, and the early-season cold-start.

    CFBD_API_KEY=<key> python3 cfb/scripts/role_momentum_sanity.py "Ohio State" 2024
    # optional 3rd arg: last regular-season week to pull (default 14)
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from role_momentum import build_role_momentum_weekly, prior_season_team_athletes, role_weekly_frame
from scoring import CONFIG, score_role_momentum_cfb


def main() -> None:
    team = sys.argv[1] if len(sys.argv) > 1 else "Ohio State"
    season = int(sys.argv[2]) if len(sys.argv) > 2 else 2024
    last_week = int(sys.argv[3]) if len(sys.argv) > 3 else 14
    weeks = range(1, last_week + 1)

    if not os.environ.get("CFBD_API_KEY"):
        sys.exit("CFBD_API_KEY not set")

    print(f"prior-season ({season - 1}) box-score athlete walk for the returning flag ...")
    prior = prior_season_team_athletes(season - 1)
    print(f"  {len(prior)} teams, {sum(len(v) for v in prior.values())} (team, athlete) pairs")

    print(f"\nbuilding cfb_player_role_weekly — {team} {season} wk{weeks.start}-{weeks.stop - 1} ...")
    rows, diag = build_role_momentum_weekly(season, weeks, prior_team_athletes=prior)
    rows = [r for r in rows if r["team"] == team]
    print(f"  {len(rows)} rows for {team}   "
          f"ppa_join_rate(all-FBS)={diag['ppa_join_rate']}  "
          f"ppa_missing={diag['ppa_missing_rows']}  "
          f"returning_rate(all-FBS)={diag['returning_rate']}")

    df = role_weekly_frame(rows)
    scored = score_role_momentum_cfb(df)

    # ---- PPA join on the scored team's rows ----
    ppa_present = scored["ppa"].notna()
    print(f"\nPPA present on {ppa_present.mean():.3f} of {team}'s scored rows "
          f"({(~ppa_present).sum()} missing)")
    if (~ppa_present).any():
        print(scored[~ppa_present][["week", "player_name", "touches"]].to_string(index=False))

    renorm = scored["_rm_ppa_renormed"]
    print(f"PPA renorm fires on {renorm.mean():.3f} of rows ({int(renorm.sum())} of {len(scored)})")

    # ---- returning / new split ----
    by_player = (scored.sort_values("week")
                 .groupby(["player_id", "player_name"])
                 .agg(is_returning=("is_returning", "first"),
                      touches=("touches", "sum"),
                      last_rm=("role_momentum", "last"),
                      last_comp=("role_momentum_completeness", "last"))
                 .sort_values("touches", ascending=False))
    print("\nseason leaders (by total touches):")
    print(by_player.head(10).to_string())

    # ---- football-intuition ordering on real players ----
    wk_last = int(scored["week"].max())
    late = scored[scored["week"] >= wk_last - 3]
    prof = (late.groupby(["player_id", "player_name"])
            .agg(ts_pct=("_rm_touch_share_trend_pct", "mean"),
                 ppa_pct=("_rm_ppa_trend_pct", "mean"),
                 rm=("role_momentum", "mean"))
            .sort_values("rm", ascending=False))
    print(f"\nlate-season (wk {wk_last-3}-{wk_last}) profile — touch-trend pct / PPA-trend pct / role_momentum:")
    print(prof.head(12).round(1).to_string())

    # ---- cold start ----
    early = scored[scored["week"] <= CONFIG["role_momentum"]["trend_window"] + 1]
    print(f"\ncold start: weeks 1-{CONFIG['role_momentum']['trend_window'] + 1} — "
          f"mean role_momentum={early['role_momentum'].mean():.1f} "
          f"(expect ~50), mean completeness={early['role_momentum_completeness'].mean():.1f} (expect ~0)")

    out_dir = Path(__file__).resolve().parent.parent / "data"   # gitignored
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"role_momentum_sanity_{team.replace(' ', '_')}_{season}.json"
    scored.to_json(out, orient="records", indent=1)
    print(f"\nfull scored frame -> {out}")


if __name__ == "__main__":
    main()
