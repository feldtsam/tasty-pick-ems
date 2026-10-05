"""
CFB ATTD player-matching layer — attd_match.py.

Ports NFL's exact-match approach (nfl/roster_match.py::match_player_
names) directly, rather than building fuzzy matching or a manual
override table — the prior feasibility investigation (2026-09-13) found
no evidence CFB's matching problem is genuinely harder than NFL's: a
real 99.92% self-consistency match rate (name + own real school ->
exactly one real player) across QB/RB/WR/TE, zero measured roster-year
lag despite real, substantial transfer-portal volume (21.84%). Team-
constrained exact matching — candidates restricted to the real game's
own home/away schools, never the full FBS pool — is the same real
mechanism that already resolves almost all of the 1.39% national
name-collision rate NFL's own matcher relies on too.

SCHEMA-AGNOSTIC INPUT, BY DESIGN: poll_ncaaf_prop_coverage.py's
parse_event_odds() captures each ATTD outcome's `name`/`description`
VERBATIM, deliberately not committing to which field holds the player's
name — CFB's real outcome schema was never confirmed against live data
(every real game probed 2026-09-13 was 4+ days from kickoff, zero live
coverage). This module does NOT assume either shape: detect_outcome_
schema() below inspects the FIRST real outcome it's given and decides
"nfl_style" (name is the constant "Yes"/"No", player is in description
— NFL's confirmed real shape) vs "mlb_style" (name is the player,
description is the team — MLB's confirmed real shape) at runtime, and
match_cfb_attd_players() logs which one it saw. The next real run near
a real kickoff answers Part 2's open schema question as a side effect
of just running this, rather than needing a separate manual inspection
pass.

POSITION SCOPE: RB/WR/TE (roster.POSITION_GROUPS), matching every other
real CFB scoring/aggregation module in this codebase (redzone.py,
scoring.py, story_archetype.py — none of them score QBs). NFL's own
roster_match.py uses the identical RB/WR/TE scope, for the identical
reason (its own scoring pillars are RB/WR/TE-only too) — a real parity
choice, not an independent decision: a QB anytime-TD prop classifies as
position_out_of_scope here, exactly the way it would under NFL's real
matcher, even though a real sportsbook board would post QB markets too.

BEYOND THE NFL PORT: name_collision, a 4th match_issue_type NFL's own
matcher doesn't have. NFL's version takes in_scope.iloc[0] on a
same-team, same-name collision — silently. This task explicitly asked
for same-name-same-school cases to be flagged, not silently resolved to
whichever row happens to sort first. The real, known collision cases
from the prior investigation (Marcel Williams/Akron — 2 real WRs; DJ
Jordan/USC — 2 real WRs) are exactly this case, and are used as
regression fixtures in test_attd_match.py.

SCHOOL RESOLUTION reuses poll_ncaaf_prop_coverage.match_school/
load_fbs_ref directly (promoted to public names from that module's own
former _match_school/_load_fbs_ref for exactly this reuse — see that
module's own comment) rather than a second, independently-maintained
Odds-API-team-string resolver.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))

from roster import POSITION_GROUPS  # noqa: E402

_NON_PLAYER_OUTCOME_NAMES = {"yes", "no", "over", "under"}


def detect_outcome_schema(outcomes: list[dict], home_school: str | None, away_school: str | None) -> str:
    """
    Inspects the FIRST real outcome's name/description fields against
    this game's own real candidate schools to decide which of the two
    known real shapes this response actually uses:
      "nfl_style" — name is a constant ("Yes"/"No"/"Over"/"Under"),
                    player is in description (NFL's confirmed real shape,
                    see nfl/market_value.py's own docstring).
      "mlb_style" — name is the player, description is the team (MLB's
                    confirmed real shape).
      "unknown"   — neither heuristic resolved (no real outcomes given,
                    or a genuinely different real shape) — callers try
                    BOTH fields as a candidate player name rather than
                    guessing one.
    Never raises — an empty/malformed outcomes list is a real, expected
    "no coverage yet" case (matches poll_ncaaf_prop_coverage.py's own
    confirmed 2026-09-13 finding: 0/57 real games had any coverage at
    that distance from kickoff), not an error.
    """
    if not outcomes:
        return "unknown"
    sample = outcomes[0]
    name = (sample.get("name") or "").strip().lower()
    description = (sample.get("description") or "").strip()
    if name in _NON_PLAYER_OUTCOME_NAMES:
        return "nfl_style"
    candidate_schools = {s.strip().lower() for s in (home_school, away_school) if s}
    if description.lower() in candidate_schools:
        return "mlb_style"
    return "unknown"


def _candidate_names(outcome: dict, schema: str) -> list[str]:
    """The real candidate player-name string(s) to try matching for one
    outcome, given the detected schema. "unknown" tries both real
    fields — an honest degrade, not a guess at which one is right."""
    name = (outcome.get("name") or "").strip()
    description = (outcome.get("description") or "").strip()
    if schema == "nfl_style":
        return [description] if description else []
    if schema == "mlb_style":
        return [name] if name else []
    seen = []
    for v in (name, description):
        if v and v not in seen:
            seen.append(v)
    return seen


def match_cfb_attd_players(
    attd_outcomes: list[dict],
    home_team: str,
    away_team: str,
    roster: list[dict],
    fbs_ref: dict,
    fbs_combos: dict,
) -> dict:
    """
    Team-constrained exact match of one real game's ATTD outcomes
    against real CFBD roster rows. Direct port of nfl/roster_match.py's
    match_player_names — same real matching shape, same classification
    philosophy — see this module's own docstring for the two real
    differences (schema-agnostic input, the added name_collision
    category).

    `attd_outcomes`: poll_ncaaf_prop_coverage.parse_event_odds()'s own
    real per-outcome list for ONE game (book_key, book_title, name,
    description, price, point).
    `home_team`/`away_team`: the event's own real Odds-API team strings
    ("Georgia Bulldogs") — resolved to CFBD's real short school names
    via poll_ncaaf_prop_coverage.match_school, the SAME resolver
    tier_for() already uses, not a fresh implementation.
    `roster`: real CFBD /roster rows for the season (roster.
    fetch_fbs_roster's own real shape: id, firstName, lastName, team,
    position, ...).
    `fbs_ref`/`fbs_combos`: poll_ncaaf_prop_coverage.load_fbs_ref()'s
    own real output, threaded through rather than reloaded per call.

    Returns {
      "schema": "nfl_style" | "mlb_style" | "unknown" — detect_outcome_
                schema()'s own real result for this game, logged (see
                below) so a caller watching real output over time can
                confirm CFB's real shape without a separate inspection
                pass.
      "home_school" / "away_school": the real resolved CFBD school
                names, or None if match_school couldn't resolve one
                (a genuinely unrecognized/non-FBS opponent).
      "matched": [...], "unmatched": [...],
    }

    Each matched row: {book_key, book_title, price, point, player_name_raw,
    player_id, player_name, position_group, team}.
    Each unmatched row: {book_key, book_title, price, point, player_name_raw,
    match_issue_type}, where match_issue_type is one of:
      "rookie_or_new"         — no real roster row anywhere under this
                                 name (any position, any school) — most
                                 likely a genuinely new/incoming player
                                 CFBD's roster doesn't have yet. Can't be
                                 proven without external data; the best
                                 available heuristic, not a certainty
                                 (same honest framing as NFL's own
                                 version of this category).
      "position_out_of_scope" — a real, matchable player outside
                                 RB/WR/TE (see module docstring on why
                                 this includes QBs, matching NFL's own
                                 real scope).
      "team_mismatch"         — a real RB/WR/TE, just not on either
                                 candidate school — transfer-portal lag,
                                 a school-resolution miss, or a name
                                 collision across DIFFERENT schools.
      "name_collision"        — 2+ real distinct athleteIds share this
                                 exact name on the SAME real candidate
                                 school — a genuine same-team ambiguity,
                                 never silently resolved to whichever one
                                 sorts first (the real, deliberate
                                 addition beyond NFL's own matcher — see
                                 module docstring).
    """
    from poll_ncaaf_prop_coverage import match_school  # local import: avoids a hard dependency for callers who only need the pure classification logic

    home_school = match_school(home_team, fbs_ref, fbs_combos)
    away_school = match_school(away_team, fbs_ref, fbs_combos)
    candidate_schools = {s for s in (home_school, away_school) if s}

    schema = detect_outcome_schema(attd_outcomes, home_team, away_team)
    print(
        f"[attd_match] {away_team} @ {home_team}: detected outcome schema = {schema!r} "
        f"({len(attd_outcomes)} real outcome rows)",
        flush=True,
    )

    name_to_rows: dict[str, list[dict]] = {}
    all_names_ever: set[str] = set()
    skill_names_ever: set[str] = set()
    for r in roster:
        full_name = f"{(r.get('firstName') or '').strip()} {(r.get('lastName') or '').strip()}".strip()
        if not full_name:
            continue
        all_names_ever.add(full_name)
        if r.get("position") in POSITION_GROUPS:
            skill_names_ever.add(full_name)
            name_to_rows.setdefault(full_name, []).append(r)

    matched: list[dict] = []
    unmatched: list[dict] = []

    for outcome in attd_outcomes:
        base = {
            "book_key": outcome.get("book_key"),
            "book_title": outcome.get("book_title"),
            "price": outcome.get("price"),
            "point": outcome.get("point"),
        }
        names_to_try = _candidate_names(outcome, schema)
        if not names_to_try:
            unmatched.append({**base, "player_name_raw": None, "match_issue_type": "rookie_or_new"})
            continue

        resolved = False
        for raw_name in names_to_try:
            candidates = name_to_rows.get(raw_name)
            if not candidates:
                continue
            in_scope = [c for c in candidates if c.get("team") in candidate_schools]
            if len(in_scope) == 1:
                m = in_scope[0]
                matched.append({
                    **base, "player_name_raw": raw_name,
                    "player_id": m.get("id"), "player_name": raw_name,
                    "position_group": m.get("position"), "team": m.get("team"),
                })
                resolved = True
                break
            if len(in_scope) > 1:
                unmatched.append({**base, "player_name_raw": raw_name, "match_issue_type": "name_collision"})
                resolved = True
                break
            # Real RB/WR/TE, just not on either candidate school this game.
            unmatched.append({**base, "player_name_raw": raw_name, "match_issue_type": "team_mismatch"})
            resolved = True
            break

        if resolved:
            continue

        # Neither candidate name matched any real skill-position roster row
        # at all — classify using whichever candidate name is the more
        # informative real one (schema-agnostic "unknown" case may have
        # tried two; the first real name string is the one to classify
        # against, same as a resolved single-field case would).
        raw_name = names_to_try[0]
        if raw_name in skill_names_ever:
            unmatched.append({**base, "player_name_raw": raw_name, "match_issue_type": "team_mismatch"})
        elif raw_name in all_names_ever:
            unmatched.append({**base, "player_name_raw": raw_name, "match_issue_type": "position_out_of_scope"})
        else:
            unmatched.append({**base, "player_name_raw": raw_name, "match_issue_type": "rookie_or_new"})

    return {
        "schema": schema,
        "home_school": home_school,
        "away_school": away_school,
        "matched": matched,
        "unmatched": unmatched,
    }


def implied_probability(price) -> float:
    """
    CFB's own duplicate of the standard American-odds -> implied-
    probability conversion (MLB's scored_picks._implied_probability /
    NFL's market_value.implied_probability -- same real formula,
    duplicated here per this project's own established convention rather
    than cross-imported across a sport boundary). Positive price
    (underdog-style payout): 100 / (price + 100). Negative price
    (favorite-style payout): -price / (-price + 100).
    """
    price = float(price)
    return 100.0 / (price + 100.0) if price > 0 else -price / (-price + 100.0)


def shape_cfb_attd_odds_rows(matched: list[dict], season: int, week: int) -> list[dict]:
    """
    Groups match_cfb_attd_players()'s own real per-(player, book) matched
    rows into one row per real player, with a real `book_odds` column:
    [{"bookmaker", "odds", "implied_prob"}, ...] -- matching MLB's own
    real book_odds shape exactly (pipeline/api/scored_picks._book_odds_
    for_match), NOT NFL's (nfl_content_drafts.book_odds has no
    implied_prob field at all -- confirmed by reading that migration
    directly; a real, flagged divergence between the two sports' existing
    schemas, matching this task's own instruction not to assume MLB and
    NFL agree).

    Deduped by bookmaker, keeping the higher raw American-odds value
    (better payout) on a genuine same-book duplicate for the same
    player -- the identical real dedup rule and reasoning MLB's own
    _book_odds_for_match already uses (a real production incident there:
    the same bookmaker appearing twice with two different prices).
    First-seen bookmaker order is preserved, same reason MLB's version
    preserves it -- a clean input's output order shouldn't depend on
    which duplicate happened to win.

    Call this per real game (match_cfb_attd_players()'s own `matched`
    output for that one game) -- a caller aggregating a whole week
    across multiple games should call this once per game and concatenate,
    since a player belongs to exactly one game per week.
    """
    order: list[str] = []
    by_player: dict[str, dict] = {}
    for row in matched:
        pid = row["player_id"]
        if pid not in by_player:
            order.append(pid)
            by_player[pid] = {
                "player_id": pid,
                "player_name": row["player_name"],
                "position_group": row["position_group"],
                "team": row["team"],
                "season": season,
                "week": week,
                "_book_order": [],
                "_books": {},
            }
        entry = by_player[pid]
        book = row["book_title"] or row["book_key"]
        price = row["price"]
        if price is None:
            continue
        if book not in entry["_books"]:
            entry["_book_order"].append(book)
            entry["_books"][book] = price
        elif price > entry["_books"][book]:
            entry["_books"][book] = price

    rows = []
    for pid in order:
        entry = by_player[pid]
        book_order = entry.pop("_book_order")
        books = entry.pop("_books")
        entry["book_odds"] = [
            {"bookmaker": name, "odds": books[name], "implied_prob": round(implied_probability(books[name]), 4)}
            for name in book_order
        ]
        rows.append(entry)
    return rows
