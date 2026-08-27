"""
Debug tool: fetches real, live batter_home_runs odds from The Odds API for
today's slate and inspects the RAW bookmakers array, before any of this
pipeline's flattening/matching code touches it, to answer one question:
does a duplicate bookmaker (e.g. two "caesars" entries with different
prices for the same player) exist in the raw API response itself, or does
it get introduced later in flatten_hr_props.py / scored_picks.py?

For every event pulled, groups every (player, bookmaker) pairing found
across ALL markets/outcomes matching this pipeline's own filter (point ==
0.5, name == "Over" — see flatten_hr_props.py) and reports, per player:
  - how many raw bookmaker KEYS appear more than once at the top-level
    `bookmakers` array (the same book listed twice by The Odds API itself)
  - how many bookmakers have more than one qualifying market with
    key == "batter_home_runs"
  - how many bookmakers have more than one qualifying outcome (point==0.5,
    name=="Over") within a single matching market

Prints the full raw bookmakers array for the first player found with ANY
duplication, or for the first player overall if none is found, so there's
always concrete raw JSON to look at either way.

Run: python3 pipeline/scripts/debug_duplicate_bookmaker.py [EVENT_INDEX]
(EVENT_INDEX optional, defaults to scanning every live event today)
"""
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))

import requests  # noqa: E402

HR_MARKET_KEY = "batter_home_runs"
TARGET_POINT = 0.5
TARGET_OUTCOME_NAME = "Over"

ODDS_EVENTS_URL = "https://api.the-odds-api.com/v4/sports/baseball_mlb/events/"
ODDS_EVENT_ODDS_URL = "https://api.the-odds-api.com/v4/sports/baseball_mlb/events/{event_id}/odds"


def _load_env_key() -> str:
    key = os.environ.get("ODDS_API_KEY")
    if key:
        return key
    # Fall back to reading the repo-root .env directly -- this script is
    # meant to be runnable standalone without requiring the caller to have
    # already sourced it, same convenience build_shelf_test_pool.py's own
    # usage note assumes but doesn't actually implement.
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("ODDS_API_KEY="):
                return line.split("=", 1)[1].strip()
    print("ODDS_API_KEY not set in the environment and not found in repo-root .env", file=sys.stderr)
    raise SystemExit(1)


def inspect_event(event_odds: dict) -> None:
    event_id = event_odds.get("id")
    matchup = f"{event_odds.get('away_team')} @ {event_odds.get('home_team')}"
    bookmakers = event_odds.get("bookmakers", [])

    print(f"\n{'=' * 70}\nEVENT {event_id}: {matchup}")
    print(f"  raw bookmakers array length: {len(bookmakers)}")

    # top-level key duplication check
    top_level_keys = [b.get("key") for b in bookmakers if isinstance(b, dict)]
    top_level_dupes = {k for k in top_level_keys if top_level_keys.count(k) > 1}
    if top_level_dupes:
        print(f"  !! DUPLICATE bookmaker KEYS at the top level: {sorted(top_level_dupes)}")
    else:
        print("  no duplicate bookmaker keys at the top level (one entry per book)")

    # player -> bookmaker_key -> list of (market_key, price) for every
    # qualifying (point==0.5, name=="Over") outcome found, across every
    # market on every bookmaker -- mirrors flatten_hr_props.py's own
    # filter exactly, so this reflects what actually reaches our code.
    per_player = defaultdict(lambda: defaultdict(list))

    for bookmaker in bookmakers:
        if not isinstance(bookmaker, dict):
            continue
        book_key = bookmaker.get("key")
        markets = bookmaker.get("markets", [])
        hr_markets = [m for m in markets if isinstance(m, dict) and m.get("key") == HR_MARKET_KEY]
        for market in hr_markets:
            for outcome in market.get("outcomes", []):
                if not isinstance(outcome, dict):
                    continue
                if outcome.get("point") != TARGET_POINT or outcome.get("name") != TARGET_OUTCOME_NAME:
                    continue
                player = outcome.get("description")
                per_player[player][book_key].append({
                    "market_key": market.get("key"),
                    "market_last_update": market.get("last_update"),
                    "price": outcome.get("price"),
                })

    # find any player with more than one raw entry under the same bookmaker key
    dupe_players = {
        player: books for player, books in per_player.items()
        if any(len(entries) > 1 for entries in books.values())
    }

    print(f"  players with a qualifying HR-Over-0.5 quote: {len(per_player)}")
    print(f"  players with a DUPLICATE bookmaker (same key, >1 quote): {len(dupe_players)}")

    if dupe_players:
        player, books = next(iter(dupe_players.items()))
        print(f"\n  --- showing the FULL raw bookmakers array for a player WITH a duplicate: {player!r} ---")
    else:
        player = next(iter(per_player), None)
        if player is None:
            print("  (no qualifying outcomes at all in this event -- nothing to show)")
            return
        books = per_player[player]
        print(f"\n  --- no duplicates found this event; showing the raw array for player: {player!r} ---")

    for book_key, entries in books.items():
        tag = " <== DUPLICATE" if len(entries) > 1 else ""
        print(f"    bookmaker={book_key!r}{tag}")
        for e in entries:
            print(f"      market={e['market_key']!r} last_update={e['market_last_update']!r} price={e['price']}")

    # full raw JSON for that one player's bookmaker entries, straight from
    # the API response, completely unprocessed -- exactly what was asked for
    raw_for_player = []
    for bookmaker in bookmakers:
        if not isinstance(bookmaker, dict):
            continue
        for market in bookmaker.get("markets", []):
            if not isinstance(market, dict) or market.get("key") != HR_MARKET_KEY:
                continue
            for outcome in market.get("outcomes", []):
                if isinstance(outcome, dict) and outcome.get("description") == player:
                    raw_for_player.append({
                        "bookmaker_key": bookmaker.get("key"),
                        "bookmaker_title": bookmaker.get("title"),
                        "bookmaker_last_update": bookmaker.get("last_update"),
                        "market_key": market.get("key"),
                        "market_last_update": market.get("last_update"),
                        "outcome": outcome,
                    })
    print(f"\n  raw JSON for {player!r} (every matching outcome, every bookmaker, unfiltered):")
    print(json.dumps(raw_for_player, indent=2))


def main(event_index: int | None):
    api_key = _load_env_key()

    events_resp = requests.get(ODDS_EVENTS_URL, params={"apiKey": api_key}, timeout=15)
    events_resp.raise_for_status()
    events = events_resp.json()

    if not events:
        print("No live/upcoming MLB events returned by The Odds API right now.")
        return

    targets = [events[event_index]] if event_index is not None else events

    for event in targets:
        odds_resp = requests.get(
            ODDS_EVENT_ODDS_URL.format(event_id=event["id"]),
            params={"apiKey": api_key, "regions": "us", "markets": HR_MARKET_KEY, "oddsFormat": "american"},
            timeout=15,
        )
        odds_resp.raise_for_status()
        inspect_event(odds_resp.json())


if __name__ == "__main__":
    idx = int(sys.argv[1]) if len(sys.argv) > 1 else None
    main(idx)
