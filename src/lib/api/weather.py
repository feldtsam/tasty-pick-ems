# ============================================================
# src/lib/api/weather.py
#
# Open-Meteo weather integration — 100% FREE, no API key needed.
# Docs: https://open-meteo.com/en/docs
#
# What this module does:
#   1. Maps every MLB ballpark to its GPS coordinates
#   2. For outdoor games, fetches current weather (temp, wind)
#   3. Classifies wind as "out", "in", or "neutral"
#   4. Normalizes into WeatherImpact objects
#   5. Skips indoor/domed parks entirely
#   6. Falls back to mock data only if the API is unreachable
# ============================================================

import requests
from typing import Dict, Optional
from .types import WeatherImpact

# ── API config ─────────────────────────────────────────────────
# No key needed — Open-Meteo is a free, open-source weather API.
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# ── Ballpark GPS coordinates + park factors ────────────────────
# lat/lon = center of each field
# factor  = HR park factor (1.0 = neutral, 1.38 = Coors = 38% more HRs)
# indoor  = True means weather doesn't apply (domed/retractable roof)
BALLPARKS: Dict[str, dict] = {
    "Coors Field":                {"lat": 39.7559,  "lon": -104.9942, "factor": 1.38, "indoor": False},
    "Great American Ball Park":   {"lat": 39.0979,  "lon":  -84.5082, "factor": 1.29, "indoor": False},
    "Yankee Stadium":             {"lat": 40.8296,  "lon":  -73.9262, "factor": 1.22, "indoor": False},
    "Citizens Bank Park":         {"lat": 39.9061,  "lon":  -75.1665, "factor": 1.19, "indoor": False},
    "Camden Yards":                     {"lat": 39.2839,  "lon":  -76.6218, "factor": 1.09, "indoor": False},
    "Oriole Park at Camden Yards":      {"lat": 39.2839,  "lon":  -76.6218, "factor": 1.09, "indoor": False},
    "Wrigley Field":              {"lat": 41.9484,  "lon":  -87.6553, "factor": 1.12, "indoor": False},
    "American Family Field":      {"lat": 43.0280,  "lon":  -87.9712, "factor": 1.08, "indoor": True},
    "Fenway Park":                {"lat": 42.3467,  "lon":  -71.0972, "factor": 1.15, "indoor": False},
    "Truist Park":                {"lat": 33.8908,  "lon":  -84.4678, "factor": 1.05, "indoor": False},
    "Guaranteed Rate Field":      {"lat": 41.8300,  "lon":  -87.6339, "factor": 1.05, "indoor": False},
    "Chase Field":                {"lat": 33.4453,  "lon": -112.0667, "factor": 1.07, "indoor": True},
    "Minute Maid Park":           {"lat": 29.7573,  "lon":  -95.3555, "factor": 1.10, "indoor": True},
    "Dodger Stadium":             {"lat": 34.0739,  "lon": -118.2400, "factor": 1.05, "indoor": False},
    "Globe Life Field":           {"lat": 32.7473,  "lon":  -97.0845, "factor": 1.00, "indoor": True},
    "Target Field":               {"lat": 44.9817,  "lon":  -93.2778, "factor": 1.02, "indoor": False},
    "Nationals Park":             {"lat": 38.8730,  "lon":  -77.0074, "factor": 1.03, "indoor": False},
    "Busch Stadium":              {"lat": 38.6226,  "lon":  -90.1928, "factor": 0.98, "indoor": False},
    "PNC Park":                   {"lat": 40.4469,  "lon":  -80.0057, "factor": 0.97, "indoor": False},
    "Progressive Field":          {"lat": 41.4962,  "lon":  -81.6852, "factor": 0.99, "indoor": False},
    "Comerica Park":              {"lat": 42.3390,  "lon":  -83.0485, "factor": 0.96, "indoor": False},
    "Kauffman Stadium":           {"lat": 39.0517,  "lon":  -94.4803, "factor": 0.96, "indoor": False},
    "Angel Stadium":              {"lat": 33.8003,  "lon": -117.8827, "factor": 0.99, "indoor": False},
    "T-Mobile Park":              {"lat": 47.5914,  "lon": -122.3325, "factor": 0.95, "indoor": False},
    "Oakland Coliseum":           {"lat": 37.7516,  "lon": -122.2005, "factor": 0.90, "indoor": False},
    "Citi Field":                 {"lat": 40.7571,  "lon":  -73.8458, "factor": 0.93, "indoor": False},
    "Oracle Park":                {"lat": 37.7786,  "lon": -122.3893, "factor": 0.90, "indoor": False},
    "Petco Park":                 {"lat": 32.7076,  "lon": -117.1570, "factor": 0.93, "indoor": False},
    "loanDepot park":             {"lat": 25.7781,  "lon":  -80.2197, "factor": 0.86, "indoor": True},
    "Tropicana Field":            {"lat": 27.7682,  "lon":  -82.6534, "factor": 0.92, "indoor": True},
    "Rogers Centre":              {"lat": 43.6414,  "lon":  -79.3894, "factor": 1.08, "indoor": True},
}

# ── WMO weather code → readable label ─────────────────────────
# Open-Meteo uses WMO codes instead of text descriptions.
# Full list: https://open-meteo.com/en/docs#weathervariables
WMO_CONDITIONS: Dict[int, str] = {
    0:  "Clear",
    1:  "Mainly Clear", 2: "Partly Cloudy", 3: "Overcast",
    45: "Foggy",        48: "Foggy",
    51: "Drizzle",      53: "Drizzle",      55: "Drizzle",
    61: "Rain",         63: "Rain",         65: "Rain",
    71: "Snow",         73: "Snow",         75: "Snow",
    80: "Rain",         81: "Rain",         82: "Rain",
    95: "Thunderstorm", 96: "Thunderstorm", 99: "Thunderstorm",
}

# ── Mock fallback data ─────────────────────────────────────────
# Only used if Open-Meteo is completely unreachable (very rare).
MOCK_WEATHER: Dict[str, WeatherImpact] = {
    "Coors Field": WeatherImpact(
        venue="Coors Field", temp_f=74, wind_speed_mph=12,
        wind_direction="SE", conditions="Clear",
        wind_category="out", hr_boost=0.22,
    ),
    "Yankee Stadium": WeatherImpact(
        venue="Yankee Stadium", temp_f=68, wind_speed_mph=8,
        wind_direction="W", conditions="Partly Cloudy",
        wind_category="out", hr_boost=0.12,
    ),
    "Citizens Bank Park": WeatherImpact(
        venue="Citizens Bank Park", temp_f=71, wind_speed_mph=6,
        wind_direction="SW", conditions="Clear",
        wind_category="neutral", hr_boost=0.05,
    ),
    "Oakland Coliseum": WeatherImpact(
        venue="Oakland Coliseum", temp_f=62, wind_speed_mph=18,
        wind_direction="W", conditions="Cloudy",
        wind_category="neutral", hr_boost=0.08,
    ),
}


# ── Main function ──────────────────────────────────────────────

def fetch_weather_for_venue(venue: str) -> Optional[WeatherImpact]:
    """
    Fetch current weather for a given ballpark using Open-Meteo.
    No API key required.

    Returns None if the venue is indoor or not in our ballpark map.
    Falls back to mock data only if Open-Meteo is unreachable.

    Usage:
        from src.lib.api.weather import fetch_weather_for_venue
        w = fetch_weather_for_venue("Coors Field")
    """
    park = BALLPARKS.get(venue)

    if not park:
        print(f"[Weather] Unknown venue: '{venue}'. Skipping.")
        return None

    if park["indoor"]:
        print(f"[Weather] {venue} is indoors — weather not a factor.")
        return None

    try:
        return _fetch_from_open_meteo(venue, park)
    except Exception as e:
        print(f"[Weather] Open-Meteo error for {venue}: {e}. Using mock data.")
        return MOCK_WEATHER.get(venue)


def get_park_factor(venue: str) -> float:
    """
    Return the HR park factor for a venue.
    1.0 = neutral. 1.38 = Coors (38% more HRs than average).
    Returns 1.0 if venue is unknown.
    """
    return BALLPARKS.get(venue, {}).get("factor", 1.0)


# ── Internal helpers ───────────────────────────────────────────

def _fetch_from_open_meteo(venue: str, park: dict) -> WeatherImpact:
    """
    Call Open-Meteo and normalize the response into a WeatherImpact.
    """
    params = {
        "latitude":        park["lat"],
        "longitude":       park["lon"],
        # Request only the current conditions we need
        "current":         "temperature_2m,wind_speed_10m,wind_direction_10m,weather_code",
        "temperature_unit": "fahrenheit",
        "wind_speed_unit":  "mph",
        "forecast_days":   1,
    }

    response = requests.get(OPEN_METEO_URL, params=params, timeout=10)
    response.raise_for_status()
    data = response.json()

    current = data["current"]

    temp_f     = current["temperature_2m"]
    wind_speed = current["wind_speed_10m"]
    wind_deg   = current["wind_direction_10m"]
    wmo_code   = current["weather_code"]

    conditions    = WMO_CONDITIONS.get(wmo_code, "Unknown")
    wind_dir_str  = _degrees_to_compass(wind_deg)
    wind_category = _classify_wind(wind_deg, wind_speed)
    hr_boost      = _calculate_hr_boost(wind_speed, wind_category, conditions)

    print(f"[Weather] {venue}: {temp_f}°F, {wind_speed}mph {wind_dir_str}, {conditions}")

    return WeatherImpact(
        venue=venue,
        temp_f=round(temp_f, 1),
        wind_speed_mph=round(wind_speed, 1),
        wind_direction=wind_dir_str,
        conditions=conditions,
        wind_category=wind_category,
        hr_boost=round(hr_boost, 3),
    )


def _degrees_to_compass(degrees: float) -> str:
    """Convert 0–360 wind degrees to a compass abbreviation (N, NE, E, ...)."""
    directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    index = int((degrees + 22.5) / 45) % 8
    return directions[index]


def _classify_wind(wind_deg: float, wind_speed: float) -> str:
    """
    Simplified wind category: "out", "in", or "neutral".

    Calm wind (under 8 mph) has minimal impact so we call it neutral.
    Above that we use a rough compass heuristic — W/SW tends to blow
    "out" in many US parks; E/SE tends to blow "in".
    A future upgrade can add per-park CF bearing for exact direction.
    """
    if wind_speed < 8:
        return "neutral"
    if 180 <= wind_deg <= 315:   # S → SW → W → NW: tends out
        return "out"
    if 45 <= wind_deg < 180:     # NE → E → SE → S: tends in
        return "in"
    return "neutral"


def _calculate_hr_boost(wind_speed: float, wind_category: str, conditions: str) -> float:
    """
    Estimate how much this weather boosts HR probability.
    Returns roughly -0.20 to +0.25.
    """
    boost = 0.0

    if wind_category == "out":
        boost += min(0.20, (wind_speed / 5) * 0.03)
    elif wind_category == "in":
        boost -= min(0.15, (wind_speed / 5) * 0.03)

    # Penalise bad conditions
    if conditions in ("Rain", "Drizzle"):
        boost -= 0.08
    elif conditions in ("Thunderstorm", "Snow"):
        boost -= 0.15

    return boost
