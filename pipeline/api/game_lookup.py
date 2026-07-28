"""
Resolves MLB's own numeric game_pk from what Make.com's existing odds-fetch
loop already has per game: home_team, away_team, and commence_time — all
three already present as top-level fields on The Odds API's own event
object (`{"id": ..., "home_team": ..., "away_team": ..., "commence_time":
..., "bookmakers": [...]}`), so nothing new has to flow through Make.com
to use this.

WHY THIS EXISTS: /api/score-game-props/game/<game_pk> (and
/api/live-data/game/<game_pk>) both key off MLB's own numeric game_pk — a
completely different ID system from The Odds API's own event ID scheme.
Make.com's odds-fetch loop only ever sees the latter, never the former.

TEAM NAME MATCHING — deliberately NOT fuzzy, unlike player names. Pulled
real data from both APIs for the same real dates to check, rather than
assuming: The Odds API's and MLB Stats API's `home_team`/`away_team`/
`team.name` strings are BYTE-IDENTICAL for all 30 real teams as of this
writing — including the "Athletics" rename (both APIs just say
"Athletics", no city) and "St. Louis Cardinals"'s period. Team names
turned out to be a much smaller, more stable problem than player names.
Given that, normalize_team_name() only handles case/period/whitespace —
cheap insurance against future formatting drift, not a real observed
mismatch — and resolve_game_pk() deliberately does NOT fuzzy-fallback the
way match_players() does for players in scored_picks.py: a wrong fuzzy
match between two TEAMS would misattribute an entire game's worth of
lineups/stats/odds, a much worse failure mode than misattributing one
player's odds line. An unresolved team pair fails loudly (ValueError) with
a clear message instead of guessing.

THE REAL DATE GOTCHA, confirmed against real data, not assumed: MLB's
schedule is keyed by `officialDate` — the game's LOCAL wall-clock date —
which for a US evening game is often ONE UTC CALENDAR DAY EARLIER than The
Odds API's `commence_time` (always a UTC timestamp). Confirmed: a real
game (Houston Astros @ Los Angeles Angels, game_pk 824003) has
commence_time "2026-07-29T01:38:00Z" but officialDate "2026-07-28" —
querying MLB's schedule for 2026-07-29 does not find this game AT ALL.
Resolving purely off commence_time's UTC calendar date would silently
fail for most evening games, especially West Coast ones. Handled by
searching BOTH commence_time's UTC date and the day before — never just
one — rather than doing real timezone math per ballpark, which would need
a per-venue timezone table this project has no other reason to maintain.

DOUBLEHEADERS — confirmed real, not hypothetical: two actual real 2026-07-28
games between the same two teams (Cleveland Guardians @ Cincinnati Reds,
game_pks 824490 and 824489, at 17:40Z and 23:10Z respectively — pulled
directly from the MLB Stats API, not constructed). When a team-name match
returns more than one game_pk, resolve_game_pk() picks the one whose own
actual start time is closest to the given commence_time, rather than
guessing the first one found — and reports this via
`disambiguated_by_time` so a caller can see when this happened.

WHY THE TWO CANDIDATE DATES ARE ALWAYS BOTH SEARCHED, never conditionally
("only check the day before if the first date came up empty") — an
earlier version tried that shortcut and a real test caught why it's wrong:
two teams routinely play multi-game series on CONSECUTIVE days, so
commence_time's own UTC date can easily already have exactly one real
match — just the wrong one (the next game in the series, not the one this
specific commence_time refers to). "A match was found" is not the same as
"the right match was found" when the same two teams play multiple days in
a row. The fix that actually works: always gather every team-name match
from both dates, then let real game start time — never date, never match
count — be the sole tiebreaker. Confirmed against a real live series
(Baltimore Orioles @ Detroit Tigers, playing on consecutive real days):
resolving a specific game correctly ignores that real neighboring game
from the day before because it isn't the closest-time match, not because
it wasn't found at all.
"""
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "live_data"))

from mlb_schedule import fetch_schedule  # noqa: E402


def normalize_team_name(name: str) -> str:
    """Case/period/whitespace-insensitive only — see module docstring for
    why this doesn't need the accent/suffix handling player names do."""
    if not name:
        return ""
    n = name.strip().lower()
    n = re.sub(r"\.", "", n)
    n = re.sub(r"\s+", " ", n)
    return n


def _parse_utc(iso_str: str) -> datetime:
    return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))


def resolve_game_pk(home_team: str, away_team: str, commence_time: str) -> dict:
    """
    Returns {"game_pk": int, "matched_game": {...}, "disambiguated_by_time": bool,
    "candidates": [game_pk, ...]} on success.

    Raises ValueError (not a silent None/False) if no team-name match is
    found on either candidate date — matches the convention game_data.py
    already uses for "not a known game", so callers can catch this the
    same way.

    ALWAYS searches both commence_time's own UTC date AND the day before,
    then ALWAYS picks whichever candidate's own real game_date_utc is
    closest to the given commence_time — never a conditional "only check
    the second date if the first came up empty" shortcut. An earlier
    version tried that shortcut and a real test caught why it's wrong:
    HOU @ LAA plays a real multi-game series, so the commence_time's own
    UTC date can ALREADY have exactly one real match — just the WRONG one
    (the next game in the series, not the one this specific commence_time
    refers to) — so "found something" isn't proof it's the right game. The
    fix is symmetric with the doubleheader case below: always gather every
    team-name match across both dates, then let actual game start time
    (not date, not match count) be the one and only tiebreaker. This
    naturally collapses to the trivial case (one real candidate, picked
    automatically) when there's genuinely only one game to find.
    """
    commence_dt = _parse_utc(commence_time)
    naive_date = commence_dt.date().isoformat()
    day_before = (commence_dt.date() - timedelta(days=1)).isoformat()

    home_norm = normalize_team_name(home_team)
    away_norm = normalize_team_name(away_team)

    def _matches_on(date_str):
        return [
            g for g in fetch_schedule(date_str)
            if normalize_team_name(g["home_team"]["name"]) == home_norm
            and normalize_team_name(g["away_team"]["name"]) == away_norm
        ]

    matches = _matches_on(naive_date) + _matches_on(day_before)

    if not matches:
        raise ValueError(
            f"no MLB schedule match for {away_team!r} @ {home_team!r} near {commence_time} "
            f"(searched {[naive_date, day_before]})"
        )

    def _time_delta_seconds(g):
        return abs((_parse_utc(g["game_date_utc"]) - commence_dt).total_seconds())

    matches.sort(key=_time_delta_seconds)
    best = matches[0]
    return {
        "game_pk": best["game_pk"],
        "matched_game": best,
        "disambiguated_by_time": len(matches) > 1,
        "candidates": [m["game_pk"] for m in matches],
    }
