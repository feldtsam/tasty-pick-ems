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

This module is pure/stateless on purpose — no HTTP handling, no
dependencies beyond the standard library — so it can be wrapped for
whatever hosting choice gets picked later without changing the logic here.
"""

HR_MARKET_KEY = "batter_home_runs"
TARGET_POINT = 0.5
TARGET_OUTCOME_NAME = "Over"


def flatten_hr_props(event: dict) -> list:
    """
    Flatten one event's nested bookmakers/markets/outcomes into a flat list
    of HR prop rows: player_name, odds, bookmaker, game_id, home_team,
    away_team, commence_time. One row per (player, bookmaker).
    """
    rows = []
    game_id = event.get("id")
    home_team = event.get("home_team")
    away_team = event.get("away_team")
    commence_time = event.get("commence_time")

    for bookmaker in event.get("bookmakers", []):
        book_name = bookmaker.get("title")

        for market in bookmaker.get("markets", []):
            if market.get("key") != HR_MARKET_KEY:
                continue

            for outcome in market.get("outcomes", []):
                if outcome.get("point") != TARGET_POINT:
                    continue
                if outcome.get("name") != TARGET_OUTCOME_NAME:
                    continue

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


def flatten_hr_props_batch(events: list) -> list:
    """Apply flatten_hr_props across a list of events, one flat result list."""
    all_rows = []
    for event in events:
        all_rows.extend(flatten_hr_props(event))
    return all_rows
