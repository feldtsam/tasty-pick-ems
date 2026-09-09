"""
Market Intelligence — Market Trends V1, Deviation only.

REPLACES the earlier snapshot-standing generator entirely (retired, not
kept running alongside this — an explicit, deliberate call: the old
market-favored/longshot/neutral stories were a player's current rank
against the pool, which is real information but not a COMPARISON between
two independently-derived facts, and so never actually satisfied Market
Trends' own eligibility rule (a price alone is not intelligence; a
comparison makes it intelligence). Real, stated consequence of retiring it
outright: this family produces fewer stories, possibly close to none some
weeks, until enough players clear the Deviation floor below to replace the
volume the old generator used to produce. Chosen on purpose over running
both in parallel.

CORE RULE (Market Trends spec, locked): magnitude determines whether
something is interesting; evidence determines whether we trust it. The two
are computed independently and never blended into one number -- see
signal_type/evidence_state's own build_story attachment below, same
pattern the Universal Card v2 evidence_classification field already
established (a SEPARATE axis from magnitude/trend_strength, not folded
into it).

SIGNAL TYPE SCOPE, V1: Deviation only. Movement (same book, price change
over time) and Disagreement (multiple books, concurrent spread) are the
spec's other two signal_types, deliberately not built here -- Movement
needs a real accumulated price-history table (nfl_price_history has never
had a row written to it as of this task -- see market_value.py's own
docstring on market_intelligence_snapshot_for_generation), and
Disagreement needs the market snapshot to retain a PER-BOOK breakdown,
which today's PRICE_HISTORY_COLUMNS schema collapses away into a
consensus/best/n_books aggregate before it's ever persisted. Both are
real, separate follow-up tasks, not partially started here.

REAL, CURRENT DATA GAP THIS MODULE INHERITS, NOT INTRODUCES: the market
snapshot this module's own build_deviation_stories() consumes
(market_intelligence_snapshot_for_generation) reads from nfl_price_history,
the same currently-empty table Movement is blocked on -- confirmed by
reading that function's own docstring directly, not assumed. That means
this module's real, correct scoring logic will produce zero stories until
that table has real rows (the Make.com poll-market-value scenario actually
running), independent of anything about Deviation's OWN eligibility floor.
Flagged explicitly so "Deviation ships with no blockers" is never
misread as "Deviation produces stories on day one" -- it produces
stories the day nfl_price_history has real, fresh rows, whenever that is.

DEVIATION BASELINE (§5 of the spec, verified independent, not assumed):
a player's expected probability is the peer median consensus_implied_
probability within their (position_group, on-field-pillar tier). The
tiering input -- td_opportunity/role_momentum/situation, explicitly
EXCLUDING market_value_score -- is computed fresh in this module
(_peer_tier_core_score below), not read off any core_score/tpe_score
column the weekly snapshot might already carry: those, if persisted, may
have market_value_score already folded in (weekly rows have historically
carried a joined market_value_score in `extra`, per reconcile_week.py's
own docstring), and reusing that value here would silently reintroduce
the exact circularity §5 rules out for Market Value itself. This is
observed-against-observed (real peer prices), never a manufactured
fair-price model -- a genuinely calibrated one is explicitly deferred
(§11), not a V1 launch dependency.

FRESHNESS (§6) is enforced upstream of everything else: _freshness_gate
runs BEFORE peer-tier assignment, so a stale price is excluded both as a
story's own subject AND as a peer-comparison input for anyone else's
expected probability -- never a low-confidence story, never a silent
contributor to another player's tier median.
"""
import math

import pandas as pd

from intelligence_schema import build_story

CONFIG = {
    # Deviation eligibility floor, implied-probability percentage points
    # (§4). Below this, no story is generated at all -- not a low-magnitude
    # story, no story.
    "deviation_floor_pp": 5.0,
    # Magnitude bands (§4), for internal ranking/labeling only, not separate
    # eligibility gates. Because the floor above equals the strong-band
    # threshold, a real Deviation story can never land in "notable" by
    # construction -- kept at the spec's full range anyway so this logic
    # doesn't silently assume the floor will never move.
    "magnitude_notable_pp": 3.0,
    "magnitude_strong_pp": 5.0,
    "magnitude_extreme_pp": 8.0,
    # Peer-tier grouping: how many core_score buckets within each
    # position_group. Starting hypothesis (quintiles) -- same "tune once
    # real coverage distributions are visible" posture §4/§6 of the spec
    # explicitly call for on the eligibility floors and evidence-state
    # cutoffs.
    "n_peer_tiers": 5,
    # Evidence state (§3b) book-count cutoffs. A Deviation story rests on
    # ONE concurrent poll (not a cross-time aggregation), so every book
    # behind it is inherently concurrent by construction (one Odds API
    # response) -- n_books alone decides thin/developing/confirmed here.
    "evidence_developing_books": 2,
    "evidence_confirmed_books": 3,
    # Freshness (§6): a single-poll observation older than this cannot
    # represent current market state or seed a new story.
    "freshness_max_age_hours": 2.0,
    # evidence_classification (Universal Card v2) -- unchanged real formula,
    # confirmed from Lovable's own trustIndicator() (same thresholds every
    # other family already uses). A SEPARATE axis from evidence_state above
    # -- see intelligence-types.ts's own doc comments for why a Market
    # Trends story carries both, additive, not one replacing the other.
    "evidence_strong_threshold": 80.0,
    "evidence_moderate_threshold": 60.0,
    # "Fully covered" bar for the completeness geometric mean below --
    # same constant name/value the retired snapshot-standing generator
    # used for the same purpose.
    "full_coverage_books": 3,
}


def _book_coverage_confidence(n_books, target: int) -> float:
    """0-100: how much of "full" book coverage this row actually has."""
    if pd.isna(n_books) or n_books <= 0:
        return 0.0
    return min(n_books / target, 1.0) * 100.0


def _peer_tier_core_score(pool: pd.DataFrame) -> pd.Series:
    """
    On-field-only composite (TD Opportunity/Role Momentum/Situation),
    explicitly and permanently excluding Market Value -- §5's independence
    requirement.

    Deliberately NOT scoring.score_universal_tpe(weekly, market_value=None):
    that function hard-requires an evidence_quality column (its own
    confidence_multiplier/tpe_score half, which this module has no reason
    to compute) -- confirmed by reading its real body, not assumed. Reusing
    it and stripping columns to dodge that dependency would be more
    fragile than replicating just the weighted-sum-with-renormalization
    half directly. Same weight constants (scoring.CONFIG's own
    universal_tpe.core_weights, market_value_score excluded), same
    present-columns-only renormalization (a per-ROW notna check, not a
    population filter) -- so this tracks that config if it's ever retuned,
    without needing evidence_quality at all.
    """
    from scoring import CONFIG as SCORING_CONFIG

    weights = {
        k: v
        for k, v in SCORING_CONFIG["universal_tpe"]["core_weights"].items()
        if k != "market_value_score"
    }
    present = [c for c in weights if c in pool.columns]
    if not present:
        return pd.Series(float("nan"), index=pool.index)
    scores = pool[present]
    weight_vec = pd.Series({c: weights[c] for c in present})
    valid = scores.notna()
    weighted_sum = (scores.fillna(0) * weight_vec).sum(axis=1)
    weight_totals = (valid * weight_vec).sum(axis=1).replace(0, float("nan"))
    result = weighted_sum / weight_totals
    return result.replace([float("inf"), float("-inf")], float("nan")).round(1)


def _assign_peer_tiers(pool: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    Peer tiers = (position_group, core_score quintile) -- §5's "group
    players into tiers by these on-field pillars." Scoped WITHIN
    position_group: an RB's and a WR's on-field TD-opportunity/role/
    situation reads live on structurally different scales, so pooling them
    into one tier would compare players who aren't real peers just because
    their raw numbers landed in the same bucket.

    pd.qcut (equal-COUNT bins) rather than equal-width: "same tier" should
    mean "similarly ranked among real peers on a real, possibly lumpy or
    skewed scale," not "similar raw score." duplicates="drop" and the
    too-few-real-values fallback both handle a thin position_group pool
    honestly -- every player becomes their own tier of one rather than a
    fabricated bucket boundary, which correctly makes their peer-median
    expectation equal to their own price (deviation = 0, never eligible)
    instead of a manufactured comparison.
    """
    pool = pool.copy()
    pool["_peer_core_score"] = _peer_tier_core_score(pool)

    def _tier_for_group(g: pd.DataFrame) -> pd.Series:
        real = g["_peer_core_score"].notna().sum()
        if real < 2:
            return pd.Series(range(len(g)), index=g.index, dtype="float64")
        try:
            return pd.qcut(g["_peer_core_score"], config["n_peer_tiers"], labels=False, duplicates="drop").astype("float64")
        except ValueError:
            return pd.Series(0.0, index=g.index, dtype="float64")

    # Explicit per-group loop + concat, not groupby(...).apply(...): apply's
    # own result-shape inference is genuinely ambiguous here (confirmed
    # directly, not assumed -- _tier_for_group's two branches return
    # different-dtype Series depending on real-value count, which pandas
    # sometimes concatenates into a DataFrame instead of a Series,
    # especially with a single real group in the pool). A manual loop with
    # a fixed dtype on every branch is predictable regardless of how many
    # distinct position_groups are actually present.
    tier_parts = [_tier_for_group(g) for _, g in pool.groupby("position_group", group_keys=False)]
    pool["_peer_tier"] = pd.concat(tier_parts) if tier_parts else pd.Series(dtype="float64")
    return pool


def _freshness_gate(snapshot: pd.DataFrame, config: dict, now: pd.Timestamp = None) -> pd.DataFrame:
    """
    §6 -- enforced BEFORE peer-tier assignment/magnitude/evidence-state,
    not after. A stale price is excluded from the whole pipeline at this
    point: it can never be a story's own subject, and it never contributes
    to another player's peer-tier expected-probability median either.

    A negative age (a bad/future poll_timestamp) is rejected, not treated
    as fresh -- a real data-quality issue should degrade to "excluded,"
    same as any other unparseable/missing input in this codebase, not
    silently pass a freshness check it doesn't actually satisfy.
    """
    if now is None:
        now = pd.Timestamp.now(tz="UTC")
    ts = pd.to_datetime(snapshot["poll_timestamp"], utc=True, errors="coerce")
    age_hours = (now - ts).dt.total_seconds() / 3600.0
    return snapshot[(age_hours >= 0) & (age_hours <= config["freshness_max_age_hours"])].copy()


def _evidence_state_for_row(n_books, config: dict) -> str:
    """§3b -- independent of signal_type and of magnitude."""
    if pd.isna(n_books) or n_books < config["evidence_developing_books"]:
        return "thin"
    if n_books < config["evidence_confirmed_books"]:
        return "developing"
    return "confirmed"


def _still_to_watch_for_row(evidence_state: str, n_books, magnitude_band: str) -> list:
    """
    §9's STILL TO WATCH -- genuinely forward-looking (what would confirm or
    undercut this as more data comes in), replacing the retrospective
    WHAT CHANGED a story-history log would show. Real, distinct from
    supporting_evidence: those are the facts the story rests on right now;
    this is what to watch for next, so the two sections can never just
    restate each other under different labels.
    """
    items = []
    if evidence_state == "thin":
        items.append({
            "label": "More books posting",
            "observation": f"Only {int(n_books) if pd.notna(n_books) else 0} book has posted so far -- a second "
            "or third line could confirm this read, or reveal it was one outlier book.",
        })
    elif evidence_state == "developing":
        items.append({
            "label": "A third book",
            "observation": "Two books agree so far -- one more posting the same way would move this to a "
            "confirmed read.",
        })
    else:
        items.append({
            "label": "Continued agreement",
            "observation": "Already a well-covered read -- watch whether newly-posting books keep agreeing with "
            "it, or a late line pulls the consensus back toward the peer-tier expectation.",
        })
    items.append({
        "label": "Whether the gap closes",
        "observation": f"A {magnitude_band} gap like this can close either way -- the market moving back toward "
        "the peer-tier expectation, or his on-field profile (next game's role/opportunity) catching up to what "
        "the market already believes.",
    })
    return items


def _magnitude_band(gap_pp: float, config: dict) -> str:
    if gap_pp >= config["magnitude_extreme_pp"]:
        return "extreme"
    if gap_pp >= config["magnitude_strong_pp"]:
        return "strong"
    return "notable"


def _story_completeness(n_books, config: dict) -> float:
    """
    Same geometric-mean shape scoring.score_evidence_quality uses for an
    analogous "two axes, neither sufficient alone" situation, reused
    deliberately -- here, "does a real peer-tier expectation exist at all"
    (always true by construction once a row reaches this function; a row
    with no real tier comparison never gets this far) combined with book
    coverage. Feeds evidence_classification (the shared cross-family trust
    indicator), a SEPARATE field from this module's own evidence_state.
    """
    book_conf = _book_coverage_confidence(n_books, config["full_coverage_books"])
    return round(math.sqrt(100.0 * book_conf), 1)


def _evidence_classification_for_row(completeness: float, confidence: float, config: dict) -> str:
    """Same real formula as the other three families, confirmed directly from Lovable's own trustIndicator()."""
    score = (confidence + completeness) / 2
    if score >= config["evidence_strong_threshold"]:
        return "strong"
    if score >= config["evidence_moderate_threshold"]:
        return "moderate"
    return "limited"


def _related_players(pool: pd.DataFrame, event_id, team, player_id, limit: int = 5) -> list:
    """
    Same-game teammates with a posted market -- generic, real context for
    "who else has real money on them this same market," not deviation-
    specific. Unchanged in spirit from the retired generator's own version
    of this helper.
    """
    teammates = pool[(pool["event_id"] == event_id) & (pool["team"] == team) & (pool["player_id"] != player_id)]
    teammates = teammates.sort_values("consensus_implied_probability", ascending=False).head(limit)
    return [
        {
            "player_id": r["player_id"],
            "display_label": r["player_name_raw"],
            "entity_type": "player",
            "direction_indicator": "none",
            "note": f"{int(r['consensus_price_american']):+d} ({r['consensus_implied_probability'] * 100:.1f}% implied)",
        }
        for _, r in teammates.iterrows()
    ]


def _format_kickoff_et(commence_time: str) -> str:
    """Real UTC ISO-8601 -> DST-aware ET display string. Unchanged from the retired generator."""
    ts = pd.Timestamp(commence_time)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    et = ts.tz_convert("America/New_York")
    hour12 = et.hour % 12 or 12
    ampm = "AM" if et.hour < 12 else "PM"
    return f"{et.strftime('%b')} {et.day}, {hour12}:{et.minute:02d} {ampm} ET"


def _headline_and_story(row: pd.Series, gap_pp: float, thin: bool) -> tuple:
    """
    VOICE (TPE Editorial Voice Spec, Section 3): Market Intelligence stays
    the tightest of the families at a ~3.5-4/10 ceiling -- observant,
    skeptical, numbers-forward. The comparison itself is the story; copy
    never competes with the two real numbers (observed vs. peer-tier
    expected) for attention. Direction: a POSITIVE gap means the market
    thinks this player is MORE likely to score than his on-field peer
    tier would suggest (shorter/more-favored price than peers); negative
    means the market is cooler on him than his peers' on-field profile
    would suggest.
    """
    name = row["player_name_raw"]
    matchup = f"{row['away_team']} @ {row['home_team']}"
    observed_pct = row["consensus_implied_probability"] * 100
    expected_pct = row["expected_probability"] * 100

    if gap_pp > 0:
        if thin:
            headline = "One early line already prices him well ahead of his on-field peer tier."
            story = (
                f"A single early line has {name} priced stronger than his on-field peer tier would suggest for "
                f"{matchup}. One book, though -- worth another look once more of them post."
            )
        else:
            headline = "The market is pricing him well ahead of his own on-field peer tier."
            story = (
                f"{name}'s market price implies a real edge over what his on-field profile (touch opportunity, "
                f"role, matchup) suggests peers at his tier get priced at for {matchup}."
            )
    else:
        if thin:
            headline = "One early line already prices him behind his on-field peer tier."
            story = (
                f"A single early line has {name} priced weaker than his on-field peer tier would suggest for "
                f"{matchup}. One book, though -- worth another look once more of them post."
            )
        else:
            headline = "The market is pricing him behind his own on-field peer tier."
            story = (
                f"{name}'s market price implies real skepticism relative to what his on-field profile suggests "
                f"peers at his tier get priced at for {matchup}."
            )

    return headline, story, observed_pct, expected_pct


def build_deviation_stories(market_snapshot: pd.DataFrame, weekly: pd.DataFrame, config: dict = CONFIG) -> list:
    """
    market_snapshot: market_intelligence_snapshot_for_generation()'s own
    output -- one row per player with a posted player_anytime_td market,
    already scored by scoring.score_market_value().

    weekly: role_defensive_weekly_snapshot()'s own output -- the same real
    season snapshot Role Changes/Defensive Trends/Coaching Trends already
    read, needed here only for its on-field pillar columns (td_opportunity/
    role_momentum/situation) that feed peer-tier assignment.

    One story per player whose deviation clears config["deviation_floor_pp"]
    after freshness gating -- not one row per market_snapshot row. A
    genuinely empty/thin snapshot (see market_value.py's own docstring on
    why nfl_price_history is currently empty) degrades correctly to zero
    stories, the same honest-degradation shape every other family already
    has for its own empty-input case.
    """
    if len(market_snapshot) == 0:
        return []

    fresh = _freshness_gate(market_snapshot, config)
    if len(fresh) == 0:
        return []

    pillar_cols = ["player_id", "td_opportunity", "role_momentum", "situation"]
    available = [c for c in pillar_cols if c in weekly.columns]
    pillars = weekly[available].drop_duplicates(subset="player_id", keep="last") if "player_id" in available else pd.DataFrame(columns=pillar_cols)

    pool = fresh.merge(pillars, on="player_id", how="left")
    # A player with no real on-field pillar row at all (no weekly redzone
    # history -- a real, expected case for a player new to the league or
    # not yet reconciled this season) can't get a real peer-tier
    # comparison. §1's own eligibility test requires a real comparison;
    # excluded here rather than defaulted into a fabricated tier.
    pool = pool[pool[[c for c in ("td_opportunity", "role_momentum", "situation") if c in pool.columns]].notna().any(axis=1)]
    if len(pool) == 0:
        return []

    tiered = _assign_peer_tiers(pool, config)
    expected = (
        tiered.groupby(["position_group", "_peer_tier"])["consensus_implied_probability"]
        .median()
        .rename("expected_probability")
    )
    tiered = tiered.merge(expected, on=["position_group", "_peer_tier"], how="left")
    tiered["_gap_pp"] = (tiered["consensus_implied_probability"] - tiered["expected_probability"]) * 100

    eligible = tiered[tiered["_gap_pp"].abs() >= config["deviation_floor_pp"]].copy()
    eligible = eligible.sort_values("_gap_pp", key=lambda s: s.abs(), ascending=False).reset_index(drop=True)

    stories = []
    for _, row in eligible.iterrows():
        gap_pp = float(row["_gap_pp"])
        n_books = row["n_books"]
        thin = pd.isna(n_books) or n_books < config["full_coverage_books"]
        headline, story_text, observed_pct, expected_pct = _headline_and_story(row, gap_pp, thin)
        evidence_state = _evidence_state_for_row(n_books, config)
        magnitude_band = _magnitude_band(abs(gap_pp), config)
        completeness = _story_completeness(n_books, config)

        evidence = [
            f"Market: {int(row['consensus_price_american']):+d} ({observed_pct:.1f}% implied probability)",
            f"Peer-tier expectation: {expected_pct:.1f}% implied probability, from the median of players with a "
            f"similar on-field profile (TD opportunity, role, situation) at his position",
            f"Gap: {abs(gap_pp):.1f} percentage points {'above' if gap_pp > 0 else 'below'} his peer tier's expected price "
            f"({magnitude_band})",
            f"Based on {int(n_books) if pd.notna(n_books) else 0} book"
            f"{'s' if pd.isna(n_books) or n_books != 1 else ''} — "
            f"{'a thin, early read' if thin else 'solid multi-book coverage'}",
        ]

        time_window = f"Live snapshot, {row['away_team']} @ {row['home_team']}, kickoff {_format_kickoff_et(row['commence_time'])}"

        story = build_story(
            intelligence_family="market_intelligence",
            entity={
                "type": "player",
                "player_id": row["player_id"],
                "player_name": row["player_name_raw"],
                "team": row["team"],
                "position_group": row.get("position_group"),
            },
            headline=headline,
            story=story_text,
            primary_signal={"name": "deviation_pp", "value": round(gap_pp, 1)},
            supporting_evidence=evidence,
            trend_direction="deviation-favorable" if gap_pp > 0 else "deviation-unfavorable",
            trend_strength=round(min(abs(gap_pp) * 5, 100.0), 1),
            sample_size=int(n_books) if pd.notna(n_books) else 0,
            completeness=completeness,
            confidence=completeness,
            time_window=time_window,
            related_players=_related_players(tiered, row["event_id"], row["team"], row["player_id"]),
        )
        # Universal Card v2 fields -- attached after build_story(), same
        # "additive, not part of the hard schema" pattern the other three
        # families' own v2 fields already use.
        story["hero_metric"] = {
            "label": "Implied probability vs. peer tier",
            "unit": "%",
            "value_format": "percent",
            "before_value": round(expected_pct, 1),
            "after_value": round(observed_pct, 1),
            "delta_value": round(gap_pp, 1),
        }
        story["signal_direction"] = "favorable" if gap_pp > 0 else "unfavorable"
        story["what_changed"] = _still_to_watch_for_row(evidence_state, n_books, magnitude_band)
        story["evidence_classification"] = _evidence_classification_for_row(story["completeness"], story["confidence"], config)
        # Market Trends V1 fields -- additive alongside evidence_
        # classification above, never a replacement for it (confirmed
        # explicitly before this module was written, not inferred).
        story["signal_type"] = "deviation"
        story["evidence_state"] = evidence_state
        stories.append(story)

    return stories
