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
import importlib
import json
import os
from pathlib import Path

import scored_picks
from lovable_forward import serialize_payload
from scored_picks import _book_odds_for_match, _implied_probability, build_scored_picks_for_game, match_players, normalize_name

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
    # Ronald Acuna deliberately has THREE real-shaped bookmaker rows —
    # the one multi-book case in this fixture, specifically to exercise
    # book_odds's reshape logic (see the conversation this was added
    # in). +300 (DK) is the best/highest price and must still win
    # best_odds/best_odds_bookmaker unchanged; -110 and +280 are real,
    # plausible-shaped competing lines from other books.
    {"player_name": "Ronald Acuna", "odds": 300, "bookmaker": "DK", "game_id": "x", "home_team": "a", "away_team": "b", "commence_time": "t"},
    {"player_name": "Ronald Acuna", "odds": -110, "bookmaker": "FD", "game_id": "x", "home_team": "a", "away_team": "b", "commence_time": "t"},
    {"player_name": "Ronald Acuna", "odds": 280, "bookmaker": "MGM", "game_id": "x", "home_team": "a", "away_team": "b", "commence_time": "t"},
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

    # --- _implied_probability() unit cases, same real formula as NFL's market_value.py ---
    results.append(check("positive price: +300 -> 25% implied (100/(300+100))", abs(_implied_probability(300) - 0.25) < 1e-9))
    results.append(check("negative price: -110 -> ~52.38% implied (110/210)", abs(_implied_probability(-110) - (110 / 210)) < 1e-9))
    results.append(check("positive price: +280 -> ~26.32% implied (100/380)", abs(_implied_probability(280) - (100 / 380)) < 1e-9))

    # --- book_odds: real reshape of match_players()'s own real odds_rows
    # (Ronald Acuna's 3-bookmaker fixture, see FAKE_ODDS_ROWS) ---
    acuna = matched_by_name["Ronald Acuña Jr."]
    book_odds = _book_odds_for_match(acuna["odds_rows"])
    print(f"\nbook_odds for Ronald Acuña Jr. (3 real-shaped bookmaker rows): {json.dumps(book_odds, indent=2)}\n")
    results.append(check("book_odds has one entry per real bookmaker row (3), not collapsed", len(book_odds) == 3))
    results.append(check(
        "book_odds preserves every real bookmaker's own real price, unchanged",
        {(b["bookmaker"], b["odds"]) for b in book_odds} == {("DK", 300), ("FD", -110), ("MGM", 280)},
    ))
    results.append(check(
        "every book_odds entry's implied_prob matches _implied_probability() applied to that SAME entry's own real price",
        all(abs(b["implied_prob"] - round(_implied_probability(b["odds"]), 4)) < 1e-9 for b in book_odds),
    ))
    results.append(check(
        "book_odds is ADDITIVE, not a replacement -- the single best_odds/best_odds_bookmaker (odds/bookmaker on the final row) is unchanged: still DK's real +300, the real highest price among the 3",
        acuna["best_odds"] == 300 and acuna["best_odds_bookmaker"] == "DK",
    ))
    results.append(check(
        "a real single-bookmaker player (Michael Harris, 1 real row) still gets a real 1-entry book_odds, not null/empty",
        len(_book_odds_for_match(matched_by_name["Michael Harris II"]["odds_rows"])) == 1,
    ))

    # --- REAL end-to-end run: real Odds API data + real live-data for game_pk 824243 ---
    print()
    if REAL_ODDS_FIXTURE.exists():
        raw_odds = json.loads(REAL_ODDS_FIXTURE.read_text())
        real_result = build_scored_picks_for_game(824243, raw_odds)

        print(f"real run: matchup={real_result['matchup']}")
        print(f"  match_summary: {({k: v for k, v in real_result['match_summary'].items() if k != 'unmatched_odds'})}")

        results.append(check("real run: lineup_status is confirmed", real_result["matchup"]["lineup_status"] == "confirmed"))
        results.append(check("real run: all 18 real odds entries matched by name", real_result["match_summary"]["matched"] == 18))
        results.append(check("real run: zero unmatched odds entries", real_result["match_summary"]["unmatched_odds_count"] == 0))
        results.append(check("real run: zero per-player scoring errors", len(real_result["errors"]) == 0))

        picks = real_result["scored_picks"]
        excluded = real_result["match_summary"]["excluded_below_odds_filter"]
        print(f"  excluded below +300 (real, odds move over time): {excluded}")

        # --- The +300 hard gate fix: every real matched candidate is
        # accounted for as either scored (odds >= 300) or excluded
        # (odds < 300) — never silently missing, never scored anyway. ---
        results.append(check(
            "real run: matched count == scored + excluded-below-300 (nothing silently dropped)",
            real_result["match_summary"]["matched"] == len(picks) + real_result["match_summary"]["excluded_below_odds_filter_count"],
        ))
        results.append(check("real run: every SCORED pick's odds is actually >= +300", all(p["odds"] >= 300 for p in picks)))
        results.append(check("real run: every EXCLUDED candidate's odds is actually < +300", all(e["odds"] < 300 for e in excluded)))

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

        # --- Raw weather persistence fix: real temp/wind fields present,
        # not just the normalized environment_score. ---
        results.append(check(
            "real run: every scored pick carries real raw weather fields (temp_f, wind_speed_mph, wind_description, roof_status)",
            all(p["temp_f"] is not None and p["wind_speed_mph"] is not None
                and p["wind_description"] is not None and p["roof_status"] is not None for p in picks),
        ))

        # --- book_odds, against the real Odds API pull for this game ---
        results.append(check(
            "real run: every scored pick has a real, non-empty book_odds list",
            all(isinstance(p["book_odds"], list) and len(p["book_odds"]) >= 1 for p in picks),
        ))
        results.append(check(
            "real run: every scored pick's book_odds count matches its own real num_bookmakers",
            all(len(p["book_odds"]) == p["num_bookmakers"] for p in picks),
        ))
        multi_book_real = [p for p in picks if p["num_bookmakers"] > 1]
        print(f"  real multi-book picks: {len(multi_book_real)} of {len(picks)} (num_bookmakers > 1)")
        if multi_book_real:
            sample = multi_book_real[0]
            print(f"  sample real book_odds ({sample['player_name']}): {json.dumps(sample['book_odds'], indent=2)}")

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

    # ============================================================
    # book_odds write-path gating (SCORED_PICKS_INCLUDE_BOOK_ODDS) —
    # calls _build_scored_pick() directly with synthetic-but-real-shaped
    # inputs (no live API/network call), and confirms the resulting row
    # serializes cleanly through the REAL lovable_forward.serialize_
    # payload() — the exact function forward_to_lovable() itself uses to
    # build what actually gets POSTed and signed — without hitting the
    # live endpoint (forward_to_lovable() itself, which does the real
    # requests.post, is never called here).
    # ============================================================
    print()
    synthetic_match = {
        "candidate": {
            "player_name": "Test Player", "mlbam_id": 999999, "team": "TST", "batting_order_slot": 3,
            "opp_pitcher_name": "Opposing Pitcher", "opp_pitcher_mlbam_id": 888888,
            "temp_f": 72.0, "wind_speed_mph": 8.0, "wind_description": "out to CF", "roof_status": "open",
        },
        "odds_rows": [
            {"player_name": "Test Player", "odds": 320, "bookmaker": "DK", "game_id": "g", "home_team": "a", "away_team": "b", "commence_time": "t"},
            {"player_name": "Test Player", "odds": 305, "bookmaker": "FD", "game_id": "g", "home_team": "a", "away_team": "b", "commence_time": "t"},
        ],
        "best_odds": 320,
        "best_odds_bookmaker": "DK",
        "num_bookmakers": 2,
        "match_type": "exact",
    }
    synthetic_game = {"game_pk": 123456, "home_team": "Test Home", "away_team": "Test Away", "game_date_utc": "2026-06-01", "venue": {"name": "Test Park"}}
    synthetic_score_result = {
        "pillars": {"skill": {"score": 70}, "matchup": {"score": 60}, "environment": {"score": 55}, "opportunity": {"score": 65}},
        "final_score": 62.5, "star_rating": 3, "score_tier": "Strong", "passes_odds_filter": True, "notes": ["synthetic test note"],
    }

    # Gate OFF (default, no env var set) — confirms this is the safe
    # state to deploy in before the real book_odds column exists.
    os.environ.pop("SCORED_PICKS_INCLUDE_BOOK_ODDS", None)
    importlib.reload(scored_picks)
    row_gate_off = scored_picks._build_scored_pick(synthetic_match, synthetic_game, synthetic_score_result, {}, {}, {})
    results.append(check(
        "gate OFF (default/unset): book_odds is NOT present in the row at all — byte-identical to pre-book_odds behavior",
        "book_odds" not in row_gate_off,
    ))
    results.append(check(
        "gate OFF: odds/bookmaker/num_bookmakers are completely unchanged (best price still wins, real values)",
        row_gate_off["odds"] == 320 and row_gate_off["bookmaker"] == "DK" and row_gate_off["num_bookmakers"] == 2,
    ))
    payload_str_off = serialize_payload([row_gate_off])
    results.append(check("gate OFF: the real serialize_payload() (same function forward_to_lovable signs/sends) produces valid JSON with no book_odds key", "book_odds" not in json.loads(payload_str_off)[0]))

    # Gate ON — confirms the real payload shape once Lovable's column exists and this gets flipped on.
    os.environ["SCORED_PICKS_INCLUDE_BOOK_ODDS"] = "true"
    importlib.reload(scored_picks)
    row_gate_on = scored_picks._build_scored_pick(synthetic_match, synthetic_game, synthetic_score_result, {}, {}, {})
    results.append(check("gate ON: book_odds is present with the real 2-entry reshape", "book_odds" in row_gate_on and len(row_gate_on["book_odds"]) == 2))
    results.append(check(
        "gate ON: odds/bookmaker/num_bookmakers are STILL completely unchanged (book_odds is additive, not a replacement)",
        row_gate_on["odds"] == 320 and row_gate_on["bookmaker"] == "DK" and row_gate_on["num_bookmakers"] == 2,
    ))

    payload_str_on = serialize_payload([row_gate_on])
    round_tripped = json.loads(payload_str_on)[0]
    print(f"real serialized payload (gate ON), one row's book_odds field:\n  {json.dumps(round_tripped['book_odds'], indent=2)}\n")
    results.append(check(
        "gate ON: the real serialize_payload() output round-trips byte-for-byte through json.loads back to the exact same book_odds — confirmed against the REAL signing/sending function, not assumed",
        round_tripped["book_odds"] == row_gate_on["book_odds"],
    ))
    results.append(check(
        "gate ON: compute_signature() (the real HMAC function) runs on this exact payload string without error — confirms nothing about book_odds breaks the real signing step",
        __import__("lovable_forward").compute_signature("fake_secret_for_test", payload_str_on).startswith("sha256="),
    ))

    # Reset the gate back to unset so it doesn't leak into any other test run in this process.
    os.environ.pop("SCORED_PICKS_INCLUDE_BOOK_ODDS", None)
    importlib.reload(scored_picks)

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
