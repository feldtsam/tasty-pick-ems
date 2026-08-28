"""
NFL Picks Shelves — all seven, fixed blueprint order: Red Zone Trends,
RB Trends, WR Trends, TE Trends, ATTD +300-499, ATTD +500-699, ATTD +700+.

CORRECTION to a claim this docstring used to make: the three ATTD
odds-band shelves were NOT already built elsewhere — that line was
aspirational/stale, confirmed false by direct search (zero odds-band
shelf logic existed anywhere in nfl/ outside this sentence) before the
NFL Shelf Curation & Tasty Six work built them here, in this file,
alongside the home-shelf-assignment layer that consumes all seven
(see curate_home_shelves.py). Left as a cautionary note for whoever
reads this next: a docstring claim needs the same real-code
verification as any other, not just at commit time.

Built per the approved Trend Shelf Architecture review (this session):
Red Zone Trends answers "who is getting the opportunities" (ranked by
TD Opportunity); the three position shelves answer "whose ROLE is
changing in a way that could create opportunity" (ranked by Role &
Momentum, position-filtered). universal_tpe_score (the `tpe_score`
column — same thing, scoring.py's own function name vs. the stored
column name) is never the primary sort for any of these FOUR shelves;
it's a secondary display field and a tiebreaker only. The three
ATTD odds-band shelves (build_odds_band_shelf, added alongside the
home-shelf-assignment work) are the one place tpe_score IS the primary
ranking signal — banded by consensus_price_american instead of pillar-
gated, since there's no single themed pillar to rank a composite-driven
shelf by.

This module is pure logic over an already-scored `weekly` DataFrame
(run_pipeline's output) — no I/O, no new pillar math, nothing here
changes td_opportunity/role_momentum/evidence_quality/tpe_score. Same
"duplicate rather than cross-import" boundary the rest of nfl/ already
keeps from pipeline/ and backtest/.

EVIDENCE GATE, CONFIGURABLE (not hardcoded): each shelf's own pillar
completeness column (td_opportunity_completeness for Red Zone Trends,
role_momentum_completeness for the position shelves) gates eligibility
before ranking — a thin-evidence row never gets ranked highly by
chance in the first place, rather than being down-weighted after the
fact. The threshold is a CONFIG value, not a constant baked into the
comparison, specifically so it can be validated against real historical
weekly pools (see test_shelves.py and the validation report) before
being treated as a permanent production setting — see CONFIG below.

FILL-GUARANTEE FALLBACK: every shelf guarantees SHELF_SIZE cards when
the eligible pool allows it, mirroring pipeline/api/shelf_curation.py's
own "never leave a slot silently empty, but flag when a fallback was
needed" philosophy (see that module's _ranked()/compute_tasty_six()).
Concretely: rank the ODDS-ELIGIBLE pool (still gated by the +300 ATTD
floor, which never weakens) by the shelf's primary signal, fill from
the completeness-gated rows first, and only backfill remaining slots
from below-gate rows, in the same rank order, if the gated pool falls
short. Every card is tagged meets_evidence_threshold so a fallback card
is visible as one, never silently indistinguishable from a gated pick —
same "flagged, not silent" discipline as the MLB repeats list.

STORYTELLING: headlines are generated from which REAL sub-component of
the pillar dominates for that row (Proven Heat vs. Emerging Heat for
Red Zone Trends; Role Trend vs. External Opportunity for the position
shelves) — a small set of data-driven templates, not per-player
hardcoded strings, and not invented from unsupported signals. Evidence
numbers use TRAILING, INCLUSIVE window sums (last 3 games INCLUDING the
shelf's own reporting week) computed fresh here for display purposes
only — deliberately NOT the same as the scoring pillars' shift(1)'d
_last3 columns (rolling MEANS that exclude the current game, correct
for a pre-game score, wrong for a retrospective "here's what's been
happening" headline).
"""
import ast

import pandas as pd

from divisions import DIVISIONS, team_to_division
from redzone import add_rolling_windows, aggregate_whole_game_targets


def _as_list(value) -> list:
    """
    ahead_injury_statuses is a real Python list in memory (straight out of
    run_pipeline), but round-trips through CSV as its str() representation
    (e.g. "['Questionable']") — confirmed directly: validating this module
    against player_redzone_weekly.csv (pd.read_csv, the same way every
    other validation this session has loaded historical data) hits exactly
    this, not a hypothetical. Parsed back to a real list here rather than
    assuming callers only ever pass an in-memory frame straight from
    run_pipeline.
    """
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = ast.literal_eval(value)
            return parsed if isinstance(parsed, list) else []
        except (ValueError, SyntaxError):
            return []
    return []

CONFIG = {
    "shelf_size": 6,
    # American odds; a positive price of 300 or higher (+300 or longer).
    # Never weakens per-shelf — see module docstring.
    "attd_odds_floor": 300,
    # Candidate threshold, NOT yet a validated permanent setting — see
    # test_shelves.py / the validation report for real historical checks
    # against each shelf's actual candidate pool before trusting 50.
    "completeness_threshold": {
        "red_zone_trends": 50.0,
        "rb_trends": 50.0,
        "wr_trends": 50.0,
        "te_trends": 50.0,
    },
}

POSITION_SHELVES = {
    "rb_trends": "RB",
    "wr_trends": "WR",
    "te_trends": "TE",
}

# ---------------------------------------------------------------------------
# Expanded Card, Phase 2 — structured Role Signals evidence layer.
#
# section_title: DATA-DRIVEN per-shelf config lookup (not a branch inside a
# story function, per instruction) — "ROLE SIGNALS" everywhere except
# +700+, which gets the "why is a low-tpe_score player even on this list"
# framing the shelf itself is built around.
SECTION_TITLE_BY_SHELF = {
    "ATTD +700+": "WHY HE'S ON THE RADAR",
}
DEFAULT_SECTION_TITLE = "ROLE SIGNALS"

# Same bar CONFIG["completeness_threshold"]'s existing per-shelf values
# already use for real-vs-fallback gating (rule 1) — one shared constant,
# not a magic number repeated at every composite-signal call site below.
ROLE_SIGNAL_COMPLETENESS_THRESHOLD = 50.0


def _attd_eligible(weekly: pd.DataFrame, min_odds: int) -> pd.Series:
    """
    The +300 ATTD floor, applied identically to all four shelves — this
    is Picks, not Intelligence (see architecture review principle #5).
    A row with no market_value_score/consensus_price_american at all
    (every historical row before Market Value existed, or a stub row
    never polled) is NOT eligible — missing odds data is not the same
    as passing the floor, and treating it as passing would silently
    admit rows this system has no real market read on at all.
    """
    return weekly["consensus_price_american"].notna() & (weekly["consensus_price_american"] >= min_odds)


def _trailing_sum(weekly: pd.DataFrame, col: str, window: int) -> pd.Series:
    """
    Trailing INCLUSIVE sum of `col` over the last `window` games for each
    (player_id, season) group, ending with and including the row's own
    game — deliberately not shift(1)'d. This is a retrospective "here's
    what's already happened" number for a headline (e.g. "7 inside-the-10
    touches over his last three games"), not a pre-game score input; the
    scoring pillars' own _last3 columns (rolling means, shift(1)'d to
    exclude the current game) answer a different question and are the
    wrong source for this. Computed fresh here, display-only — never
    merged back into the scored weekly table or read by any pillar.
    """
    weekly = weekly.sort_values(["player_id", "season", "week"])
    g = weekly.groupby(["player_id", "season"])[col]
    return g.transform(lambda s: s.rolling(window, min_periods=1).sum())


def _add_whole_game_target_share_trend(weekly: pd.DataFrame, pbp: pd.DataFrame) -> pd.DataFrame:
    """
    WR Trends' one approved V1 extension beyond the existing pillars:
    whole-game (not red-zone-scoped) target-share movement. See
    redzone.aggregate_whole_game_targets for why this needs its own
    aggregation rather than reusing Role & Momentum's red-zone-scoped
    touch-share trend.

    Deliberately NOT folded into role_momentum (Option B from the
    architecture decision — see module docstring): merged onto `weekly`
    here, at the shelf layer, as a WR-Trends-only supporting field. Used
    as storytelling evidence and as a tiebreaker AFTER tpe_score, never
    as a ranking input ahead of role_momentum — role_momentum stays the
    single primary signal for WR Trends, same as the other two position
    shelves, so WR Trends isn't secretly ranked on a different mechanism
    than RB/TE Trends.

    Reuses add_rolling_windows (the exact same shift(1)'d rolling-window
    logic every pillar's trend input already uses) rather than
    reimplementing trend math a second time — only the raw target_share
    column and the choice to call it on a non-red-zone-scoped table are
    new.
    """
    targets = aggregate_whole_game_targets(pbp)
    targets = add_rolling_windows(targets, metrics=["target_share"], group_cols=["player_id", "season"])
    targets["target_share_trend"] = (targets["target_share_last3"] - targets["target_share_season_avg"]).round(3)

    keep = ["game_id", "player_id", "season", "week", "targets", "target_share", "target_share_trend"]
    return weekly.merge(targets[keep], on=["game_id", "player_id", "season", "week"], how="left")


def _rank_pool(pool: pd.DataFrame, primary_col: str, extra_tiebreak_cols: list = None) -> pd.DataFrame:
    """
    Sort best-first by the shelf's own primary signal, tpe_score as the
    FIRST tiebreaker (architecture review principle #3 / this task's
    section 3) — never the other way around. evidence_quality breaks any
    remaining tie. extra_tiebreak_cols (e.g. WR Trends' target_share_trend)
    apply only after both of those — a shelf-specific supporting signal
    breaking a tie is fine; letting it outrank the primary signal or
    tpe_score would not be.
    """
    cols = [primary_col, "tpe_score", "evidence_quality"] + (extra_tiebreak_cols or [])
    return pool.sort_values(cols, ascending=False)


def eligible_pool(weekly: pd.DataFrame, primary_col: str, odds_floor: int, position_filter: str = None) -> pd.DataFrame:
    """
    The FULL eligible pool for one shelf's own primary signal — ATTD-
    eligible, has a real primary_col value, restricted to RB/WR/TE
    (narrowed further to one position when position_filter is given) —
    BEFORE any completeness-threshold gating or shelf_size truncation.

    Extracted out of _build_shelf and made PUBLIC (no leading
    underscore), deliberately — added for cross-shelf HOME assignment
    (see curate_home_shelves.py), which needs the full untruncated pool
    across all seven shelves, not just each shelf's own already-capped
    top-6 display list, the same reason MLB's shelf_curation.py needed
    an analogous eligible/rank split for its own cross-shelf player
    dedup. This is real, validated business logic (position scoping,
    ATTD floor) that a companion orchestration module needs to stay in
    sync with, not a trivial one-liner worth duplicating across modules
    the way this codebase normally prefers (see shelves.py's own _as_list
    for that more common pattern) — an explicit, reasoned exception, not
    a casual reach into an implementation detail. _build_shelf's own
    behavior is unchanged — it now calls this instead of duplicating the
    same three-line filter inline; confirmed a strict no-op against the
    full real historical backfill (test_shelves.py, unchanged pass/fail).

    Position-group scoping matches _build_shelf's own real bug fix
    (2025 Week 2, Josh Allen/Bo Nix — see _build_shelf's docstring):
    always RB/WR/TE, never "no restriction at all" just because
    position_filter is None.
    """
    pool = weekly[_attd_eligible(weekly, odds_floor) & weekly[primary_col].notna()].copy()
    pool = pool[pool["position_group"].isin(["RB", "WR", "TE"])]
    if position_filter is not None:
        pool = pool[pool["position_group"] == position_filter]
    return pool


def _build_shelf(
    weekly: pd.DataFrame,
    primary_col: str,
    completeness_col: str,
    threshold: float,
    shelf_size: int,
    odds_floor: int,
    position_filter: str = None,
    extra_tiebreak_cols: list = None,
) -> dict:
    """
    Shared shelf-builder for all four TREND shelves — position_filter=
    "RB"/"WR"/"TE" restricts to one position; None means "every RB/WR/TE"
    (Red Zone Trends), NOT "no position restriction at all". A real bug
    caught directly in historical validation (2025 Week 2): Josh Allen
    and Bo Nix — both quarterbacks — appeared on Red Zone Trends,
    because td_opportunity itself doesn't carry any position restriction
    (a QB's red-zone scramble touches still percentile-rank), and the
    first version of this function had NO position_group check when
    position_filter was None. Every other part of this system scopes to
    RB/WR/TE only (see redzone._position_lookup's own docstring: "not
    meaningful for a receiving/rushing position-group aggregation") —
    Red Zone Trends is no exception, it's just not restricted to ONE of
    the three. Fixed to always require position_group in {RB, WR, TE},
    with position_filter narrowing further to one of them when given
    (now enforced in eligible_pool, above).
    Returns {"cards": [...], "gated_pool_size": int, "below_gate_pool_size":
    int, "fallback_count": int, "eligible_pool_size": int} — the counts
    validation needs to report honestly (section 4/addendum), not just
    the final card list.
    """
    pool = eligible_pool(weekly, primary_col, odds_floor, position_filter)
    eligible_pool_size = len(pool)
    gated = pool[pool[completeness_col] >= threshold]
    below_gate = pool[pool[completeness_col] < threshold]

    gated_ranked = _rank_pool(gated, primary_col, extra_tiebreak_cols)
    below_gate_ranked = _rank_pool(below_gate, primary_col, extra_tiebreak_cols)

    cards = []
    for _, row in gated_ranked.iterrows():
        cards.append({"row": row, "meets_evidence_threshold": True})
        if len(cards) >= shelf_size:
            break

    fallback_count = 0
    if len(cards) < shelf_size:
        for _, row in below_gate_ranked.iterrows():
            cards.append({"row": row, "meets_evidence_threshold": False})
            fallback_count += 1
            if len(cards) >= shelf_size:
                break

    return {
        "cards": cards,
        "eligible_pool_size": eligible_pool_size,
        "gated_pool_size": len(gated),
        "below_gate_pool_size": len(below_gate),
        "fallback_count": fallback_count,
    }


def add_red_zone_trend_windows(weekly: pd.DataFrame) -> pd.DataFrame:
    """
    Attaches i10_touches_trail3/gl_touches_trail3/rz_tds_trail3 (trailing
    INCLUSIVE 3-game sums, display-only — see _trailing_sum's own
    docstring for why these are deliberately NOT the scoring pillars'
    shift(1)'d _last3 columns) — the exact three columns red_zone_story
    reads. Extracted out of build_red_zone_trends (which now calls this
    instead of computing them inline) so a caller that needs red_zone_
    story() for a row OUTSIDE shelves.py's own top-N ranking — see
    curate_home_shelves.py's home-shelf reconnection — doesn't have to
    reimplement this prep step. Confirmed a strict no-op for
    build_red_zone_trends' own output (test_shelves.py, unchanged
    pass/fail).
    """
    weekly = weekly.copy()
    weekly["i10_touches_trail3"] = _trailing_sum(weekly, "i10_touches", 3)
    weekly["gl_touches_trail3"] = _trailing_sum(weekly, "gl_touches", 3)
    weekly["rz_tds_trail3"] = _trailing_sum(weekly, "rz_tds", 3)
    return weekly


def _round(x):
    """None-safe round to 2dp — every role_signal value/delta passes
    through this so a NaN never silently leaks into written JSON as
    "NaN" (invalid) instead of a real null."""
    return round(float(x), 2) if x is not None and pd.notna(x) else None


def _pct_col_is_real(row: pd.Series, col: str) -> bool:
    """
    Reuses fill_neutral's own exact fallback sentinel (50.0) as the
    real-vs-fallback signal for an already-percentile/composite-scored
    column — the SAME convention every completeness column in this
    pipeline already relies on (see scoring.py's own repeated use of
    this exact pattern). A coincidental genuine 50.0 reading is
    indistinguishable from a fallback by this check alone — the same
    documented simplification scoring.score_role_momentum already
    accepts for depth_chart_movement_pct's own completeness tracking
    (see that function's own docstring), not a new one invented here.
    """
    v = row.get(col)
    return pd.notna(v) and v != 50.0


def _trend_candidate(row: pd.Series, key: str, label: str, raw_col: str, window: int, gate_col: str, unit: str, scale: float = 1.0) -> dict:
    """
    TREND-style role signal candidate: value/delta both come straight
    off add_rolling_windows' own shift(1)'d `{raw_col}_last{window}` /
    `{raw_col}_season_avg` columns — the exact same inputs scoring.py's
    _trend_delta(weekly, raw_col, window) already reads, NOT
    reimplemented or recomputed here.

    Eligibility (and therefore whether `delta` is populated at all)
    reuses `gate_col` — an EXISTING precomputed percentile-trend column
    (e.g. touch_share_trend_pct) that already carries _trend_delta's
    own games_played masking from scoring.py. The masking boolean
    itself isn't separately persisted, but the resulting fallback-to-50
    sentinel on that column is a reliable proxy for it (same
    _pct_col_is_real convention used everywhere else in this file) —
    this reuses scoring.py's real masked-trend infrastructure by
    reading its output, rather than reimplementing _trend_delta's own
    games_played/cumcount logic a second time here (this module's own
    "duplicate rather than cross-import [pillar math]" boundary is
    about not recomputing scoring, not about refusing to read its
    already-computed columns).

    scale=100 converts a stored 0-1 share to a percentage-point display
    number — the SAME *100 every existing evidence string in this file
    already applies (see position_story's own snap-share prose) — not
    a new rescale invented for this task. scale=1 for a raw count
    (rz_touches).

    A TREND candidate REQUIRES a real (unmasked) delta to be eligible
    at all — it exists specifically to show a recent-vs-season trend;
    a level with no known trend isn't what this candidate is for (see
    _level_candidate for that case).
    """
    last = row.get(f"{raw_col}_last{window}")
    season_avg = row.get(f"{raw_col}_season_avg")
    real_delta = pd.notna(last) and pd.notna(season_avg) and _pct_col_is_real(row, gate_col)
    delta = (last - season_avg) * scale if real_delta else None
    value = last * scale if pd.notna(last) else None
    return {
        "key": key, "label": label, "value": _round(value), "delta": _round(delta), "unit": unit,
        "_eligible": real_delta, "_magnitude": abs(delta) if delta is not None else 0.0,
    }


def _level_candidate(row: pd.Series, key: str, label: str, raw_col: str, window: int, unit: str, scale: float = 1.0) -> dict:
    """
    LEVEL-style role signal candidate for a raw metric with NO existing
    percentile-trend gate column to reuse — confirmed directly: gl_
    touches/i10_touches feed no trend input anywhere in scoring.py (only
    rz_touches' own trend feeds Emerging Heat's touch_volume_trend_pct
    — see _trend_candidate for that one). Eligibility is a plain
    notna() check on the recent-window value itself, not a masked-delta
    one — there is no existing masked column for these two metrics to
    reuse, and inventing a fresh games_played mask here would be a
    second parallel trend system, which this task explicitly said not
    to build.

    delta is still computed/shown, UNMASKED, whenever a season_avg
    exists — an honest, if occasionally coincidental early-season
    number, the same accepted trade-off the +700+ shelf's own "small
    sample is expected and fine here" framing explicitly embraces for
    this exact candidate (gl_touches).
    """
    last = row.get(f"{raw_col}_last{window}")
    season_avg = row.get(f"{raw_col}_season_avg")
    value = last * scale if pd.notna(last) else None
    delta = (last - season_avg) * scale if (pd.notna(last) and pd.notna(season_avg)) else None
    return {
        "key": key, "label": label, "value": _round(value), "delta": _round(delta), "unit": unit,
        "_eligible": value is not None, "_magnitude": (value if value is not None else 0.0),
    }


def _composite_candidate(row: pd.Series, key: str, label: str, value_col: str, completeness_col: str = None) -> dict:
    """
    COMPOSITE-style role signal candidate: an already-percentile/
    composite-scored pillar column (0-100), used directly as `value` —
    delta is always None (this pipeline keeps no prior-week snapshot of
    these composite scores to diff against; inventing one is out of
    scope). Ranking magnitude is distance from the neutral-50 midpoint
    — the closest available analog to "how much does this stand out"
    when there's no separate delta concept, reusing the same neutral-50
    semantic already central to this pipeline's fallback convention
    rather than inventing a new one.

    Eligibility prefers a REAL completeness column when this row
    actually has one (the precise gate, same threshold CONFIG's
    per-shelf completeness_threshold values already use) — falls back
    to the neutral-50-sentinel heuristic on value_col itself only when
    completeness_col is missing or genuinely absent from this row.
    Confirmed this gap is real, not hypothetical: defensive_matchup_
    completeness was missing from a real local stub fixture that
    predated its own addition to scoring.py (caught and corrected
    during this task's own investigation) — the fallback exists for
    exactly that kind of stale/partial row, not a defensive guess.
    """
    value = row.get(value_col)
    completeness = row.get(completeness_col) if completeness_col else None
    if completeness is not None and pd.notna(completeness):
        eligible = completeness >= ROLE_SIGNAL_COMPLETENESS_THRESHOLD
    else:
        eligible = _pct_col_is_real(row, value_col)
    return {
        "key": key, "label": label, "value": _round(value), "delta": None, "unit": "score",
        "_eligible": eligible, "_magnitude": abs(value - 50.0) if pd.notna(value) else 0.0,
    }


def _select_role_signals(candidates: list) -> list:
    """
    Deterministic selection over a shelf's own confirmed candidate pool
    (see each *_role_signals function below — never signals outside the
    approved lists). Only ELIGIBLE candidates are considered at all;
    ranked by magnitude of movement (abs(delta) for TREND candidates,
    distance-from-50 for COMPOSITE ones, raw value for LEVEL ones — see
    each builder's own docstring), ties broken by the candidate's own
    position in the shelf's declared pool order below.

    THAT TIEBREAK IS A DOCUMENTED JUDGMENT CALL, flagged rather than
    silently assumed: the shelf's own player-ranking tiebreak
    (tpe_score, then evidence_quality — see _rank_pool) doesn't
    directly translate to "which METRIC to show for one already-chosen
    player" — pool-declaration order (itself ordered to match this
    task's own approved priority lists) is the closest honest analog,
    not a literal reuse of tpe_score/evidence_quality as a tiebreak
    here.

    Top 2 or 3 — a 3rd is included ONLY if a 3rd candidate actually
    cleared eligibility; NEVER padded with a fallback/weak reading to
    hit a round number (rule 3). A pool with 0 or 1 eligible candidates
    returns exactly that many, not a fabricated minimum — see this
    task's own report for whether that was observed against real data.
    """
    eligible = [(i, c) for i, c in enumerate(candidates) if c["_eligible"]]
    eligible.sort(key=lambda ic: (-ic[1]["_magnitude"], ic[0]))
    chosen = eligible[:3]
    return [{"label": c["label"], "value": c["value"], "delta": c["delta"], "unit": c["unit"]} for _, c in chosen]


def red_zone_trends_role_signals(row: pd.Series) -> list:
    """Red Zone Trends' confirmed pool, exactly: rz_touch_share
    (recent), rz_touches (recent window), gl_touches, i10_touches — no
    signals outside this list."""
    candidates = [
        _trend_candidate(row, "rz_touch_share", "Red-Zone Touch Share", "rz_touch_share", 3, "touch_share_trend_pct", unit="%", scale=100),
        _trend_candidate(row, "rz_touches", "Red-Zone Touches (Recent)", "rz_touches", 3, "touch_volume_trend_pct", unit="touches", scale=1),
        _level_candidate(row, "gl_touches", "Goal-Line Touches (Recent)", "gl_touches", 3, unit="touches"),
        _level_candidate(row, "i10_touches", "Inside-10 Touches (Recent)", "i10_touches", 3, unit="touches"),
    ]
    return _select_role_signals(candidates)


def rb_trends_role_signals(row: pd.Series) -> list:
    """RB Trends' confirmed pool, exactly: snap_share, rz_touch_share,
    gl_touches, depth_chart_movement_pct."""
    candidates = [
        _trend_candidate(row, "snap_share", "Snap Share", "snap_share", 3, "snap_share_trend_pct", unit="%", scale=100),
        _trend_candidate(row, "rz_touch_share", "Red-Zone Touch Share", "rz_touch_share", 3, "touch_share_trend_pct", unit="%", scale=100),
        _level_candidate(row, "gl_touches", "Goal-Line Touches (Recent)", "gl_touches", 3, unit="touches"),
        _composite_candidate(row, "depth_chart_movement_pct", "Depth-Chart Movement", "depth_chart_movement_pct"),
    ]
    return _select_role_signals(candidates)


def _target_share_level_candidate(row: pd.Series) -> dict:
    """LEVEL candidate: current whole-game target share (see
    _add_whole_game_target_share_trend — requires `pbp` threaded
    through build_wr_trends/build_te_trends; absent otherwise, and this
    candidate is then simply ineligible, not an error)."""
    value = row.get("target_share")
    scaled = value * 100 if pd.notna(value) else None
    return {
        "key": "target_share", "label": "Target Share", "value": _round(scaled), "delta": None, "unit": "%",
        "_eligible": scaled is not None, "_magnitude": (scaled if scaled is not None else 0.0),
    }


def _target_share_trend_candidate(row: pd.Series) -> dict:
    """LEVEL candidate whose own value IS a delta-shaped number
    (target_share_last3 - target_share_season_avg, already computed by
    _add_whole_game_target_share_trend, UNMASKED — that helper applies
    no games_played gate today, same accepted early-season trade-off as
    _level_candidate). Shown as `value` with `delta: None` rather than
    duplicated into both fields, to avoid a confusing "delta of a
    delta" reading."""
    value = row.get("target_share_trend")
    scaled = value * 100 if pd.notna(value) else None
    return {
        "key": "target_share_trend", "label": "Target Share Trend", "value": _round(scaled), "delta": None, "unit": "pp",
        "_eligible": scaled is not None, "_magnitude": abs(scaled) if scaled is not None else 0.0,
    }


def wr_te_trends_role_signals(row: pd.Series) -> list:
    """
    WR Trends' AND TE Trends' confirmed pool — the SAME pool and SAME
    2-vs-3 rule for both, per instruction: target_share, target_share_
    trend, with rz_touch_share filling an optional 3rd slot only if it
    clears the same eligibility bar as the first two (no special-casing
    needed — _select_role_signals' own top-2-or-3 logic already
    produces exactly this from a 3-candidate pool).

    snap_share is explicitly NOT in this pool for either shelf —
    confirmed misleading for TE receiving involvement specifically
    (blocking snaps inflate it), and kept out of WR's own pool too so
    both position shelves that share this pool are genuinely sharing
    it, not diverging silently.

    Both target_share candidates require `pbp` to have been threaded
    through at build time (build_wr_trends already did; build_te_trends
    is EXTENDED by this task to do the same — see that function's own
    docstring for why TE Trends had no access to this at all before).
    Omitted `pbp` degrades gracefully to whatever real signals ARE
    available (rz_touch_share alone, or zero) — never padded, per rule 3.
    """
    candidates = [
        _target_share_level_candidate(row),
        _target_share_trend_candidate(row),
        _trend_candidate(row, "rz_touch_share", "Red-Zone Touch Share", "rz_touch_share", 3, "touch_share_trend_pct", unit="%", scale=100),
    ]
    return _select_role_signals(candidates)


def _evidence_quality_convergence_candidate(row: pd.Series) -> dict:
    """
    COMPOSITE candidate for the task's own combined "evidence_quality/
    signal_convergence" pool entry — a DOCUMENTED JUDGMENT CALL, flagged
    rather than silently assumed: treated as ONE candidate (not two),
    eligible only when signal_convergence is real AND True (showing
    evidence_quality as a role signal only when cross-pillar convergence
    actually backs it up — a plain non-fallback evidence_quality reading
    alone, without convergence, was judged not to earn a spot in a
    "why this hits" list on its own).
    """
    value = row.get("evidence_quality")
    converged = bool(row.get("signal_convergence", False))
    completeness = row.get("evidence_completeness")
    completeness_ok = pd.notna(completeness) and completeness >= ROLE_SIGNAL_COMPLETENESS_THRESHOLD
    eligible = converged and pd.notna(value) and completeness_ok
    return {
        "key": "evidence_quality", "label": "Signal Convergence", "value": _round(value), "delta": None, "unit": "score",
        "_eligible": eligible, "_magnitude": abs(value - 50.0) if pd.notna(value) else 0.0,
    }


def attd_300_499_role_signals(row: pd.Series) -> list:
    """
    ATTD +300-499's confirmed pool: proven_heat (the td_opportunity
    SUB-SCORE, not the blended composite — reads the real proven_heat
    column directly, never td_opportunity itself), rz_touch_share
    (recent), evidence_quality/signal_convergence, with defensive_
    matchup_vulnerability as an optional 4th-priority candidate —
    included in the SAME pool passed to _select_role_signals rather
    than special-cased, so "only wins if it outranks one of the first
    three" falls out of the shared top-2-or-3 ranking for free.
    """
    candidates = [
        _composite_candidate(row, "proven_heat", "Proven Heat", "proven_heat", "td_opportunity_completeness"),
        _trend_candidate(row, "rz_touch_share", "Red-Zone Touch Share", "rz_touch_share", 3, "touch_share_trend_pct", unit="%", scale=100),
        _evidence_quality_convergence_candidate(row),
        _composite_candidate(row, "defensive_matchup_vulnerability", "Defensive Matchup", "defensive_matchup_vulnerability", "defensive_matchup_completeness"),
    ]
    return _select_role_signals(candidates)


def _market_value_snapshot_candidate(row: pd.Series) -> dict:
    """
    COMPOSITE candidate, delta ALWAYS None — market_value_score is
    snapshot-only, never backfilled with a trailing history to diff
    against (re-confirmed directly against market_value.py's own
    current docstring during this task, not assumed from memory: "the
    snapshot is never joined onto the historical weekly table... no
    future backfill run changes that"). Computing or displaying a
    market-value TREND would require data that structurally doesn't
    exist yet — explicitly out of scope, per instruction.
    """
    value = row.get("market_value_score")
    completeness = row.get("market_value_completeness")
    if pd.notna(completeness):
        eligible = completeness >= ROLE_SIGNAL_COMPLETENESS_THRESHOLD
    else:
        eligible = _pct_col_is_real(row, "market_value_score")
    return {
        "key": "market_value_score", "label": "Market Value (Snapshot)", "value": _round(value), "delta": None, "unit": "score",
        "_eligible": eligible, "_magnitude": abs(value - 50.0) if pd.notna(value) else 0.0,
    }


def attd_500_699_role_signals(row: pd.Series) -> list:
    """ATTD +500-699's confirmed pool: emerging_heat, role_trend,
    depth_chart_movement_pct, with market_value_score as an optional
    snapshot-only 4th (delta forced None — see _market_value_snapshot_
    candidate)."""
    candidates = [
        _composite_candidate(row, "emerging_heat", "Emerging Heat", "emerging_heat", "td_opportunity_completeness"),
        _composite_candidate(row, "role_trend", "Role Trend", "role_trend", "role_momentum_completeness"),
        _composite_candidate(row, "depth_chart_movement_pct", "Depth-Chart Movement", "depth_chart_movement_pct"),
        _market_value_snapshot_candidate(row),
    ]
    return _select_role_signals(candidates)


def _external_opportunity_candidate(row: pd.Series) -> dict:
    """
    LEVEL candidate reading external_opportunity directly (scoring.
    score_role_momentum's own severity-ladder score, already derived
    from ahead_injury_statuses — see that function's own docstring).
    Eligible only when > 0: external_opportunity's own design already
    treats an empty/healthy-ahead-of-him case as a real 0, not a
    fallback (score_role_momentum: "a real reading, no vacated
    opportunity") — but a real 0 still isn't a noteworthy "why he's on
    the radar" signal to surface here, so this candidate requires a
    genuinely nonzero severity, not just a non-missing one.
    """
    value = row.get("external_opportunity")
    return {
        "key": "external_opportunity", "label": "Backup Opportunity", "value": _round(value), "delta": None, "unit": "score",
        "_eligible": pd.notna(value) and value > 0, "_magnitude": (value if pd.notna(value) else 0.0),
    }


def attd_700_plus_role_signals(row: pd.Series) -> list:
    """
    ATTD +700+'s confirmed pool: gl_touches (recent — small-sample
    explicitly expected and fine here, per instruction, which is why
    this reuses _level_candidate's unmasked-delta LEVEL treatment
    rather than a TREND candidate that would require a real delta),
    depth_chart_movement_pct, ahead_injury_statuses/external_
    opportunity. Explicitly EXCLUDES tpe_score/evidence_quality — a low
    composite score is normal and expected at this price, and citing it
    would undercut the "why is he even on the radar" framing this
    shelf's own section_title (WHY HE'S ON THE RADAR) is built around.
    """
    candidates = [
        _level_candidate(row, "gl_touches", "Goal-Line Touches (Recent)", "gl_touches", 3, unit="touches"),
        _composite_candidate(row, "depth_chart_movement_pct", "Depth-Chart Movement", "depth_chart_movement_pct"),
        _external_opportunity_candidate(row),
    ]
    return _select_role_signals(candidates)


def add_td_opportunity_history_lookup(history_weekly: pd.DataFrame) -> dict:
    """
    Builds {(player_id, season): [(week, td_opportunity), ...]} (sorted
    ascending by week) from a real per-week history table shaped like
    nfl/scripts/player_redzone_weekly.csv (player_id/season/week/
    td_opportunity columns) — built ONCE per caller, not re-filtered
    fresh inside every row's own card-finalize step.

    Loading that CSV is explicitly NOT this function's job — this
    module stays pure logic over already-loaded DataFrames (see module
    docstring's own "no I/O" principle); the caller (curate_home_
    shelves.py / api/index.py) owns the actual pd.read_csv call and
    passes the result in. history_weekly=None (or empty) is a real,
    expected state — see td_opportunity_trend_for_row's own docstring
    for why (confirmed: player_redzone_weekly.csv has zero rows for a
    season before its first week is ever reconciled).
    """
    if history_weekly is None or len(history_weekly) == 0:
        return {}
    h = history_weekly.sort_values("week")
    return {key: list(zip(g["week"], g["td_opportunity"])) for key, g in h.groupby(["player_id", "season"])}


def td_opportunity_trend_for_row(row: pd.Series, history_lookup: dict) -> list:
    """
    PUBLIC (no leading underscore), same reasoned exception as
    red_zone_story/position_story/odds_band_story above:
    curate_home_shelves.py's own reconnected content step
    (_story_for_row) bypasses _finalize_cards entirely — see that
    function's own docstring for why — so it needs this (and
    section_title_for_shelf) directly too, not just the story
    functions.

    Real week-over-week td_opportunity series, most-recent (this card's
    own row) last. Raw 0-100 scale, unchanged — no rescale invented
    here, per instruction; a sparkline-specific rescale, if ever needed,
    is a frontend concern, flagged rather than solved here.

    history_lookup=None/{} (no matching key) degrades to "this week's
    own value only", a length-1 list — CONFIRMED this is the real,
    expected state for the actual current live week at the time this
    was built (2026 Week 1): player_redzone_weekly.csv only ever gains
    rows for a completed, reconciled week (see scripts/reconcile_
    week.py / api/index.py's reconcile-week endpoint), so a length-1
    trend this early in a season is not a bug to guard against.

    NO FIXED WINDOW CAP — checked real row counts before assuming one
    (2025 Week 10, real data): per-player prior-week counts range 1-9,
    naturally bounded by how many weeks of the season have actually
    been played. Never large enough this season to need artificial
    truncation; left uncapped rather than inventing an arbitrary limit
    not called for by real data.
    """
    key = (row.get("player_id"), row.get("season"))
    prior = [v for _, v in history_lookup.get(key, [])] if history_lookup else []
    return prior + [row.get("td_opportunity")]


def section_title_for_shelf(shelf_name: str) -> str:
    return SECTION_TITLE_BY_SHELF.get(shelf_name, DEFAULT_SECTION_TITLE)


def _confidence_band_for_row(row: pd.Series) -> str:
    """
    Lazy, function-scoped import — content_writer/ is NOT on sys.path
    by default when shelves.py is imported standalone (e.g.
    test_shelves.py, or any caller that never goes through curate_home_
    shelves.py's own sys.path setup); a top-level import here would
    break exactly those callers. Mirrors curate_home_shelves.py's own
    established pattern for reaching into content_writer/ from outside
    it (see write_content_draft_rows there).

    Reuses nfl_writer_common.nfl_regular_row_confidence_band_for_score
    UNCHANGED — the exact same function/thresholds curate_home_
    shelves.py already uses to compute every written row's own
    confidence_band today. NOT re-derived here: CONFIRMED directly
    (real stub CSV inspected during this task) that confidence_band is
    NOT a column on `weekly` — it only ever existed downstream,
    computed at write-shaping time from tpe_score. Computing it here
    means every card this module returns already carries a real
    confidence_band matching what curate_home_shelves.py would compute
    for that same row.
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent / "content_writer"))
    from nfl_writer_common import nfl_regular_row_confidence_band_for_score
    return nfl_regular_row_confidence_band_for_score(row.get("tpe_score"))


def red_zone_story(row: pd.Series) -> dict:
    """
    Proven Heat vs. Emerging Heat, whichever dominates this row's
    td_opportunity — see scoring.score_td_opportunity for both
    sub-components' own definitions, reused here unmodified.

    PUBLIC (no leading underscore), same reasoned exception as
    eligible_pool/odds_band_eligible above: curate_home_shelves.py's
    reconnected content step calls this directly on a home-assigned
    player's own row, not on rows drawn from this module's own top-N
    ranking — see that module's docstring for why a lookup keyed against
    build_all_shelves()'s own (already-capped) card list has real gaps
    for that purpose. Requires add_red_zone_trend_windows() already
    applied to `weekly` before this row was pulled from it.

    NOT a blind proven_heat >= emerging_heat comparison, on purpose —
    caught directly in real-historical validation (2025 Week 10, 3 of 6
    Red Zone Trends cards): proven_heat can legitimately win even with
    ZERO real recent production, because its conversion-rate component
    deliberately shrinks to the league-average rate when a player has no
    real touches yet (see scoring._shrink_rate's own docstring — correct,
    intentional pillar behavior, not a bug). A storytelling layer that
    just asks "which sub-score is numerically higher" doesn't know that,
    and it produced a real headline ("The goal-line work is becoming
    his.") backed by "0 goal-line touches, 0 inside-the-10 touches, 0
    red-zone touchdowns" — a genuine bug in this function, not in the
    pillar. Fixed by checking for real trailing production BEFORE
    reaching for the Proven Heat framing; the Emerging Heat framing is
    always safe to use as-is, since touch_share_trend_pct/snap_share_
    trend_pct are already percentile-normalized trend reads, not raw
    counts that can look artificially strong at zero.

    Phase 2: role_signals now comes from red_zone_trends_role_signals
    (the confirmed pool), and why_this_hits is the same content the
    "evidence" field used to hold — renamed only, per instruction.
    """
    i10_trail = row.get("i10_touches_trail3")
    gl_trail = row.get("gl_touches_trail3")
    rz_tds_trail = row.get("rz_tds_trail3")
    has_real_production = sum(x for x in (i10_trail, gl_trail, rz_tds_trail) if pd.notna(x)) > 0

    if row["proven_heat"] >= row["emerging_heat"] and has_real_production:
        headline = "The goal-line work is becoming his."
        why_this_hits = (
            f"{int(gl_trail)} goal-line touches, {int(i10_trail)} inside-the-10 touches, and "
            f"{int(rz_tds_trail)} red-zone touchdowns over his last three games"
        )
    else:
        headline = "The opportunity is climbing before the touchdowns have arrived."
        why_this_hits = (
            f"Red-zone touch share trending {row['touch_share_trend_pct']:.0f}th percentile, "
            f"snap share trending {row['snap_share_trend_pct']:.0f}th percentile"
        )
    return {"headline": headline, "why_this_hits": why_this_hits, "role_signals": red_zone_trends_role_signals(row)}


def position_story(row: pd.Series, position: str) -> dict:
    """
    PUBLIC (no leading underscore) — same reasoned exception as
    red_zone_story above; called directly by curate_home_shelves.py on a
    home-assigned player's own row. No extra column prep needed beyond
    what score_role_momentum already puts on `weekly` (unlike
    red_zone_story, which needs add_red_zone_trend_windows() first).

    Role Trend vs. External Opportunity, whichever dominates this row's
    role_momentum — see scoring.score_role_momentum. External
    Opportunity's evidence is always safe as-is: it comes directly from
    ahead_injury_statuses (already-built, already position-scoped), and
    external_opportunity can only numerically dominate role_trend when a
    real injury designation set its severity above 0 in the first place
    — there's no zero-evidence case to guard against on this branch the
    way there is on the Role Trend one below.

    Role Trend's OWN raw evidence is checked before use, same fix and
    same reason as red_zone_story: role_trend can legitimately win on
    the strength of depth-chart movement or the OTHER trend window even
    while this week's own snap share is flat or falling — caught
    directly in validation (RB Trends, 2025 Week 10: "role may already
    be changing hands" cited as evidence a snap share that went 32% →
    30%, a real decline, not a takeover). touch_share_trend_pct_role /
    snap_share_trend_pct_role (already percentile-normalized trend reads,
    same safety property as Red Zone Trends' Emerging Heat framing) are
    the honest fallback when the raw snap-share number itself isn't
    actually up.

    Phase 2: role_signals is rb_trends_role_signals for RB, wr_te_
    trends_role_signals for WR/TE — the two confirmed, genuinely
    different pools this task specified. why_this_hits is the same
    content the "evidence" field used to hold — renamed only.
    """
    if row["role_trend"] >= row["external_opportunity"]:
        headline = f"This {position} role may already be changing hands."
        snap_last1 = row.get("snap_share_last1")
        snap_season = row.get("snap_share_season_avg")
        snap_really_up = pd.notna(snap_last1) and pd.notna(snap_season) and snap_last1 > snap_season
        if snap_really_up:
            why_this_hits = f"Snap share {snap_season*100:.0f}% (season) → {snap_last1*100:.0f}% (most recent)"
        else:
            why_this_hits = (
                f"Touch-share trend {row['touch_share_trend_pct_role']:.0f}th percentile, "
                f"snap-share trend {row['snap_share_trend_pct_role']:.0f}th percentile, "
                f"depth-chart movement {row['depth_chart_movement_pct']:.0f}th percentile"
            )
    else:
        headline = f"An opportunity may be opening up ahead of him on the {position} depth chart."
        statuses = _as_list(row.get("ahead_injury_statuses"))
        why_this_hits = (
            f"Teammate(s) ranked ahead of him carry an injury designation: {', '.join(statuses)}"
            if statuses else "Ranked ahead of a teammate whose availability is now in question"
        )
    role_signals = rb_trends_role_signals(row) if position == "RB" else wr_te_trends_role_signals(row)
    return {"headline": headline, "why_this_hits": why_this_hits, "role_signals": role_signals}


def _finalize_cards(shelf_result: dict, story_fn, shelf_name: str, history_lookup: dict = None) -> list:
    cards = []
    for rank, entry in enumerate(shelf_result["cards"], start=1):
        row = entry["row"]
        story = story_fn(row)
        cards.append({
            "rank": rank,
            "player_id": row["player_id"],
            "player_name": row["player_name"],
            "posteam": row["posteam"],
            "position_group": row.get("position_group"),
            "primary_signal_value": row["_primary_value"],
            "tpe_score": row.get("tpe_score"),
            "evidence_quality": row.get("evidence_quality"),
            # Strength-gated read of evidence_convergence (see
            # scoring.score_evidence_quality's own docstring) -- whether
            # multiple pillars are BOTH tightly agreed AND meaningfully
            # strong, not just close together. Deliberately a distinct
            # name from evidence_convergence (that internal column stays
            # ungated, feeding evidence_quality's own math unchanged) so
            # the two are never confused downstream.
            "signal_convergence": bool(row.get("signal_convergence", False)),
            # Whether defensive weakness is the DOMINANT reason this pick
            # qualifies (score_signal_breach) -- a materially stricter,
            # differently-scoped claim than signal_convergence's own
            # cross-pillar agreement read. Independent column, independent
            # math; see scoring.score_signal_breach's own docstring.
            "signal_breach": bool(row.get("signal_breach", False)),
            "completeness": row["_completeness_value"],
            "meets_evidence_threshold": entry["meets_evidence_threshold"],
            "consensus_price_american": row.get("consensus_price_american"),
            "headline": story["headline"],
            "why_this_hits": story["why_this_hits"],
            "confidence_band": _confidence_band_for_row(row),
            "td_opportunity_trend": td_opportunity_trend_for_row(row, history_lookup),
            "role_signals": story["role_signals"],
            "section_title": section_title_for_shelf(shelf_name),
        })
    return cards


def build_red_zone_trends(weekly: pd.DataFrame, config: dict = CONFIG, history_weekly: pd.DataFrame = None) -> dict:
    """
    Ranked by td_opportunity — see scoring.score_td_opportunity, reused
    unmodified. No position filter (Red Zone Trends spans all eligible
    RB/WR/TE).

    `history_weekly`: real per-week history (nfl/scripts/player_
    redzone_weekly.csv shape) for td_opportunity_trend — see
    add_td_opportunity_history_lookup. Omit it and every card's
    td_opportunity_trend degrades to a length-1 list (this week only).
    """
    threshold = config["completeness_threshold"]["red_zone_trends"]
    weekly = add_red_zone_trend_windows(weekly)
    weekly["_primary_value"] = weekly["td_opportunity"]
    weekly["_completeness_value"] = weekly["td_opportunity_completeness"]

    result = _build_shelf(
        weekly, primary_col="td_opportunity", completeness_col="td_opportunity_completeness",
        threshold=threshold, shelf_size=config["shelf_size"], odds_floor=config["attd_odds_floor"],
    )
    history_lookup = add_td_opportunity_history_lookup(history_weekly)
    result["cards"] = _finalize_cards(result, red_zone_story, "Red Zone Trends", history_lookup)
    return result


def _build_position_trends(weekly: pd.DataFrame, position: str, shelf_key: str, shelf_name: str, config: dict, history_weekly: pd.DataFrame = None) -> dict:
    threshold = config["completeness_threshold"][shelf_key]
    weekly = weekly.copy()
    weekly["_primary_value"] = weekly["role_momentum"]
    weekly["_completeness_value"] = weekly["role_momentum_completeness"]

    result = _build_shelf(
        weekly, primary_col="role_momentum", completeness_col="role_momentum_completeness",
        threshold=threshold, shelf_size=config["shelf_size"], odds_floor=config["attd_odds_floor"],
        position_filter=position,
    )
    history_lookup = add_td_opportunity_history_lookup(history_weekly)
    result["cards"] = _finalize_cards(result, lambda row: position_story(row, position), shelf_name, history_lookup)
    return result


def build_rb_trends(weekly: pd.DataFrame, config: dict = CONFIG, history_weekly: pd.DataFrame = None) -> dict:
    return _build_position_trends(weekly, "RB", "rb_trends", "RB Trends", config, history_weekly)


def build_te_trends(weekly: pd.DataFrame, pbp: pd.DataFrame = None, config: dict = CONFIG, history_weekly: pd.DataFrame = None) -> dict:
    """
    Same role_momentum-primary ranking as RB/WR Trends, unaffected by
    `pbp`. If `pbp` IS provided, also attaches whole-game target-share
    movement — EXTENDED by this task: TE Trends previously had no
    access to target_share/target_share_trend at all (confirmed
    directly — only build_wr_trends called _add_whole_game_target_
    share_trend before now), but TE Trends' own confirmed role_signals
    pool (wr_te_trends_role_signals) is the SAME pool WR Trends uses,
    so it needs the same input. Omit `pbp` and TE Trends still ranks
    identically (role_momentum-only, unchanged); only the target_share/
    target_share_trend role_signal candidates are unavailable, never
    padded with something weaker to compensate (rule 3).
    """
    threshold = config["completeness_threshold"]["te_trends"]
    weekly = weekly.copy()
    if pbp is not None:
        weekly = _add_whole_game_target_share_trend(weekly, pbp)
    weekly["_primary_value"] = weekly["role_momentum"]
    weekly["_completeness_value"] = weekly["role_momentum_completeness"]

    result = _build_shelf(
        weekly, primary_col="role_momentum", completeness_col="role_momentum_completeness",
        threshold=threshold, shelf_size=config["shelf_size"], odds_floor=config["attd_odds_floor"],
        position_filter="TE",
    )
    history_lookup = add_td_opportunity_history_lookup(history_weekly)
    result["cards"] = _finalize_cards(result, lambda row: position_story(row, "TE"), "TE Trends", history_lookup)
    return result


def build_wr_trends(weekly: pd.DataFrame, pbp: pd.DataFrame = None, config: dict = CONFIG, history_weekly: pd.DataFrame = None) -> dict:
    """
    Same role_momentum-primary ranking as RB/TE Trends. If `pbp` is
    provided, also attaches whole-game target-share movement (this
    shelf's one approved V1 extension) as supporting evidence and a
    tertiary tiebreaker — never as a ranking input ahead of
    role_momentum. Omit `pbp` to build the shelf without it (role_
    momentum-only ranking still works identically; only the extra
    evidence field, tiebreak, and target_share/target_share_trend
    role_signal candidates are unavailable).
    """
    threshold = config["completeness_threshold"]["wr_trends"]
    weekly = weekly.copy()
    if pbp is not None:
        weekly = _add_whole_game_target_share_trend(weekly, pbp)
    weekly["_primary_value"] = weekly["role_momentum"]
    weekly["_completeness_value"] = weekly["role_momentum_completeness"]

    extra_tiebreak = ["target_share_trend"] if "target_share_trend" in weekly.columns else None
    result = _build_shelf(
        weekly, primary_col="role_momentum", completeness_col="role_momentum_completeness",
        threshold=threshold, shelf_size=config["shelf_size"], odds_floor=config["attd_odds_floor"],
        position_filter="WR", extra_tiebreak_cols=extra_tiebreak,
    )

    def _wr_story(row):
        story = position_story(row, "WR")
        if pd.notna(row.get("target_share_trend")):
            story["why_this_hits"] += f"; whole-game target share trend {row['target_share_trend']*100:+.1f}pp"
        return story

    history_lookup = add_td_opportunity_history_lookup(history_weekly)
    result["cards"] = _finalize_cards(result, _wr_story, "WR Trends", history_lookup)
    return result


ODDS_BANDS = [
    ("ATTD +300-499", 300, 499),
    ("ATTD +500-699", 500, 699),
    ("ATTD +700+", 700, None),
]


def odds_band_eligible(weekly: pd.DataFrame, lo: int, hi) -> pd.DataFrame:
    """
    The full eligible pool for one ATTD odds band — RB/WR/TE only
    (matching every other shelf's own scope), a real tpe_score, and
    consensus_price_american actually falling in [lo, hi] (hi=None for
    the open-ended +700+ band, same "no upper bound" convention ODDS_
    TIERS uses on the MLB side). BANDED, not floored — deliberately
    different from eligible_pool's own attd_odds_floor (>=300, no
    upper bound) used by the four trend shelves: a player can be
    ATTD-eligible for a trend shelf while sitting in exactly one of
    these three price bands, never more than one.
    """
    odds = weekly["consensus_price_american"]
    in_band = odds.notna() & (odds >= lo) & (hi is None or odds <= hi)
    pool = weekly[in_band & weekly["position_group"].isin(["RB", "WR", "TE"]) & weekly["tpe_score"].notna()].copy()
    return pool


ODDS_BAND_ROLE_SIGNALS = {
    "ATTD +300-499": attd_300_499_role_signals,
    "ATTD +500-699": attd_500_699_role_signals,
    "ATTD +700+": attd_700_plus_role_signals,
}


def odds_band_story(row: pd.Series, band_label: str = None) -> dict:
    """
    PUBLIC (no leading underscore) — same reasoned exception as
    red_zone_story/position_story above.

    Odds-band shelves are ranked by the final composite (tpe_score), not
    one themed pillar the way the four trend shelves are — there's no
    "proven vs. emerging"-style split to draw a specific narrative from
    here. Cites the real composite score and evidence quality directly
    rather than inventing a themed storyline these shelves don't have.

    Phase 2: role_signals genuinely differs per band (ODDS_BAND_ROLE_
    SIGNALS), branching on `band_label` inside ONE function rather than
    three separate top-level ones — this task's own "your call" on
    cleanest implementation: headline/why_this_hits are identical
    across all three bands (still no themed split to draw from), so
    three top-level functions would triple that shared code for zero
    real behavioral difference there; only the role_signals pool needs
    to vary, and that's a one-line dict lookup. band_label=None (only
    reachable if a caller bypasses build_odds_band_shelf) falls back to
    the +300-499 pool rather than raising — an honest "closest
    default", matching this module's general missing-input philosophy,
    not a crash.
    """
    return {
        "headline": "The model's combined read has him near the top of this price range.",
        "why_this_hits": f"Universal TPE Score {row['tpe_score']:.0f}/100, evidence quality {row['evidence_quality']:.0f}/100",
        "role_signals": ODDS_BAND_ROLE_SIGNALS.get(band_label, attd_300_499_role_signals)(row),
    }


def _build_odds_band_shelf(weekly: pd.DataFrame, lo: int, hi, shelf_size: int) -> dict:
    """
    Shared builder for the three ATTD odds-band shelves (5-7) — ranked
    straight by tpe_score (the final composite IS the primary signal
    here, unlike the four trend shelves where it's a tiebreaker only —
    see module docstring's own principle #3). No completeness-threshold
    gate / fallback split the way the trend shelves have: there's no
    single pillar's completeness column that's the natural gate for a
    composite-ranked shelf, so every real ATTD-eligible RB/WR/TE
    actually in this odds band is eligible outright, same shape as
    _build_shelf's own return contract (cards/eligible_pool_size/etc.)
    so downstream code (home-shelf assignment, card finalization)
    doesn't need a special case for these three shelves.
    """
    pool = odds_band_eligible(weekly, lo, hi)
    eligible_pool_size = len(pool)
    pool["_primary_value"] = pool["tpe_score"]
    pool["_completeness_value"] = pool["evidence_quality"]

    ranked = pool.sort_values(["tpe_score", "evidence_quality"], ascending=False)
    cards = []
    for _, row in ranked.iterrows():
        cards.append({"row": row, "meets_evidence_threshold": True})
        if len(cards) >= shelf_size:
            break

    return {
        "cards": cards, "eligible_pool_size": eligible_pool_size,
        "gated_pool_size": eligible_pool_size, "below_gate_pool_size": 0, "fallback_count": 0,
    }


def build_odds_band_shelf(weekly: pd.DataFrame, lo: int, hi, config: dict = CONFIG, label: str = None, history_weekly: pd.DataFrame = None) -> dict:
    result = _build_odds_band_shelf(weekly, lo, hi, config["shelf_size"])
    history_lookup = add_td_opportunity_history_lookup(history_weekly)
    shelf_name = label or "ATTD +300-499"
    result["cards"] = _finalize_cards(result, lambda row: odds_band_story(row, label), shelf_name, history_lookup)
    return result


def around_the_league_story(row: pd.Series, division: str) -> dict:
    """
    PUBLIC, same reasoned exception as red_zone_story/position_story/
    odds_band_story above. Around the League has no themed pillar either
    (same shape as the odds-band shelves, just grouped by division
    instead of price) -- cites the real composite score directly, same
    as odds_band_story, with division-appropriate copy rather than that
    function's "this price range" framing (which would be a wrong claim
    to reuse verbatim here — nothing about this shelf is odds-banded).

    Phase 2: role_signals reuses the +300-499 pool (attd_300_499_role_
    signals) — Around the League's own eligible pool is built from
    odds_band_eligible(weekly, 300, None), i.e. the exact same
    tpe_score-primary, ATTD-300+ population the three odds-band shelves
    already draw from (see build_around_the_league's own docstring: not
    a new ranking system), so its role_signals should read from the
    same kind of candidate pool, not one of the position-shelf pools
    that assume a themed pillar split this shelf doesn't have. Around
    the League isn't in SECTION_TITLE_BY_SHELF, so it gets the default
    "ROLE SIGNALS" title — consistent with the approved spec's own
    framing (a re-slice of the same eligible pool, not a +700+-style
    "why is he even here" shelf).
    """
    return {
        "headline": f"One of the model's top-graded picks in the {division}.",
        "why_this_hits": f"Universal TPE Score {row['tpe_score']:.0f}/100, evidence quality {row['evidence_quality']:.0f}/100",
        "role_signals": attd_300_499_role_signals(row),
    }


def _build_around_the_league_division(weekly: pd.DataFrame, division: str, shelf_size: int) -> dict:
    """
    Shared per-division builder for Around the League — same no-
    completeness-gate shape as _build_odds_band_shelf (see that
    function's own docstring: no single pillar's completeness is the
    natural gate for a tpe_score-primary shelf), reusing odds_band_
    eligible(weekly, 300, None) UNMODIFIED for the pool itself: lo=300,
    hi=None collapses that function's own band filter to exactly the
    full ATTD-eligible RB/WR/TE-with-a-real-tpe_score pool — no new
    eligibility logic, no separate pool built for this shelf.

    Grouped by division via team_to_division(posteam) — Around the
    League slices by the player's OWN team, not opponent, matching "top
    picks around the league by division" rather than a matchup concept.

    NO fill/backfill (a thin division returns fewer than shelf_size
    cards, never padded, never borrowed from an adjacent division, floor
    never weakens) and NO per-game/per-team clustering cap — confirmed
    directly that no such cap exists anywhere in this NFL pipeline today
    (DEFAULT_MAX_PER_GAME is an MLB-only constant, pipeline/api/
    shelf_curation.py — nothing analogous exists here to collide with),
    and even if it did, it would be the wrong tool here: this shelf
    groups BY team ownership within a division on purpose, the opposite
    of de-clustering.
    """
    pool = odds_band_eligible(weekly, 300, None)
    pool = pool[pool["posteam"].apply(team_to_division) == division].copy()
    eligible_pool_size = len(pool)
    pool["_primary_value"] = pool["tpe_score"]
    pool["_completeness_value"] = pool["evidence_quality"]

    ranked = pool.sort_values(["tpe_score", "evidence_quality"], ascending=False)
    cards = []
    for _, row in ranked.iterrows():
        cards.append({"row": row, "meets_evidence_threshold": True})
        if len(cards) >= shelf_size:
            break

    return {
        "cards": cards, "eligible_pool_size": eligible_pool_size,
        "gated_pool_size": eligible_pool_size, "below_gate_pool_size": 0, "fallback_count": 0,
    }


def build_around_the_league(weekly: pd.DataFrame, config: dict = CONFIG, history_weekly: pd.DataFrame = None) -> dict:
    """
    Around the League — the 8th content family. NOT a new prediction
    model or ranking system: reuses the exact same eligible pool and
    exact same universal_tpe_score ranking the three ATTD odds-band
    shelves already use (see _build_around_the_league_division), re-
    sliced by NFL division (divisions.py) instead of by odds band.

    Top 6 per division, no minimum enforced. Returns ALL EIGHT divisions
    always, even one with an empty list — {"AFC East": [...], ...} — so
    a caller can render an explicit empty state per division rather than
    a missing key, per the approved spec.

    Return shape is dict[str, list] of already-finalized cards (the same
    shape _finalize_cards produces for every other shelf: rank/player_id/
    player_name/posteam/tpe_score/evidence_quality/signal_convergence/
    signal_breach/headline/why_this_hits/confidence_band/td_opportunity_
    trend/role_signals/section_title/...), NOT the {"cards": [...], ...}
    wrapper build_all_shelves' own seven shelves use — a deliberate
    difference from every other builder in this file, because the
    approved spec asked for the bare list shape here specifically.
    """
    out = {}
    history_lookup = add_td_opportunity_history_lookup(history_weekly)
    for division in DIVISIONS:
        result = _build_around_the_league_division(weekly, division, config["shelf_size"])
        out[division] = _finalize_cards(
            result, lambda row, d=division: around_the_league_story(row, d), division, history_lookup,
        )
    return out


def build_all_shelves(weekly: pd.DataFrame, pbp: pd.DataFrame = None, config: dict = CONFIG, history_weekly: pd.DataFrame = None) -> dict:
    """
    All seven shelves at once, fixed blueprint order: Red Zone Trends,
    RB Trends, WR Trends, TE Trends, then the three ATTD odds-band
    shelves. pbp is optional (only needed for WR/TE Trends' target-share
    extension — TE Trends now also uses it, see build_te_trends); omit
    it to build all seven shelves from `weekly` alone. history_weekly is
    optional (only needed for td_opportunity_trend — see
    add_td_opportunity_history_lookup); omit it and every card's
    td_opportunity_trend degrades to a length-1 list (this week only).
    """
    shelves = {
        "Red Zone Trends": build_red_zone_trends(weekly, config, history_weekly),
        "RB Trends": build_rb_trends(weekly, config, history_weekly),
        "WR Trends": build_wr_trends(weekly, pbp, config, history_weekly),
        "TE Trends": build_te_trends(weekly, pbp, config, history_weekly),
    }
    for label, lo, hi in ODDS_BANDS:
        shelves[label] = build_odds_band_shelf(weekly, lo, hi, config, label, history_weekly)
    return shelves
