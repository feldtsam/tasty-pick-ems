"""
Compute team bullpen quality (pillar 4 input) directly from real Statcast
data, for the same reason park factors are computed rather than scraped:
Baseball-Reference's team pitching tables use ambiguous full city names
("Los Angeles", "New York", "Chicago") that don't distinguish Dodgers from
Angels, Yankees from Mets, or Cubs from White Sox — unusable for team
aggregation. Statcast's home_team/away_team codes are unambiguous, so we
derive everything from there instead.

Definition: a pitch belongs to "the bullpen" if the pitcher throwing it is
not that team's starter for that specific game (starter = whoever pitched
in inning 1 for that team — same definition used in fetch_month_statcast.py
for opposing-starter detection).

Uses PRIOR-season data (2022) to score 2023 games, same as the other
baselines — avoids look-ahead bias.

Usage: python scripts/fetch_bullpen_quality.py 2022
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
PROC_DIR = ROOT / "data" / "processed"
PROC_DIR.mkdir(parents=True, exist_ok=True)


def compute_bullpen_quality(pitches: pd.DataFrame) -> pd.DataFrame:
    df = pitches.copy()
    df["pitching_team"] = np.where(df["inning_topbot"] == "Top", df["home_team"], df["away_team"])

    starters = (
        df[df["inning"] == 1]
        .groupby(["game_pk", "pitching_team"])["pitcher"]
        .first()
        .rename("starter_id")
        .reset_index()
    )
    df = df.merge(starters, on=["game_pk", "pitching_team"], how="left")
    bullpen = df[df["pitcher"] != df["starter_id"]].copy()

    pa = bullpen.dropna(subset=["events"]).copy()
    pa["is_hr"] = pa["events"] == "home_run"
    pa["is_k"] = pa["events"] == "strikeout"
    pa["is_bb"] = pa["events"] == "walk"

    bbe = bullpen.dropna(subset=["bb_type"]).copy()
    bbe["is_hard_hit"] = bbe["launch_speed"] >= 95

    pa_agg = pa.groupby("pitching_team").agg(
        bullpen_pa=("is_hr", "size"),
        bullpen_hr=("is_hr", "sum"),
        bullpen_k=("is_k", "sum"),
        bullpen_bb=("is_bb", "sum"),
    )
    bbe_agg = bbe.groupby("pitching_team").agg(
        bullpen_bbe=("is_hard_hit", "size"),
        bullpen_hard_hit=("is_hard_hit", "sum"),
    )

    team = pa_agg.join(bbe_agg, how="left").reset_index().rename(columns={"pitching_team": "team"})
    team["bullpen_hr_per_pa"] = team["bullpen_hr"] / team["bullpen_pa"]
    team["bullpen_k_pct"] = team["bullpen_k"] / team["bullpen_pa"]
    team["bullpen_bb_pct"] = team["bullpen_bb"] / team["bullpen_pa"]
    team["bullpen_hard_hit_pct"] = (team["bullpen_hard_hit"] / team["bullpen_bbe"] * 100).round(1)

    return team.sort_values("bullpen_hr_per_pa").reset_index(drop=True)


if __name__ == "__main__":
    season = int(sys.argv[1]) if len(sys.argv) > 1 else 2022

    raw_path = RAW_DIR / f"statcast_full_{season}.parquet"
    if not raw_path.exists():
        print(f"No cached full-season pull at {raw_path}. Run fetch_park_factors.py first.")
        sys.exit(1)

    print(f"Loading cached raw pitch data from {raw_path}...")
    raw = pd.read_parquet(raw_path)

    bullpen = compute_bullpen_quality(raw)
    out_path = PROC_DIR / f"bullpen_quality_{season}.parquet"
    bullpen.to_parquet(out_path, index=False)
    bullpen.to_csv(PROC_DIR / f"bullpen_quality_{season}.csv", index=False)
    print(f"Saved {len(bullpen)} teams -> {out_path}")
    print(bullpen.to_string(index=False))
