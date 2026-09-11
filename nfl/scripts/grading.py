"""
NFL Anytime TD grading — two real data sources, two real functions, sharing
one output contract. Modeled on pipeline/api/live_data/grading.py's own
4-state (won/lost/void/pending) shape and its "pure, deterministic,
side-effect-free, no timestamp" discipline — same reasoning: a future
storage layer stamps graded_at at write time, this module never does.

WHY TWO FUNCTIONS, NOT ONE WITH A SOURCE FLAG: the two data sources have
genuinely different reliability contracts, confirmed by real investigation,
not assumed:

  grade_pick_espn()      — same-night, via ESPN's public scoreboard/
                            boxscore API. FAST (minutes after a game ends)
                            but has a real, confirmed fragility: ESPN's
                            player identity is its own internal athlete id,
                            not a gsis id (checked directly against a real
                            game -- Rhamondre Stevenson is ESPN athlete
                            "4569173", nothing like his real gsis id).
                            Bridging that gap means matching by NAME within
                            the one team's box score for the one known
                            game -- narrow (one team, one game, one known
                            name already on the bookmark row), but still a
                            real name-matching operation, not an id join.

  grade_pick_nflverse()  — the Thursday correction pass (and the eventual
                            authoritative source once nflverse's own
                            nightly refresh has the data), via
                            nfl_data_py's pbp/snap-count releases. Zero
                            identity risk: player_id (gsis) and game_id are
                            ALREADY the exact keys pbp/snap-count data uses
                            natively -- confirmed directly (redzone.py's
                            own _touches() sets player_id = rusher_
                            player_id/receiver_player_id, no crosswalk
                            hop). This is the reliable, authoritative
                            grader.

THE REAL DESIGN CALL THIS MAKES, to keep the name-matching fragility from
ever producing a WRONG grade rather than just a SLOW one: grade_pick_espn()
can only ever return "won" or "pending" -- never "lost" or "void". A
confident, unambiguous name match with a real qualifying TD -> "won"
(same-night good news travels fast, which is the whole point of grading
same-night in the first place). Anything else -- game not final yet, no
ESPN event mapped, the name not found in the box score, an ambiguous
match (more than one athlete with that exact name on that team), zero
qualifying TDs found -- returns "pending", not a guess. The authoritative
LOST/VOID call is reserved for grade_pick_nflverse(), which has zero
identity risk and a real participation signal (snap counts) to
distinguish "played, no TD" from "inactive/did not play" -- something
grade_pick_espn() has no reliable way to determine from name-matched box
score presence alone (a player who ran routes but drew zero targets and a
player who was inactive both look identical: absent from the box score).
A missed same-night win just waits for Thursday; it is never reported
wrong.
"""
from __future__ import annotations

import pandas as pd
import requests

ESPN_SUMMARY_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary"
REQUEST_TIMEOUT_SECONDS = 15

# Only rushing and receiving carry OFFENSE-attributed touchdowns for the
# player who scored them -- confirmed directly against a real completed
# game's box score (NE @ SEA, 2026-09-09): ESPN's boxscore.players[].
# statistics groups TDs into SEPARATE categories per how the score
# happened -- rushing, receiving, defensive (fumble-return-style),
# interceptions (pick-six), kickReturns, puntReturns -- each with its own
# TD column. This mirrors the exact reasoning redzone.py's own _touches()
# already uses rush_touchdown/pass_touchdown instead of the generic
# touchdown column for: a defensive/special-teams score is a DIFFERENT
# category here, never mixed into an offensive player's rushing/receiving
# total. Reading only these two categories, and no others, is what
# excludes DEF/ST touchdowns from an Anytime TD grade -- not an extra
# filter, just never looking at the categories that would need one.
_OFFENSE_TD_CATEGORIES = ("rushing", "receiving")


def _fetch_espn_summary(espn_event_id: int) -> dict:
    resp = requests.get(
        ESPN_SUMMARY_URL, params={"event": espn_event_id}, timeout=REQUEST_TIMEOUT_SECONDS
    )
    resp.raise_for_status()
    return resp.json()


def _pending(reason: str) -> dict:
    return {"status": "pending", "reason": reason, "touchdowns": None, "espn_event_id": None}


def grade_pick_espn(
    player_id: str,
    player_name: str,
    player_team: str,
    game_id: str,
    schedules: pd.DataFrame,
) -> dict:
    """
    Same-night grade attempt via ESPN's public scoreboard/boxscore API.

    player_id (gsis) is accepted for the return payload's own bookkeeping
    only -- ESPN's data is never joined by it, per the module docstring.
    player_name/player_team come straight off the bookmark row itself
    (bookmarks.player/.team, captured at save time), the same convention
    every other saved-pick field already uses -- no separate roster fetch.

    schedules is nfl_data_py.import_schedules()'s own output (the
    habitatring.com/games.csv mirror) -- confirmed directly to carry a
    real `espn` column mapping nflverse's own game_id to ESPN's numeric
    event id ALREADY, in the same file this pipeline already reads for
    kickoff/roof/weather. No separate id-crosswalk needed for this half
    of the lookup at all.

    Returns (see module docstring for why only these two states):
      {"status": "won"|"pending", "reason": str,
       "touchdowns": int|None, "espn_event_id": int|None}
    """
    game_row = schedules[schedules["game_id"] == game_id]
    if game_row.empty:
        return _pending(f"game_id {game_id!r} not found in schedules -- can't resolve an ESPN event id yet")
    game_row = game_row.iloc[0]

    espn_event_id = game_row.get("espn")
    if pd.isna(espn_event_id):
        return _pending(f"no ESPN event id mapped for game_id {game_id!r} yet")
    espn_event_id = int(espn_event_id)

    home_team, away_team = game_row["home_team"], game_row["away_team"]
    if player_team == home_team:
        side = "home"
    elif player_team == away_team:
        side = "away"
    else:
        # A real, worth-surfacing mismatch (traded player, stale saved
        # team abbreviation) -- never silently pick a side.
        return _pending(
            f"player_team {player_team!r} matches neither home ({home_team!r}) nor away ({away_team!r}) for {game_id!r}"
        )

    data = _fetch_espn_summary(espn_event_id)
    status = data.get("header", {}).get("competitions", [{}])[0].get("status", {}).get("type", {})
    if not status.get("completed"):
        return {
            **_pending(f"game is currently {status.get('description', 'not final')!r}"),
            "espn_event_id": espn_event_id,
        }

    competitors = data.get("header", {}).get("competitions", [{}])[0].get("competitors", [])
    # Matched by ESPN's own team.id, not by abbreviation string -- ESPN and
    # nflverse disagree on at least one real team's short code (confirmed:
    # nflverse's LA Rams are "LA", ESPN's are "LAR"), so home/away SIDE
    # (already known from nflverse's own schedule row, matched above) is
    # the safe join key here, never a cross-provider abbreviation compare.
    espn_team_id = next((c["team"]["id"] for c in competitors if c.get("homeAway") == side), None)
    if espn_team_id is None:
        return {**_pending("could not resolve the ESPN team id for this side"), "espn_event_id": espn_event_id}

    team_block = next(
        (t for t in data.get("boxscore", {}).get("players", []) if t.get("team", {}).get("id") == espn_team_id),
        None,
    )
    if team_block is None:
        return {
            **_pending("no box score player stats found for this team -- likely means zero recorded stats at all"),
            "espn_event_id": espn_event_id,
        }

    matches: list[int] = []
    ambiguous = False
    for category in _OFFENSE_TD_CATEGORIES:
        group = next((g for g in team_block.get("statistics", []) if g.get("name") == category), None)
        if not group:
            continue
        labels = group.get("labels", [])
        if "TD" not in labels:
            continue
        td_index = labels.index("TD")
        athlete_ids_seen = set()
        for entry in group.get("athletes", []):
            athlete = entry.get("athlete", {})
            if athlete.get("displayName") != player_name:
                continue
            athlete_ids_seen.add(athlete.get("id"))
            raw_td = entry.get("stats", [None] * len(labels))[td_index]
            try:
                matches.append(int(raw_td))
            except (TypeError, ValueError):
                pass
        if len(athlete_ids_seen) > 1:
            ambiguous = True

    if ambiguous:
        return {
            **_pending(f"more than one distinct ESPN athlete named {player_name!r} on this team's box score -- ambiguous, not guessing"),
            "espn_event_id": espn_event_id,
        }

    if not matches:
        return {
            **_pending(f"{player_name!r} not found in rushing/receiving box score for this game (zero touches, inactive, or a name mismatch -- deferring to the nflverse correction pass to tell which)"),
            "espn_event_id": espn_event_id,
        }

    total_tds = sum(matches)
    if total_tds > 0:
        return {
            "status": "won",
            "reason": f"{total_tds} real rushing/receiving touchdown(s), confirmed via ESPN box score",
            "touchdowns": total_tds,
            "espn_event_id": espn_event_id,
        }

    return {
        **_pending(f"{player_name!r} played (found in the box score) with 0 rushing/receiving touchdowns -- deferring the final lost/void call to the nflverse correction pass"),
        "espn_event_id": espn_event_id,
    }


def grade_pick_nflverse(
    player_id: str,
    game_id: str,
    pbp: pd.DataFrame,
    schedules: pd.DataFrame,
    snap_counts: pd.DataFrame,
    id_crosswalk: pd.DataFrame,
) -> dict:
    """
    The authoritative grade: nfl_data_py's own pbp + schedule + snap-count
    releases, all keyed natively by gsis player_id / nflverse game_id --
    zero identity risk (see module docstring). This is what the Thursday
    correction pass calls, and it's the only function in this module that
    can return "lost" or "void".

    won   -- a real rush_touchdown (as rusher) or pass_touchdown (as
             receiver) for player_id, in game_id's pbp. Same two columns,
             same reasoning, redzone.py's own _touches() already uses --
             excludes DEF/ST scores by construction (a fumble/pick-six
             return never sets the offensive ball-carrier's own rush_
             touchdown/pass_touchdown flag).
    lost  -- game is final, no qualifying TD, but player_id has a real
             snap-count row for game_id with offense_snaps > 0 -- they
             played, the prop just didn't hit.
    void  -- game is final, no qualifying TD, and player_id has NO
             snap-count row (or offense_snaps == 0) for game_id -- did
             not actually take the field, same convention MLB's own
             grade_pick() already uses for a zero-plate-appearance pick
             (prop never had a fair chance to resolve).
    pending -- game_id isn't final yet (schedules' home_score/away_score
             are still null for it).

    snap_counts is nfl_data_py.import_snap_counts()'s own output, keyed by
    pfr_player_id (PFR's id space, not gsis) -- id_crosswalk (redzone.py's
    build_id_crosswalk(), reused unmodified, not reimplemented) is what
    this function uses to translate player_id (gsis) into the
    pfr_player_id snap_counts is actually keyed by. This is the one place
    this module genuinely needs the crosswalk infra the original grading
    spec assumed everywhere -- confirmed it's only needed here, not for
    the TD lookup itself.
    """
    game_row = schedules[schedules["game_id"] == game_id]
    if game_row.empty:
        return {"status": "pending", "reason": f"game_id {game_id!r} not found in schedules",
                "touchdowns": None, "game_final": False}
    game_row = game_row.iloc[0]

    is_final = pd.notna(game_row.get("home_score")) and pd.notna(game_row.get("away_score"))
    if not is_final:
        return {"status": "pending", "reason": "game has no final score in schedules yet",
                "touchdowns": None, "game_final": False}

    game_pbp = pbp[pbp["game_id"] == game_id]
    rush_tds = game_pbp[(game_pbp["rusher_player_id"] == player_id) & (game_pbp["rush_touchdown"] == 1)]
    rec_tds = game_pbp[(game_pbp["receiver_player_id"] == player_id) & (game_pbp["pass_touchdown"] == 1)]
    total_tds = len(rush_tds) + len(rec_tds)

    if total_tds > 0:
        return {"status": "won", "reason": f"{total_tds} real rush/receiving touchdown(s) in nflverse pbp",
                "touchdowns": total_tds, "game_final": True}

    # Zero TDs -- decide lost vs void via real snap-count participation,
    # translated through the crosswalk (see docstring).
    xwalk_row = id_crosswalk[id_crosswalk["gsis_id"] == player_id]
    if xwalk_row.empty:
        return {
            "status": "void",
            "reason": "no pfr_id crosswalk entry for this player -- can't confirm real participation, treated as did-not-play rather than guessed as a loss",
            "touchdowns": 0,
            "game_final": True,
        }
    pfr_id = xwalk_row.iloc[0]["pfr_id"]

    game_snaps = snap_counts[
        (snap_counts["pfr_player_id"] == pfr_id) & (snap_counts["game_id"] == game_id)
    ]
    played = not game_snaps.empty and (game_snaps["offense_snaps"].fillna(0) > 0).any()

    if played:
        return {"status": "lost", "reason": "0 rushing/receiving touchdowns in a game the player actually played",
                "touchdowns": 0, "game_final": True}

    return {
        "status": "void",
        "reason": "no recorded offensive snaps this game -- did not play, prop never had a fair chance to resolve",
        "touchdowns": 0,
        "game_final": True,
    }
