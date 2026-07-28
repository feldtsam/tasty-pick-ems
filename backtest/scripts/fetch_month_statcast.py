"""
Pull raw pitch-by-pitch Statcast data for a date range and derive a
batter-game outcome table: one row per (game, batter) with whether they
hit a home run that game, plus matchup context (opposing starter, handedness,
venue) needed to join against the season skill/matchup baselines.

Usage: python scripts/fetch_month_statcast.py 2023-06-01 2023-06-30
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


def fetch_raw(start_dt: str, end_dt: str) -> pd.DataFrame:
    print(f"Pulling Statcast pitch-level data {start_dt} -> {end_dt} (this can take a while)...")
    df = pb.statcast(start_dt=start_dt, end_dt=end_dt)
    print(f"Pulled {len(df)} pitches.")
    return df


def derive_batter_games(pitches: pd.DataFrame) -> pd.DataFrame:
    df = pitches.copy()
    df["batting_team"] = df.apply(
        lambda r: r["away_team"] if r["inning_topbot"] == "Top" else r["home_team"], axis=1
    )
    df["opp_team"] = df.apply(
        lambda r: r["home_team"] if r["inning_topbot"] == "Top" else r["away_team"], axis=1
    )

    # Starting pitcher per team per game = the pitcher who threw in inning 1 for that
    # team's half-inning. (at_bat_number is a single game-wide counter, not per-team,
    # so at_bat_number == 1 only ever catches the team that bats first — that was a bug.)
    starters = (
        df[df["inning"] == 1]
        .groupby(["game_pk", "opp_team"])["pitcher"]
        .first()
        .rename("opp_starter_id")
        .reset_index()
        .rename(columns={"opp_team": "batting_team"})
    )

    pa_events = df.dropna(subset=["events"]).copy()
    hr_flag = (
        pa_events.assign(is_hr=pa_events["events"] == "home_run")
        .groupby(["game_pk", "game_date", "batter"])["is_hr"]
        .max()
        .rename("hit_hr")
        .reset_index()
    )
    pa_count = (
        pa_events.groupby(["game_pk", "game_date", "batter"])["at_bat_number"]
        .nunique()
        .rename("pa_count")
        .reset_index()
    )
    meta = (
        df.groupby(["game_pk", "game_date", "batter"])
        .agg(
            stand=("stand", "first"),
            batting_team=("batting_team", "first"),
            opp_team=("opp_team", "first"),
            home_team=("home_team", "first"),
            away_team=("away_team", "first"),
        )
        .reset_index()
    )

    games = meta.merge(hr_flag, on=["game_pk", "game_date", "batter"]).merge(
        pa_count, on=["game_pk", "game_date", "batter"]
    )
    games = games.merge(starters, on=["game_pk", "batting_team"], how="left")

    starter_hand = (
        df[["pitcher", "p_throws"]].drop_duplicates().rename(
            columns={"pitcher": "opp_starter_id", "p_throws": "opp_starter_throws"}
        )
    )
    games = games.merge(starter_hand, on="opp_starter_id", how="left")

    # Statcast's `player_name` column is the PITCHER's name, not the batter's —
    # look up real batter names separately via the Chadwick register.
    unique_batters = games["batter"].dropna().unique().tolist()
    names = pb.playerid_reverse_lookup(unique_batters, key_type="mlbam")
    names["batter_name"] = names["name_first"].str.title() + " " + names["name_last"].str.title()
    names = names.rename(columns={"key_mlbam": "batter"})[["batter", "batter_name"]]
    games = games.merge(names, on="batter", how="left")

    games["hit_hr"] = games["hit_hr"].astype(int)
    return games.sort_values(["game_date", "game_pk", "batting_team"]).reset_index(drop=True)


if __name__ == "__main__":
    start_dt = sys.argv[1] if len(sys.argv) > 1 else "2023-06-01"
    end_dt = sys.argv[2] if len(sys.argv) > 2 else "2023-06-30"

    raw_path = RAW_DIR / f"statcast_{start_dt}_{end_dt}.parquet"
    if raw_path.exists():
        print(f"Loading cached raw pitch data from {raw_path}...")
        raw = pd.read_parquet(raw_path)
    else:
        raw = fetch_raw(start_dt, end_dt)
        raw.to_parquet(raw_path, index=False)
        print(f"Saved raw pitch data -> {raw_path}")

    games = derive_batter_games(raw)
    out_path = PROC_DIR / f"batter_games_{start_dt}_{end_dt}.parquet"
    games.to_parquet(out_path, index=False)
    print(f"Saved {len(games)} batter-game rows -> {out_path}")
    print(f"HR rate in sample: {games['hit_hr'].mean():.4f}")
