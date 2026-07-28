"""
Ties the pillars together into one final 0-100 score, then applies the
red-flag penalty layer on top. Calibrates the data-derived adjustments
(platoon bonus, batting-order curve, wind-in threshold) from a prior-season
calibration source, never the dataframe being scored/validated.
"""
import pandas as pd

from . import calibrate, pillars, red_flags
from .features import build_feature_matrix


def score_dataset(df: pd.DataFrame, skill_2022: pd.DataFrame, matchup_2022: pd.DataFrame,
                   bullpen_2022: pd.DataFrame, park_2022: pd.DataFrame, batted_ball_2022: pd.DataFrame,
                   baseline_pitches: pd.DataFrame, calibration_games: pd.DataFrame, config: dict) -> tuple:
    scales = pillars.build_reference_scales(skill_2022, matchup_2022, bullpen_2022, park_2022,
                                             batted_ball_2022, config)
    platoon = calibrate.compute_platoon_bonus(baseline_pitches, sensitivity=config["matchup"]["platoon_sensitivity"])
    # Calibrated from calibration_games (prior season), not df (the period being
    # scored/validated) — same "don't fit and validate on the same data" principle
    # as the platoon bonus above.
    order_curve, avg_pa_by_slot = calibrate.compute_batting_order_curve(calibration_games)
    wind_in_min_speed_mph = calibrate.compute_wind_in_threshold(calibration_games)

    features = build_feature_matrix(df, scales, config, order_curve)

    scored = df.copy()
    scored["pillar_skill"] = pillars.score_skill(features, config)
    scored["pillar_matchup"] = pillars.score_matchup(features, config, platoon)
    scored["pillar_environment"] = pillars.score_environment(features, config)
    scored["pillar_opportunity"] = pillars.score_opportunity(features, config)

    w = config["pillar_weights"]
    scored["final_score"] = (
        w["skill"] * scored["pillar_skill"]
        + w["matchup"] * scored["pillar_matchup"]
        + w["environment"] * scored["pillar_environment"]
        + w["opportunity"] * scored["pillar_opportunity"]
    )

    flags = red_flags.compute_red_flags(scored, scales, config, wind_in_min_speed_mph)
    scored = pd.concat([scored, flags], axis=1)
    scored["final_score_rf"] = red_flags.apply_red_flag_penalty(
        scored["final_score"], scored["red_flag_count"], config
    )

    calibration = {
        "platoon": platoon,
        "batting_order_curve": order_curve,
        "avg_pa_by_slot": avg_pa_by_slot,
        "wind_in_min_speed_mph": wind_in_min_speed_mph,
        "scales": scales,
        "features": features,
    }
    return scored, calibration
