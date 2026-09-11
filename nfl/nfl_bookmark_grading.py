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
