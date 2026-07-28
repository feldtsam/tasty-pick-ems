"""
Places in the model where a raw adjustment size is measured from real data
instead of assumed.
"""
import pandas as pd

from .features import _wind_direction_multiplier


def compute_platoon_bonus(pitches: pd.DataFrame, sensitivity: float) -> dict:
    """
    Empirical HR-rate gap between opposite-handed and same-handed matchups,
    converted into a symmetric score-point bonus/penalty.

    Calibrated from raw prior-season Statcast pitches (every actual pitcher
    faced per PA — `stand` vs `p_throws`), not from the batter-game sample
    being scored. Two reasons: (1) a much larger sample is more stable —
    a first attempt calibrated from the ~7,800-row June 2023 sample alone
    came back with an implausible *negative* platoon lift, which resolved
    to the textbook-expected positive lift once checked against the full
    182K-PA 2022 season; (2) calibrating a model parameter on the same data
    used to validate the model is a mild form of leakage in the validation
    itself — better to source it from the prior-season baseline, same as
    every other pillar input.

    Note: elsewhere in the model (pillar 2's per-game matchup score), the
    platoon comparison uses the *starting* pitcher's hand as a practical
    proxy, since a prop is framed around the starter matchup — it won't
    capture a batter's actual platoon edge against a differently-handed
    reliever later in the game. That's fine for scoring a single game, but
    is exactly why this calibration step uses full play-by-play instead.
    """
    pa = pitches.dropna(subset=["events", "stand", "p_throws"]).copy()
    pa["is_hr"] = pa["events"] == "home_run"
    same = pa[pa["stand"] == pa["p_throws"]]
    opposite = pa[pa["stand"] != pa["p_throws"]]

    same_rate = same["is_hr"].mean()
    opposite_rate = opposite["is_hr"].mean()
    overall_rate = pa["is_hr"].mean()

    relative_lift = (opposite_rate - same_rate) / overall_rate
    bonus_opposite = relative_lift * sensitivity
    bonus_same = -relative_lift * sensitivity

    return {
        "same_hand_hr_rate": same_rate,
        "opposite_hand_hr_rate": opposite_rate,
        "overall_hr_rate": overall_rate,
        "relative_lift": relative_lift,
        "bonus_opposite": bonus_opposite,
        "bonus_same": bonus_same,
        "n_same": len(same),
        "n_opposite": len(opposite),
    }


def compute_batting_order_curve(df: pd.DataFrame) -> tuple:
    """
    Real order-slot -> average-PA-that-game relationship in the sample,
    min-max scaled to 0-100, instead of assuming a linear 1-9 taper.
    """
    known = df.dropna(subset=["batting_order_slot"])
    avg_pa_by_slot = known.groupby("batting_order_slot")["pa_count"].mean()
    lo, hi = avg_pa_by_slot.min(), avg_pa_by_slot.max()
    curve = ((avg_pa_by_slot - lo) / (hi - lo) * 100).to_dict()
    return curve, avg_pa_by_slot.to_dict()


def compute_wind_in_threshold(calibration_games: pd.DataFrame) -> float:
    """
    Minimum wind speed for a "wind blowing in" red flag to be meaningful,
    rather than triggering on a negligible 1-2mph reading that happens to
    be classified as "in". Calibrated as the median wind speed among real
    prior-season games where the wind was blowing in (excluding indoor
    games, whose recorded wind reading doesn't reach the field — see the
    Environment pillar's roof-closed handling).
    """
    games = calibration_games.drop_duplicates(subset=["game_pk"])
    indoor = games["condition"].isin(["Roof Closed", "Dome"])
    direction = games["wind_description"].apply(_wind_direction_multiplier)
    is_in = (direction == -1) & ~indoor
    return games.loc[is_in, "wind_speed_mph"].median()
