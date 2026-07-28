"""
DAY-LEVEL GAME DISCOVERY ONLY. Fetches today's (or any date's) MLB
schedule so a caller knows which game_pks exist and their rough status —
used by build_candidates_for_date()'s dev/test wrapper and by
test_live_data.py to find real game_pks to validate against.

The actual per-game lineup/probable-pitcher/weather fetch used by the
production path (build_candidates_for_game()) does NOT use this module —
see game_data.py, which gets everything from one feed/live call per game_pk
instead. This module still exposes lineups/probable pitchers via the
schedule hydrate (kept because it's a useful at-a-glance day summary and
was already confirmed working — see below) but nothing downstream depends
on that part of its output anymore.

Lineup availability window (confirmed against the real live slate, not
assumed — this finding still holds and is exactly what game_data.py relies
on too, just via a different endpoint): a game more than ~2 days out
(`status.detailedState == "Scheduled"`) has empty lineups. They fill in
reliably starting around "Pre-Game" status (roughly 1-1.5 hours before
first pitch) and stay populated through "In Progress" and "Final".
`probablePitcher` is available far earlier (days out) and reliably,
independent of lineup posting.
"""
import requests

SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
TIMEOUT_S = 15

# "Scheduled"/"Pre-Game" etc. come from status.detailedState. Anything not
# in this set (Postponed, Cancelled, Suspended pre-resumption) means the
# game isn't going to produce a normal lineup today and callers should
# treat it as unscored, not as "lineup not posted yet".
NOT_HAPPENING_STATES = {"Postponed", "Cancelled", "Suspended"}


def _extract_probable_pitcher(team_side: dict):
    pp = team_side.get("probablePitcher")
    if not pp:
        return None
    return {"mlbam_id": pp.get("id"), "full_name": pp.get("fullName")}


def _extract_lineup(players: list) -> list:
    """Player list order from the schedule hydrate IS batting order —
    confirmed against feed/live's explicit battingOrder field. slot is
    1-indexed position in that list."""
    lineup = []
    for slot, p in enumerate(players, start=1):
        lineup.append({
            "mlbam_id": p.get("id"),
            "full_name": p.get("fullName"),
            "batting_order_slot": slot,
            "position": (p.get("primaryPosition") or {}).get("abbreviation"),
        })
    return lineup


def fetch_schedule(date: str) -> list:
    """
    date: "YYYY-MM-DD". Returns a list of game dicts, one per gamePk
    (doubleheaders naturally produce two separate entries, same matchup,
    different gamePk/gameNumber — nothing special needed to handle them,
    each is just scored as its own game).
    """
    resp = requests.get(
        SCHEDULE_URL,
        params={"sportId": 1, "date": date, "hydrate": "probablePitcher,lineups,team"},
        timeout=TIMEOUT_S,
    )
    resp.raise_for_status()
    data = resp.json()

    games = []
    for date_block in data.get("dates", []):
        for g in date_block.get("games", []):
            status = g.get("status", {})
            detailed_state = status.get("detailedState")
            away = g["teams"]["away"]
            home = g["teams"]["home"]

            lineups = g.get("lineups") or {}
            away_lineup = _extract_lineup(lineups.get("awayPlayers", []))
            home_lineup = _extract_lineup(lineups.get("homePlayers", []))

            if detailed_state in NOT_HAPPENING_STATES:
                lineup_status = "not_happening"
            elif away_lineup and home_lineup:
                lineup_status = "confirmed"
            else:
                lineup_status = "not_yet_posted"

            games.append({
                "game_pk": g.get("gamePk"),
                "game_number": g.get("gameNumber"),
                "double_header": g.get("doubleHeader"),
                "game_date_utc": g.get("gameDate"),
                "official_date": g.get("officialDate"),
                "status": {
                    "abstract": status.get("abstractGameState"),
                    "detailed": detailed_state,
                    "reason": status.get("reason"),
                },
                "venue": {"id": (g.get("venue") or {}).get("id"), "name": (g.get("venue") or {}).get("name")},
                "away_team": {
                    "id": away["team"].get("id"),
                    "name": away["team"].get("name"),
                    "abbreviation": away["team"].get("abbreviation"),
                },
                "home_team": {
                    "id": home["team"].get("id"),
                    "name": home["team"].get("name"),
                    "abbreviation": home["team"].get("abbreviation"),
                },
                "away_probable_pitcher": _extract_probable_pitcher(away),
                "home_probable_pitcher": _extract_probable_pitcher(home),
                "lineup_status": lineup_status,
                "away_lineup": away_lineup,
                "home_lineup": home_lineup,
            })
    return games
