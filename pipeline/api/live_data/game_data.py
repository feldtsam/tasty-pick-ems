"""
Single-game fetch: given just a `game_pk`, one `feed/live` call returns
everything needed to build that game's candidates — status, teams, venue,
probable pitchers, batting-order lineup, and weather/roof. This replaces
the earlier two-call design (a whole-day schedule hydrate for lineups +
probable pitchers, plus a separate per-game feed/live call for weather) —
confirmed feed/live alone carries all of it, so the per-game endpoint
needs nothing but a game_pk. No day-level schedule call, no whole-slate
batching.

Lineup extraction: `liveData.boxscore.teams.{home,away}.players` lists
every rostered player (starters, bench, bullpen). Confirmed against a real
live game (824001) that the *starting* nine are exactly the ones with a
`battingOrder` value ending in "00" (e.g. "100".."900" — substitutes made
later in the game get other values like "101", irrelevant here since only
the starting lineup is used) — and that this produces the identical
9-player, same-order lineup the old schedule-hydrate approach did.

Confirmed a far-future "Scheduled" game (no lineup posted yet) still
returns `probablePitchers` (at least the side MLB has announced — a game
several days out can have only one side's starter announced, e.g.
`{"away": {...}}` with no "home" key at all) and an empty lineup (every
player's `battingOrder` is None) — same three-way `lineup_status` used
before: "confirmed" / "not_yet_posted" / "not_happening".
"""
import requests

from mlb_schedule import NOT_HAPPENING_STATES

FEED_LIVE_URL = "https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live"
TIMEOUT_S = 15


def _parse_wind(wind_str):
    """"7 mph, Out To CF" -> (7.0, "Out To CF"). "0 mph, None" -> (0.0, "None")."""
    if not wind_str:
        return None, None
    speed_part, _, desc_part = wind_str.partition(",")
    desc = desc_part.strip() or None
    speed_digits = "".join(ch for ch in speed_part if ch.isdigit() or ch == ".")
    speed = float(speed_digits) if speed_digits else None
    return speed, desc


def _derive_roof_status(condition: str, roof_type: str) -> str:
    cond = (condition or "").lower()
    if "closed" in cond:
        return "closed"
    if (roof_type or "").lower() == "dome":
        return "dome"
    return "outdoor"


def _extract_lineup(players: dict) -> list:
    starters = []
    for pdata in players.values():
        bo = pdata.get("battingOrder")
        if not bo or len(bo) != 3 or not bo.endswith("00"):
            continue
        slot = int(bo) // 100
        starters.append((slot, {
            "mlbam_id": pdata["person"]["id"],
            "full_name": pdata["person"]["fullName"],
            "batting_order_slot": slot,
            "position": (pdata.get("position") or {}).get("abbreviation"),
        }))
    starters.sort(key=lambda x: x[0])
    return [s[1] for s in starters]


def fetch_game_data(game_pk: int) -> dict:
    """Returns one game's full context — status/teams/venue/probable
    pitchers/lineup/weather — from a single feed/live call. Raises on a
    genuine HTTP failure. Raises ValueError for an unknown/invalid game_pk —
    confirmed feed/live does NOT 404 on one; it returns HTTP 200 with
    `gamePk: 0` and near-empty gameData (`status.detailedState: "Unknown"`,
    no officialDate/season/teams), which would otherwise surface as a
    confusing downstream KeyError/TypeError instead of a clear error.
    Never raises for a real game that simply hasn't posted a lineup yet."""
    resp = requests.get(FEED_LIVE_URL.format(game_pk=game_pk), timeout=TIMEOUT_S)
    resp.raise_for_status()
    data = resp.json()

    if data.get("gamePk") != game_pk:
        raise ValueError(f"game_pk {game_pk} is not a known MLB game (feed/live returned an empty/placeholder response).")

    game_data = data.get("gameData", {})
    status = game_data.get("status", {})
    detailed_state = status.get("detailedState")
    dt = game_data.get("datetime", {})
    teams = game_data.get("teams", {})
    venue = game_data.get("venue", {})
    field_info = venue.get("fieldInfo") or {}
    weather = game_data.get("weather") or {}
    pp = game_data.get("probablePitchers") or {}
    game_info = game_data.get("game", {})

    box_teams = (data.get("liveData", {}).get("boxscore", {}) or {}).get("teams", {})
    away_lineup = _extract_lineup((box_teams.get("away") or {}).get("players", {}))
    home_lineup = _extract_lineup((box_teams.get("home") or {}).get("players", {}))

    if detailed_state in NOT_HAPPENING_STATES:
        lineup_status = "not_happening"
    elif away_lineup and home_lineup:
        lineup_status = "confirmed"
    else:
        lineup_status = "not_yet_posted"

    def _probable(side):
        p = pp.get(side)
        return {"mlbam_id": p.get("id"), "full_name": p.get("fullName")} if p else None

    wind_speed, wind_desc = _parse_wind(weather.get("wind"))
    temp_raw = weather.get("temp")
    try:
        temp_f = float(temp_raw) if temp_raw not in (None, "") else None
    except ValueError:
        temp_f = None

    return {
        "game_pk": game_pk,
        "game_number": game_info.get("gameNumber"),
        "double_header": game_info.get("doubleHeader"),
        "season": int(game_info["season"]) if game_info.get("season") else None,
        "game_date_utc": dt.get("dateTime"),
        "official_date": dt.get("officialDate"),
        "status": {
            "abstract": status.get("abstractGameState"),
            "detailed": detailed_state,
            "reason": status.get("reason"),
        },
        "venue": {"id": venue.get("id"), "name": venue.get("name")},
        "away_team": {
            "id": (teams.get("away") or {}).get("id"),
            "name": (teams.get("away") or {}).get("name"),
            "abbreviation": (teams.get("away") or {}).get("abbreviation"),
        },
        "home_team": {
            "id": (teams.get("home") or {}).get("id"),
            "name": (teams.get("home") or {}).get("name"),
            "abbreviation": (teams.get("home") or {}).get("abbreviation"),
        },
        "away_probable_pitcher": _probable("away"),
        "home_probable_pitcher": _probable("home"),
        "lineup_status": lineup_status,
        "away_lineup": away_lineup,
        "home_lineup": home_lineup,
        "weather": {
            "condition": weather.get("condition"),
            "temp_f": temp_f,
            "wind_speed_mph": wind_speed,
            "wind_description": wind_desc,
            "roof_status": _derive_roof_status(weather.get("condition"), field_info.get("roofType")),
        },
    }
