"""
Score the June 2023 sample with the four-pillar model plus red-flag
penalties, and check whether higher scores actually correspond to higher
real HR rates.

Usage: python scripts/run_backtest.py 2023-06-01_2023-06-30 2022
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from scoring.config import CONFIG
from scoring.dataset import build_calibration_games, build_scoring_dataset, load_season_baselines
from scoring.model import score_dataset
from scoring.red_flags import NARRATIVE_ONLY_FLAG_COLUMNS, PENALTY_FLAG_COLUMNS

PROC_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"


def decile_validation(df: pd.DataFrame, score_col: str, label: str) -> pd.DataFrame:
    df = df.copy()
    df["decile"] = pd.qcut(df[score_col], 10, labels=False, duplicates="drop")
    summary = df.groupby("decile").agg(
        avg_score=(score_col, "mean"), hr_rate=("hit_hr", "mean"), n=("hit_hr", "size"),
    ).round(4)
    print(f"\n--- Validation: HR rate by {label} decile ---")
    print(summary.to_string())

    hr_rates = summary["hr_rate"].to_numpy()
    is_monotonic = all(hr_rates[i] <= hr_rates[i + 1] + 0.02 for i in range(len(hr_rates) - 1))
    lift = hr_rates[-1] / hr_rates[0]
    print(f"Roughly monotonic (allowing noise): {is_monotonic}  |  "
          f"Bottom: {hr_rates[0]:.4f}  |  Top: {hr_rates[-1]:.4f}  |  Lift: {lift:.2f}x")
    return summary


if __name__ == "__main__":
    month_label = sys.argv[1] if len(sys.argv) > 1 else "2023-06-01_2023-06-30"
    season = int(sys.argv[2]) if len(sys.argv) > 2 else 2022

    print(f"Building scoring dataset for {month_label} against {season} baselines...")
    df = build_scoring_dataset(month_label, season)
    baselines = load_season_baselines(season)

    raw_path = Path(__file__).resolve().parent.parent / "data" / "raw" / f"statcast_full_{season}.parquet"
    baseline_pitches = pd.read_parquet(raw_path)
    calibration_games = build_calibration_games(season)

    scored, calibration = score_dataset(
        df, baselines["skill"], baselines["matchup"], baselines["bullpen"], baselines["park"],
        baselines["batted_ball"], baseline_pitches, calibration_games, CONFIG
    )

    print(f"\nScored {len(scored)} batter-games.")

    print("\n--- Calibration (measured from prior-season data, not assumed) ---")
    p = calibration["platoon"]
    print(f"Same-hand HR rate:     {p['same_hand_hr_rate']:.4f}  (n={p['n_same']})")
    print(f"Opposite-hand HR rate: {p['opposite_hand_hr_rate']:.4f}  (n={p['n_opposite']})")
    print(f"Relative lift: {p['relative_lift']:+.3f}  ->  bonus_opposite={p['bonus_opposite']:+.2f} pts, "
          f"bonus_same={p['bonus_same']:+.2f} pts")
    print("\nBatting order -> avg PA that game (calibrated curve input):")
    for slot in sorted(calibration["avg_pa_by_slot"]):
        pa = calibration["avg_pa_by_slot"][slot]
        score = calibration["batting_order_curve"][slot]
        print(f"  slot {slot}: avg PA={pa:.2f}  ->  order_score={score:.1f}")
    print(f"\nWind-in red-flag minimum speed (2022 median among in-blowing games): "
          f"{calibration['wind_in_min_speed_mph']:.1f} mph")

    print("\n--- Pillar score summary ---")
    print(scored[["pillar_skill", "pillar_matchup", "pillar_environment", "pillar_opportunity",
                   "final_score", "final_score_rf"]].describe().round(1))

    print("\n--- Red flag trigger rates ---")
    print("  Penalty-affecting:")
    for col in PENALTY_FLAG_COLUMNS:
        print(f"    {col}: {scored[col].mean():.1%} of batter-games")
    print("  Narrative-only (computed, not scored):")
    for col in NARRATIVE_ONLY_FLAG_COLUMNS:
        print(f"    {col}: {scored[col].mean():.1%} of batter-games")
    print(f"  red_flag_count distribution:\n{scored['red_flag_count'].value_counts().sort_index().to_string()}")

    decile_validation(scored, "final_score", "final_score (no red flags)")
    decile_validation(scored, "final_score_rf", "final_score_rf (with red flags)")

    out_path = PROC_DIR / f"scored_{month_label}.parquet"
    scored.to_parquet(out_path, index=False)
    print(f"\nSaved scored dataset -> {out_path}")
