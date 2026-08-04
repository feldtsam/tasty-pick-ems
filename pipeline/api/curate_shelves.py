"""
Pulls a full day's real `scored_picks` from Lovable's signed read endpoint,
sanity-checks it isn't suspiciously incomplete, runs it through the
already-tested `shelf_curation.py` logic, and shapes the result into
`shelf_assignments`-ready rows for forwarding to Lovable's write endpoint.

This is the piece that makes shelf curation possible at all: unlike
scoring (`scored_picks.py`), which only ever needs one game's data at a
time and only ever writes forward, shelf curation is inherently a
cross-game, whole-slate operation — it needs to rank candidates against
every other candidate scored that day, not just within one game.

ARCHITECTURE DECISION (Option 2, chosen over two real alternatives):
Lovable exposes a new signed read endpoint (`/api/public/scored-picks-read`,
mirroring the existing write endpoints' HMAC pattern exactly) rather than
either (a) a public RLS read policy + the anon key, or (b) having Make.com
accumulate the whole day's results in memory across its own execution and
never touch the database until the final write.

(a) was rejected because an RLS policy readable by the anon key isn't
scoped to Make.com at all — the anon key ships in every client bundle, so
that's "readable by the entire internet," not "readable by this one
trusted caller." A signed endpoint keeps access gated by a shared secret,
consistent with every other cross-boundary read/write in this project.

(b) was rejected because it's the one design that's actually MORE fragile
against the exact failure mode this needs to guard against: `scored_picks`
is already the durable, persisted source of truth — every game's picks
survive there independent of whatever happens to Make.com's execution
afterward. Trusting one long-running Make.com scenario to hold the whole
day's state correctly in memory from first game to last reintroduces
exactly the kind of monolithic, hard-to-recover execution shape every
other piece of this pipeline (per-game `score-game-props`, `by-event`
resolution) has deliberately avoided. Reading from the durable table at
curation time instead makes shelf curation naturally re-runnable — if a
correction or retry is needed, it just re-fetches current state.

RELIABILITY: `sanity_check_slate()` exists specifically because a Make.com
run failing partway through a day's games should produce a LOUD, visible
flag here — not a quietly-broken set of shelves curated from a handful of
games because most of the day's scoring calls never happened.
"""
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lovable_forward import compute_signature, serialize_payload  # noqa: E402
from shelf_curation import DEFAULT_SHELF_SIZE, assign_shelves, compute_tasty_six  # noqa: E402

REQUEST_TIMEOUT_SECONDS = 20

# A real full MLB day is typically 10-15 games. Fewer than this many
# DISTINCT game_pks in the read response is treated as a suspiciously
# incomplete slate — flagged, never silently curated. A configurable
# constant, not a number buried inline.
MIN_EXPECTED_GAMES = 5

# Which raw field each shelf's `shelf_score` actually represents — mirrors
# the mapping already documented in pipeline/README.md. shelf_curation.py's
# own entries don't carry this label themselves (a plain float shelf_score
# means something different per shelf), so it's attached here, once, at
# the point the label is written to the database.
SHELF_SCORE_LABELS = {
    "+300-499": "final_score",
    "+500-699": "final_score",
    "Going Nuclear": "final_score",
    "Hot Hitters": "recent_ops",
    "Cold Pitchers to Attack": "recent_era",
    "Weather Factors": "environment_score",
}


def fetch_todays_scored_picks(date: str, secret: str, read_url: str) -> dict:
    """
    Calls Lovable's signed `scored-picks-read` endpoint for one date.
    Reuses the EXACT same HMAC signing primitives `lovable_forward.py`
    already uses for writes — same secret, same signature header — just
    applied to an outgoing read request's body instead of a write payload.
    """
    payload_str = serialize_payload({"date": date})
    signature = compute_signature(secret, payload_str)
    response = requests.post(
        read_url,
        data=payload_str.encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Signature": signature},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


def sanity_check_slate(read_result: dict, min_expected_games: int = MIN_EXPECTED_GAMES) -> dict:
    """
    The real reliability guard this module exists for. Returns
    `{"suspicious": bool, "distinct_game_pk_count": int, "row_count": int,
    "reason": str|None}`. `suspicious=True` means "do not curate from
    this" — the caller is expected to stop, not proceed with a best-effort
    partial result.
    """
    game_count = read_result.get("distinct_game_pk_count", 0)
    row_count = read_result.get("row_count", 0)
    suspicious = game_count < min_expected_games
    return {
        "suspicious": suspicious,
        "distinct_game_pk_count": game_count,
        "row_count": row_count,
        "reason": (
            f"only {game_count} distinct game(s) in today's scored_picks — expected at least "
            f"{min_expected_games} for a real MLB day. Curating shelves from this would likely "
            f"produce broken or misleading results; treat this as a failed/incomplete run, not a quiet slow day."
        ) if suspicious else None,
    }


def _tasty_lookup(tasty_six: dict) -> dict:
    """shelf_name -> (mlbam_id, game_pk) for whichever candidate
    compute_tasty_six() actually picked for that shelf. Shared by both
    _shelf_assignment_rows() and _shelf_candidates_detailed() below so the
    thin write-shape rows and the rich debug view can never disagree about
    which entry is the real Tasty Six pick."""
    lookup = {}
    for shelf_name, entry in tasty_six["picks"].items():
        if entry is not None:
            c = entry["candidate"]
            lookup[shelf_name] = (c["mlbam_id"], c["game_pk"])
    return lookup


def _shelf_assignment_rows(shelves: dict, tasty_lookup: dict) -> list:
    """
    Flattens `assign_shelves()`'s output into `shelf_assignments`-shaped
    rows — one row per (candidate, shelf) appearance, exactly the grain
    the real table is keyed on, and exactly what gets forwarded to
    Lovable's real write endpoint. Deliberately thin — real column names
    only, nothing extra, so this payload never drifts from what
    shelf_assignments actually has columns for.
    """
    rows = []
    for shelf_name, entries in shelves.items():
        for entry in entries:
            c = entry["candidate"]
            is_tasty = tasty_lookup.get(shelf_name) == (c["mlbam_id"], c["game_pk"])
            rows.append({
                "mlbam_id": c["mlbam_id"],
                "game_pk": c["game_pk"],
                "shelf": shelf_name,
                "rank": entry["rank"],
                "is_tasty_six": is_tasty,
                "shelf_score": entry["shelf_score"],
                "shelf_score_label": SHELF_SCORE_LABELS[shelf_name],
            })
    return rows


def _shelf_candidates_detailed(shelves: dict, tasty_lookup: dict) -> dict:
    """
    The FULL real candidate data behind each shelf entry — pillar_detail,
    all four pillar scores, odds, and the recent-form extras Hot Hitters/
    Cold Pitchers to Attack entries carry — tagged with the same real
    is_tasty_six flag as _shelf_assignment_rows(), via the same shared
    tasty_lookup so the two views can't disagree.

    NEVER forwarded to Lovable — shelf_assignments has no columns for most
    of this. Exists only to be surfaced locally through
    /api/curate-shelves's include_rows debug option, for pulling real
    candidate data to test against (e.g. the content writer's citation/
    numeric-grounding checks in tasty_six_writer_schema.py, which need
    pillar_detail and can't work from the thin write-shape rows alone).
    """
    detailed = {}
    for shelf_name, entries in shelves.items():
        rows = []
        for entry in entries:
            c = entry["candidate"]
            is_tasty = tasty_lookup.get(shelf_name) == (c["mlbam_id"], c["game_pk"])
            rows.append({**entry, "shelf": shelf_name, "is_tasty_six": is_tasty})
        detailed[shelf_name] = rows
    return detailed


def curate_shelves_for_date(date: str, secret: str, read_url: str, shelf_size: int = DEFAULT_SHELF_SIZE) -> dict:
    """
    The full chain: read -> sanity-check -> curate -> shape for write.
    Does NOT forward to Lovable itself — mirrors the existing separation
    between pure orchestration (this function) and network calls (the
    Flask route in index.py), same as scored_picks.py/lovable_forward.py.

    Returns:
      {
        "date": str, "error": str|None, "sanity_check": dict,
        "shelf_assignments": [...rows, or [] if error/suspicious...],
        "shelf_candidates_detailed": {shelf_name: [...full candidate rows...],
                                       or {} if error/suspicious},
        "tasty_six_repeats": [...shelf names that needed a fallback...],
        "shelf_sizes": {shelf_name: count, ...},
      }
    """
    read_result = fetch_todays_scored_picks(date, secret, read_url)

    if not read_result.get("ok"):
        return {
            "date": date,
            "error": f"read endpoint returned an error: {read_result.get('error')}",
            "sanity_check": None,
            "shelf_assignments": [],
            "shelf_candidates_detailed": {},
            "tasty_six_repeats": [],
            "shelf_sizes": {},
        }

    sanity = sanity_check_slate(read_result)
    if sanity["suspicious"]:
        return {
            "date": date,
            "error": sanity["reason"],
            "sanity_check": sanity,
            "shelf_assignments": [],
            "shelf_candidates_detailed": {},
            "tasty_six_repeats": [],
            "shelf_sizes": {},
        }

    candidates = read_result["scored_picks"]
    season = int(date[:4])
    shelves = assign_shelves(candidates, season=season, shelf_size=shelf_size)
    tasty_six = compute_tasty_six(shelves)
    tasty_lookup = _tasty_lookup(tasty_six)
    rows = _shelf_assignment_rows(shelves, tasty_lookup)
    detailed = _shelf_candidates_detailed(shelves, tasty_lookup)

    return {
        "date": date,
        "error": None,
        "sanity_check": sanity,
        "shelf_assignments": rows,
        "shelf_candidates_detailed": detailed,
        "tasty_six_repeats": tasty_six["repeats"],
        "shelf_sizes": {name: len(entries) for name, entries in shelves.items()},
    }
