"""
Unit tests for cfb/attd_match.py (CFB ATTD player-matching layer).

    python3 cfb/test_attd_match.py

Same fixture-based discipline as cfb/scripts/test_poll_ncaaf_prop_
coverage.py's own 14/14 schema-agnostic tests: hand-built fixtures
covering BOTH candidate outcome schemas (NFL-style inverted, MLB-style
non-inverted), since no real live CFB coverage exists to test against
yet (see attd_match.py's own module docstring). The name-collision
fixtures use the REAL athleteIds the 2026-09-13 feasibility investigation
found (Marcel Williams/Akron, DJ Jordan/USC) as concrete regression
cases, not synthetic placeholders.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from attd_match import (
    detect_outcome_schema,
    implied_probability,
    match_cfb_attd_players,
    shape_cfb_attd_odds_rows,
)


def check(label, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    return bool(cond)


# --- Real CFBD roster fixture ------------------------------------------------
# Includes the two REAL known same-school name-collision cases from the
# 2026-09-13 feasibility investigation, with their real athleteIds.
ROSTER = [
    {"id": "1001", "firstName": "Jaylen", "lastName": "Carter", "team": "Georgia", "position": "WR"},
    {"id": "1002", "firstName": "Trevor", "lastName": "Etienne", "team": "Georgia", "position": "RB"},
    {"id": "1003", "firstName": "Marvin", "lastName": "Harrison", "team": "Alabama", "position": "WR"},
    {"id": "1004", "firstName": "Cole", "lastName": "Bishop", "team": "Alabama", "position": "QB"},
    {"id": "1005", "firstName": "Big", "lastName": "Lineman", "team": "Georgia", "position": "OL"},
    # Real known same-school collision #1 (Marcel Williams, Akron, 2 real WRs)
    {"id": "4244849", "firstName": "Marcel", "lastName": "Williams", "team": "Akron", "position": "WR"},
    {"id": "5226912", "firstName": "Marcel", "lastName": "Williams", "team": "Akron", "position": "WR"},
    # Real known same-school collision #2 (DJ Jordan, USC, 2 real WRs)
    {"id": "5123193", "firstName": "DJ", "lastName": "Jordan", "team": "USC", "position": "WR"},
    {"id": "5306715", "firstName": "DJ", "lastName": "Jordan", "team": "USC", "position": "WR"},
    # A real cross-school namesake -- same full name, different (non-
    # candidate) school -- must resolve as team_mismatch when cited for
    # a game neither Akron nor USC is playing in, not a collision.
    {"id": "9999", "firstName": "Marcel", "lastName": "Williams", "team": "Ohio State", "position": "RB"},
]

FBS_REF = {
    "Georgia": {"conf": "SEC", "mascot": "Bulldogs", "tier": "P4"},
    "Alabama": {"conf": "SEC", "mascot": "Crimson Tide", "tier": "P4"},
    "Akron": {"conf": "Mid-American", "mascot": "Zips", "tier": "G5"},
    "USC": {"conf": "Big Ten", "mascot": "Trojans", "tier": "P4"},
    "Ohio State": {"conf": "Big Ten", "mascot": "Buckeyes", "tier": "P4"},
}
FBS_COMBOS = {f"{s} {v['mascot']}".strip(): s for s, v in FBS_REF.items()}


def _outcome(book_key, book_title, name, description, price, point=None):
    return {"book_key": book_key, "book_title": book_title, "name": name, "description": description, "price": price, "point": point}


if __name__ == "__main__":
    r = []

    # --- detect_outcome_schema ---
    r.append(check(
        "NFL-style outcome (name='Yes') detected correctly",
        detect_outcome_schema([_outcome("dk", "DraftKings", "Yes", "Trevor Etienne", -140)], "Georgia Bulldogs", "Alabama Crimson Tide") == "nfl_style",
    ))
    r.append(check(
        "MLB-style outcome (description=team) detected correctly",
        detect_outcome_schema([_outcome("dk", "DraftKings", "Trevor Etienne", "Georgia Bulldogs", -140)], "Georgia Bulldogs", "Alabama Crimson Tide") == "mlb_style",
    ))
    r.append(check("empty outcomes list -> unknown, not a crash", detect_outcome_schema([], "A", "B") == "unknown"))
    r.append(check(
        "a genuinely ambiguous shape (neither heuristic matches) -> unknown",
        detect_outcome_schema([_outcome("dk", "DraftKings", "Trevor Etienne", "", -140)], "Georgia Bulldogs", "Alabama Crimson Tide") == "unknown",
    ))

    # --- match_cfb_attd_players: NFL-style schema, real clean match ---
    outcomes = [
        _outcome("dk", "DraftKings", "Yes", "Trevor Etienne", -140),
        _outcome("fd", "FanDuel", "Yes", "Marvin Harrison", 165),
    ]
    result = match_cfb_attd_players(outcomes, "Alabama Crimson Tide", "Georgia Bulldogs", ROSTER, FBS_REF, FBS_COMBOS)
    r.append(check("schema correctly detected as nfl_style", result["schema"] == "nfl_style"))
    r.append(check("home/away schools resolved correctly", result["home_school"] == "Alabama" and result["away_school"] == "Georgia"))
    r.append(check("both real players matched", len(result["matched"]) == 2 and result["unmatched"] == []))
    r.append(check("matched row carries the real athleteId", any(m["player_id"] == "1002" for m in result["matched"])))

    # --- MLB-style schema, same real game ---
    outcomes_mlb = [
        _outcome("dk", "DraftKings", "Trevor Etienne", "Georgia Bulldogs", -140),
        _outcome("fd", "FanDuel", "Marvin Harrison", "Alabama Crimson Tide", 165),
    ]
    result_mlb = match_cfb_attd_players(outcomes_mlb, "Alabama Crimson Tide", "Georgia Bulldogs", ROSTER, FBS_REF, FBS_COMBOS)
    r.append(check("MLB-style schema correctly detected", result_mlb["schema"] == "mlb_style"))
    r.append(check("MLB-style shape also produces 2 clean matches", len(result_mlb["matched"]) == 2))

    # --- team_mismatch: a real RB/WR/TE, real name, wrong game ---
    outcomes_mismatch = [_outcome("dk", "DraftKings", "Yes", "Marvin Harrison", 150)]
    result_wrong_game = match_cfb_attd_players(outcomes_mismatch, "Akron Zips", "USC Trojans", ROSTER, FBS_REF, FBS_COMBOS)
    r.append(check(
        "a real player cited for a game he's not actually in -> team_mismatch",
        result_wrong_game["unmatched"] and result_wrong_game["unmatched"][0]["match_issue_type"] == "team_mismatch",
    ))

    # --- position_out_of_scope: a real, matchable QB ---
    outcomes_qb = [_outcome("dk", "DraftKings", "Yes", "Cole Bishop", 300)]
    result_qb = match_cfb_attd_players(outcomes_qb, "Alabama Crimson Tide", "Georgia Bulldogs", ROSTER, FBS_REF, FBS_COMBOS)
    r.append(check(
        "a real QB (outside RB/WR/TE scope) -> position_out_of_scope",
        result_qb["unmatched"] and result_qb["unmatched"][0]["match_issue_type"] == "position_out_of_scope",
    ))

    # --- rookie_or_new: no real roster row anywhere ---
    outcomes_unknown_player = [_outcome("dk", "DraftKings", "Yes", "Totally Fictional Player", 500)]
    result_new = match_cfb_attd_players(outcomes_unknown_player, "Alabama Crimson Tide", "Georgia Bulldogs", ROSTER, FBS_REF, FBS_COMBOS)
    r.append(check(
        "a name with no real roster row at all -> rookie_or_new",
        result_new["unmatched"] and result_new["unmatched"][0]["match_issue_type"] == "rookie_or_new",
    ))

    # --- name_collision: the REAL known cases, both directions ---
    outcomes_akron = [_outcome("dk", "DraftKings", "Yes", "Marcel Williams", 400)]
    result_akron = match_cfb_attd_players(outcomes_akron, "Akron Zips", "USC Trojans", ROSTER, FBS_REF, FBS_COMBOS)
    r.append(check(
        "REAL known collision: Marcel Williams/Akron flags name_collision, not a silent pick",
        result_akron["unmatched"] and result_akron["unmatched"][0]["match_issue_type"] == "name_collision",
    ))

    outcomes_usc = [_outcome("dk", "DraftKings", "Yes", "DJ Jordan", 350)]
    result_usc = match_cfb_attd_players(outcomes_usc, "Akron Zips", "USC Trojans", ROSTER, FBS_REF, FBS_COMBOS)
    r.append(check(
        "REAL known collision: DJ Jordan/USC flags name_collision, not a silent pick",
        result_usc["unmatched"] and result_usc["unmatched"][0]["match_issue_type"] == "name_collision",
    ))

    # --- The Ohio State Marcel Williams is a DIFFERENT real namesake --
    # cited for a game neither he nor the Akron pair are actually in ->
    # team_mismatch, not a collision (collision only applies when 2+
    # candidates share BOTH the name AND a real candidate school).
    outcomes_wrong_team_namesake = [_outcome("dk", "DraftKings", "Yes", "Marcel Williams", 400)]
    result_namesake = match_cfb_attd_players(outcomes_wrong_team_namesake, "Alabama Crimson Tide", "Georgia Bulldogs", ROSTER, FBS_REF, FBS_COMBOS)
    r.append(check(
        "a cross-school namesake cited for an unrelated game -> team_mismatch, not name_collision",
        result_namesake["unmatched"] and result_namesake["unmatched"][0]["match_issue_type"] == "team_mismatch",
    ))

    # --- shape_cfb_attd_odds_rows: grouping + dedup + implied_prob ---
    matched_rows = [
        {"book_key": "dk", "book_title": "DraftKings", "price": -140, "point": None,
         "player_name_raw": "Trevor Etienne", "player_id": "1002", "player_name": "Trevor Etienne",
         "position_group": "RB", "team": "Georgia"},
        {"book_key": "fd", "book_title": "FanDuel", "price": -120, "point": None,
         "player_name_raw": "Trevor Etienne", "player_id": "1002", "player_name": "Trevor Etienne",
         "position_group": "RB", "team": "Georgia"},
        # A genuine same-book duplicate for the same player, worse price --
        # must NOT overwrite the better -140 already recorded.
        {"book_key": "dk", "book_title": "DraftKings", "price": -160, "point": None,
         "player_name_raw": "Trevor Etienne", "player_id": "1002", "player_name": "Trevor Etienne",
         "position_group": "RB", "team": "Georgia"},
        {"book_key": "fd", "book_title": "FanDuel", "price": 165, "point": None,
         "player_name_raw": "Marvin Harrison", "player_id": "1003", "player_name": "Marvin Harrison",
         "position_group": "WR", "team": "Alabama"},
    ]
    shaped = shape_cfb_attd_odds_rows(matched_rows, season=2025, week=6)
    r.append(check("shape produces exactly one row per real distinct player", len(shaped) == 2))
    etienne = next(r_ for r_ in shaped if r_["player_id"] == "1002")
    r.append(check("Etienne's book_odds has exactly 2 real books (DK + FD), not 3", len(etienne["book_odds"]) == 2))
    dk_entry = next(b for b in etienne["book_odds"] if b["bookmaker"] == "DraftKings")
    r.append(check("same-book duplicate keeps the BETTER (-140, not -160) real price", dk_entry["odds"] == -140))
    r.append(check("implied_prob is computed and reasonable (0<p<1)", 0 < dk_entry["implied_prob"] < 1))
    r.append(check("book order is first-seen (DraftKings before FanDuel)", [b["bookmaker"] for b in etienne["book_odds"]] == ["DraftKings", "FanDuel"]))
    r.append(check("row carries real season/week", etienne["season"] == 2025 and etienne["week"] == 6))

    # --- implied_probability: known real values ---
    r.append(check("implied_probability(+100) == 0.5 exactly", abs(implied_probability(100) - 0.5) < 1e-9))
    r.append(check("implied_probability(-110) is the real ~0.5238", abs(implied_probability(-110) - 0.5238) < 0.001))
    r.append(check("a positive price implies LESS than 50%", implied_probability(150) < 0.5))
    r.append(check("a negative price implies MORE than 50%", implied_probability(-150) > 0.5))

    print()
    p = sum(r)
    print(f"{p}/{len(r)} checks passed")
    raise SystemExit(0 if p == len(r) else 1)
