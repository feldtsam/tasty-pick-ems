"""
Tests two Evidence Validator fixes, both found by the first real run of
the validator against a real issue (brief_2026w9_20261110T024417,
attempt 2).

PART 1 -- the odds pattern was eating hyphenated adjectives.
  The prose "a near-90% snap share" matched _ODDS_PATTERN as "-90": the
  hyphen of a compound adjective read as a minus sign. Odds are checked
  at EXACT tolerance with no rounding, so a correct, hedged percentage
  hard-failed as invented -- the single hard fail of that run. Worse, the
  odds pass consumes its span first, so _PERCENT_PATTERN never saw "90%"
  and the hedged-rounding path could not rescue it either.

PART 2 -- the name pool held abbreviations, the prose wrote cities.
  entity.team is "DET"; the draft wrote "Detroit", "the Lions", "Miami".
  None matched, so every correct team mention flagged as possibly
  fabricated -- 7 of the 10 needs-review flags on that run. Teams now
  resolve through a 32-team alias map, grounded when a referenced story
  names that team (its own `team`, or either side of its time_window
  matchup) and flagged when it does not.

No API call is made anywhere in this file.

Run: python3 nfl/newsletter/test_weekly_brief_odds_and_teams.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "api"))
sys.path.insert(0, str(HERE.parent / "content_writer"))

from evidence_validator import (  # noqa: E402
    TEAM_ALIASES,
    _extract_numbers,
    _referenced_team_abbrevs,
    _team_grounds,
    _teams_in_time_window,
    check_claim_traceability,
)


def check(label, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    return bool(cond)


def story(team="DET", time_window="Live snapshot, Detroit Lions @ Miami Dolphins, kickoff Nov 8, 1:00 PM ET",
          judgment="season usage (snap_share ~0.882, targets ~11.4/game) over 5 weeks",
          evidence=None):
    s = {
        "intelligence_story_id": "S1",
        "entity": {"player_name": "Amon-Ra St. Brown", "team": team},
        "team": team,
        "time_window": time_window,
        "interrogation": {"judgment": {"what_we_know": judgment}},
    }
    if evidence:
        s["supporting_evidence"] = evidence
    return s


def numeric(text, stories):
    return [x for x in check_claim_traceability(text, stories) if x["claim_type"] == "numeric"]


def entities(text, stories):
    return {x["claim_text"]: x["status"]
            for x in check_claim_traceability(text, stories) if x["claim_type"] == "entity"}


def main():
    r = []
    ST = story()

    # ==============================================================
    # PART 1 -- odds pattern
    # ==============================================================
    parsed = _extract_numbers("a near-90% snap share")
    r.append(check(
        "THE HARD FAIL FROM THE REAL RUN: 'near-90%' is no longer parsed as odds — "
        "it parses once, as a percent, with rounding allowed",
        parsed == [("90%", 90.0, False)],
    ))
    hit = next(x for x in numeric("a near-90% snap share", [ST]) if x["claim_text"] == "90%")
    r.append(check(
        "and it grounds through the percent path, rounded from the real 0.882",
        hit["status"] == "pass" and hit["grounding"] == "rounded" and hit["rounded_from"] == 88.2,
    ))
    r.append(check(
        "'nearly 90%' (the whitespace form) still works too",
        next(x for x in numeric("nearly 90% of snaps", [ST])
             if x["claim_text"] == "90%")["grounding"] == "rounded",
    ))
    r.append(check(
        "an UNHEDGED '90%' against a real 88.2 still fails — the hedge is what unlocks it",
        next(x for x in numeric("a 90% snap share", [ST])
             if x["claim_text"] == "90%")["status"] == "fail",
    ))
    r.append(check(
        "a hyphen inside an ordinary word is not a hedge ('linear-90%' does not round)",
        next(x for x in numeric("a linear-90% thing", [ST])
             if x["claim_text"] == "90%")["status"] == "fail",
    ))

    # genuine odds still parse, and still demand exactness
    r.append(check(
        "GENUINE ODDS still parse, with exact_required=True",
        _extract_numbers("priced at +140 and -442")
        == [("+140", 140.0, True), ("-442", -442.0, True)],
    ))
    odds_story = story(evidence=["Market: -442 (81.6% implied probability)", "+140 elsewhere"])
    r.append(check(
        "a real odds value grounds exactly",
        next(x for x in numeric("the price is -442", [odds_story])
             if x["claim_text"] == "-442")["status"] == "pass",
    ))
    r.append(check(
        "a WRONG odds value still fails — no rounding tolerance for a price",
        next(x for x in numeric("the price is -445", [odds_story])
             if x["claim_text"] == "-445")["status"] == "fail",
    ))
    r.append(check(
        "and a wrong odds value is not rescued by a hedge either "
        "(a price is exact; 'about -445' for a real -442 is a different price)",
        next(x for x in numeric("about -445", [odds_story])
             if x["claim_text"] == "-445")["status"] == "fail",
    ))
    r.append(check(
        "a date is not an odds price ('2026-11-10' yields no -11)",
        not any(t.startswith("-") for t, _v, _e in _extract_numbers("on 2026-11-10 the line moved")),
    ))

    # ==============================================================
    # PART 2 -- team names
    # ==============================================================
    r.append(check("all 32 teams are mapped", len(TEAM_ALIASES) == 32))
    r.append(check(
        "Washington, both LA teams and both NY teams are present",
        {"WAS", "LAR", "LAC", "NYG", "NYJ"} <= set(TEAM_ALIASES),
    ))
    r.append(check(
        "the time_window matchup yields BOTH teams, the opponent included",
        _teams_in_time_window("Live snapshot, Detroit Lions @ Miami Dolphins, kickoff Nov 8") == {"DET", "MIA"},
    ))
    r.append(check(
        "a window naming no team yields none",
        _teams_in_time_window("Season 2026, through Week 8") == set(),
    ))
    r.append(check(
        "the referenced set is the story's own team plus both sides of its matchup",
        _referenced_team_abbrevs([ST]) == {"DET", "MIA"},
    ))

    ref = _referenced_team_abbrevs([ST])
    for name in ("Detroit", "Lions", "Detroit Lions", "Miami", "Dolphins"):
        r.append(check(f"{name!r} grounds for this DET-vs-MIA story", _team_grounds(name, ref) is True))
    r.append(check(
        "'Dallas' in that same story STILL FLAGS — a real team nobody referenced",
        _team_grounds("Dallas", ref) is False,
    ))
    # NB the subject is deliberately not sentence-initial: sentence
    # openers are masked out of name extraction entirely (a separate,
    # intentional trade-off -- see _extract_name_candidates), so a team
    # starting a sentence is never checked either way.
    r.append(check(
        "end to end: Detroit / Lions / Miami all pass as entities, Dallas needs review",
        entities("They watched Detroit lean on the Lions' offense at Miami, unlike Dallas.", [ST])
        == {"Detroit": "pass", "Lions": "pass", "Miami": "pass", "Dallas": "needs_review"},
    ))
    r.append(check(
        "documented trade-off: a sentence-initial team is not extracted at all, so it is "
        "neither grounded nor flagged",
        entities("Detroit leaned on its offense.", [ST]) == {},
    ))

    # shared cities resolve without cross-matching
    nyg = story(team="NYG", time_window="Live snapshot, New York Giants @ Dallas Cowboys")
    nyg_ref = _referenced_team_abbrevs([nyg])
    r.append(check(
        "a Giants story: 'New York' and 'Giants' ground",
        _team_grounds("New York", nyg_ref) is True and _team_grounds("Giants", nyg_ref) is True,
    ))
    r.append(check(
        "NO CROSS-MATCHING: 'Jets' and 'New York Jets' still flag in a Giants story, "
        "despite the shared city",
        _team_grounds("Jets", nyg_ref) is False and _team_grounds("New York Jets", nyg_ref) is False,
    ))
    r.append(check(
        "the Giants window resolves to NYG and DAL, not NYJ",
        nyg_ref == {"NYG", "DAL"},
    ))

    lar = story(team="LAR", time_window="Live snapshot, Los Angeles Rams @ Seattle Seahawks")
    lar_ref = _referenced_team_abbrevs([lar])
    r.append(check(
        "a Rams story: 'Los Angeles' and 'Rams' ground; 'Chargers' does not",
        _team_grounds("Los Angeles", lar_ref) is True
        and _team_grounds("Rams", lar_ref) is True
        and _team_grounds("Chargers", lar_ref) is False,
    ))
    r.append(check(
        "the Rams window resolves to LAR and SEA, not LAC",
        lar_ref == {"LAR", "SEA"},
    ))
    lac = story(team="LAC", time_window="Live snapshot, Los Angeles Chargers @ Denver Broncos")
    r.append(check(
        "and the mirror case: a Chargers story grounds 'Chargers', flags 'Rams'",
        _team_grounds("Chargers", _referenced_team_abbrevs([lac])) is True
        and _team_grounds("Rams", _referenced_team_abbrevs([lac])) is False,
    ))

    # a non-team name is still handled by the player pool
    r.append(check(
        "a non-team name defers to the player-name pool (returns None, not a verdict)",
        _team_grounds("Amon-Ra St. Brown", ref) is None and _team_grounds("Tyreek Hill", ref) is None,
    ))
    r.append(check(
        "A REAL UNKNOWN PLAYER STILL FLAGS alongside the team fix",
        entities("Detroit leaned on Tyreek Hill.", [ST]).get("Tyreek Hill") == "needs_review",
    ))

    print()
    if all(r):
        print(f"All {len(r)} checks passed.")
    else:
        print(f"{len(r) - sum(r)} of {len(r)} checks FAILED — see above.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
