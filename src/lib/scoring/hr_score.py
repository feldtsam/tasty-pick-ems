# ============================================================
# src/lib/scoring/hrScore.py
#
# HR Environment Score — 0 to 100.
#
# Combines park factor, weather, pitcher data, odds value,
# and batter power into a single number that tells you how
# favorable conditions are for home runs in a given game.
#
# Score breakdown (max 100 points):
#   Park factor    0–30 pts   (data available now)
#   Weather boost  0–25 pts   (data available now)
#   Pitcher weak.  0–20 pts   (placeholder — add Statcast later)
#   Odds value     0–15 pts   (placeholder — add prop data later)
#   Batter power   0–10 pts   (placeholder — add Statcast later)
# ============================================================

from typing import Optional
from src.lib.api.types import WeatherImpact, HREnvironmentScore


def calculate_hr_score(
    venue: str,
    park_factor: float,
    weather: Optional[WeatherImpact],
    # These are placeholders — connect real data when available
    pitcher_hr_per9: Optional[float] = None,   # e.g. 1.8 HR/9
    batter_barrel_pct: Optional[float] = None, # e.g. 0.14 = 14%
    best_prop_odds: Optional[int] = None,      # e.g. 340 = +340
) -> HREnvironmentScore:
    """
    Calculate an HR Environment Score from 0–100 for a game.

    Args:
        venue:             Ballpark name, used for labeling.
        park_factor:       HR park factor from weather.py (1.0 = neutral).
        weather:           WeatherImpact object (or None for indoor parks).
        pitcher_hr_per9:   Opposing pitcher's HR per 9 innings (optional).
        batter_barrel_pct: Best batter's barrel % in this game (optional).
        best_prop_odds:    Best available HR prop odds in American format (optional).

    Returns:
        HREnvironmentScore with total, label, component scores, and content angle.
    """

    # ── Component 1: Park Factor (0–30 pts) ───────────────────
    # Scale: factor 0.80 = 0 pts | factor 1.0 = 10 pts | factor 1.40+ = 30 pts
    park_score = _park_factor_score(park_factor)

    # ── Component 2: Weather (0–25 pts) ───────────────────────
    # Wind blowing out + fast = high score | bad conditions = low score
    weather_score = _weather_score(weather)

    # ── Component 3: Pitcher HR Weakness (0–20 pts) ───────────
    # For now: neutral 10 pts. Replace with real data later.
    pitcher_score = _pitcher_score(pitcher_hr_per9)

    # ── Component 4: Odds Value (0–15 pts) ────────────────────
    # For now: neutral 8 pts. Replace with real prop data later.
    odds_score = _odds_score(best_prop_odds)

    # ── Component 5: Batter Power (0–10 pts) ──────────────────
    # For now: neutral 5 pts. Replace with Statcast barrel % later.
    batter_score = _batter_score(batter_barrel_pct)

    # ── Total ──────────────────────────────────────────────────
    total = min(100, park_score + weather_score + pitcher_score + odds_score + batter_score)
    label = _score_label(total)
    angle = _content_angle(venue, park_factor, weather, total)

    return HREnvironmentScore(
        total=round(total),
        label=label,
        park_score=round(park_score),
        weather_score=round(weather_score),
        pitcher_score=round(pitcher_score),
        odds_score=round(odds_score),
        batter_score=round(batter_score),
        content_angle=angle,
    )


# ── Score component functions ──────────────────────────────────

def _park_factor_score(factor: float) -> float:
    """
    Map park factor (0.80–1.40+) to 0–30 points.
    Linear scale: 0.80 → 0 pts, 1.40 → 30 pts.
    """
    MIN_FACTOR, MAX_FACTOR = 0.80, 1.40
    clamped = max(MIN_FACTOR, min(MAX_FACTOR, factor))
    return ((clamped - MIN_FACTOR) / (MAX_FACTOR - MIN_FACTOR)) * 30


def _weather_score(weather: Optional[WeatherImpact]) -> float:
    """
    Map weather conditions to 0–25 points.
    Indoor park (weather=None) → 10 pts (neutral baseline).
    """
    if weather is None:
        return 10  # Indoor park: neutral, roof controls conditions

    score = 10.0  # Start at neutral

    # Wind speed bonus/penalty: 0 mph = 0 extra, 20+ mph = 10 extra
    wind_speed_score = min(10, (weather.wind_speed_mph / 20) * 10)

    # Wind direction modifier
    direction_modifier = {
        "out":     +5,   # Blowing to outfield — great for HRs
        "neutral":  0,
        "in":      -5,   # Blowing in — suppresses HRs
    }.get(weather.wind_category, 0)

    # Weather condition modifier
    condition_modifier = 0
    if weather.conditions in ("Clear", "Sunny"):
        condition_modifier = 2
    elif weather.conditions in ("Rain", "Drizzle"):
        condition_modifier = -5
    elif weather.conditions in ("Thunderstorm", "Snow"):
        condition_modifier = -10

    score += wind_speed_score + direction_modifier + condition_modifier

    return max(0, min(25, score))


def _pitcher_score(hr_per9: Optional[float]) -> float:
    """
    Map pitcher HR/9 to 0–20 points.
    Higher HR/9 = easier to hit HRs = more points.
    Returns neutral 10 pts when data is not available.
    """
    if hr_per9 is None:
        return 10  # Neutral placeholder

    # Scale: 0.5 HR/9 → 0 pts | 1.0 → 10 pts | 2.0+ → 20 pts
    return max(0, min(20, (hr_per9 / 2.0) * 20))


def _odds_score(best_odds: Optional[int]) -> float:
    """
    Map available odds to 0–15 points.
    Longer odds = more value opportunity = more points.
    Returns neutral 8 pts when no prop data is available.
    """
    if best_odds is None:
        return 8  # Neutral placeholder

    # +300 → 3 pts | +500 → 10 pts | +700+ → 15 pts
    return max(0, min(15, ((best_odds - 200) / 500) * 15))


def _batter_score(barrel_pct: Optional[float]) -> float:
    """
    Map batter barrel percentage to 0–10 points.
    Higher barrel % = more power = more points.
    Returns neutral 5 pts when data is not available.
    """
    if barrel_pct is None:
        return 5  # Neutral placeholder

    # Scale: 0% → 0 pts | 10% → 5 pts | 20%+ → 10 pts
    return max(0, min(10, (barrel_pct / 0.20) * 10))


def _score_label(score: float) -> str:
    """Convert a numeric score to a readable label."""
    if score >= 80: return "Elite"
    if score >= 65: return "Strong"
    if score >= 50: return "Moderate"
    if score >= 35: return "Weak"
    return "Poor"


def _content_angle(
    venue: str,
    park_factor: float,
    weather: Optional[WeatherImpact],
    score: float,
) -> str:
    """
    Generate a pre-written TikTok content angle for this game environment.
    This is what shows up in the Live Data dashboard as the 'content angle' chip.
    """
    factor_pct = int((park_factor - 1.0) * 100)
    factor_str = f"+{factor_pct}%" if factor_pct > 0 else f"{factor_pct}%"

    if score >= 80:
        wind = f" Wind {weather.wind_speed_mph:.0f} mph {weather.wind_category}." if weather else ""
        return (
            f"🔥 ELITE HR environment at {venue} today. "
            f"{factor_str} park factor.{wind} "
            f"Attack every power bat in this game."
        )
    elif score >= 65:
        return (
            f"💪 Strong conditions at {venue}. {factor_str} park factor. "
            f"The setup is right — target the +300+ power bats here."
        )
    elif score >= 50:
        return (
            f"👀 Moderate HR environment at {venue}. {factor_str} park factor. "
            f"Playable — go with the top-tier power matchups only."
        )
    elif score >= 35:
        return (
            f"⚠️ Tough conditions at {venue} today. If you're playing it, "
            f"focus on the long shots and look for the sneaky edge."
        )
    else:
        return (
            f"❌ Poor HR environment at {venue}. "
            f"Better spots elsewhere on the slate today."
        )
