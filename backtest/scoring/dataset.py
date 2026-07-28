"""
Merges the separately-pulled raw tables (batter-game outcomes, season
skill/matchup/bullpen baselines, park factors, game context) into one
scoring-ready dataframe, one row per batter-game.
"""
from pathlib import Path

import pandas as pd

PROC_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"

# Canonical full-season batter-game month_label per season, used as the
# calibration source for platoon/batting-order (always prior-season, never
# the period being scored/validated).
SEASON_MONTH_LABEL = {
    2022: "2022-04-07_2022-10-05",
    2023: "2023-03-30_2023-10-01",
}


def build_calibration_games(season: int) -> pd.DataFrame:
    """Lightweight batter_games + context merge for a season, used to
    calibrate anything that must come from a prior season rather than the
    period being scored/validated: the batting-order curve (needs
    batting_order_slot + pa_count) and the red-flag wind-in speed threshold
    (needs wind_speed_mph/wind_description/condition) — not the full
    skill/matchup/park/bullpen merge."""
    month_label = SEASON_MONTH_LABEL[season]
    games = pd.read_parquet(PROC_DIR / f"batter_games_{month_label}.parquet")
    context = pd.read_parquet(PROC_DIR / f"game_context_{month_label}.parquet")
    return games.merge(
        context[["game_pk", "batter", "batting_order_slot", "wind_speed_mph",
                  "wind_description", "condition"]],
        on=["game_pk", "batter"], how="left",
    )


def load_season_baselines(season: int) -> dict:
    return {
        "skill": pd.read_parquet(PROC_DIR / f"batter_skill_{season}.parquet"),
        "matchup": pd.read_parquet(PROC_DIR / f"pitcher_matchup_{season}.parquet"),
        "park": pd.read_parquet(PROC_DIR / f"park_factors_{season}.parquet"),
        "bullpen": pd.read_parquet(PROC_DIR / f"bullpen_quality_{season}.parquet"),
        "batted_ball": pd.read_parquet(PROC_DIR / f"batted_ball_profile_{season}.parquet"),
    }


def build_scoring_dataset(month_label: str, season: int) -> pd.DataFrame:
    games = pd.read_parquet(PROC_DIR / f"batter_games_{month_label}.parquet")
    context = pd.read_parquet(PROC_DIR / f"game_context_{month_label}.parquet")
    baselines = load_season_baselines(season)

    df = games.merge(
        baselines["skill"][["mlbam_id", "avg_exit_velo", "sweet_spot_pct", "hard_hit_pct",
                             "barrel_pct", "xslg", "xwoba", "hr_per_pa", "pa_bref"]],
        left_on="batter", right_on="mlbam_id", how="left",
    ).drop(columns=["mlbam_id"])

    df = df.merge(
        baselines["batted_ball"][["mlbam_id", "pull_pct", "fb_pct", "bbe_count"]],
        left_on="batter", right_on="mlbam_id", how="left",
    ).drop(columns=["mlbam_id"])

    df = df.merge(
        baselines["matchup"][["mlbam_id", "hard_hit_pct_allowed", "barrel_pct_allowed",
                               "xslg_allowed", "xwoba_allowed", "hr_per_9", "k_per_9", "ip"]],
        left_on="opp_starter_id", right_on="mlbam_id", how="left",
    ).drop(columns=["mlbam_id"])

    df = df.merge(
        baselines["park"][["venue_team", "park_factor_hr"]],
        left_on="home_team", right_on="venue_team", how="left",
    ).drop(columns=["venue_team"])

    df = df.merge(
        context[["game_pk", "batter", "batting_order_slot", "temp_f", "condition",
                 "wind_speed_mph", "wind_description"]],
        on=["game_pk", "batter"], how="left",
    )

    df = df.merge(
        baselines["bullpen"][["team", "bullpen_hr_per_pa", "bullpen_k_pct",
                               "bullpen_hard_hit_pct", "bullpen_pa"]],
        left_on="opp_team", right_on="team", how="left",
    ).drop(columns=["team"])

    return df
