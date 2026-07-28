"""
Tests live_data against the REAL, LIVE current MLB slate — no mocks, no
fixtures — the same rigor the rest of this project has used throughout
(backtest/ validated against real seasons, score_candidate tested against
real 2022 stat lines). This is deliberately a network-dependent test: its
entire point is confirming what the real APIs actually return today, not
what we assume they return.

Calls build_candidates_for_game() once per game_pk directly (timed
individually), NOT the build_candidates_for_date() batch wrapper — this is
what actually validates the per-game restructure: each call here is
exactly the same call the deployed /api/live-data/game/<game_pk> route
makes, so the latency measured here is the real number that matters for
Vercel's Hobby-tier timeout, not a whole-slate total that no longer
reflects any single request.

Run: python3 pipeline/api/live_data/test_live_data.py [YYYY-MM-DD]
(defaults to today)
"""
import datetime
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "live_scoring"))

from build_game_candidates import build_candidates_for_game  # noqa: E402
from mlb_schedule import fetch_schedule  # noqa: E402
from score_candidate import score_candidate  # noqa: E402

# Vercel Hobby-tier serverless timeout is 10s. Checked against 8s (not 10s)
# to leave real margin for cold starts / network variance in production,
# rather than a check that would only just barely pass locally.
HOBBY_TIMEOUT_MARGIN_S = 8.0


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


if __name__ == "__main__":
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().isoformat()
    print(f"Testing against real live MLB data for {date}\n")

    schedule_games = fetch_schedule(date)
    results = []
    results.append(check(f"schedule returned at least one game for {date}", len(schedule_games) > 0))

    games = []
    latencies = []
    for sg in schedule_games:
        t0 = time.time()
        result = build_candidates_for_game(sg["game_pk"])
        latencies.append(time.time() - t0)
        games.append(result["game"])

    if latencies:
        print(f"per-game latency: min={min(latencies):.2f}s  avg={sum(latencies)/len(latencies):.2f}s  max={max(latencies):.2f}s\n")
        results.append(check(
            f"every individual game_pk call stayed under {HOBBY_TIMEOUT_MARGIN_S:.0f}s "
            f"(Vercel Hobby's actual limit is 10s — this leaves real margin)",
            max(latencies) < HOBBY_TIMEOUT_MARGIN_S,
        ))

    confirmed = [g for g in games if g["lineup_status"] == "confirmed"]
    not_yet = [g for g in games if g["lineup_status"] == "not_yet_posted"]
    not_happening = [g for g in games if g["lineup_status"] == "not_happening"]
    print(f"games: {len(games)} total, {len(confirmed)} confirmed lineups, "
          f"{len(not_yet)} not yet posted, {len(not_happening)} not happening\n")

    results.append(check(
        "not_happening games (postponed/cancelled) have zero candidates",
        all(len(g["candidates"]) == 0 for g in not_happening),
    ))
    results.append(check(
        "not_yet_posted games have zero candidates (no batting order = not a real candidate)",
        all(len(g["candidates"]) == 0 for g in not_yet),
    ))
    results.append(check(
        "every confirmed game has exactly 18 candidates (9 away + 9 home)",
        all(len(g["candidates"]) == 18 for g in confirmed),
    ))

    all_candidates = [c for g in confirmed for c in g["candidates"]]
    results.append(check("at least one real candidate was built", len(all_candidates) > 0))

    if all_candidates:
        results.append(check(
            "every candidate has a real player_name (non-empty string)",
            all(isinstance(c["player_name"], str) and c["player_name"] for c in all_candidates),
        ))
        results.append(check(
            "every candidate has a batting_order_slot in 1-9",
            all(c["batting_order_slot"] in range(1, 10) for c in all_candidates),
        ))
        results.append(check(
            "every candidate has a _stat_source (never silently unset)",
            all(c.get("_stat_source") in
                ("current_season", "prior_season_2025_fallback", "current_season_small_sample_no_fallback", "unavailable")
                for c in all_candidates),
        ))
        results.append(check(
            "no candidate's barrel_pct is a nonsense value (must be 0-100 or None)",
            all(c["barrel_pct"] is None or 0 <= c["barrel_pct"] <= 100 for c in all_candidates),
        ))
        results.append(check(
            "no candidate's hr_per_pa is negative or absurdly high (must be 0-0.3 or None)",
            all(c["hr_per_pa"] is None or 0 <= c["hr_per_pa"] <= 0.3 for c in all_candidates),
        ))
        results.append(check(
            "opposing pitcher matchup fields present for every candidate with a known opposing pitcher",
            all(c["opp_pitcher_name"] is None or c.get("opp_hr_per_9") is not None for c in all_candidates),
        ))
        results.append(check(
            "temp_f, when present, is in a physically plausible range for an MLB game (30-110F)",
            all(c["temp_f"] is None or 30 <= c["temp_f"] <= 110 for c in all_candidates),
        ))
        results.append(check(
            "roof_status is always one of the four values score_candidate() understands",
            all(c["roof_status"] in ("open", "closed", "dome", "outdoor", None) for c in all_candidates),
        ))

        # --- Feed every real candidate through the actual scorer — the real
        # end-to-end integration check: this is the whole point of matching
        # field names to score_candidate()'s schema. ---
        crashed = []
        scored = []
        for c in all_candidates:
            clean = {k: v for k, v in c.items() if not k.startswith("_") and k not in ("mlbam_id", "opp_pitcher_mlbam_id")}
            try:
                scored.append((c, score_candidate(clean)))
            except Exception as e:  # noqa: BLE001 — deliberately broad, this IS the crash check
                crashed.append((c["player_name"], str(e)))

        results.append(check(
            f"every real candidate ({len(all_candidates)}) scores without crashing",
            len(crashed) == 0,
        ))
        if crashed:
            for name, err in crashed:
                print(f"    CRASHED: {name}: {err}")

        if scored:
            final_scores = [r["final_score"] for _, r in scored]
            results.append(check(
                "final_score distribution isn't degenerate (not all identical)",
                len(set(final_scores)) > 1,
            ))
            results.append(check(
                "final_score stays within the model's 0-100 range for every real candidate",
                all(0 <= s <= 100 for s in final_scores),
            ))

        # --- Flag anything that looks off, rather than assuming success ---
        print()
        no_pitcher_flags = [n for g in games for n in g["notes"] if "no probable pitcher available" in n]
        if no_pitcher_flags:
            print("FLAGGED (missing opposing probable pitcher, needs human review):")
            for n in no_pitcher_flags:
                print(f"  - {n}")
        else:
            print("Every confirmed game had both probable pitchers available at scoring time.")

        no_source_players = [c["player_name"] for c in all_candidates if c.get("_stat_source") == "unavailable"]
        if no_source_players:
            print(f"FLAGGED ({len(no_source_players)}) players with NO stat source at all (fully neutral-scored): {no_source_players}")

        fallback_players = [(c["player_name"], c["_stat_source_note"]) for c in all_candidates if c.get("_stat_source") == "prior_season_2025_fallback"]
        print(f"\n{len(fallback_players)} candidate(s) used the 2025 fallback (small current-season sample):")
        for name, note in fallback_players[:10]:
            print(f"  - {name}: {note}")

        print(f"\nTop 5 scores today:")
        for c, r in sorted(scored, key=lambda x: -x[1]["final_score"])[:5]:
            print(f"  {c['player_name']:<22} {c['team']} final={r['final_score']:>5.1f} stars={r['star_rating']} src={c['_stat_source']}")

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
