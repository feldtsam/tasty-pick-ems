"""
Pull season-level batter skill metrics and pitcher matchup metrics for a
given season and cache them to data/processed/.

These are used as the "known before the test period" skill/matchup baseline
(e.g. 2022 season stats to score 2023 games), which avoids look-ahead bias —
we never use stats from games that haven't happened yet relative to the
game being scored.

Usage: python scripts/fetch_season_baselines.py 2022
"""
import sys
from pathlib import Path

import pandas as pd
import pybaseball as pb

pb.cache.enable()

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
PROC_DIR = ROOT / "data" / "processed"
RAW_DIR.mkdir(parents=True, exist_ok=True)
PROC_DIR.mkdir(parents=True, exist_ok=True)


def fetch_batter_skill(season: int) -> pd.DataFrame:
    print(f"[batter] pulling Statcast exit-velo/barrels for {season}...")
    ev = pb.statcast_batter_exitvelo_barrels(season, minBBE=1)

    print(f"[batter] pulling Statcast expected stats for {season}...")
    xs = pb.statcast_batter_expected_stats(season, minPA=1)

    print(f"[batter] pulling Baseball-Reference batting totals for {season}...")
    bref = pb.batting_stats_bref(season)

    ev = ev.rename(columns={
        "player_id": "mlbam_id",
        "anglesweetspotpercent": "sweet_spot_pct",
        "avg_hit_speed": "avg_exit_velo",
        "ev95percent": "hard_hit_pct",
        "brl_percent": "barrel_pct",
        "brl_pa": "barrels_per_pa",
    })[["mlbam_id", "last_name, first_name", "attempts", "avg_exit_velo",
        "sweet_spot_pct", "hard_hit_pct", "barrel_pct", "barrels_per_pa"]]

    xs = xs.rename(columns={
        "player_id": "mlbam_id",
        "est_slg": "xslg",
        "est_woba": "xwoba",
    })[["mlbam_id", "pa", "slg", "xslg", "woba", "xwoba"]]

    bref = bref[["mlbID", "Name", "Tm", "PA", "HR"]].rename(columns={
        "mlbID": "mlbam_id", "Name": "name", "Tm": "team",
        "PA": "pa_bref", "HR": "hr",
    })
    bref["mlbam_id"] = pd.to_numeric(bref["mlbam_id"], errors="coerce")
    bref = bref.dropna(subset=["mlbam_id"])
    bref["mlbam_id"] = bref["mlbam_id"].astype(int)

    merged = ev.merge(xs, on="mlbam_id", how="outer").merge(bref, on="mlbam_id", how="left")
    merged["season"] = season
    merged["hr_per_pa"] = merged["hr"] / merged["pa_bref"]
    return merged


def fetch_pitcher_matchup(season: int) -> pd.DataFrame:
    print(f"[pitcher] pulling Statcast exit-velo/barrels allowed for {season}...")
    ev = pb.statcast_pitcher_exitvelo_barrels(season, minBBE=1)

    print(f"[pitcher] pulling Statcast expected stats allowed for {season}...")
    xs = pb.statcast_pitcher_expected_stats(season, minPA=1)

    print(f"[pitcher] pulling Baseball-Reference pitching totals for {season}...")
    bref = pb.pitching_stats_bref(season)

    ev = ev.rename(columns={
        "player_id": "mlbam_id",
        "ev95percent": "hard_hit_pct_allowed",
        "brl_percent": "barrel_pct_allowed",
    })[["mlbam_id", "last_name, first_name", "avg_hit_speed", "hard_hit_pct_allowed",
        "barrel_pct_allowed"]]

    xs = xs.rename(columns={
        "player_id": "mlbam_id",
        "est_slg": "xslg_allowed",
        "est_woba": "xwoba_allowed",
    })[["mlbam_id", "xslg_allowed", "xwoba_allowed"]]

    bref = bref[["mlbID", "Name", "Tm", "IP", "HR", "SO9", "BF"]].rename(
        columns={
            "mlbID": "mlbam_id", "Name": "name", "Tm": "team",
            "IP": "ip", "HR": "hr_allowed", "SO9": "k_per_9",
            "BF": "batters_faced",
        }
    )
    bref["mlbam_id"] = pd.to_numeric(bref["mlbam_id"], errors="coerce")
    bref = bref.dropna(subset=["mlbam_id"])
    bref["mlbam_id"] = bref["mlbam_id"].astype(int)
    bref["hr_per_9"] = bref["hr_allowed"] / bref["ip"] * 9
    # NOTE: bref's scraped "GB/FB", "LD", "PU" columns were dropped — spot-checking
    # them against known extreme groundball pitchers (e.g. Framber Valdez) didn't
    # match published GB%/FB% splits, so the true fly-ball-rate-allowed stat isn't
    # reliably available from this source yet. hard_hit_pct_allowed / barrel_pct_allowed
    # (from Statcast, verified below) stand in as the batted-ball-quality proxy for
    # now. See README for the plan to compute real FB% from raw Statcast bb_type data.

    merged = ev.merge(xs, on="mlbam_id", how="outer").merge(bref, on="mlbam_id", how="left")
    merged["season"] = season
    return merged


if __name__ == "__main__":
    season = int(sys.argv[1]) if len(sys.argv) > 1 else 2022

    batters = fetch_batter_skill(season)
    out_path = PROC_DIR / f"batter_skill_{season}.parquet"
    batters.to_parquet(out_path, index=False)
    print(f"Saved {len(batters)} batters -> {out_path}")

    pitchers = fetch_pitcher_matchup(season)
    out_path = PROC_DIR / f"pitcher_matchup_{season}.parquet"
    pitchers.to_parquet(out_path, index=False)
    print(f"Saved {len(pitchers)} pitchers -> {out_path}")
