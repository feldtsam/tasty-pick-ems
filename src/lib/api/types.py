# ============================================================
# src/lib/api/types.py
#
# All shared data shapes live here.
# These are the Python equivalent of TypeScript interfaces —
# plain dataclasses that every module imports and returns.
# ============================================================

from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class Game:
    """
    A single MLB game happening today.
    Produced by mlb.py and used by the scoring + live-data route.
    """
    game_id: int          # MLB's internal game ID (gamePk)
    home_team: str        # Full team name, e.g. "Houston Astros"
    away_team: str
    start_time: str       # ISO 8601 UTC string, e.g. "2026-05-22T17:05:00Z"
    venue: str            # Ballpark name, e.g. "Coors Field"
    home_pitcher: Optional[str] = None  # "FirstName LastName" or None if TBD
    away_pitcher: Optional[str] = None
    is_outdoor: bool = True  # False for domed/retractable-roof parks


@dataclass
class PropOdds:
    """
    A single player HR prop betting line.
    Produced by odds.py. Only lines at +300 or longer are returned.
    """
    player_name: str      # "Aaron Judge"
    team: str             # "NYY"
    odds: int             # American format: +340, +420, etc.
    game_id: Optional[int] = None
    bookmaker: str = "Unknown"


@dataclass
class WeatherImpact:
    """
    Weather conditions at a ballpark, with a pre-computed HR impact.
    Produced by weather.py.
    """
    venue: str
    temp_f: float         # Temperature in Fahrenheit
    wind_speed_mph: float
    wind_direction: str   # Compass abbreviation: "NW", "SE", etc.
    conditions: str       # "Clear", "Partly Cloudy", "Rain", "Thunderstorm"
    wind_category: str    # "out" | "neutral" | "in" — simplified direction
    hr_boost: float       # How much this weather helps HRs: -0.20 to +0.25


@dataclass
class HREnvironmentScore:
    """
    Combined HR environment score for a game, 0–100.
    Produced by hr_score.py.
    Higher = better conditions for HRs happening in this game.
    """
    total: int            # Final score 0–100
    label: str            # "Elite" | "Strong" | "Moderate" | "Weak" | "Poor"

    # Component subscores (see hr_score.py for full explanation)
    park_score: int       # 0–30 from ballpark factor
    weather_score: int    # 0–25 from wind speed / direction
    pitcher_score: int    # 0–20 placeholder (upgradeable with Statcast data)
    odds_score: int       # 0–15 placeholder (upgradeable with prop data)
    batter_score: int     # 0–10 placeholder (upgradeable with batter stats)

    content_angle: str    # Pre-written one-liner for TikTok content
