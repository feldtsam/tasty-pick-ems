"""
CFBD /games + /plays/stats + /plays fetchers — the cap-safe ingestion
front door.

Flow for one (season, week), per the approved Step 1 proposal + the
2026-08-31 spec §8a/§8b amendments:

    1. GET /games?year=&week=&classification=fbs                  # 1 call
    2. GET /plays?year=&week=&classification=fbs&playType=TD      # 1 call -> TD play ids
    3. GET /plays/stats?gameId={id}  for each completed game      # ~90 calls, CONCURRENT
       (fetch_week_play_stats runs these in a thread pool)

Never issues an unfiltered /plays/stats — cfbd_get() raises if any single
response comes back at the 2,000-row cap (see cfb/ids.py). The
/plays?playType=TD call carries the same guard.

~94 CFBD calls, ~15s wall-clock for a full ~90-game week (spec §8b).
estimate_week_cost() projects both for a given slate.

/plays/stats row shape (CFBD `PlayStat`):
    gameId season week team conference opponent teamScore opponentScore
    driveId playId period clock yardsToGoal down distance
    athleteId athleteName statType stat

`statType` quirks confirmed against a live week (spec §8a): `Target` fires
only on INCOMPLETE targets, `Reception` on completed ones — so a pass
target is `Target` + `Reception`, and a touch is `Rush` + `Target` +
`Reception`. `statType='Touchdown'` is almost never emitted (3 rows in 8
games) — TDs come from /plays instead.
"""
from __future__ import annotations

# Flat import (not `from cfb.ids import ...`) — mirrors nfl/'s own
# convention: cfb/ is not imported as a package by the Vercel entry point
# (cfb/api/index.py), which sys.path-inserts this directory and imports
# these modules by bare name. cfb/__init__.py exists only so `python -m`
# / pytest rootdir behave; nothing here relies on package-relative imports.
from ids import cfbd_get

# statType strings this package reads. Filtering is done in memory on the
# string (a per-gameId /plays/stats pull returns the whole game).
STAT_RUSH = "Rush"
STAT_TARGET = "Target"          # receiver on an INCOMPLETE pass
STAT_RECEPTION = "Reception"    # receiver on a COMPLETED pass
STAT_TOUCHDOWN = "Touchdown"    # kept for the stat_type_distribution diagnostic only — not the TD source

# /plays playType values that are OFFENSIVE scrimmage touchdowns. The
# return / recovery / block touchdown types are defensive or special
# teams and are deliberately excluded — a touch row must never inherit a
# TD from one of those.
OFFENSIVE_TD_PLAY_TYPES = ("Rushing Touchdown", "Passing Touchdown")

DEFAULT_CLASSIFICATION = "fbs"
DEFAULT_SEASON_TYPE = "regular"

# Measured per-call wall-clock (CFBD, 2025 wk3 live probe), padded a
# little for slow weeks / cold connections. Used only by
# estimate_week_cost() — a guard against silent regression as game/roster
# counts grow, not a runtime dependency.
# Concurrency for the per-game /plays/stats fan-out (see fetch_week_play_stats).
# CFBD 429s ("Too many concurrent requests for this endpoint") at 8 — 8/70
# games failed a full-week run. 4 clears it (with the jittered 429 retry
# in cfbd_get as backstop) and still keeps the fan-out to ~10-15s.
PLAY_STATS_MAX_WORKERS = 4

_LAT = {
    "games": 0.8,
    "play_types": 0.3,
    "plays_td": 2.0,     # ~490 rows; 1.9s observed on a full week
    "roster": 1.5,       # ~15k rows; 1.2s observed
    "play_stats_call": 0.5,  # per-call incl. some 429 retry headroom at 4 workers
    "aggregation": 2.5,  # pandas over a full week's ~7-8k touch rows
    "overhead": 2.0,     # flask, json, pool spin-up, forward POSTs
}
VERCEL_MAX_DURATION_S = 120  # keep in sync with cfb/vercel.json


def estimate_week_cost(
    completed_games_count: int,
    *,
    workers: int = PLAY_STATS_MAX_WORKERS,
    roster_cached: bool = False,
) -> dict:
    """
    Rough CFBD call-count and wall-clock estimate for one weekly run, so a
    dry-run / test can flag when a full week is drifting toward the Vercel
    timeout before it actually starts failing in production.

    calls = /games + /plays/types + /plays?playType=TD (+ /roster unless
    cached) + one /plays/stats per completed game.
    wall  = the serial fixed calls + the parallelised /plays/stats
    fan-out (ceil(games/workers) batches) + aggregation + overhead.
    """
    import math

    fixed_calls = 3 + (0 if roster_cached else 1)
    total_calls = fixed_calls + completed_games_count

    w = max(1, workers)
    batches = math.ceil(completed_games_count / w) if completed_games_count else 0
    # 1.35x fudge: GIL + connection setup mean a batch of N doesn't run in
    # exactly one call's time.
    play_stats_wall = batches * _LAT["play_stats_call"] * 1.35

    serial_fixed = _LAT["games"] + _LAT["play_types"] + _LAT["plays_td"] + (
        0.0 if roster_cached else _LAT["roster"]
    )
    est_wall = round(serial_fixed + play_stats_wall + _LAT["aggregation"] + _LAT["overhead"], 1)

    return {
        "completed_games": completed_games_count,
        "workers": w,
        "roster_cached": roster_cached,
        "estimated_cfbd_calls": total_calls,
        "estimated_wall_clock_s": est_wall,
        "vercel_max_duration_s": VERCEL_MAX_DURATION_S,
        "fits_with_margin": est_wall < VERCEL_MAX_DURATION_S * 0.66,
        # Parallelisation keeps wall-clock well under the timeout even for a
        # huge slate, so the real ceiling is CFBD call volume: ~1 call per
        # completed game. >150 in a single run is a bowl-week / bad-input
        # signal worth a look (and a nudge toward the free-tier monthly cap).
        "high_call_volume": total_calls > 150,
    }


def fetch_games(
    season: int,
    week: int,
    *,
    classification: str = DEFAULT_CLASSIFICATION,
    season_type: str = DEFAULT_SEASON_TYPE,
) -> list[dict]:
    """
    All games for one (season, week). `completed` distinguishes a game
    whose play stats are final from one not yet played / in progress —
    callers filter on it.

    season_type defaults to "regular"; bowl / CFP weeks need a separate
    run with season_type="postseason" (a documented v1 limitation, not a
    bug — regular-season weeks are the entire v1 target).
    """
    params = {"year": season, "week": week, "seasonType": season_type}
    if classification:
        params["classification"] = classification
    games = cfbd_get("/games", params)
    if not isinstance(games, list):
        raise TypeError(f"/games did not return a list: {type(games)!r}")
    return games


def completed_games(games: list[dict]) -> list[dict]:
    """The subset whose play-by-play is final. CFBD publishes /plays/stats
    ~14h after a game ends (verification round), so a very recent game can
    be completed:true with stats still settling — that's acceptable for a
    manually-triggered run and idempotent re-runs (game_id natural key)
    correct it on the next pull."""
    return [g for g in games if g.get("completed") is True]


def fetch_play_stats_for_game(game_id: int, *, season_type: str = DEFAULT_SEASON_TYPE) -> list[dict]:
    """
    Every play-stat row for one game. gameId alone is a unique filter;
    ~150-350 rows, never near the cap.
    """
    rows = cfbd_get(
        "/plays/stats",
        {"gameId": int(game_id), "seasonType": season_type},
        truncation_guard=True,
    )
    if not isinstance(rows, list):
        raise TypeError(f"/plays/stats?gameId={game_id} did not return a list: {type(rows)!r}")
    return rows


def fetch_week_play_stats(
    completed: list[dict],
    *,
    season_type: str = DEFAULT_SEASON_TYPE,
    max_workers: int = PLAY_STATS_MAX_WORKERS,
) -> tuple[list[dict], dict]:
    """
    Per-game /plays/stats for every completed game, fetched concurrently.
    Returns (all_rows, diagnostics). One game that 404s / errors is
    recorded in diagnostics and skipped — it does not take down the rest
    of the week (same per-item resilience the NFL pipeline's loaders use).

    Ordering of `all_rows` is not deterministic across runs (games land as
    their fetches complete) — nothing downstream depends on it: both
    aggregations group by keys, and the write routes upsert on a natural
    key.
    """
    from concurrent.futures import ThreadPoolExecutor

    all_rows: list[dict] = []
    per_game: list[dict] = []
    errors: list[dict] = []

    def _one(g: dict) -> tuple[int, list[dict] | None, str | None]:
        gid = int(g["id"])
        try:
            return gid, fetch_play_stats_for_game(gid, season_type=season_type), None
        except Exception as e:  # noqa: BLE001 — deliberately broad, see docstring
            return gid, None, f"{type(e).__name__}: {e}"

    workers = max(1, min(max_workers, len(completed))) if completed else 1
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for gid, rows, err in pool.map(_one, completed):
            if err is not None:
                errors.append({"game_id": gid, "error": err})
                continue
            all_rows.extend(rows or [])
            per_game.append({"game_id": gid, "rows": len(rows or [])})

    diagnostics = {
        "completed_games": len(completed),
        "games_fetched": len(per_game),
        "games_errored": errors,
        "play_stat_rows_total": len(all_rows),
        "workers": workers,
        "per_game_row_counts": sorted(per_game, key=lambda x: x["game_id"]),
    }
    return all_rows, diagnostics


# --------------------------------------------------------------------------
# /plays — the touchdown source (spec §8a)
# --------------------------------------------------------------------------
def fetch_play_types() -> list[dict]:
    """CFBD /plays/types — [{id, text, abbreviation}]. Fetched once per run
    only for the diagnostic that lists which touchdown play types exist,
    so a future run notices if CFBD adds/renames an offensive TD type."""
    rows = cfbd_get("/plays/types")
    return rows if isinstance(rows, list) else []


# /plays' `playType` query param wants the CFBD *abbreviation*. Every
# touchdown play type — Rushing, Passing, and all four return/recovery
# types — shares the abbreviation "TD" (confirmed live via /plays/types).
# So `playType=TD` returns EVERY touchdown play for the week in one call
# (~450-600 rows for a full FBS week, ~0.25s), and the offensive subset
# is filtered in memory. Verified equivalent to the old per-team pull:
# identical td_play_ids set, identical /plays/stats intersection.
TD_PLAYTYPE_ABBREVIATION = "TD"


def fetch_scoring_td_play_ids(
    season: int,
    week: int,
    *,
    completed_game_ids: set[int] | None = None,
    classification: str = DEFAULT_CLASSIFICATION,
    season_type: str = DEFAULT_SEASON_TYPE,
) -> tuple[set[str], dict]:
    """
    The set of `playId`s that were OFFENSIVE touchdowns (playType in
    OFFENSIVE_TD_PLAY_TYPES) for the week — the TD source that replaces
    the almost-never-emitted `statType='Touchdown'` rows (spec §8a).

    One `/plays?playType=TD` call for the whole week, deduped on play
    `id`, filtered to offensive rushing/passing TDs (the return /
    recovery / block TD types are defensive or special teams and are
    excluded — a touch must never inherit one), and to
    `completed_game_ids` so the set lines up with the /plays/stats pull.

    Returns (td_play_ids, diagnostics).
    """
    all_td_types: list[str] = []
    try:
        all_td_types = sorted(
            {
                t["text"] for t in fetch_play_types()
                if isinstance(t.get("text"), str) and "touchdown" in t["text"].lower()
            }
        )
    except Exception as e:  # noqa: BLE001 — diagnostic only, never fatal
        all_td_types = [f"<play/types fetch failed: {type(e).__name__}: {e}>"]

    params = {
        "year": int(season), "week": int(week),
        "playType": TD_PLAYTYPE_ABBREVIATION, "seasonType": season_type,
    }
    if classification:
        params["classification"] = classification
    # truncation_guard: a 2,000-row result would mean TD plays are being
    # silently dropped — fail loud rather than under-count TDs.
    rows = cfbd_get("/plays", params, truncation_guard=True)
    rows = rows if isinstance(rows, list) else []

    seen_play_ids: set[str] = set()
    td_play_ids: set[str] = set()
    td_plays_detail: list[dict] = []
    play_type_counts: dict[str, int] = {}

    for r in rows:
        pid = r.get("id")
        if pid is None:
            continue
        pid = str(pid)
        if pid in seen_play_ids:
            continue
        seen_play_ids.add(pid)
        if r.get("scoring") is not True:
            continue
        pt = r.get("playType")
        play_type_counts[pt] = play_type_counts.get(pt, 0) + 1
        if pt not in OFFENSIVE_TD_PLAY_TYPES:
            continue
        if completed_game_ids is not None and r.get("gameId") not in completed_game_ids:
            continue
        td_play_ids.add(pid)
        if len(td_plays_detail) < 50:
            td_plays_detail.append(
                {
                    "id": pid, "gameId": r.get("gameId"), "playType": pt,
                    "yardsToGoal": r.get("yardsToGoal"), "offense": r.get("offense"),
                    "playText": (r.get("playText") or "")[:120],
                }
            )

    diagnostics = {
        "offensive_td_play_types_used": list(OFFENSIVE_TD_PLAY_TYPES),
        "all_touchdown_play_types_available": all_td_types,
        "plays_call": "/plays?playType=TD (1 call)",
        "plays_rows_returned": len(rows),
        "distinct_plays_seen": len(seen_play_ids),
        "scoring_play_type_counts": dict(sorted(play_type_counts.items(), key=lambda kv: -kv[1])),
        "td_play_ids": len(td_play_ids),
        "td_plays_detail": td_plays_detail,
    }
    return td_play_ids, diagnostics
