"""
Market Value (10% weight, per the NFL Master Blueprint) — The Odds API
integration for NFL Anytime Touchdown Scorer (player_anytime_td) props.

Scoped to snapshot-only signals this round: a single live poll gives
implied probability, consensus price, and best available price. NOT
built yet — price trajectory (line movement over time) or
market-disagreement scoring (spread across books) — both need historical
odds to validate against, and the free-plan historical endpoint doesn't
support player prop markets at all (confirmed directly: the same key
that pulls live odds fine gets a clean 401
HISTORICAL_UNAVAILABLE_ON_FREE_USAGE_PLAN on the historical endpoint).
Building a trajectory score now would mean tuning weights against
nothing — deferred, not solved. See PRICE_HISTORY_COLUMNS below for the
storage design that will make trajectory scoring computable once live
polling has accumulated enough snapshots.

NFL-SPECIFIC PARSER, NOT src/lib/api/odds.py's _normalize_props: the
outcome schema is inverted from the MLB market. MLB's batter_home_runs
market uses {"name": <player>, "description": <team>}; NFL's
player_anytime_td uses {"name": "Yes" (constant — this market is offered
one-sided, no "No" side), "description": <player name>}, with no team
field on the outcome at all. Reusing the MLB parsing logic as-is would
silently misread every row (team read as player name, "Yes" read as
player name) rather than erroring, which is worse than not reusing it.

This module is pure parsing/scoring logic — it never calls The Odds API
itself. A caller (a future polling script, not built this round) fetches
the raw event JSON and passes it in, matching the same "script layer does
I/O, module layer does pure transforms" split as redzone.py/scoring.py.

score_market_value() percentile-ranks consensus_implied_probability using
the same normalize.py primitives (build_reference_scale, percentile_lookup,
fill_neutral) every other pillar's pct() closures are built from — not a
literal call to scoring._percentile_fn, which is hardcoded to the redzone
weekly table's own qualified-population logic (season-total rz_touches),
a concept that doesn't exist here.

STRUCTURALLY CANNOT BE BACKFILLED — not a temporary gap, a permanent
fact for the 2022/2024/2025 rows in player_redzone_weekly.csv. Two
independent reasons, both already established: there is no historical
odds data at all (the free-plan historical endpoint doesn't support
player-prop markets, confirmed directly), and even the CURRENT live
snapshot is never joined onto the historical weekly table (Market Value
was deliberately kept snapshot-only, with its own separate table shape).
market_value_score only ever exists for a live poll's own player pool —
historical rows will never have one, and no future backfill run changes
that unless historical odds data is purchased separately. The final TPE
score formula has to treat this as a standing gap for historical rows,
not a bug to fix.
"""
import json

import numpy as np
import pandas as pd

from normalize import build_reference_scale, fill_neutral, percentile_lookup
from roster_match import match_player_names

ATTD_MARKET = "player_anytime_td"


def _is_dst_outcome(player_name_raw: str) -> bool:
    """
    Team defense/special-teams entries (e.g. "Seattle Seahawks D/ST")
    share the same market as individual players — these aren't a player
    at all and would never match seasonal_rosters. Handled as an
    explicit, separate category (see parse_attd_event's is_dst column),
    not folded into "unmatched".
    """
    return player_name_raw.strip().endswith("D/ST")


def parse_attd_event(event: dict) -> pd.DataFrame:
    """
    Pure parse of one event's raw player_anytime_td response (The Odds
    API's GET .../events/{id}/odds shape) into a tidy long table, one row
    per (book, player) outcome. No roster matching yet — see
    match_attd_players.

    Player name comes from each outcome's `description` field, not
    `name` (which is always the constant "Yes" for this market — see
    module docstring).
    """
    rows = []
    for bm in event.get("bookmakers", []):
        for market in bm.get("markets", []):
            if market.get("key") != ATTD_MARKET:
                continue
            for outcome in market.get("outcomes", []):
                player_name_raw = outcome.get("description", "") or ""
                rows.append(
                    {
                        "event_id": event.get("id"),
                        "commence_time": event.get("commence_time"),
                        "home_team": event.get("home_team"),
                        "away_team": event.get("away_team"),
                        "book_key": bm.get("key"),
                        "book_title": bm.get("title"),
                        "player_name_raw": player_name_raw,
                        "price": outcome.get("price"),
                        "is_dst": _is_dst_outcome(player_name_raw),
                    }
                )
    return pd.DataFrame(rows)


def match_attd_players(
    parsed: pd.DataFrame, seasonal_rosters: pd.DataFrame, team_desc: pd.DataFrame, season: int
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Match each non-D/ST row's player_name_raw to a (player_id,
    position_group, team) via roster_match.match_player_names — the same
    3-way-classified matcher redzone.py's 2025+ depth-chart parser uses —
    constrained to the two teams actually playing in that event. The
    outcome itself carries no team field, so the event's home_team/
    away_team (full names, e.g. "Seattle Seahawks") are resolved to
    nflverse abbreviations via import_team_desc() first. Restricting the
    candidate pool to the two rosters in play also helps resolve name
    collisions (e.g. two different real "Josh Allen"s in the league) that
    a global name match wouldn't.

    D/ST rows are excluded before matching even starts (see
    _is_dst_outcome) — they're a known, explicitly-handled category, not
    a matching failure of any kind.
    """
    parsed = parsed[~parsed["is_dst"]].copy()

    name_to_abbr = dict(zip(team_desc["team_name"], team_desc["team_abbr"]))
    parsed["_candidate_teams"] = parsed.apply(
        lambda r: {name_to_abbr.get(r["home_team"]), name_to_abbr.get(r["away_team"])}, axis=1
    )

    matched, unmatched = match_player_names(
        parsed, seasonal_rosters, season, name_col="player_name_raw", candidate_teams_col="_candidate_teams"
    )
    matched = matched.drop(columns=["_candidate_teams"], errors="ignore")
    unmatched = unmatched.drop(columns=["_candidate_teams"], errors="ignore")
    return matched, unmatched


def implied_probability(price: pd.Series) -> pd.Series:
    """
    Standard American-odds -> implied probability conversion.
    Positive price (underdog-style payout): 100 / (price + 100).
    Negative price (favorite-style payout): -price / (-price + 100).
    """
    price = price.astype(float)
    return pd.Series(
        np.where(price > 0, 100.0 / (price + 100.0), -price / (-price + 100.0)),
        index=price.index,
    )


def _probability_to_american(p: pd.Series) -> pd.Series:
    """
    Inverse of implied_probability, for a readable consensus display
    price. p <= 0.5 -> positive (underdog-style) price; p > 0.5 ->
    negative (favorite-style) price.
    """
    p = p.astype(float)
    return pd.Series(
        np.where(p <= 0.5, ((1 - p) / p * 100).round(), (-(p / (1 - p)) * 100).round()),
        index=p.index,
    )


def snapshot_scoring_inputs(matched: pd.DataFrame) -> pd.DataFrame:
    """
    Collapse matched (one row per book per player, from
    match_attd_players) down to one row per player per poll — the grain
    the price-history table stores (see PRICE_HISTORY_COLUMNS). Designed
    for "1 book available" as the normal case, not an edge case: this far
    from kickoff, most players will only have a single quote, and every
    computation here degenerates correctly to that one value rather than
    needing special-case handling (a median/max of one number is just
    that number).

    consensus_implied_probability is the MEDIAN of each book's OWN
    implied probability, not an average of raw American-odds prices —
    American odds aren't linear in probability space, so averaging the
    raw numbers directly would distort the consensus. This is a known
    handicapping-101 mistake, not a style choice.

    best_price is the single most generous price available — the row
    with the LOWEST implied probability among the books quoting that
    player. A plain numeric max on raw American-odds prices would get
    this wrong the moment negative prices show up mixed with positive
    ones (e.g. a heavily-favored goal-line back at -150 next to a
    long-shot teammate at +800) — comparing by implied probability
    handles the sign mix correctly by construction.
    """
    df = matched.copy()
    df["implied_probability"] = implied_probability(df["price"])

    best = (
        df.sort_values("implied_probability")
        .drop_duplicates(subset=["event_id", "player_id"], keep="first")[
            ["event_id", "player_id", "price", "book_title"]
        ]
        .rename(columns={"price": "best_price", "book_title": "best_book"})
    )

    group_cols = [
        "event_id", "commence_time", "home_team", "away_team",
        "player_id", "player_name_raw", "team", "position_group",
    ]
    consensus = (
        df.groupby(group_cols)
        .agg(n_books=("book_key", "nunique"), consensus_implied_probability=("implied_probability", "median"))
        .reset_index()
    )

    out = consensus.merge(best, on=["event_id", "player_id"], how="left")
    out["consensus_price_american"] = _probability_to_american(out["consensus_implied_probability"]).astype(int)
    return out


# --- Price-history storage: DESIGN ONLY. Nothing in this module writes
# to disk or polls the live API on a schedule — that's a future polling
# script, not built this round (building it now means starting to
# populate a table nothing reads yet, ahead of the trajectory scoring
# that would consume it).
#
# Follows nfl/'s existing flat-file convention (scripts/backfill_redzone.py
# writes player_redzone_weekly.csv), extended with backtest/data/'s
# raw-vs-processed split — the closest existing precedent for time-series
# storage in this codebase. There's no database anywhere in this project
# to follow instead (checked: no connection strings, no schema.sql, no
# ORM usage; the only Postgres/Supabase references are an unrelated
# downstream content-publishing system in pipeline/, not odds storage).
#
#   nfl/data/market_value/raw/{event_id}_{poll_timestamp}.json
#     the exact raw API response for one event's player_anytime_td
#     market at one poll, unmodified — lets a parsing bug found later be
#     reprocessed against real historical responses instead of needing to
#     have captured the "right" columns up front.
#
#   nfl/data/market_value/price_history.csv (append-only)
#     one row per player per poll — snapshot_scoring_inputs' output plus
#     a poll_timestamp and match-status columns. This is what trajectory/
#     momentum scoring will eventually read once enough polls have
#     accumulated; nothing reads it yet.

PRICE_HISTORY_COLUMNS = [
    "poll_timestamp",  # UTC ISO 8601, when this snapshot was pulled
    "event_id",  # The Odds API's event id
    "commence_time",  # game kickoff, UTC ISO 8601
    "season",  # nullable until resolved against a schedule join
    "week",  # nullable until resolved against a schedule join
    "home_team",
    "away_team",  # nflverse abbreviations
    "player_id",  # gsis id, nullable if unmatched
    "player_name_raw",  # as returned by The Odds API
    "team",  # nflverse abbreviation, nullable if unmatched
    "position_group",  # RB/WR/TE, nullable if unmatched
    "matched",  # bool
    "match_issue_type",  # nullable: "rookie_or_new" / "team_mismatch"
    "n_books",
    "best_price",
    "best_book",
    "consensus_implied_probability",
    "consensus_price_american",
]


def new_price_history_rows(
    matched_snapshot: pd.DataFrame, unmatched: pd.DataFrame, poll_timestamp: str, season: int,
) -> pd.DataFrame:
    """
    Build price-history rows (PRICE_HISTORY_COLUMNS schema) from one
    poll's snapshot_scoring_inputs() output plus match_attd_players'
    unmatched rows — matching failures get a row too (matched=False),
    not silently dropped from the record. Returns a DataFrame ready to
    append to nfl_price_history; does not write anything itself — the
    caller (nfl/api/index.py's poll-market-value endpoint) owns the
    actual write.

    `season` is a REQUIRED explicit param, not read off the input
    frames — this function has no way to know it otherwise (neither
    input frame carries a reliable season column of its own). A single
    call to this function is always scoped to exactly one season by its
    only real caller (poll_market_value_endpoint batches events via
    parsed_by_season before calling this per batch), so one scalar
    value is correct here, unlike `week`.

    `week` is DELIBERATELY NOT a parameter here, unlike `season` — a
    single poll_market_value_endpoint request can span multiple
    DIFFERENT weeks within the same season (rare, but real: a batch of
    events near a season boundary), so there is no single correct
    scalar value the way there is for season. Both `matched_snapshot`
    and `unmatched` are expected to already carry a real, resolved
    per-row `week` column BEFORE this function is called — mirroring
    poll_market_value_for_stub.py's own existing convention of
    attaching season/week onto its snapshot before this shaping step,
    just per-row instead of a single constant, since that caller's own
    week can genuinely vary row to row within one request. If a caller
    doesn't provide it, the column-fill loop below defaults it to None
    for every row — an honest "not resolved" rather than a crash, but
    real callers should resolve it (see poll_market_value_endpoint's
    own _week_lookup_for_season).

    CONFIRMED FIX, not a new design: season/week used to be
    unconditionally hardcoded to None here regardless of what the
    caller knew — the real bug scripts/reconcile_week.py's own live
    deployment crash surfaced (a downstream consumer needing to query
    nfl_price_history by (player_id, season, week) got zero real rows
    back, since every row had season=week=NULL by construction, not by
    missing data).
    """
    matched_out = matched_snapshot.astype(object).copy()
    matched_out["matched"] = True
    matched_out["match_issue_type"] = None

    unmatched_out = unmatched.astype(object).copy()
    unmatched_out["matched"] = False
    for col in ("player_id", "team", "position_group", "n_books", "best_price", "best_book",
                "consensus_implied_probability", "consensus_price_american"):
        unmatched_out[col] = None

    # Both frames are cast to object dtype first — with unmatched rows
    # genuinely having no player_id/team/etc. by design, several columns
    # are legitimately all-NA on one side of this concat, which pandas
    # warns about as a future dtype-inference change; casting to object
    # up front sidesteps the ambiguity entirely rather than fighting it.
    combined = pd.concat([matched_out, unmatched_out], ignore_index=True)
    combined["poll_timestamp"] = poll_timestamp
    combined["season"] = season
    # week: NOT reassigned here — see docstring. Whatever real per-row
    # value the caller already attached to matched_snapshot/unmatched
    # survives the concat above untouched; the fill loop below only
    # backstops a caller that genuinely never provided one at all.

    for col in PRICE_HISTORY_COLUMNS:
        if col not in combined.columns:
            combined[col] = None

    return combined[PRICE_HISTORY_COLUMNS]


DEFAULT_NFL_PRICE_HISTORY_READ_URL = "https://tastypickems.com/api/public/nfl-price-history-read"


def read_price_history(season: int, week: int, secret: str, read_url: str = None) -> dict:
    """
    One signed POST (body {"season": season, "week": week}), returns
    {"ok": bool, "error": str|None, "status_code": int|None, "rows":
    [...]} for EVERY real nfl_price_history row at (season, week) — same
    real sign+POST+capture-response reuse of forward_to_lovable every
    other read route in this codebase already uses (see curate_home_
    shelves.read_shelf_signal_history's own docstring for why that's a
    deliberate reuse, not a misuse).

    Returns the FULL real poll history for the week, not pre-reduced to
    "latest per player" — that reduction is scripts/reconcile_week.py's
    own job (see market_value_snapshot_for_reconciliation), matching the
    read route's own design (a future different caller might genuinely
    want the whole poll history, not just the latest).

    A real "zero rows" response (nfl_price_history has no polls yet for
    this week — the expected, current state until the Make.com polling
    scenario is built, a separately-tracked blocker, not this function's
    job) is a genuine, valid outcome (rows=[]), not an error — mirrors
    read_shelf_signal_history's own "no rows for this week is valid"
    convention exactly.
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent / "api"))
    from lovable_forward import forward_to_lovable, resolve_url_env

    url = read_url or resolve_url_env("LOVABLE_NFL_PRICE_HISTORY_READ_URL", DEFAULT_NFL_PRICE_HISTORY_READ_URL)
    result = forward_to_lovable({"season": season, "week": week}, secret, url)
    if not result["success"]:
        return {"ok": False, "error": result["error"], "status_code": result["status_code"], "rows": []}
    try:
        body = json.loads(result["response_body"])
    except (json.JSONDecodeError, TypeError):
        return {
            "ok": False, "error": f"non-JSON response body: {result['response_body']!r}",
            "status_code": result["status_code"], "rows": [],
        }
    return {"ok": True, "error": None, "status_code": result["status_code"], "rows": body.get("price_history") or []}


def latest_price_history_per_player(rows: list) -> pd.DataFrame:
    """
    Reduces read_price_history()'s real row list to one row per real
    player_id — whichever has the latest real poll_timestamp — the
    "latest poll on file" semantic reconcile_week()'s own docstring
    already documented and accepted for the stub-file mechanism this
    replaces (see that module's own "FINAL SNAPSHOT" note: "last poll on
    file," NOT "guaranteed last poll before kickoff" — unchanged by this
    swap of data source). Rows with a null player_id (an unmatched
    outcome — see new_price_history_rows) are dropped here: they carry
    no market_value_score-relevant identity to key on, same as they
    never had one in the old stub file either.

    Empty input -> an empty, correctly-shaped DataFrame (player_id/
    season/week/consensus_implied_probability/best_price columns), not
    an error — the real, expected state before the Make.com polling
    scenario has run for a given week (see read_price_history's own
    docstring).
    """
    cols = ["player_id", "season", "week", "poll_timestamp", "consensus_implied_probability", "best_price"]
    if not rows:
        return pd.DataFrame(columns=[c for c in cols if c != "poll_timestamp"])
    df = pd.DataFrame(rows)
    df = df[df["player_id"].notna()].copy()
    if len(df) == 0:
        return pd.DataFrame(columns=[c for c in cols if c != "poll_timestamp"])
    df["poll_timestamp"] = pd.to_datetime(df["poll_timestamp"])
    latest = (
        df.sort_values("poll_timestamp")
        .drop_duplicates(subset=["player_id"], keep="last")
    )
    return latest[[c for c in cols if c != "poll_timestamp"]].reset_index(drop=True)


def market_value_snapshot_for_reconciliation(season: int, week: int, secret: str, read_url: str = None) -> pd.DataFrame:
    """
    The real replacement for reconcile_week()'s old `stub[[player_id,
    season, week, market_value_score, market_value_completeness,
    consensus_implied_probability, best_price]]` read — same shape,
    different source. Reads the real latest-per-player nfl_price_history
    snapshot for (season, week), then runs scoring.score_market_value()
    (the REAL one, grouped by season/week — NOT this module's own unused
    score_market_value(snapshot) above, which has no completeness
    column and a different, single-snapshot reference population) on it
    — the exact same real function scripts/poll_market_value_for_stub.py
    already calls, computing market_value_score/market_value_completeness
    fresh, since neither is ever stored in nfl_price_history itself (see
    that module's own confirmed investigation: they're always computed
    on demand from consensus_implied_probability, never persisted raw).

    A genuinely empty snapshot (no real polls yet for this week) still
    returns a correctly-shaped, zero-row DataFrame with every expected
    column — reconcile_week()'s own left-merge against this produces
    honest NaN market_value_score/completeness for every player, the
    SAME graceful degradation the old stub-CSV path already had for any
    player missing from the stub (a left merge, not an inner one) —
    not a new behavior invented here, a preserved one.
    """
    from scoring import CONFIG, score_market_value as scoring_score_market_value

    result = read_price_history(season, week, secret, read_url)
    latest = latest_price_history_per_player(result["rows"])
    if len(latest) == 0:
        return pd.DataFrame(columns=[
            "player_id", "season", "week", "market_value_score", "market_value_completeness",
            "consensus_implied_probability", "best_price",
        ])
    latest["season"] = season
    latest["week"] = week
    scored = scoring_score_market_value(latest, CONFIG)
    return scored[[
        "player_id", "season", "week", "market_value_score", "market_value_completeness",
        "consensus_implied_probability", "best_price",
    ]]


def latest_price_history_full_per_player(rows: list) -> pd.DataFrame:
    """
    Same real "latest real poll per player" reduction as
    latest_price_history_per_player, but keeps every PRICE_HISTORY_COLUMNS
    column instead of narrowing to the 6 reconciliation needs. Built for
    Phase 3's generation need: build_market_intelligence_stories() needs a
    FULL-ROW snapshot (event_id, team names, book fields, etc.), not just
    the market-value/consensus numbers reconcile_week()'s merge step needs —
    a genuinely different consumer than latest_price_history_per_player was
    built for, confirmed by reading build_market_intelligence_stories()'s own
    body during the Phase 3 investigation (it references row['event_id'],
    row['away_team'], row['home_team'], row['commence_time'], row['n_books'],
    row['best_price']/['best_book'], row['consensus_price_american'], none of
    which the narrower reduction carries).

    Only real MATCHED rows are kept (player_id notna) — an unmatched row
    (see new_price_history_rows) carries no player identity at all, so it
    has nothing a story could be built around, same exclusion latest_price_
    history_per_player already applies for its own narrower purpose.

    Empty input -> an empty, correctly-shaped DataFrame (every real
    PRICE_HISTORY_COLUMNS column present), not an error — same "no polls
    yet" honest-degradation convention every other read-then-reduce
    wrapper in this codebase already has.
    """
    if not rows:
        return pd.DataFrame(columns=PRICE_HISTORY_COLUMNS)
    df = pd.DataFrame(rows)
    df = df[df["player_id"].notna()].copy()
    if len(df) == 0:
        return pd.DataFrame(columns=PRICE_HISTORY_COLUMNS)
    df["poll_timestamp"] = pd.to_datetime(df["poll_timestamp"])
    latest = (
        df.sort_values("poll_timestamp")
        .drop_duplicates(subset=["player_id"], keep="last")
    )
    for col in PRICE_HISTORY_COLUMNS:
        if col not in latest.columns:
            latest[col] = None
    return latest[PRICE_HISTORY_COLUMNS].reset_index(drop=True)


def market_intelligence_snapshot_for_generation(season: int, week: int, secret: str, read_url: str = None) -> pd.DataFrame:
    """
    The real input build_market_intelligence_stories() needs, sourced from
    storage instead of a live poll: reads the real latest-per-player nfl_
    price_history snapshot for (season, week) with EVERY real column intact
    (see latest_price_history_full_per_player), then runs scoring.
    score_market_value() (the REAL one, grouped by season/week) on it —
    the exact same real function scripts/poll_market_value_for_stub.py's
    own fetch_and_score_market_value already calls on a fresh live poll;
    this mirrors that same snapshot_scoring_inputs -> attach season/week ->
    score_market_value sequence, just reading the poll back from nfl_
    price_history instead of hitting The Odds API directly.

    A genuinely empty snapshot (no real polls yet for this week — the
    expected, current state until the Make.com polling scenario is built)
    still returns a correctly-shaped, zero-row DataFrame — build_market_
    intelligence_stories()'s own pool iteration already degrades correctly
    to "no stories" against a zero-row frame, not a new behavior invented
    here.
    """
    from scoring import CONFIG, score_market_value as scoring_score_market_value

    result = read_price_history(season, week, secret, read_url)
    latest = latest_price_history_full_per_player(result["rows"])
    if len(latest) == 0:
        cols = list(PRICE_HISTORY_COLUMNS) + ["market_value_score", "market_value_completeness"]
        return pd.DataFrame(columns=cols)
    return scoring_score_market_value(latest, CONFIG)


def score_market_value(snapshot: pd.DataFrame) -> pd.DataFrame:
    """
    Percentile-ranks consensus_implied_probability (snapshot_scoring_
    inputs' output) into market_value_score, 0-100, same direction
    convention as every other pillar: higher = more opportunity. No
    inversion needed — percentile_lookup already ranks "% of the
    population at or below this value," so a player the market rates as
    MORE likely to score anytime (a higher implied probability) lands at
    a HIGHER percentile automatically.

    Missing values (there aren't any by construction here — every row in
    `snapshot` came from a real quote — but kept for the same missing-
    data philosophy as everywhere else in this project, in case a caller
    passes a partially-null frame) fall back to neutral 50 via
    fill_neutral, not silently dropped or zeroed.

    REAL METHODOLOGICAL CAVEAT, not just a technical footnote: every
    other pillar's reference population is thousands of rows across three
    backfilled seasons. This one's reference population is whatever
    players happen to have a live quote in THIS poll — right now, a
    single game (~20 players). A percentile computed against 20 players
    in one game is a much noisier, more easily-skewed number than a
    percentile computed against a multi-season population, even though
    it's produced by the exact same mechanism. This isn't a bug to fix
    here; it's an inherent property of a live-snapshot-only pillar with
    no cross-game population to rank against yet, and will only really
    stabilize once several games' worth of live polls exist to pool
    together (still not "seasons" of data, just "more than one game").
    """
    out = snapshot.copy()
    scale = build_reference_scale(out["consensus_implied_probability"])
    raw_pct = percentile_lookup(out["consensus_implied_probability"], scale)
    out["market_value_score"] = fill_neutral(raw_pct).round(1)
    return out
