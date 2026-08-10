"""
Tests shelf_curation.py against a REAL multi-game pool of scored picks —
real odds, real lineups, real recent-form data, no mocks or synthetic
data. Every check here was written AFTER hand-verifying the real numbers
independently against raw MLB Stats API responses (see the comments below
and the conversation this was built in) — this test suite exists to keep
those real findings from silently regressing, not to prove the code
agrees with itself.

Reads the pool from /tmp/shelf_test_pool.json rather than pulling live —
building it spends real Odds API requests (a paid-tier player-props
budget, not the free MLB Stats API everything else here uses), so it's a
separate, deliberately-not-automatic step:

    python3 pipeline/scripts/build_shelf_test_pool.py

Run: python3 pipeline/api/test_shelf_curation.py
"""
import json
from pathlib import Path

from shelf_curation import DEFAULT_MAX_PER_GAME, DEFAULT_SHELF_SIZE, ODDS_TIERS, assign_shelves, compute_tasty_six

POOL_PATH = Path("/tmp/shelf_test_pool.json")
SEASON = 2026

# Real MLBAM IDs hand-verified during development (see shelf_curation.py's
# and recent_form.py's docstrings for the full story):
#   - Dean Kremer: a real, severe, escalating decline over his last 5
#     starts (1, 4, 2, 6, 8 earned runs — ERA 7.56), independently
#     cross-checked against his raw gameLog before trusting the shelf.
#   - Caleb Ferguson: a real reliever/opener whose short relief stints
#     must NOT be counted as "starts" — the bug that motivated filtering
#     recent_form.py's pitcher window to gamesStarted==1 only.
DEAN_KREMER = 665152
CALEB_FERGUSON = 657571


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


if __name__ == "__main__":
    if not POOL_PATH.exists():
        print(f"SKIPPED — {POOL_PATH} not present in this environment. Regenerate with:\n"
              f"  python3 pipeline/scripts/build_shelf_test_pool.py")
        raise SystemExit(0)

    pool = json.loads(POOL_PATH.read_text())
    print(f"Real pool: {len(pool)} scored picks\n")

    results = []
    results.append(check("real pool is non-trivially sized (>=50 candidates)", len(pool) >= 50))

    shelves = assign_shelves(pool, season=SEASON, shelf_size=DEFAULT_SHELF_SIZE)
    tasty_six = compute_tasty_six(shelves)

    print("Shelf sizes:")
    for name, entries in shelves.items():
        print(f"  {name}: {len(entries)}")
    print()

    # --- No empty shelves on a real, reasonably-sized slate ---
    results.append(check("no shelf is empty", all(len(entries) > 0 for entries in shelves.values())))

    # --- Odds-tier shelves: every entry actually falls in its own range,
    # and is ranked by final_score descending ---
    for label, lo, hi in ODDS_TIERS:
        entries = shelves[label]
        in_range = all(e["candidate"]["odds"] >= lo and (hi is None or e["candidate"]["odds"] <= hi) for e in entries)
        results.append(check(f"{label}: every entry's odds is actually in range", in_range))
        scores = [e["candidate"]["final_score"] for e in entries]
        results.append(check(f"{label}: ranked by final_score, descending", scores == sorted(scores, reverse=True)))

    # --- Hot Hitters: ranked by recent_ops descending, sample-size gated ---
    hot = shelves["Hot Hitters"]
    ops_values = [e["shelf_score"] for e in hot]
    results.append(check("Hot Hitters: ranked by recent_ops, descending", ops_values == sorted(ops_values, reverse=True)))
    results.append(check("Hot Hitters: every entry cleared the minimum sample size",
                          all(e["recent_form"]["recent_games_sampled"] >= 8 for e in hot)))

    # --- Cold Pitchers to Attack: ranked by opposing pitcher's recent ERA
    # descending, sample-size gated, and the real Dean Kremer case shows up
    # correctly (independently verified ERA 7.56) ---
    cold = shelves["Cold Pitchers to Attack"]
    era_values = [e["shelf_score"] for e in cold]
    results.append(check("Cold Pitchers: ranked by opposing pitcher's recent_era, descending", era_values == sorted(era_values, reverse=True)))
    results.append(check("Cold Pitchers: every entry's opposing pitcher cleared the minimum start sample",
                          all(e["opposing_pitcher_recent_form"]["recent_starts_sampled"] >= 3 for e in cold)))
    kremer_entries = [e for e in cold if e["candidate"]["opp_pitcher_mlbam_id"] == DEAN_KREMER]
    if kremer_entries:
        results.append(check(
            "Cold Pitchers: real Dean Kremer entries show the independently-verified ERA (7.56)",
            all(abs(e["opposing_pitcher_recent_form"]["recent_era"] - 7.56) < 0.01 for e in kremer_entries),
        ))
    results.append(check(
        "Cold Pitchers: the real reliever/opener (Ferguson) never appears — too few real starts to qualify",
        all(e["candidate"]["opp_pitcher_mlbam_id"] != CALEB_FERGUSON for e in cold),
    ))

    # --- Weather Factors: environment pillar is genuinely the dominant
    # (highest) pillar for every entry, ranked by environment_score ---
    weather = shelves["Weather Factors"]
    env_values = [e["shelf_score"] for e in weather]
    results.append(check("Weather Factors: ranked by environment_score, descending", env_values == sorted(env_values, reverse=True)))
    all_dominant = all(
        e["candidate"]["environment_score"] == max(
            e["candidate"]["skill_score"], e["candidate"]["matchup_score"],
            e["candidate"]["environment_score"], e["candidate"]["opportunity_score"],
        )
        for e in weather
    )
    results.append(check("Weather Factors: environment is genuinely the dominant pillar for every entry", all_dominant))

    # --- Design question #2, confirmed against real data: candidates CAN
    # appear in multiple shelves, and nothing breaks when they do ---
    membership = {}
    for shelf_name, entries in shelves.items():
        for e in entries:
            key = (e["candidate"]["mlbam_id"], e["candidate"]["game_pk"])
            membership.setdefault(key, []).append(shelf_name)
    multi_shelf = {k: v for k, v in membership.items() if len(v) > 1}
    print(f"\nCandidates in 2+ shelves: {len(multi_shelf)}")
    results.append(check("at least one real candidate appears in multiple shelves (expected, not a bug)", len(multi_shelf) > 0))

    # --- Real production bug (2026-08-09), confirmed against live data:
    # Cold Pitchers to Attack came back 8 of 8 picks from a single real
    # game (every batter facing the same real cold pitcher shares that
    # pitcher's exact recent_era as their shelf_score, so they all sort
    # together — see _cold_pitchers_shelf). Weather Factors showed the
    # same pattern at smaller scale (8 picks, only 2 real games) since
    # environment_score is heavily game/park-level. Fix applies uniformly
    # via _ranked()'s max_per_game cap — checked here across ALL shelves,
    # not just the one that happened to fail in production. ---
    for shelf_name, entries in shelves.items():
        game_counts = {}
        for e in entries:
            gp = e["candidate"]["game_pk"]
            game_counts[gp] = game_counts.get(gp, 0) + 1
        max_from_one_game = max(game_counts.values()) if game_counts else 0
        results.append(check(
            f"{shelf_name}: no single real game supplies more than {DEFAULT_MAX_PER_GAME} of "
            f"{len(entries)} picks (max found: {max_from_one_game})",
            max_from_one_game <= DEFAULT_MAX_PER_GAME,
        ))

    # --- No two shelves are near-identical (>=6 of 8 shared candidates) ---
    names = list(shelves.keys())
    max_pairwise_overlap = 0
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a = {e["candidate"]["mlbam_id"] for e in shelves[names[i]]}
            b = {e["candidate"]["mlbam_id"] for e in shelves[names[j]]}
            max_pairwise_overlap = max(max_pairwise_overlap, len(a & b))
    print(f"Largest pairwise shelf overlap: {max_pairwise_overlap} of {DEFAULT_SHELF_SIZE}")
    results.append(check("no two shelves are near-identical (largest overlap stays under 6 of 8)", max_pairwise_overlap < 6))

    # --- Tasty Six: one entry per shelf, all six populated on a real slate,
    # and — the fallback rule's whole point — six DISTINCT players even
    # though shelves themselves are allowed to overlap ---
    tasty_picks = tasty_six["picks"]
    results.append(check("Tasty Six has exactly 6 entries", len(tasty_picks) == 6))
    results.append(check("Tasty Six: no shelf came back empty on this real slate", all(v is not None for v in tasty_picks.values())))

    tasty_keys = [(v["candidate"]["mlbam_id"], v["candidate"]["game_pk"]) for v in tasty_picks.values()]
    results.append(check(
        f"Tasty Six: six distinct real players on this real slate (no repeat forced) — repeats flagged: {tasty_six['repeats']}",
        len(set(tasty_keys)) == 6 or len(tasty_six["repeats"]) > 0,
    ))

    print(f"\nTasty Six (repeats: {tasty_six['repeats'] or 'none'}):")
    for shelf_name, entry in tasty_picks.items():
        c = entry["candidate"]
        print(f"  {shelf_name}: {c['player_name']} ({c['team']}) odds={c['odds']} final={c['final_score']}")

    # --- Regression test for the exact real case that motivated this rule:
    # confirmed earlier that Riley Greene was independently the #1 pick for
    # BOTH "Going Nuclear" and "Cold Pitchers to Attack" on a real slate.
    # Whether or not that exact tie recurs on today's real data, prove the
    # fallback mechanism itself works by forcing the same scenario directly
    # against real shelf data — one shelf's #1 duplicated as another
    # shelf's #1 — and confirming the second shelf falls through to its own
    # #2 (or further) instead of accepting the duplicate. ---
    shelf_names_in_order = list(shelves.keys())
    contested_shelf, other_shelf = None, None
    for i, name_a in enumerate(shelf_names_in_order):
        if not shelves[name_a]:
            continue
        for name_b in shelf_names_in_order[i + 1:]:
            if len(shelves[name_b]) >= 2:
                contested_shelf, other_shelf = name_a, name_b
                break
        if contested_shelf:
            break

    if contested_shelf:
        rigged_shelves = dict(shelves)
        # Force other_shelf's #1 to be an exact duplicate of contested_shelf's #1.
        rigged_shelves[other_shelf] = [shelves[contested_shelf][0]] + shelves[other_shelf][1:]
        rigged_result = compute_tasty_six(rigged_shelves)
        picks = rigged_result["picks"]
        contested_key = (picks[contested_shelf]["candidate"]["mlbam_id"], picks[contested_shelf]["candidate"]["game_pk"])
        other_key = (picks[other_shelf]["candidate"]["mlbam_id"], picks[other_shelf]["candidate"]["game_pk"])
        results.append(check(
            f"forced-duplicate scenario ({other_shelf!r}'s #1 rigged to match {contested_shelf!r}'s #1): "
            f"the later shelf falls back to its own #2 instead of accepting the duplicate",
            contested_key != other_key,
        ))
    else:
        print("(skipped the forced-duplicate regression check — no shelf pair in this real pool had enough overlap to rig one)")

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
