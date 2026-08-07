"""
Tests bookmark_grading.py against the same REAL multi-game candidate pool
test_official_pick_grading.py uses — no mocks. Reuses
/tmp/shelf_test_pool.json (239 real candidates across 14 real completed
2026-07-28 games; regenerate with
`python3 pipeline/scripts/build_shelf_test_pool.py` if it's not present).

Unlike official picks (which fan out per SHELF), bookmarks fan out per USER —
simulated here by giving a handful of real candidates 2-3 synthetic bookmark
ids each, standing in for different real users saving the same real pick.

Run: python3 pipeline/api/test_bookmark_grading.py
"""
import json
import uuid
from collections import Counter, defaultdict
from pathlib import Path

from bookmark_grading import grade_bookmarks

POOL_PATH = Path("/tmp/shelf_test_pool.json")


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

    # Real candidates, deduplicated by (mlbam_id, game_pk) — a real pool can
    # list the same candidate more than once across different odds ties.
    seen = set()
    candidates = []
    for c in pool:
        key = (c["mlbam_id"], c["game_pk"])
        if key not in seen:
            seen.add(key)
            candidates.append(c)

    # Simulate real per-user fan-out: the first 5 real candidates each get 2
    # synthetic bookmark rows (2 different "users" saving the same real
    # pick); the rest get exactly 1.
    picks = []
    for i, c in enumerate(candidates):
        n_users = 2 if i < 5 else 1
        for _ in range(n_users):
            picks.append({"id": str(uuid.uuid4()), "mlbam_id": c["mlbam_id"], "game_pk": c["game_pk"]})

    print(f"Real bookmark rows (simulated per-user fan-out): {len(picks)}")
    unique_pairs = {(p["mlbam_id"], p["game_pk"]) for p in picks}
    print(f"Unique (mlbam_id, game_pk) pairs: {len(unique_pairs)}\n")

    results = []
    result = grade_bookmarks(picks)

    print(f"errors: {result['errors']}")
    print(f"unique_games_graded: {result['unique_games_graded']}  picks_graded: {result['picks_graded']}")
    status_counts = Counter(r["status"] for r in result["results"])
    print(f"status breakdown: {dict(status_counts)}\n")

    results.append(check("zero errors grading real, known-valid completed games", len(result["errors"]) == 0))
    results.append(check("every input pick produced exactly one result", result["picks_graded"] == len(picks)))
    results.append(check("all real games are Final -> no pick graded as pending", status_counts.get("pending", 0) == 0))
    results.append(check("at least one real bookmark actually won (real HR)", status_counts.get("won", 0) > 0))
    results.append(check("at least one real bookmark lost (sanity: not every pick wins)", status_counts.get("lost", 0) > 0))

    # --- The real property this module exists for: the same real pick
    # saved by multiple different users gets ONE real MLB lookup, fanned
    # out to multiple bookmark result rows, all reporting the identical
    # verdict — and each row keeps its own distinct id. ---
    results.append(check(
        "real dedup: unique_games_graded equals the real number of distinct (mlbam_id, game_pk) pairs, not the bookmark count",
        result["unique_games_graded"] == len(unique_pairs) < len(picks),
    ))

    by_key = defaultdict(list)
    for r in result["results"]:
        by_key[(r["mlbam_id"], r["game_pk"])].append(r)
    multi_user = {k: v for k, v in by_key.items() if len(v) > 1}

    print(f"Real candidates saved by 2+ simulated users: {len(multi_user)}")
    results.append(check("at least one real candidate was saved by 2+ simulated users in this batch", len(multi_user) > 0))
    results.append(check(
        "every multi-user candidate's saves report an IDENTICAL real verdict — graded once, shown consistently everywhere",
        all(len({r["status"] for r in rows}) == 1 and len({r["home_runs"] for r in rows}) == 1 for rows in multi_user.values()),
    ))
    results.append(check(
        "each saved bookmark keeps its own distinct id (per-user tracking, not collapsed into one)",
        all(len({r["id"] for r in rows}) == len(rows) for rows in multi_user.values()),
    ))

    # --- Idempotency: the same real batch, graded twice, must be
    # byte-identical — same discipline as grade_pick() itself. ---
    result_again = grade_bookmarks(picks)
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
