"""
Compute a naive, single-season empirical HR park factor per venue directly
from real Statcast batted-ball data — no scraping, no guessed numbers.

Baseball Savant / FanGraphs publish smoothed, multi-year, player-mix-adjusted
park factors. This is a simpler, fully-reproducible version: for each venue,
compare the home-run rate on all batted balls hit there (by both teams) to
the league-wide rate. It does NOT adjust for which teams' hitters happen to
play there more often — documented limitation, see README.

    park_factor_hr = (venue HR / venue batted-ball-events)
                    / (league HR / league batted-ball-events) * 100

100 = league average, matching the Savant convention (>100 favors HR, <100
suppresses HR).

Uses PRIOR-season data (2022, matching the batter/pitcher skill baseline) to
score 2023 games — avoids look-ahead bias just like the other baselines.

Usage: python scripts/fetch_park_factors.py 2022-04-07 2022-10-05 2022
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


def fetch_full_season(start_dt: str, end_dt: str, season: int) -> pd.DataFrame:
    raw_path = RAW_DIR / f"statcast_full_{season}.parquet"
    if raw_path.exists():
        print(f"Loading cached raw pitch data from {raw_path}...")
        return pd.read_parquet(raw_path)

    print(f"Pulling full-season Statcast pitch-level data {start_dt} -> {end_dt}...")
    df = pb.statcast(start_dt=start_dt, end_dt=end_dt)
    print(f"Pulled {len(df)} pitches.")
    df.to_parquet(raw_path, index=False)
    print(f"Saved raw pitch data -> {raw_path}")
    return df


def compute_park_factors(pitches: pd.DataFrame) -> pd.DataFrame:
    bbe = pitches.dropna(subset=["bb_type"]).copy()  # batted-ball events only
    bbe["is_hr"] = bbe["events"] == "home_run"

    by_venue = (
        bbe.groupby("home_team")
        .agg(batted_balls=("is_hr", "size"), home_runs=("is_hr", "sum"))
        .reset_index()
        .rename(columns={"home_team": "venue_team"})
    )
    by_venue["hr_rate"] = by_venue["home_runs"] / by_venue["batted_balls"]

    league_hr_rate = bbe["is_hr"].sum() / len(bbe)
    by_venue["park_factor_hr"] = (by_venue["hr_rate"] / league_hr_rate * 100).round(1)
    by_venue["league_hr_rate"] = league_hr_rate

    return by_venue.sort_values("park_factor_hr", ascending=False).reset_index(drop=True)


if __name__ == "__main__":
    start_dt = sys.argv[1] if len(sys.argv) > 1 else "2022-04-07"
    end_dt = sys.argv[2] if len(sys.argv) > 2 else "2022-10-05"
    season = int(sys.argv[3]) if len(sys.argv) > 3 else 2022

    raw = fetch_full_season(start_dt, end_dt, season)
    park_factors = compute_park_factors(raw)

    out_path = PROC_DIR / f"park_factors_{season}.parquet"
    park_factors.to_parquet(out_path, index=False)
    park_factors.to_csv(PROC_DIR / f"park_factors_{season}.csv", index=False)
    print(f"Saved {len(park_factors)} venues -> {out_path}")
    print(park_factors.to_string(index=False))
