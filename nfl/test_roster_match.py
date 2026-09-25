"""
Tests for roster_match.match_player_names' two-pass matching and the
non-player outcome classifier in market_value.

Synthetic rosters throughout — these test matching MECHANISM, not any real
player's real team. The real-data verification lives in the branch's own
report (83 rows across 9 players newly matched on real week-3 data, zero
player_id changes).

Run: python3 nfl/test_roster_match.py
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from market_value import _is_dst_outcome, _non_player_outcome  # noqa: E402
from roster_match import (  # noqa: E402
    _FEED_NAME_ALIASES,
    feed_name_key,
    match_player_names,
    normalize_player_name,
)


def check(label, ok):
    print(f"[{'PASS' if ok else 'FAIL'}] {label}")
    return ok


def roster(*players):
    """players: (name, position, team, player_id)"""
    return pd.DataFrame(
        [{"season": 2099, "player_name": n, "position": p, "team": t, "player_id": i}
         for n, p, t, i in players]
    )


def rows(*specs):
    """specs: (name, {candidate teams})"""
    return pd.DataFrame([{"player_name_raw": n, "_teams": t} for n, t in specs])


def run(r, rs):
    return match_player_names(r, rs, 2099, "player_name_raw", "_teams")


if __name__ == "__main__":
    results = []

    # ---------------- normalize_player_name ----------------
    cases = [
        ("Deebo Samuel Sr.", "deebo samuel"),
        ("Travis Etienne Jr.", "travis etienne"),
        ("Brian Thomas Jr", "brian thomas"),
        ("C.J. Williams", "cj williams"),
        ("Thomas Fidone II", "thomas fidone"),
        ("De'Von Achane", "devon achane"),
        ("Michael Pittman", "michael pittman"),
        ("  Extra   Spaces  ", "extra spaces"),
    ]
    results.append(check(
        "normalize: suffixes, periods, apostrophes and whitespace all reduce to the same key",
        all(normalize_player_name(a) == b for a, b in cases),
    ))
    results.append(check(
        "normalize: a non-string or NaN yields '' so it can never collide with a real name",
        normalize_player_name(None) == "" and normalize_player_name(float("nan")) == "",
    ))
    results.append(check(
        "normalize: nicknames and first-name variants are NOT folded -- 'Josh'/'Joshua' and "
        "'Mike'/'Michael' stay distinct, because folding them could merge two real players",
        normalize_player_name("Josh Allen") != normalize_player_name("Joshua Allen")
        and normalize_player_name("Mike Evans") != normalize_player_name("Michael Evans"),
    ))
    results.append(check(
        "normalize: suffix stripping is whole-token only -- a surname containing a suffix "
        "substring survives ('Ives' keeps its 'iv', 'Sriracha' keeps its 'sr')",
        normalize_player_name("Kevin Ives") == "kevin ives"
        and normalize_player_name("Bo Sriracha") == "bo sriracha",
    ))

    # ---------------- pass 1: exact still wins ----------------
    r = roster(("Chris Godwin Jr.", "WR", "TB", "id-godwin-jr"),
               ("Chris Godwin", "WR", "TB", "id-godwin-exact"))
    m, u = run(rows(("Chris Godwin", {"TB", "MIN"})), r)
    results.append(check(
        "exact match wins: with both an exact and a normalising roster entry present, the "
        "EXACT one is taken -- normalisation can only ever rescue, never overrule",
        len(m) == 1 and m.iloc[0]["player_id"] == "id-godwin-exact" and len(u) == 0,
    ))

    # ---------------- pass 2: the real failures ----------------
    r = roster(("Deebo Samuel Sr.", "WR", "SF", "id-deebo"),
               ("Travis Etienne", "RB", "NO", "id-etienne"),
               ("Brian Thomas Jr.", "WR", "JAX", "id-thomas"),
               ("Thomas Fidone II", "TE", "NYG", "id-fidone"))
    m, u = run(rows(("Deebo Samuel", {"SF", "ARI"}),
                    ("Travis Etienne Jr.", {"NO", "LV"}),
                    ("Brian Thomas Jr", {"JAX", "NE"}),
                    ("Thomas Fidone", {"NYG", "TEN"})), r)
    results.append(check(
        "normalised fallback: all four real-world suffix shapes match -- roster-has-suffix, "
        "feed-has-suffix, period-only difference, and a roman numeral",
        len(m) == 4 and len(u) == 0
        and set(m["player_id"]) == {"id-deebo", "id-etienne", "id-thomas", "id-fidone"},
    ))

    # ---------------- ambiguity is reported, never resolved ----------------
    # A REAL collision: both entries normalise to "josh allen". Note that
    # "Joshua Allen" would NOT collide -- normalisation deliberately does not
    # fold nicknames or first-name variants, only punctuation and suffixes.
    r = roster(("Josh Allen", "WR", "BUF", "id-a"), ("Josh Allen Jr.", "RB", "BUF", "id-b"))
    m, u = run(rows(("Josh Allen Sr.", {"BUF", "MIA"})), r)
    results.append(check(
        "ambiguity: two roster players normalising to one key on a candidate team leaves the "
        "row UNMATCHED as ambiguous_match -- never a silent pick of the first",
        len(m) == 0 and len(u) == 1 and u.iloc[0]["match_issue_type"] == "ambiguous_match",
    ))
    results.append(check(
        "ambiguity: an ambiguous row carries no player_id at all, so nothing downstream can "
        "mistake it for a resolved match",
        "player_id" not in u.columns or pd.isna(u.iloc[0].get("player_id")),
    ))
    m2, u2 = run(rows(("Josh Allen", {"BUF", "MIA"})), r)
    results.append(check(
        "ambiguity: the same pair is NOT ambiguous when the feed name matches one exactly -- "
        "pass 1 resolves it before pass 2 ever runs",
        len(m2) == 1 and m2.iloc[0]["player_id"] == "id-a",
    ))
    r3 = roster(("Josh Allen", "WR", "BUF", "id-a"), ("Josh Allen Jr.", "RB", "MIA", "id-b"))
    m3, u3 = run(rows(("Josh Allen Sr.", {"BUF"}), ), r3)
    results.append(check(
        "ambiguity: the team filter disambiguates when the two players are on different "
        "teams and only one is in the game",
        len(m3) == 1 and m3.iloc[0]["player_id"] == "id-a",
    ))

    # ---------------- honest fallback reasons ----------------
    r = roster(("Real Backfield", "RB", "KC", "id-rb"),
               ("Patrick Mahomes II", "QB", "KC", "id-qb"))
    m, u = run(rows(("Nobody At All", {"KC", "LV"}),
                    ("Patrick Mahomes", {"KC", "LV"}),
                    ("Real Backfield", {"DEN", "LAC"})), r)
    reasons = dict(zip(u["player_name_raw"], u["match_issue_type"]))
    results.append(check(
        "reason: a genuinely absent name is not_on_roster -- renamed from rookie_or_new, "
        "which named a cause the data never supported",
        reasons.get("Nobody At All") == "not_on_roster",
    ))
    results.append(check(
        "reason: a QB whose suffix differs is position_out_of_scope, not not_on_roster -- "
        "the fallbacks are normalised-aware too",
        reasons.get("Patrick Mahomes") == "position_out_of_scope",
    ))
    results.append(check(
        "reason: a real RB/WR/TE not on either candidate team is team_mismatch -- the branch "
        "that never used to fire, because it needed an exact name match first",
        reasons.get("Real Backfield") == "team_mismatch",
    ))
    results.append(check(
        "reason: nothing is filed as rookie_or_new any more",
        "rookie_or_new" not in set(u["match_issue_type"]),
    ))

    # ---------------- the alias map, one check per entry ----------------
    alias_cases = [
        ("Drew Ogletree",   "Andrew Ogletree", "TE", "IND", "id-ogletree"),
        ("Joshua Palmer",   "Josh Palmer",     "WR", "BUF", "id-palmer"),
        ("Zonovan Knight",  "Bam Knight",      "RB", "ARI", "id-knight"),
        ("Hollywood Brown", "Marquise Brown",  "WR", "PHI", "id-brown"),
    ]
    for feed, roster_name, pos, team, pid in alias_cases:
        r = roster((roster_name, pos, team, pid),
                   # a same-surname decoy on the OTHER candidate team, so each
                   # case proves the alias resolved it and not the team filter
                   (f"Decoy {roster_name.split()[-1]}", "RB", "XXX", "id-decoy"))
        m, u = run(rows((feed, {team, "XXX"})), r)
        results.append(check(
            f"alias: '{feed}' resolves to '{roster_name}' ({pos} {team}) -- a nickname "
            f"normalisation cannot reach",
            len(m) == 1 and m.iloc[0]["player_id"] == pid and len(u) == 0,
        ))

    results.append(check(
        "alias: James Jordan is deliberately absent -- no Jordan at RB/WR/TE on either of "
        "its event's teams, so it stays unmatched rather than guessed",
        feed_name_key("James Jordan") == "james jordan"
        and "james jordan" not in _FEED_NAME_ALIASES,
    ))
    results.append(check(
        "alias: the map is exactly the four confirmed entries, so a fifth cannot be added "
        "without a test failing here first",
        set(_FEED_NAME_ALIASES) == {"drew ogletree", "joshua palmer",
                                    "zonovan knight", "hollywood brown"},
    ))
    results.append(check(
        "alias: one direction only -- aliases apply to the FEED name, never the roster, so "
        "a roster entry is never rewritten by one",
        normalize_player_name("Drew Ogletree") == "drew ogletree"
        and feed_name_key("Drew Ogletree") == "andrew ogletree",
    ))
    r = roster(("Andrew Ogletree", "TE", "IND", "id-a"), ("Drew Ogletree", "TE", "IND", "id-b"))
    m, u = run(rows(("Drew Ogletree", {"IND", "HOU"})), r)
    results.append(check(
        "alias: an EXACT roster match still wins over the alias -- if a real 'Drew Ogletree' "
        "existed alongside 'Andrew Ogletree', pass 1 takes him",
        len(m) == 1 and m.iloc[0]["player_id"] == "id-b",
    ))

    # ---------------- non-player outcomes ----------------
    results.append(check(
        "non-player: '<Team> Defense' is caught -- the real bug, where endswith('D/ST') alone "
        "let every team defense through to the matcher and out as a rookie",
        _non_player_outcome("Chicago Bears Defense") == "team_defense"
        and _is_dst_outcome("Chicago Bears Defense"),
    ))
    results.append(check(
        "non-player: the original D/ST spelling still works",
        _non_player_outcome("Seattle Seahawks D/ST") == "team_defense",
    ))
    results.append(check(
        "non-player: 'No Scorer' is a market outcome, kept distinct from a team defense",
        _non_player_outcome("No Scorer") == "market_outcome"
        and _non_player_outcome("no scorer") == "market_outcome",
    ))
    results.append(check(
        "non-player: a real player is untouched, and an unobserved string like 'None' is NOT "
        "swallowed -- guessing at market strings risks eating a real name",
        _non_player_outcome("Deebo Samuel") is None
        and _non_player_outcome("None") is None
        and _non_player_outcome(None) is None,
    ))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
