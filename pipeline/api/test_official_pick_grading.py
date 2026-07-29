"""
Tests official_pick_grading.py against a REAL multi-game, multi-shelf pool
— no mocks. Reuses the same real cached pool test_shelf_curation.py uses
(/tmp/shelf_test_pool.json — 239 real candidates across 14 real completed
2026-07-28 games; regenerate with
`python3 pipeline/scripts/build_shelf_test_pool.py` if it's not present),
run through the real, already-tested `assign_shelves()` to produce a real
day's worth of official shelf picks, then graded against real final MLB
outcomes.

Run: python3 pipeline/api/test_official_pick_grading.py
"""
import json
from collections import Counter, defaultdict
from pathlib import Path

from official_pick_grading import grade_official_picks
from shelf_curation import assign_shelves

POOL_PATH = Path("/tmp/shelf_test_pool.json")
SEASON = 2026


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


if __name__ == "__main__":
    if not POOL_PATH.exists():
        print(f"SKIPPED — {POOL_PATH} not present in this environment. Regenerate with:\n"
              f"  python3 pipeline/scripts/build_shelf_test_pool.py")
        raise SystemExit(0)

    pool = json.loads(POOL_PATH.read_text())
    shelves = assign_shelves(pool, season=SEASON, shelf_size=8)

    # Flatten to the real shelf_assignments-shaped input this module
    # expects — one entry per (candidate, shelf) appearance, exactly the
    # grain the real Performance Tracker needs.
    picks = []
    for shelf_name, entries in shelves.items():
        for e in entries:
            c = e["candidate"]
            picks.append({"mlbam_id": c["mlbam_id"], "game_pk": c["game_pk"], "shelf": shelf_name, "player_name": c["player_name"]})

    print(f"Real official picks across all 6 shelves: {len(picks)}")
    unique_pairs = {(p["mlbam_id"], p["game_pk"]) for p in picks}
    print(f"Unique (mlbam_id, game_pk) pairs: {len(unique_pairs)}\n")

    results = []
    result = grade_official_picks(picks)

    print(f"errors: {result['errors']}")
    print(f"unique_games_graded: {result['unique_games_graded']}  picks_graded: {result['picks_graded']}")
    status_counts = Counter(r["status"] for r in result["results"])
    print(f"status breakdown: {dict(status_counts)}\n")

    # --- Every real completed game's pick graded cleanly, no errors ---
    results.append(check("zero errors grading real, known-valid completed games", len(result["errors"]) == 0))
    results.append(check("every input pick produced exactly one result", result["picks_graded"] == len(picks)))
    results.append(check("all real games are Final -> no pick graded as pending or void", status_counts.get("pending", 0) == 0 and status_counts.get("void", 0) == 0))
    results.append(check("at least one real official pick actually won (real HR)", status_counts.get("won", 0) > 0))
    results.append(check("at least one real official pick lost (sanity: not every pick wins)", status_counts.get("lost", 0) > 0))

    # --- The real efficiency + consistency property this module exists
    # for: a candidate on multiple shelves gets ONE real MLB lookup, fanned
    # out to multiple result rows, all reporting the identical verdict. ---
    results.append(check(
        "real dedup: unique_games_graded equals the real number of distinct (mlbam_id, game_pk) pairs, not the pick count",
        result["unique_games_graded"] == len(unique_pairs) < len(picks),
    ))

    by_key = defaultdict(list)
    for r in result["results"]:
        by_key[(r["mlbam_id"], r["game_pk"])].append(r)
    multi_shelf = {k: v for k, v in by_key.items() if len(v) > 1}

    print(f"Real candidates appearing on multiple shelves: {len(multi_shelf)}")
    for (mlbam_id, game_pk), rows in multi_shelf.items():
        name = next(p["player_name"] for p in picks if p["mlbam_id"] == mlbam_id and p["game_pk"] == game_pk)
        print(f"  {name}: shelves={[r['shelf'] for r in rows]} status={rows[0]['status']} home_runs={rows[0]['home_runs']}")

    results.append(check("at least one real candidate appeared on 2+ shelves in this real slate", len(multi_shelf) > 0))
    results.append(check(
        "every multi-shelf candidate's shelf appearances report an IDENTICAL real verdict — graded once, shown consistently everywhere",
        all(len({r["status"] for r in rows}) == 1 and len({r["home_runs"] for r in rows}) == 1 for rows in multi_shelf.values()),
    ))
    results.append(check(
        "each shelf appearance is still its own distinct result row (per-shelf tracking, not collapsed into one)",
        all(len(rows) == len({r["shelf"] for r in rows}) for rows in multi_shelf.values()),
    ))

    # --- Idempotency: the same real batch, graded twice, must be
    # byte-identical — same discipline as grade_pick() itself and the
    # duplicate-submission test that caught a real upsert bug earlier. ---
    result_again = grade_official_picks(picks)
    results.append(check(
        "grading the SAME real batch twice produces byte-identical results, errors, and counts",
        result["results"] == result_again["results"]
        and result["errors"] == result_again["errors"]
        and result["unique_games_graded"] == result_again["unique_games_graded"],
    ))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
