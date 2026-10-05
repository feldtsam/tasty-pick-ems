"""
Unit tests for poll_ncaaf_prop_coverage.parse_event_odds (2026-09-13
ATTD outcome capture fix).

    python3 cfb/scripts/test_poll_ncaaf_prop_coverage.py

IMPORTANT CAVEAT, read before trusting this too far: every fixture below
is HAND-BUILT, not a real captured response -- as of this investigation
(2026-09-13), zero real CFB games had any player_anytime_td coverage
live (every game was 4+ days from kickoff; see the investigation's own
findings report for the exhaustive 57-game probe that confirmed this).
These tests prove parse_event_odds extracts outcome fields correctly
GIVEN a response shaped like the API's documented format — they do NOT
confirm CFB's real outcome shape matches what's assumed here. The two
fixtures below intentionally cover BOTH candidate shapes (NFL-style
inverted, and MLB-style non-inverted) precisely because parse_event_odds
itself takes no position on which one is real for CFB — it captures
`name`/`description` verbatim either way. Re-run with a real captured
response the next time real coverage exists and add a fixture built from
that real data once Part 2's schema question is actually answered.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from poll_ncaaf_prop_coverage import parse_event_odds


def check(label, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    return bool(cond)


# Shaped like NFL's confirmed real format (nfl/market_value.py's own
# docstring): outcome "name" is the constant "Yes", the player is in
# "description", no team on the outcome.
NFL_STYLE_EVENT = {
    "id": "evt1",
    "home_team": "Georgia Bulldogs",
    "away_team": "Alabama Crimson Tide",
    "bookmakers": [
        {
            "key": "draftkings",
            "title": "DraftKings",
            "markets": [
                {
                    "key": "player_anytime_td",
                    "outcomes": [
                        {"name": "Yes", "description": "Trevor Etienne", "price": -140},
                        {"name": "Yes", "description": "Ryan Williams", "price": 165},
                    ],
                },
                {
                    "key": "player_rush_yds",
                    "outcomes": [{"name": "Over", "description": "Trevor Etienne", "price": -115, "point": 74.5}],
                },
            ],
        },
        {
            "key": "fanduel",
            "title": "FanDuel",
            "markets": [
                {
                    "key": "player_anytime_td",
                    "outcomes": [{"name": "Yes", "description": "Trevor Etienne", "price": -130}],
                }
            ],
        },
    ],
}

# Shaped like MLB's non-inverted format (name=player, description=team) --
# included so parse_event_odds is proven NOT to assume the NFL shape.
MLB_STYLE_EVENT = {
    "id": "evt2",
    "home_team": "Team A",
    "away_team": "Team B",
    "bookmakers": [
        {
            "key": "draftkings",
            "title": "DraftKings",
            "markets": [
                {
                    "key": "player_anytime_td",
                    "outcomes": [{"name": "Some Player", "description": "Team A", "price": 120, "point": None}],
                }
            ],
        }
    ],
}

EMPTY_EVENT = {"id": "evt3", "home_team": "X", "away_team": "Y", "bookmakers": []}


if __name__ == "__main__":
    r = []

    markets, attd = parse_event_odds(NFL_STYLE_EVENT)
    r.append(check("book-presence markets unchanged shape: 2 books on player_anytime_td",
                    markets["player_anytime_td"] == ["draftkings", "fanduel"]))
    r.append(check("book-presence markets: player_rush_yds also captured (not ATTD-only)",
                    markets.get("player_rush_yds") == ["draftkings"]))
    r.append(check("3 real ATTD outcome rows captured (2 DK + 1 FD)", len(attd) == 3))
    r.append(check("every captured outcome carries its own book_key", {o["book_key"] for o in attd} == {"draftkings", "fanduel"}))
    r.append(check("name captured verbatim, not reinterpreted (still 'Yes')", all(o["name"] == "Yes" for o in attd)))
    r.append(check("description captured verbatim (real player names, uninterpreted)",
                    {o["description"] for o in attd} == {"Trevor Etienne", "Ryan Williams"}))
    r.append(check("price captured as a real signed int", any(o["price"] == -140 for o in attd)))
    r.append(check("player_rush_yds outcomes are NOT mixed into attd_outcomes (market-scoped)",
                    all("point" not in o or o["point"] is None for o in attd)))

    markets2, attd2 = parse_event_odds(MLB_STYLE_EVENT)
    r.append(check("MLB-style shape: still just captures name/description verbatim, no inversion assumed",
                    attd2[0]["name"] == "Some Player" and attd2[0]["description"] == "Team A"))
    r.append(check("point=None is preserved, not dropped", "point" in attd2[0] and attd2[0]["point"] is None))

    markets3, attd3 = parse_event_odds(EMPTY_EVENT)
    r.append(check("empty bookmakers -> empty markets dict", markets3 == {}))
    r.append(check("empty bookmakers -> empty attd_outcomes list", attd3 == []))

    markets4, attd4 = parse_event_odds({"id": "evt4", "bookmakers": []})
    r.append(check("a non-dict-shaped body doesn't crash parse_event_odds", parse_event_odds(None) == ({}, [])))
    r.append(check("a dict with no bookmakers key doesn't crash", (markets4, attd4) == ({}, [])))

    print()
    p = sum(r)
    print(f"{p}/{len(r)} checks passed")
    raise SystemExit(0 if p == len(r) else 1)
