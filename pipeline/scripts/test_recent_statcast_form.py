"""
Tests recent_statcast_form.py's pure aggregation logic against synthetic
Statcast-shaped rows — no network, no real pybaseball pull. Same "hand-
built input dicts, assert on output" style as live_data's
_batter_recent_form_from_splits tests.

Run: python pipeline/scripts/test_recent_statcast_form.py
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from recent_statcast_form import (  # noqa: E402
    MIN_BATTED_BALL_EVENTS,
    apply_sample_size_gate,
    build_write_rows,
    compute_batted_ball_profile,
)


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


def _row(batter, bb_type=None, launch_speed_angle=None, launch_angle=None):
    """One synthetic Statcast pitch row — only the columns
    compute_batted_ball_profile() actually reads. A non-batted-ball pitch
    (a called strike, a ball) has bb_type=None, matching real Statcast."""
    return {"batter": batter, "bb_type": bb_type, "launch_speed_angle": launch_speed_angle, "launch_angle": launch_angle}


if __name__ == "__main__":
    results = []

    # --- Synthetic batter 111: 4 real batted-ball events, 2 fly balls,
    # 1 barrel (launch_speed_angle == 6), known launch angles. Plus 2
    # non-batted-ball pitches (should never be counted at all). ---
    pitches = pd.DataFrame([
        _row(111, bb_type="fly_ball", launch_speed_angle=6, launch_angle=28.0),   # barrel + FB
        _row(111, bb_type="fly_ball", launch_speed_angle=4, launch_angle=32.0),   # FB, not a barrel
        _row(111, bb_type="ground_ball", launch_speed_angle=1, launch_angle=-4.0),
        _row(111, bb_type="line_drive", launch_speed_angle=3, launch_angle=12.0),
        _row(111, bb_type=None, launch_speed_angle=None, launch_angle=None),  # called strike, not a BBE
        _row(111, bb_type=None, launch_speed_angle=None, launch_angle=None),  # ball, not a BBE
        # --- Synthetic batter 222: only 2 real batted-ball events — below
        # the real sample-size gate, should still be counted honestly. ---
        _row(222, bb_type="fly_ball", launch_speed_angle=6, launch_angle=30.0),
        _row(222, bb_type="popup", launch_speed_angle=2, launch_angle=55.0),
    ])

    profile = compute_batted_ball_profile(pitches)
    b111 = profile[profile["mlbam_id"] == 111].iloc[0]
    b222 = profile[profile["mlbam_id"] == 222].iloc[0]

    results.append(check(
        "batter 111: recent_batted_ball_events counts only real BBE rows (4), not the 2 non-BBE pitches",
        b111["recent_batted_ball_events"] == 4,
    ))
    results.append(check(
        "batter 111: recent_fb_pct is exactly 2 of 4 real BBE = 50.0%",
        b111["recent_fb_pct"] == 50.0,
    ))
    results.append(check(
        "batter 111: recent_barrel_pct is exactly 1 of 4 real BBE (launch_speed_angle==6) = 25.0%",
        b111["recent_barrel_pct"] == 25.0,
    ))
    results.append(check(
        "batter 111: recent_avg_launch_angle is the real mean of all 4 (28+32-4+12)/4 = 17.0",
        abs(b111["recent_avg_launch_angle"] - 17.0) < 0.05,
    ))
    results.append(check(
        "batter 222: real small sample (2 BBE) is still counted honestly before gating",
        b222["recent_batted_ball_events"] == 2,
    ))

    # --- Sample-size gate: batter 111 (4 real BBE) is below
    # MIN_BATTED_BALL_EVENTS=15 too in this synthetic example — use an
    # explicit low threshold to test the gate's real boundary behavior
    # directly rather than needing 15+ synthetic rows for a "passes"
    # case. ---
    gated_strict = apply_sample_size_gate(profile, min_batted_ball_events=4)
    g111 = gated_strict[gated_strict["mlbam_id"] == 111].iloc[0]
    g222 = gated_strict[gated_strict["mlbam_id"] == 222].iloc[0]
    results.append(check(
        "gate boundary: exactly at the threshold (4 real BBE, min=4) is NOT gated — real metrics preserved",
        pd.notna(g111["recent_barrel_pct"]) and g111["recent_barrel_pct"] == 25.0,
    ))
    results.append(check(
        "gate: below the threshold (2 real BBE < min=4) sets the three rate/average metrics to null",
        pd.isna(g222["recent_barrel_pct"]) and pd.isna(g222["recent_fb_pct"]) and pd.isna(g222["recent_avg_launch_angle"]),
    ))
    results.append(check(
        "gate: recent_batted_ball_events itself is NEVER hidden, even for a real small/gated sample",
        g222["recent_batted_ball_events"] == 2,
    ))

    # --- Real default threshold sanity: MIN_BATTED_BALL_EVENTS is the
    # documented 15, not silently changed. ---
    results.append(check(
        "MIN_BATTED_BALL_EVENTS default matches the documented value (15)",
        MIN_BATTED_BALL_EVENTS == 15,
    ))

    # --- build_write_rows: real NaN becomes real JSON null, not a
    # fabricated 0 or a missing key. ---
    rows = build_write_rows(gated_strict, "2026-07-22", "2026-08-11")
    row222 = next(r for r in rows if r["mlbam_id"] == 222)
    results.append(check(
        "build_write_rows: a gated-out metric becomes real Python None (JSON null), not 0 or a missing key",
        row222["recent_barrel_pct"] is None
        and "recent_barrel_pct" in row222
        and row222["recent_batted_ball_events"] == 2,
    ))
    results.append(check(
        "build_write_rows: window_start/window_end are the real passed-in dates, not recomputed",
        row222["window_start"] == "2026-07-22" and row222["window_end"] == "2026-08-11",
    ))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
