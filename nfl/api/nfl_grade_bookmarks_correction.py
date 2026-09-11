"""
Ties nfl_bookmark_grading.py's correction (nflverse, Thursday-morning)
grading to real sources of "which NFL picks need re-checking against
authoritative data" — Step 2 of the NFL grading build, extended for NFL
Official Recap (System (b), decision 4: "one shared grading
determination, two destinations, not two separate grading runs"), same
extension nfl_grade_bookmarks_live.py got.

TWO READ SOURCES, ONE GRADING PASS:
  - bookmarks-needing-correction-read.ts -- NFL bookmarks whose result is
    still null or ESPN-confirmed "win" (System (a)).
  - nfl-shelf-picks-needing-correction-read.ts -- approved
    nfl_content_drafts rows whose nfl_official_pick_results status is
    still null or "won" (System (b)), same 9-day lookback window.
grade_nfl_picks_correction (nfl_bookmark_grading.py) dedupes the UNION of
both by (player_key/player_id, event_key/event_id) before ever calling
nflverse, so a player+game shared by both sources is graded exactly once.

TWO WRITE DESTINATIONS, SAME OLD-VS-NEW DECISION TABLE ON EACH SIDE
INDEPENDENTLY: bookmark-results-write.ts (bookmarks.result, keyed by row
id) and nfl-official-pick-results-write.ts (nfl_official_pick_results,
keyed by player_id/event_id/shelf). Each destination compares nflverse's
new status against ITS OWN prior status (the bookmark's old `result`, or
the shelf pick's old `status`) — the two destinations can genuinely
diverge (e.g. a bookmark never graded same-night while its matching
shelf pick was already ESPN-confirmed "won", if the shelf pick's kickoff
fell in-window a cycle earlier) so each gets its own correction: true
decision, not one shared flag:

  old status | new status     | action
  null       | won/lost/void  | normal write (no correction flag)
  null       | pending        | no write, re-checked later
  win/won    | won            | no write, nflverse agrees
  win/won    | lost/void      | write with correction: true
  win/won    | pending        | no write, leave the existing win alone

shelf_read_url/shelf_write_url are optional (default None), same
omit-to-skip convention as nfl_grade_bookmarks_live.py's own extension.
"""
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from lovable_forward import compute_signature, forward_to_lovable, serialize_payload  # noqa: E402
from nfl_bookmark_grading import grade_nfl_picks_correction  # noqa: E402
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


def fetch_nfl_shelf_picks_needing_correction(secret: str, shelf_read_url: str, lookback_days: int | None = None) -> dict:
    """Signed POST to nfl-shelf-picks-needing-correction-read.ts. Same
    lookback_days convention as fetch_nfl_bookmarks_needing_correction --
    omitted lets that endpoint apply its own default (9, matching the
    bookmark side so both passes cover the same real window)."""
    body: dict = {}
    if lookback_days is not None:
        body["lookback_days"] = lookback_days
    payload_str = serialize_payload(body)
    signature = compute_signature(secret, payload_str)
    response = requests.post(
        shelf_read_url,
        data=payload_str.encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Signature": signature},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


def _decide_write(status: str, old_status, won_value: str) -> tuple[bool, bool]:
    """
    Shared old-vs-new decision (see module docstring's table). Returns
    (should_write, is_correction). `won_value` is the old-status source's
    own spelling of a confirmed win -- "win" for bookmarks.result,
    "won" for nfl_official_pick_results.status -- so this one function
    serves both destinations' differently-spelled old-status columns.
    """
    if status == "pending":
        return False, False
    if old_status == won_value:
        if status == "won":
            return False, False
        return True, True
    return True, False


def _write_payload_for(results: list, old_result_by_id: dict) -> tuple[list, dict]:
    """Bookmark-side decision table application. Returns (write_payload, counts)."""
    write_payload = []
    newly_graded = corrected = unchanged = still_pending = 0

    for r in results:
        status = r["status"]
        old_result = old_result_by_id.get(r["id"])
        should_write, is_correction = _decide_write(status, old_result, "win")

        if status == "pending":
            still_pending += 1
            continue
        if not should_write:
            unchanged += 1
            continue

        row = {"id": r["id"], "status": status}
        if is_correction:
            row["correction"] = True
            corrected += 1
        else:
            newly_graded += 1
        write_payload.append(row)

    return write_payload, {
        "newly_graded_count": newly_graded,
        "corrected_count": corrected,
        "unchanged_count": unchanged,
        "still_pending_count": still_pending,
    }


def _shelf_write_payload_for(shelf_results: list, old_status_by_key: dict) -> tuple[list, dict]:
    """Shelf-side decision table application -- same table, keyed by
    (player_id, event_id, shelf) and compared against nfl_official_pick_
    results' own "won" spelling instead of bookmarks.result's "win"."""
    write_payload = []
    newly_graded = corrected = unchanged = still_pending = 0

    for r in shelf_results:
        status = r["status"]
        key = (r["player_id"], r["event_id"], r["shelf"])
        old_status = old_status_by_key.get(key)
        should_write, is_correction = _decide_write(status, old_status, "won")

        if status == "pending":
            still_pending += 1
            continue
        if not should_write:
            unchanged += 1
            continue

        row = {
            "player_id": r["player_id"], "event_id": r["event_id"], "shelf": r["shelf"],
            "status": status, "touchdowns": r["touchdowns"], "reason": r["reason"],
        }
        if is_correction:
            row["correction"] = True
            corrected += 1
        else:
            newly_graded += 1
        write_payload.append(row)

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
    shelf_read_url: str | None = None,
    shelf_write_url: str | None = None,
) -> dict:
    """
    The full Thursday-morning chain: read (bookmarks, and -- when
    shelf_read_url is given -- shelf picks, both whose games recently
    happened and are either ungraded or ESPN-confirmed "win"/"won") ->
    grade (real nflverse lookups, deduplicated per unique game ACROSS
    both sources combined) -> decide per row whether a write is needed
    and whether it's a correction, independently per destination -> write
    (forward each side to its own destination, correction: true set
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
        "shelf_picks_needing_correction_count": 0,
        "shelf_newly_graded_count": 0,
        "shelf_corrected_count": 0,
        "shelf_unchanged_count": 0,
        "shelf_still_pending_count": 0,
        "shelf_results_written": [],
        "shelf_forwarded": None,
    }

    read_result = fetch_nfl_bookmarks_needing_correction(secret, read_url, lookback_days)
    if not read_result.get("ok"):
        return {**empty_result, "error": f"read endpoint returned an error: {read_result.get('error')}"}
    bookmark_picks = read_result["bookmarks_needing_correction"]
    old_result_by_id = {p["id"]: p["result"] for p in bookmark_picks}

    shelf_picks = []
    old_status_by_key = {}
    if shelf_read_url:
        shelf_read_result = fetch_nfl_shelf_picks_needing_correction(secret, shelf_read_url, lookback_days)
        if not shelf_read_result.get("ok"):
            return {
                **empty_result,
                "error": f"shelf read endpoint returned an error: {shelf_read_result.get('error')}",
            }
        shelf_picks = shelf_read_result["shelf_picks_needing_correction"]
        old_status_by_key = {(p["player_id"], p["event_id"], p["shelf"]): p["status"] for p in shelf_picks}

    if not bookmark_picks and not shelf_picks:
        return empty_result

    pbp = load_pbp([season])
    schedules = load_schedules([season])
    snap_counts = load_snap_counts([season])
    id_crosswalk = load_id_crosswalk([season])

    grading_result = grade_nfl_picks_correction(bookmark_picks, shelf_picks, pbp, schedules, snap_counts, id_crosswalk)

    write_payload, counts = _write_payload_for(grading_result["bookmark_results"], old_result_by_id)
    forward_result = None
    if write_payload:
        forward_result = forward_to_lovable(write_payload, secret, write_url)

    shelf_write_payload, shelf_counts = _shelf_write_payload_for(grading_result["shelf_results"], old_status_by_key)
    shelf_forward_result = None
    if shelf_write_payload and shelf_write_url:
        shelf_forward_result = forward_to_lovable(shelf_write_payload, secret, shelf_write_url)

    return {
        "error": None,
        "picks_needing_correction_count": len(bookmark_picks),
        "newly_graded_count": counts["newly_graded_count"],
        "corrected_count": counts["corrected_count"],
        "unchanged_count": counts["unchanged_count"],
        "still_pending_count": counts["still_pending_count"],
        "grading_errors": grading_result["errors"],
        "results_written": write_payload,
        "forwarded": forward_result,
        "shelf_picks_needing_correction_count": len(shelf_picks),
        "shelf_newly_graded_count": shelf_counts["newly_graded_count"],
        "shelf_corrected_count": shelf_counts["corrected_count"],
        "shelf_unchanged_count": shelf_counts["unchanged_count"],
        "shelf_still_pending_count": shelf_counts["still_pending_count"],
        "shelf_results_written": shelf_write_payload,
        "shelf_forwarded": shelf_forward_result,
    }
