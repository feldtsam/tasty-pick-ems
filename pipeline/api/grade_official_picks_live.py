"""
Ties official_pick_grading.py's existing batch-grading logic to a live
source of "which official picks actually need grading right now" — the
last piece connecting the official-picks grading system to real data.

WHY THIS IS AN ANTI-JOIN, NOT A GAME-STATUS PRE-FILTER: an earlier design
question was whether to first check which games have finished, then only
fetch shelf_assignments for those games. Rejected — grade_pick() (in
live_data/grading.py, reused unmodified here via official_pick_grading.py)
already does a fresh feed/live lookup per unique (mlbam_id, game_pk) and
returns status="pending" for anything not yet final. A separate upfront
game-status check would be a second live MLB lookup per game for
information grading already produces as a side effect of doing its real
job. Instead: pull every shelf_assignments row that doesn't yet have a
matching official_pick_results row (regardless of whether the underlying
game has finished), grade all of them, and only forward the TERMINAL
results (won/lost/void) to the write step. A still-in-progress game simply
produces no official_pick_results row this run — which means it's still
"ungraded" next run, with no separate tracking needed. This is the same
idempotency mechanism as the anti-join itself: nothing marks a pick as
"attempted", only "graded".

THE ANTI-JOIN ITSELF lives in the new Lovable-side read endpoint
(picks-needing-grading-read.ts — see pipeline README), not here: it
queries shelf_assignments rows with no matching official_pick_results row
on the real (mlbam_id, game_pk, shelf) unique index, bounded by a lookback
window. This module only calls that endpoint, filters the results, and
forwards.

WHY A DEDICATED READ ENDPOINT, NOT AN EXTENSION OF scored-picks-read.ts:
different grain (shelf_assignments rows, not scored_picks rows) and a
fundamentally different filter (an anti-join against a second table, not
a date window) — reusing the HMAC/verification boilerplate is right,
reusing the query shape would not be.
"""
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lovable_forward import compute_signature, forward_to_lovable, serialize_payload  # noqa: E402
from official_pick_grading import grade_official_picks  # noqa: E402

REQUEST_TIMEOUT_SECONDS = 20

# Only these statuses represent a real, final determination — the only
# ones that ever belong in official_pick_results. "pending" is
# deliberately never forwarded (see module docstring): letting it reach
# the write endpoint would actually be rejected there anyway (status is a
# strict enum on that side), but filtering here is what makes THIS module
# never even attempt it, rather than relying on the write endpoint as the
# only backstop.
TERMINAL_STATUSES = ("won", "lost", "void")


def fetch_picks_needing_grading(secret: str, read_url: str, lookback_days: int = None) -> dict:
    """
    Calls Lovable's signed picks-needing-grading-read endpoint. Reuses the
    same HMAC signing primitives every other signed call in this pipeline
    uses. `lookback_days` is optional — omitted entirely from the body
    when None, letting the Lovable route apply its own default.
    """
    body = {} if lookback_days is None else {"lookback_days": lookback_days}
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


def grade_official_picks_for_pending(secret: str, read_url: str, write_url: str, lookback_days: int = None) -> dict:
    """
    The full chain: read (what needs grading) -> grade (real MLB lookups,
    deduplicated per unique game) -> filter to terminal results only ->
    write (forward to official_pick_results).

    Does not treat "zero picks need grading" or "some picks are still
    pending" as errors — both are normal, expected outcomes of this
    design, not signs something broke (unlike curate_shelves.py, this
    module has no minimum-count sanity gate; a real grading batch can
    legitimately be 0, 2, or 200 depending on how many games just wrapped
    up since the last run).

    Returns:
      {
        "error": str|None,               # only set on a genuine failure
                                          # (read endpoint unreachable/erroring)
        "picks_needing_grading_count": int,
        "graded_count": int,             # terminal results, actually written
        "still_pending_count": int,      # graded games not yet final
        "grading_errors": [...],         # per-game errors from grade_official_picks
        "results_written": [...],        # the terminal results forwarded
        "forwarded": {"success":..., "status_code":..., "error":...} | None,
      }
    """
    empty_result = {
        "error": None,
        "picks_needing_grading_count": 0,
        "graded_count": 0,
        "still_pending_count": 0,
        "grading_errors": [],
        "results_written": [],
        "forwarded": None,
    }

    read_result = fetch_picks_needing_grading(secret, read_url, lookback_days)

    if not read_result.get("ok"):
        return {**empty_result, "error": f"read endpoint returned an error: {read_result.get('error')}"}

    picks = read_result["picks_needing_grading"]
    if not picks:
        return empty_result

    grading_result = grade_official_picks(picks)
    terminal = [r for r in grading_result["results"] if r["status"] in TERMINAL_STATUSES]
    still_pending = [r for r in grading_result["results"] if r["status"] not in TERMINAL_STATUSES]

    forward_result = None
    if terminal:
        forward_result = forward_to_lovable(terminal, secret, write_url)

    return {
        "error": None,
        "picks_needing_grading_count": len(picks),
        "graded_count": len(terminal),
        "still_pending_count": len(still_pending),
        "grading_errors": grading_result["errors"],
        "results_written": terminal,
        "forwarded": forward_result,
    }
