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

DEPTH-CHART GAP, RESOLVED for depth_chart_rank (redzone.add_depth_chart_
rank now parses both the pre-2025 and 2025+ schemas transparently, see
redzone._new_schema_depth_chart) - depth_chart_movement_pct through Role
Trend is real for 2025 rows now, not a neutral fallback. Two things
worth knowing rather than assuming: (1) a factual correction to what was
claimed here before - the new schema does have a gsis_id, populated for
~99% of rows, it was never actually missing; (2) add_injury_context is
STILL old-schema-only (redzone.py, not fixed here) - ahead_injury_
statuses is still empty for every 2025+ row, a related but separate,
explicitly out-of-scope gap for now.

Situation (20% weight) - two independent contextual modifiers, combined
as a plain weighted average, NOT the max/bonus mechanic used above. That
mechanic exists for two readings of the same underlying thing where one
might be a stale echo of the other (Proven vs Emerging, Role Trend vs
injury lag). Defensive matchup and weather don't have that relationship
to each other - they're genuinely independent, so averaging them is the
honest combination, not a workaround:
  Defensive Matchup     - mirrors Proven Heat's structure exactly
  Vulnerability            (recency-weighted TDs allowed + shrinkage-
                            adjusted conversion rate allowed), just
                            measuring what a defense ALLOWS to a player's
                            position group instead of what the player
                            PRODUCES. Recency-weighted per the blueprint's
                            "developing weakness, not just a bad defense"
                            framing.
  Environment            - dome/closed roof -> fully controlled (100).
                            Outdoors -> a simple wind/temp blend, fixed
                            formula (not a percentile - see
                            score_situation). Deliberately simple, per the
                            blueprint's own "lightly weighted" framing for
                            this component.

Explicitly out of scope for Situation, per instruction rather than
oversight: game script / team totals (belongs with Market Value once odds
are wired in) and offensive line / QB-connection / coaching-tendency
signals (no clean nfl_data_py source - flagged as a known gap, no proxy
built).

Evidence Quality & Convergence (10% weight) - unlike every pillar above,
needs no new data at all: a pure meta-layer over TD Opportunity's, Role &
Momentum's, and Situation's own outputs, so it must run after all three
(see score_evidence_quality). Two axes, combined as a genuine AND
relationship (geometric mean, not max/bonus or weighted average - a new
combination shape in this module, deliberately, because neither axis
alone is sufficient the way the sub-components of the other pillars are):
  Completeness  - what fraction of the three pillars' percentile inputs
                   were real values vs neutral-50 fallback, exposed by
                   each pillar via _percentile_fn's optional
                   track_fallback accumulator rather than re-derived from
                   raw columns.
  Convergence   - do the signal FAMILIES agree with each other, direction-
                   agnostic (100 - range of the family scores) -
                   deliberately NOT "how many score above a bullish
                   threshold," which would conflate agreement with
                   directionality that the other four pillars already
                   own. Four unanimously bearish families are in just as
                   much agreement as four unanimously bullish ones.

Market Value doesn't have a comparable 0-100 score yet (snapshot-only,
see market_value.py) and isn't backfilled into historical rows at all -
convergence runs over however many of the four families are actually
present on a row (currently 3, typically), not a hardcoded 4.

Universal TPE Score - score_universal_tpe combines all five pillars, but
NOT as a literal five-way additive weighted sum, despite the blueprint
stating Evidence Quality & Convergence as a flat "10% weight." That's a
deliberate, documented deviation, not an oversight:

  core_score = weighted sum of TD Opportunity (30) / Role & Momentum (20)
               / Situation (20) / Market Value (20), present-pillars-only
               with renormalization (Market Value is absent for every
               historical row - see market_value.py - so core_score
               renormalizes over the remaining 70 for those rows, same
               "score what's there" philosophy as everywhere else).

  confidence_multiplier = 0.5 + 0.5 * (evidence_quality / 100)

  tpe_score = core_score * confidence_multiplier

Evidence Quality & Convergence is structurally a META-score - a
statement about how much to trust the other four numbers, not a fifth
independent opinion on the opportunity itself the way they are. Folding
it into the same additive weighted sum as a flat 10% slice caps its
influence at +-10 points regardless of how contradictory or thin the
underlying evidence is: a player with wildly conflicting pillar reads
(e.g. TD Opportunity loves him, Role & Momentum hates him) would still
net a TPE score dominated by the raw blend of those conflicting numbers,
reading like a solid pick despite the evidence not actually supporting
that read. Acting as a confidence multiplier on the combined core_score
instead lets it meaningfully discount (or, near evidence_quality=100,
barely touch) the final score in proportion to how much the evidence
actually supports it - the whole reason this pillar exists.

The 0.5 floor exists because evidence_quality=50 is this system's
convention for "unremarkable" (neutral fallback, or genuinely average
completeness/convergence), not "bad" - the same convention every other
neutral-50 fallback in this module already uses. A naive
core_score * (evidence_quality/100) would halve an entirely ordinary
row's score just for being average, which the floor prevents: at
evidence_quality=50 the multiplier is 0.75 (a mild, not punitive,
discount); at 100 it's 1.0 (full credit, score untouched); at 0 it's 0.5
(the most a genuinely contradictory/unsupported read can be discounted -
not zeroed out entirely, since the underlying opportunity may still be
real even when the evidence for it is shaky). Floor value is a starting
point ("hypothesis to tune"), same as every other constant in this
module.
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
    "defensive_matchup": {
        # Mirrors proven_heat's split and internal recency weights exactly
        # - same conceptual shape (recency-weighted production +
        # shrinkage-adjusted rate), just measuring what's allowed instead
        # of what's produced.
        "recent_tds_allowed_weight": 0.6,
        "conversion_rate_allowed_weight": 0.4,
        "recent_tds_allowed": {
            "last1": 0.35,
            "last3": 0.30,
            "last5": 0.20,
            "season_avg": 0.15,
        },
    },
    # Season-total touches allowed a (defteam, position_group, season)
    # combo needs to help define the defensive reference scale. Same
    # "define the scale honestly, still score everyone against it"
    # philosophy as min_rz_touches_for_qualification, just a higher number
    # since it's a whole defense's exposure rather than one player's usage.
    "min_touches_allowed_for_qualification": 20,
    "environment": {
        # Rough starting constants, not researched - same "hypothesis to
        # calibrate later" treatment as every other weight in this module.
        "wind_ceiling_mph": 20.0,
        "temp_comfort_f": 50.0,
        "wind_weight": 0.5,
        "temp_weight": 0.5,
    },
    "situation": {
        "defensive_matchup_weight": 0.7,
        "environment_weight": 0.3,
    },
    "evidence_quality": {
        # The four signal-family scores convergence checks agreement
        # across, and the per-family completeness columns averaged for
        # the completeness sub-score. market_value_score doesn't exist
        # yet (Market Value is snapshot-only so far, no comparable 0-100
        # score) -- listed here anyway so it's picked up automatically,
        # for whichever rows it's actually present on, once it exists.
        # Both lists are read via column-existence checks, not asserted
        # to all be present -- convergence and completeness both operate
        # over however many of these are actually on a given row (2-4),
        # not a hardcoded 4. See score_evidence_quality.
        "family_score_columns": ["td_opportunity", "role_momentum", "situation", "market_value_score"],
        "completeness_columns": ["td_opportunity_completeness", "role_momentum_completeness", "situation_completeness"],
    },
    "universal_tpe": {
        # The blueprint's stated pillar weights, applied to everything
        # EXCEPT evidence_quality -- see module docstring for why that
        # one acts as a confidence multiplier instead of a fifth additive
        # term. Read via column-existence + per-row notna checks (not
        # asserted present), so market_value_score's absence on every
        # historical row renormalizes these three to sum to 100 rather
        # than silently scoring against a 70-point ceiling.
        "core_weights": {
            "td_opportunity": 30,
            "role_momentum": 20,
            "situation": 20,
            "market_value_score": 20,
        },
        # tpe_score = core_score * (confidence_floor + (1 - confidence_floor) * evidence_quality / 100).
        # At evidence_quality=50 (this system's "unremarkable," not
        # "bad," convention) the multiplier is 0.75, a mild discount, not
        # a halving. Starting point, not researched -- same "hypothesis
        # to tune later" treatment as every other constant here.
        "confidence_floor": 0.5,
    },
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


def _percentile_fn(weekly: pd.DataFrame, config: dict, track_fallback: list = None):
    """
    Returns a pct(values) closure bound to this weekly table's qualified
    reference population. Behavior (the actual returned percentiles) is
    completely unchanged from before track_fallback existed — passing it
    is purely additive instrumentation, not a scoring change.

    track_fallback: an optional list. If provided, pct() appends a
    boolean Series to it on every call — True where that call's result
    was a neutral-50 fallback (the raw percentile was NaN before
    fill_neutral), False where it was a real value. Lets a scoring
    function expose "what fraction of my inputs were real" (see
    score_evidence_quality) just by passing the same list through every
    pct() call it already makes, without re-deriving anything from raw
    columns after the fact.
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
    Recent-window mean minus season-to-date average, e.g.
    rz_touch_share_last3 - rz_touch_share_season_avg for window=3. Positive
    = trending up. Reads columns add_rolling_windows already produced -
    shared by TD Opportunity's Emerging Heat (last3 only) and Role &
    Momentum's Role Trend (last3 and last5, blended), neither of which
    recomputes the underlying rolling means.

    MASKED to NaN whenever the player has <= `window` prior games this
    season (via games_played, a per-row count of real rows already
    seen in that (player_id, season) group — bye weeks add no row at
    all, so this counts games actually played, not calendar weeks
    elapsed, same as add_rolling_windows' shift/rolling already does).

    Confirmed directly (real 2025 Week 2 data, n=1 prior game): add_
    rolling_windows' rolling(window, min_periods=1) and .expanding().
    mean() both simply average over "however many prior games exist,
    capped at window" — with <= window prior games, last{window} and
    season_avg are computed over the EXACT SAME set of games and are
    mathematically guaranteed identical (delta=0.0), regardless of the
    player's real usage. That's indistinguishable from a genuinely flat
    multi-game trend by value alone (both read exactly 0.0) — only
    distinguishable by checking how much history actually existed,
    which is what games_played does here. Before this mask, every
    player with exactly one game of history read as "100% complete" on
    this input, identical to a player with ten games — confirmed via
    td_opportunity_completeness's real Week 2 2025 distribution, which
    was bimodal at exactly 30.0/100.0 with nothing in between.

    Once games_played > window, last{window} starts excluding older
    games season_avg still includes, so a nonzero (or a genuinely
    coincidental zero) delta becomes a real, unmasked observation again
    — deliberately NOT a delta-value check (unlike depth_chart_
    movement_pct's own "delta == 0" tie-mass fallback, the closest
    precedent in this module): a delta-value check can't tell a forced
    zero apart from a real one once enough history exists, and would
    incorrectly flatten a genuine stable multi-game trend that happens
    to land on exactly 0.0.
    """
    games_played = weekly.groupby(["player_id", "season"]).cumcount()
    delta = weekly[f"{col}_last{window}"] - weekly[f"{col}_season_avg"]
    return delta.where(games_played > window)


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
    proven_heat, emerging_heat, td_opportunity, td_opportunity_completeness,
    and the intermediate percentile components appended, for inspectability.

    Only ever uses shift(1)'d / cumulative-through-the-prior-game inputs -
    never a row's own current-game rz_touches/rz_tds/snap_share - so the
    score always reflects what was knowable heading into that game, never
    the game's own outcome.

    td_opportunity_completeness (0-100) is the fraction of this pillar's
    10 percentile-normalized inputs (4 recent-production + 3 conversion-
    rate + 3 trend) that were real values rather than neutral-50 fallback,
    via _percentile_fn's track_fallback — see score_evidence_quality,
    which consumes this.
    """
    weekly = weekly.sort_values(["player_id", "season", "week"]).copy()
    ph_cfg = config["proven_heat"]
    eh_cfg = config["emerging_heat"]
    combo_cfg = config["combination"]
    k = config["shrinkage_k"]

    fallback_flags = []
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
    weekly["td_opportunity_completeness"] = (
        (1 - pd.concat(fallback_flags, axis=1).mean(axis=1)) * 100
    ).round(1)

    return weekly


def score_role_momentum(weekly: pd.DataFrame, config: dict = CONFIG) -> pd.DataFrame:
    """
    Score every row for Role & Momentum (20% weight in the Universal TPE
    Score). Requires weekly to already have depth_rank
    (redzone.add_depth_chart_rank) and ahead_injury_statuses
    (redzone.add_injury_context) columns, in addition to add_rolling_windows'
    output. Returns weekly with role_trend, external_opportunity,
    role_momentum, role_momentum_completeness, and the intermediate
    percentile components appended.

    See module docstring for the depth-chart 2025-schema gap: depth_rank is
    NaN for every 2025 row, so depth_chart_movement_pct falls back to
    neutral there, not a real reading.

    DOCUMENTED SIMPLIFICATION: role_momentum_completeness (see
    score_evidence_quality) can't distinguish "no depth-chart data at all"
    from "depth-chart delta was deliberately routed to neutral because it
    was exactly 0" (see the tie-mass comment below) — both hit the same
    NaN-before-fill_neutral path _percentile_fn's track_fallback observes,
    even though a real 0 is informative (no rank change) and true
    missingness isn't. Treating them the same is a deliberate simplification,
    not an oversight — a two-tier distinction was considered and explicitly
    deferred rather than built.
    """
    weekly = weekly.sort_values(["player_id", "season", "week"]).copy()
    rt_cfg = config["role_trend"]
    combo_cfg = config["combination"]
    severity_map = config["injury_severity"]

    fallback_flags = []
    pct = _percentile_fn(weekly, config, track_fallback=fallback_flags)

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
    weekly["role_momentum_completeness"] = (
        (1 - pd.concat(fallback_flags, axis=1).mean(axis=1)) * 100
    ).round(1)

    return weekly


def _environment_score(weekly: pd.DataFrame, env_cfg: dict, track_fallback: list = None) -> pd.Series:
    """
    dome/closed roof -> 100 (fully controlled, no weather risk).
    outdoors/open -> a blend of wind_score and temp_score using the
    wind_ceiling_mph / temp_comfort_f constants in CONFIG["environment"] -
    rough starting points, not researched, same "hypothesis to calibrate
    later" treatment as every other weight in this module.

    Missing temp/wind among outdoor games (~17% of them, per the checked
    null rate) -> neutral 50, same missing-data philosophy as everywhere
    else - not the same as a dome (which is a real, known "no weather
    risk" fact, not an absence of data). track_fallback, if provided,
    records exactly that distinction: False (real) for every dome/closed
    row and every outdoor row with actual temp/wind data, True only for
    outdoor rows missing it — same completeness-tracking contract as
    _percentile_fn's track_fallback (see score_evidence_quality).
    """
    indoor = weekly["roof"].isin(["dome", "closed"])

    wind_score = 100.0 - (
        weekly["wind"].clip(lower=0, upper=env_cfg["wind_ceiling_mph"]) / env_cfg["wind_ceiling_mph"] * 100.0
    )
    temp_score = (weekly["temp"].clip(lower=0) / env_cfg["temp_comfort_f"] * 100.0).clip(upper=100.0)
    outdoor_raw = env_cfg["wind_weight"] * wind_score + env_cfg["temp_weight"] * temp_score

    if track_fallback is not None:
        track_fallback.append(~indoor & outdoor_raw.isna())

    outdoor_score = fill_neutral(outdoor_raw)
    return pd.Series(np.where(indoor, 100.0, outdoor_score), index=weekly.index)


def score_situation(weekly: pd.DataFrame, allowed_weekly: pd.DataFrame, config: dict = CONFIG) -> pd.DataFrame:
    """
    Score every row for Situation (20% weight in the Universal TPE Score).
    Requires weekly to already have the allowed_*-prefixed columns
    (redzone.add_defensive_matchup_context) and roof/temp/wind
    (redzone.add_environment_data). Also takes allowed_weekly (the
    un-fanned defense-allowed table, one row per (defteam, position_group,
    week) — aggregate_redzone_allowed's output before it gets joined onto
    the many-offensive-players-per-defense-week weekly table) — needed to
    compute league-average conversion rates correctly; summing from the
    already-joined weekly table would over-count every defense-week by
    however many offensive players faced them that week.

    Combines two independent contextual modifiers as a plain weighted
    average — deliberately NOT the max/bonus mechanic TD Opportunity and
    Role & Momentum use. That mechanic exists for two readings of the same
    underlying thing where one might be a stale echo of the other;
    defensive matchup and weather aren't that, so averaging them is the
    honest combination here, not a workaround.

    Also returns situation_completeness (0-100) — the fraction of this
    pillar's 8 inputs (7 percentile-normalized defensive-matchup inputs +
    1 environment reading) that were real rather than neutral-50 fallback
    — see score_evidence_quality, which consumes this alongside TD
    Opportunity's and Role & Momentum's own completeness columns.

    ALSO returns defensive_matchup_completeness (0-100) — the SAME
    fraction, but over just the 7 defensive-matchup inputs, excluding
    environment entirely. Added for nfl/defensive_trends.py: situation_
    completeness blends two contextual modifiers that have nothing to do
    with each other (a dome/wind reading has no bearing on whether a
    defense is genuinely vulnerable at a position), so it would be a
    misleading confidence signal for a story that's ONLY about defensive
    matchup. Purely additive — situation_completeness's own computation
    (and every existing caller) is unchanged; this exposes a distinction
    already being tracked internally (fallback_flags already separates
    cleanly into "the first 7 came from pct_allowed calls, the 8th from
    _environment_score" — confirmed directly by reading the call
    sequence below, not assumed) as its own column.

    Explicitly out of scope, per instruction: game script / team totals
    (Market Value pillar, once odds are wired in) and offensive line / QB-
    connection / coaching-tendency signals (no clean nfl_data_py source,
    flagged as a known gap rather than proxied).
    """
    weekly = weekly.sort_values(["player_id", "season", "week"]).copy()
    dm_cfg = config["defensive_matchup"]
    env_cfg = config["environment"]
    sit_cfg = config["situation"]
    k = config["shrinkage_k"]

    qualified_allowed = weekly["allowed_season_total_rz_touches_allowed"] >= config["min_touches_allowed_for_qualification"]

    fallback_flags = []
    dm_fallback_flags = []

    def pct_allowed(values: pd.Series) -> pd.Series:
        raw = percentile_lookup(values, build_reference_scale(values, qualified_allowed))
        fallback_flags.append(raw.isna())
        dm_fallback_flags.append(raw.isna())
        return fill_neutral(raw)

    # --- Defensive Matchup Vulnerability: mirrors Proven Heat's structure
    # exactly, measuring what the opponent defense ALLOWS to this player's
    # position group instead of what the player PRODUCES.
    w = dm_cfg["recent_tds_allowed"]
    recent_tds_allowed_pct = (
        w["last1"] * pct_allowed(weekly["allowed_rz_tds_last1"])
        + w["last3"] * pct_allowed(weekly["allowed_rz_tds_last3"])
        + w["last5"] * pct_allowed(weekly["allowed_rz_tds_last5"])
        + w["season_avg"] * pct_allowed(weekly["allowed_rz_tds_season_avg"])
    )

    league_avg_gl_rate_allowed = allowed_weekly["gl_tds"].sum() / allowed_weekly["gl_touches"].sum()
    league_avg_i10_rate_allowed = allowed_weekly["i10_tds"].sum() / allowed_weekly["i10_touches"].sum()
    league_avg_rz_rate_allowed = allowed_weekly["rz_tds"].sum() / allowed_weekly["rz_touches"].sum()

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

    # --- Environment ---
    environment_score = _environment_score(weekly, env_cfg, track_fallback=fallback_flags)

    # --- Combine: plain weighted average, see docstring for why. ---
    situation = (
        sit_cfg["defensive_matchup_weight"] * defensive_matchup_vulnerability
        + sit_cfg["environment_weight"] * environment_score
    )

    weekly["recent_tds_allowed_pct"] = recent_tds_allowed_pct.round(1)
    weekly["conversion_rate_allowed_pct"] = conversion_rate_allowed_pct.round(1)
    weekly["defensive_matchup_vulnerability"] = defensive_matchup_vulnerability.round(1)
    weekly["environment_score"] = environment_score.round(1)
    weekly["situation"] = situation.round(1)
    weekly["situation_completeness"] = (
        (1 - pd.concat(fallback_flags, axis=1).mean(axis=1)) * 100
    ).round(1)
    weekly["defensive_matchup_completeness"] = (
        (1 - pd.concat(dm_fallback_flags, axis=1).mean(axis=1)) * 100
    ).round(1)

    return weekly


def score_market_value(weekly: pd.DataFrame, config: dict = CONFIG) -> pd.DataFrame:
    """
    Score every row for Market Value (20% weight in the Universal TPE
    Score's core_weights — see score_universal_tpe). Requires weekly to
    already have season, week, and consensus_implied_probability columns
    — nfl/market_value.py's snapshot_scoring_inputs output, joined onto
    a season/week grain (that join is a future step, not built this
    round; see market_value.py's module docstring for why Market Value
    stays snapshot-only and isn't backfilled into the historical weekly
    table at all). Returns weekly with market_value_score and
    market_value_completeness appended. Deliberately NOT wired into
    score_universal_tpe yet — that's the next step, after this is
    reviewed.

    Single component: consensus_implied_probability (the market's own
    median-of-books read on how likely this player is to score anytime)
    percentile-ranked against that week's eligible RB/WR/TE pool. Reuses
    the same population-relative pattern _percentile_fn wraps elsewhere
    in this module — build_reference_scale -> percentile_lookup ->
    fill_neutral, the same three normalize.py primitives, not a
    reimplementation — but grouped by (season, week) rather than called
    through _percentile_fn itself, since that helper's qualified-
    population logic (season-total rz_touches) is a red-zone-usage
    concept with no equivalent here.

    The (season, week) grouping is a deliberate difference from every
    other pillar's pooled multi-season reference population, not an
    oversight: red-zone usage and opponent context are stable enough
    season to season that pooling 2022+2024+2025 to compare a Week 1
    read against a Week 17 one is reasonable (see module docstring's
    NORMALIZATION CAVEAT). A player's market price isn't — it's only
    meaningful relative to that SAME week's board (that week's slate,
    injury news, and game context all move together), so this week's own
    pool is the correct reference population, not a cross-season blend.

    best_price is intentionally NOT part of the score — left untouched
    as a pass-through display field only (e.g. a future "best odds:
    DraftKings +450 vs consensus +380" UI), never folded into
    market_value_score in any weighted or bonus form.
    consensus_implied_probability is already the market's best single
    read (median across books, see snapshot_scoring_inputs);  best_price
    is deliberately the single most generous outlier quote by
    construction and would just inject book-shopping noise into a score
    meant to measure market-implied probability, not shopping upside.

    market_value_completeness (0-100) is expected to read ~100 for
    essentially every row: the +300 eligibility floor upstream already
    requires a posted player_anytime_td market before a row exists here
    at all, unlike the other pillars' genuinely gap-prone inputs (missing
    injury/weather/depth-chart data). Still routed through the same
    neutral-50 fallback as every other pillar for any unexpected missing/
    NaN consensus_implied_probability — defensive consistency, not an
    assumption that completeness is 100 by construction.

    n_books=1 (the normal case this far from kickoff — see
    snapshot_scoring_inputs) degenerates correctly here same as it
    already does at the raw-input layer: consensus_implied_probability
    is just that one book's own value, and percentile_lookup handles a
    reference scale containing single-quote values with no special
    casing needed.
    """
    weekly = weekly.copy()

    def _group_percentile(s: pd.Series) -> pd.Series:
        return pd.Series(percentile_lookup(s, build_reference_scale(s)), index=s.index)

    raw_pct = weekly.groupby(["season", "week"])["consensus_implied_probability"].transform(_group_percentile)

    weekly["market_value_score"] = fill_neutral(raw_pct).round(1)
    weekly["market_value_completeness"] = ((1 - raw_pct.isna()) * 100).round(1)

    return weekly


def score_evidence_quality(weekly: pd.DataFrame, config: dict = CONFIG) -> pd.DataFrame:
    """
    Score every row for Evidence Quality & Convergence (10% weight in the
    Universal TPE Score). Unlike every other pillar, this needs no new
    data at all — it's a pure meta-layer over the outputs TD Opportunity,
    Role & Momentum, and Situation already produced. It MUST run after
    all three (score_evidence_quality reads their *_completeness columns
    and final scores directly; there's nothing to re-derive from raw
    play-by-play/roster/schedule data, and nothing here would be
    computable any earlier in the pipeline).

    Two independent axes, each real on their own but neither sufficient
    alone — combined as a genuine AND relationship, not the max/bonus or
    plain-weighted-average shapes used elsewhere in this module:

      Completeness  - what fraction of the three pillars' percentile-
                       normalized inputs were real values, not neutral-50
                       fallback. Mean of td_opportunity_completeness /
                       role_momentum_completeness / situation_completeness
                       (config["evidence_quality"]["completeness_columns"]).

      Convergence   - do the signal FAMILIES agree with each other,
                       direction-agnostic. Deliberately NOT "how many
                       score above a bullish threshold" — that conflates
                       agreement with directionality, which the other
                       four pillars (90% of the weight) already fully
                       own. A player where all four families unanimously
                       read bearish is in strong agreement and should
                       score high on convergence, same as a unanimous
                       bullish read; a player where TD Opportunity loves
                       him and Situation hates him is in poor agreement
                       regardless of which direction the overall pick
                       leans. Measured as 100 - range(family scores) —
                       bounded [0,100] by construction (family scores are
                       already 0-100, so the range between them can't
                       exceed 100), no extra scaling constant needed.
                       Operates over however many of
                       config["evidence_quality"]["family_score_columns"]
                       are actually present on a given row (2-4) — Market
                       Value doesn't have a comparable 0-100 score yet
                       (snapshot-only, no market_value_score column), and
                       isn't backfilled into historical rows at all, so
                       this will typically run over 3 families for now,
                       not 4. Building market_value_score is a real
                       follow-on, deliberately not folded into this task.

    evidence_quality = sqrt(completeness * convergence) — a geometric
    mean, not an average. Either axis near zero craters the combined
    score: high completeness with contradictory signals isn't
    trustworthy (plenty of real data that disagrees with itself is a
    genuine red flag), and high convergence built on mostly-fallback data
    isn't trustworthy either (agreement between one or two real signals
    is easy to get by chance). A weighted average would hide exactly that
    failure mode by letting one strong axis paper over a weak one.

    Rows with fewer than 2 family scores present get neutral 50 for
    convergence (a "range" of one value is meaningless, not informative)
    — same missing-data philosophy as every fallback elsewhere in this
    module.
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


def score_universal_tpe(weekly: pd.DataFrame, market_value: pd.DataFrame = None, config: dict = CONFIG) -> pd.DataFrame:
    """
    Score every row for the final Universal TPE Score. Must run after
    score_td_opportunity, score_role_momentum, score_situation, and
    score_evidence_quality (reads all four's outputs directly) — the
    last step in the pipeline, not a fifth independent pillar computation.

    market_value: optional, score_market_value()'s output (a player_id/
    season/week-keyed frame with market_value_score and
    market_value_completeness columns — score_market_value's own live-
    poll snapshot table, not weekly itself, since Market Value is
    computed on an entirely separate table with no rz_touches/defteam/
    etc. — see market_value.py's module docstring). Left-merged onto
    weekly here, on (player_id, season, week), the same join key every
    other pillar's data is already keyed by — every row of weekly is
    kept regardless of whether a live market existed for that player
    that week; market_value_score is simply NaN where it didn't, and the
    present-columns-only renormalization below (unchanged, and unchanged
    in behavior even before this parameter existed) already handles that
    per row, same as it's always handled market_value_score's total
    absence on every historical row. Omit this argument (the default) to
    score without attempting a merge at all — the right choice for
    historical backfills, where a live market can never have existed
    (see market_value.py) and merging would be pure overhead for a
    column that's already going to be 100% NaN. Any pre-existing
    market_value_score/market_value_completeness columns on weekly are
    dropped before merging, so calling this twice with market_value set
    doesn't produce _x/_y suffixed duplicates.

    DELIBERATE DEVIATION FROM THE BLUEPRINT'S LITERAL "10% ADDITIVE
    WEIGHT" FOR EVIDENCE QUALITY & CONVERGENCE — see module docstring for
    the full reasoning. Short version: Evidence Quality is structurally a
    meta-score (how much to trust the other four pillars), not an
    independent opinion on the opportunity like they are, so it acts as a
    confidence multiplier on their combined read instead of a fifth
    additive slice:

      core_score = weighted sum of td_opportunity/role_momentum/situation/
                   market_value_score, present-columns-only with weights
                   renormalized to sum to 100 over whichever are present
                   (market_value_score is absent for every historical
                   row — see market_value.py — so core_score renormalizes
                   over the remaining 70 there, not scored against a
                   70-point ceiling). This has always been a per-ROW
                   renormalization, not a population filter — every row
                   of weekly gets a core_score, with or without Market
                   Value; nothing here (before or after this parameter
                   existed) drops rows lacking a pillar.
      confidence_multiplier = confidence_floor + (1 - confidence_floor)
                               * (evidence_quality / 100)
      tpe_score = core_score * confidence_multiplier

    Returns weekly with core_score, confidence_multiplier, and tpe_score
    appended.
    """
    weekly = weekly.copy()
    if market_value is not None:
        weekly = weekly.drop(columns=["market_value_score", "market_value_completeness"], errors="ignore")
        weekly = weekly.merge(
            market_value[["player_id", "season", "week", "market_value_score", "market_value_completeness"]],
            on=["player_id", "season", "week"],
            how="left",
        )

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
