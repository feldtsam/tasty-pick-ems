"""
Static NFL team -> division lookup, keyed on the exact team-abbreviation
format this pipeline's own scored data actually uses (weekly's posteam/
defteam columns) -- confirmed directly against real data before writing
this, not copied from nfl_data_py.import_team_desc() unchecked.

CONFIRMED, not assumed: import_team_desc() itself carries a real
team_division column (fetched fresh, not from memory) that matches this
table's 8 division names and every team's grouping exactly -- this table
could technically be derived from that live source at import time instead
of hardcoded. Kept static and hand-written anyway, deliberately: division
realignment is rare enough that a static table is the lower-risk choice
here, and import_team_desc() pulls a real CSV over HTTPS on every call
(hit a real, unrelated local SSL-cert failure fetching it directly during
this table's own verification) -- a dependency this simple lookup doesn't
need to carry.

ONE REAL CORRECTION versus the "standard abbreviations" a first guess
would produce: the Rams are "LA" in this pipeline's actual scored data
(weekly.posteam/defteam, every season 2022/2024/2025, confirmed against
real rows -- e.g. Matthew Stafford, Davante Adams), NOT "LAR". nflverse's
own team_desc reference table lists both "LA" and "LAR" as separate rows
for the same team (a real, confirmed quirk of that source, not a typo
here) -- "LAR" would have been a silent, total miss for every real Rams
player had it shipped unchecked. Every other code below (BUF, LV, LAC,
WAS, etc.) was cross-checked the same way and matched what a standard
abbreviation guess would produce.
"""

DIVISIONS = {
    "AFC East": ["BUF", "MIA", "NE", "NYJ"],
    "AFC North": ["BAL", "CIN", "CLE", "PIT"],
    "AFC South": ["HOU", "IND", "JAX", "TEN"],
    "AFC West": ["DEN", "KC", "LV", "LAC"],
    "NFC East": ["DAL", "NYG", "PHI", "WAS"],
    "NFC North": ["CHI", "DET", "GB", "MIN"],
    "NFC South": ["ATL", "CAR", "NO", "TB"],
    "NFC West": ["ARI", "LA", "SF", "SEA"],
}

# Reverse index, built once at import time rather than scanning DIVISIONS'
# 8 lists on every call -- team_to_division() runs once per pool row.
_TEAM_TO_DIVISION = {team: division for division, teams in DIVISIONS.items() for team in teams}


def team_to_division(team: str) -> str | None:
    """Return the division for a team abbreviation (weekly's own posteam/
    defteam format), or None if unrecognized -- never guessed at."""
    return _TEAM_TO_DIVISION.get(team)
