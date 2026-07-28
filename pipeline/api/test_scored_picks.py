"""
Tests scored_picks.py at two levels:

1. Synthetic edge cases for match_players()/normalize_name() — accents,
   suffixes, a genuine near-miss spelling (fuzzy path), an ambiguous
   same-name collision, and a fully unmatched name. These are hand-crafted
   on purpose (not real data) specifically to exercise matching paths that
   may not show up in any single real game's odds — a real slate proved
   the primary exact-match path handles more than expected (see below),
   which means the fuzzy/ambiguous paths need their own targeted coverage
   or they'd otherwise be unverified dead code.

2. A REAL end-to-end run against real data pulled for this module: the
   real Odds API batter_home_runs response for BAL @ DET (2026-07-28,
   game_pk 824243) plus that same game's real live-data candidates.
   Confirms the whole pipeline — flatten, fetch, match, score — end to end
   against real inputs, no mocks. Real result, worth noting: The Odds
   API's feed spells a real player "Javier Baez" (no accent) while the MLB
   Stats API spells the same person "Javier Báez" — and normalize_name()'s
   accent-stripping means this is resolved by the EXACT match path, not
   the fuzzy fallback. That's a better outcome than needing fuzzy logic
   for it, but it does mean this one real test case doesn't exercise the
   fuzzy path itself — hence part 1 above.

Run: python3 pipeline/api/test_scored_picks.py
"""
import json
from pathlib import Path

from scored_picks import build_scored_picks_for_game, match_players, normalize_name

REAL_ODDS_FIXTURE = Path("/tmp/real_odds_bal_det.json")  # see conversation — real Odds API pull for game_pk 824243


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


# --- Synthetic candidates for a fake game, exercising every match path ---
FAKE_CANDIDATES = [
    {"player_name": "Ronald Acuña Jr.", "mlbam_id": 1001, "team": "ATL"},
    {"player_name": "Michael Harris II", "mlbam_id": 1002, "team": "ATL"},
    {"player_name": "Luis Garcia", "mlbam_id": 1003, "team": "HOU"},
    {"player_name": "Luis Garcia", "mlbam_id": 1004, "team": "WSH"},  # deliberate same-name collision
    {"player_name": "Bobby Witt Jr.", "mlbam_id": 1005, "team": "KC"},
    {"player_name": "Bench Player", "mlbam_id": 1006, "team": "KC"},  # no odds offered — expected, not an error
]

FAKE_ODDS_ROWS = [
    {"player_name": "Ronald Acuna", "odds": 300, "bookmaker": "DK", "game_id": "x", "home_team": "a", "away_team": "b", "commence_time": "t"},
    {"player_name": "Michael Harris", "odds": 250, "bookmaker": "FD", "game_id": "x", "home_team": "a", "away_team": "b", "commence_time": "t"},
    {"player_name": "Luis Garcia", "odds": 400, "bookmaker": "DK", "game_id": "x", "home_team": "a", "away_team": "b", "commence_time": "t"},
    {"player_name": "Bobby Wit Jr", "odds": 500, "bookmaker": "DK", "game_id": "x", "home_team": "a", "away_team": "b", "commence_time": "t"},
    {"player_name": "Nobody Real", "odds": 1000, "bookmaker": "DK", "game_id": "x", "home_team": "a", "away_team": "b", "commence_time": "t"},
]


if __name__ == "__main__":
    results = []

    # --- normalize_name() unit cases ---
    results.append(check("accent stripped: Báez -> baez", normalize_name("Javier Báez") == "javier baez"))
    results.append(check("suffix stripped: Guerrero Jr. -> guerrero", normalize_name("Vladimir Guerrero Jr.") == "vladimir guerrero"))
    results.append(check("apostrophe stripped: O'Hoppe -> ohoppe", normalize_name("O'Hoppe") == "ohoppe"))
    results.append(check("accent + suffix together: Acuña Jr. -> acuna", normalize_name("Ronald Acuña Jr.") == "ronald acuna"))

    # --- match_players() against the synthetic fixture ---
    result = match_players(FAKE_ODDS_ROWS, FAKE_CANDIDATES)
    matched_by_name = {m["candidate"]["player_name"]: m for m in result["matched"]}

    results.append(check("Acuña matched exactly (accent+suffix both stripped)",
                          "Ronald Acuña Jr." in matched_by_name and matched_by_name["Ronald Acuña Jr."]["match_type"] == "exact"))
    results.append(check("Michael Harris II matched exactly (suffix stripped)",
                          "Michael Harris II" in matched_by_name and matched_by_name["Michael Harris II"]["match_type"] == "exact"))
    results.append(check("Bobby Witt Jr. matched via the FUZZY path (real near-miss spelling: 'Wit' vs 'Witt')",
                          "Bobby Witt Jr." in matched_by_name and matched_by_name["Bobby Witt Jr."]["match_type"] == "fuzzy"))
    results.append(check(
        "the two 'Luis Garcia' candidates are NOT guessed at — reported as an ambiguous unmatched odds entry instead",
        "Luis Garcia" not in matched_by_name
        and any(u["player_name"] == "Luis Garcia" and "ambiguous" in u["reason"] for u in result["unmatched_odds"]),
    ))
    results.append(check(
        "'Nobody Real' has no plausible match and is reported unmatched, not dropped",
        any(u["player_name"] == "Nobody Real" for u in result["unmatched_odds"]),
    ))
    results.append(check(
        "'Bench Player' (no odds offered) shows up in unmatched_candidates, not treated as an error",
        any(c["player_name"] == "Bench Player" for c in result["unmatched_candidates"]),
    ))
    results.append(check("exactly 3 real matches + 2 problem cases (ambiguous, unmatched) accounted for",
                          len(result["matched"]) == 3 and len(result["unmatched_odds"]) == 2))

    # --- REAL end-to-end run: real Odds API data + real live-data for game_pk 824243 ---
    print()
    if REAL_ODDS_FIXTURE.exists():
        raw_odds = json.loads(REAL_ODDS_FIXTURE.read_text())
        real_result = build_scored_picks_for_game(824243, raw_odds)

        print(f"real run: matchup={real_result['matchup']}")
        print(f"  match_summary: {({k: v for k, v in real_result['match_summary'].items() if k != 'unmatched_odds'})}")

        results.append(check("real run: lineup_status is confirmed", real_result["matchup"]["lineup_status"] == "confirmed"))
        results.append(check("real run: all 18 real odds entries matched", real_result["match_summary"]["matched"] == 18))
        results.append(check("real run: zero unmatched odds entries", real_result["match_summary"]["unmatched_odds_count"] == 0))
        results.append(check("real run: zero per-player scoring errors", len(real_result["errors"]) == 0))
        results.append(check("real run: 18 scored picks produced", len(real_result["scored_picks"]) == 18))

        picks = real_result["scored_picks"]
        results.append(check("real run: every scored pick has a final_score in 0-100",
                              all(0 <= p["final_score"] <= 100 for p in picks)))
        results.append(check("real run: final_score distribution isn't degenerate",
                              len({p["final_score"] for p in picks}) > 1))
        results.append(check("real run: star ratings are monotonic with final_score",
                              all(a["star_rating"] >= b["star_rating"] for a in picks for b in picks if a["final_score"] > b["final_score"])))
        results.append(check("real run: the real Báez/Baez accent mismatch resolved via the EXACT path",
                              any(p["player_name"] == "Javier Báez" and p["match_type"] == "exact" for p in picks)))
        results.append(check("real run: every scored pick has a non-null pillar_detail and notes list",
                              all(p["pillar_detail"] and isinstance(p["notes"], list) for p in picks)))

        print(f"\n  top 3 by final_score:")
        for p in sorted(picks, key=lambda x: -x["final_score"])[:3]:
            print(f"    {p['player_name']:<20} {p['team']} odds={p['odds']:>6} final={p['final_score']:>5.1f} stars={p['star_rating']}")
    else:
        print(f"SKIPPED real end-to-end check — {REAL_ODDS_FIXTURE} not present in this environment "
              f"(re-fetch: see conversation for the exact curl against The Odds API for game 824243 / event "
              f"452645d823de15633407805cf5bc269a).")

    # --- Malformed input handling ---
    print()
    bad_shape_result = build_scored_picks_for_game(824243, {"not_a_recognized_shape": True})
    results.append(check(
        "an unrecognized odds payload shape is reported as an error, not a crash",
        len(bad_shape_result["errors"]) == 1 and bad_shape_result["scored_picks"] == [],
    ))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
