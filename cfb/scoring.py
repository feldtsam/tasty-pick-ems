"""
CFB v1 scoring — TD Opportunity (§2), the Situation pillar (§3, v1 =
defensive-matchup-only), and the redefined Role & Momentum pillar (§4).

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

Parameters: recency weights .35/.30/.20/.15,
min_rz_touches_for_qualification=15, min_touches_allowed_for_qualification
=20 (spec §8). Shrinkage k and the thin-sample rate gate were retuned
2026-09 from the CFB weeks-1-8 real-data validation (k 6->12,
min_cumulative_rz_touches_for_rate=15 added) — see CONFIG. `snap_share`
has no CFB source and is left as a PERMANENT structural fallback — an
all-NaN column fed through the existing neutral-50 fallback mechanism,
which caps td_opportunity_completeness at ~90% all season (Evidence
Quality audit).

FBS-OPPONENT FILTER: neither scorer excludes non-FBS-opponent rows on its
own — the caller drops them first with drop_non_fbs_opponent_rows() so
FCS blowout stats never enter a player's rolling windows OR the percentile
reference. Weeks 1-3 are ~25-33% FCS-opponent rows; negligible by week 5.

DEFERRED TO v2 (not silently dropped — flagged like Market Value, spec
§3/§6): the Environment sub-component of Situation (dome / wind / temp /
heat-index). The `0.7·dmv + 0.3·env` blend mechanism is in place now —
score_defensive_matchup_cfb combines `situation` through the same
present-columns renormalization score_universal_tpe uses for
market_value_score, so with no environment_score column `situation ==
defensive_matchup_vulnerability`, and the blend activates automatically
when v2 adds that column.

OUT OF SCOPE here: Evidence Quality; Market Value; core_weights / any
end-to-end pipeline wiring.

Role & Momentum (§4) consumes `cfb_player_role_weekly` rows assembled by
cfb/role_momentum.py (a separate, box-score + PPA ingest, unrelated to the
`/plays/stats` red-zone ingest the other two pillars share).
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
    # Phantom-touch prior strength for TD-conversion-rate shrinkage.
    # 2026-09: raised from the original §8-locked 6 -> 12 after the CFB
    # weeks-1-8 real-data validation. k=6 let a 2-for-2 goal-line game
    # percentile-rank at ~95th on a 2-touch sample; CFB players accumulate
    # red-zone touches far slower than NFL, so the NFL-tuned k under-shrinks
    # here. Applies to proven_heat's conversion rate AND DMV's
    # conversion-rate-allowed (both re-validated: DMV intuition unchanged).
    "shrinkage_k": 12,
    # Season-total red-zone touches for a player's rows to help DEFINE the
    # percentile reference scale. Every row is still scored against the
    # scale regardless of its own sample size (§8).
    "min_rz_touches_for_qualification": 15,
    # 2026-09: PILLAR-WIDE cold-start gate. A player-row whose cumulative RZ
    # touches THROUGH THE PRIOR WEEK are below this contributes nothing to
    # TD Opportunity — every percentile input (proven_heat's rates AND
    # emerging_heat's trends) is forced to the neutral-50 fallback and
    # td_opportunity is exactly 50. Deliberately equals
    # min_rz_touches_for_qualification — "enough sample to define the scale"
    # and "enough sample to be scored against it" are the same bar. Same
    # cold-start pattern as Role & Momentum. (Name kept from the earlier
    # proven_heat-only version.) See score_td_opportunity_cfb.
    "min_cumulative_rz_touches_for_rate": 15,
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
    "situation": {
        # Sub-component weights for the Situation pillar. NFL's Situation is
        # `0.7·defensive_matchup_vulnerability + 0.3·environment_score`.
        # CFB v1 ships ONLY the defensive-matchup sub-component — the
        # Environment sub-component (dome / wind / temp / heat-index) is
        # DEFERRED TO v2 alongside Market Value (spec §3, §6). It is not
        # silently dropped: environment_score simply never becomes a column
        # in v1, and `situation` is combined by the SAME present-columns
        # renormalization score_universal_tpe uses for market_value_score's
        # absence from core_weights — with only defensive_matchup_
        # vulnerability present its weight renormalizes 0.7/0.7 -> 1.0, so
        # situation == defensive_matchup_vulnerability exactly. When
        # environment_score is built and added as a column in v2 the 70/30
        # blend activates here with no code change.
        "sub_weights": {
            "defensive_matchup_vulnerability": 0.7,
            "environment_score": 0.3,
        },
    },
    "role_momentum": {
        # Locked redefinition (2026-09): NFL's 4 inputs (snap_share_trend,
        # depth_chart_movement, external_opportunity, touch_share_trend) have
        # no CFB data path except the last. New structure = 2 scored trend
        # inputs + 1 completeness modifier.
        "touch_share_trend_weight": 0.70,
        "ppa_trend_weight": 0.30,
        # Both trends are last-`trend_window`-mean vs season-to-date (the
        # shared _trend_delta), so real values only appear from a player's
        # (window+1)th game — Role & Momentum is neutral-50 for roughly the
        # first 4 games of every player-season. Accepted: it is a 12% pillar
        # and Evidence Quality down-weights its low early-season completeness.
        "trend_window": 3,
        # Season-total touches for a player's rows to help DEFINE the
        # percentile reference scale (every row is still scored against the
        # scale regardless of its own volume — same rule as the other
        # pillars' _qualified_mask).
        "min_touches_for_qualification": 20,
        # Reliability discount applied to role_momentum_COMPLETENESS ONLY
        # (never the score) when a player has no prior-season box-score
        # history for this team — a transfer-in or a true freshman, both a
        # genuinely thin CFB-team sample. Derived from box-score
        # id-continuity (cfb/role_momentum.py), NOT a /player/portal or
        # /player/search call — both were evaluated and passed over.
        # 1.0 = returning / established, < 1.0 = new to team.
        "new_team_completeness_factor": 0.75,
    },
    # Locked (spec §5): Evidence Quality is a pure meta-layer over the
    # three pillars' own outputs, ported verbatim from nfl/scoring.py's
    # score_evidence_quality with only the family/completeness column
    # lists narrowed to CFB's real 3 pillars (Market Value doesn't exist
    # for CFB at all — not "present but renormalizing away" the way it
    # is on the NFL side, so it's never listed here). signal_convergence/
    # signal_breach are DELIBERATELY NOT ported — spec §5 hard-gates both
    # ("do not ship these two booleans for CFB until a tuned backfill
    # exists"); their NFL thresholds were reverse-engineered from an NFL
    # backfill and have no CFB-data grounding yet.
    "evidence_quality": {
        "family_score_columns": ["td_opportunity", "situation", "role_momentum"],
        "completeness_columns": [
            "td_opportunity_completeness", "situation_completeness", "role_momentum_completeness",
        ],
    },
    # Locked (spec §0): final CFB v1 pillar weights. Market Value is
    # DEFERRED TO v2 (spec §6) — simply absent from core_weights, not
    # present-with-zero, so score_universal_tpe_cfb's present-columns
    # renormalization never has to special-case it (there's nothing to
    # renormalize away — unlike NFL, where market_value_score IS listed
    # here and renormalizes off on every historical row that lacks it).
    "universal_tpe": {
        "core_weights": {
            "td_opportunity": 53,
            "situation": 35,
            "role_momentum": 12,
        },
        # tpe_score = core_score * (confidence_floor + (1 - confidence_floor) * evidence_quality / 100).
        # Locked (spec §5 pseudocode) — identical formula and floor to
        # NFL's own, not re-derived: `confidence_multiplier = 0.5 + 0.5 ·
        # (evidence_quality / 100)`.
        "confidence_floor": 0.5,
    },
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


def drop_non_fbs_opponent_rows(weekly: pd.DataFrame, fbs_team_ids: set[int]) -> pd.DataFrame:
    """
    Keep only rows where BOTH `team_id` and `opponent_team_id` are FBS.

    Apply this to `cfb_player_redzone_weekly` and `cfb_defense_redzone_
    allowed_weekly` BEFORE scoring. Dropping the row entirely (not just
    excluding it from the reference) is deliberate: an FBS RB's 3-TD game
    against an FCS opponent should not sit in his rolling windows or
    cumulative rate for the rest of the season, and an FCS team's "allowed"
    row (or an FBS team's row from an FCS-blowout week) should not help
    define where the percentile boundaries fall.

    `fbs_team_ids` — CFBD `/teams/fbs?year=<season>` ids for that season
    (cfb/ids.fbs_team_ids). No-op if the frame has no team-id columns.
    """
    ids = set(fbs_team_ids)
    cols = [c for c in ("team_id", "opponent_team_id") if c in weekly.columns]
    if not cols:
        return weekly
    mask = pd.Series(True, index=weekly.index)
    for c in cols:
        mask &= weekly[c].isin(ids)
    return weekly[mask].copy()


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
    audit); early in a season the two other trend inputs, the thin rolling
    windows, and the pillar-wide thin-sample gate (below) push it lower.

    PILLAR-WIDE THIN-SAMPLE GATE (2026-09, from the weeks-1-8 real-data
    validation): until a player's cumulative RZ touches through the prior
    week reach config["min_cumulative_rz_touches_for_rate"] (=15), the row
    contributes NOTHING to the pillar — every one of the 10 percentile
    inputs is forced to the neutral-50 fallback, `proven_heat` and
    `emerging_heat` are 50, `td_opportunity` is exactly 50, and
    td_opportunity_completeness is 0. Same cold-start pattern as Role &
    Momentum. An earlier version gated only the `proven_heat` (rate /
    recent-production) inputs; the weeks-1-8 run showed the exact same
    1-3-touch backups then re-surfaced at the top of the board through
    `emerging_heat`, because a trend computed on a handful of touches
    percentile-ranks just as high. `td_opportunity_gated` (bool) and
    `cum_rz_touches_prior` / `player_games_played` are on the output row so
    a gated fluke is filterable, not just flagged by completeness.

    ACCEPTED TRADEOFF: a genuine low-volume, high-conversion back (a
    big-play RB whose team spreads its RZ carries) stays neutral all
    season if he never clears 15 cumulative RZ touches — on that few
    touches a high conversion rate is not trustworthy, and TD Opportunity
    is a workload pillar. Such a player still gets his due from the other
    pillars.

    Body follows nfl/scoring.py.score_td_opportunity apart from building
    the all-NaN `snap_share` column, rolling the windows here (NFL does
    that upstream in run_pipeline), and the CFB gate above.
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

    # Pillar-wide thin-sample gate (2026-09): a player whose cumulative RZ
    # touches THROUGH THE PRIOR WEEK are below the minimum has no
    # trustworthy signal on EITHER half — proven_heat's rates and
    # emerging_heat's trends are all built on a handful of touches. NaN
    # every percentile input for those rows so pct() routes them through
    # neutral-50 and they register as incomplete; td_opportunity is then
    # forced to exactly 50 below. Same cold-start pattern as Role &
    # Momentum. (An earlier version gated only proven_heat; the weeks-1-8
    # run showed the same 1-3-touch backups re-surfaced via emerging_heat.)
    cum_rz_touches = _season_cumulative(weekly, "rz_touches")
    cum_rz_tds = _season_cumulative(weekly, "rz_tds")
    thin_sample = cum_rz_touches < config["min_cumulative_rz_touches_for_rate"]
    for _col in ("rz_tds_last1", "rz_tds_last3", "rz_tds_last5", "rz_tds_season_avg"):
        weekly.loc[thin_sample, _col] = np.nan

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

    league_avg_gl_rate = _safe_ratio(weekly["gl_tds"].sum(), weekly["gl_touches"].sum())
    league_avg_i10_rate = _safe_ratio(weekly["i10_tds"].sum(), weekly["i10_touches"].sum())
    league_avg_rz_rate = _safe_ratio(weekly["rz_tds"].sum(), weekly["rz_touches"].sum())

    gl_rate = _shrink_rate(cum_gl_tds, cum_gl_touches, league_avg_gl_rate, k).where(~thin_sample)
    i10_rate = _shrink_rate(cum_i10_tds, cum_i10_touches, league_avg_i10_rate, k).where(~thin_sample)
    rz_rate = _shrink_rate(cum_rz_tds, cum_rz_touches, league_avg_rz_rate, k).where(~thin_sample)

    conversion_rate_pct = pd.concat([pct(gl_rate), pct(i10_rate), pct(rz_rate)], axis=1).mean(axis=1)

    proven_heat = (
        ph_cfg["recent_td_production_weight"] * recent_td_production_pct
        + ph_cfg["conversion_rate_weight"] * conversion_rate_pct
    )

    # --- Emerging Heat: touch-share / snap-share / touch-volume trend ---
    # Gated by the same thin_sample mask as proven_heat — a trend on <15
    # cumulative touches is noise, not a signal (see the docstring).
    touch_share_trend_pct = pct(_trend_delta(weekly, "rz_touch_share", 3).where(~thin_sample))
    snap_share_trend_pct = pct(_trend_delta(weekly, "snap_share", 3))       # permanent fallback for CFB
    touch_volume_trend_pct = pct(_trend_delta(weekly, "rz_touches", 3).where(~thin_sample))

    emerging_heat = (
        eh_cfg["touch_share_trend_weight"] * touch_share_trend_pct
        + eh_cfg["snap_share_trend_weight"] * snap_share_trend_pct
        + eh_cfg["touch_volume_trend_weight"] * touch_volume_trend_pct
    )

    # --- Combine: stronger signal sets the floor, weaker one can only add ---
    hi = np.maximum(proven_heat, emerging_heat)
    lo = np.minimum(proven_heat, emerging_heat)
    td_opportunity = (hi + combo_cfg["bonus_weight"] * lo * (1 - hi / 100)).clip(0, 100)

    # Pillar-wide gate: a thin-sample row is exactly neutral on both halves
    # and on the final score — it does not rank in a score-sorted view.
    proven_heat = proven_heat.mask(thin_sample, 50.0)
    emerging_heat = emerging_heat.mask(thin_sample, 50.0)
    td_opportunity = td_opportunity.mask(thin_sample, 50.0)

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

    # Sample-size context for downstream filtering (Evidence Quality, a
    # "top RZ backs" view): games played INCLUDING this one, cumulative RZ
    # touches THROUGH THE PRIOR WEEK (what the gate tests), and whether the
    # gate fired on this row.
    weekly["player_games_played"] = weekly.groupby(["player_id", "season"]).cumcount() + 1
    weekly["cum_rz_touches_prior"] = cum_rz_touches.astype(int)
    weekly["td_opportunity_gated"] = thin_sample

    return weekly


# ===========================================================================
# Situation (§3) — CFB v1: defensive-matchup-only; Environment deferred to v2
# ===========================================================================
def score_defensive_matchup_cfb(
    weekly: pd.DataFrame, allowed_weekly: pd.DataFrame, config: dict = CONFIG
) -> pd.DataFrame:
    """
    Score every offensive-player row for the Situation pillar (§3).

    NFL's Situation is `0.7·defensive_matchup_vulnerability + 0.3·
    environment_score`. CFB v1 ships ONLY the defensive-matchup sub-
    component — the Environment sub-component (dome / wind / temp / heat-
    index) is DEFERRED TO v2 alongside Market Value (spec §3, §6).

    `situation` is still combined through a sub-weight blend
    (config["situation"]["sub_weights"]), using the SAME present-columns
    renormalization score_universal_tpe applies to market_value_score's
    absence from core_weights — NOT a hardcoded weight-1.0 special case.
    In v1 only defensive_matchup_vulnerability is a column, so its 0.7
    weight renormalizes 0.7/0.7 -> 1.0 and `situation ==
    defensive_matchup_vulnerability` exactly. When an environment_score
    column is added in v2 the 70/30 blend takes effect here with no code
    change.

    `weekly`         — a whole season's `cfb_player_redzone_weekly` rows
                       (needs opponent_team_id + position_group).
    `allowed_weekly` — the same season's `cfb_defense_redzone_allowed_weekly`
                       rows (one per defense-position-week), suffixed as
                       stored.

    Returns `weekly` with recent_tds_allowed_pct, conversion_rate_allowed_pct,
    defensive_matchup_vulnerability, situation, defensive_matchup_completeness,
    and situation_completeness appended. Every offensive player facing the
    same defense at the same position in the same week gets an identical
    defensive_matchup_vulnerability (and, in v1, situation) — it is a
    property of the matchup, not the player.

    Math (percentile fn, shrinkage, qualification at
    min_touches_allowed_for_qualification=20) is the
    nfl/scoring.py.score_situation defensive-matchup block verbatim.

    situation_completeness equals defensive_matchup_completeness in v1
    (Situation has no other sub-component). In v2 it becomes mean(7 dmv
    inputs + 1 env input), matching NFL's 8-input situation_completeness.
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

    # --- Situation = present-sub-components renormalized blend (§3). ---
    # Identical per-row mechanic to score_universal_tpe's core_weights
    # handling of market_value_score's absence: filter the sub-weight vector
    # to the sub-components that are actually columns, then divide the
    # weighted sum by the present weight total. v1 has only
    # defensive_matchup_vulnerability -> situation == it exactly. v2 adds an
    # environment_score column and the 0.7/0.3 blend activates with no
    # change here.
    sub_weights = config["situation"]["sub_weights"]
    present_sub = [c for c in sub_weights if c in weekly.columns]
    sub_scores = weekly[present_sub]
    sub_wv = pd.Series({c: sub_weights[c] for c in present_sub})
    sub_valid = sub_scores.notna()
    situation = (sub_scores.fillna(0) * sub_wv).sum(axis=1) / (sub_valid * sub_wv).sum(axis=1)
    weekly["situation"] = situation.round(1)

    dmc = ((1 - pd.concat(fallback_flags, axis=1).mean(axis=1)) * 100).round(1)
    weekly["defensive_matchup_completeness"] = dmc
    # v1: Situation is defensive-matchup-only (Environment deferred to v2,
    # spec §3/§6), so situation_completeness == defensive_matchup_completeness
    # with no environment input referenced. v2: mean(7 dmv inputs + 1 env
    # input), matching NFL's 8-input situation_completeness.
    weekly["situation_completeness"] = dmc

    return weekly


# ===========================================================================
# Role & Momentum (§4) — REDEFINED for CFB
# ===========================================================================
def score_role_momentum_cfb(weekly: pd.DataFrame, config: dict = CONFIG) -> pd.DataFrame:
    """
    Score every row of `cfb_player_role_weekly` (a whole season's rows, one
    per RB/WR/TE per game — assembled by cfb/role_momentum.py) for Role &
    Momentum.

        role_momentum = 0.70 * pct(touch_share_trend) + 0.30 * pct(ppa_trend)

    Two scored inputs, both a 3-game-smoothed trend (last-3-game mean minus
    season-to-date average, the shared _trend_delta), then percentile-
    normalized against the qualified reference population:

      * touch_share_trend  — player touches / his offense's total touches
        (rush attempts + receptions, QB keepers included in the
        denominator), trended. `touch_share` is supplied on the input row.
      * ppa_trend           — CFBD averagePPA.all per player-game
        (/ppa/players/games), trended. A CFB-native efficiency axis, no NFL
        precedent. `ppa` is supplied on the input row; NaN where CFBD
        attributed no PPA that game (verified ~3% of player-games, almost
        all 1-touch cameos).

    RENORMALIZATION FALLBACK (mirrors score_universal_tpe's per-row pillar
    renorm): if a player has NO prior-game PPA at all when the trend is
    needed — `ppa_trend` NaN while `touch_share_trend` is real — the PPA
    term is dropped and touch_share_trend carries 100% weight for that row
    (`_rm_ppa_renormed = True`). This is distinct from thin history (both
    trends NaN for a player's first `trend_window`+1 games → both fall to
    neutral 50 → role_momentum ~= 50). Verified rare on real data.

    role_momentum_completeness (0-100) = weight-proportional realness of the
    two trend inputs (0.70 if only touch_share_trend is real, 1.0 if both,
    0.0 for a thin-history early-season row) multiplied by a reliability
    factor: 1.0 for a returning/established player, config's
    new_team_completeness_factor (< 1.0) for a player with no prior-season
    box-score history for this team (`is_returning == False` on the input
    row). The factor touches completeness only, never the score (§4.3).

    DROPPED for CFB, not proxied (no viable data path, and PPA does not
    conceptually substitute for opportunity context): depth_chart_movement,
    external_opportunity.

    Only shift(1)'d trend inputs are used — the score reflects what was
    knowable heading INTO that game. Same DataFrame-in / DataFrame-out shape
    as score_td_opportunity_cfb / score_defensive_matchup_cfb.
    """
    weekly = weekly.sort_values(["player_id", "season", "week"]).copy()
    rm_cfg = config["role_momentum"]
    window = rm_cfg["trend_window"]
    tw = rm_cfg["touch_share_trend_weight"]
    pw = rm_cfg["ppa_trend_weight"]

    # Degrade cleanly if the ingestion layer didn't attach a column.
    if "ppa" not in weekly.columns:
        weekly["ppa"] = np.nan
    if "is_returning" not in weekly.columns:
        weekly["is_returning"] = np.nan  # unknown -> no completeness discount

    weekly = add_rolling_windows(
        weekly, metrics=["touch_share", "ppa"], group_cols=["player_id", "season"]
    )

    # Percentile scale defined by players whose season-total touches clear
    # the minimum (the _qualified_mask rule, on `touches` here).
    season_total_touches = weekly.groupby(["player_id", "season"])["touches"].transform("sum")
    qualified = season_total_touches >= rm_cfg["min_touches_for_qualification"]

    fallback_flags: list = []

    def pct(values: pd.Series) -> pd.Series:
        raw = percentile_lookup(values, build_reference_scale(values, qualified))
        fallback_flags.append(raw.isna())
        return fill_neutral(raw)

    ts_delta = _trend_delta(weekly, "touch_share", window)
    ppa_delta = _trend_delta(weekly, "ppa", window)

    ts_pct = pct(ts_delta)
    ppa_pct = pct(ppa_delta)

    # PPA genuinely absent (no prior-game PPA for this player at all) vs.
    # merely thin history: touch_share_trend real, ppa_trend NaN. Both share
    # the same `games_played > window` mask, so ts real => past the mask =>
    # a NaN ppa_delta here can only mean no prior-game PPA ever existed.
    ppa_renormed = ppa_delta.isna() & ts_delta.notna()

    blended = tw * ts_pct + pw * ppa_pct
    role_momentum = pd.Series(
        np.where(ppa_renormed, ts_pct, blended), index=weekly.index
    ).clip(0, 100)

    # completeness: weight-proportional input realness, then the team-history
    # reliability factor.
    ts_real = ~fallback_flags[0]
    ppa_real = (~fallback_flags[1]) & (~ppa_renormed)
    input_realness = tw * ts_real.astype(float) + pw * ppa_real.astype(float)
    team_factor = np.where(
        weekly["is_returning"] == False,  # noqa: E712 — explicit: NaN/unknown must NOT discount
        rm_cfg["new_team_completeness_factor"],
        1.0,
    )
    role_momentum_completeness = pd.Series(
        input_realness.to_numpy() * team_factor * 100, index=weekly.index
    )

    weekly["_rm_touch_share_trend_pct"] = ts_pct.round(1)
    weekly["_rm_ppa_trend_pct"] = ppa_pct.round(1)
    weekly["_rm_ppa_renormed"] = ppa_renormed
    weekly["role_momentum"] = role_momentum.round(1)
    weekly["role_momentum_completeness"] = role_momentum_completeness.round(1)

    return weekly


def _safe_ratio(num, den) -> float:
    """league-average rate; a 0 or NaN denominator -> 0.0 (an uninformative
    but real prior, same spirit as _shrink_rate's touches=0 case)."""
    try:
        r = float(num) / float(den)
    except (TypeError, ZeroDivisionError):
        return 0.0
    return r if np.isfinite(r) else 0.0


# ===========================================================================
# Evidence Quality & Universal TPE Score (§5 / §0) — CFB v1
# ===========================================================================
# Added 2026-09 (Track B: curation/orchestration layer). Neither function
# existed for CFB before this — cfb/scoring.py's own module docstring long
# listed "Evidence Quality... core_weights / any end-to-end pipeline wiring"
# as OUT OF SCOPE, and every prior reference to score_universal_tpe in this
# file (Situation's/Role & Momentum's renormalization comments) was pointing
# at the NFL pattern by ANALOGY, not calling a real CFB function — confirmed
# directly (grep), not assumed, before writing these. Ported verbatim from
# nfl/scoring.py's score_evidence_quality/score_universal_tpe — same
# "duplicate rather than cross-import" rule this whole file already follows
# — with the family/completeness lists and core_weights narrowed to CFB's
# real 3 pillars (see CONFIG["evidence_quality"]/CONFIG["universal_tpe"]'s
# own comments for exactly what changed and why). The orchestration/write
# layer that calls these (cfb/api/curate_cfb_shelves.py) does not recompute
# any of this math itself — it only calls these two functions, matching the
# NFL split between reconcile_week.py (scoring) and curate_home_shelves.py
# (shelf assignment + write).


def score_evidence_quality_cfb(weekly: pd.DataFrame, config: dict = CONFIG) -> pd.DataFrame:
    """
    Score every row for Evidence Quality (§5) — a pure meta-layer over
    TD Opportunity / Situation / Role & Momentum's own outputs. Must run
    AFTER all three (score_td_opportunity_cfb, score_defensive_matchup_cfb,
    score_role_momentum_cfb) — reads their *_completeness columns and
    final scores directly; nothing here is derivable from raw ingested
    rows.

    completeness = mean(td_opportunity_completeness, situation_completeness,
                         role_momentum_completeness) — config["evidence_
                         quality"]["completeness_columns"], read via
                         column-existence checks (never asserted present),
                         so a caller that hasn't merged in all three pillars
                         yet degrades gracefully rather than KeyError-ing.

    convergence = 100 - range(family scores) — direction-agnostic
                  agreement across however many of config["evidence_
                  quality"]["family_score_columns"] (td_opportunity,
                  situation, role_momentum — CFB has no market_value_score
                  column to include, unlike NFL) are actually present on a
                  given row. Rows with fewer than 2 real family scores get
                  neutral-50 convergence (a "range" of one value is
                  meaningless).

    evidence_quality = sqrt(completeness * convergence) — geometric mean,
                        not an average, so either axis near zero craters
                        the combined read. Identical formula to NFL's.

    DELIBERATELY NOT ported: signal_convergence / signal_breach. Spec §5
    hard-gates both for CFB ("do not ship these two booleans... until a
    tuned backfill exists") — their NFL thresholds (spread_threshold=80,
    strength_floor=60) were reverse-engineered from an NFL backfill with
    no CFB-data grounding. Recomputing them here with borrowed NFL
    thresholds would be exactly the silently-wrong-number failure mode
    the spec's hard gate exists to prevent, not a shortcut worth taking.

    Returns weekly with evidence_completeness, evidence_convergence, and
    evidence_quality appended (0-100 each).
    """
    weekly = weekly.copy()
    eq_cfg = config["evidence_quality"]

    completeness_cols = [c for c in eq_cfg["completeness_columns"] if c in weekly.columns]
    completeness = weekly[completeness_cols].mean(axis=1)

    family_cols = [c for c in eq_cfg["family_score_columns"] if c in weekly.columns]
    family_scores = weekly[family_cols]
    n_present = family_scores.notna().sum(axis=1)
    score_range = family_scores.max(axis=1) - family_scores.min(axis=1)
    convergence = fill_neutral(pd.Series(np.where(n_present >= 2, 100.0 - score_range, np.nan), index=weekly.index))

    evidence_quality = np.sqrt(completeness * convergence)

    weekly["evidence_completeness"] = completeness.round(1)
    weekly["evidence_convergence"] = convergence.round(1)
    weekly["evidence_quality"] = evidence_quality.round(1)

    return weekly


def score_universal_tpe_cfb(weekly: pd.DataFrame, config: dict = CONFIG) -> pd.DataFrame:
    """
    Score every row for the final CFB Universal TPE Score (§0/§5). Must
    run after score_td_opportunity_cfb, score_defensive_matchup_cfb,
    score_role_momentum_cfb, AND score_evidence_quality_cfb (reads all
    four's outputs directly) — the last step, not a fourth independent
    pillar computation.

    No `market_value` parameter, unlike NFL's score_universal_tpe — CFB
    Market Value is deferred to v2 (spec §6) and has no snapshot table to
    merge in at all yet, so there is nothing analogous to NFL's optional
    live-poll merge to thread through here. When v2 adds a real
    market_value_score column, it need only be added to CONFIG["universal_
    tpe"]["core_weights"] for the renormalization below to pick it up —
    no change to this function.

    core_score = weighted sum of td_opportunity/situation/role_momentum,
                 present-columns-only, weights renormalized to sum to 100
                 over whichever of CONFIG["universal_tpe"]["core_weights"]
                 are actually present on a given row. This is the SAME
                 per-row renormalization score_defensive_matchup_cfb
                 already uses for Situation's own env sub-component
                 (spec §3) — here it's what makes Role & Momentum's
                 current lack of any real deployed ingestion (cfb_player_
                 role_weekly has no write path yet — 2026-09 investigation)
                 degrade honestly rather than crash: a row with real
                 td_opportunity/situation but role_momentum entirely
                 absent (NaN, not just low-completeness) renormalizes
                 core_score over the remaining 88 rather than being scored
                 against a 100-point ceiling it can never reach.

    confidence_multiplier = confidence_floor + (1 - confidence_floor) *
                             (evidence_quality / 100)
    tpe_score = core_score * confidence_multiplier

    Returns weekly with core_score, confidence_multiplier, and tpe_score
    appended.
    """
    weekly = weekly.copy()
    tpe_cfg = config["universal_tpe"]
    weights = tpe_cfg["core_weights"]

    present_cols = [c for c in weights if c in weekly.columns]
    scores = weekly[present_cols]
    weight_vec = pd.Series({c: weights[c] for c in present_cols})

    valid = scores.notna()
    weighted_sum = (scores.fillna(0) * weight_vec).sum(axis=1)
    weight_totals = (valid * weight_vec).sum(axis=1)
    core_score = weighted_sum / weight_totals

    floor = tpe_cfg["confidence_floor"]
    confidence_multiplier = floor + (1 - floor) * (weekly["evidence_quality"] / 100)
    tpe_score = core_score * confidence_multiplier

    weekly["core_score"] = core_score.round(1)
    weekly["confidence_multiplier"] = confidence_multiplier.round(3)
    weekly["tpe_score"] = tpe_score.round(1)

    return weekly
