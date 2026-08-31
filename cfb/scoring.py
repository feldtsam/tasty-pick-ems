"""
CFB v1 scoring — TD Opportunity (§2) and Situation's Defensive Matchup
Vulnerability half (§3).

Pure DataFrame-in / DataFrame-out, exactly like nfl/scoring.py. Reads the
two ingestion tables' rows (assembled by the caller across a whole
season); rolling windows / percentiles are computed here at scoring time,
NOT stored (spec §8). The read-route wiring that feeds a real season's
rows in from Supabase is a later integration step.

The percentile / shrinkage / rolling-window / qualification math is
COPIED VERBATIM from nfl/scoring.py + nfl/redzone.py (the "duplicate
rather than cross-import" rule) so the semantics are provably identical:
  _qualified_mask, _percentile_fn, _trend_delta, _shrink_rate,
  _season_cumulative, _cumulative_through_prior_week, add_rolling_windows
are byte-for-byte the NFL versions; add_defensive_matchup_context differs
only in join keys (CFB uses the stable integer team id, spec §8, and the
`cfb_defense_redzone_allowed_weekly` columns carry an `_allowed` suffix).

Locked parameters (spec §8): recency weights .35/.30/.20/.15, shrinkage
k=6, min_rz_touches_for_qualification=15,
min_touches_allowed_for_qualification=20. `snap_share` has no CFB source
and is left as a PERMANENT structural fallback — an all-NaN column fed
through the existing neutral-50 fallback mechanism, which caps
td_opportunity_completeness at ~90% all season (Evidence Quality audit).

OUT OF SCOPE here: the environment half of Situation (dome/wind/temp) and
the `0.7·dmv + 0.3·env` blend; Role & Momentum; Evidence Quality; Market
Value; core_weights / any end-to-end pipeline wiring.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from normalize import build_reference_scale, fill_neutral, percentile_lookup

# ---------------------------------------------------------------------------
# CONFIG — only the §8-locked params for these two pillars.
# ---------------------------------------------------------------------------
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
        "snap_share_trend_weight": 0.3,   # permanent structural fallback for CFB — no snap data
        "touch_volume_trend_weight": 0.2,
    },
    "combination": {
        "bonus_weight": 0.3,
    },
    # Phantom-touch prior strength for TD-conversion-rate shrinkage (§8).
    "shrinkage_k": 6,
    # Season-total red-zone touches for a player's rows to help DEFINE the
    # percentile reference scale. Every row is still scored against the
    # scale regardless of its own sample size (§8).
    "min_rz_touches_for_qualification": 15,
    "defensive_matchup": {
        # Mirrors proven_heat's split + recency weights exactly — same
        # shape, measuring what's ALLOWED instead of produced.
        "recent_tds_allowed_weight": 0.6,
        "conversion_rate_allowed_weight": 0.4,
        "recent_tds_allowed": {
            "last1": 0.35,
            "last3": 0.30,
            "last5": 0.20,
            "season_avg": 0.15,
        },
    },
    "min_touches_allowed_for_qualification": 20,
}


# ===========================================================================
# math helpers — VERBATIM from nfl/scoring.py / nfl/redzone.py
# ===========================================================================
def _qualified_mask(weekly: pd.DataFrame, config: dict) -> pd.Series:
    """Which rows count toward defining the percentile reference scale: the
    player's own season-total rz_touches clears the qualification minimum.
    Every row is still scored against the resulting scale regardless of its
    own sample size — this only controls who defines the scale."""
    season_total_rz_touches = weekly.groupby(["player_id", "season"])["rz_touches"].transform("sum")
    return season_total_rz_touches >= config["min_rz_touches_for_qualification"]


def _percentile_fn(weekly: pd.DataFrame, config: dict, track_fallback: list | None = None):
    """
    Returns a pct(values) closure bound to this weekly table's qualified
    reference population. Passing track_fallback is purely additive
    instrumentation — it appends a boolean Series on every call, True where
    the result was a neutral-50 fallback (raw percentile NaN before
    fill_neutral). Lets a scoring fn expose "what fraction of my inputs
    were real" with no re-derivation.
    """
    qualified = _qualified_mask(weekly, config)

    def pct(values: pd.Series) -> pd.Series:
        raw = percentile_lookup(values, build_reference_scale(values, qualified))
        if track_fallback is not None:
            track_fallback.append(raw.isna())
        return fill_neutral(raw)

    return pct


def _trend_delta(weekly: pd.DataFrame, col: str, window: int) -> pd.Series:
    """
    Recent-window mean minus season-to-date average (e.g.
    rz_touch_share_last3 - rz_touch_share_season_avg). Positive = trending
    up. MASKED to NaN whenever the player has <= `window` prior games this
    season: with <= window prior games last{window} and season_avg are
    computed over the exact same set of games and are mathematically
    identical (delta = 0), indistinguishable from a genuinely flat trend
    by value alone — so it's routed through the neutral-50 fallback until
    enough history exists.
    """
    games_played = weekly.groupby(["player_id", "season"]).cumcount()
    delta = weekly[f"{col}_last{window}"] - weekly[f"{col}_season_avg"]
    return delta.where(games_played > window)


def _shrink_rate(tds: pd.Series, touches: pd.Series, league_avg_rate: float, k: float) -> pd.Series:
    """
    Regress a TD-per-touch rate toward the league average, weighted by k
    phantom touches at the league rate. touches=0 resolves to exactly
    league_avg_rate (no info -> population mean), not NaN.
    """
    return (tds + k * league_avg_rate) / (touches + k)


def _season_cumulative(weekly: pd.DataFrame, col: str) -> pd.Series:
    """Season-to-date cumulative total of col through the prior game only
    (cumsum then shift(1)), reset at each player-season boundary."""
    g = weekly.groupby(["player_id", "season"])[col]
    return g.transform(lambda s: s.cumsum().shift(1)).fillna(0)


def _cumulative_through_prior_week(df: pd.DataFrame, col: str, group_cols: list[str]) -> pd.Series:
    """Season-to-date cumulative total of col through the prior week only,
    within each group_cols group. Same cumsum+shift pattern as
    _season_cumulative — the defense-allowed-table counterpart."""
    g = df.groupby(group_cols)[col]
    return g.transform(lambda s: s.cumsum().shift(1)).fillna(0)


def add_rolling_windows(
    weekly: pd.DataFrame, metrics: list[str] | None = None, group_cols: list[str] | None = None
) -> pd.DataFrame:
    """
    last-1 / last-3 / last-5 / season-to-date rolling means within each
    group_cols group. Every window is shift(1)'d (last1 = plain shift) so
    the value landing on a row is always what was true heading INTO that
    game, never the game's own outcome. Grouped by (..., season) — no
    cross-season carryover; a new season's week-1 row gets NaN across
    every _last*/_season_avg column, which the scoring functions route
    through their neutral-50 path.

    VERBATIM from nfl/redzone.py.add_rolling_windows.
    """
    if group_cols is None:
        group_cols = ["player_id", "season"]
    weekly = weekly.sort_values(group_cols + ["week"]).copy()
    if metrics is None:
        metrics = ["rz_touches", "rz_touch_share", "rz_tds", "snap_share"]

    for m in metrics:
        g = weekly.groupby(group_cols)[m]
        weekly[f"{m}_last1"] = g.transform(lambda s: s.shift(1))
        weekly[f"{m}_last3"] = g.transform(lambda s: s.rolling(3, min_periods=1).mean().shift(1))
        weekly[f"{m}_last5"] = g.transform(lambda s: s.rolling(5, min_periods=1).mean().shift(1))
        weekly[f"{m}_season_avg"] = g.transform(lambda s: s.expanding().mean().shift(1))

    return weekly


_ALLOWED_SUFFIX_COLS = [
    f"{band}_{stat}"
    for band in ("rz", "i10", "gl")
    for stat in ("touches", "rush_touches", "target_touches", "tds")
]


def _unsuffix_allowed(allowed_weekly: pd.DataFrame) -> pd.DataFrame:
    """`cfb_defense_redzone_allowed_weekly` stores band counts as
    `rz_touches_allowed` etc. Rename to the unsuffixed names the
    NFL-derived rolling / context code expects; add_defensive_matchup_
    context re-applies the `allowed_` prefix on the lagged columns, exactly
    as nfl/redzone.py does."""
    rename = {f"{c}_allowed": c for c in _ALLOWED_SUFFIX_COLS if f"{c}_allowed" in allowed_weekly.columns}
    return allowed_weekly.rename(columns=rename)


def add_defensive_matchup_context(weekly: pd.DataFrame, allowed_weekly: pd.DataFrame) -> pd.DataFrame:
    """
    Join the defense-allowed rolling/trend columns onto each offensive
    player's row, keyed by (this row's own opponent defense team_id,
    season, week, this player's own position_group).

    Differs from nfl/redzone.py.add_defensive_matchup_context ONLY in the
    identity columns: CFB groups the defense by the stable integer
    `team_id` (spec §8), and the player row carries `opponent_team_id`
    (the defense faced) rather than an added `defteam` string. The
    cumulative / season-total / lagged-column selection and the
    `allowed_` prefixing are identical.

    `allowed_weekly` must already be unsuffixed (_unsuffix_allowed) and
    rolled (add_rolling_windows with group_cols=[team_id, position_group,
    season]).
    """
    allowed_weekly = allowed_weekly.copy()
    group_keys = ["team_id", "position_group", "season"]

    allowed_weekly["season_total_rz_touches_allowed"] = (
        allowed_weekly.groupby(group_keys)["rz_touches"].transform("sum")
    )
    cum_cols = []
    for band in ("gl", "i10", "rz"):
        for stat in ("touches", "tds"):
            col = f"{band}_{stat}"
            cum_col = f"cum_{col}"
            allowed_weekly[cum_col] = _cumulative_through_prior_week(allowed_weekly, col, group_keys)
            cum_cols.append(cum_col)

    keep = [c for c in allowed_weekly.columns if c.endswith(("_last1", "_last3", "_last5", "_season_avg"))]
    keep += cum_cols + ["season_total_rz_touches_allowed"]

    renamed = allowed_weekly[["team_id", "season", "week", "position_group"] + keep].rename(
        columns={**{c: f"allowed_{c}" for c in keep}, "team_id": "_join_def_team_id"}
    )
    out = weekly.merge(
        renamed,
        left_on=["opponent_team_id", "season", "week", "position_group"],
        right_on=["_join_def_team_id", "season", "week", "position_group"],
        how="left",
    )
    return out.drop(columns=["_join_def_team_id"])


# ===========================================================================
# TD Opportunity (§2)
# ===========================================================================
def score_td_opportunity_cfb(weekly: pd.DataFrame, config: dict = CONFIG) -> pd.DataFrame:
    """
    Score every row of `cfb_player_redzone_weekly` (a whole season's rows)
    for TD Opportunity. Returns `weekly` with proven_heat, emerging_heat,
    td_opportunity, td_opportunity_completeness and the intermediate
    percentile components appended.

    Only shift(1)'d / cumulative-through-prior-game inputs are used — the
    score reflects what was knowable heading INTO that game.

    td_opportunity_completeness (0-100) is the fraction of this pillar's 10
    percentile-normalized inputs (4 recent-production + 3 conversion-rate +
    3 trend) that were real values rather than neutral-50 fallback. The
    `snap_share` trend input is ALWAYS a fallback for CFB (no data source),
    so completeness is structurally capped at ~90% (Evidence Quality
    audit); early in a season the two other trend inputs and the thin
    rolling windows push it lower still.

    Body identical to nfl/scoring.py.score_td_opportunity apart from
    building the all-NaN `snap_share` column and rolling the windows here
    (NFL does that upstream in run_pipeline).
    """
    weekly = weekly.copy()
    # Permanent structural fallback (spec §8): no CFB snap-count source
    # exists. An all-NaN column rolls to all-NaN windows -> _trend_delta
    # NaN -> pct() records a fallback -> neutral 50, at weight 0.3.
    if "snap_share" not in weekly.columns:
        weekly["snap_share"] = np.nan

    weekly = add_rolling_windows(
        weekly,
        metrics=["rz_touches", "rz_touch_share", "rz_tds", "snap_share"],
        group_cols=["player_id", "season"],
    )

    ph_cfg = config["proven_heat"]
    eh_cfg = config["emerging_heat"]
    combo_cfg = config["combination"]
    k = config["shrinkage_k"]

    fallback_flags: list = []
    pct = _percentile_fn(weekly, config, track_fallback=fallback_flags)

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

    league_avg_gl_rate = _safe_ratio(weekly["gl_tds"].sum(), weekly["gl_touches"].sum())
    league_avg_i10_rate = _safe_ratio(weekly["i10_tds"].sum(), weekly["i10_touches"].sum())
    league_avg_rz_rate = _safe_ratio(weekly["rz_tds"].sum(), weekly["rz_touches"].sum())

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
    snap_share_trend_pct = pct(_trend_delta(weekly, "snap_share", 3))       # permanent fallback for CFB
    touch_volume_trend_pct = pct(_trend_delta(weekly, "rz_touches", 3))

    emerging_heat = (
        eh_cfg["touch_share_trend_weight"] * touch_share_trend_pct
        + eh_cfg["snap_share_trend_weight"] * snap_share_trend_pct
        + eh_cfg["touch_volume_trend_weight"] * touch_volume_trend_pct
    )

    # --- Combine: stronger signal sets the floor, weaker one can only add ---
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
    weekly["td_opportunity_completeness"] = (
        (1 - pd.concat(fallback_flags, axis=1).mean(axis=1)) * 100
    ).round(1)

    return weekly


# ===========================================================================
# Situation — Defensive Matchup Vulnerability half only (§3)
# ===========================================================================
def score_defensive_matchup_cfb(
    weekly: pd.DataFrame, allowed_weekly: pd.DataFrame, config: dict = CONFIG
) -> pd.DataFrame:
    """
    Score every offensive-player row for Defensive Matchup Vulnerability —
    the 0.7-weighted half of Situation (§3). The environment half
    (dome/wind/temp) and the `0.7·dmv + 0.3·env` blend are a separate task.

    `weekly`         — a whole season's `cfb_player_redzone_weekly` rows
                       (needs opponent_team_id + position_group).
    `allowed_weekly` — the same season's `cfb_defense_redzone_allowed_weekly`
                       rows (one per defense-position-week), suffixed as
                       stored.

    Returns `weekly` with recent_tds_allowed_pct, conversion_rate_allowed_pct,
    defensive_matchup_vulnerability, defensive_matchup_completeness, and
    situation_completeness appended. Every offensive player facing the same
    defense at the same position in the same week gets an identical
    defensive_matchup_vulnerability (it is a property of the matchup, not
    the player).

    Math (percentile fn, shrinkage, qualification at
    min_touches_allowed_for_qualification=20) is the
    nfl/scoring.py.score_situation defensive-matchup block verbatim.

    PLACEHOLDER (design decision c): situation_completeness is set equal to
    defensive_matchup_completeness for now — when the environment half
    lands it becomes mean(7 dmv inputs + 1 env input), matching NFL's
    8-input situation_completeness.
    """
    weekly = weekly.sort_values(["player_id", "season", "week"]).copy()

    allowed = _unsuffix_allowed(allowed_weekly)
    allowed = add_rolling_windows(
        allowed,
        metrics=["rz_touches", "rz_tds", "i10_touches", "i10_tds", "gl_touches", "gl_tds"],
        group_cols=["team_id", "position_group", "season"],
    )
    weekly = add_defensive_matchup_context(weekly, allowed)

    dm_cfg = config["defensive_matchup"]
    k = config["shrinkage_k"]

    qualified_allowed = (
        weekly["allowed_season_total_rz_touches_allowed"]
        >= config["min_touches_allowed_for_qualification"]
    )

    fallback_flags: list = []

    def pct_allowed(values: pd.Series) -> pd.Series:
        raw = percentile_lookup(values, build_reference_scale(values, qualified_allowed))
        fallback_flags.append(raw.isna())
        return fill_neutral(raw)

    # --- recency-weighted TDs allowed ---
    w = dm_cfg["recent_tds_allowed"]
    recent_tds_allowed_pct = (
        w["last1"] * pct_allowed(weekly["allowed_rz_tds_last1"])
        + w["last3"] * pct_allowed(weekly["allowed_rz_tds_last3"])
        + w["last5"] * pct_allowed(weekly["allowed_rz_tds_last5"])
        + w["season_avg"] * pct_allowed(weekly["allowed_rz_tds_season_avg"])
    )

    # --- shrinkage-adjusted conversion rate allowed, per band ---
    league_avg_gl_rate_allowed = _safe_ratio(allowed["gl_tds"].sum(), allowed["gl_touches"].sum())
    league_avg_i10_rate_allowed = _safe_ratio(allowed["i10_tds"].sum(), allowed["i10_touches"].sum())
    league_avg_rz_rate_allowed = _safe_ratio(allowed["rz_tds"].sum(), allowed["rz_touches"].sum())

    gl_rate_allowed = _shrink_rate(
        weekly["allowed_cum_gl_tds"], weekly["allowed_cum_gl_touches"], league_avg_gl_rate_allowed, k
    )
    i10_rate_allowed = _shrink_rate(
        weekly["allowed_cum_i10_tds"], weekly["allowed_cum_i10_touches"], league_avg_i10_rate_allowed, k
    )
    rz_rate_allowed = _shrink_rate(
        weekly["allowed_cum_rz_tds"], weekly["allowed_cum_rz_touches"], league_avg_rz_rate_allowed, k
    )

    conversion_rate_allowed_pct = pd.concat(
        [pct_allowed(gl_rate_allowed), pct_allowed(i10_rate_allowed), pct_allowed(rz_rate_allowed)], axis=1
    ).mean(axis=1)

    defensive_matchup_vulnerability = (
        dm_cfg["recent_tds_allowed_weight"] * recent_tds_allowed_pct
        + dm_cfg["conversion_rate_allowed_weight"] * conversion_rate_allowed_pct
    )

    weekly["recent_tds_allowed_pct"] = recent_tds_allowed_pct.round(1)
    weekly["conversion_rate_allowed_pct"] = conversion_rate_allowed_pct.round(1)
    weekly["defensive_matchup_vulnerability"] = defensive_matchup_vulnerability.round(1)

    dmc = ((1 - pd.concat(fallback_flags, axis=1).mean(axis=1)) * 100).round(1)
    weekly["defensive_matchup_completeness"] = dmc
    # PLACEHOLDER — equals dmc until the environment half of Situation is built.
    weekly["situation_completeness"] = dmc

    return weekly


def _safe_ratio(num, den) -> float:
    """league-average rate; a 0 or NaN denominator -> 0.0 (an uninformative
    but real prior, same spirit as _shrink_rate's touches=0 case)."""
    try:
        r = float(num) / float(den)
    except (TypeError, ZeroDivisionError):
        return 0.0
    return r if np.isfinite(r) else 0.0
