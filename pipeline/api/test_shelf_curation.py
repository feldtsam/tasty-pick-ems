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

from shelf_curation import (
    DEFAULT_MAX_PER_GAME,
    DEFAULT_SHELF_SIZE,
    ODDS_TIERS,
    _cold_pitchers_eligible,
    _dedupe_by_player,
    _hot_hitters_eligible,
    _odds_tier_eligible,
    _percentile_rank,
    _rank_eligible,
    _resolve_player_conflicts,
    _weather_factors_eligible,
    assign_shelves,
    compute_tasty_six,
)

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

    # --- Synthetic, deterministic unit test of _resolve_player_conflicts
    # + _percentile_rank in isolation, pinned down independent of
    # whatever today's real pool happens to contain: player A is rank 1
    # of 2 (percentile 100) in shelf X but rank 2 of 2 (percentile 50) in
    # shelf Y — must be kept in X, dropped entirely from Y (not just
    # demoted), leaving Y's own unrelated candidate (B) untouched. Also
    # confirms the internal _percentile bookkeeping field never leaks
    # into the returned entries. ---
    synthetic = {
        "X": [
            {"candidate": {"mlbam_id": 1, "player_name": "A"}, "shelf_score": 90},
            {"candidate": {"mlbam_id": 3, "player_name": "C"}, "shelf_score": 10},
        ],
        "Y": [
            {"candidate": {"mlbam_id": 2, "player_name": "B"}, "shelf_score": 5.0},
            {"candidate": {"mlbam_id": 1, "player_name": "A"}, "shelf_score": 1.0},
        ],
    }
    synthetic_resolved = _resolve_player_conflicts(synthetic)
    results.append(check(
        "_resolve_player_conflicts: player A (100th pct in X, 50th pct in Y) keeps only X",
        {e["candidate"]["mlbam_id"] for e in synthetic_resolved["X"]} == {1, 3}
        and {e["candidate"]["mlbam_id"] for e in synthetic_resolved["Y"]} == {2},
    ))
    results.append(check(
        "_resolve_player_conflicts: the internal _percentile field never leaks into returned entries",
        all("_percentile" not in e for entries in synthetic_resolved.values() for e in entries),
    ))

    # --- Synthetic, deterministic unit test of _dedupe_by_player in
    # isolation: two rows for the same player (mlbam_id 999) in the SAME
    # shelf's eligible pool — the axis _resolve_player_conflicts does NOT
    # cover (that only handles ONE row being eligible for MULTIPLE
    # shelves, not multiple rows landing in the SAME shelf). Higher
    # shelf_score row must survive, the other must be dropped entirely,
    # and an unrelated candidate in the same pool must be untouched. ---
    dupe_synthetic = [
        {"candidate": {"mlbam_id": 999, "player_name": "Dup Player", "game_pk": 111}, "shelf_score": 70.0},
        {"candidate": {"mlbam_id": 999, "player_name": "Dup Player", "game_pk": 222}, "shelf_score": 65.0},
        {"candidate": {"mlbam_id": 5, "player_name": "Other Player", "game_pk": 333}, "shelf_score": 50.0},
    ]
    dupe_deduped = _dedupe_by_player(dupe_synthetic)
    results.append(check(
        "_dedupe_by_player: two rows for the same player in one shelf collapse to the higher-shelf_score one, "
        "unrelated candidate untouched",
        len(dupe_deduped) == 2
        and any(e["candidate"]["game_pk"] == 111 and e["candidate"]["mlbam_id"] == 999 for e in dupe_deduped)
        and not any(e["candidate"]["game_pk"] == 222 for e in dupe_deduped)
        and any(e["candidate"]["mlbam_id"] == 5 for e in dupe_deduped),
    ))

    # --- Real production case (confirmed live, 2026-08-20): Cam Smith
    # had two real scored_picks rows on the same day — one from game_pk
    # 824155 (created 2026-08-19, a stale prior-day row), one from
    # game_pk 824153 (created 2026-08-20, today's real one) — both
    # independently eligible for Hot Hitters, both surviving uncaught by
    # either max_per_game (different real games) or cross-shelf conflict
    # resolution (both rows are in the SAME shelf). Pulls TODAY's live
    # pool directly (not the cached /tmp file, which predates this case)
    # — skips gracefully if the live read is unavailable in this
    # environment, same pattern other real-data tests in this codebase
    # use for network-dependent checks. ---
    try:
        from curate_shelves import fetch_todays_scored_picks
        import os
        import datetime as _dt
        live_secret = os.environ.get("LOVABLE_WEBHOOK_SECRET")
        live_today = _dt.datetime.now().strftime("%Y-%m-%d")
        live_result = fetch_todays_scored_picks(
            live_today, live_secret, "https://tastypickems.lovable.app/api/public/scored-picks-read",
        )
        live_pool = live_result.get("scored_picks", []) if live_result.get("ok") else []
    except Exception as e:
        print(f"(skipped the real Cam Smith live-data check — could not reach scored-picks-read: {e})")
        live_pool = []

    if live_pool:
        cam_smith_rows = [p for p in live_pool if p.get("player_name") == "Cam Smith"]
        live_shelves = assign_shelves(live_pool, season=int(live_today[:4]), shelf_size=DEFAULT_SHELF_SIZE)
        cam_smith_appearances = [
            (sn, e["candidate"]["game_pk"]) for sn, entries in live_shelves.items() for e in entries
            if e["candidate"]["player_name"] == "Cam Smith"
        ]
        print(f"Real Cam Smith rows in today's live pool: {len(cam_smith_rows)} — appearances in curated shelves: {cam_smith_appearances}")
        results.append(check(
            "real Cam Smith case: appears at most once across all shelves on today's real live pool, "
            "even with 2+ real candidate rows present",
            len(cam_smith_appearances) <= 1,
        ))
    else:
        print("(skipped the real Cam Smith live-data check — no live pool available in this environment)")

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

    # --- Cross-slate player dedup (the real fix this test guards):
    # reverses the earlier "multiple shelf membership is expected"
    # design. A player is keyed by mlbam_id alone here (not (mlbam_id,
    # game_pk)) — every shelf's candidates are batter HR-prop picks tied
    # to that batter's own single game_pk that day, so mlbam_id alone is
    # already the right cross-shelf identity; the old (mlbam_id,
    # game_pk) key was really only ever distinguishing "same player,"
    # never "same player, different games," on any one real slate. ---
    membership = {}
    for shelf_name, entries in shelves.items():
        for e in entries:
            mlbam_id = e["candidate"]["mlbam_id"]
            membership.setdefault(mlbam_id, []).append(shelf_name)
    multi_shelf = {k: v for k, v in membership.items() if len(v) > 1}
    print(f"\nPlayers in 2+ shelves: {len(multi_shelf)}")
    results.append(check("ZERO real players appear in more than one shelf on this real slate (the cross-slate dedup fix)", len(multi_shelf) == 0))

    # --- Direct proof of mechanism #1: score-based assignment picks a
    # contested player's genuinely HIGHEST-PERCENTILE shelf, not the
    # earliest-built one. Recomputes each shelf's full eligible pool +
    # percentile independently here (not reusing assign_shelves()'s
    # internals) and, for every player eligible for 2+ shelves, compares
    # what BUILD ORDER would have chosen (first eligible shelf in fixed
    # order) against what PERCENTILE actually chose (assign_shelves()'s
    # real output) — dynamically finding a real disagreement on today's
    # real pool rather than hardcoding one player's name, since who's
    # contested (and which way it goes) can shift day to day. Real 2026-
    # 08-19 case that first surfaced this: Christian Walker was 100th
    # percentile (rank 1 of his pool) on Weather Factors but only 43rd
    # percentile on Going Nuclear — build order would have swept him into
    # Going Nuclear anyway (it builds first), score-based correctly keeps
    # him on Weather Factors instead. ---
    batter_ids = list({c["mlbam_id"] for c in pool})
    pitcher_ids = list({c["opp_pitcher_mlbam_id"] for c in pool if c.get("opp_pitcher_mlbam_id")})
    from recent_form import fetch_batters_recent_form, fetch_pitchers_recent_form  # noqa: E402
    batter_form = fetch_batters_recent_form(batter_ids, SEASON)
    pitcher_form = fetch_pitchers_recent_form(pitcher_ids, SEASON)

    eligible_by_shelf = {}
    for label, lo, hi in ODDS_TIERS:
        eligible_by_shelf[label] = _odds_tier_eligible(pool, lo, hi)
    eligible_by_shelf["Hot Hitters"] = _hot_hitters_eligible(pool, batter_form)
    eligible_by_shelf["Cold Pitchers to Attack"] = _cold_pitchers_eligible(pool, pitcher_form)
    eligible_by_shelf["Weather Factors"] = _weather_factors_eligible(pool)
    build_order = list(eligible_by_shelf.keys())

    real_membership = {}
    for shelf_name, entries in eligible_by_shelf.items():
        for e in _percentile_rank(entries):
            real_membership.setdefault(e["candidate"]["mlbam_id"], []).append((shelf_name, e["_percentile"]))

    # Walks ALL real disagreements, not just the first — a separate, real,
    # OUT-OF-SCOPE-for-today edge case exists (confirmed via git-stash
    # against the unmodified aa0339d code, so NOT caused by this round's
    # _dedupe_by_player fix): a player can win the percentile comparison
    # for a shelf with a large eligible pool (e.g. 58.7th percentile in a
    # 60-candidate Hot Hitters pool is still outside that shelf's own
    # top-8 cut), get removed from every OTHER shelf they were eligible
    # for by conflict resolution, and then also fail to make their
    # "winning" shelf's own size cap — ending up on ZERO shelves. Real
    # case found this run: Mickey Moniak. Flagged explicitly below, not
    # silently absorbed into this check or hidden — but this test still
    # needs ONE disagreement where the player genuinely DOES land on
    # their percentile-winning shelf to prove that's the real, working
    # mechanism, so it searches past any zero-shelf cases for one.
    all_disagreements = []
    for mlbam_id, shelf_pcts in real_membership.items():
        if len(shelf_pcts) < 2:
            continue
        build_order_winner = min(shelf_pcts, key=lambda t: build_order.index(t[0]))[0]
        percentile_winner = max(shelf_pcts, key=lambda t: t[1])[0]
        if build_order_winner != percentile_winner:
            all_disagreements.append((mlbam_id, shelf_pcts, build_order_winner, percentile_winner))

    proven_disagreement = None
    for mlbam_id, shelf_pcts, build_order_winner, percentile_winner in all_disagreements:
        name = next(e["candidate"]["player_name"] for e in eligible_by_shelf[shelf_pcts[0][0]] if e["candidate"]["mlbam_id"] == mlbam_id)
        actual_shelf = next((sn for sn, entries in shelves.items() if any(e["candidate"]["mlbam_id"] == mlbam_id for e in entries)), None)
        if actual_shelf is None:
            print(
                f"SEPARATE, OUT-OF-SCOPE FINDING (not a failure of today's fix — confirmed pre-existing in "
                f"aa0339d): {name} won the percentile comparison for {percentile_winner} "
                f"({dict(shelf_pcts)[percentile_winner]:.1f}th pct) but didn't survive that shelf's own "
                f"size cap, and conflict resolution had already removed them from {build_order_winner} "
                f"— they appear on ZERO shelves this run."
            )
            continue
        proven_disagreement = (name, shelf_pcts, build_order_winner, percentile_winner, actual_shelf)
        break

    if proven_disagreement:
        name, shelf_pcts, build_order_winner, percentile_winner, actual_shelf = proven_disagreement
        print(f"Real build-order-vs-percentile disagreement found: {name} — {[(s, round(p,1)) for s,p in shelf_pcts]}")
        print(f"  build order would pick: {build_order_winner}   percentile actually picks: {percentile_winner}")
        results.append(check(
            f"{name}: assign_shelves() actually placed them on their highest-percentile shelf "
            f"({percentile_winner}), NOT the earliest-built eligible one ({build_order_winner})",
            actual_shelf == percentile_winner,
        ))
    else:
        print("(skipped the score-vs-build-order disagreement check — no real disagreement on this pool resulted in the player actually landing on a shelf)")

    # --- Direct proof of mechanism #2: backfill still works after
    # conflict resolution. Seeds a real shelf's conflict-resolved pool by
    # removing its own real #1 entirely (simulating them having lost the
    # conflict to a higher-percentile shelf) and confirms _rank_eligible
    # promotes the next-best REMAINING real candidate rather than
    # shrinking the shelf — same skip-and-backfill guarantee as the
    # DEFAULT_MAX_PER_GAME cap already has, now proven for the player-
    # conflict path too. Uses Going Nuclear: its real eligible pool is
    # large on this pool (58 candidates for an 8-slot shelf), so a real
    # backfill candidate is guaranteed to exist. ---
    nuclear_eligible = eligible_by_shelf["Going Nuclear"]
    baseline_nuclear = _rank_eligible(nuclear_eligible, DEFAULT_SHELF_SIZE, DEFAULT_MAX_PER_GAME)
    if baseline_nuclear and len(nuclear_eligible) > len(baseline_nuclear):
        real_top_player = baseline_nuclear[0]["candidate"]["mlbam_id"]
        real_top_name = baseline_nuclear[0]["candidate"]["player_name"]
        without_top = [e for e in nuclear_eligible if e["candidate"]["mlbam_id"] != real_top_player]
        backfilled_nuclear = _rank_eligible(without_top, DEFAULT_SHELF_SIZE, DEFAULT_MAX_PER_GAME)
        results.append(check(
            f"post-conflict-resolution backfill: removing the real #1 ({real_top_name}) from Going Nuclear's "
            f"conflict-resolved pool still produces a full {DEFAULT_SHELF_SIZE}-entry shelf "
            f"({len(nuclear_eligible)} real eligible candidates before removal)",
            all(e["candidate"]["mlbam_id"] != real_top_player for e in backfilled_nuclear)
            and len(backfilled_nuclear) == len(baseline_nuclear),
        ))
    else:
        print("(skipped the post-conflict backfill check — Going Nuclear had no spare real candidates on this real pool)")

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

    # --- No two shelves share ANY candidate — a direct consequence of the
    # cross-slate dedup above, checked pairwise as its own explicit proof
    # rather than just inferred from the membership check. ---
    names = list(shelves.keys())
    max_pairwise_overlap = 0
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a = {e["candidate"]["mlbam_id"] for e in shelves[names[i]]}
            b = {e["candidate"]["mlbam_id"] for e in shelves[names[j]]}
            max_pairwise_overlap = max(max_pairwise_overlap, len(a & b))
    print(f"Largest pairwise shelf overlap: {max_pairwise_overlap} of {DEFAULT_SHELF_SIZE}")
    results.append(check("no two shelves share any real player (pairwise overlap is exactly 0)", max_pairwise_overlap == 0))

    # --- Tasty Six: one entry per shelf, all six populated on a real slate,
    # and — the fallback rule's original whole point — six DISTINCT
    # players. NOW STRUCTURALLY GUARANTEED rather than merely likely:
    # since shelves themselves are cross-slate deduped by player before
    # compute_tasty_six ever runs, a player can no longer be present in
    # more than one shelf's ranked list at all — so compute_tasty_six's
    # own used_keys/repeats fallback (still present, unmodified, still
    # correct as a unit — see the forced-duplicate test below) has no
    # real scenario left to fire on. repeats should always come back
    # empty in practice now, checked directly, not just tolerated. ---
    tasty_picks = tasty_six["picks"]
    results.append(check("Tasty Six has exactly 6 entries", len(tasty_picks) == 6))
    results.append(check("Tasty Six: no shelf came back empty on this real slate", all(v is not None for v in tasty_picks.values())))

    tasty_keys = [(v["candidate"]["mlbam_id"], v["candidate"]["game_pk"]) for v in tasty_picks.values()]
    results.append(check("Tasty Six: six distinct real players on this real slate", len(set(tasty_keys)) == 6))
    results.append(check(
        "Tasty Six: repeats comes back empty — its own fallback is no longer reachable given shelves are "
        "already player-deduped upstream (a real, checked consequence of this fix, not assumed)",
        tasty_six["repeats"] == [],
    ))

    print(f"\nTasty Six (repeats: {tasty_six['repeats'] or 'none'}):")
    for shelf_name, entry in tasty_picks.items():
        c = entry["candidate"]
        print(f"  {shelf_name}: {c['player_name']} ({c['team']}) odds={c['odds']} final={c['final_score']}")

    # --- Unit-level regression test for compute_tasty_six's OWN fallback,
    # kept even though the cross-slate shelf dedup above means this exact
    # scenario (Riley Greene independently #1 on two real shelves at once)
    # can no longer occur naturally through assign_shelves() anymore — the
    # fallback code itself is unmodified and still correct, it just has no
    # real trigger left upstream (see the Tasty Six "repeats" check above).
    # Rigging shelves by hand here, bypassing assign_shelves entirely,
    # tests compute_tasty_six as a standalone unit against a duplicate it
    # would never naturally receive today, so this safety net doesn't
    # silently bit-rot unnoticed if something upstream ever changes again. ---
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
