"""
Ties nfl_bookmark_grading.py's live (ESPN, same-night) grading to real
sources of "which NFL picks actually need grading right now" — the NFL
equivalent of pipeline/api/grade_bookmarks_live.py, extended for NFL
Official Recap (System (b), decision 4: "one shared grading
determination, two destinations, not two separate grading runs").

TWO READ SOURCES, ONE GRADING PASS:
  - bookmarks-needing-grading-read.ts ({"sport": "NFL"}) -- personal
    bookmarks needing grading (System (a), unchanged).
  - nfl-shelf-picks-needing-grading-read.ts -- approved nfl_content_drafts
    rows needing a shelf-level grade for the official recap (System (b)).
grade_nfl_picks_live (nfl_bookmark_grading.py) dedupes the UNION of both
by (player_key/player_id, event_key/event_id) before ever calling ESPN,
so a player+game shared by both sources is graded exactly once.

TWO WRITE DESTINATIONS: bookmark-results-write.ts (bookmarks.result) and
nfl-official-pick-results-write.ts (nfl_official_pick_results), each fed
from its own half of grade_nfl_picks_live's output. Neither write
depends on the other succeeding or failing.

shelf_read_url/shelf_write_url are optional (default None) so a caller
grading bookmarks alone (or a test exercising just that half) doesn't
have to supply System (b) infrastructure that may not exist yet --
omitting either one skips the shelf side entirely, bookmark grading
behaves exactly as it did before this file's own System (b) extension.

ONLY "won" IS EVER FORWARDED, on both destinations — grade_pick_espn()
(grading.py) can only return "won" or "pending" by design (see its own
module docstring for why: the ESPN player-identity join is name-matched,
not id-matched, so a missed/ambiguous match defers to the Thursday
nflverse correction pass rather than ever risking a wrong grade).
"pending" is filtered here, the same discipline
grade_official_picks_live.py/grade_bookmarks_live.py already use for
their own TERMINAL_STATUSES filter — this file never even attempts to
forward it, not relying on the write endpoint's status enum as the only
backstop.
"""
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from lovable_forward import compute_signature, forward_to_lovable, serialize_payload  # noqa: E402
from nfl_bookmark_grading import grade_nfl_picks_live  # noqa: E402
from backfill_redzone import load_schedules  # noqa: E402

REQUEST_TIMEOUT_SECONDS = 20

# See module docstring — grade_pick_espn() structurally cannot produce
# anything else, this is a defense-in-depth filter, not the only guard.
TERMINAL_STATUSES = ("won",)


def fetch_nfl_bookmarks_needing_grading(secret: str, read_url: str) -> dict:
    """Signed POST to bookmarks-needing-grading-read.ts with {"sport": "NFL"}."""
    body = {"sport": "NFL"}
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


def fetch_nfl_shelf_picks_needing_grading(secret: str, read_url: str) -> dict:
    """Signed POST to nfl-shelf-picks-needing-grading-read.ts. No body needed
    -- that endpoint takes none (see its own module docstring: unbounded,
    anti-join-only, no lookback window)."""
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


def grade_nfl_bookmarks_for_pending(
    secret: str,
    read_url: str,
    write_url: str,
    season: int,
    shelf_read_url: str = None,
    shelf_write_url: str = None,
) -> dict:
    """
    The full same-night chain: read (bookmarks needing grading, and --
    when shelf_read_url is given -- shelf picks needing grading) -> grade
    (real ESPN lookups, deduplicated per unique game ACROSS both sources
    combined) -> filter each side to "won" only -> write (forward each
    side to its own destination).

    `season` picks which nflverse schedules file to load for the
    game_id -> ESPN event id lookup (import_schedules() is per-season) --
    the caller (the Flask endpoint) resolves this the same way every
    other NFL endpoint already does (current week/season lookup), not
    hardcoded here.

    Returns the bookmark-side fields in the exact same shape/names
    grade_bookmarks_for_pending (MLB) and this function's own pre-System-
    (b) version used, plus a parallel shelf_* block that's all-zero/None
    when shelf_read_url is omitted.
    """
    empty_result = {
        "error": None,
        "picks_needing_grading_count": 0,
        "graded_count": 0,
        "still_pending_count": 0,
        "grading_errors": [],
        "results_written": [],
        "forwarded": None,
        "shelf_picks_needing_grading_count": 0,
        "shelf_graded_count": 0,
        "shelf_still_pending_count": 0,
        "shelf_results_written": [],
        "shelf_forwarded": None,
    }

    read_result = fetch_nfl_bookmarks_needing_grading(secret, read_url)
    if not read_result.get("ok"):
        return {**empty_result, "error": f"read endpoint returned an error: {read_result.get('error')}"}
    bookmark_picks = read_result["bookmarks_needing_grading"]

    shelf_picks = []
    if shelf_read_url:
        shelf_read_result = fetch_nfl_shelf_picks_needing_grading(secret, shelf_read_url)
        if not shelf_read_result.get("ok"):
            return {
                **empty_result,
                "error": f"shelf read endpoint returned an error: {shelf_read_result.get('error')}",
            }
        shelf_picks = shelf_read_result["shelf_picks_needing_grading"]

    if not bookmark_picks and not shelf_picks:
        return empty_result

    schedules = load_schedules([season])
    grading_result = grade_nfl_picks_live(bookmark_picks, shelf_picks, schedules)

    terminal = [r for r in grading_result["bookmark_results"] if r["status"] in TERMINAL_STATUSES]
    still_pending = [r for r in grading_result["bookmark_results"] if r["status"] not in TERMINAL_STATUSES]
    write_payload = [{"id": r["id"], "status": r["status"]} for r in terminal]
    forward_result = None
    if write_payload:
        forward_result = forward_to_lovable(write_payload, secret, write_url)

    shelf_terminal = [r for r in grading_result["shelf_results"] if r["status"] in TERMINAL_STATUSES]
    shelf_still_pending = [r for r in grading_result["shelf_results"] if r["status"] not in TERMINAL_STATUSES]
    shelf_forward_result = None
    if shelf_terminal and shelf_write_url:
        shelf_write_payload = [
            {"player_id": r["player_id"], "event_id": r["event_id"], "shelf": r["shelf"], "status": r["status"],
             "touchdowns": r["touchdowns"], "reason": r["reason"]}
            for r in shelf_terminal
        ]
        shelf_forward_result = forward_to_lovable(shelf_write_payload, secret, shelf_write_url)

    return {
        "error": None,
        "picks_needing_grading_count": len(bookmark_picks),
        "graded_count": len(terminal),
        "still_pending_count": len(still_pending),
        "grading_errors": grading_result["errors"],
        "results_written": terminal,
        "forwarded": forward_result,
        "shelf_picks_needing_grading_count": len(shelf_picks),
        "shelf_graded_count": len(shelf_terminal),
        "shelf_still_pending_count": len(shelf_still_pending),
        "shelf_results_written": shelf_terminal,
        "shelf_forwarded": shelf_forward_result,
    }
