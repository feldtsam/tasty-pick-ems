"""
Red-flag penalties: conditions that heavily penalize — but never veto — a
batter-game's final score. Applied multiplicatively on top of the pillar-
weighted final_score, stacking across however many penalty-relevant flags
trigger:

    final_score_rf = final_score * (1 - penalty_per_flag) ** n_flags_triggered

Revision history worth knowing before touching this file — v1 used K/9
(facing a high-strikeout pitcher), wind blowing in, and batting 8th/9th.
Backtested on the full 2023 season, that made the model WORSE (2.85x lift
-> 2.65x), not better. Investigation found:
  - High-K/9 pitchers correlate with a HIGHER, not lower, batter HR rate on
    both 2022 and 2023 independently — K/9 measures whiff ability, not
    damage suppression, and those turned out to be different skills here.
  - Wind-in showed no clean suppression effect at any speed threshold —
    "out" wind clearly boosts HR rate as expected, but "in" didn't show the
    mirror-image suppression.
  - Low-order was directionally real but fully redundant with
    pillar_opportunity, which already encodes it continuously — double
    counting the same signal, not adding one.
See backtest/README.md for the full writeup.

v2 (this version): replaced the K/9 flag with one built on hard-hit%
allowed — chosen over HR/9-allowed because it's built on a much larger
per-pitcher sample (batted-ball count vs. raw HR count, a comparatively
rare event), so a threshold calibrated on it should generalize more
reliably. Worth flagging: a bottom-quartile check on *either* candidate
metric showed the same backwards pattern as K/9 (elite-suppression
pitchers by season aggregate correlate with higher, not lower, batter HR
rate, on both 2022 and 2023) before this flag was ever wired into the
pipeline — so this replacement was not expected to cleanly fix things, and
the real backtest result should be read with that in mind, not assumed to
be an improvement. Removed the low-order flag entirely (redundant, not
just weak). Downgraded wind-in to narrative-only: still computed and
exposed (for a card's "why" text, if useful context), but no longer
included in red_flag_count or the penalty multiplier.

Thresholds calibrated from 2022 data, validated out-of-sample on 2023 —
same principle as everything else in this project.
"""
import pandas as pd

from .features import _wind_direction_multiplier
from .normalize import percentile_lookup

# Flags that affect red_flag_count / the score multiplier.
PENALTY_FLAG_COLUMNS = ["flag_elite_suppression_pitcher"]
# Computed and exposed, but excluded from the penalty — narrative use only.
NARRATIVE_ONLY_FLAG_COLUMNS = ["flag_wind_in"]
FLAG_COLUMNS = PENALTY_FLAG_COLUMNS + NARRATIVE_ONLY_FLAG_COLUMNS


def compute_red_flags(df: pd.DataFrame, scales: dict, config: dict, wind_in_min_speed_mph: float) -> pd.DataFrame:
    cfg = config["red_flags"]
    flags = pd.DataFrame(index=df.index)

    # Bottom quartile hard-hit%-allowed = elite contact suppression = tough matchup.
    hard_hit_allowed_pct = percentile_lookup(df["hard_hit_pct_allowed"], scales["hard_hit_pct_allowed"])
    flags["flag_elite_suppression_pitcher"] = (
        hard_hit_allowed_pct <= cfg["elite_suppression_percentile_threshold"]
    ).fillna(False)

    # Narrative-only — computed for "why" text, does not affect red_flag_count/penalty.
    indoor = df["condition"].isin(["Roof Closed", "Dome"])
    direction_mult = df["wind_description"].apply(_wind_direction_multiplier)
    is_in = (direction_mult == -1) & ~indoor
    wind_speed = pd.to_numeric(df["wind_speed_mph"], errors="coerce")
    flags["flag_wind_in"] = (is_in & (wind_speed >= wind_in_min_speed_mph)).fillna(False)

    flags["red_flag_count"] = flags[PENALTY_FLAG_COLUMNS].sum(axis=1)
    return flags


def apply_red_flag_penalty(final_score: pd.Series, red_flag_count: pd.Series, config: dict) -> pd.Series:
    penalty = config["red_flags"]["penalty_per_flag"]
    multiplier = (1 - penalty) ** red_flag_count
    return final_score * multiplier
