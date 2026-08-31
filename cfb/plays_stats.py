"""
CFBD /games + /plays/stats + /plays fetchers — the cap-safe ingestion
front door.

Flow for one (season, week), per the approved Step 1 proposal + the
2026-08-31 spec §8a amendment:

    1. GET /games?year=&week=&classification=fbs                  # 1 call
    2. for each game where completed is true:
         GET /plays/stats?gameId={id}                             # ~1 call/game
    3. GET /plays?year=&week=&classification=fbs&playType={t}      # ~2 calls
         for t in ("Rushing Touchdown", "Passing Touchdown")      #   -> TD play ids

Never issues an unfiltered /plays/stats — cfbd_get() raises if any single
response comes back at the 2,000-row cap (see cfb/ids.py). /plays is
filtered by playType so its per-call result stays well under the cap too.

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


# --------------------------------------------------------------------------
# /plays — the touchdown source (spec §8a)
# --------------------------------------------------------------------------
def fetch_play_types() -> list[dict]:
    """CFBD /plays/types — [{id, text, abbreviation}]. Fetched once per run
    only for the diagnostic that lists which touchdown play types exist,
    so a future run notices if CFBD adds/renames an offensive TD type."""
    rows = cfbd_get("/plays/types")
    return rows if isinstance(rows, list) else []


def fetch_plays_for_team(
    season: int,
    week: int,
    team: str,
    *,
    classification: str = DEFAULT_CLASSIFICATION,
    season_type: str = DEFAULT_SEASON_TYPE,
) -> list[dict]:
    """
    /plays for one (season, week) restricted to one team (either side of
    the play) — ~140-180 rows, well under the 2,000-row cap. /plays has no
    gameId filter and its `playType` filter wants an abbreviation this
    package would have to guess, so per-team + in-memory filtering is the
    robust path. Each `Play` row carries `id` (== /plays/stats `playId`),
    `gameId`, `scoring` (bool), `playType`, `offense`, `defense`,
    `yardsToGoal`, `playText`.
    """
    params = {"year": int(season), "week": int(week), "team": team, "seasonType": season_type}
    if classification:
        params["classification"] = classification
    rows = cfbd_get("/plays", params)
    return rows if isinstance(rows, list) else []


def fetch_scoring_td_play_ids(
    season: int,
    week: int,
    teams: list[str],
    *,
    completed_game_ids: set[int] | None = None,
    classification: str = DEFAULT_CLASSIFICATION,
    season_type: str = DEFAULT_SEASON_TYPE,
) -> tuple[set[str], dict]:
    """
    The set of `playId`s that were OFFENSIVE touchdowns (playType in
    OFFENSIVE_TD_PLAY_TYPES) for the given teams' games — the TD source
    that replaces the almost-never-emitted `statType='Touchdown'` rows
    (spec §8a). Pull /plays once per team, dedupe on play `id`, keep only
    scoring plays whose playType is an offensive rushing/passing TD (the
    return / recovery / block TD types are defensive or special teams and
    are excluded — a touch must never inherit one).

    Restricted to `completed_game_ids` when given, so the set lines up
    with the games the /plays/stats pull covers.

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

    seen_play_ids: set[str] = set()
    td_play_ids: set[str] = set()
    td_plays_detail: list[dict] = []
    play_type_counts: dict[str, int] = {}
    teams_fetched = 0
    plays_seen = 0

    for team in sorted(set(teams)):
        rows = fetch_plays_for_team(season, week, team, classification=classification, season_type=season_type)
        teams_fetched += 1
        for r in rows:
            pid = r.get("id")
            if pid is None:
                continue
            pid = str(pid)
            if pid in seen_play_ids:
                continue
            seen_play_ids.add(pid)
            plays_seen += 1
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
        "teams_fetched": teams_fetched,
        "distinct_plays_seen": plays_seen,
        "scoring_play_type_counts": dict(sorted(play_type_counts.items(), key=lambda kv: -kv[1])),
        "td_play_ids": len(td_play_ids),
        "td_plays_detail": td_plays_detail,
    }
    return td_play_ids, diagnostics
