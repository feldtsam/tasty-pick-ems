"""
Tests the defensive parsing added after a real incident: Make.com calls to
/api/flatten-and-forward returned "success": true, "rows_sent": 0 across
22 real games, with no error surfaced — indistinguishable from "genuinely
no props today" from the outside. These tests simulate the plausible
malformed-input shapes (a nested field arriving as a JSON string instead
of a real array, the same failure mode a no-code tool's text-templated
JSON body construction is prone to) and confirm each one now either
recovers correctly or is clearly diagnosable, never silent.

Run: python3 pipeline/api/test_malformed_input.py
"""
from flatten_hr_props import flatten_hr_props, flatten_hr_props_batch

GOOD_EVENT = {
    "id": "g1",
    "home_team": "Detroit Tigers",
    "away_team": "Kansas City Royals",
    "commence_time": "2026-07-25T17:11:00Z",
    "bookmakers": [{
        "key": "betrivers",
        "title": "BetRivers",
        "markets": [{
            "key": "batter_home_runs",
            "outcomes": [
                {"name": "Over", "description": "Andrew Velazquez", "price": 1100, "point": 0.5},
                {"name": "Over", "description": "Matt Vierling", "price": 750, "point": 0.5},
            ],
        }],
    }],
}


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


if __name__ == "__main__":
    results = []

    # --- Baseline: well-formed input still works exactly as before ---
    diag = {}
    rows = flatten_hr_props(GOOD_EVENT, diagnostics=diag)
    results.append(check("well-formed event still produces 2 rows", len(rows) == 2))
    results.append(check("well-formed event: diagnostics show real data, no recovery flags",
                          diag.get("bookmakers_seen") == 1 and diag.get("outcomes_matching_filter") == 2
                          and "bookmakers_recovered_from_string" not in diag))

    # --- Scenario A: the whole `bookmakers` array arrived as a JSON string ---
    # (e.g. Make.com's text-templated body stringified a nested field)
    import json
    stringified_bookmakers_event = dict(GOOD_EVENT)
    stringified_bookmakers_event["bookmakers"] = json.dumps(GOOD_EVENT["bookmakers"])
    diag = {}
    rows = flatten_hr_props(stringified_bookmakers_event, diagnostics=diag)
    results.append(check("stringified bookmakers[] is recovered, not silently dropped", len(rows) == 2))
    results.append(check("diagnostics flag the recovery happened",
                          diag.get("bookmakers_recovered_from_string") == 1))

    # --- Scenario B: `markets` (one level deeper) arrived as a JSON string ---
    stringified_markets_event = json.loads(json.dumps(GOOD_EVENT))  # deep copy
    stringified_markets_event["bookmakers"][0]["markets"] = json.dumps(GOOD_EVENT["bookmakers"][0]["markets"])
    diag = {}
    rows = flatten_hr_props(stringified_markets_event, diagnostics=diag)
    results.append(check("stringified markets[] is recovered, not silently dropped", len(rows) == 2))
    results.append(check("diagnostics flag the markets-level recovery",
                          diag.get("markets_recovered_from_string") == 1))

    # --- Scenario C: `outcomes` (deepest level) arrived as a JSON string ---
    stringified_outcomes_event = json.loads(json.dumps(GOOD_EVENT))
    stringified_outcomes_event["bookmakers"][0]["markets"][0]["outcomes"] = json.dumps(
        GOOD_EVENT["bookmakers"][0]["markets"][0]["outcomes"]
    )
    diag = {}
    rows = flatten_hr_props(stringified_outcomes_event, diagnostics=diag)
    results.append(check("stringified outcomes[] is recovered, not silently dropped", len(rows) == 2))
    results.append(check("diagnostics flag the outcomes-level recovery",
                          diag.get("outcomes_recovered_from_string") == 1))

    # --- Scenario D: the whole top-level `events` list arrived as a JSON string ---
    diag = {}
    rows = flatten_hr_props_batch(json.dumps([GOOD_EVENT, GOOD_EVENT]), diagnostics=diag)
    results.append(check("stringified events[] is recovered, not iterated character-by-character", len(rows) == 4))
    results.append(check("diagnostics flag the top-level list recovery",
                          diag.get("events_list_recovered_from_string") == 1))

    # --- Scenario E: genuinely empty/no props (the case this must NOT be confused with) ---
    empty_event = {"id": "g2", "home_team": "A", "away_team": "B", "commence_time": "x", "bookmakers": []}
    diag = {}
    rows = flatten_hr_props(empty_event, diagnostics=diag)
    results.append(check("genuinely empty bookmakers -> 0 rows, as before", rows == []))
    results.append(check(
        "genuinely empty case shows 0 bookmakers_seen with NO recovery flags — "
        "distinguishable from a parsing failure at a glance",
        diag.get("bookmakers_seen") == 0 and "bookmakers_recovered_from_string" not in diag
        and "bookmakers_wrong_type" not in diag,
    ))

    # --- Scenario F: bookmakers present, but markets have no HR market at all ---
    no_hr_market_event = json.loads(json.dumps(GOOD_EVENT))
    no_hr_market_event["bookmakers"][0]["markets"][0]["key"] = "batter_total_bases"
    diag = {}
    rows = flatten_hr_props(no_hr_market_event, diagnostics=diag)
    results.append(check("no matching HR market -> 0 rows", rows == []))
    results.append(check(
        "diagnostics show bookmakers WERE seen but hr_markets_seen is 0 — "
        "distinguishes 'no HR market offered' from 'nothing parsed at all'",
        diag.get("bookmakers_seen") == 1 and diag.get("hr_markets_seen", 0) == 0,
    ))

    # --- Scenario G: a non-dict item mixed into the events list doesn't crash the batch ---
    diag = {}
    rows = flatten_hr_props_batch([GOOD_EVENT, "not an event", 12345, None], diagnostics=diag)
    results.append(check("non-dict items in events list are skipped, not crashed on", len(rows) == 2))
    results.append(check("diagnostics count the skipped non-dict items", diag.get("events_not_dict") == 3))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
