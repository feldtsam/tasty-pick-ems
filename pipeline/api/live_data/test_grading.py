"""
Tests grading.py against REAL, already-completed MLB games — no mocks.
Every case here is a real player's real box score line from a real
2026-07-28 game, independently confirmed before being trusted (see
grading.py's own docstring for the full story behind each real finding).

Run: python3 pipeline/api/live_data/test_grading.py
"""
from grading import grade_pick, _game_state

# --- Real game 1: BAL @ DET, game_pk 824243, a normal completed game ---
GAME_1 = 824243
EDUARDO_VALENCIA = 680664   # real: 2 HR, 5 PA -> won
GLEYBER_TORRES = 650402     # real: 1 HR, 5 PA -> won
JACKSON_HOLLIDAY = 702616   # real: 0 HR, 3 PA -> lost
BEN_MALGERI = 701162        # real: 0 HR, 2 PA (partial-game substitute) -> lost
HAO_YU_LEE = 701678         # real: 0 PA, benched all game -> void
JEREMIAH_JACKSON = 669236   # real: 0 PA, benched all game -> void

# --- Real game 2: CLE @ CIN, game_pk 824490 — originally postponed
# 2026-07-27, made up as part of a doubleheader, now genuinely Final with
# a real complete box score. Using a second real game/matchup here
# specifically to confirm the "postponed doesn't mean void" finding holds
# beyond just game-state classification — these are REAL graded outcomes
# from a game that was postponed before eventually completing. ---
GAME_2_MADE_UP_AFTER_POSTPONEMENT = 824490
SAL_STEWART = 701398        # real: 1 HR, 5 PA -> won
ELLY_DE_LA_CRUZ = 682829    # real: 0 HR, 5 PA -> lost

# --- Real game 3: ATL @ NYM, game_pk 823598 — postponed from its
# original date, rescheduled under the SAME gamePk, confirmed via direct
# feed/live check to be genuinely "In Progress" (not concluded) as of
# this test. Real, live confirmation of the "pending, not void" case. ---
GAME_3_POSTPONED_THEN_RESCHEDULED_IN_PROGRESS = 823598
ANY_PLAYER_IN_GAME_3 = 683002  # Gunnar Henderson — game state alone determines "pending" regardless of player


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


if __name__ == "__main__":
    results = []

    # --- Real wins ---
    r = grade_pick(EDUARDO_VALENCIA, GAME_1)
    print(f"Eduardo Valencia: {r}")
    results.append(check("real 2-HR game grades as won with home_runs=2", r["status"] == "won" and r["home_runs"] == 2))

    r = grade_pick(GLEYBER_TORRES, GAME_1)
    print(f"Gleyber Torres: {r}")
    results.append(check("real 1-HR game grades as won with home_runs=1", r["status"] == "won" and r["home_runs"] == 1))

    r = grade_pick(SAL_STEWART, GAME_2_MADE_UP_AFTER_POSTPONEMENT)
    print(f"Sal Stewart (postponed-then-made-up game): {r}")
    results.append(check(
        "a real win in a game that was ORIGINALLY POSTPONED still grades correctly once truly Final",
        r["status"] == "won" and r["home_runs"] == 1,
    ))

    # --- Real losses, including a real partial-game substitute ---
    r = grade_pick(JACKSON_HOLLIDAY, GAME_1)
    print(f"Jackson Holliday: {r}")
    results.append(check("real 0-HR full game grades as lost", r["status"] == "lost" and r["home_runs"] == 0 and r["plate_appearances"] == 3))

    r = grade_pick(BEN_MALGERI, GAME_1)
    print(f"Ben Malgeri (real partial-game substitute): {r}")
    results.append(check(
        "a real partial-game substitute (2 PA, not a full game) still grades as a real 'lost', not void",
        r["status"] == "lost" and r["plate_appearances"] == 2,
    ))

    r = grade_pick(ELLY_DE_LA_CRUZ, GAME_2_MADE_UP_AFTER_POSTPONEMENT)
    print(f"Elly De La Cruz (postponed-then-made-up game): {r}")
    results.append(check("a real loss in a game that was originally postponed still grades correctly", r["status"] == "lost"))

    # --- Real "didn't play" cases -> void, not lost ---
    r = grade_pick(HAO_YU_LEE, GAME_1)
    print(f"Hao-Yu Lee (real, benched all game): {r}")
    results.append(check("a real player benched all game (0 PA) grades as void, not lost", r["status"] == "void"))

    r = grade_pick(JEREMIAH_JACKSON, GAME_1)
    print(f"Jeremiah Jackson (real, benched all game): {r}")
    results.append(check("a second real benched player also grades as void", r["status"] == "void"))

    # --- Real pending case: postponed, rescheduled, genuinely still in progress ---
    r = grade_pick(ANY_PLAYER_IN_GAME_3, GAME_3_POSTPONED_THEN_RESCHEDULED_IN_PROGRESS)
    print(f"Game 3 (real, postponed-then-rescheduled, currently in progress): {r}")
    results.append(check(
        "a real game that shows stale 'Postponed' on /schedule but is genuinely live via feed/live grades as pending, NOT void",
        r["status"] == "pending",
    ))

    # --- Idempotency: the whole point of this feature. Call grade_pick()
    # three times for the same real, truly-Final (mlbam_id, game_pk) and
    # require byte-identical results every time — the same discipline as
    # the duplicate-submission test that caught a real upsert bug in
    # scored_picks earlier this project. ---
    repeat_calls = [grade_pick(EDUARDO_VALENCIA, GAME_1) for _ in range(3)]
    results.append(check(
        "grade_pick() is fully idempotent — 3 repeated real calls return byte-identical results",
        repeat_calls[0] == repeat_calls[1] == repeat_calls[2],
    ))

    # --- Error handling ---
    try:
        grade_pick(EDUARDO_VALENCIA, 1)  # confirmed elsewhere: feed/live returns a placeholder for an invalid game_pk, not a 404
        results.append(check("an invalid game_pk raises ValueError instead of a wrong result", False))
    except ValueError as e:
        results.append(check("an invalid game_pk raises ValueError instead of a wrong result", "not a known MLB game" in str(e)))

    try:
        grade_pick(EDUARDO_VALENCIA, GAME_1, pick_type="strikeout_prop")
        results.append(check("an unsupported pick_type raises ValueError rather than silently grading as home_run", False))
    except ValueError as e:
        results.append(check("an unsupported pick_type raises ValueError rather than silently grading as home_run", "not supported" in str(e)))

    # --- Synthetic unit test, clearly labeled as such: a genuinely
    # cancelled-forever game (never made up) wasn't found in real data
    # today — every real postponement encountered either resolved to
    # Final-played or was still pending. This exercises _game_state()'s
    # void branch directly with a hand-built status dict rather than
    # leaving it unverified. ---
    synthetic_cancelled = {"gameData": {"status": {"abstractGameState": "Final", "detailedState": "Cancelled"}}}
    state = _game_state(synthetic_cancelled)
    results.append(check(
        "[SYNTHETIC, no real example found today] a genuinely cancelled-forever game classifies as void",
        state["terminal"] and state["outcome"] == "void",
    ))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
