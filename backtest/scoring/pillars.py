"""
The four pillar scoring functions. Each returns a 0-100 Series aligned to
the input dataframe's index. Pillar 5 (Market Intelligence) is skipped —
no historical odds data yet.

Pillar aggregation (grouping + hand-set internal weights) lives here; the
underlying individual normalized features live in scoring/features.py,
shared with the data-fit weight comparison in scripts/fit_data_weights.py
so both consume identical feature engineering.
"""
import pandas as pd

from .features import build_feature_matrix
from .normalize import build_reference_scale


def build_reference_scales(skill_2022: pd.DataFrame, matchup_2022: pd.DataFrame,
                            bullpen_2022: pd.DataFrame, park_2022: pd.DataFrame,
                            batted_ball_2022: pd.DataFrame, config: dict) -> dict:
    scales = {}

    qualified_batters = skill_2022["pa_bref"] >= config["skill"]["min_pa"]
    for col in ("avg_exit_velo", "sweet_spot_pct", "hard_hit_pct", "barrel_pct", "xslg", "xwoba", "hr_per_pa"):
        scales[col] = build_reference_scale(skill_2022[col], qualified_batters)

    qualified_bbe = batted_ball_2022["bbe_count"] >= config["skill"]["min_bbe"]
    for col in ("pull_pct", "fb_pct"):
        scales[col] = build_reference_scale(batted_ball_2022[col], qualified_bbe)

    qualified_pitchers = matchup_2022["ip"] >= config["matchup"]["min_ip"]
    for col in ("hard_hit_pct_allowed", "barrel_pct_allowed", "xslg_allowed", "xwoba_allowed", "hr_per_9", "k_per_9"):
        scales[col] = build_reference_scale(matchup_2022[col], qualified_pitchers)

    qualified_bullpens = bullpen_2022["bullpen_pa"] >= config["opportunity"]["min_bullpen_pa"]
    for col in ("bullpen_hr_per_pa", "bullpen_k_pct", "bullpen_hard_hit_pct"):
        scales[col] = build_reference_scale(bullpen_2022[col], qualified_bullpens)

    scales["park_factor_min"] = park_2022["park_factor_hr"].min()
    scales["park_factor_max"] = park_2022["park_factor_hr"].max()

    return scales


def score_skill(features: pd.DataFrame, config: dict) -> pd.Series:
    cfg = config["skill"]

    contact_quality = features[
        ["avg_exit_velo_pct", "hard_hit_pct_pct", "sweet_spot_pct_pct", "fb_pct_pct", "pull_pct_pct"]
    ].mean(axis=1)
    power_production = features[["barrel_pct_pct", "xslg_pct", "xwoba_pct"]].mean(axis=1)
    track_record = features["hr_per_pa_pct"]

    return (
        cfg["contact_quality_weight"] * contact_quality
        + cfg["power_production_weight"] * power_production
        + cfg["track_record_weight"] * track_record
    )


def score_matchup(features: pd.DataFrame, config: dict, platoon: dict) -> pd.Series:
    cfg = config["matchup"]

    contact_allowed = features[
        ["hard_hit_allowed_pct", "barrel_allowed_pct", "xslg_allowed_pct", "xwoba_allowed_pct"]
    ].mean(axis=1)
    rate_outcome = features[["hr9_allowed_pct", "k9_allowed_pct_inv"]].mean(axis=1)
    pitcher_quality = cfg["contact_allowed_weight"] * contact_allowed + cfg["rate_outcome_weight"] * rate_outcome

    # platoon_opposite_pct is 100/0/50 (opposite/same/unknown) — convert back to the
    # hand-set model's additive point-bonus form (calibrated bonus size, not a percentile).
    is_opposite = features["platoon_opposite_pct"] == 100.0
    is_same = features["platoon_opposite_pct"] == 0.0
    platoon_adj = is_opposite * platoon["bonus_opposite"] + is_same * platoon["bonus_same"]

    return (pitcher_quality + platoon_adj).clip(0, 100)


def score_environment(features: pd.DataFrame, config: dict) -> pd.Series:
    cfg = config["environment"]
    weather_score = (features["wind_pct"] + features["temp_pct"]) / 2
    return cfg["park_weight"] * features["park_factor_pct"] + cfg["weather_weight"] * weather_score


def score_opportunity(features: pd.DataFrame, config: dict) -> pd.Series:
    cfg = config["opportunity"]
    bullpen_score = features[["bullpen_hr_pa_pct", "bullpen_hard_hit_pct_pct", "bullpen_k_pct_inv_pct"]].mean(axis=1)
    return cfg["batting_order_weight"] * features["batting_order_pct"] + cfg["bullpen_weight"] * bullpen_score
