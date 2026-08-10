"""
Universal TPE Score components (NFL Master Blueprint) scored from the
outputs of redzone.py: aggregate_redzone_game -> add_snap_shares ->
add_depth_chart_rank -> add_injury_context -> add_rolling_windows.

TD Opportunity (30% weight) — two lenses, blended so neither can cancel
the other out:
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

Role & Momentum (20% weight) — same "don't cancel" shape, different two
lenses:
  Role Trend             - already observable in the data: touch-share and
                            snap-share trend (last3+last5 vs season
                            average, reused from the same _trend_delta
                            helper TD Opportunity uses, not recomputed) and
                            depth-chart-rank movement.
  External Opportunity   - a LEADING indicator: the worst injury status
                            among teammates ranked ahead on the depth
                            chart this week. An injury that just happened
                            hasn't shown up in this week's snap/touch
                            trend yet - averaging it in directly would
                            dilute it against a same-week signal that
                            hasn't caught up, the same reasoning that
                            justified Proven/Emerging Heat's combination
                            mechanic.

All weights in CONFIG are starting points ("hypotheses to tune"), matching
backtest/scoring/config.py's stated philosophy - nothing here is derived
from data except the league-average conversion rates used for shrinkage
and the percentile reference scales, both noted where they're built. The
injury severity ladder (Out/Doubtful/Questionable -> 100/70/40) is a fixed
mapping, not a percentile, deliberately: an injury designation is
categorical and absolute ("backup's backup is Out"), not a noisy
continuous stat that benefits from relative scaling.

NORMALIZATION CAVEAT: percentile reference scales are built by pooling all
three backfilled seasons (2022, 2024, 2025). That's fine for normalization
(putting a raw value on a 0-100 scale against a realistic population) but
must NOT be reused later to validate or calibrate these weights - fitting
and grading on the same data is exactly the leakage backtest/'s
prior-season/later-season split exists to avoid (see
backtest/scoring/model.py's calibration comments). There's no NFL backtest
script yet, so nothing is at risk today; a real train/validate season
split is needed before any calibration phase begins.

DEPTH-CHART GAP, OPEN ITEM FOR LAUNCH: nflverse changed the depth-chart
source schema at some point in 2025 (see redzone._skill_position_depth_
chart's docstring) - depth_rank is NaN for every 2025 row, so
depth_chart_movement_pct falls back to neutral for all of 2025 through
Role Trend. This is not a minor coverage footnote like the snap-count
crosswalk misses: it's the entire current season's depth-chart signal.
The live weekly job launching into the 2026 season needs a parser built
against the new schema (dt/team/player_name/pos_abb/pos_slot/pos_rank, no
gsis_id) before depth-chart movement means anything live - deferred, not
solved, and should not be assumed working once this pillar ships.
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
    "role_trend": {
        "touch_share_trend_weight": 0.40,
        "snap_share_trend_weight": 0.30,
        "depth_chart_movement_weight": 0.30,
    },
    # Fixed, not a percentile - see module docstring. Any status not listed
    # (e.g. "Note") falls back to 0 via .get(status, 0) at the call site.
    "injury_severity": {
        "Out": 100.0,
        "Doubtful": 70.0,
        "Questionable": 40.0,
    },
    "combination": {
        # How much the weaker of (Proven, Emerging) / (Role Trend,
        # External Opportunity) can still add on top of the stronger one,
        # scaled by how much headroom is left below 100 - never enough to
        # pull the score below max(...). Reused as-is across both pillars
        # rather than tuned separately - consistency over a fresh guess,
        # per the same "hypotheses to tune later" philosophy.
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


def _qualified_mask(weekly: pd.DataFrame, config: dict) -> pd.Series:
    """Which rows count toward defining the percentile reference scale:
    the player's own season-total rz_touches clears the qualification
    minimum. Every row still gets scored against the resulting scale
    regardless of its own sample size - this only controls who defines
    the scale. Shared by every scoring function so the reference
    population is defined identically everywhere."""
    season_total_rz_touches = weekly.groupby(["player_id", "season"])["rz_touches"].transform("sum")
    return season_total_rz_touches >= config["min_rz_touches_for_qualification"]


def _percentile_fn(weekly: pd.DataFrame, config: dict):
    """Returns a pct(values) closure bound to this weekly table's qualified
    reference population."""
    qualified = _qualified_mask(weekly, config)

    def pct(values: pd.Series) -> pd.Series:
        scale = build_reference_scale(values, qualified)
        return fill_neutral(percentile_lookup(values, scale))

    return pct


def _trend_delta(weekly: pd.DataFrame, col: str, window: int) -> pd.Series:
    """Recent-window mean minus season-to-date average, e.g.
    rz_touch_share_last3 - rz_touch_share_season_avg for window=3. Positive
    = trending up. Reads columns add_rolling_windows already produced -
    shared by TD Opportunity's Emerging Heat (last3 only) and Role &
    Momentum's Role Trend (last3 and last5, blended), neither of which
    recomputes the underlying rolling means."""
    return weekly[f"{col}_last{window}"] - weekly[f"{col}_season_avg"]


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

    pct = _percentile_fn(weekly, config)

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
    touch_share_trend_pct = pct(_trend_delta(weekly, "rz_touch_share", 3))
    snap_share_trend_pct = pct(_trend_delta(weekly, "snap_share", 3))
    touch_volume_trend_pct = pct(_trend_delta(weekly, "rz_touches", 3))

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


def score_role_momentum(weekly: pd.DataFrame, config: dict = CONFIG) -> pd.DataFrame:
    """
    Score every row for Role & Momentum (20% weight in the Universal TPE
    Score). Requires weekly to already have depth_rank
    (redzone.add_depth_chart_rank) and ahead_injury_statuses
    (redzone.add_injury_context) columns, in addition to add_rolling_windows'
    output. Returns weekly with role_trend, external_opportunity,
    role_momentum, and the intermediate percentile components appended.

    See module docstring for the depth-chart 2025-schema gap: depth_rank is
    NaN for every 2025 row, so depth_chart_movement_pct falls back to
    neutral there, not a real reading.
    """
    weekly = weekly.sort_values(["player_id", "season", "week"]).copy()
    rt_cfg = config["role_trend"]
    combo_cfg = config["combination"]
    severity_map = config["injury_severity"]

    pct = _percentile_fn(weekly, config)

    # --- Role Trend: touch-share / snap-share trend, last3+last5 blended.
    # Reuses _trend_delta -- the exact same helper TD Opportunity's
    # Emerging Heat uses -- rather than recomputing the rolling deltas.
    touch_share_trend_pct = pd.concat(
        [pct(_trend_delta(weekly, "rz_touch_share", 3)), pct(_trend_delta(weekly, "rz_touch_share", 5))],
        axis=1,
    ).mean(axis=1)
    snap_share_trend_pct = pd.concat(
        [pct(_trend_delta(weekly, "snap_share", 3)), pct(_trend_delta(weekly, "snap_share", 5))],
        axis=1,
    ).mean(axis=1)

    # --- Role Trend: depth-chart movement. depth_rank itself is a
    # pre-game roster designation (known before kickoff, like the injury
    # report), not a game outcome -- no shift needed on the current row's
    # own value. The trailing comparison (this week's rank vs last week's)
    # is what needs the lag, via depth_rank_prev.
    depth_rank_prev = weekly.groupby(["player_id", "season"])["depth_rank"].transform(lambda s: s.shift(1))
    depth_chart_delta = depth_rank_prev - weekly["depth_rank"]  # positive = moved to a better (lower) rank
    # A delta of exactly 0 ("no change") is ~96% of qualified rows -- an
    # integer rank has a real mass point there that a continuous share
    # metric doesn't. percentile_lookup ranks ties by count-strictly-less,
    # so that huge tie cluster would land at the *bottom* (an entrenched
    # starter's normal "still rank 1" week scoring near the worst in the
    # league), not the middle. Route 0 through the same neutral-50
    # fallback as missing data instead -- "nothing changed" carries no
    # momentum signal either way, so neutral is the honest reading. Only
    # genuine nonzero rank changes get percentile-ranked against each
    # other.
    depth_chart_movement_pct = pct(depth_chart_delta.where(depth_chart_delta != 0))

    role_trend = (
        rt_cfg["touch_share_trend_weight"] * touch_share_trend_pct
        + rt_cfg["snap_share_trend_weight"] * snap_share_trend_pct
        + rt_cfg["depth_chart_movement_weight"] * depth_chart_movement_pct
    )

    # --- External Opportunity: worst injury status among teammates ranked
    # ahead this week. Fixed severity ladder, not a percentile -- see
    # module docstring. Empty list (no one ahead, or no one ahead hurt) ->
    # 0, a real reading (no vacated opportunity), not a missing one.
    external_opportunity = weekly["ahead_injury_statuses"].apply(
        lambda statuses: max((severity_map.get(s, 0.0) for s in statuses), default=0.0)
    )

    # --- Combine: same headroom-scaled mechanic as TD Opportunity. Role
    # Trend is "already observable in the data"; External Opportunity is a
    # leading indicator that hasn't shown up in the trend yet -- neither
    # should be able to cancel the other.
    hi = np.maximum(role_trend, external_opportunity)
    lo = np.minimum(role_trend, external_opportunity)
    role_momentum = (hi + combo_cfg["bonus_weight"] * lo * (1 - hi / 100)).clip(0, 100)

    weekly["touch_share_trend_pct_role"] = touch_share_trend_pct.round(1)
    weekly["snap_share_trend_pct_role"] = snap_share_trend_pct.round(1)
    weekly["depth_chart_movement_pct"] = depth_chart_movement_pct.round(1)
    weekly["role_trend"] = role_trend.round(1)
    weekly["external_opportunity"] = external_opportunity.round(1)
    weekly["role_momentum"] = role_momentum.round(1)

    return weekly
