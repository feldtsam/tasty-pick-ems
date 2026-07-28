"""
Generates a self-contained JSON snapshot of everything the live single-
candidate scorer (api/live_scoring/score_candidate.py) needs, so that
scorer can run with zero dependency on backtest/ at runtime (no pandas, no
access to backtest/'s multi-hundred-MB raw data — just this one small
JSON file bundled into the pipeline/ deployment).

This script is READ-ONLY against backtest/ — it imports the actual
validated scoring/normalize.py, scoring/calibrate.py, and scoring/config.py
to guarantee the live scorer uses the *exact* same reference-population
qualification thresholds and calibrated constants as the validated
backtest, rather than a hand-reimplemented approximation that could
quietly drift from it. Nothing in backtest/ is modified or written to —
including its scoring/dataset.py, which is why the games+context merge
below is done locally rather than via backtest's build_calibration_games()
(that function's SEASON_MONTH_LABEL lookup only knows about 2022/2023;
extending it would mean editing a backtest/ file for a job this script can
do itself in three lines, using data backtest/ already has on disk).

Two things stay pinned to the *validated 2023 backtest distribution*
regardless of which season powers the reference population below:
star-rating boundaries and temperature min/max scaling. Both come from
running the actual scoring model at scale and observing its real output —
recalibrating them for a new reference year would mean re-running the full
out-of-sample validation loop with that year as the baseline, which is a
separate, larger task than "regenerate the reference population."

Run with backtest/'s own venv (needs pandas + backtest/scoring):
    cd backtest && source .venv/bin/activate
    python3 ../pipeline/scripts/build_reference_snapshot.py 2025 2025-03-27_2025-09-28
"""
import json
import sys
from pathlib import Path

BACKTEST_ROOT = Path(__file__).resolve().parent.parent.parent / "backtest"
PIPELINE_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(BACKTEST_ROOT))

import pandas as pd  # noqa: E402  (after sys.path insert)

from scoring import calibrate, pillars  # noqa: E402
from scoring.config import CONFIG  # noqa: E402
from scoring.dataset import load_season_baselines  # noqa: E402

PROC_DIR = BACKTEST_ROOT / "data" / "processed"
RAW_DIR = BACKTEST_ROOT / "data" / "raw"

# Fixed anchor for star-rating boundaries and temp scaling — see module
# docstring. Not the SEASON parameter; deliberately independent of it.
VALIDATED_BACKTEST_SCORED_FILE = PROC_DIR / "scored_2023-03-30_2023-10-01.parquet"

BATTER_SCALE_COLS = ["avg_exit_velo", "sweet_spot_pct", "hard_hit_pct", "barrel_pct", "xslg", "xwoba", "hr_per_pa"]
BATTED_BALL_SCALE_COLS = ["pull_pct", "fb_pct"]
PITCHER_SCALE_COLS = ["hard_hit_pct_allowed", "barrel_pct_allowed", "xslg_allowed", "xwoba_allowed", "hr_per_9", "k_per_9"]
BULLPEN_SCALE_COLS = ["bullpen_hr_per_pa", "bullpen_k_pct", "bullpen_hard_hit_pct"]


def build_local_calibration_games(month_label: str) -> pd.DataFrame:
    """Local equivalent of backtest/scoring/dataset.py's
    build_calibration_games(), without touching that file. Same merge,
    same two columns pulled from context (batting_order_slot, pa_count is
    already in games) — just parameterized by an explicit month_label
    instead of a season->month_label lookup dict that doesn't know about
    every season."""
    games = pd.read_parquet(PROC_DIR / f"batter_games_{month_label}.parquet")
    context = pd.read_parquet(PROC_DIR / f"game_context_{month_label}.parquet")
    return games.merge(
        context[["game_pk", "batter", "batting_order_slot"]],
        on=["game_pk", "batter"], how="left",
    )


def build_snapshot(season: int, month_label: str) -> dict:
    baselines = load_season_baselines(season)
    scales = pillars.build_reference_scales(
        baselines["skill"], baselines["matchup"], baselines["bullpen"], baselines["park"],
        baselines["batted_ball"], CONFIG,
    )
    batter_scales = {c: scales[c].tolist() for c in BATTER_SCALE_COLS}
    batted_ball_scales = {c: scales[c].tolist() for c in BATTED_BALL_SCALE_COLS}
    pitcher_scales = {c: scales[c].tolist() for c in PITCHER_SCALE_COLS}
    bullpen_scales = {c: scales[c].tolist() for c in BULLPEN_SCALE_COLS}

    calibration_games = build_local_calibration_games(month_label)
    raw_pitches = pd.read_parquet(RAW_DIR / f"statcast_full_{season}.parquet")
    platoon = calibrate.compute_platoon_bonus(raw_pitches, sensitivity=CONFIG["matchup"]["platoon_sensitivity"])
    order_curve, avg_pa_by_slot = calibrate.compute_batting_order_curve(calibration_games)

    park_factor_by_team = dict(zip(baselines["park"]["venue_team"], baselines["park"]["park_factor_hr"]))

    # Per-player raw stat lookups (not just the sorted scale) — used by the
    # live-data endpoint's small-sample fallback: when a player's *current*
    # season sample is below the qualification minimum, fall back to their
    # own prior-full-season number here rather than a noisy small sample or
    # a blank neutral. See pipeline/api/live_data/ for where this is used.
    skill_by_id = baselines["skill"].merge(baselines["batted_ball"], on="mlbam_id", how="left")
    batter_lookup = {}
    for _, row in skill_by_id.iterrows():
        mid = row.get("mlbam_id")
        if pd.isna(mid):
            continue
        batter_lookup[str(int(mid))] = {
            c: (None if pd.isna(row.get(c)) else float(row.get(c)))
            for c in BATTER_SCALE_COLS + BATTED_BALL_SCALE_COLS
        }

    pitcher_lookup = {}
    for _, row in baselines["matchup"].iterrows():
        mid = row.get("mlbam_id")
        if pd.isna(mid):
            continue
        pitcher_lookup[str(int(mid))] = {
            c: (None if pd.isna(row.get(c)) else float(row.get(c)))
            for c in PITCHER_SCALE_COLS
        }

    # Star-rating quintile boundaries and temp scaling: fixed to the
    # validated 2023 backtest distribution, not `season` — see module
    # docstring for why.
    scored_2023 = pd.read_parquet(VALIDATED_BACKTEST_SCORED_FILE)
    star_boundaries = scored_2023["final_score"].quantile([0.2, 0.4, 0.6, 0.8]).tolist()
    temp_min = float(scored_2023["temp_f"].astype(float).min())
    temp_max = float(scored_2023["temp_f"].astype(float).max())

    return {
        "season": season,
        "generated_from": f"backtest/ validated {season} baselines (games/context: {month_label}) "
                           f"+ star-rating/temp scaling fixed to the validated 2023 full-season backtest results",
        "config": CONFIG,
        "batter_scales": batter_scales,
        "batted_ball_scales": batted_ball_scales,
        "pitcher_scales": pitcher_scales,
        "bullpen_scales": bullpen_scales,
        "park_factor_min": float(scales["park_factor_min"]),
        "park_factor_max": float(scales["park_factor_max"]),
        "park_factor_by_team": park_factor_by_team,
        "temp_min_f": temp_min,
        "temp_max_f": temp_max,
        "platoon": {"bonus_opposite": platoon["bonus_opposite"], "bonus_same": platoon["bonus_same"]},
        "batting_order_curve": {str(int(k)): v for k, v in order_curve.items()},
        "avg_pa_by_slot": {str(int(k)): v for k, v in avg_pa_by_slot.items()},
        "star_rating_boundaries": star_boundaries,
        "batter_lookup_by_id": batter_lookup,
        "pitcher_lookup_by_id": pitcher_lookup,
    }


if __name__ == "__main__":
    season = int(sys.argv[1]) if len(sys.argv) > 1 else 2022
    month_label = sys.argv[2] if len(sys.argv) > 2 else "2022-04-07_2022-10-05"

    out_path = PIPELINE_ROOT / "api" / "live_scoring" / "reference_data" / f"reference_snapshot_{season}.json"

    snapshot = build_snapshot(season, month_label)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(snapshot, f, indent=2)

    print(f"Saved reference snapshot -> {out_path}")
    for name, cols in [("batter", BATTER_SCALE_COLS), ("batted_ball", BATTED_BALL_SCALE_COLS),
                       ("pitcher", PITCHER_SCALE_COLS), ("bullpen", BULLPEN_SCALE_COLS)]:
        key = {"batter": "batter_scales", "batted_ball": "batted_ball_scales",
               "pitcher": "pitcher_scales", "bullpen": "bullpen_scales"}[name]
        sizes = {c: len(snapshot[key][c]) for c in cols}
        print(f"{name}_scales sizes: {sizes}")
    print(f"park_factor range: {snapshot['park_factor_min']:.1f} - {snapshot['park_factor_max']:.1f}")
    print(f"temp range (fixed, 2023-backtest-anchored): {snapshot['temp_min_f']:.1f} - {snapshot['temp_max_f']:.1f}")
    print(f"platoon: {snapshot['platoon']}")
    print(f"batting order curve: {snapshot['batting_order_curve']}")
    print(f"star rating boundaries (fixed, 2023-backtest-anchored): {snapshot['star_rating_boundaries']}")
    print(f"per-player lookups: {len(snapshot['batter_lookup_by_id'])} batters, "
          f"{len(snapshot['pitcher_lookup_by_id'])} pitchers")
