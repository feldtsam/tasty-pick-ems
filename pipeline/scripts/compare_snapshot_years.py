"""
One-off diagnostic: runs the exact same 6 hand-made test candidates from
api/live_scoring/test_score_candidate.py against both the 2022-based and
2025-based reference snapshots, side by side, to see how much switching
the reference year actually moves real results — not just the calibrated
constants (already checked separately: platoon bonus roughly doubled).

Not part of the deployed pipeline or the permanent test suite — a one-time
comparison for this specific migration.

Run: python3 pipeline/scripts/compare_snapshot_years.py
"""
import importlib.util
import json
import sys
from pathlib import Path

LIVE_SCORING_DIR = Path(__file__).resolve().parent.parent / "api" / "live_scoring"
sys.path.insert(0, str(LIVE_SCORING_DIR))


def load_scorer(snapshot_filename: str):
    """Loads a fresh copy of score_candidate.py, then overrides its
    module-level snapshot to point at a specific file — lets both the
    2022 and 2025 versions run in the same process for a direct diff."""
    spec = importlib.util.spec_from_file_location(f"score_candidate_{snapshot_filename}", LIVE_SCORING_DIR / "score_candidate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with open(LIVE_SCORING_DIR / "reference_data" / snapshot_filename) as f:
        mod._SNAPSHOT = json.load(f)
    mod.CONFIG = mod._SNAPSHOT["config"]
    return mod


scorer_2022 = load_scorer("reference_snapshot_2022.json")
scorer_2025 = load_scorer("reference_snapshot_2025.json")

from test_score_candidate import (  # noqa: E402
    AARON_JUDGE_2022, AARON_NOLA_2022, BRYAN_REYNOLDS_2022, LEAGUE_AVERAGE_PITCHER,
    MYLES_STRAW_2022, YUSEI_KIKUCHI_2022,
)

CANDIDATES = {
    "Judge/weak-pitcher/good-park": {
        "player_name": "Aaron Judge", "team": "NYY", **AARON_JUDGE_2022,
        "opp_pitcher_name": "Yusei Kikuchi", **YUSEI_KIKUCHI_2022,
        "batter_stand": "R",
        "home_team": "CIN", "wind_speed_mph": 12, "wind_description": "Out To LF",
        "temp_f": 85, "roof_status": "outdoor",
        "batting_order_slot": 3, "odds": 450,
    },
    "Straw/elite-pitcher/bad-park": {
        "player_name": "Myles Straw", "team": "CLE", **MYLES_STRAW_2022,
        "opp_pitcher_name": "Aaron Nola", **AARON_NOLA_2022,
        "batter_stand": "R",
        "home_team": "DET", "wind_speed_mph": 12, "wind_description": "In From CF",
        "temp_f": 50, "roof_status": "outdoor",
        "batting_order_slot": 9, "odds": 150,
    },
    "Reynolds/avg-everything": {
        "player_name": "Bryan Reynolds", "team": "PIT", **BRYAN_REYNOLDS_2022,
        "opp_pitcher_name": "League-Average Pitcher", **LEAGUE_AVERAGE_PITCHER,
        "batter_stand": "L",
        "home_team": "BOS", "wind_speed_mph": 3, "wind_description": "Calm",
        "temp_f": 70, "roof_status": "outdoor",
        "batting_order_slot": 5, "odds": 350,
    },
    "Judge/elite-pitcher": {
        "player_name": "Aaron Judge", "team": "NYY", **AARON_JUDGE_2022,
        "opp_pitcher_name": "Aaron Nola", **AARON_NOLA_2022,
        "batter_stand": "R",
        "home_team": "BOS", "wind_speed_mph": 3, "wind_description": "Calm",
        "temp_f": 70, "roof_status": "outdoor",
        "batting_order_slot": 3, "odds": 240,
    },
    "Straw/great-context": {
        "player_name": "Myles Straw", "team": "CLE", **MYLES_STRAW_2022,
        "opp_pitcher_name": "Yusei Kikuchi", **YUSEI_KIKUCHI_2022,
        "batter_stand": "R",
        "home_team": "MIL", "wind_speed_mph": 15, "wind_description": "Out To RF",
        "temp_f": 88, "roof_status": "outdoor",
        "batting_order_slot": 2, "odds": 600,
    },
    "missing-data": {"player_name": "Unknown Prospect", "team": "XXX"},
}


if __name__ == "__main__":
    print(f"{'candidate':<32} {'2022 score':>10} {'2022 stars':>10}   ->   "
          f"{'2025 score':>10} {'2025 stars':>10}   {'delta':>7}")
    print("-" * 100)

    max_abs_delta = 0.0
    max_abs_delta_label = None
    star_changes = []

    for label, candidate in CANDIDATES.items():
        r22 = scorer_2022.score_candidate(dict(candidate))
        r25 = scorer_2025.score_candidate(dict(candidate))
        delta = r25["final_score"] - r22["final_score"]
        print(f"{label:<32} {r22['final_score']:>10.1f} {r22['star_rating']:>10}   ->   "
              f"{r25['final_score']:>10.1f} {r25['star_rating']:>10}   {delta:>+7.1f}")
        if abs(delta) > abs(max_abs_delta):
            max_abs_delta = delta
            max_abs_delta_label = label
        if r22["star_rating"] != r25["star_rating"]:
            star_changes.append((label, r22["star_rating"], r25["star_rating"]))

    print()
    print(f"Largest single-candidate final_score shift: {max_abs_delta:+.1f} ({max_abs_delta_label})")
    if star_changes:
        print(f"Star rating changed for {len(star_changes)}/{len(CANDIDATES)} candidates:")
        for label, s22, s25 in star_changes:
            print(f"  {label}: {s22} -> {s25} stars")
    else:
        print("No candidate's star rating changed.")

    # Directional sanity, same checks as the main test suite, re-verified
    # against the new 2025 snapshot specifically.
    r = {label: scorer_2025.score_candidate(dict(c)) for label, c in CANDIDATES.items()}
    elite = r["Judge/weak-pitcher/good-park"]["final_score"]
    weak = r["Straw/elite-pitcher/bad-park"]["final_score"]
    mid = r["Reynolds/avg-everything"]["final_score"]
    all_scores = [v["final_score"] for v in r.values()]
    print()
    print(f"[{'PASS' if elite == max(all_scores) else 'FAIL'}] elite candidate still scores highest under 2025")
    print(f"[{'PASS' if weak == min(all_scores) else 'FAIL'}] weak candidate still scores lowest under 2025")
    print(f"[{'PASS' if weak < mid < elite else 'FAIL'}] middling candidate still lands strictly between under 2025")
