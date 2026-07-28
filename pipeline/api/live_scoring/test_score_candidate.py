"""
Tests score_candidate() against realistic hand-made candidates — real
players, real 2022 stat lines pulled directly from backtest/'s validated
data (not guessed numbers), spanning elite/weak/middling skill and
matchup combinations, the same way the historical model was validated in
backtest/ before being trusted.

Checks directional sanity (elite should score highest, weak should score
lowest, tough-matchup should meaningfully drag down even an elite hitter)
rather than exact target numbers, since there's no "correct" answer for a
single hypothetical game the way there was for the historical decile
validation — the point is confirming the ported model behaves consistently
with what backtest/ already proved about which factors matter and by how
much.

Run: python3 pipeline/api/live_scoring/test_score_candidate.py
"""
from score_candidate import score_candidate

# Real 2022 stat lines, pulled directly from backtest/data/processed/ —
# see the investigation in this session for the exact query. Not invented.
AARON_JUDGE_2022 = dict(
    barrel_pct=26.5, hard_hit_pct=61.9, avg_exit_velo=95.9, sweet_spot_pct=39.0,
    fb_pct=38.1, pull_pct=47.9, xslg=0.732, xwoba=0.468, hr_per_pa=0.087193,
)
MYLES_STRAW_2022 = dict(
    barrel_pct=0.7, hard_hit_pct=26.5, avg_exit_velo=87.0, sweet_spot_pct=34.1,
    fb_pct=22.5, pull_pct=28.2, xslg=0.316, xwoba=0.281, hr_per_pa=0.0,
)
BRYAN_REYNOLDS_2022 = dict(
    barrel_pct=7.9, hard_hit_pct=42.9, avg_exit_velo=90.2, sweet_spot_pct=35.5,
    fb_pct=27.8, pull_pct=44.7, xslg=0.430, xwoba=0.332, hr_per_pa=0.043974,
)

AARON_NOLA_2022 = dict(  # elite contact suppression, real 2022 stats
    opp_throws="R", opp_barrel_pct_allowed=7.1, opp_hard_hit_pct_allowed=31.6,
    opp_xslg_allowed=0.354, opp_xwoba_allowed=0.262, opp_hr_per_9=0.899218, opp_k_per_9=10.2,
)
YUSEI_KIKUCHI_2022 = dict(  # weak contact suppression, real 2022 stats
    opp_throws="L", opp_barrel_pct_allowed=14.8, opp_hard_hit_pct_allowed=47.9,
    opp_xslg_allowed=0.481, opp_xwoba_allowed=0.366, opp_hr_per_9=2.065868, opp_k_per_9=11.1,
)
LEAGUE_AVERAGE_PITCHER = dict(  # constructed, not a real pitcher — deliberately near league-average
    opp_throws="R", opp_barrel_pct_allowed=9.0, opp_hard_hit_pct_allowed=37.0,
    opp_xslg_allowed=0.400, opp_xwoba_allowed=0.310, opp_hr_per_9=1.2, opp_k_per_9=8.5,
)


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


if __name__ == "__main__":
    results = []

    # --- Candidate 1: elite skill, weak matchup, favorable park/wind ---
    c1 = score_candidate({
        "player_name": "Aaron Judge", "team": "NYY", **AARON_JUDGE_2022,
        "opp_pitcher_name": "Yusei Kikuchi", **YUSEI_KIKUCHI_2022,
        "batter_stand": "R",
        "home_team": "CIN", "wind_speed_mph": 12, "wind_description": "Out To LF",
        "temp_f": 85, "roof_status": "outdoor",
        "batting_order_slot": 3, "odds": 450,
    })

    # --- Candidate 2: weak skill, elite matchup, unfavorable park/wind ---
    c2 = score_candidate({
        "player_name": "Myles Straw", "team": "CLE", **MYLES_STRAW_2022,
        "opp_pitcher_name": "Aaron Nola", **AARON_NOLA_2022,
        "batter_stand": "R",
        "home_team": "DET", "wind_speed_mph": 12, "wind_description": "In From CF",
        "temp_f": 50, "roof_status": "outdoor",
        "batting_order_slot": 9, "odds": 150,
    })

    # --- Candidate 3: middling skill, average matchup, neutral context ---
    c3 = score_candidate({
        "player_name": "Bryan Reynolds", "team": "PIT", **BRYAN_REYNOLDS_2022,
        "opp_pitcher_name": "League-Average Pitcher", **LEAGUE_AVERAGE_PITCHER,
        "batter_stand": "L",
        "home_team": "BOS", "wind_speed_mph": 3, "wind_description": "Calm",
        "temp_f": 70, "roof_status": "outdoor",
        "batting_order_slot": 5, "odds": 350,
    })

    # --- Candidate 4: elite skill, TOUGH matchup — isolates matchup's drag ---
    c4 = score_candidate({
        "player_name": "Aaron Judge", "team": "NYY", **AARON_JUDGE_2022,
        "opp_pitcher_name": "Aaron Nola", **AARON_NOLA_2022,
        "batter_stand": "R",
        "home_team": "BOS", "wind_speed_mph": 3, "wind_description": "Calm",
        "temp_f": 70, "roof_status": "outdoor",
        "batting_order_slot": 3, "odds": 240,
    })

    # --- Candidate 5: weak skill, GREAT context — isolates how much context can lift a bad hitter ---
    c5 = score_candidate({
        "player_name": "Myles Straw", "team": "CLE", **MYLES_STRAW_2022,
        "opp_pitcher_name": "Yusei Kikuchi", **YUSEI_KIKUCHI_2022,
        "batter_stand": "R",
        "home_team": "MIL", "wind_speed_mph": 15, "wind_description": "Out To RF",
        "temp_f": 88, "roof_status": "outdoor",
        "batting_order_slot": 2, "odds": 600,
    })

    # --- Candidate 6: mostly missing data — must not crash, must default to neutral ---
    c6 = score_candidate({
        "player_name": "Unknown Prospect", "team": "XXX",
    })

    for label, c in [("Judge/weak-pitcher/good-park", c1), ("Straw/elite-pitcher/bad-park", c2),
                      ("Reynolds/avg-everything", c3), ("Judge/elite-pitcher", c4),
                      ("Straw/great-context", c5), ("missing-data", c6)]:
        print(f"{label}: final_score={c['final_score']} stars={c['star_rating']} tier={c['score_tier']} "
              f"pillars=(skill={c['pillars']['skill']['score']}, matchup={c['pillars']['matchup']['score']}, "
              f"env={c['pillars']['environment']['score']}, opp={c['pillars']['opportunity']['score']})")
    print()

    # --- Directional sanity checks ---
    results.append(check(
        "elite-everything (c1) scores highest of all six candidates",
        c1["final_score"] == max(c["final_score"] for c in [c1, c2, c3, c4, c5, c6]),
    ))
    results.append(check(
        "weak-everything (c2) scores lowest of all six candidates",
        c2["final_score"] == min(c["final_score"] for c in [c1, c2, c3, c4, c5, c6]),
    ))
    results.append(check(
        "middling (c3) lands strictly between elite (c1) and weak (c2)",
        c2["final_score"] < c3["final_score"] < c1["final_score"],
    ))
    results.append(check(
        "same elite hitter (Judge) scores higher vs. a weak pitcher (c1) than vs. an elite one (c4) — "
        "the matchup pillar has a real, directionally-correct effect on an otherwise-identical batter",
        c1["final_score"] > c4["final_score"],
    ))
    results.append(check(
        "an elite hitter facing a tough matchup (c4) still meaningfully outscores a weak hitter in the "
        "same tough matchup (implicit: skill still dominates as the largest-weighted pillar)",
        c4["final_score"] > c2["final_score"] + 20,
    ))
    results.append(check(
        "great context alone lifts a weak hitter (c5) above their own worst-case baseline (c2), but the "
        "lift is bounded — skill's 40% weight means context can't turn a weak hitter into a top scorer",
        c2["final_score"] < c5["final_score"] < c1["final_score"] - 15,
    ))
    # Monotonic = never "higher score but fewer stars than a lower-scoring candidate".
    # Ties in stars across different scores are expected (quintile-bucketed, not a
    # violation) — several hand-picked extreme candidates can legitimately land in
    # the same top bucket even with different exact scores.
    results.append(check("star ratings are monotonic with final_score across all six",
                          all(a["star_rating"] >= b["star_rating"]
                              for a in [c1, c2, c3, c4, c5, c6] for b in [c1, c2, c3, c4, c5, c6]
                              if a["final_score"] > b["final_score"])))
    results.append(check("elite candidate (c1) passes the +300 odds filter", c1["passes_odds_filter"] is True))
    results.append(check("weak candidate (c2) at +150 does NOT pass the +300 odds filter", c2["passes_odds_filter"] is False))
    results.append(check("elite candidate (c1) reaches at least 4 stars", c1["star_rating"] >= 4))
    results.append(check("weak candidate (c2) is at most 2 stars", c2["star_rating"] <= 2))

    # --- Missing-data candidate: must not crash, must be near-neutral, must explain itself ---
    results.append(check("missing-data candidate produced a result without crashing", c6["final_score"] is not None))
    results.append(check(
        "missing-data candidate lands close to neutral (45-55), not skewed by silently-zeroed inputs",
        45 <= c6["final_score"] <= 55,
    ))
    results.append(check(
        "missing-data candidate's notes explain what was defaulted (bullpen gap at minimum)",
        any("bullpen" in n.lower() for n in c6["notes"]),
    ))
    results.append(check(
        "missing-data candidate correctly fails the odds filter (no odds provided)",
        c6["passes_odds_filter"] is False,
    ))

    # --- Known-gap fields: accepted without crashing, but flagged as unused, never silently scored ---
    c_gaps = score_candidate({
        "player_name": "Gap Test", **BRYAN_REYNOLDS_2022,
        "expected_hr": 32, "opp_fb_pct_allowed": 40.0, "opp_avg_exit_velo_allowed": 89.0,
        "opp_xera": 3.50, "pitch_type_tendencies": {"fastball_pct": 55}, "projected_pa": 4.2,
        "odds": 400,
    })
    results.append(check("providing all documented-gap fields doesn't crash", c_gaps["final_score"] is not None))
    results.append(check(
        "all six documented-gap fields are flagged in notes as provided-but-unused",
        sum(any(field in n for n in c_gaps["notes"])
            for field in ["expected_hr", "opp_fb_pct_allowed", "opp_avg_exit_velo_allowed",
                           "opp_xera", "pitch_type_tendencies", "projected_pa"]) == 6,
    ))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
