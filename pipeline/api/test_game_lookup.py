"""
Tests game_lookup.py against REAL MLB schedule data — no mocks, no
fixtures, same rigor as everything else in this project. Specifically
targets the two real gotchas found while building this: a real
cross-UTC-midnight game whose commence_time and officialDate fall on
different calendar dates, and a real doubleheader (two real games between
the same two teams on the same date).

Run: python3 pipeline/api/test_game_lookup.py
"""
from game_lookup import normalize_team_name, resolve_game_pk
from mlb_schedule import fetch_schedule


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


if __name__ == "__main__":
    results = []

    # --- normalize_team_name() unit cases ---
    results.append(check("case-insensitive", normalize_team_name("DETROIT TIGERS") == normalize_team_name("Detroit Tigers")))
    results.append(check("period stripped: 'St. Louis Cardinals'", normalize_team_name("St. Louis Cardinals") == "st louis cardinals"))
    results.append(check("whitespace collapsed", normalize_team_name("Detroit   Tigers") == normalize_team_name("Detroit Tigers")))

    # --- Real, ordinary same-day case: BAL @ DET, 2026-07-28, game_pk 824243 ---
    # commence_time here is the game's OWN real gameDate — no cross-midnight
    # gotcha involved, confirms the straightforward path works.
    result = resolve_game_pk("Detroit Tigers", "Baltimore Orioles", "2026-07-28T22:40:00Z")
    results.append(check("ordinary same-day match resolves to the real game_pk (824243)", result["game_pk"] == 824243))
    # NOTE: BAL @ DET is a real multi-game series (both teams also played the
    # day before) — this real data caught an earlier design bug (see module
    # docstring): candidates from BOTH searched dates always compete on
    # closest-actual-time, so disambiguated_by_time being True here is
    # correct, not a sign anything's wrong — it means the closest-time
    # tiebreak correctly ran and still landed on the right game_pk.
    results.append(check("824243 is the closest-time match even though the day-before series game is also a real candidate",
                          824243 in result["candidates"] and result["game_pk"] == 824243))

    # --- The real cross-midnight gotcha: HOU @ LAA, commence_time
    # "2026-07-29T01:38:00Z" but MLB's own officialDate is 2026-07-28.
    # First, confirm the gotcha itself is real (not assumed): querying the
    # commence_time's own UTC date directly does NOT find this game.
    games_on_naive_date = fetch_schedule("2026-07-29")
    naive_pks = {g["game_pk"] for g in games_on_naive_date}
    results.append(check(
        "confirms the real gotcha: game_pk 824003 is NOT in the naive commence_time UTC date's schedule",
        824003 not in naive_pks,
    ))
    # Now confirm resolve_game_pk finds it anyway, by also checking the day before.
    result = resolve_game_pk("Los Angeles Angels", "Houston Astros", "2026-07-29T01:38:00Z")
    results.append(check(
        "cross-midnight game resolves correctly to the real game_pk (824003) by also checking the day before",
        result["game_pk"] == 824003,
    ))

    # --- The real doubleheader: CLE @ CIN, 2026-07-28, two real games:
    # 824490 at 17:40Z, 824489 at 23:10Z. ---
    early = resolve_game_pk("Cincinnati Reds", "Cleveland Guardians", "2026-07-28T17:45:00Z")  # a few min after 824490's real start
    late = resolve_game_pk("Cincinnati Reds", "Cleveland Guardians", "2026-07-28T23:05:00Z")   # a few min before 824489's real start

    results.append(check("doubleheader: commence_time near the EARLY game resolves to 824490", early["game_pk"] == 824490))
    results.append(check("doubleheader: commence_time near the LATE game resolves to 824489", late["game_pk"] == 824489))
    results.append(check("doubleheader: both resolutions are flagged disambiguated_by_time=True", early["disambiguated_by_time"] and late["disambiguated_by_time"]))
    results.append(check(
        "doubleheader: both real game_pks show up as candidates in both directions",
        set(early["candidates"]) == {824490, 824489} and set(late["candidates"]) == {824490, 824489},
    ))

    # --- No match: a team pair that isn't playing near this time ---
    try:
        resolve_game_pk("Detroit Tigers", "Los Angeles Angels", "2026-07-28T22:40:00Z")
        results.append(check("a real but non-matching team pair raises ValueError instead of guessing", False))
    except ValueError as e:
        results.append(check("a real but non-matching team pair raises ValueError instead of guessing", "no MLB schedule match" in str(e)))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
