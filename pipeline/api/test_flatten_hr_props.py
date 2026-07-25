"""
Tests flatten_hr_props / flatten_hr_props_batch against realistic sample
data before this gets relied on in the live pipeline.

Run: python3 pipeline/test_flatten_hr_props.py
No dependencies beyond the standard library and flatten_hr_props.py.
"""
from flatten_hr_props import flatten_hr_props, flatten_hr_props_batch

# --- Sample data -------------------------------------------------------

# Event 1: exactly the example from the spec. One bookmaker, three
# outcomes — two at point 0.5 (both Over, both should survive), one at
# point 1.5 (should be discarded).
EVENT_1 = {
    "id": "6b06a4bf25ee92a7ac908981664ca759",
    "sport_key": "baseball_mlb",
    "home_team": "Detroit Tigers",
    "away_team": "Kansas City Royals",
    "commence_time": "2026-07-25T17:11:00Z",
    "bookmakers": [
        {
            "key": "betrivers",
            "title": "BetRivers",
            "markets": [
                {
                    "key": "batter_home_runs",
                    "outcomes": [
                        {"name": "Over", "description": "Andrew Velazquez", "price": 1100, "point": 0.5},
                        {"name": "Over", "description": "Matt Vierling", "price": 750, "point": 0.5},
                        {"name": "Over", "description": "Andrew Velazquez", "price": 13000, "point": 1.5},
                    ],
                }
            ],
        }
    ],
}

# Event 2: two bookmakers, and includes an "Under" outcome (should be
# discarded even though its point is 0.5 — only "Over" counts) plus a
# point-2.5 outcome (should be discarded).
EVENT_2 = {
    "id": "event2-yankees-redsox",
    "sport_key": "baseball_mlb",
    "home_team": "Boston Red Sox",
    "away_team": "New York Yankees",
    "commence_time": "2026-07-25T23:05:00Z",
    "bookmakers": [
        {
            "key": "draftkings",
            "title": "DraftKings",
            "markets": [
                {
                    "key": "batter_home_runs",
                    "outcomes": [
                        {"name": "Over", "description": "Aaron Judge", "price": 150, "point": 0.5},
                        {"name": "Under", "description": "Aaron Judge", "price": -200, "point": 0.5},
                        {"name": "Over", "description": "Aaron Judge", "price": 600, "point": 1.5},
                        {"name": "Over", "description": "Rafael Devers", "price": 200, "point": 0.5},
                    ],
                }
            ],
        },
        {
            "key": "fanduel",
            "title": "FanDuel",
            "markets": [
                {
                    "key": "batter_home_runs",
                    "outcomes": [
                        {"name": "Over", "description": "Aaron Judge", "price": 145, "point": 0.5},
                        {"name": "Over", "description": "Aaron Judge", "price": 2500, "point": 2.5},
                        {"name": "Over", "description": "Rafael Devers", "price": 210, "point": 0.5},
                    ],
                }
            ],
        },
    ],
}

# Event 3: one bookmaker with a non-HR market mixed in (should be ignored
# entirely, not just filtered), plus normal HR outcomes.
EVENT_3 = {
    "id": "event3-dodgers-giants",
    "sport_key": "baseball_mlb",
    "home_team": "Los Angeles Dodgers",
    "away_team": "San Francisco Giants",
    "commence_time": "2026-07-26T02:10:00Z",
    "bookmakers": [
        {
            "key": "caesars",
            "title": "Caesars",
            "markets": [
                {
                    "key": "batter_total_bases",
                    "outcomes": [
                        {"name": "Over", "description": "Mookie Betts", "price": -120, "point": 1.5},
                    ],
                },
                {
                    "key": "batter_home_runs",
                    "outcomes": [
                        {"name": "Over", "description": "Mookie Betts", "price": 175, "point": 0.5},
                        {"name": "Over", "description": "Shohei Ohtani", "price": -110, "point": 0.5},
                        {"name": "Over", "description": "Shohei Ohtani", "price": 900, "point": 1.5},
                    ],
                },
            ],
        }
    ],
}

# Event 4: no bookmakers at all — should produce zero rows, not crash.
EVENT_4_NO_BOOKMAKERS = {
    "id": "event4-empty",
    "home_team": "Team A",
    "away_team": "Team B",
    "commence_time": "2026-07-26T18:00:00Z",
    "bookmakers": [],
}


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


if __name__ == "__main__":
    results = []

    # --- Event 1 ---
    rows_1 = flatten_hr_props(EVENT_1)
    results.append(check("Event 1: exactly 2 rows survive (both point 0.5)", len(rows_1) == 2))
    results.append(check(
        "Event 1: Velazquez row is correct",
        {"player_name": "Andrew Velazquez", "odds": 1100, "bookmaker": "BetRivers",
         "game_id": "6b06a4bf25ee92a7ac908981664ca759", "home_team": "Detroit Tigers",
         "away_team": "Kansas City Royals", "commence_time": "2026-07-25T17:11:00Z"} in rows_1,
    ))
    results.append(check(
        "Event 1: no point-1.5 row leaked through",
        not any(r["player_name"] == "Andrew Velazquez" and r["odds"] == 13000 for r in rows_1),
    ))

    # --- Event 2 ---
    rows_2 = flatten_hr_props(EVENT_2)
    results.append(check("Event 2: exactly 4 rows survive (2 players x 2 bookmakers)", len(rows_2) == 4))
    results.append(check(
        "Event 2: no 'Under' outcome leaked through",
        not any(r["odds"] == -200 for r in rows_2),
    ))
    results.append(check(
        "Event 2: no point-1.5 or point-2.5 outcome leaked through",
        not any(r["odds"] in (600, 2500) for r in rows_2),
    ))
    results.append(check(
        "Event 2: both bookmakers represented",
        {r["bookmaker"] for r in rows_2} == {"DraftKings", "FanDuel"},
    ))

    # --- Event 3 ---
    rows_3 = flatten_hr_props(EVENT_3)
    results.append(check("Event 3: exactly 2 rows survive (non-HR market ignored)", len(rows_3) == 2))
    results.append(check(
        "Event 3: batter_total_bases market did not leak in",
        not any(r["odds"] == -120 for r in rows_3),
    ))
    results.append(check(
        "Event 3: point-1.5 Ohtani row discarded",
        not any(r["player_name"] == "Shohei Ohtani" and r["odds"] == 900 for r in rows_3),
    ))

    # --- Event 4 ---
    rows_4 = flatten_hr_props(EVENT_4_NO_BOOKMAKERS)
    results.append(check("Event 4: no bookmakers -> zero rows, no crash", rows_4 == []))

    # --- Batch across all events ---
    batch = flatten_hr_props_batch([EVENT_1, EVENT_2, EVENT_3, EVENT_4_NO_BOOKMAKERS])
    results.append(check("Batch: total row count is 2 + 4 + 2 + 0 = 8", len(batch) == 8))
    results.append(check(
        "Batch: every row has all 7 expected fields",
        all(set(r.keys()) == {"player_name", "odds", "bookmaker", "game_id",
                               "home_team", "away_team", "commence_time"} for r in batch),
    ))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
