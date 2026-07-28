"""
Compute Pull% and FB% per batter directly from real Statcast batted-ball
data — closes the last documented gap in the Player Skill pillar. These two
metrics were unavailable via pybaseball's FanGraphs functions (blocked with
HTTP 403; see README), so this derives them from the cached raw pitch data
used elsewhere in the project (same approach as park factors / bullpen
quality).

FB% = fraction of batted balls Statcast itself classifies as "fly_ball"
(bb_type) — no formula or judgment call, straight from the data.

Pull% needs a spray angle, since Statcast's raw pitch data doesn't publish
a pre-classified pull/straight/oppo field. Uses the standard hc_x/hc_y ->
spray angle formula built on Baseball Savant's coordinate system (home
plate at approx (125.42, 198.27); 0 deg = straight to CF, positive = 1B/RF
side, negative = 3B/LF side), with a +/-15 degree "straightaway" zone — a
commonly used public approximation of FanGraphs' undisclosed exact zone
boundaries, not an exact replica. Pull side depends on batter handedness:
RHB pull to LF (negative angle), LHB pull to RF (positive angle).

That handedness-dependent sign is exactly the kind of thing that was wrong
in an earlier pass at pitcher FB% (see README) — so this script validates
itself before saving anything: home runs are near-universally pulled
(bat-speed/timing physics), regardless of exact rate, for both batter
handedness. That's true independent of any specific player's stats, so if
the pulled-HR share comes back close to 50/50 or inverted for either
handedness, the sign convention is wrong.

Usage: python scripts/fetch_batted_ball_profile.py 2022
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
PROC_DIR = ROOT / "data" / "processed"
PROC_DIR.mkdir(parents=True, exist_ok=True)

STRAIGHTAWAY_HALF_WIDTH_DEG = 15.0
HOME_PLATE_X, HOME_PLATE_Y = 125.42, 198.27


def compute_spray_angle(hc_x: pd.Series, hc_y: pd.Series) -> pd.Series:
    return np.degrees(np.arctan2(hc_x - HOME_PLATE_X, HOME_PLATE_Y - hc_y))


def classify_pull(spray_angle: pd.Series, stand: pd.Series) -> pd.Series:
    is_rhb = stand == "R"
    return np.where(
        is_rhb,
        spray_angle < -STRAIGHTAWAY_HALF_WIDTH_DEG,
        spray_angle > STRAIGHTAWAY_HALF_WIDTH_DEG,
    )


def compute_batted_ball_profile(pitches: pd.DataFrame) -> pd.DataFrame:
    bbe = pitches.dropna(subset=["bb_type", "hc_x", "hc_y", "stand"]).copy()
    bbe["spray_angle"] = compute_spray_angle(bbe["hc_x"], bbe["hc_y"])
    bbe["is_pull"] = classify_pull(bbe["spray_angle"], bbe["stand"])
    bbe["is_fb"] = bbe["bb_type"] == "fly_ball"

    profile = (
        bbe.groupby("batter")
        .agg(bbe_count=("is_fb", "size"), pull_count=("is_pull", "sum"), fb_count=("is_fb", "sum"))
        .reset_index()
    )
    profile["pull_pct"] = (profile["pull_count"] / profile["bbe_count"] * 100).round(1)
    profile["fb_pct"] = (profile["fb_count"] / profile["bbe_count"] * 100).round(1)
    return profile.rename(columns={"batter": "mlbam_id"})


def validate_pull_direction(pitches: pd.DataFrame) -> bool:
    hr = pitches[pitches["events"] == "home_run"].dropna(subset=["hc_x", "hc_y", "stand"]).copy()
    hr["spray_angle"] = compute_spray_angle(hr["hc_x"], hr["hc_y"])
    hr["is_pull"] = classify_pull(hr["spray_angle"], hr["stand"])

    ok = True
    for hand in ("R", "L"):
        subset = hr[hr["stand"] == hand]
        pulled_share = subset["is_pull"].mean()
        print(f"  {hand}HB home runs pulled: {pulled_share:.1%} (n={len(subset)})")
        if pulled_share < 0.60:  # HRs are overwhelmingly pulled; this is a low bar, not a precise target
            ok = False
    return ok


if __name__ == "__main__":
    season = int(sys.argv[1]) if len(sys.argv) > 1 else 2022
    raw_path = RAW_DIR / f"statcast_full_{season}.parquet"
    if not raw_path.exists():
        print(f"No cached full-season pull at {raw_path}. Run fetch_park_factors.py first.")
        sys.exit(1)

    print(f"Loading cached raw pitch data from {raw_path}...")
    raw = pd.read_parquet(raw_path)

    print("Sanity check: HRs should be overwhelmingly pulled for both handedness groups...")
    if not validate_pull_direction(raw):
        print("FAILED sanity check — pull-side sign convention looks wrong. Not saving output.")
        sys.exit(1)
    print("Passed.")

    profile = compute_batted_ball_profile(raw)
    out_path = PROC_DIR / f"batted_ball_profile_{season}.parquet"
    profile.to_parquet(out_path, index=False)
    print(f"\nSaved {len(profile)} batters -> {out_path}")
    print(profile[["pull_pct", "fb_pct", "bbe_count"]].describe().round(1).to_string())
