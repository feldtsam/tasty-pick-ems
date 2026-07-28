"""
Individual normalized (0-100) features — the shared "ingredients" consumed
by both the hand-set pillar model (scoring/pillars.py) and the data-fit
weight comparison (scripts/fit_data_weights.py). Single source of truth so
a logistic regression can be fit on exactly the same features the hand-set
model uses, metric by metric (e.g. Pull%/FB% get their own coefficients,
not a pre-blended Contact Quality average).
"""
import numpy as np
import pandas as pd

from .normalize import fill_neutral, percentile_lookup

SKILL_FEATURES = [
    "avg_exit_velo_pct", "hard_hit_pct_pct", "sweet_spot_pct_pct", "fb_pct_pct", "pull_pct_pct",
    "barrel_pct_pct", "xslg_pct", "xwoba_pct",
    "hr_per_pa_pct",
]
MATCHUP_FEATURES = [
    "hard_hit_allowed_pct", "barrel_allowed_pct", "xslg_allowed_pct", "xwoba_allowed_pct",
    "hr9_allowed_pct", "k9_allowed_pct_inv",
    "platoon_opposite_pct",
]
ENVIRONMENT_FEATURES = ["park_factor_pct", "wind_pct", "temp_pct"]
OPPORTUNITY_FEATURES = ["batting_order_pct", "bullpen_hr_pa_pct", "bullpen_hard_hit_pct_pct", "bullpen_k_pct_inv_pct"]

PILLAR_FEATURE_GROUPS = {
    "skill": SKILL_FEATURES,
    "matchup": MATCHUP_FEATURES,
    "environment": ENVIRONMENT_FEATURES,
    "opportunity": OPPORTUNITY_FEATURES,
}


def _wind_direction_multiplier(desc) -> float:
    if pd.isna(desc):
        return 0.0
    d = str(desc).lower()
    if "out" in d:
        return 1.0
    if "in" in d:
        return -1.0
    return 0.0  # cross-wind ("L To R" / "R To L") treated as neutral for v1


def build_feature_matrix(df: pd.DataFrame, scales: dict, config: dict, order_curve: dict) -> pd.DataFrame:
    f = pd.DataFrame(index=df.index)

    # --- skill ---
    f["avg_exit_velo_pct"] = fill_neutral(percentile_lookup(df["avg_exit_velo"], scales["avg_exit_velo"]))
    f["hard_hit_pct_pct"] = fill_neutral(percentile_lookup(df["hard_hit_pct"], scales["hard_hit_pct"]))
    f["sweet_spot_pct_pct"] = fill_neutral(percentile_lookup(df["sweet_spot_pct"], scales["sweet_spot_pct"]))
    f["fb_pct_pct"] = fill_neutral(percentile_lookup(df["fb_pct"], scales["fb_pct"]))
    f["pull_pct_pct"] = fill_neutral(percentile_lookup(df["pull_pct"], scales["pull_pct"]))
    f["barrel_pct_pct"] = fill_neutral(percentile_lookup(df["barrel_pct"], scales["barrel_pct"]))
    f["xslg_pct"] = fill_neutral(percentile_lookup(df["xslg"], scales["xslg"]))
    f["xwoba_pct"] = fill_neutral(percentile_lookup(df["xwoba"], scales["xwoba"]))
    f["hr_per_pa_pct"] = fill_neutral(percentile_lookup(df["hr_per_pa"], scales["hr_per_pa"]))

    # --- matchup ---
    f["hard_hit_allowed_pct"] = fill_neutral(percentile_lookup(df["hard_hit_pct_allowed"], scales["hard_hit_pct_allowed"]))
    f["barrel_allowed_pct"] = fill_neutral(percentile_lookup(df["barrel_pct_allowed"], scales["barrel_pct_allowed"]))
    f["xslg_allowed_pct"] = fill_neutral(percentile_lookup(df["xslg_allowed"], scales["xslg_allowed"]))
    f["xwoba_allowed_pct"] = fill_neutral(percentile_lookup(df["xwoba_allowed"], scales["xwoba_allowed"]))
    f["hr9_allowed_pct"] = fill_neutral(percentile_lookup(df["hr_per_9"], scales["hr_per_9"]))
    f["k9_allowed_pct_inv"] = fill_neutral(100 - percentile_lookup(df["k_per_9"], scales["k_per_9"]))

    # Platoon expressed on the same 0-100 scale as everything else (100 =
    # opposite-hand/favorable, 0 = same-hand/unfavorable, 50 = unknown) so
    # its fitted coefficient is directly comparable to the percentile features.
    is_opposite = df["stand"] != df["opp_starter_throws"]
    platoon_pct = np.where(is_opposite, 100.0, 0.0)
    platoon_pct = np.where(df["opp_starter_throws"].isna() | df["stand"].isna(), 50.0, platoon_pct)
    f["platoon_opposite_pct"] = platoon_pct

    # --- environment ---
    pf_min, pf_max = scales["park_factor_min"], scales["park_factor_max"]
    park_score = ((df["park_factor_hr"] - pf_min) / (pf_max - pf_min) * 100).clip(0, 100)
    f["park_factor_pct"] = park_score.fillna(50)

    indoor = df["condition"].isin(["Roof Closed", "Dome"])
    wind_speed = df["wind_speed_mph"].where(~indoor, 0.0)
    direction_mult = df["wind_description"].apply(_wind_direction_multiplier)
    direction_mult = direction_mult.where(~indoor, 0.0)
    ceiling = config["environment"]["wind_speed_ceiling_mph"]
    wind_component = (wind_speed / ceiling * 100).clip(upper=100) * direction_mult
    wind_score = (50 + wind_component / 2).clip(0, 100)
    f["wind_pct"] = wind_score.fillna(50)

    temp_f = pd.to_numeric(df["temp_f"], errors="coerce")
    temp_lo, temp_hi = temp_f.min(), temp_f.max()
    temp_score = ((temp_f - temp_lo) / (temp_hi - temp_lo) * 100).clip(0, 100)
    f["temp_pct"] = temp_score.fillna(50)

    # --- opportunity ---
    f["batting_order_pct"] = fill_neutral(df["batting_order_slot"].map(order_curve))
    f["bullpen_hr_pa_pct"] = fill_neutral(percentile_lookup(df["bullpen_hr_per_pa"], scales["bullpen_hr_per_pa"]))
    f["bullpen_hard_hit_pct_pct"] = fill_neutral(percentile_lookup(df["bullpen_hard_hit_pct"], scales["bullpen_hard_hit_pct"]))
    f["bullpen_k_pct_inv_pct"] = fill_neutral(100 - percentile_lookup(df["bullpen_k_pct"], scales["bullpen_k_pct"]))

    return f
