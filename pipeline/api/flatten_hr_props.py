"""
Flattens The Odds API's nested batter_home_runs response into a flat list
of rows Make.com can work with directly, instead of chaining Iterators.

Nesting being collapsed: event -> bookmakers[] -> markets[] (key ==
"batter_home_runs") -> outcomes[] -> one row per (player, bookmaker).

Filtering applied:
  - point == 0.5 only. The Odds API returns separate outcome rows for
    "at least 1 HR" (point 0.5), "at least 2 HR" (point 1.5), "at least 3
    HR" (point 2.5), etc. Only the 0.5 line is the product's target market.
  - name == "Over" only. Each point line has both an "Over" (player DOES
    hit that many HRs) and "Under" (player does NOT) outcome. The flat
    output schema here has no field to distinguish them, and the product
    is about picking players TO hit a home run, so this keeps only the
    "Over" side. This wasn't explicitly spelled out in the original ask —
    flagging it here since it's an easy default to change if the intent
    was actually to keep both sides (would need an added "bet_type" field).

This module is pure/stateless on purpose — no HTTP handling (a network
dependency lives one layer up, in lovable_forward.py) — so it can be
wrapped for whatever hosting choice gets picked later without changing the
logic here.

Defensive parsing, added after a real incident: a caller (Make.com, in
practice) can end up sending a nested field — `bookmakers`, `markets`, or
`outcomes` — as a JSON-encoded *string* instead of a real array, if
whatever built the request body stringified it along the way (a known trap
in no-code tools that build JSON bodies from text templates rather than a
structured mapper). Before this hardening, that produced a *silent* empty
result: `some_string.get(...)` would exist to call on nothing because the
for-loop over a list of characters never got that far — `bookmaker.get()`
on a lone character would actually raise, so in practice it depends on
exactly which level got stringified, but the failure modes range from
"crashes" to "silently returns nothing", and from the outside both looked
identical to "no HR props were available today." Every optional `diagnostics`
dict below exists specifically to tell those apart after the fact.
"""
import json

HR_MARKET_KEY = "batter_home_runs"
TARGET_POINT = 0.5
TARGET_OUTCOME_NAME = "Over"


def _bump(diagnostics, key, n=1):
    if diagnostics is not None:
        diagnostics[key] = diagnostics.get(key, 0) + n


def _as_list(value, diagnostics, recovered_key, wrong_type_key):
    """Returns value as-is if it's already a list. If it's a JSON string,
    tries to recover the real list from it. Otherwise reports the type and
    treats it as empty rather than crashing."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            _bump(diagnostics, wrong_type_key + "_unparseable_string")
            return []
        if isinstance(parsed, list):
            _bump(diagnostics, recovered_key)
            return parsed
        _bump(diagnostics, wrong_type_key, )
        return []
    _bump(diagnostics, wrong_type_key)
    return []


def flatten_hr_props(event: dict, diagnostics: dict = None) -> list:
    """
    Flatten one event's nested bookmakers/markets/outcomes into a flat list
    of HR prop rows: player_name, odds, bookmaker, game_id, home_team,
    away_team, commence_time. One row per (player, bookmaker).

    `diagnostics`, if passed a dict, gets counters merged into it describing
    what was actually found at each nesting level — see module docstring.
    """
    if not isinstance(event, dict):
        _bump(diagnostics, "events_not_dict")
        return []

    rows = []
    game_id = event.get("id")
    home_team = event.get("home_team")
    away_team = event.get("away_team")
    commence_time = event.get("commence_time")

    bookmakers = _as_list(event.get("bookmakers", []), diagnostics,
                           "bookmakers_recovered_from_string", "bookmakers_wrong_type")
    _bump(diagnostics, "bookmakers_seen", len(bookmakers))

    for bookmaker in bookmakers:
        if not isinstance(bookmaker, dict):
            _bump(diagnostics, "bookmakers_not_dict")
            continue
        book_name = bookmaker.get("title")

        markets = _as_list(bookmaker.get("markets", []), diagnostics,
                            "markets_recovered_from_string", "markets_wrong_type")

        for market in markets:
            if not isinstance(market, dict) or market.get("key") != HR_MARKET_KEY:
                continue
            _bump(diagnostics, "hr_markets_seen")

            outcomes = _as_list(market.get("outcomes", []), diagnostics,
                                 "outcomes_recovered_from_string", "outcomes_wrong_type")

            for outcome in outcomes:
                if not isinstance(outcome, dict):
                    continue
                _bump(diagnostics, "outcomes_seen")
                if outcome.get("point") != TARGET_POINT:
                    continue
                if outcome.get("name") != TARGET_OUTCOME_NAME:
                    continue
                _bump(diagnostics, "outcomes_matching_filter")

                rows.append({
                    "player_name": outcome.get("description"),
                    "odds": outcome.get("price"),
                    "bookmaker": book_name,
                    "game_id": game_id,
                    "home_team": home_team,
                    "away_team": away_team,
                    "commence_time": commence_time,
                })

    return rows


def flatten_hr_props_batch(events, diagnostics: dict = None) -> list:
    """
    Apply flatten_hr_props across a list of events, one flat result list.
    `events` is typed loosely on purpose — see the string-recovery handling
    below, which exists because it's arrived as a JSON string in practice.
    """
    if isinstance(events, str):
        try:
            events = json.loads(events)
            _bump(diagnostics, "events_list_recovered_from_string")
        except (json.JSONDecodeError, TypeError):
            _bump(diagnostics, "events_list_unparseable_string")
            return []

    if not isinstance(events, list):
        _bump(diagnostics, "events_list_wrong_type")
        return []

    all_rows = []
    for event in events:
        _bump(diagnostics, "events_processed")
        all_rows.extend(flatten_hr_props(event, diagnostics=diagnostics))
    return all_rows
