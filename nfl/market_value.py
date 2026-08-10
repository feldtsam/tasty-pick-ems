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
"""
import numpy as np
import pandas as pd

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
    position_group, team) via seasonal_rosters — the same source
    redzone._position_lookup already uses for clean RB/WR/TE labels —
    constrained to the two teams actually playing in that event. The
    outcome itself carries no team field, so the event's home_team/
    away_team (full names, e.g. "Seattle Seahawks") are resolved to
    nflverse abbreviations via import_team_desc() first. Restricting the
    candidate pool to the two rosters in play also helps resolve name
    collisions (e.g. two different real "Josh Allen"s in the league) that
    a global name match wouldn't.

    Uses a plain per-row loop rather than a vectorized merge, unlike the
    rest of this codebase — deliberately: this only ever processes tens
    of rows per event (one game's ATTD market has ~20-30 outcomes), so
    the performance cost is zero, and the row-wise team-constrained
    matching logic here is meaningfully easier to verify correct than the
    equivalent merge-and-filter would be.

    RB/WR/TE only, matching redzone._position_lookup's own restriction —
    Market Value's position_group has to share that vocabulary with TD
    Opportunity/Role & Momentum/Situation, all scoped to RB/WR/TE.

    Returns (matched, unmatched) — unmatched rows are never dropped
    silently. Each unmatched row gets a heuristic match_issue_type:
      "rookie_or_new"        - no exact name match anywhere in
                                seasonal_rosters (any position, any team,
                                any season) — most likely a player with no
                                backfilled season history yet (e.g. a
                                rookie who hasn't played a tracked
                                season). Can't be proven without external
                                data; this is the best available
                                heuristic, not a certainty.
      "position_out_of_scope" - a real, matchable player (QB, OL, etc.),
                                correctly excluded for being outside
                                RB/WR/TE — not a bug, the same scope every
                                other pillar in this module already has.
      "team_mismatch"         - a real RB/WR/TE, just not on either team
                                playing in this game (traded, a team-
                                abbreviation resolution issue, or a
                                genuine name collision) — worth a human
                                look, more likely a real issue than
                                expected coverage noise.
    D/ST rows are excluded from both outputs entirely (see
    _is_dst_outcome) — they're a known, explicitly-handled category, not
    a matching failure of any kind.
    """
    parsed = parsed[~parsed["is_dst"]].copy()

    name_to_abbr = dict(zip(team_desc["team_name"], team_desc["team_abbr"]))
    parsed["home_abbr"] = parsed["home_team"].map(name_to_abbr)
    parsed["away_abbr"] = parsed["away_team"].map(name_to_abbr)

    season_rosters = seasonal_rosters[seasonal_rosters["season"] == season]
    # RB/WR/TE only, same restriction as redzone._position_lookup — this
    # module's position_group has to share that vocabulary, since Market
    # Value's output is meant to sit alongside TD Opportunity/Role &
    # Momentum/Situation, all of which are scoped to RB/WR/TE.
    skill_position_rosters = season_rosters[season_rosters["position"].isin(["RB", "WR", "TE"])]
    roster_lookup = skill_position_rosters[["player_id", "player_name", "position", "team"]]

    all_names_ever = set(seasonal_rosters["player_name"].dropna())
    skill_position_names_ever = set(
        seasonal_rosters.loc[seasonal_rosters["position"].isin(["RB", "WR", "TE"]), "player_name"].dropna()
    )

    matched_rows = []
    unmatched_rows = []
    for row in parsed.to_dict("records"):
        candidates = roster_lookup[roster_lookup["player_name"] == row["player_name_raw"]]
        in_game = candidates[candidates["team"].isin([row["home_abbr"], row["away_abbr"]])]

        if len(in_game) >= 1:
            m = in_game.iloc[0]
            matched_rows.append(
                {**row, "player_id": m["player_id"], "team": m["team"], "position_group": m["position"]}
            )
        elif row["player_name_raw"] in skill_position_names_ever:
            # A real RB/WR/TE, just not exact-matchable to a team playing
            # in this game (traded, an abbreviation resolution issue, or
            # a genuine name collision) — a real issue worth a look, not
            # expected coverage noise.
            unmatched_rows.append({**row, "match_issue_type": "team_mismatch"})
        elif row["player_name_raw"] in all_names_ever:
            # A real player (QB, OL, etc.) correctly out of scope for a
            # system built around RB/WR/TE red-zone touches — not a bug,
            # matches the scope every other pillar in this module already
            # has (e.g. QBs are excluded from redzone._position_lookup
            # the same way).
            unmatched_rows.append({**row, "match_issue_type": "position_out_of_scope"})
        else:
            unmatched_rows.append({**row, "match_issue_type": "rookie_or_new"})

    drop_cols = ["home_abbr", "away_abbr"]
    matched = pd.DataFrame(matched_rows).drop(columns=drop_cols, errors="ignore")
    unmatched = pd.DataFrame(unmatched_rows).drop(columns=drop_cols, errors="ignore")
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
    matched_snapshot: pd.DataFrame, unmatched: pd.DataFrame, poll_timestamp: str
) -> pd.DataFrame:
    """
    Build price-history rows (PRICE_HISTORY_COLUMNS schema) from one
    poll's snapshot_scoring_inputs() output plus match_attd_players'
    unmatched rows — matching failures get a row too (matched=False),
    not silently dropped from the record. Returns a DataFrame ready to
    append to nfl/data/market_value/price_history.csv; does not write
    anything itself — the actual polling script (not built this round)
    owns when/whether to append.
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
    combined["season"] = None
    combined["week"] = None

    for col in PRICE_HISTORY_COLUMNS:
        if col not in combined.columns:
            combined[col] = None

    return combined[PRICE_HISTORY_COLUMNS]
