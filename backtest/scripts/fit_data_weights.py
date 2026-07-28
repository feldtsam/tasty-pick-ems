"""
Fit a logistic regression on 2022 batter-games to see whether data-derived
weights beat the hand-set pillar weights — and, per-metric, what the data
says about Pull%/FB%'s marginal contribution specifically.

Methodology (same principle that caught the platoon-bonus bug): fit on
2022, validate out-of-sample on 2023. The regression is trained on the
individual normalized features from scoring/features.py (not the
pre-aggregated pillar scores), so each metric — including Pull% and FB% —
gets its own coefficient.

Known limitation, documented rather than silently accepted: the training
step itself uses 2022's own season aggregates (skill/matchup/park/bullpen)
to explain 2022 game outcomes, which is mildly self-referential (a
September home run partly informs the season aggregate used to explain a
June game). This wasn't solved by pulling a 2021 baseline — that's flagged
as a possible follow-up, not done here. It does NOT affect the actual
out-of-sample test: scoring 2023 always uses the 2022 baseline, exactly
like the hand-set model, so the comparison that matters is clean.

Usage: python scripts/fit_data_weights.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
import statsmodels.api as sm

from scoring import calibrate, pillars
from scoring.config import CONFIG
from scoring.dataset import build_calibration_games, build_scoring_dataset, load_season_baselines
from scoring.features import PILLAR_FEATURE_GROUPS, build_feature_matrix

ROOT = Path(__file__).resolve().parent.parent
PROC_DIR = ROOT / "data" / "processed"
RAW_DIR = ROOT / "data" / "raw"

TRAIN_SEASON = 2022
TRAIN_MONTH_LABEL = "2022-04-07_2022-10-05"
TEST_MONTH_LABEL = "2023-03-30_2023-10-01"


def decile_validation(df: pd.DataFrame, score_col: str) -> pd.DataFrame:
    df = df.copy()
    df["decile"] = pd.qcut(df[score_col], 10, labels=False, duplicates="drop")
    return df.groupby("decile").agg(
        avg_score=(score_col, "mean"), hr_rate=("hit_hr", "mean"), n=("hit_hr", "size"),
    ).round(4)


if __name__ == "__main__":
    print(f"Building 2022 training dataset ({TRAIN_MONTH_LABEL})...")
    train_df = build_scoring_dataset(TRAIN_MONTH_LABEL, TRAIN_SEASON)
    baselines = load_season_baselines(TRAIN_SEASON)
    scales = pillars.build_reference_scales(
        baselines["skill"], baselines["matchup"], baselines["bullpen"], baselines["park"],
        baselines["batted_ball"], CONFIG,
    )
    calibration_games = build_calibration_games(TRAIN_SEASON)
    order_curve, _ = calibrate.compute_batting_order_curve(calibration_games)

    train_features = build_feature_matrix(train_df, scales, CONFIG, order_curve)
    y_train = train_df["hit_hr"]

    print(f"Fitting logistic regression on {len(train_df)} 2022 batter-games, {train_features.shape[1]} features...")
    X_train = sm.add_constant(train_features)
    fit = sm.Logit(y_train, X_train).fit(disp=0)

    print("\n--- Fitted coefficients (2022 training data) ---")
    summary = pd.DataFrame({
        "coef": fit.params,
        "std_err": fit.bse,
        "p_value": fit.pvalues,
    }).round(4)
    print(summary.to_string())
    cond_no = np.linalg.cond(X_train.to_numpy(dtype=float))
    print(f"\nCondition number: {cond_no:.1f} (>30 is a conventional multicollinearity flag)")

    print("\n--- Implied pillar weights (sum of |coef| per group, normalized to 100%) ---")
    pillar_importance = {}
    for pillar_name, feature_cols in PILLAR_FEATURE_GROUPS.items():
        pillar_importance[pillar_name] = summary.loc[feature_cols, "coef"].abs().sum()
    total = sum(pillar_importance.values())
    hand_set = CONFIG["pillar_weights"]
    comparison = pd.DataFrame({
        "hand_set_weight": {k: hand_set[k] for k in pillar_importance},
        "fitted_implied_weight": {k: v / total for k, v in pillar_importance.items()},
    }).round(3)
    print(comparison.to_string())

    print("\n--- Skill pillar coefficients only (Pull%/FB% marginal contribution) ---")
    print(summary.loc[PILLAR_FEATURE_GROUPS["skill"]].to_string())

    print(f"\nBuilding 2023 test dataset ({TEST_MONTH_LABEL}) for out-of-sample validation...")
    test_df = build_scoring_dataset(TEST_MONTH_LABEL, TRAIN_SEASON)
    # Same scales/order_curve fit from 2022 training — no leakage from 2023 itself.
    test_features = build_feature_matrix(test_df, scales, CONFIG, order_curve)
    X_test = sm.add_constant(test_features, has_constant="add")
    test_df["fitted_score"] = fit.predict(X_test) * 100

    print("\n--- Out-of-sample validation: HR rate by fitted_score decile (2023) ---")
    decile_summary = decile_validation(test_df, "fitted_score")
    print(decile_summary.to_string())

    hr_rates = decile_summary["hr_rate"].to_numpy()
    lift = hr_rates[-1] / hr_rates[0]
    print(f"\nBottom decile HR rate: {hr_rates[0]:.4f}  |  Top decile HR rate: {hr_rates[-1]:.4f}  |  Lift: {lift:.2f}x")

    out_path = PROC_DIR / f"scored_fitted_{TEST_MONTH_LABEL}.parquet"
    test_df.to_parquet(out_path, index=False)
    summary.to_csv(PROC_DIR / "fitted_coefficients_2022.csv")
    print(f"\nSaved scored dataset -> {out_path}")
    print(f"Saved coefficient table -> {PROC_DIR / 'fitted_coefficients_2022.csv'}")
