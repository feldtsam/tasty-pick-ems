"""
Ties nfl_bookmark_grading.py's live (ESPN, same-night) grading to a real
source of "which NFL bookmarks actually need grading right now" — the NFL
equivalent of pipeline/api/grade_bookmarks_live.py.

READ SIDE: calls the SAME bookmarks-needing-grading-read.ts endpoint MLB's
grading already calls, but with {"sport": "NFL"} in the body — the branch
added to that route this task, which returns
{id, player_key, event_key, player, team} rows instead of MLB's
{id, mlbam_id, game_pk}. MLB's own call (grade_bookmarks_live.py, an
UNMODIFIED file in a different Vercel project) sends no body at all and
keeps getting the exact MLB-shaped response — this file's addition changes
nothing about that.

WRITE SIDE: forwards to the SAME bookmark-results-write.ts endpoint MLB's
grading already calls — confirmed sport-agnostic, unmodified, during this
task's own investigation. STATUS_TO_RESULT there already maps "won"/
"lost"/"void" the same way regardless of sport.

ONLY "won" IS EVER FORWARDED — grade_pick_espn() (grading.py) can only
return "won" or "pending" by design (see its own module docstring for
why: the ESPN player-identity join is name-matched, not id-matched, so a
missed/ambiguous match defers to the Thursday nflverse correction pass
rather than ever risking a wrong grade). "pending" is filtered here, the
same discipline grade_official_picks_live.py/grade_bookmarks_live.py
already use for their own TERMINAL_STATUSES filter — this file never
even attempts to forward it, not relying on the write endpoint's status
enum as the only backstop.
"""
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from lovable_forward import compute_signature, forward_to_lovable, serialize_payload  # noqa: E402
from nfl_bookmark_grading import grade_nfl_bookmarks_live  # noqa: E402
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


def grade_nfl_bookmarks_for_pending(secret: str, read_url: str, write_url: str, season: int) -> dict:
    """
    The full same-night chain: read (which NFL bookmarks need grading) ->
    grade (real ESPN lookups, deduplicated per unique game) -> filter to
    "won" only -> write (forward to bookmark-results-write).

    `season` picks which nflverse schedules file to load for the
    game_id -> ESPN event id lookup (import_schedules() is per-season) --
    the caller (the Flask endpoint) resolves this the same way every
    other NFL endpoint already does (current week/season lookup), not
    hardcoded here.

    Returns the same shape as grade_bookmarks_for_pending (MLB's own
    orchestration function), field names adapted for this sport.
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

    read_result = fetch_nfl_bookmarks_needing_grading(secret, read_url)

    if not read_result.get("ok"):
        return {**empty_result, "error": f"read endpoint returned an error: {read_result.get('error')}"}

    picks = read_result["bookmarks_needing_grading"]
    if not picks:
        return empty_result

    schedules = load_schedules([season])
    grading_result = grade_nfl_bookmarks_live(picks, schedules)
    terminal = [r for r in grading_result["results"] if r["status"] in TERMINAL_STATUSES]
    still_pending = [r for r in grading_result["results"] if r["status"] not in TERMINAL_STATUSES]

    # bookmark-results-write.ts expects {id, status: "won"|"lost"|"void"} --
    # this grader only ever produces "won" among terminal results (see
    # module docstring), so the mapping here is a direct pass-through, not
    # a real status translation.
    write_payload = [{"id": r["id"], "status": r["status"]} for r in terminal]

    forward_result = None
    if write_payload:
        forward_result = forward_to_lovable(write_payload, secret, write_url)

    return {
        "error": None,
        "picks_needing_grading_count": len(picks),
        "graded_count": len(terminal),
        "still_pending_count": len(still_pending),
        "grading_errors": grading_result["errors"],
        "results_written": terminal,
        "forwarded": forward_result,
    }
