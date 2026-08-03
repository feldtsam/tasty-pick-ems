"""
Grades a saved HR-prop pick against the real, final outcome of its game —
did the player actually hit a home run?

STANDALONE for now — not wired into any endpoint, storage, or Make.com
scenario yet, same "validate the logic first" sequencing as everything
else in this pipeline.

REUSES the same MLB Stats API `feed/live` endpoint already used by
game_data.py for pre-game lineups/weather — just read AFTER the game
concludes instead of before it starts. No new data source, same call
shape (one gamePk in, real data out).

A REAL, CONCRETE FINDING THAT SHAPED THIS DESIGN: the day-level `/schedule`
endpoint's status for a gamePk can go STALE once a postponed game gets
rescheduled and later played — confirmed against real data, not assumed.
A real game (Atlanta @ New York Mets, 2026-07-28, game_pk 823598) showed
`detailedState: "Postponed"` via `/schedule` at one point, while
`feed/live` for the EXACT SAME gamePk, checked moments later, showed
`"In Progress"` — the game had been postponed from its original date and
rescheduled (reusing the same gamePk) and was actively being played when
checked directly. Grading off a cached/stale schedule snapshot could have
permanently voided a pick whose game was genuinely still going to produce
a real result. Fixed by ALWAYS re-fetching `feed/live` fresh at grading
time — never trusting an earlier schedule-level status. Confirmed correct
against a second real case: Cleveland @ Cincinnati, game_pk 824490, also
originally postponed the same day — `feed/live` now correctly shows
`"Final"` with a real, complete box score once its doubleheader makeup
actually finished.

A SECOND REAL DISTINCTION THIS SURFACED: "postponed" does not mean
"cancelled forever." The overwhelming majority of real MLB postponements
get made up later (same or new gamePk) and DO eventually produce a real
result — voiding a pick immediately just because a game shows "Postponed"
at the exact moment it happens to be checked would produce a WRONG,
premature verdict for a game that's simply delayed, not actually over.
This module therefore returns FOUR statuses, not the three originally
specified: "won", "lost", "void", and "pending". "pending" covers
not-yet-started, in-progress, AND postponed-awaiting-makeup games alike —
anything that hasn't reached a genuine terminal state yet, meaning "check
again later," not a wrong final verdict. "void" is reserved for a game
`feed/live` itself currently reports as terminally Postponed/Cancelled/
Suspended (`abstractGameState == "Final"` but the game never actually
completed real play). Flagging this as a deliberate refinement of the
original "postponed -> void" instruction, not a silent substitution —
recommend confirming this 4-state model before wiring grading into
anything that acts on the result.

A THIRD REAL EDGE CASE, confirmed against real box score data: a player
can be on the game-day active roster and appear in the box score with
`plateAppearances: 0` (confirmed real: Hao-Yu Lee and Jeremiah Jackson,
both 2026-07-28, both benched/never entered) — VOIDED here rather than
graded "lost", matching standard prop-betting convention that a player
who never actually took the field didn't get a fair chance to resolve the
prop. A player scratched entirely off the day-of roster (not just
benched) may not appear in the box score at all — handled by the same
code path (treated identically to zero plate appearances) but a fully
real example of that specific case wasn't found today; the two confirmed
real zero-PA cases exercise the same branch.

Extra innings and mid-game substitutions needed NO special handling,
confirmed against real data — the box score is a running total for
whatever the player actually did in the whole game regardless of length
or when they entered/exited (confirmed: Ben Malgeri, real 2026-07-28 game,
2 PA reflecting a partial-game substitute appearance, graded correctly as
a real "lost" rather than mishandled).
"""
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

FEED_LIVE_URL = "https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live"
TIMEOUT_S = 15

# Only home-run props are implemented — the pick_type parameter exists so
# this can grow to other prop types later without changing the call shape,
# not because anything else is supported today.
SUPPORTED_PICK_TYPES = ("home_run",)

# Terminal-but-never-played states, confirmed to include real cases that
# DO eventually resolve to "Final" once resumed/made up (see module
# docstring) — only voided when feed/live ITSELF, checked fresh, still
# reports one of these at grading time.
_VOID_DETAILED_STATES = {"Postponed", "Cancelled", "Suspended"}


def _fetch_feed_live(game_pk: int) -> dict:
    """Confirmed (see game_data.py) that an unknown/invalid game_pk
    doesn't 404 — feed/live returns a placeholder body with `gamePk: 0`.
    Same defensive check reused here."""
    resp = requests.get(FEED_LIVE_URL.format(game_pk=game_pk), timeout=TIMEOUT_S)
    resp.raise_for_status()
    data = resp.json()
    if data.get("gamePk") != game_pk:
        raise ValueError(f"game_pk {game_pk} is not a known MLB game (feed/live returned an empty/placeholder response).")
    return data


def _game_state(data: dict) -> dict:
    """Classifies the game's CURRENT real status — always from a freshly
    fetched feed/live response, never a cached/earlier schedule snapshot
    (see module docstring for the real case that makes this matter)."""
    status = data["gameData"]["status"]
    abstract = status.get("abstractGameState")
    detailed = status.get("detailedState")

    if abstract in ("Preview", "Live"):
        return {"terminal": False, "outcome": None, "reason": f"game is currently {detailed!r} — not concluded yet"}

    # abstract == "Final" from here on.
    if detailed in _VOID_DETAILED_STATES:
        return {"terminal": True, "outcome": "void",
                "reason": f"game's current real status is {detailed!r} — never completed real play"}

    return {"terminal": True, "outcome": "played", "reason": None}


def _player_batting_line(data: dict, mlbam_id: int) -> dict:
    """Real box score line for one player in one game. `found=False`
    covers a player entirely absent from the game-day roster; a player
    who WAS on the roster but never batted shows up with
    plate_appearances=0 instead — both are treated identically by
    grade_pick() (see module docstring)."""
    box = data["liveData"]["boxscore"]
    key = f"ID{mlbam_id}"
    for side in ("home", "away"):
        pdata = box["teams"][side]["players"].get(key)
        if pdata is not None:
            batting = pdata.get("stats", {}).get("batting") or {}
            return {
                "found": True,
                "plate_appearances": batting.get("plateAppearances") or 0,
                "home_runs": batting.get("homeRuns") or 0,
            }
    return {"found": False, "plate_appearances": 0, "home_runs": 0}


def grade_pick(mlbam_id: int, game_pk: int | str, pick_type: str = "home_run") -> dict:
    """
    Pure, deterministic, side-effect-free: reads real, current MLB data
    and returns a verdict. Deliberately returns NO timestamp — grading the
    SAME real (mlbam_id, game_pk) after the game has reached a genuine
    terminal state always returns byte-identical output (see
    test_grading.py's repeated-call check); a future storage layer stamps
    its own graded_at at write time, keeping this function's own output
    fully idempotent rather than "idempotent except for a clock field".

    Returns:
      {"status": "won"|"lost"|"void"|"pending", "reason": str,
       "home_runs": int|None, "plate_appearances": int|None,
       "game_detailed_state": str}
    """
    if pick_type not in SUPPORTED_PICK_TYPES:
        raise ValueError(f"pick_type {pick_type!r} is not supported yet — only {SUPPORTED_PICK_TYPES} implemented so far.")

    # Real finding from wiring up the live official-picks grading chain:
    # shelf_assignments.game_pk is a `text` column, so a real caller reading
    # from it hands this function a numeric STRING, not an int. Every call
    # site up to that point (test_grading.py, official_pick_grading.py)
    # only ever passed a Python int, so this went uncaught until real data
    # from a text column hit _fetch_feed_live()'s `gamePk` equality check
    # below, which compares against the JSON response's integer gamePk.
    # Coercing once here — rather than loosening that equality check to a
    # string comparison — keeps the "placeholder response for an unknown
    # game_pk" defense exact while making this function itself tolerant of
    # either caller convention.
    game_pk = int(game_pk)

    data = _fetch_feed_live(game_pk)
    state = _game_state(data)
    detailed_state = data["gameData"]["status"].get("detailedState")

    if not state["terminal"]:
        return {"status": "pending", "reason": state["reason"], "home_runs": None,
                "plate_appearances": None, "game_detailed_state": detailed_state}

    if state["outcome"] == "void":
        return {"status": "void", "reason": state["reason"], "home_runs": None,
                "plate_appearances": None, "game_detailed_state": detailed_state}

    line = _player_batting_line(data, mlbam_id)
    if not line["found"] or line["plate_appearances"] == 0:
        return {
            "status": "void",
            "reason": "player recorded zero plate appearances this game — did not actually play, prop never had a fair chance to resolve",
            "home_runs": line["home_runs"] if line["found"] else None,
            "plate_appearances": line["plate_appearances"],
            "game_detailed_state": detailed_state,
        }

    won = line["home_runs"] > 0
    return {
        "status": "won" if won else "lost",
        "reason": (f"{line['home_runs']} real home run(s) in {line['plate_appearances']} plate appearances" if won
                   else f"0 home runs in {line['plate_appearances']} plate appearances"),
        "home_runs": line["home_runs"],
        "plate_appearances": line["plate_appearances"],
        "game_detailed_state": detailed_state,
    }
