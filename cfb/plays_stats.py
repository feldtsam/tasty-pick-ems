"""
CFBD /games + /plays/stats fetchers — the cap-safe ingestion front door.

Flow for one (season, week), per the approved Step 1 proposal:

    1. GET /games?year=&week=&classification=fbs        # 1 call
    2. for each game where completed is true:
         GET /plays/stats?gameId={id}                   # ~1 call/game, ~200 rows

Never issues an unfiltered /plays/stats — cfbd_get() itself raises if any
single response comes back at the 2,000-row cap (see cfb/ids.py).

/plays/stats row shape (CFBD `PlayStat`, confirmed against the published
OpenAPI schema + the verification round):
    gameId season week team conference opponent teamScore opponentScore
    driveId playId period clock yardsToGoal down distance
    athleteId athleteName statType stat

`statType` is a human string ("Rush", "Target", "Reception", "Touchdown",
"Completion", "Incompletion", ...). This package only ever reads Rush /
Target / Touchdown (touch = Rush + Target per spec §8; Reception is
deliberately NOT a touch).
"""
from __future__ import annotations

# Flat import (not `from cfb.ids import ...`) — mirrors nfl/'s own
# convention: cfb/ is not imported as a package by the Vercel entry point
# (cfb/api/index.py), which sys.path-inserts this directory and imports
# these modules by bare name. cfb/__init__.py exists only so `python -m`
# / pytest rootdir behave; nothing here relies on package-relative imports.
from ids import cfbd_get

# statType strings this package cares about. Ids (Rush=7, Target=2,
# Reception=5, Touchdown=22) exist too but /plays/stats?gameId= returns the
# whole game, so filtering is done in memory on the string.
STAT_RUSH = "Rush"
STAT_TARGET = "Target"
STAT_TOUCHDOWN = "Touchdown"

DEFAULT_CLASSIFICATION = "fbs"
DEFAULT_SEASON_TYPE = "regular"


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
    rows = cfbd_get("/plays/stats", {"gameId": int(game_id), "seasonType": season_type})
    if not isinstance(rows, list):
        raise TypeError(f"/plays/stats?gameId={game_id} did not return a list: {type(rows)!r}")
    return rows


def fetch_week_play_stats(
    completed: list[dict],
    *,
    season_type: str = DEFAULT_SEASON_TYPE,
) -> tuple[list[dict], dict]:
    """
    Concatenate per-game /plays/stats for every completed game. Returns
    (all_rows, diagnostics). One game that 404s / errors is recorded in
    diagnostics and skipped — it does not take down the rest of the week
    (same per-item resilience the NFL pipeline's own loaders use).
    """
    all_rows: list[dict] = []
    per_game: list[dict] = []
    errors: list[dict] = []

    for g in completed:
        gid = int(g["id"])
        try:
            rows = fetch_play_stats_for_game(gid, season_type=season_type)
        except Exception as e:  # noqa: BLE001 — deliberately broad, see docstring
            errors.append({"game_id": gid, "error": f"{type(e).__name__}: {e}"})
            continue
        all_rows.extend(rows)
        per_game.append({"game_id": gid, "rows": len(rows)})

    diagnostics = {
        "completed_games": len(completed),
        "games_fetched": len(per_game),
        "games_errored": errors,
        "play_stat_rows_total": len(all_rows),
        "per_game_row_counts": per_game,
    }
    return all_rows, diagnostics
