"""
Market Intelligence — the first of four NFL Intelligence families (Role
Changes, Defensive Trends, Coaching Trends are not built yet). Chosen to
go first specifically because it's near-pure reuse of already-validated
work (market_value.py, scoring.score_market_value) — the cheapest place
to get intelligence_schema.py's shared story shape right before three
more families build on top of it.

CORE QUESTION: "What is the betting market doing?" — how strongly (or
weakly) the market believes a player will score, and how much of that
read to trust given how many books are actually behind it.

V1 SCOPE, DELIBERATELY: SNAPSHOT STANDING, NOT MOVEMENT. market_value.py's
own PRICE_HISTORY_COLUMNS design (its module docstring) is still
unpopulated — nothing in this project has ever appended a row to a real
price-history table. That means there is no real "odds moved from +450
to +320" data to build a story from today, and this module does not
fake one. trend_direction/trend_strength here describe CURRENT STANDING
relative to the same event's eligible pool (via the existing percentile
mechanism, score_market_value's own output) — "the market currently has
him near the top of the board," never "his price has been climbing."
A real movement-based Market Intelligence story (true week-over-week or
even intra-week price trend) is a genuine, flagged VALUABLE LATER item,
gated entirely on the price-history table getting built — not something
this module should approximate with a proxy in the meantime.

SAMPLE-SIZE HONESTY: n_books is a real, meaningful confidence signal
market_value_completeness (score_market_value's own column) does NOT
capture on its own — that column only tracks whether a real percentile
was computed at all vs. neutral-fallback, not how many books stood
behind the price that got percentile-ranked. A 1-book price and a
5-book consensus are not equally trustworthy even when both produced a
"real" percentile. This module's own `completeness` combines both axes
via the same geometric-mean shape scoring.score_evidence_quality already
uses for an analogous "two axes, neither sufficient alone" situation —
reused, not reinvented. Thin coverage also changes the HEADLINE
LANGUAGE itself, not just the numeric confidence field (see
_headline_and_story) — a 1-book read should read like an early, hedged
signal, not a confident market verdict.

DATA CHECK BEFORE BUILDING (real, not assumed): every player_id/team/
position_group/event_id field this module needs is already present on
market_value.snapshot_scoring_inputs' / scoring.score_market_value's own
output. No extension to market_value.py was needed — confirmed directly
before writing this module, not assumed.
"""
import math

import pandas as pd

from intelligence_schema import build_story

CONFIG = {
    # n_books at or above this is treated as "fully covered" for
    # confidence purposes — a starting hypothesis (this project's whole
    # NFL build has never yet seen a real n_books > 1, since every
    # captured snapshot so far has come from a single book posting this
    # early before kickoff), tunable once real multi-book data exists.
    "full_coverage_books": 3,
    # market_value_score bands for trend_direction — starting points,
    # same "hypothesis to tune" treatment scoring.CONFIG's own constants
    # get.
    "favored_threshold": 65.0,
    "longshot_threshold": 35.0,
    # evidence_classification (Universal Card v2) -- the REAL formula,
    # confirmed directly from Lovable's own trustIndicator() (same
    # thresholds already confirmed and shipped for the other three
    # families).
    "evidence_strong_threshold": 80.0,
    "evidence_moderate_threshold": 60.0,
}


def _book_coverage_confidence(n_books, target: int) -> float:
    """0-100: how much of "full" book coverage this row actually has."""
    if pd.isna(n_books) or n_books <= 0:
        return 0.0
    return min(n_books / target, 1.0) * 100.0


def _story_completeness(market_value_completeness: float, n_books, config: dict) -> float:
    """
    Geometric mean of (does a real percentile exist at all) and (how
    many books stand behind it) — same two-necessary-axes shape as
    scoring.score_evidence_quality's completeness*convergence, reused
    deliberately: a real percentile built on one book isn't fully
    trustworthy, and neither is a "complete" reading that turns out to
    be a neutral-50 fallback in disguise. Either axis near zero should
    crater the combined read, same reasoning as the original.
    """
    book_conf = _book_coverage_confidence(n_books, config["full_coverage_books"])
    return round(math.sqrt(max(market_value_completeness, 0.0) * book_conf), 1)


def _trend_direction_and_strength(market_value_score: float, config: dict) -> tuple:
    """
    STANDING, not movement — see module docstring. direction is which
    side of the pool this player sits on right now; strength is how far
    from a neutral 50 read that standing is (0 = dead-even market read,
    100 = as extreme as this pool gets).
    """
    if market_value_score >= config["favored_threshold"]:
        direction = "market-favored"
    elif market_value_score <= config["longshot_threshold"]:
        direction = "market-longshot"
    else:
        direction = "market-neutral"
    strength = round(min(abs(market_value_score - 50.0) * 2, 100.0), 1)
    return direction, strength


def _related_players(snapshot: pd.DataFrame, event_id, team, player_id, limit: int = 5) -> list:
    """
    SAME-TEAM teammates with a posted market, not everyone in the game —
    an opposing-team player isn't genuinely "related" to a market-
    conviction story about this player's own role; a teammate who's
    priced higher or lower for the SAME anytime-TD market is. Already
    directly computable from score_market_value's own output (event_id/
    team are already columns), no extension needed. Capped at `limit`
    (ranked by market_value_score) rather than dumping an entire
    roster's worth of entries into every story.

    Universal Card v2 shape (confirmed against real data before
    assuming, per every other family's own v2 pass): every real entry
    this function has ever produced is a real player (entity_type is
    always "player" — there's no group/team-level related entity this
    function could produce today). direction_indicator is "none" for
    every entry here, a real, considered choice, not an oversight: a
    market-comparison teammate isn't a causal beneficiary or victim of
    THIS story's own trend the way Defensive/Coaching/Role's related
    players are (see those modules' own reasoning) — they're relevant
    CONTEXT (who else has real money on them this same market), with no
    real up/down claim this module has evidence for. note reuses the
    same real per-teammate fields already available on this same
    DataFrame (not new numbers) — market_value_score plus their own
    real consensus price, the same two real facts the main entity's own
    story already cites for itself.
    """
    teammates = snapshot[
        (snapshot["event_id"] == event_id) & (snapshot["team"] == team) & (snapshot["player_id"] != player_id)
    ]
    teammates = teammates.sort_values("market_value_score", ascending=False).head(limit)
    return [
        {
            "player_id": r["player_id"],
            "display_label": r["player_name_raw"],
            "entity_type": "player",
            "direction_indicator": "none",
            "note": f"Market value score {r['market_value_score']:.0f}/100 · {int(r['consensus_price_american']):+d} ({r['consensus_implied_probability']*100:.1f}% implied)",
        }
        for _, r in teammates.iterrows()
    ]


def _headline_and_story(row: pd.Series, direction: str, pool_rank: int, pool_size: int, thin: bool) -> tuple:
    """
    Story first, per this project's established storytelling hierarchy
    (see shelves.py) — headline makes the claim, story adds one sentence
    of context, supporting_evidence (built by the caller) carries the
    actual numbers. Language hedges explicitly when thin=True (n_books
    below CONFIG's full-coverage target) — the whole point of the
    sample-size honesty requirement is that a headline itself should
    read differently for a 1-book price than a well-covered one, not
    just carry a lower number in a field nobody's looking at.
    """
    name = row["player_name_raw"]
    matchup = f"{row['away_team']} @ {row['home_team']}"

    if direction == "market-favored":
        if thin:
            headline = "One early book already has him near the top of the board."
            story = (
                f"A single early line already prices {name} among the field's strongest anytime-TD bets for "
                f"{matchup} — worth confirming once more books post, but a notable first read."
            )
        else:
            headline = "The market has him as one of the field's clearest bets to score."
            story = (
                f"With real multi-book coverage behind it, the market consistently prices {name} near the top "
                f"of the board for {matchup} — this is a well-supported read, not an early outlier."
            )
    elif direction == "market-longshot":
        if thin:
            headline = "An early, thin line has him near the back of the board."
            story = (
                f"Only one book has posted on {name} so far for {matchup}, and it's a long price — too early "
                f"to call this a real market verdict yet."
            )
        else:
            headline = "The market isn't buying it — priced as one of the longer shots on the board."
            story = f"Multiple books agree: {name} is priced near the bottom of this week's field for {matchup}."
    else:
        headline = "The market has him priced in the middle of the pack, at least in an early read."
        if thin:
            story = (
                f"With only one book posted so far, {name} sits at rank {pool_rank} of {pool_size} in this week's "
                f"early market-implied field for {matchup} — too thin to call a real signal either way yet."
            )
        else:
            story = f"{name} sits at rank {pool_rank} of {pool_size} in this week's market-implied field for {matchup} — no strong signal either way."

    return headline, story


def _format_kickoff_et(commence_time: str) -> str:
    """
    commence_time: real UTC ISO-8601 string, The Odds API's own raw
    format (confirmed at parse_attd_event / market_value.PRICE_HISTORY_
    COLUMNS) — converted here to a real, DST-aware Eastern Time display
    string ("Sep 8, 1:00 PM ET"), the standard display timezone every
    other real kickoff time in this codebase already uses (see redzone.
    py's own America/New_York kickoff_et handling).

    Uses pandas' real IANA-backed tz database (tz_convert, not a fixed
    UTC offset) — confirmed directly against 10 real cases spanning
    every real kickoff shape this needs to handle correctly, not just
    the early-Sunday-afternoon happy path: early/late Sunday, TNF/SNF/
    MNF (all three of which land on the NEXT UTC calendar day at these
    kickoff times — tz_convert correctly rolls the LOCAL date back,
    confirmed explicitly, not assumed), the real November DST
    transition (EDT before, EST after — no special-casing needed, the
    real tz database already knows this), a real international early-
    window kickoff, and midnight/noon-hour edges.
    """
    ts = pd.Timestamp(commence_time)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    et = ts.tz_convert("America/New_York")
    hour12 = et.hour % 12 or 12
    ampm = "AM" if et.hour < 12 else "PM"
    return f"{et.strftime('%b')} {et.day}, {hour12}:{et.minute:02d} {ampm} ET"


# ============================================================
# Universal Card v2 fields — same "attach after build_story(), not
# threaded through STORY_FIELDS" approach the other three families
# already established. Last of the four; see each function's own
# docstring for what's genuinely different about Market's real shape.
# ============================================================

def _signal_direction_for_row(direction: str) -> str:
    """
    A real, considered judgment call, not an obvious mechanical mapping
    — flagged explicitly (per Sam's own explicit invitation to
    reconsider rather than force a value here), not silently assumed.

    trend_direction here is a SNAPSHOT-STANDING read (market-favored/
    market-longshot/market-neutral), never a movement — confirmed
    already, unchanged. But signal_direction's real job across every
    family isn't "is this a trend," it's "does this real signal help or
    hurt a bettor considering this specific player" — the same
    generic, cross-family vocabulary already applied to Role Changes'
    own non-movement trend_direction (opportunity-driven/role-trend-
    driven, also not a trend). Market's current standing is real,
    substantive information for that same question: the market
    strongly believing a player scores (market-favored) is a real,
    validating signal for that player's own real anytime-TD prospects
    -- favorable, the same spirit Market Intelligence's own headline/
    story text already uses ("one of the field's clearest bets to
    score"). market-longshot is the real inverse (the market itself
    signaling against this player) -- unfavorable. market-neutral has
    no real signal either way -- neutral, the first real use of that
    value across all four families (none of the other three's real
    story populations ever produced it).

    NOT the same question as "is this a good VALUE bet against the
    market's own price" (arbitrage) -- that's a genuinely different
    real question this module was never built to answer (V1 is a
    snapshot standing read, not a value-vs-price model), and isn't
    what's being classified here.
    """
    if direction == "market-favored":
        return "favorable"
    if direction == "market-longshot":
        return "unfavorable"
    return "neutral"


def _hero_metric_for_row() -> None:
    """
    Permanently null by design, not a placeholder waiting on future
    work in this same pass -- confirmed, unchanged from the original
    backend audit: V1 is snapshot-only (market_value.py's PRICE_
    HISTORY_COLUMNS table has never had a row written to it — see this
    module's own docstring), so there is no real upstream time series
    to compute a real before_value/after_value from at all. A real,
    movement-based Market Intelligence hero_metric only becomes
    possible once that table gets built — flagged there already as
    Valuable Later, not approximated here with a fake "before" value.
    """
    return None


def _what_changed_for_row(row: pd.Series, pool_rank: int, pool_size: int, n_books, thin: bool) -> list:
    """
    Real editorial content — genuinely closer to Role Changes' direct-
    reuse case than Defensive/Coaching's fresh-rewrite case: this
    family's own supporting_evidence lines are almost entirely already
    plain language (confirmed directly, not assumed) -- "Consensus
    price: -105 (51.2% implied probability)" and "Based on 1 book — a
    thin, early read" read exactly like real what_changed copy already,
    reused near-verbatim below. The ONE real exception is the pool-rank
    line, which embeds a raw internal field name inline ("Ranks 3 of 20
    players... (market_value_score 95/100)") -- that one number is
    dropped here (primary_signal already carries it elsewhere in the
    schema; no need to duplicate an internal field name in a user-
    facing field just to restate it).
    """
    price_label = "Consensus price"
    coverage_label = "Book coverage"
    items = [
        {"label": price_label, "observation": f"Consensus price: {int(row['consensus_price_american']):+d} ({row['consensus_implied_probability']*100:.1f}% implied probability)."},
        {
            "label": coverage_label,
            "observation": f"Based on {int(n_books) if pd.notna(n_books) else 0} book" + ("s" if pd.isna(n_books) or n_books != 1 else "") + (" — a thin, early read." if thin else " — solid multi-book coverage."),
        },
        {"label": "Market ranking", "observation": f"Ranks {pool_rank} of {pool_size} players with a posted market this week."},
    ]
    return items[:3]


def _evidence_classification_for_row(completeness: float, confidence: float, config: dict) -> str:
    """Same real formula as the other three families, confirmed directly from Lovable's own trustIndicator(): score = (confidence+completeness)/2, strong >= 80, moderate >= 60, else limited."""
    score = (confidence + completeness) / 2
    if score >= config["evidence_strong_threshold"]:
        return "strong"
    if score >= config["evidence_moderate_threshold"]:
        return "moderate"
    return "limited"


def build_market_intelligence_stories(snapshot: pd.DataFrame, config: dict = CONFIG) -> list:
    """
    snapshot: scoring.score_market_value()'s own output (market_value.py's
    snapshot_scoring_inputs, scored) — one row per player with a posted
    player_anytime_td market. One story per row.
    """
    stories = []
    pool_size = len(snapshot)
    ranked = snapshot.sort_values("market_value_score", ascending=False).reset_index(drop=True)

    for idx, row in ranked.iterrows():
        pool_rank = idx + 1
        n_books = row["n_books"]
        thin = pd.isna(n_books) or n_books < config["full_coverage_books"]
        direction, strength = _trend_direction_and_strength(row["market_value_score"], config)
        completeness = _story_completeness(row["market_value_completeness"], n_books, config)
        headline, story_text = _headline_and_story(row, direction, pool_rank, pool_size, thin)

        evidence = [
            f"Consensus price: {int(row['consensus_price_american']):+d} "
            f"({row['consensus_implied_probability']*100:.1f}% implied probability)",
            f"Based on {int(n_books) if pd.notna(n_books) else 0} book"
            f"{'s' if pd.isna(n_books) or n_books != 1 else ''} — "
            f"{'a thin, early read' if thin else 'solid multi-book coverage'}",
            f"Ranks {pool_rank} of {pool_size} players with a posted market this week "
            f"(market_value_score {row['market_value_score']:.0f}/100)",
        ]
        if pd.notna(row.get("best_price")) and row["best_price"] != row["consensus_price_american"]:
            evidence.append(f"Best available price: {int(row['best_price']):+d} at {row['best_book']}")

        # Real display-appropriate caption -- deliberately drops two
        # things the earlier version had: the "(not a trend — see
        # module docstring)" aside (a real, confirmed production bug:
        # Lovable's own schema caps this field at 100 chars, and that
        # clause alone was ~40 chars of internal engineering commentary
        # no real user should ever see; the honesty safeguard it was
        # duplicating already lives fully in _headline_and_story's own
        # careful standing-language, confirmed directly, not assumed),
        # and the raw ISO-8601 poll_timestamp (not genuinely human-
        # readable either, and nothing downstream ever consumed it --
        # confirmed via a real repo-wide search before removing the
        # parameter entirely, not just unused-here). Real full team
        # names (row['away_team']/['home_team'], the same real values
        # _headline_and_story's own `matchup` string already uses) plus
        # a real DST-aware ET kickoff time still clears the 100-char
        # cap with real margin even at the two real longest NFL team
        # names paired together (confirmed: 88 of 100 chars, not just
        # the short-name happy path).
        time_window = f"Live snapshot, {row['away_team']} @ {row['home_team']}, kickoff {_format_kickoff_et(row['commence_time'])}"

        story = build_story(
            intelligence_family="market_intelligence",
            entity={
                "type": "player", "player_id": row["player_id"], "player_name": row["player_name_raw"],
                "team": row["team"], "position_group": row.get("position_group"),
            },
            headline=headline,
            story=story_text,
            primary_signal={"name": "market_value_score", "value": float(row["market_value_score"])},
            supporting_evidence=evidence,
            trend_direction=direction,
            trend_strength=strength,
            sample_size=int(n_books) if pd.notna(n_books) else 0,
            completeness=completeness,
            confidence=completeness,
            time_window=time_window,
            related_players=_related_players(ranked, row["event_id"], row["team"], row["player_id"]),
        )
        # Universal Card v2 fields -- attached after build_story(), not
        # part of its own hard STORY_FIELDS contract. hero_metric is
        # permanently None for this family (see _hero_metric_for_row's
        # own docstring); lifecycle_state is also always None for
        # Market (the already-approved deferral, unrelated to this
        # task, unchanged) -- both real, structural absences, not gaps.
        story["hero_metric"] = _hero_metric_for_row()
        story["signal_direction"] = _signal_direction_for_row(direction)
        story["what_changed"] = _what_changed_for_row(row, pool_rank, pool_size, n_books, thin)
        story["evidence_classification"] = _evidence_classification_for_row(story["completeness"], story["confidence"], config)
        stories.append(story)

    return stories
