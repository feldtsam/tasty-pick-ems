"""
Low-level CFBD HTTP primitive + team-identity helpers.

The single authenticated GET every other cfb/ module goes through
(cfb/plays_stats.py, cfb/roster.py), plus the name -> stable-integer-id
resolution the two aggregations need. Kept here rather than in
plays_stats.py so roster.py can import the client without importing the
play-stats fetchers it doesn't use.

Identity model (spec §8): a player is CFBD `athleteId` (a string); a team
is CFBD's stable integer team `id`, NOT the `school` string. The
`/plays/stats` rows only carry team/opponent as *strings*, so every run
resolves those strings to integer ids via that week's `/games` response
(which carries homeId/homeTeam and awayId/awayTeam for every game) — a
per-game map is always exactly right for the two teams in that game and
needs no global team table.
"""
import os
import time

import requests

CFBD_BASE = "https://apinext.collegefootballdata.com"

# The 2,000-row response cap applies to /plays/stats specifically: an
# unfiltered /plays/stats?year=&week= truncates at exactly 2,000 (CFBD
# verification round), while a per-gameId call returns ~150-350. Other
# collection endpoints are NOT capped at 2,000 — /roster?classification=fbs
# alone returns ~15k rows for a full season. So this guard is opt-in per
# call (truncation_guard=True), used only where a 2,000-row result really
# would mean a silently-truncated aggregate.
CFBD_TRUNCATION_ROWS = 2000

REQUEST_TIMEOUT_SECONDS = 20
MAX_RETRIES = 5
RETRY_BACKOFF_SECONDS = 2.0
# CFBD returns 429 "Too many concurrent requests for this endpoint" under
# even modest fan-out on /plays/stats. It clears in well under a second,
# so back off gently but persistently (with jitter so a pool of workers
# doesn't retry in lockstep) rather than treating it like a 5xx.
RETRY_BACKOFF_429_SECONDS = 0.6


class CFBDError(RuntimeError):
    """Any non-2xx from CFBD, or a transport-level failure after retries."""


def _api_key() -> str:
    key = os.environ.get("CFBD_API_KEY")
    if key is None or key.strip() == "":
        raise CFBDError(
            "CFBD_API_KEY is not set (or is blank). It is a CFBD free-tier "
            "Bearer token — set it in cfb/.env.local for local runs, or as a "
            "Vercel env var for the deployed endpoint."
        )
    return key.strip()


def cfbd_get(path: str, params: dict | None = None, *, truncation_guard: bool = False) -> list | dict:
    """
    Authenticated GET against apinext.collegefootballdata.com. Returns the
    parsed JSON body (a list for the collection endpoints this package
    uses). Raises CFBDError on any non-2xx after a small retry budget for
    429 / 5xx.

    `path` is the leading-slash path only ("/games", "/plays/stats", ...).

    truncation_guard=True raises if the response is a list of >= 2,000 rows
    — use it ONLY for /plays/stats, where that number means a silently
    truncated (unfiltered) result. Other endpoints (/roster) legitimately
    return far more than 2,000 rows.
    """
    import random

    url = f"{CFBD_BASE}{path}"
    headers = {"Authorization": f"Bearer {_api_key()}", "Accept": "application/json"}

    last_err: str | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
        except requests.RequestException as e:
            last_err = f"{type(e).__name__}: {e}"
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue
            raise CFBDError(f"GET {path} failed after {MAX_RETRIES} attempts — {last_err}") from e

        if resp.status_code == 429 or resp.status_code >= 500:
            last_err = f"HTTP {resp.status_code}: {resp.text[:300]}"
            if attempt < MAX_RETRIES:
                if resp.status_code == 429:
                    # brief, jittered — the concurrency window clears fast
                    time.sleep(RETRY_BACKOFF_429_SECONDS * attempt + random.uniform(0, 0.4))
                else:
                    time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue
            raise CFBDError(f"GET {path} — {last_err}")

        if not (200 <= resp.status_code < 300):
            raise CFBDError(f"GET {path} — HTTP {resp.status_code}: {resp.text[:500]}")

        try:
            body = resp.json()
        except ValueError as e:
            raise CFBDError(f"GET {path} — response was not JSON: {resp.text[:300]}") from e

        if truncation_guard and isinstance(body, list) and len(body) >= CFBD_TRUNCATION_ROWS:
            raise CFBDError(
                f"GET {path} params={params} returned {len(body)} rows (>= the "
                f"{CFBD_TRUNCATION_ROWS}-row /plays/stats cap). This call is effectively "
                f"unfiltered and its result is truncated — never aggregate from it. "
                f"Fetch per-gameId instead."
            )
        return body

    raise CFBDError(f"GET {path} — exhausted retries ({last_err})")


def team_id_map_from_games(games: list[dict]) -> dict[str, int]:
    """
    { team_school_string -> stable integer team id } built from a week's
    /games response. Covers both teams of every game in the list, which is
    exactly the set of team/opponent strings /plays/stats can return for
    that same (season, week). A school with no id in any game (should not
    happen for a completed FBS game) is simply absent — callers treat a
    missing id as "unresolved", never guess.
    """
    out: dict[str, int] = {}
    for g in games:
        for name_key, id_key in (("homeTeam", "homeId"), ("awayTeam", "awayId")):
            name = g.get(name_key)
            tid = g.get(id_key)
            if name is not None and tid is not None:
                out[str(name)] = int(tid)
    return out


def game_index(games: list[dict]) -> dict[int, dict]:
    """{ gameId -> game dict } for O(1) lookup of venue / completion / etc."""
    return {int(g["id"]): g for g in games if g.get("id") is not None}


_FBS_IDS_CACHE: dict[int, frozenset[int]] = {}


def fbs_team_ids(season: int) -> frozenset[int]:
    """
    { stable integer team id } for every FBS program in `season`, from CFBD
    `/teams/fbs?year=`. Season-cached. Used by
    scoring.drop_non_fbs_opponent_rows to keep FCS blowout stats out of the
    scoring reference (see the CFB weeks-1-8 validation).
    """
    season = int(season)
    if season not in _FBS_IDS_CACHE:
        rows = cfbd_get("/teams/fbs", {"year": season})
        _FBS_IDS_CACHE[season] = frozenset(
            int(t["id"]) for t in (rows or []) if isinstance(t, dict) and t.get("id") is not None
        )
    return _FBS_IDS_CACHE[season]


_TEAM_CONFERENCE_CACHE: dict[int, dict[int, str]] = {}


def team_conference_map(season: int) -> dict[int, str]:
    """
    { stable integer team id -> conference string } for every FBS program
    in `season`, from the SAME CFBD `/teams/fbs?year=` call fbs_team_ids
    uses (season-cached separately since callers of one don't always need
    the other). Keyed by team_id, not the school-name string every
    /plays/stats row carries `team`/`opponent` as -- avoids the exact
    string-matching fragility poll_ncaaf_prop_coverage.py's _match_school
    exists to work around on a different data source. Conference realign-
    ment is handled correctly by construction: querying a different
    `season` returns that season's real alignment (confirmed directly
    against the CFBD OpenAPI schema and a live call, not assumed).
    """
    season = int(season)
    if season not in _TEAM_CONFERENCE_CACHE:
        rows = cfbd_get("/teams/fbs", {"year": season})
        _TEAM_CONFERENCE_CACHE[season] = {
            int(t["id"]): t.get("conference")
            for t in (rows or [])
            if isinstance(t, dict) and t.get("id") is not None
        }
    return _TEAM_CONFERENCE_CACHE[season]


_TEAM_COLOR_CACHE: dict[int, dict[int, tuple[str, str]]] = {}


def team_color_map(season: int) -> dict[int, tuple[str, str]]:
    """
    { stable integer team id -> (color, alternateColor) } hex pair for
    every FBS program in `season`, from the SAME CFBD `/teams/fbs?year=`
    call fbs_team_ids/team_conference_map use (season-cached separately,
    since not every caller of one needs the others -- yes, this means up
    to 3 real calls to the identical endpoint/params when a run needs all
    three; a shared raw-response cache would remove that, but isn't built
    here to avoid touching the two existing functions' own call pattern
    in this pass).

    Both hex fields confirmed 100% populated (non-null, valid #rrggbb)
    across all 136 real FBS teams in a live 2025 pull, and confirmed
    stable across 130 schools common to 2019 and 2025 (zero real color
    differences) -- see that investigation's own report; not re-verified
    here. Feeds cfb.story_archetype.get_cfb_tint_profile(). A team
    missing either hex value (should not happen for a real FBS program)
    is simply absent from this map -- callers treat a missing entry as
    "no team color available," never fabricate one.
    """
    season = int(season)
    if season not in _TEAM_COLOR_CACHE:
        rows = cfbd_get("/teams/fbs", {"year": season})
        out: dict[int, tuple[str, str]] = {}
        for t in rows or []:
            if not isinstance(t, dict) or t.get("id") is None:
                continue
            color, alt = t.get("color"), t.get("alternateColor")
            if color and alt:
                out[int(t["id"])] = (color, alt)
        _TEAM_COLOR_CACHE[season] = out
    return _TEAM_COLOR_CACHE[season]


def fetch_ap_top25(season: int, week: int, *, season_type: str = "regular") -> dict[int, dict]:
    """
    { team_id -> {rank, school, conference} } for the real AP Top 25 poll
    (CFBD's own poll name is exactly "AP Top 25" — confirmed against a
    real 2025 week-3 response, NOT "Coaches Poll", which is a different
    real poll CFBD returns in the SAME /rankings response and would
    silently rank the wrong 25 teams if grabbed by position instead of by
    name) for one (season, week) — Top 25 TD Watch's eligibility source.

    One CFBD call: GET /rankings?year=&week=&seasonType=. PollRank rows
    already carry teamId directly (no team-name-string join needed, unlike
    /plays/stats' team/opponent strings — CFBD's rankings response is
    id-native).

    Returns {} (not an error) when the poll hasn't been released yet for
    that (season, week) — e.g. a future week, or a season/week combo
    before polling starts — same "real zero, not a crash" convention as
    every other CFBD fetcher in this package.
    """
    rows = cfbd_get("/rankings", {"year": int(season), "week": int(week), "seasonType": season_type})
    if not isinstance(rows, list):
        raise TypeError(f"/rankings did not return a list: {type(rows)!r}")

    out: dict[int, dict] = {}
    for poll_week in rows:
        for poll in poll_week.get("polls") or []:
            if poll.get("poll") != "AP Top 25":
                continue
            for r in poll.get("ranks") or []:
                tid = r.get("teamId")
                if tid is None:
                    continue
                out[int(tid)] = {
                    "rank": r.get("rank"),
                    "school": r.get("school"),
                    "conference": r.get("conference"),
                }
    return out
