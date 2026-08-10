"""
TD Opportunity — the 30%-weighted opportunity component of the Universal
TPE Score (NFL Master Blueprint), scored from the outputs of redzone.py
(aggregate_redzone_game -> add_snap_shares -> add_rolling_windows).

Two lenses, blended so neither can cancel the other out:
  Proven Heat    - already scoring: recent TD production + TD-conversion
                   rate (shrinkage-adjusted so a 1-touch/1-TD game doesn't
                   register as a "100% converter").
  Emerging Heat  - rising opportunity, TDs not arrived yet: touch-share
                   trend, snap-share trend, and raw touch-volume trend,
                   combined as a weighted SUM, not a gate or a product -
                   a real snap-share rise still has to register as a
                   (weaker) signal even when touch share hasn't caught up,
                   which a multiplicative combination would suppress
                   instead of surface.

All weights in CONFIG are starting points ("hypotheses to tune"), matching
backtest/scoring/config.py's stated philosophy - nothing here is derived
from data except the league-average conversion rates used for shrinkage
and the percentile reference scales, both noted where they're built.

NORMALIZATION CAVEAT: percentile reference scales are built by pooling all
three backfilled seasons (2022, 2024, 2025). That's fine for normalization
(putting a raw value on a 0-100 scale against a realistic population) but
must NOT be reused later to validate or calibrate these weights - fitting
and grading on the same data is exactly the leakage backtest/'s
prior-season/later-season split exists to avoid (see
backtest/scoring/model.py's calibration comments). There's no NFL backtest
script yet, so nothing is at risk today; a real train/validate season
split is needed before any calibration phase begins.

KNOWN CARRYOVER CAVEAT: the _last1/_last3/_last5 inputs this module
consumes are computed by redzone.add_rolling_windows, which groups by
player_id only (not player_id + season) - a player's week-1 rolling values
in a new season currently carry over trailing games from wherever their
history last picked up (e.g. a different team, a season or more earlier,
with no backfilled data in between), rather than resetting to "no recent
history." This is a pre-existing property of add_rolling_windows, not
something introduced here - flagging it because it directly affects this
module's output for early-season rows, most visibly for a player who
changed teams between backfilled seasons.
"""
import numpy as np
import pandas as pd

from normalize import build_reference_scale, fill_neutral, percentile_lookup

CONFIG = {
    "proven_heat": {
        "recent_td_production_weight": 0.6,
        "conversion_rate_weight": 0.4,
        "recent_td_production": {
            "last1": 0.35,
            "last3": 0.30,
            "last5": 0.20,
            "season_avg": 0.15,
        },
    },
    "emerging_heat": {
        "touch_share_trend_weight": 0.5,
        "snap_share_trend_weight": 0.3,
        "touch_volume_trend_weight": 0.2,
    },
    "combination": {
        # How much the weaker of (Proven, Emerging) can still add on top of
        # the stronger one, scaled by how much headroom is left below 100 -
        # never enough to pull the score below max(Proven, Emerging).
        "bonus_weight": 0.3,
    },
    # Phantom-touch prior strength for TD-conversion-rate shrinkage.
    # Standard regression-to-the-mean range; not tuned against outcome data
    # yet (no NFL backtest script exists to tune it against).
    "shrinkage_k": 6,
    # Season-total red-zone touches required for a player's rows to help
    # define the percentile reference scale. Every player's rows still get
    # looked up against that scale regardless of their own sample size -
    # this only controls who defines the scale, matching
    # normalize.py / backtest/scoring/normalize.py's documented philosophy.
    "min_rz_touches_for_qualification": 15,
}


def _shrink_rate(tds: pd.Series, touches: pd.Series, league_avg_rate: float, k: float) -> pd.Series:
    """
    Regress a TD-per-touch rate toward the league average, weighted by k
    phantom touches at the league rate. touches=0 naturally resolves to
    exactly league_avg_rate (no info -> fall back to the population mean),
    not NaN - a real, if uninformative, answer rather than a missing one.
    """
    return (tds + k * league_avg_rate) / (touches + k)


def _season_cumulative(weekly: pd.DataFrame, col: str) -> pd.Series:
    """
    Season-to-date cumulative total of col through the prior game only
    (cumsum then shift(1)), reset at each player-season boundary. Used for
    shrinkage-rate denominators/numerators, which need real touch/TD
    volumes, not the per-game rate means add_rolling_windows produces.

    Grouped by (player_id, season), not just player_id - deliberately
    stricter than add_rolling_windows' current behavior (see module
    docstring's carryover caveat). New code, so it's written correctly
    from the start rather than replicating the known gap.
    """
    g = weekly.groupby(["player_id", "season"])[col]
    return g.transform(lambda s: s.cumsum().shift(1)).fillna(0)


def score_td_opportunity(weekly: pd.DataFrame, config: dict = CONFIG) -> pd.DataFrame:
    """
    Score every row of the red-zone weekly table (post add_snap_shares +
    add_rolling_windows) for TD Opportunity. Returns `weekly` with
    proven_heat, emerging_heat, td_opportunity, and the intermediate
    percentile components appended, for inspectability.

    Only ever uses shift(1)'d / cumulative-through-the-prior-game inputs -
    never a row's own current-game rz_touches/rz_tds/snap_share - so the
    score always reflects what was knowable heading into that game, never
    the game's own outcome.
    """
    weekly = weekly.sort_values(["player_id", "season", "week"]).copy()
    ph_cfg = config["proven_heat"]
    eh_cfg = config["emerging_heat"]
    combo_cfg = config["combination"]
    k = config["shrinkage_k"]

    # --- Reference population for percentile scales: qualify on each
    # player's FULL season-total rz_touches (a stable, season-long
    # measure), but every row is still scored against the resulting scale
    # regardless of its own sample size.
    season_total_rz_touches = weekly.groupby(["player_id", "season"])["rz_touches"].transform("sum")
    qualified = season_total_rz_touches >= config["min_rz_touches_for_qualification"]

    def pct(values: pd.Series) -> pd.Series:
        scale = build_reference_scale(values, qualified)
        return fill_neutral(percentile_lookup(values, scale))

    # --- Proven Heat: recent TD production ---
    w = ph_cfg["recent_td_production"]
    recent_td_production_pct = (
        w["last1"] * pct(weekly["rz_tds_last1"])
        + w["last3"] * pct(weekly["rz_tds_last3"])
        + w["last5"] * pct(weekly["rz_tds_last5"])
        + w["season_avg"] * pct(weekly["rz_tds_season_avg"])
    )

    # --- Proven Heat: shrinkage-adjusted conversion rate, all three bands ---
    cum_gl_touches = _season_cumulative(weekly, "gl_touches")
    cum_gl_tds = _season_cumulative(weekly, "gl_tds")
    cum_i10_touches = _season_cumulative(weekly, "i10_touches")
    cum_i10_tds = _season_cumulative(weekly, "i10_tds")
    cum_rz_touches = _season_cumulative(weekly, "rz_touches")
    cum_rz_tds = _season_cumulative(weekly, "rz_tds")

    league_avg_gl_rate = weekly["gl_tds"].sum() / weekly["gl_touches"].sum()
    league_avg_i10_rate = weekly["i10_tds"].sum() / weekly["i10_touches"].sum()
    league_avg_rz_rate = weekly["rz_tds"].sum() / weekly["rz_touches"].sum()

    gl_rate = _shrink_rate(cum_gl_tds, cum_gl_touches, league_avg_gl_rate, k)
    i10_rate = _shrink_rate(cum_i10_tds, cum_i10_touches, league_avg_i10_rate, k)
    rz_rate = _shrink_rate(cum_rz_tds, cum_rz_touches, league_avg_rz_rate, k)

    conversion_rate_pct = pd.concat([pct(gl_rate), pct(i10_rate), pct(rz_rate)], axis=1).mean(axis=1)

    proven_heat = (
        ph_cfg["recent_td_production_weight"] * recent_td_production_pct
        + ph_cfg["conversion_rate_weight"] * conversion_rate_pct
    )

    # --- Emerging Heat: touch-share / snap-share / touch-volume trend ---
    touch_share_trend = weekly["rz_touch_share_last3"] - weekly["rz_touch_share_season_avg"]
    snap_share_trend = weekly["snap_share_last3"] - weekly["snap_share_season_avg"]
    touch_volume_trend = weekly["rz_touches_last3"] - weekly["rz_touches_season_avg"]

    touch_share_trend_pct = pct(touch_share_trend)
    snap_share_trend_pct = pct(snap_share_trend)
    touch_volume_trend_pct = pct(touch_volume_trend)

    emerging_heat = (
        eh_cfg["touch_share_trend_weight"] * touch_share_trend_pct
        + eh_cfg["snap_share_trend_weight"] * snap_share_trend_pct
        + eh_cfg["touch_volume_trend_weight"] * touch_volume_trend_pct
    )

    # --- Combine: the stronger signal sets the floor, the weaker one can
    # only add, scaled by remaining headroom - mathematically guaranteed
    # to never fall below max(Proven, Emerging).
    hi = np.maximum(proven_heat, emerging_heat)
    lo = np.minimum(proven_heat, emerging_heat)
    td_opportunity = (hi + combo_cfg["bonus_weight"] * lo * (1 - hi / 100)).clip(0, 100)

    weekly["recent_td_production_pct"] = recent_td_production_pct.round(1)
    weekly["conversion_rate_pct"] = conversion_rate_pct.round(1)
    weekly["proven_heat"] = proven_heat.round(1)
    weekly["touch_share_trend_pct"] = touch_share_trend_pct.round(1)
    weekly["snap_share_trend_pct"] = snap_share_trend_pct.round(1)
    weekly["touch_volume_trend_pct"] = touch_volume_trend_pct.round(1)
    weekly["emerging_heat"] = emerging_heat.round(1)
    weekly["td_opportunity"] = td_opportunity.round(1)

    return weekly
