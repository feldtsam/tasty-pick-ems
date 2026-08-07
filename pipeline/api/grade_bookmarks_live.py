"""
Ties bookmark_grading.py's batch-grading logic to a live source of "which
user-saved picks actually need grading right now" — the bookmarks equivalent
of grade_official_picks_live.py.

THE READ SIDE IS SIMPLER THAN OFFICIAL GRADING'S: official picks need an
anti-join across two tables (shelf_assignments vs official_pick_results)
because results live in a separate table. Bookmarks store their own grading
state inline (`result`, `graded_at`, `status` are columns on the bookmark row
itself), so bookmarks-needing-grading-read.ts is a single direct filter
(`result IS NULL AND locks_at <= now()`), not an anti-join — no lookback
window parameter to pass here.

THE WRITE SIDE IS GENUINELY DIFFERENT, NOT JUST A DIFFERENT URL:
bookmark-results-write.ts performs an UPDATE against the same `bookmarks`
row (matched by its own `id`, the primary key), never an insert/upsert into a
separate table — see that route's own comments for why this is still exactly
as scoped/safe as the insert-based writes despite being a mutation.
"""
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lovable_forward import compute_signature, forward_to_lovable, serialize_payload  # noqa: E402
from bookmark_grading import grade_bookmarks  # noqa: E402

REQUEST_TIMEOUT_SECONDS = 20

# Same discipline as grade_official_picks_live.py: "pending" is never
# forwarded. bookmark-results-write.ts's status enum is strict ("won"/"lost"/
# "void" only, same as official-pick-results-write.ts), so filtering here is
# what makes this module never even attempt a pending write, rather than
# relying on the write endpoint's schema as the only backstop.
TERMINAL_STATUSES = ("won", "lost", "void")


def fetch_bookmarks_needing_grading(secret: str, read_url: str) -> dict:
    """
    Calls Lovable's signed bookmarks-needing-grading-read endpoint. Reuses
    the same HMAC signing primitives every other signed call in this
    pipeline uses. No body fields — see module docstring for why this read
    has no window parameter, unlike the official-picks equivalent.
    """
    payload_str = serialize_payload({})
    signature = compute_signature(secret, payload_str)
    response = requests.post(
        read_url,
        data=payload_str.encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Signature": signature},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


def grade_bookmarks_for_pending(secret: str, read_url: str, write_url: str) -> dict:
    """
    The full chain: read (what needs grading) -> grade (real MLB lookups,
    deduplicated per unique game) -> filter to terminal results only ->
    write (forward to bookmark-results-write, which UPDATEs each bookmark
    row in place).

    Does not treat "zero picks need grading" as an error — same reasoning
    as grade_official_picks_for_pending: a real batch can legitimately be 0.

    Returns:
      {
        "error": str|None,
        "picks_needing_grading_count": int,
        "graded_count": int,             # terminal results, actually written
        "still_pending_count": int,      # graded games not yet final
        "grading_errors": [...],
        "results_written": [...],
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

    read_result = fetch_bookmarks_needing_grading(secret, read_url)

    if not read_result.get("ok"):
        return {**empty_result, "error": f"read endpoint returned an error: {read_result.get('error')}"}

    picks = read_result["bookmarks_needing_grading"]
    if not picks:
        return empty_result

    grading_result = grade_bookmarks(picks)
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
