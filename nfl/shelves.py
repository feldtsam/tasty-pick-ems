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
    """
    i10_trail = row.get("i10_touches_trail3")
    gl_trail = row.get("gl_touches_trail3")
    rz_tds_trail = row.get("rz_tds_trail3")
    has_real_production = sum(x for x in (i10_trail, gl_trail, rz_tds_trail) if pd.notna(x)) > 0

    if row["proven_heat"] >= row["emerging_heat"] and has_real_production:
        headline = "The goal-line work is becoming his."
        evidence = (
            f"{int(gl_trail)} goal-line touches, {int(i10_trail)} inside-the-10 touches, and "
            f"{int(rz_tds_trail)} red-zone touchdowns over his last three games"
        )
    else:
        headline = "The opportunity is climbing before the touchdowns have arrived."
        evidence = (
            f"Red-zone touch share trending {row['touch_share_trend_pct']:.0f}th percentile, "
            f"snap share trending {row['snap_share_trend_pct']:.0f}th percentile"
        )
    return {"headline": headline, "evidence": evidence}


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
    """
    if row["role_trend"] >= row["external_opportunity"]:
        headline = f"This {position} role may already be changing hands."
        snap_last1 = row.get("snap_share_last1")
        snap_season = row.get("snap_share_season_avg")
        snap_really_up = pd.notna(snap_last1) and pd.notna(snap_season) and snap_last1 > snap_season
        if snap_really_up:
            evidence = f"Snap share {snap_season*100:.0f}% (season) → {snap_last1*100:.0f}% (most recent)"
        else:
            evidence = (
                f"Touch-share trend {row['touch_share_trend_pct_role']:.0f}th percentile, "
                f"snap-share trend {row['snap_share_trend_pct_role']:.0f}th percentile, "
                f"depth-chart movement {row['depth_chart_movement_pct']:.0f}th percentile"
            )
    else:
        headline = f"An opportunity may be opening up ahead of him on the {position} depth chart."
        statuses = _as_list(row.get("ahead_injury_statuses"))
        evidence = (
            f"Teammate(s) ranked ahead of him carry an injury designation: {', '.join(statuses)}"
            if statuses else "Ranked ahead of a teammate whose availability is now in question"
        )
    return {"headline": headline, "evidence": evidence}


def _finalize_cards(shelf_result: dict, story_fn) -> list:
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
            "completeness": row["_completeness_value"],
            "meets_evidence_threshold": entry["meets_evidence_threshold"],
            "consensus_price_american": row.get("consensus_price_american"),
            "headline": story["headline"],
            "evidence": story["evidence"],
        })
    return cards


def build_red_zone_trends(weekly: pd.DataFrame, config: dict = CONFIG) -> dict:
    """
    Ranked by td_opportunity — see scoring.score_td_opportunity, reused
    unmodified. No position filter (Red Zone Trends spans all eligible
    RB/WR/TE).
    """
    threshold = config["completeness_threshold"]["red_zone_trends"]
    weekly = add_red_zone_trend_windows(weekly)
    weekly["_primary_value"] = weekly["td_opportunity"]
    weekly["_completeness_value"] = weekly["td_opportunity_completeness"]

    result = _build_shelf(
        weekly, primary_col="td_opportunity", completeness_col="td_opportunity_completeness",
        threshold=threshold, shelf_size=config["shelf_size"], odds_floor=config["attd_odds_floor"],
    )
    result["cards"] = _finalize_cards(result, red_zone_story)
    return result


def _build_position_trends(weekly: pd.DataFrame, position: str, shelf_key: str, config: dict) -> dict:
    threshold = config["completeness_threshold"][shelf_key]
    weekly = weekly.copy()
    weekly["_primary_value"] = weekly["role_momentum"]
    weekly["_completeness_value"] = weekly["role_momentum_completeness"]

    result = _build_shelf(
        weekly, primary_col="role_momentum", completeness_col="role_momentum_completeness",
        threshold=threshold, shelf_size=config["shelf_size"], odds_floor=config["attd_odds_floor"],
        position_filter=position,
    )
    result["cards"] = _finalize_cards(result, lambda row: position_story(row, position))
    return result


def build_rb_trends(weekly: pd.DataFrame, config: dict = CONFIG) -> dict:
    return _build_position_trends(weekly, "RB", "rb_trends", config)


def build_te_trends(weekly: pd.DataFrame, config: dict = CONFIG) -> dict:
    return _build_position_trends(weekly, "TE", "te_trends", config)


def build_wr_trends(weekly: pd.DataFrame, pbp: pd.DataFrame = None, config: dict = CONFIG) -> dict:
    """
    Same role_momentum-primary ranking as RB/TE Trends. If `pbp` is
    provided, also attaches whole-game target-share movement (this
    shelf's one approved V1 extension) as supporting evidence and a
    tertiary tiebreaker — never as a ranking input ahead of
    role_momentum. Omit `pbp` to build the shelf without it (role_
    momentum-only ranking still works identically; only the extra
    evidence field and tiebreak are unavailable).
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
            story["evidence"] += f"; whole-game target share trend {row['target_share_trend']*100:+.1f}pp"
        return story

    result["cards"] = _finalize_cards(result, _wr_story)
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


def odds_band_story(row: pd.Series) -> dict:
    """
    PUBLIC (no leading underscore) — same reasoned exception as
    red_zone_story/position_story above.

    Odds-band shelves are ranked by the final composite (tpe_score), not
    one themed pillar the way the four trend shelves are — there's no
    "proven vs. emerging"-style split to draw a specific narrative from
    here. Cites the real composite score and evidence quality directly
    rather than inventing a themed storyline these shelves don't have.
    """
    return {
        "headline": "The model's combined read has him near the top of this price range.",
        "evidence": f"Universal TPE Score {row['tpe_score']:.0f}/100, evidence quality {row['evidence_quality']:.0f}/100",
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


def build_odds_band_shelf(weekly: pd.DataFrame, lo: int, hi, config: dict = CONFIG) -> dict:
    result = _build_odds_band_shelf(weekly, lo, hi, config["shelf_size"])
    result["cards"] = _finalize_cards(result, odds_band_story)
    return result


def build_all_shelves(weekly: pd.DataFrame, pbp: pd.DataFrame = None, config: dict = CONFIG) -> dict:
    """
    All seven shelves at once, fixed blueprint order: Red Zone Trends,
    RB Trends, WR Trends, TE Trends, then the three ATTD odds-band
    shelves. pbp is optional (only needed for WR Trends' target-share
    extension); omit it to build all seven shelves from `weekly` alone.
    """
    shelves = {
        "Red Zone Trends": build_red_zone_trends(weekly, config),
        "RB Trends": build_rb_trends(weekly, config),
        "WR Trends": build_wr_trends(weekly, pbp, config),
        "TE Trends": build_te_trends(weekly, config),
    }
    for label, lo, hi in ODDS_BANDS:
        shelves[label] = build_odds_band_shelf(weekly, lo, hi, config)
    return shelves
