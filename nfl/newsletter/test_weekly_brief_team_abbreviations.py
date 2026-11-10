"""
Tests that the team abbreviation the DATABASE actually stores resolves.

nfl_intelligence_stories writes the Rams as "LA", not "LAR". Checked
against 2026 weeks 6-9, all families: "LA" was the only abbreviation in
the entire table that no TEAM_ALIASES key matched, and it carried real
rows (three Week 9 role_changes -- Blake Corum, Puka Nacua, Tyler
Higbee). Because _referenced_team_abbrevs only kept values that were
already keys, it dropped the team outright, so a story about Nacua that
said "the Rams" or "Los Angeles" had nothing to ground against and
flagged as possibly fabricated. The team check also deliberately does
not fall through to the player-name pool, so nothing rescued it.

The fix normalizes the abbreviation on the way in (_ABBREV_ALIASES)
rather than adding a 33rd entry, so each team keeps exactly one
TEAM_ALIASES entry as the single source of its name forms.

No API call is made anywhere in this file.

Run: python3 nfl/newsletter/test_weekly_brief_team_abbreviations.py
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
    _ABBREV_ALIASES,
    _canonical_abbrev,
    _referenced_team_abbrevs,
    _team_grounds,
    check_claim_traceability,
)


def check(label, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    return bool(cond)


def story(team, time_window="Season 2026, through Week 9", player="Puka Nacua"):
    """Shaped like a real role_changes row: the team lives in both
    `team` and `entity.team`, and the time_window names no matchup."""
    return {
        "intelligence_story_id": "S1",
        "entity": {"player_name": player, "team": team, "type": "player"},
        "team": team,
        "time_window": time_window,
        "interrogation": {"judgment": {"what_we_know": "snap_share ~0.712 over 3 weeks"}},
    }


def entities(text, stories):
    return {x["claim_text"]: x["status"]
            for x in check_claim_traceability(text, stories) if x["claim_type"] == "entity"}


def main():
    r = []

    # ==============================================================
    # the inbound normalizer
    # ==============================================================
    r.append(check(
        "THE BUG: the DB's 'LA' now resolves to the Rams entry",
        _canonical_abbrev("LA") == "LAR",
    ))
    r.append(check(
        "'LAR' still resolves to itself -- the canonical spelling keeps working",
        _canonical_abbrev("LAR") == "LAR",
    ))
    r.append(check(
        "'LAC' is untouched: the DB already spells the Chargers canonically",
        _canonical_abbrev("LAC") == "LAC",
    ))
    r.append(check(
        "case and stray punctuation do not matter ('la', ' LA ' both resolve)",
        _canonical_abbrev("la") == "LAR" and _canonical_abbrev(" LA ") == "LAR",
    ))
    r.append(check(
        "a non-team value still resolves to None, not a wrong team",
        _canonical_abbrev("ZZZ") is None and _canonical_abbrev("") is None
        and _canonical_abbrev(None) is None and _canonical_abbrev(123) is None,
    ))
    r.append(check(
        "the Rams keep exactly ONE TEAM_ALIASES entry -- 'LA' is a normalization, "
        "not a 33rd team",
        len(TEAM_ALIASES) == 32 and "LA" not in TEAM_ALIASES and "LAR" in TEAM_ALIASES
        and _ABBREV_ALIASES == {"LA": "LAR"},
    ))

    # ==============================================================
    # a real "LA" story grounds its own team's prose
    # ==============================================================
    la = story("LA")
    la_ref = _referenced_team_abbrevs([la])
    r.append(check(
        "an 'LA' story now puts LAR in play (before the fix this was the empty set)",
        la_ref == {"LAR"},
    ))
    r.append(check(
        "REQUIREMENT 1: 'Rams' and 'Los Angeles' both ground for an 'LA' story",
        _team_grounds("Rams", la_ref) is True
        and _team_grounds("Los Angeles", la_ref) is True,
    ))
    r.append(check(
        "...and so do the full name and the bare abbreviation in prose",
        _team_grounds("Los Angeles Rams", la_ref) is True
        and _team_grounds("LA", la_ref) is True,
    ))
    r.append(check(
        "REQUIREMENT 1, end to end: the prose that used to flag now passes",
        entities("They leaned on Puka Nacua, and the Rams kept him in Los Angeles.", [la])
        == {"Puka Nacua": "pass", "Rams": "pass", "Los Angeles": "pass"},
    ))

    # ==============================================================
    # REQUIREMENT 2 -- the Chargers must not come along for the ride
    # ==============================================================
    r.append(check(
        "REQUIREMENT 2: 'Chargers' does NOT ground for an LA-Rams story",
        _team_grounds("Chargers", la_ref) is False,
    ))
    r.append(check(
        "...nor does the Chargers' full name, despite the shared city",
        _team_grounds("Los Angeles Chargers", la_ref) is False,
    ))
    r.append(check(
        "...and end to end it is flagged for review, not silently accepted",
        entities("The Chargers did it too.", [la]) == {"Chargers": "needs_review"},
    ))
    # the two ways the Chargers legitimately come into play
    la_vs_lac = story("LA", time_window="Live snapshot, Los Angeles Chargers @ Los Angeles Rams")
    r.append(check(
        "REQUIREMENT 2: 'Chargers' DOES ground once they appear in the time_window",
        _referenced_team_abbrevs([la_vs_lac]) == {"LAR", "LAC"}
        and _team_grounds("Chargers", _referenced_team_abbrevs([la_vs_lac])) is True,
    ))
    lac_story = story("LAC", player="Derius Davis")
    r.append(check(
        "REQUIREMENT 2: 'Chargers' DOES ground when it is the story's own team field",
        _referenced_team_abbrevs([lac_story]) == {"LAC"}
        and _team_grounds("Chargers", _referenced_team_abbrevs([lac_story])) is True,
    ))
    r.append(check(
        "and the mirror holds: 'Rams' flags on a Chargers-only story",
        _team_grounds("Rams", _referenced_team_abbrevs([lac_story])) is False,
    ))

    # ==============================================================
    # REQUIREMENT 3 -- LAR still works
    # ==============================================================
    lar = story("LAR")
    lar_ref = _referenced_team_abbrevs([lar])
    r.append(check(
        "REQUIREMENT 3: a canonical 'LAR' story behaves exactly as an 'LA' one",
        lar_ref == {"LAR"} and lar_ref == la_ref,
    ))
    r.append(check(
        "REQUIREMENT 3: 'Rams' and 'Los Angeles' ground, 'Chargers' flags, on 'LAR'",
        _team_grounds("Rams", lar_ref) is True
        and _team_grounds("Los Angeles", lar_ref) is True
        and _team_grounds("Chargers", lar_ref) is False,
    ))
    r.append(check(
        "a full-name time_window still resolves the Rams without any abbreviation",
        _referenced_team_abbrevs([story(None, "Live snapshot, Los Angeles Rams @ Seattle Seahawks")])
        == {"LAR", "SEA"},
    ))

    # ==============================================================
    # every abbreviation the live table actually holds
    # ==============================================================
    # The 32 distinct values found in nfl_intelligence_stories, 2026
    # weeks 6-9, across every family. "LA" is in here and "LAR" is NOT:
    # the canonical spelling never appears in the data at all.
    IN_DATABASE = [
        "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN",
        "DET", "GB", "HOU", "IND", "JAX", "KC", "LA", "LAC", "LV", "MIA",
        "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SEA", "SF", "TB",
        "TEN", "WAS",
    ]
    unresolved = [a for a in IN_DATABASE if _canonical_abbrev(a) is None]
    r.append(check(
        f"every one of the {len(IN_DATABASE)} abbreviations in the live table resolves "
        f"(unresolved: {unresolved or 'none'})",
        not unresolved,
    ))
    r.append(check(
        "...and they cover all 32 teams, each exactly once",
        len({_canonical_abbrev(a) for a in IN_DATABASE}) == 32,
    ))
    r.append(check(
        "the data really does use 'LA' and never 'LAR' -- which is why the fix was needed",
        "LA" in IN_DATABASE and "LAR" not in IN_DATABASE,
    ))

    print()
    if all(r):
        print(f"All {len(r)} checks passed.")
    else:
        print(f"{len(r) - sum(r)} of {len(r)} checks FAILED — see above.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
