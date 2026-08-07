"""
Grades every USER-SAVED pick (`bookmarks`) needing grading — the equivalent
of official_pick_grading.py for user-saved picks, deliberately separate per
the original design boundary (never shares a table or a query with official
picks; see official_pick_grading.py's own docstring).

REUSES, does not reimplement, `live_data.grading.grade_pick()` — same
function, unmodified, that already handles the real MLB Stats API lookup and
the 4-state (won/lost/void/pending) model.

THE ONE REAL DIFFERENCE FROM official_pick_grading.py: official picks fan out
per SHELF (a candidate can appear on multiple shelves); bookmarks fan out per
USER instead (many different users can save the same real player+game). The
underlying real-world fact (did this player hit a home run) can't differ by
who saved it, so the real MLB lookup for a given (mlbam_id, game_pk) is still
performed exactly ONCE and fanned out to every bookmark row that shares it —
same dedupe mechanism, different fan-out key (`id` instead of `shelf`).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "live_data"))

from grading import grade_pick  # noqa: E402


def grade_bookmarks(picks: list) -> dict:
    """
    picks: a list of bookmark-shaped dicts — each must have `id`, `mlbam_id`,
    `game_pk` (extra fields are ignored, harmless to include for caller
    convenience/logging).

    Returns:
      {
        "results": [
          {"id": str, "mlbam_id": int, "game_pk": ...,
           "status": "won"|"lost"|"void"|"pending",
           "home_runs": int|None, "plate_appearances": int|None,
           "reason": str, "game_detailed_state": str},
          ...  # one entry per INPUT pick that didn't error, same order
        ],
        "errors": [{"mlbam_id": int, "game_pk": ..., "error": str}, ...],
        "unique_games_graded": int,   # real MLB lookups actually performed
        "picks_graded": int,          # len(results)
      }
    """
    unique_keys = []
    seen = set()
    for p in picks:
        key = (p["mlbam_id"], p["game_pk"])
        if key not in seen:
            seen.add(key)
            unique_keys.append(key)

    cache = {}
    errors = []
    for mlbam_id, game_pk in unique_keys:
        try:
            cache[(mlbam_id, game_pk)] = grade_pick(mlbam_id, game_pk)
        except Exception as e:  # noqa: BLE001 — one bad game must not sink the whole batch
            errors.append({"mlbam_id": mlbam_id, "game_pk": game_pk, "error": f"{type(e).__name__}: {e}"})

    results = []
    for p in picks:
        key = (p["mlbam_id"], p["game_pk"])
        r = cache.get(key)
        if r is None:
            continue  # this (mlbam_id, game_pk) errored above, already recorded
        results.append({
            "id": p["id"],
            "mlbam_id": p["mlbam_id"],
            "game_pk": p["game_pk"],
            "status": r["status"],
            "home_runs": r["home_runs"],
            "plate_appearances": r["plate_appearances"],
            "reason": r["reason"],
            "game_detailed_state": r["game_detailed_state"],
        })

    return {
        "results": results,
        "errors": errors,
        "unique_games_graded": len(cache),
        "picks_graded": len(results),
    }
