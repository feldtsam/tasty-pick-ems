"""
Tests recent_form.py against REAL player game logs — no mocks. Confirms
the aggregation math against hand-computed values from a real pitcher's
actual last 5 starts (verified independently against the raw MLB Stats
API response before writing this test, not just trusting the code's own
output).

Run: python3 pipeline/api/live_data/test_recent_form.py
"""
from recent_form import fetch_batters_recent_form, fetch_pitchers_recent_form

SEASON = 2026
MIKE_TROUT = 545361
GEORGE_KIRBY = 669923
# Real bug caught while sanity-checking real shelf_curation.py output by
# hand: Caleb Ferguson is used almost entirely as a reliever/opener in the
# real 2026 game logs (gamesStarted=0 for nearly every appearance, mostly
# 0.1-1.2 IP stints) — an earlier version of _pitcher_recent_form_from_splits
# took the last 5 APPEARANCES regardless of role and produced a wildly
# distorted "5-start" ERA (9.64 over just 4.7 total innings). See below.
CALEB_FERGUSON = 657571


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


if __name__ == "__main__":
    results = []

    # --- Hitter: Mike Trout, real 2026 game log ---
    batters = fetch_batters_recent_form([MIKE_TROUT], SEASON)
    trout = batters[MIKE_TROUT]
    print(f"Trout recent form: {trout}")

    results.append(check("Trout: sampled exactly 15 games (has more than 15 games played this season)",
                          trout["recent_games_sampled"] == 15))
    results.append(check("Trout: recent_ops is a plausible real value (0.400-1.300)",
                          trout["recent_ops"] is not None and 0.400 <= trout["recent_ops"] <= 1.300))
    results.append(check("Trout: recent_hr_per_pa is plausible (0.0-0.15)",
                          trout["recent_hr_per_pa"] is not None and 0.0 <= trout["recent_hr_per_pa"] <= 0.15))
    results.append(check("Trout: recent_plate_appearances is plausible for 15 games (30-90)",
                          30 <= trout["recent_plate_appearances"] <= 90))

    # --- Pitcher: George Kirby, real 2026 game log — cross-checked by hand
    # against the raw gameLog response (last 5 starts: 6/23 IP6 ER1,
    # 6/29 IP8 ER2, 7/8 IP6 ER2, 7/20 IP6 ER0, 7/27 IP4 ER7 HR4 — a real
    # rough final start). Hand computation: 30 IP, 12 ER -> ERA 3.60;
    # 6 HR -> HR/9 1.80. ---
    pitchers = fetch_pitchers_recent_form([GEORGE_KIRBY], SEASON)
    kirby = pitchers[GEORGE_KIRBY]
    print(f"Kirby recent form: {kirby}")

    results.append(check("Kirby: sampled exactly 5 starts", kirby["recent_starts_sampled"] == 5))
    results.append(check("Kirby: recent_innings_pitched matches hand calculation (30.0)",
                          kirby["recent_innings_pitched"] == 30.0))
    results.append(check("Kirby: recent_era matches hand calculation (3.60)",
                          abs(kirby["recent_era"] - 3.60) < 0.01))
    results.append(check("Kirby: recent_hr_per_9 matches hand calculation (1.80)",
                          abs(kirby["recent_hr_per_9"] - 1.80) < 0.01))

    # --- Regression test for the real bug above: a reliever/opener used
    # almost entirely in short relief stints must NOT have his last 5
    # APPEARANCES treated as 5 starts — real 2026 data has him with only
    # ONE real start (gamesStarted=1) all season, well under
    # MIN_PITCHER_RECENT_SAMPLE, which should show up here as a tiny
    # real starts_sampled, not 5. ---
    ferguson = fetch_pitchers_recent_form([CALEB_FERGUSON], SEASON)[CALEB_FERGUSON]
    print(f"Ferguson (real reliever/opener) recent form: {ferguson}")
    results.append(check(
        "a reliever/opener's relief appearances are NOT counted as starts — sampled starts is well under 5",
        ferguson["recent_starts_sampled"] < 5,
    ))

    # --- A player with no current-season log at all should degrade
    # gracefully, not crash. Using an obviously-invalid ID. ---
    empty_batters = fetch_batters_recent_form([1], SEASON)
    results.append(check("a player with no game log gets a clean zero-sample result, not a crash/exception",
                          empty_batters[1]["recent_games_sampled"] == 0 and empty_batters[1]["recent_ops"] is None))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
