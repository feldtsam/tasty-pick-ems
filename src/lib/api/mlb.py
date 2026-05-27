# ============================================================
# src/lib/api/mlb.py
#
# MLB Stats API integration — completely FREE, no key required.
# Official docs: https://statsapi.mlb.com/docs/
#
# What this module does:
#   1. Fetches today's MLB schedule
#   2. Pulls game IDs, teams, start time, venue, probable pitchers
#   3. Normalizes the messy API response into clean Game objects
#   4. Falls back to mock data if the API is unreachable
# ============================================================

import requests
from datetime import date
from typing import List
from .types import Game

# ── API config ────────────────────────────────────────────────
# No API key needed — MLB Stats is a public API.
MLB_BASE_URL = "https://statsapi.mlb.com/api/v1"

# These parks have roofs — weather doesn't affect them.
INDOOR_VENUES = {
    "Globe Life Field",
    "Minute Maid Park",
    "Chase Field",
    "loanDepot park",
    "Tropicana Field",
    "Rogers Centre",
    "American Family Field",
}

# ── Mock fallback data ─────────────────────────────────────────
# Used if the MLB API is down or returns an unexpected response.
MOCK_GAMES: List[Game] = [
    Game(
        game_id=1001,
        home_team="Oakland Athletics",
        away_team="Houston Astros",
        start_time="2026-05-22T17:05:00Z",
        venue="Oakland Coliseum",
        home_pitcher="Luis Severino",
        away_pitcher="JP Sears",
        is_outdoor=True,
    ),
    Game(
        game_id=1002,
        home_team="New York Yankees",
        away_team="Seattle Mariners",
        start_time="2026-05-22T18:05:00Z",
        venue="Yankee Stadium",
        home_pitcher="Gerrit Cole",
        away_pitcher="Logan Gilbert",
        is_outdoor=True,
    ),
    Game(
        game_id=1003,
        home_team="Colorado Rockies",
        away_team="Chicago Cubs",
        start_time="2026-05-22T20:10:00Z",
        venue="Coors Field",
        home_pitcher="Kyle Freeland",
        away_pitcher="Wade Miley",
        is_outdoor=True,
    ),
    Game(
        game_id=1004,
        home_team="Philadelphia Phillies",
        away_team="Cincinnati Reds",
        start_time="2026-05-22T18:05:00Z",
        venue="Citizens Bank Park",
        home_pitcher="Zack Wheeler",
        away_pitcher="Hunter Greene",
        is_outdoor=True,
    ),
]


# ── Main function ──────────────────────────────────────────────

def fetch_todays_games() -> List[Game]:
    """
    Fetch today's MLB schedule and return a list of Game objects.
    Falls back to MOCK_GAMES if anything goes wrong.

    Usage:
        from src.lib.api.mlb import fetch_todays_games
        games = fetch_todays_games()
    """
    today = date.today().strftime("%Y-%m-%d")

    try:
        games = _fetch_from_api(today)
        print(f"[MLB API] Fetched {len(games)} games for {today}")
        return games

    except Exception as e:
        # Log the error but don't crash — return mock data instead.
        print(f"[MLB API] Error: {e}. Falling back to mock data.")
        return MOCK_GAMES


def _fetch_from_api(date_str: str) -> List[Game]:
    """
    Internal: actually calls the MLB API and normalizes the response.
    Raises an exception if anything goes wrong (caught by fetch_todays_games).
    """
    url = f"{MLB_BASE_URL}/schedule"
    params = {
        "sportId": 1,          # 1 = MLB (not MiLB, not international)
        "date": date_str,
        # "hydrate" tells the API to include extra data in one call
        "hydrate": "probablePitcher,venue,team",
    }

    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()  # Throws if HTTP status is 4xx or 5xx

    data = response.json()
    games = []

    # The API wraps results in a "dates" array (one entry per date requested)
    for date_entry in data.get("dates", []):
        for raw_game in date_entry.get("games", []):
            game = _normalize_game(raw_game)
            if game:
                games.append(game)

    return games


def _normalize_game(raw: dict) -> Game:
    """
    Convert a single raw API game dict into our clean Game object.
    Returns None if essential fields are missing.
    """
    try:
        venue = raw.get("venue", {}).get("name", "Unknown Venue")

        home = raw.get("teams", {}).get("home", {})
        away = raw.get("teams", {}).get("away", {})

        # Probable pitchers may not exist yet (e.g. TBD)
        home_pitcher = home.get("probablePitcher", {}).get("fullName")
        away_pitcher = away.get("probablePitcher", {}).get("fullName")

        return Game(
            game_id=raw["gamePk"],
            home_team=home.get("team", {}).get("name", "Unknown"),
            away_team=away.get("team", {}).get("name", "Unknown"),
            start_time=raw.get("gameDate", ""),
            venue=venue,
            home_pitcher=home_pitcher,
            away_pitcher=away_pitcher,
            is_outdoor=venue not in INDOOR_VENUES,
        )

    except (KeyError, TypeError) as e:
        print(f"[MLB API] Could not normalize game: {e}")
        return None
