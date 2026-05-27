#!/usr/bin/env python3
# ============================================================
# server.py — Tasty Pick Ems Backend
#
# This is the Flask web server that:
#   1. Calls the MLB, Odds, and Weather APIs (or uses mock data)
#   2. Exposes clean JSON endpoints your dashboard fetches from
#   3. Keeps all API keys server-side (never exposed to the browser)
#
# Start it with:   ./start.sh   (or: python3 server.py)
# Default port:    http://localhost:5001
# ============================================================

import os
import sys
from datetime import datetime, timezone
from typing import List, Optional
from dataclasses import asdict

# Add project root to Python path so src.lib imports work
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

# Load .env file if it exists (safe to call even if .env doesn't exist)
load_dotenv()

# Import our API modules
from src.lib.api.mlb     import fetch_todays_games
from src.lib.api.odds    import fetch_hr_props
from src.lib.api.weather import fetch_weather_for_venue, get_park_factor
from src.lib.scoring.hr_score import calculate_hr_score

# ── Flask setup ───────────────────────────────────────────────
app = Flask(__name__)

# Allow requests from the frontend (localhost:5174 is our file server)
CORS(app, origins=["http://localhost:5174", "http://127.0.0.1:5174", "null"])

PORT = int(os.getenv("PORT", 5001))


# ── Simple in-memory cache ────────────────────────────────────
# Avoids hammering APIs on every page refresh.
# Cache expires after CACHE_TTL_SECONDS.
_cache = {}
CACHE_TTL_SECONDS = 300  # 5 minutes


def _is_cache_fresh(key: str) -> bool:
    if key not in _cache:
        return False
    age = (datetime.now(timezone.utc) - _cache[key]["timestamp"]).total_seconds()
    return age < CACHE_TTL_SECONDS


def _set_cache(key: str, data):
    _cache[key] = {"data": data, "timestamp": datetime.now(timezone.utc)}


# ── Routes ────────────────────────────────────────────────────

@app.route("/api/health")
def health():
    """
    Quick health check — use this to confirm the backend is running.
    Visit http://localhost:5001/api/health in your browser.
    """
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "apis": {
            "mlb":     "free — no key needed",
            "odds":    "key set" if os.getenv("ODDS_API_KEY") else "missing — using mock data",
            "weather": "free — Open-Meteo, no key needed",
        }
    })


@app.route("/api/games")
def games():
    """
    Returns today's MLB games.
    Example: GET http://localhost:5001/api/games
    """
    if _is_cache_fresh("games"):
        return jsonify(_cache["games"]["data"])

    game_list = fetch_todays_games()
    result = [asdict(g) for g in game_list]
    _set_cache("games", result)
    return jsonify(result)


@app.route("/api/props")
def props():
    """
    Returns available HR props at +300 or longer.
    Example: GET http://localhost:5001/api/props
    """
    if _is_cache_fresh("props"):
        return jsonify(_cache["props"]["data"])

    prop_list = fetch_hr_props()
    result = [asdict(p) for p in prop_list]
    _set_cache("props", result)
    return jsonify(result)


@app.route("/api/weather/<path:venue>")
def weather(venue: str):
    """
    Returns weather for a specific ballpark.
    Example: GET http://localhost:5001/api/weather/Coors Field
    """
    impact = fetch_weather_for_venue(venue)
    if impact is None:
        return jsonify({"error": f"No weather data for '{venue}' (may be indoor or unknown)"}), 404
    return jsonify(asdict(impact))


@app.route("/api/live")
def live():
    """
    Main endpoint — returns everything the Live Data page needs in one call:
      - Today's games with weather + HR environment score
      - Available HR props
      - Which data sources are live vs. mock

    Example: GET http://localhost:5001/api/live
    """
    if _is_cache_fresh("live"):
        return jsonify(_cache["live"]["data"])

    # Fetch from all three sources
    games     = fetch_todays_games()
    hr_props  = fetch_hr_props()

    # Figure out which APIs have real keys vs. mock
    using_mock = {
        "mlb":     False,  # MLB is always live — no key needed
        "odds":    not bool(os.getenv("ODDS_API_KEY")),
        "weather": False,  # Open-Meteo — always live, no key needed
    }

    # Build per-game data packets
    game_packets = []
    for game in games:
        # Get weather (None for indoor parks)
        weather_data = fetch_weather_for_venue(game.venue) if game.is_outdoor else None

        # Get park factor from our ballpark map
        park_factor = get_park_factor(game.venue)

        # Find HR props for this game
        game_props = _match_props_to_game(hr_props, game.home_team, game.away_team)

        # Calculate HR environment score
        best_odds = min((p.odds for p in game_props), default=None)
        score = calculate_hr_score(
            venue=game.venue,
            park_factor=park_factor,
            weather=weather_data,
            best_prop_odds=best_odds,
        )

        game_packets.append({
            "game":    asdict(game),
            "weather": asdict(weather_data) if weather_data else None,
            "props":   [asdict(p) for p in game_props],
            "score":   asdict(score),
        })

    # Sort games by score (best HR environment first)
    game_packets.sort(key=lambda x: x["score"]["total"], reverse=True)

    result = {
        "date":       datetime.now().strftime("%Y-%m-%d"),
        "games":      game_packets,
        "all_props":  [asdict(p) for p in hr_props],
        "using_mock": using_mock,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }

    _set_cache("live", result)
    return jsonify(result)


# ── Helper functions ──────────────────────────────────────────

def _match_props_to_game(props, home_team: str, away_team: str):
    """
    Try to match HR props to a game by team abbreviation.
    This is imperfect since The Odds API uses abbreviations and MLB uses full names.
    A production version would use game_id matching across APIs.
    """
    # Build a rough map of team name keywords to abbreviations
    name_to_abbrev = {
        "Yankees": "NYY", "Mets": "NYM", "Red Sox": "BOS",
        "Cubs": "CHC", "White Sox": "CHW", "Astros": "HOU",
        "Athletics": "OAK", "Dodgers": "LAD", "Angels": "LAA",
        "Giants": "SF", "Padres": "SD", "Mariners": "SEA",
        "Rangers": "TEX", "Phillies": "PHI", "Braves": "ATL",
        "Cardinals": "STL", "Reds": "CIN", "Pirates": "PIT",
        "Rockies": "COL", "Diamondbacks": "ARI", "Brewers": "MIL",
        "Twins": "MIN", "Tigers": "DET", "Indians": "CLE",
        "Royals": "KC", "Guardians": "CLE", "Orioles": "BAL",
        "Blue Jays": "TOR", "Rays": "TB", "Marlins": "MIA",
        "Nationals": "WSH",
    }

    # Find abbreviations for the home and away teams
    home_abbrev = next((v for k, v in name_to_abbrev.items() if k in home_team), None)
    away_abbrev = next((v for k, v in name_to_abbrev.items() if k in away_team), None)

    matched = []
    for prop in props:
        if prop.team in (home_abbrev, away_abbrev):
            matched.append(prop)

    return matched


# ── Entry point ───────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 55)
    print("  TASTY PICK EMS — Backend Server")
    print("=" * 55)
    print(f"  Running at: http://localhost:{PORT}")
    print(f"  Health:     http://localhost:{PORT}/api/health")
    print(f"  Live data:  http://localhost:{PORT}/api/live")
    print()
    print("  API key status:")
    print(f"    MLB Stats:  ✓ free, no key needed")
    print(f"    Odds API:   {'✓ key found' if os.getenv('ODDS_API_KEY') else '✗ missing — using mock data'}")
    print(f"    Weather:    ✓ Open-Meteo (free, no key needed)")
    print()
    print("  Press Ctrl+C to stop.")
    print("=" * 55)

    # use_reloader=False prevents Flask from trying to spawn Python 3.7
    # (which is broken on this machine). Save and restart manually if you edit server.py.
    app.run(host="0.0.0.0", port=PORT, debug=True, use_reloader=False)
