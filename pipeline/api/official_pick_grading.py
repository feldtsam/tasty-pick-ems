"""
Grades every OFFICIAL Tasty Pick Ems pick — everything in `scored_picks`/
`shelf_assignments` — for the Performance Tracker page. A deliberately
SEPARATE system from grading a user's individually saved pick: different
orchestrator (this file, not the one that will eventually exist for
saved picks), different result table (`official_pick_results`, drafted
below — never `bookmarks` or any future user-picks table), different
volume (every shelf-eligible candidate across all six shelves each day —
confirmed real: 239 candidates across a real 14-game slate this session —
versus a handful of individual user selections). Per explicit
instruction: official picks and user-saved picks must never share a
table or a query, and that boundary is kept at the code level here too,
not just the schema level — this module never imports from or writes
anything resembling a user-picks concept.

REUSES, does not reimplement, the core fact-checking logic:
`live_data.grading.grade_pick()` — the exact same function, unmodified,
that already handles the real MLB Stats API lookup, the always-refetch-
feed/live-fresh fix, and the 4-state (won/lost/void/pending) model. This
module is purely an ORCHESTRATION layer on top of it, mirroring how
`scored_picks.py` orchestrates `live_data`/`live_scoring` primitives
without reimplementing either.

THE ONE NEW THING HERE: a candidate can legitimately appear on multiple
shelves (confirmed and tested repeatedly this session — shelf_curation.py
deliberately allows it). Grading needs to produce one result PER SHELF
APPEARANCE for the Performance Tracker's per-shelf track record (e.g.
"Hot Hitters: 12-8" tracked separately from "Going Nuclear: 3-15" even for
the same real player/game) — but the underlying real-world fact (did this
player actually hit a home run in this game) cannot differ by shelf, so
the real MLB lookup for a given (mlbam_id, game_pk) is performed exactly
ONCE and fanned out to every shelf_assignments row that shares it, rather
than re-querying MLB's API once per shelf appearance. At real volume (a
candidate showing up on 2-3 of six shelves is common — confirmed in this
session's own real shelf-curation output) this meaningfully cuts real API
calls, and — more importantly — guarantees every shelf appearance of the
same real pick reports an IDENTICAL verdict, never a contradiction.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "live_data"))

from grading import grade_pick  # noqa: E402


def grade_official_picks(picks: list) -> dict:
    """
    picks: a list of shelf_assignments-shaped dicts — each must have
    `mlbam_id`, `game_pk`, and `shelf` (extra fields like `player_name`
    are ignored, harmless to include for caller convenience/logging).
    Typically the full day's shelf_assignments rows across all six
    shelves, supplied by whatever caller already has read access to them
    (this pipeline doesn't read Lovable's tables directly — same
    constraint as everywhere else here).

    Returns:
      {
        "results": [
          {"mlbam_id": int, "game_pk": ..., "shelf": str,
           "status": "won"|"lost"|"void"|"pending",
           "home_runs": int|None, "plate_appearances": int|None,
           "reason": str, "game_detailed_state": str},
          ...  # one entry per INPUT pick that didn't error, same order
        ],
        "errors": [{"mlbam_id": int, "game_pk": ..., "error": str}, ...],
        "unique_games_graded": int,   # real MLB lookups actually performed
        "picks_graded": int,          # len(results) — may be less than
                                      # len(picks) if some games errored
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
        except Exception as e:  # noqa: BLE001 — one bad game must not sink the whole day's grading batch
            errors.append({"mlbam_id": mlbam_id, "game_pk": game_pk, "error": f"{type(e).__name__}: {e}"})

    results = []
    for p in picks:
        key = (p["mlbam_id"], p["game_pk"])
        r = cache.get(key)
        if r is None:
            continue  # this (mlbam_id, game_pk) errored above, already recorded
        results.append({
            "mlbam_id": p["mlbam_id"],
            "game_pk": p["game_pk"],
            "shelf": p["shelf"],
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
