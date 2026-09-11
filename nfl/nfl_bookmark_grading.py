"""
Grades a batch of NFL bookmark-shaped picks — the NFL equivalent of
pipeline/api/bookmark_grading.py, split into the same two real data
sources grading.py exposes (grade_pick_espn / grade_pick_nflverse), not
one function picking between them. See grading.py's own module docstring
for why that split exists and what each source can/can't return.

SAME FAN-OUT DISCIPLINE AS bookmark_grading.py: the real, underlying fact
("did this player score a real touchdown in this game") can't differ by
which user saved it, so each unique (player_key, event_key) is graded
exactly once and fanned out to every bookmark row sharing it — not
re-fetched per row.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

# grading.py lives in scripts/, this file lives at the top level (nfl/) --
# same sys.path dance stub_store.py already does to reach api/ from
# scripts/, just the other direction.
sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))

from grading import grade_pick_espn, grade_pick_nflverse  # noqa: E402


def _dedup_keys(picks: list) -> list[tuple]:
    seen = set()
    keys = []
    for p in picks:
        key = (p["player_key"], p["event_key"])
        if key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def grade_nfl_bookmarks_live(picks: list, schedules: pd.DataFrame) -> dict:
    """
    Same-night grading via ESPN (grade_pick_espn) — see that function's
    own docstring for why this can only ever produce "won" or "pending"
    results, never "lost"/"void". Callers filter to terminal statuses
    before writing (see nfl_grade_bookmarks_live.py), same as MLB's own
    orchestration does — "pending" here just means "not confirmed yet,"
    it is never forwarded.

    picks: bookmarks-needing-grading rows shaped like {id, player_key,
    event_key, player, team} (bookmarks-needing-grading-read.ts's NFL
    branch, sport: "NFL"). player/team are required for this source --
    ESPN has no gsis-id join (see grading.py).

    Returns the same shape as pipeline/api/bookmark_grading.py's
    grade_bookmarks(), field names adapted for this sport:
      {
        "results": [
          {"id": str, "player_key": str, "event_key": str,
           "status": "won"|"pending", "touchdowns": int|None,
           "reason": str, "espn_event_id": int|None},
          ...
        ],
        "errors": [{"player_key": str, "event_key": str, "error": str}, ...],
        "unique_games_graded": int,
        "picks_graded": int,
      }
    """
    cache = {}
    errors = []
    for player_key, event_key in _dedup_keys(picks):
        pick = next(p for p in picks if p["player_key"] == player_key and p["event_key"] == event_key)
        try:
            cache[(player_key, event_key)] = grade_pick_espn(
                player_key, pick["player"], pick["team"], event_key, schedules
            )
        except Exception as e:  # noqa: BLE001 — one bad game must not sink the whole batch
            errors.append({"player_key": player_key, "event_key": event_key, "error": f"{type(e).__name__}: {e}"})

    results = []
    for p in picks:
        key = (p["player_key"], p["event_key"])
        r = cache.get(key)
        if r is None:
            continue  # this (player_key, event_key) errored above, already recorded
        results.append({
            "id": p["id"],
            "player_key": p["player_key"],
            "event_key": p["event_key"],
            "status": r["status"],
            "touchdowns": r["touchdowns"],
            "reason": r["reason"],
            "espn_event_id": r.get("espn_event_id"),
        })

    return {
        "results": results,
        "errors": errors,
        "unique_games_graded": len(cache) + len(errors),
        "picks_graded": len(results),
    }


def grade_nfl_picks_live(bookmark_picks: list, shelf_picks: list, schedules: pd.DataFrame) -> dict:
    """
    NFL Official Recap, System (b), decision 4 -- "one shared grading
    determination, two destinations, not two separate grading runs".

    ESPN same-night grading over the UNION of bookmark-shaped picks
    (bookmarks-needing-grading-read.ts, sport: NFL) and shelf-shaped
    picks (nfl-shelf-picks-needing-grading-read.ts), deduplicated by
    (player_key/player_id, event_key/event_id) ACROSS BOTH sets combined
    -- a player+game graded once for a bookmark is never re-fetched from
    ESPN again just because the same real pick also has a shelf-level
    nfl_official_pick_results row to write, and vice versa. Everything
    else (dedup discipline, per-game error isolation) matches
    grade_nfl_bookmarks_live exactly -- this supersedes it as the real
    orchestration entry point; that function is kept, tested, and
    correct on its own, just no longer what the Flask routes call.

    bookmark_picks: {id, player_key, event_key, player, team} rows.
    shelf_picks: {player_id, event_id, shelf, player_name, team} rows.
    Identity is the same real player/game regardless of which source
    named it -- when a pair appears in both, the bookmark side's own
    player/team wins ESPN name-matching (arbitrary but deterministic;
    both describe the same real person).

    Returns {"bookmark_results": [...], "shelf_results": [...],
    "errors": [...], "unique_games_graded": int, "picks_graded": int} --
    bookmark_results/shelf_results carry the same per-source fields
    grade_nfl_bookmarks_live's "results" does (id or player_id/event_id/
    shelf, status, touchdowns, reason, espn_event_id).
    """
    name_team_by_key = {}
    for p in bookmark_picks:
        name_team_by_key.setdefault((p["player_key"], p["event_key"]), (p["player"], p["team"]))
    for p in shelf_picks:
        name_team_by_key.setdefault((p["player_id"], p["event_id"]), (p["player_name"], p["team"]))

    cache = {}
    errors = []
    for (player_key, event_key), (name, team) in name_team_by_key.items():
        try:
            cache[(player_key, event_key)] = grade_pick_espn(player_key, name, team, event_key, schedules)
        except Exception as e:  # noqa: BLE001 — one bad game must not sink the whole batch
            errors.append({"player_key": player_key, "event_key": event_key, "error": f"{type(e).__name__}: {e}"})

    bookmark_results = []
    for p in bookmark_picks:
        r = cache.get((p["player_key"], p["event_key"]))
        if r is None:
            continue
        bookmark_results.append({
            "id": p["id"],
            "player_key": p["player_key"],
            "event_key": p["event_key"],
            "status": r["status"],
            "touchdowns": r["touchdowns"],
            "reason": r["reason"],
            "espn_event_id": r.get("espn_event_id"),
        })

    shelf_results = []
    for p in shelf_picks:
        r = cache.get((p["player_id"], p["event_id"]))
        if r is None:
            continue
        shelf_results.append({
            "player_id": p["player_id"],
            "event_id": p["event_id"],
            "shelf": p["shelf"],
            "status": r["status"],
            "touchdowns": r["touchdowns"],
            "reason": r["reason"],
            "espn_event_id": r.get("espn_event_id"),
        })

    return {
        "bookmark_results": bookmark_results,
        "shelf_results": shelf_results,
        "errors": errors,
        "unique_games_graded": len(cache) + len(errors),
        "picks_graded": len(bookmark_results) + len(shelf_results),
    }


def grade_nfl_bookmarks_correction(
    picks: list,
    pbp: pd.DataFrame,
    schedules: pd.DataFrame,
    snap_counts: pd.DataFrame,
    id_crosswalk: pd.DataFrame,
) -> dict:
    """
    The Thursday correction pass, via grade_pick_nflverse — the
    authoritative 4-state grader (see grading.py). picks here are NOT
    necessarily only-ungraded rows the way grade_nfl_bookmarks_live's are:
    the caller (nfl_grade_bookmarks_correction.py) is expected to pass
    BOTH still-pending rows (never graded same-night) and already-"won"
    rows settled same-night (as a real confirmation/correction check, not
    just a fill-in-the-gaps pass) — this function itself doesn't care
    which, it just grades every (player_key, event_key) it's given.

    Returns the same shape as grade_nfl_bookmarks_live, with
    grade_pick_nflverse's own richer per-result fields
    (game_final) carried through instead of espn_event_id.
    """
    cache = {}
    errors = []
    for player_key, event_key in _dedup_keys(picks):
        try:
            cache[(player_key, event_key)] = grade_pick_nflverse(
                player_key, event_key, pbp, schedules, snap_counts, id_crosswalk
            )
        except Exception as e:  # noqa: BLE001
            errors.append({"player_key": player_key, "event_key": event_key, "error": f"{type(e).__name__}: {e}"})

    results = []
    for p in picks:
        key = (p["player_key"], p["event_key"])
        r = cache.get(key)
        if r is None:
            continue
        results.append({
            "id": p["id"],
            "player_key": p["player_key"],
            "event_key": p["event_key"],
            "status": r["status"],
            "touchdowns": r["touchdowns"],
            "reason": r["reason"],
            "game_final": r.get("game_final"),
        })

    return {
        "results": results,
        "errors": errors,
        "unique_games_graded": len(cache) + len(errors),
        "picks_graded": len(results),
    }


def grade_nfl_picks_correction(
    bookmark_picks: list,
    shelf_picks: list,
    pbp: pd.DataFrame,
    schedules: pd.DataFrame,
    snap_counts: pd.DataFrame,
    id_crosswalk: pd.DataFrame,
) -> dict:
    """
    NFL Official Recap, System (b), decision 4 -- the Thursday nflverse
    correction pass' own "one shared grading determination, two
    destinations" combination, via grade_pick_nflverse. Same dedup
    discipline as grade_nfl_picks_live, just across bookmark-shaped
    ({id, player_key, event_key}) and shelf-shaped ({player_id,
    event_id, shelf}) picks instead -- no player_name/team needed here,
    grade_pick_nflverse is id-native (see grading.py).

    Like grade_nfl_bookmarks_correction, picks here are NOT necessarily
    only-ungraded rows: both bookmark_picks and shelf_picks are expected
    to carry BOTH still-pending and already-"won" entries (a real
    confirmation/correction check), per each source's own read endpoint.

    Returns the same shape as grade_nfl_picks_live, with
    grade_pick_nflverse's own richer per-result field (game_final)
    carried through instead of espn_event_id.
    """
    keys = set()
    for p in bookmark_picks:
        keys.add((p["player_key"], p["event_key"]))
    for p in shelf_picks:
        keys.add((p["player_id"], p["event_id"]))

    cache = {}
    errors = []
    for player_key, event_key in keys:
        try:
            cache[(player_key, event_key)] = grade_pick_nflverse(
                player_key, event_key, pbp, schedules, snap_counts, id_crosswalk
            )
        except Exception as e:  # noqa: BLE001
            errors.append({"player_key": player_key, "event_key": event_key, "error": f"{type(e).__name__}: {e}"})

    bookmark_results = []
    for p in bookmark_picks:
        r = cache.get((p["player_key"], p["event_key"]))
        if r is None:
            continue
        bookmark_results.append({
            "id": p["id"],
            "player_key": p["player_key"],
            "event_key": p["event_key"],
            "status": r["status"],
            "touchdowns": r["touchdowns"],
            "reason": r["reason"],
            "game_final": r.get("game_final"),
        })

    shelf_results = []
    for p in shelf_picks:
        r = cache.get((p["player_id"], p["event_id"]))
        if r is None:
            continue
        shelf_results.append({
            "player_id": p["player_id"],
            "event_id": p["event_id"],
            "shelf": p["shelf"],
            "status": r["status"],
            "touchdowns": r["touchdowns"],
            "reason": r["reason"],
            "game_final": r.get("game_final"),
        })

    return {
        "bookmark_results": bookmark_results,
        "shelf_results": shelf_results,
        "errors": errors,
        "unique_games_graded": len(cache) + len(errors),
        "picks_graded": len(bookmark_results) + len(shelf_results),
    }
