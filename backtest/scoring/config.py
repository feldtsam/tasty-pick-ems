"""
All tunable weights for the five-pillar scoring model (pillar 5 / Market
Intelligence skipped — no historical odds data). Pillar weights are the
midpoints of the user's stated ranges (skill 35-40%, matchup 25%,
environment 20%, opportunity 10-15%), rescaled to sum to 100 after dropping
Market Intelligence's 10-15% share.

Nothing here is derived from data except where noted — these are starting
points meant to be tuned once the backtest validation (scripts/run_backtest.py)
shows how well they separate HR rate by score bucket.
"""

CONFIG = {
    "pillar_weights": {
        "skill": 0.40,
        "matchup": 0.26,
        "environment": 0.21,
        "opportunity": 0.13,
    },
    "skill": {
        "min_pa": 100,  # reference-population qualification minimum, not a score floor
        "min_bbe": 50,  # same, but for the batted-ball-count-based Pull%/FB%
        "contact_quality_weight": 0.35,   # avg exit velo, hard-hit%, sweet-spot%, FB%, Pull%
        "power_production_weight": 0.40,  # barrel%, xSLG, xwOBA
        "track_record_weight": 0.25,      # HR/PA
    },
    "matchup": {
        "min_ip": 20,
        "contact_allowed_weight": 0.6,  # hard-hit%/barrel%/xSLG/xwOBA allowed
        "rate_outcome_weight": 0.4,     # HR/9, inverted K/9
        # Platoon split isn't a continuous stat, so it's an additive nudge
        # rather than folded into the percentile blend. The actual bonus
        # size is calibrated from the full prior-season Statcast play-by-play
        # (see scoring/calibrate.py) rather than guessed — this sensitivity
        # constant just scales how many score points a 1.0x relative HR
        # rate lift (opposite-hand vs same-hand) is worth.
        "platoon_sensitivity": 15.0,
    },
    "environment": {
        "park_weight": 0.6,     # more stable, multi-game signal
        "weather_weight": 0.4,  # single-game reading, noisier
        "wind_speed_ceiling_mph": 20,  # wind at/above this is treated as max effect
    },
    "opportunity": {
        "min_bullpen_pa": 500,  # season-long team aggregate, all 30 teams clear this easily
        "batting_order_weight": 0.6,
        "bullpen_weight": 0.4,
    },
    "red_flags": {
        # Heavy penalties, never a hard veto: multiplicative, so an
        # otherwise-elite score can survive a flag or two rather than being
        # zeroed out. Stacks across however many penalty flags trigger:
        #   final_score_rf = final_score * (1 - penalty_per_flag) ** n_flags
        # Starting point, not tuned — see backtest results for whether this
        # is too weak/strong.
        "penalty_per_flag": 0.15,
        # v2: replaced the K/9-based flag (empirically backwards — see
        # scoring/red_flags.py) with bottom-quartile hard-hit%-allowed
        # among qualified 2022 starters (reuses the reference scale already
        # built for pillar 2 — no separate calibration step needed).
        "elite_suppression_percentile_threshold": 25.0,
        # Wind blowing in, and at least this fast — calibrated as the
        # median wind speed among real prior-season "blowing in" games
        # (scoring/calibrate.py:compute_wind_in_threshold), not hardcoded
        # here. Filled in at runtime by scoring/model.py. v2: narrative-only,
        # no longer affects the penalty — see scoring/red_flags.py.
        "wind_in_min_speed_mph": None,
        # v2: the low-order flag was removed entirely (redundant with
        # pillar_opportunity, not just weak) — no config left for it.
    },
}
