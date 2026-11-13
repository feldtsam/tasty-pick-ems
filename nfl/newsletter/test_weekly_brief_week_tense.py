"""
Tests the week-tense check added after the published 2026 Week 10 issue.

Every Intelligence family except market_intelligence is keyed to
issue_week - 1 (prep_interrogation_eps.expected_week_for_family), so a
Defensive Trends or Role Changes story in a Week 10 brief is describing
Week 9. Nothing checked that, and two claims reached readers:

  "Tyler Allgeier faces them this week with a real chance at goal-line
   work."   -- he had faced Seattle in WEEK 9; Seattle's Week 10 game
               was at Las Vegas.
  "...with Wilson still out..."
               -- an injury status as it stood in Week 9.

Market stories are keyed to the issue week itself and name their real
upcoming matchup, so "this week" is correct in them and must not flag.

The check is needs_review, never a hard fail: it judges wording, not
arithmetic, and blocking a finished brief over a preposition is the
trade weekly_brief_shape.py already learned not to make.

No API call is made anywhere in this file.

Run: python3 nfl/newsletter/test_weekly_brief_week_tense.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "api"))
sys.path.insert(0, str(HERE.parent / "content_writer"))

from evidence_validator import (  # noqa: E402
    FORWARD_LOOKING_PHRASES,
    check_week_tense,
    week_in_text,
)

ISSUE = "2026 NFL Week 10"

DEF_W9 = {
    "intelligence_story_id": "D1",
    "intelligence_family": "defensive_trends",
    "time_window": "Season 2026, last 3 games through Week 9 vs. season-to-date",
}
ROLE_W9 = {
    "intelligence_story_id": "R1",
    "intelligence_family": "role_changes",
    "time_window": "Season 2026, through Week 9",
}
MARKET_W10 = {
    "intelligence_story_id": "M1",
    "intelligence_family": "market_intelligence",
    "time_window": "Live snapshot, Kansas City Chiefs @ Atlanta Falcons, kickoff Nov 15, 1:00 PM ET",
}


def check(label, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    return bool(cond)


def main():
    r = []

    # ==============================================================
    # week_in_text
    # ==============================================================
    r.append(check(
        "week_in_text reads the week a trend window runs THROUGH, not the first number in it",
        week_in_text("Season 2026, last 3 games through Week 9 vs. season-to-date") == 9,
    ))
    r.append(check(
        "week_in_text reads a plain role-change window",
        week_in_text("Season 2026, through Week 9") == 9,
    ))
    r.append(check(
        "week_in_text reads the issue label",
        week_in_text(ISSUE) == 10,
    ))
    r.append(check(
        "a Market time_window names no week, so it returns None (this is what keeps "
        "upcoming-week stories out of the check)",
        week_in_text(MARKET_W10["time_window"]) is None,
    ))
    r.append(check(
        "non-strings and weekless text return None, never 0",
        week_in_text(None) is None and week_in_text("") is None and week_in_text("no week here") is None,
    ))

    # ==============================================================
    # SHOULD FLAG -- the two real published claims
    # ==============================================================
    allgeier = check_week_tense(
        "Tyler Allgeier faces them this week with a real chance at goal-line work.", [DEF_W9], ISSUE)
    r.append(check(
        "THE PUBLISHED BUG: 'faces them this week' over a Week 9 defensive_trends story flags",
        len(allgeier) == 1 and allgeier[0]["status"] == "needs_review",
    ))
    r.append(check(
        "...and it is a FLAG, not a hard fail",
        allgeier[0]["status"] == "needs_review" and allgeier[0]["check"] == "week_tense",
    ))
    r.append(check(
        "...and the detail names both the issue week and the source week, so a human can see the gap",
        "Week 10" in allgeier[0]["detail"] and "Week 9" in allgeier[0]["detail"]
        and "defensive_trends" in allgeier[0]["detail"],
    ))
    wilson = check_week_tense(
        "I'd want to see whether that gap closes this week, with Wilson still out, before I call it settled.",
        [ROLE_W9], ISSUE)
    r.append(check(
        "THE OTHER PUBLISHED BUG: 'still out' over a Week 9 role_changes story flags",
        len(wilson) == 1 and wilson[0]["status"] == "needs_review",
    ))
    r.append(check(
        "the flagged phrases are reported so a reviewer knows what to look for",
        "'this week'" in wilson[0]["claim_text"] and "'still out'" in wilson[0]["claim_text"],
    ))
    r.append(check(
        "'this Sunday' over a Week 9 story flags too",
        len(check_week_tense("exactly what I'd want this Sunday to clarify", [DEF_W9], ISSUE)) == 1,
    ))
    r.append(check(
        "the headline is scanned as well as the body (the caller passes both)",
        len(check_week_tense("Seattle faces a tough back", [DEF_W9], ISSUE)) == 1,
    ))
    r.append(check(
        "a coaching_trends story two weeks back flags as well",
        len(check_week_tense("they face them this week", [{
            "intelligence_family": "coaching_trends",
            "time_window": "Season 2026, last 3 games through Week 8 vs. season-to-date"}], ISSUE)) == 1,
    ))

    # ==============================================================
    # SHOULD NOT FLAG
    # ==============================================================
    market = check_week_tense(
        "I'd want to see this Sunday against Kansas City whether he gets the same carries this week.",
        [MARKET_W10], ISSUE)
    r.append(check(
        "A MARKET STORY FROM THE ISSUE WEEK DOES NOT FLAG — 'this Sunday' and 'this week' are correct there",
        market == [],
    ))
    r.append(check(
        "a Week 9 story with no forward-looking wording does not flag",
        check_week_tense(
            "Seattle allowed two red-zone touchdowns per game over its last three outings.",
            [DEF_W9], ISSUE) == [],
    ))
    r.append(check(
        "the approved phrasings are not caught: 'as of last week', 'in Week 9', 'over the last few games'",
        check_week_tense(
            "With Wilson listed out as of last week, and the trend built in Week 9 over the last few games.",
            [DEF_W9], ISSUE) == [],
    ))
    r.append(check(
        "a story citing nothing does not flag",
        check_week_tense("Everything changes this week.", [], ISSUE) == [],
    ))
    r.append(check(
        "an unreadable issue label is silent, not a false positive",
        check_week_tense("faces them this week", [DEF_W9], "Calibration Fixture V2 -- synthetic") == []
        and check_week_tense("faces them this week", [DEF_W9], None) == [],
    ))
    r.append(check(
        "empty prose is silent",
        check_week_tense("", [DEF_W9], ISSUE) == [],
    ))
    r.append(check(
        "a mixed citation still flags once when ANY cited story is from an earlier week",
        len(check_week_tense("he faces them this week", [MARKET_W10, DEF_W9], ISSUE)) == 1,
    ))

    # ==============================================================
    # the phrase list is the one place to edit
    # ==============================================================
    r.append(check(
        "every phrase in FORWARD_LOOKING_PHRASES actually triggers the check",
        all(len(check_week_tense(f"something {p} something", [DEF_W9], ISSUE)) == 1
            for p in FORWARD_LOOKING_PHRASES),
    ))
    r.append(check(
        "the list is lowercase, so the case-insensitive match can never silently miss",
        all(p == p.lower() for p in FORWARD_LOOKING_PHRASES),
    ))
    r.append(check(
        "matching is case-insensitive in the prose",
        len(check_week_tense("He FACES them THIS WEEK", [DEF_W9], ISSUE)) == 1,
    ))

    print()
    if all(r):
        print(f"All {len(r)} checks passed.")
    else:
        print(f"{len(r) - sum(r)} of {len(r)} checks FAILED — see above.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
