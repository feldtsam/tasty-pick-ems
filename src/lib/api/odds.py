# ============================================================
# src/lib/api/odds.py
#
# The Odds API integration — requires an API key.
# Get your free key at: https://the-odds-api.com
# Free tier: ~500 requests/month, game-level odds only.
# ⚠️  Player props (HR lines) require a PAID plan.
#
# What this module does:
#   1. Fetches MLB events from The Odds API
#   2. For each event, fetches batter HR prop lines
#   3. Filters to only return lines at +300 or longer
#   4. Normalizes into PropOdds objects
#   5. Falls back to mock data if key is missing or API fails
# ============================================================

import os
import requests
from typing import List, Optional
from .types import PropOdds

# ── API config ────────────────────────────────────────────────
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
SPORT_KEY     = "baseball_mlb"       # The Odds API's key for MLB
REGION        = "us"                 # Use US bookmakers
ODDS_FORMAT   = "american"           # +340 format (not decimal)

# The market key for batter home run props
# ⚠️  This market requires a paid plan.
HR_MARKET = "batter_home_runs"

# Only surface props at this odds threshold or longer
MIN_ODDS = 300  # +300 minimum

# ── Mock fallback data ─────────────────────────────────────────
MOCK_PROPS: List[PropOdds] = [
    PropOdds(player_name="Aaron Judge",    team="NYY", odds=340, bookmaker="Mock Data"),
    PropOdds(player_name="Kyle Schwarber", team="PHI", odds=380, bookmaker="Mock Data"),
    PropOdds(player_name="Pete Alonso",    team="NYM", odds=420, bookmaker="Mock Data"),
    PropOdds(player_name="Yordan Alvarez", team="HOU", odds=310, bookmaker="Mock Data"),
    PropOdds(player_name="Adolis Garcia",  team="TEX", odds=450, bookmaker="Mock Data"),
]


# ── Main function ──────────────────────────────────────────────

def fetch_hr_props() -> List[PropOdds]:
    """
    Fetch today's MLB HR prop lines at +300 or longer.
    Returns mock data if ODDS_API_KEY is not set or API fails.

    Usage:
        from src.lib.api.odds import fetch_hr_props
        props = fetch_hr_props()
    """
    api_key = os.getenv("ODDS_API_KEY")

    # If no key is configured, use mock data and explain why.
    if not api_key:
        print("[Odds API] No ODDS_API_KEY found in environment. Using mock data.")
        print("[Odds API] Get a free key at https://the-odds-api.com and add it to .env")
        return MOCK_PROPS

    try:
        props = _fetch_from_api(api_key)
        print(f"[Odds API] Fetched {len(props)} HR props at +{MIN_ODDS} or longer.")
        return props

    except Exception as e:
        print(f"[Odds API] Error: {e}. Falling back to mock data.")
        return MOCK_PROPS


def _fetch_from_api(api_key: str) -> List[PropOdds]:
    """
    Internal: fetch events from The Odds API, then get HR props for each.
    """
    # Step 1: Get a list of today's MLB events (game IDs)
    events = _fetch_events(api_key)
    if not events:
        return []

    # Step 2: For each event, fetch HR prop lines
    all_props: List[PropOdds] = []
    for event in events:
        event_props = _fetch_event_props(api_key, event["id"])
        all_props.extend(event_props)

    # Step 3: Filter to +MIN_ODDS or longer
    return [p for p in all_props if p.odds >= MIN_ODDS]


def _fetch_events(api_key: str) -> list:
    """
    Returns a list of raw event dicts: [{"id": "...", "home_team": "...", ...}]
    """
    url = f"{ODDS_API_BASE}/sports/{SPORT_KEY}/events"
    response = requests.get(url, params={"apiKey": api_key}, timeout=10)
    response.raise_for_status()
    return response.json()


def _fetch_event_props(api_key: str, event_id: str) -> List[PropOdds]:
    """
    Fetch HR prop lines for a single event.
    ⚠️  Requires a paid Odds API plan. Returns [] on a free plan.
    """
    url = f"{ODDS_API_BASE}/sports/{SPORT_KEY}/events/{event_id}/odds"
    params = {
        "apiKey":      api_key,
        "regions":     REGION,
        "markets":     HR_MARKET,
        "oddsFormat":  ODDS_FORMAT,
    }

    try:
        response = requests.get(url, params=params, timeout=10)

        # 422 = market not available on your plan
        if response.status_code == 422:
            print(f"[Odds API] HR props require a paid plan (event {event_id}).")
            return []

        response.raise_for_status()
        return _normalize_props(response.json())

    except requests.HTTPError as e:
        print(f"[Odds API] HTTP error for event {event_id}: {e}")
        return []


def _normalize_props(raw: dict) -> List[PropOdds]:
    """
    Convert raw Odds API response into PropOdds objects.

    The API structure for player props:
    {
        "bookmakers": [{
            "title": "DraftKings",
            "markets": [{
                "key": "batter_home_runs",
                "outcomes": [{
                    "name": "Aaron Judge",     # player name
                    "description": "NYY",      # team abbrev
                    "price": 340              # American odds (positive = underdog)
                }]
            }]
        }]
    }
    """
    props: List[PropOdds] = []

    for bookmaker in raw.get("bookmakers", []):
        book_name = bookmaker.get("title", "Unknown")

        for market in bookmaker.get("markets", []):
            if market.get("key") != HR_MARKET:
                continue

            for outcome in market.get("outcomes", []):
                price = outcome.get("price", 0)

                # Skip negative odds (favorites) and anything under MIN_ODDS
                if price < MIN_ODDS:
                    continue

                props.append(PropOdds(
                    player_name=outcome.get("name", "Unknown"),
                    team=outcome.get("description", ""),
                    odds=int(price),
                    bookmaker=book_name,
                ))

    return props
