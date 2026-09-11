"""
Ties nfl_bookmark_grading.py's correction (nflverse, Thursday-morning)
grading to a real source of "which NFL bookmarks need re-checking against
authoritative data" — Step 2 of the NFL grading build, deferred out of
System (a) because this read query is genuinely shaped differently from
the same-night one (see bookmarks-needing-correction-read.ts's own module
docstring in the tastypickems repo).

READ SIDE: calls bookmarks-needing-correction-read.ts, which returns
NFL bookmarks whose game recently happened AND whose result is either
still NULL (grade_pick_espn left it pending) or already "win"
(ESPN-confirmed, still worth re-checking since grade_pick_espn's identity
join is name-matched, not id-matched). Rows come back shaped
{id, player_key, event_key, result} — `result` is carried through
specifically so this file can tell "never graded" from "ESPN said win"
without a second round trip.

GRADE SIDE: grade_nfl_bookmarks_correction (nfl_bookmark_grading.py) via
grade_pick_nflverse — the authoritative, id-native, 4-state grader. Unlike
the live pass, this one is fed BOTH still-pending and already-"win" rows
on purpose, as a real confirmation/correction check, not just a
fill-in-the-gaps pass.

WRITE SIDE: forwards to the SAME bookmark-results-write.ts endpoint the
live pass uses. The decision of whether a row needs `correction: true`
is made HERE, by comparing nflverse's new status against the old
`result` value carried through from the read side:

  old result | new status | action
  -----------|------------|----------------------------------------------
  null       | won/lost/  | normal write (no correction flag — nothing to
             | void       | overwrite, bookmark-results-write's own
             |            | .is("result", null) guard passes naturally)
  null       | pending    | no write — still unresolved, next Thursday's
             |            | run (or this same run, next lookback window)
             |            | re-checks it
  win        | won        | no write — nflverse agrees with ESPN, nothing
             |            | to correct
  win        | lost/void  | write with correction: true — nflverse
             |            | overturns ESPN's same-night "win"
  win        | pending    | no write — never observed in practice (a
             |            | player ESPN could name-match already has an
             |            | id-native game to look up), but if nflverse
             |            | itself can't resolve the game yet (e.g. not
             |            | final), leaving the existing "win" alone is
             |            | the safe default rather than blanking it
"""
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from lovable_forward import compute_signature, forward_to_lovable, serialize_payload  # noqa: E402
from nfl_bookmark_grading import grade_nfl_bookmarks_correction  # noqa: E402
from backfill_redzone import load_id_crosswalk, load_pbp, load_schedules, load_snap_counts  # noqa: E402

REQUEST_TIMEOUT_SECONDS = 20


def fetch_nfl_bookmarks_needing_correction(secret: str, read_url: str, lookback_days: int | None = None) -> dict:
    """Signed POST to bookmarks-needing-correction-read.ts. `lookback_days`
    omitted lets the endpoint apply its own default (9, as of this build —
    see that endpoint's docstring for why, and that the number is
    provisional pending Sam's confirmation)."""
    body: dict = {}
    if lookback_days is not None:
        body["lookback_days"] = lookback_days
    payload_str = serialize_payload(body)
    signature = compute_signature(secret, payload_str)
    response = requests.post(
        read_url,
        data=payload_str.encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Signature": signature},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


def _write_payload_for(results: list, old_result_by_id: dict) -> tuple[list, dict]:
    """
    Applies the old-result-vs-new-status table from the module docstring.
    Returns (write_payload, counts) where counts breaks down what happened
    to every graded row, for the caller's own return shape.
    """
    write_payload = []
    newly_graded = 0
    corrected = 0
    unchanged = 0
    still_pending = 0

    for r in results:
        status = r["status"]
        old_result = old_result_by_id.get(r["id"])

        if status == "pending":
            still_pending += 1
            continue

        if old_result == "win":
            if status == "won":
                unchanged += 1
                continue
            write_payload.append({"id": r["id"], "status": status, "correction": True})
            corrected += 1
        else:
            # old_result is None (never graded) -- normal write, no
            # correction flag needed since there's nothing to overwrite.
            write_payload.append({"id": r["id"], "status": status})
            newly_graded += 1

    return write_payload, {
        "newly_graded_count": newly_graded,
        "corrected_count": corrected,
        "unchanged_count": unchanged,
        "still_pending_count": still_pending,
    }


def grade_nfl_bookmarks_for_correction(
    secret: str,
    read_url: str,
    write_url: str,
    season: int,
    lookback_days: int | None = None,
) -> dict:
    """
    The full Thursday-morning chain: read (NFL bookmarks whose games
    recently happened and are either ungraded or ESPN-confirmed "win") ->
    grade (real nflverse lookups, deduplicated per unique game) -> decide
    per row whether a write is needed and whether it's a correction ->
    write (forward to bookmark-results-write, with correction: true set
    per-row as appropriate).

    `season` picks which nflverse data to load (pbp, schedules, snap
    counts, id crosswalk are all per-season) -- resolved by the caller
    (the Flask endpoint) the same way every other NFL endpoint already
    does, not hardcoded here.
    """
    empty_result = {
        "error": None,
        "picks_needing_correction_count": 0,
        "newly_graded_count": 0,
        "corrected_count": 0,
        "unchanged_count": 0,
        "still_pending_count": 0,
        "grading_errors": [],
        "results_written": [],
        "forwarded": None,
    }

    read_result = fetch_nfl_bookmarks_needing_correction(secret, read_url, lookback_days)

    if not read_result.get("ok"):
        return {**empty_result, "error": f"read endpoint returned an error: {read_result.get('error')}"}

    picks = read_result["bookmarks_needing_correction"]
    if not picks:
        return empty_result

    old_result_by_id = {p["id"]: p["result"] for p in picks}

    pbp = load_pbp([season])
    schedules = load_schedules([season])
    snap_counts = load_snap_counts([season])
    id_crosswalk = load_id_crosswalk([season])

    grading_result = grade_nfl_bookmarks_correction(picks, pbp, schedules, snap_counts, id_crosswalk)
    write_payload, counts = _write_payload_for(grading_result["results"], old_result_by_id)

    forward_result = None
    if write_payload:
        forward_result = forward_to_lovable(write_payload, secret, write_url)

    return {
        "error": None,
        "picks_needing_correction_count": len(picks),
        "newly_graded_count": counts["newly_graded_count"],
        "corrected_count": counts["corrected_count"],
        "unchanged_count": counts["unchanged_count"],
        "still_pending_count": counts["still_pending_count"],
        "grading_errors": grading_result["errors"],
        "results_written": write_payload,
        "forwarded": forward_result,
    }
