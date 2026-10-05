"""
athleteId -> position resolution, season-cached.

CFBD `/roster?year=&classification=fbs` returns one `RosterPlayer` per
player per team for the season:
    id firstName lastName team height weight jersey year position ...
`id` is the same string the /plays/stats rows carry as `athleteId`;
`position` is a clean short tag ("RB", "WR", "TE", "QB", "OL", ...) — the
verification round confirmed a clean RB/WR/TE taxonomy (Georgia 2024:
RB 11 / WR 21 / TE 9).

Two views over the same fetched rows:
  * position_group(season)     -> { athleteId -> "RB"|"WR"|"TE" }  (skill only)
  * raw_position_lookup(season)-> { athleteId -> "<RAW POSITION>" } (every position)

The raw view is what lets cfb/redzone.py tell a QB scramble (spec §8 /
Step 1 Q4: excluded from typed rows, stashed in extra.qb_rz) apart from a
genuinely-unrostered athleteId (Q3: NULL position_group, logged, never
dropped).
"""
from __future__ import annotations

from ids import cfbd_get

POSITION_GROUPS = ("RB", "WR", "TE")

# season -> { athleteId(str) -> RAW position string, upper-cased }
_RAW_CACHE: dict[int, dict[str, str]] = {}


def fetch_fbs_roster(season: int) -> list[dict]:
    """Whole-FBS roster for a season in one call."""
    rows = cfbd_get("/roster", {"year": int(season), "classification": "fbs"})
    if not isinstance(rows, list):
        raise TypeError(f"/roster did not return a list: {type(rows)!r}")
    return rows


def _fetch_roster_per_team(season: int, teams: list[str]) -> list[dict]:
    out: list[dict] = []
    for t in teams:
        rows = cfbd_get("/roster", {"year": int(season), "team": t})
        if isinstance(rows, list):
            out.extend(rows)
    return out


def _load_raw(season: int, fallback_teams: list[str] | None) -> dict[str, str]:
    if season in _RAW_CACHE:
        return _RAW_CACHE[season]

    rows = fetch_fbs_roster(season)
    if not rows and fallback_teams:
        rows = _fetch_roster_per_team(season, fallback_teams)

    raw: dict[str, str] = {}
    for r in rows:
        aid = r.get("id")
        pos = r.get("position")
        if aid is None or pos is None:
            continue
        raw[str(aid)] = str(pos).strip().upper()

    _RAW_CACHE[season] = raw
    return raw


def raw_position_lookup(season: int, *, fallback_teams: list[str] | None = None) -> dict[str, str]:
    """{ athleteId -> RAW upper-cased position } for every rostered player."""
    return _load_raw(season, fallback_teams)


def position_lookup(season: int, *, fallback_teams: list[str] | None = None) -> dict[str, str]:
    """{ athleteId -> "RB"|"WR"|"TE" } — skill positions only."""
    raw = _load_raw(season, fallback_teams)
    return {aid: pos for aid, pos in raw.items() if pos in POSITION_GROUPS}


def clear_cache() -> None:
    """Test hook."""
    _RAW_CACHE.clear()
