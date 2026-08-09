"""
Ties the two content-writer generation functions to a live source of
"which of today's real curated candidates don't have a draft yet" — the
automation this project was missing: curate_shelves.py populates real
shelf_assignments, but nothing before this module ever automatically
called generate_tasty_six_draft()/generate_shelf_card_draft() for what it
curated. Tonight's real 48-candidate batch was a one-off manual script;
this is the reusable, idempotent version of that same operation.

SAME FAN-OUT SHAPE AS grade_official_picks_live.py/grade_bookmarks_live.py:
a single endpoint does the internal looping (one real Claude call + one
real forward per candidate) so Make.com calls this once per scheduled run
instead of looping through every candidate itself.

THE IDEMPOTENCY MECHANISM IS AN ANTI-JOIN, SAME PHILOSOPHY AS GRADING:
content-drafts-needing-generation-read.ts (Lovable side) already excludes
any shelf_assignments row that has a matching content_drafts row for the
writer_type its is_tasty_six flag implies. This module never needs to
track "already attempted" separately — running it twice in a row is safe
by construction: the second run's read step returns nothing for candidates
the first run already wrote.

WRITER_TYPE SPLIT MIRRORS THE READ SIDE EXACTLY: is_tasty_six=true ->
generate_tasty_six_draft() (writer_type 'tasty_six'), else ->
generate_shelf_card_draft() (writer_type 'shelf_card') — the same rule
content-drafts-needing-generation-read.ts's SQL uses to decide what counts
as "already covered", kept in exactly one place in spirit (duplicated here
only because the split has to happen in both a SQL WHERE clause and a
Python dispatch, not because the rule itself differs).
"""
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "content_writer"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "content_writer" / "voice"))

from lovable_forward import compute_signature, forward_to_lovable, serialize_payload  # noqa: E402
from generate_tasty_six_content import draft_for_write as t6_draft_for_write, generate_tasty_six_draft  # noqa: E402
from generate_shelf_card_content import draft_for_write as sc_draft_for_write, generate_shelf_card_draft  # noqa: E402

REQUEST_TIMEOUT_SECONDS = 20


def fetch_candidates_needing_content(secret: str, read_url: str, slate: str | None = None) -> dict:
    """
    Calls Lovable's signed content-drafts-needing-generation-read endpoint.
    Reuses the same HMAC signing primitives every other signed call in this
    pipeline uses. `slate` is optional (YYYY-MM-DD) — omitted entirely from
    the body when None, letting the Lovable route default to today
    (America/New_York), same convention as get_published_shelf_picks.
    """
    body = {} if slate is None else {"slate": slate}
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


def generate_content_drafts_for_pending(
    secret: str, read_url: str, write_url: str, api_key: str, slate: str | None = None,
) -> dict:
    """
    The full chain: read (what needs content) -> generate (real Claude
    calls, one per candidate, same writer functions the manual /generate
    routes already use unmodified) -> write (forward each real draft to
    content_drafts, one at a time, same as those routes do internally).

    A candidate erroring (a real Claude/network failure, or a candidate
    shape generate_*_draft rejects) is recorded and skipped — one bad
    candidate must not sink the rest of a real day's batch, same
    discipline as official_pick_grading.py's per-game error isolation.

    Returns:
      {
        "error": str|None,                    # only set on a genuine read failure
        "candidates_found": int,
        "tasty_six_found": int,
        "shelf_card_found": int,
        "generated_count": int,                # real drafts actually produced
        "validation_passed_count": int,
        "validation_failed_count": int,        # flagged, still forwarded
        "generation_errors": [...],            # per-candidate errors
        "forwarded_count": int,                # real successful Lovable writes
        "forward_errors": [...],               # per-candidate forward failures
        "results": [...],                      # one summary dict per real draft generated
      }
    """
    empty_result = {
        "error": None,
        "candidates_found": 0,
        "tasty_six_found": 0,
        "shelf_card_found": 0,
        "generated_count": 0,
        "validation_passed_count": 0,
        "validation_failed_count": 0,
        "generation_errors": [],
        "forwarded_count": 0,
        "forward_errors": [],
        "results": [],
    }

    read_result = fetch_candidates_needing_content(secret, read_url, slate)

    if not read_result.get("ok"):
        return {**empty_result, "error": f"read endpoint returned an error: {read_result.get('error')}"}

    candidates = read_result["candidates_needing_content"]
    if not candidates:
        return {
            **empty_result,
            "tasty_six_found": read_result.get("tasty_six_count", 0),
            "shelf_card_found": read_result.get("shelf_card_count", 0),
        }

    generation_errors = []
    forward_errors = []
    results = []

    for c in candidates:
        writer_type = "tasty_six" if c.get("is_tasty_six") else "shelf_card"
        player_name = c.get("candidate", {}).get("player_name")
        mlbam_id = c.get("candidate", {}).get("mlbam_id")
        game_pk = c.get("candidate", {}).get("game_pk")
        shelf = c.get("shelf")

        try:
            if writer_type == "tasty_six":
                draft = generate_tasty_six_draft(c, api_key)
                row = t6_draft_for_write(draft)
            else:
                draft = generate_shelf_card_draft(c, api_key)
                row = sc_draft_for_write(draft)
        except Exception as e:  # noqa: BLE001 — one bad candidate must not sink the whole real batch
            generation_errors.append({
                "mlbam_id": mlbam_id, "game_pk": game_pk, "shelf": shelf,
                "writer_type": writer_type, "error": f"{type(e).__name__}: {e}",
            })
            continue

        forward_result = forward_to_lovable([row], secret, write_url)
        if not forward_result["success"]:
            forward_errors.append({
                "mlbam_id": mlbam_id, "game_pk": game_pk, "shelf": shelf,
                "writer_type": writer_type, "error": forward_result["error"],
                "status_code": forward_result["status_code"],
            })

        results.append({
            "mlbam_id": mlbam_id,
            "game_pk": game_pk,
            "shelf": shelf,
            "player_name": player_name,
            "writer_type": writer_type,
            "validation_passed": draft["validation_passed"],
            "review_status": draft["review_status"],
            "forwarded": forward_result["success"],
        })

    return {
        "error": None,
        "candidates_found": len(candidates),
        "tasty_six_found": read_result.get("tasty_six_count", 0),
        "shelf_card_found": read_result.get("shelf_card_count", 0),
        "generated_count": len(results),
        "validation_passed_count": sum(1 for r in results if r["validation_passed"]),
        "validation_failed_count": sum(1 for r in results if not r["validation_passed"]),
        "generation_errors": generation_errors,
        "forwarded_count": sum(1 for r in results if r["forwarded"]),
        "forward_errors": forward_errors,
        "results": results,
    }
