"""
Per-player official current-season counting stats from the MLB Stats API
(`/people/{id}/stats`), fetched concurrently for just the players actually
in today's lineups/probable-pitcher slots.

Why per-player calls instead of one bulk request (investigated first,
rejected): the MLB Stats API's bulk `/api/v1/stats?stats=season&group=
hitting&...` leaderboard endpoint has a hidden qualification filter —
confirmed empirically: a `limit=2000` request against it returned only 147
"leader" players, every one with 322+ PA. A rookie or part-time lineup
player below that threshold simply isn't in the response, with no error or
indication of the filter. Per-player calls, keyed by the MLBAM ID already
in hand from the lineup/probable-pitcher data, avoid that trap entirely.

These are the OFFICIAL counting stats (plateAppearances, homeRuns for
hitters; inningsPitched, strikeOuts, homeRuns, battersFaced for pitchers) —
used for two things: (1) the sample-size gate in stat_selection.py
(current-season PA/IP vs. backtest's validated min_pa=100/min_ip=20), and
(2) computing hr_per_pa / hr_per_9 / k_per_9, none of which Baseball
Savant's leaderboards expose directly.
"""
from concurrent.futures import ThreadPoolExecutor

import requests

PEOPLE_STATS_URL = "https://statsapi.mlb.com/api/v1/people/{id}/stats"
PEOPLE_BATCH_URL = "https://statsapi.mlb.com/api/v1/people"
TIMEOUT_S = 15
MAX_WORKERS = 10
PEOPLE_BATCH_CHUNK = 100  # confirmed the batch endpoint accepts a comma-joined personIds list; chunked defensively rather than assuming no size cap


def parse_innings_pitched(ip_str) -> float:
    """MLB's innings-pitched notation uses ".1"/".2" for thirds of an
    inning (NOT decimal tenths) — "120.1" is 120 and 1/3 innings, not
    120.1 innings. "6.2" is 6 and 2/3, i.e. 6.667."""
    if ip_str is None or ip_str == "":
        return None
    whole_str, _, frac_str = str(ip_str).partition(".")
    whole = float(whole_str) if whole_str not in ("", "-") else 0.0
    thirds = {"": 0.0, "0": 0.0, "1": 1 / 3, "2": 2 / 3}.get(frac_str, 0.0)
    sign = -1.0 if whole_str.startswith("-") else 1.0
    return whole + sign * thirds


def _fetch_one(mlbam_id: int, group: str, season: int) -> dict:
    """Returns the raw `stat` dict for one player/group/season, or {} if
    the player has no stats in that group/season (confirmed real response
    shape: `{"stats": []}`, not an error) — never raises for a missing
    season, only for a genuine HTTP/network failure."""
    resp = requests.get(
        PEOPLE_STATS_URL.format(id=mlbam_id),
        params={"stats": "season", "group": group, "season": season},
        timeout=TIMEOUT_S,
    )
    resp.raise_for_status()
    data = resp.json()
    stats_blocks = data.get("stats", [])
    if not stats_blocks or not stats_blocks[0].get("splits"):
        return {}
    return stats_blocks[0]["splits"][0].get("stat", {})


def fetch_hitting_stats(mlbam_ids: list, season: int) -> dict:
    """Returns {mlbam_id (int): {"plate_appearances", "home_runs",
    "hr_per_pa"} or {} if no current-season MLB stats exist for that
    player}. Fetched concurrently — same ThreadPoolExecutor pattern already
    used in backtest/scripts/fetch_game_context.py for many individual
    per-player/per-game calls."""
    def _one(mlbam_id):
        stat = _fetch_one(mlbam_id, "hitting", season)
        if not stat:
            return mlbam_id, {}
        pa = stat.get("plateAppearances")
        hr = stat.get("homeRuns")
        return mlbam_id, {
            "plate_appearances": pa,
            "home_runs": hr,
            "hr_per_pa": (hr / pa) if pa else None,
        }

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        return dict(ex.map(_one, mlbam_ids))


def fetch_handedness(mlbam_ids: list) -> dict:
    """Batter stand / pitcher throws — not on the stats endpoint, lives on
    `/people` instead. Confirmed the batch form (`personIds=1,2,3`) returns
    all requested players in one call rather than needing one request per
    player. Returns {mlbam_id (int): {"bat_side": "L"/"R"/"S", "throws":
    "L"/"R"}}."""
    ids = list(dict.fromkeys(mlbam_ids))  # de-dupe, preserve order
    out = {}
    for i in range(0, len(ids), PEOPLE_BATCH_CHUNK):
        chunk = ids[i:i + PEOPLE_BATCH_CHUNK]
        resp = requests.get(PEOPLE_BATCH_URL, params={"personIds": ",".join(str(x) for x in chunk)}, timeout=TIMEOUT_S)
        resp.raise_for_status()
        for p in resp.json().get("people", []):
            out[p["id"]] = {
                "bat_side": (p.get("batSide") or {}).get("code"),
                "throws": (p.get("pitchHand") or {}).get("code"),
            }
    return out


def fetch_pitching_stats(mlbam_ids: list, season: int) -> dict:
    """Returns {mlbam_id (int): {"innings_pitched", "strikeouts",
    "home_runs_allowed", "batters_faced", "hr_per_9", "k_per_9"} or {} if
    no current-season MLB stats exist."""
    def _one(mlbam_id):
        stat = _fetch_one(mlbam_id, "pitching", season)
        if not stat:
            return mlbam_id, {}
        ip = parse_innings_pitched(stat.get("inningsPitched"))
        k = stat.get("strikeOuts")
        hr = stat.get("homeRuns")
        return mlbam_id, {
            "innings_pitched": ip,
            "strikeouts": k,
            "home_runs_allowed": hr,
            "batters_faced": stat.get("battersFaced"),
            "hr_per_9": (hr / ip * 9) if ip else None,
            "k_per_9": (k / ip * 9) if ip else None,
        }

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        return dict(ex.map(_one, mlbam_ids))
