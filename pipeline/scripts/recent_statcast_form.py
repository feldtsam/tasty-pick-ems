"""
Daily batch job: real recent-window Statcast metrics (Barrel %, Fly-Ball %,
avg Launch Angle) per batter, from a bulk pybaseball.statcast() pull over a
trailing calendar-day window — closes the last documented gap in the
hitter stat card (StoryDetail.tsx's buildHitterStats) after the earlier
HR/Hits/XBH/OPS/ERA fix. These three specifically ARE achievable from a
bulk pull; the pitcher-side Statcast metrics (Hard-Hit % Allowed, Exit
Velo, xERA) are still out of scope for this task.

WHY A SEPARATE SCHEDULED SCRIPT, NOT A LIVE PER-REQUEST CALL: a genuine
recent-WINDOW Statcast pull means raw pitch-by-pitch data — the same
multi-second-per-player-class operation backtest/ already uses for
full-season batch analysis, too slow for Vercel's serverless timeout
multiplied across every candidate on a slate (the same constraint already
documented in live_data/savant_stats.py's docstring for why THAT module
only uses season-long Savant leaderboards, not a rolling window). A BULK
pull — pybaseball.statcast(start_dt, end_dt), one real call returning
every pitch across the whole league for the window — sidesteps that
entirely: this script aggregates per batter locally afterward. Run once a
day by GitHub Actions (see .github/workflows/recent_statcast_form.yml),
not Vercel — no serverless timeout to fit inside at all.

MODELED CLOSELY ON backtest/scripts/fetch_batted_ball_profile.py: same
real Fly-Ball % definition (bb_type == "fly_ball", straight from
Statcast's own classification, no formula or judgment call) and the same
real "batted-ball event" universe (rows with a real bb_type present).
Barrel % and avg Launch Angle aren't in that script but come from the
exact same raw pull — launch_speed_angle == 6 is Baseball Savant's own
documented Barrel classification bucket (not a derived formula), and
launch_angle is a raw column on every batted-ball row.

WINDOW: 21 calendar days, ending yesterday (today's games generally
haven't all gone final league-wide when this runs — see the workflow's
early-morning schedule, before the pipeline's first ~12pm ET run). Chosen
to land close to live_data/recent_form.py's existing hitter window
(HITTER_RECENT_GAMES=15) in real practical terms: not every calendar day
has a given batter's team playing, so 21 CALENDAR days of games works out
to roughly 15-18 real games played for a semi-regular starter — close to
that 15-game window without a much longer or shorter real-world span.
Documented here, not silently chosen, per the task's own instruction.

SAMPLE-SIZE GATE: MIN_BATTED_BALL_EVENTS=15 real batted-ball events over
the window. Below this, the three rate/average metrics are published as
None — a small real sample is genuinely noisy (2-for-3 barrels isn't a
real 67% barrel rate) — but recent_batted_ball_events itself is always
recorded at its real (small) value, never hidden. Mirrors
shelf_curation.py's MIN_HITTER_RECENT_SAMPLE gate for the MLB-Stats-API-
derived recent form — same "flag a real small sample rather than publish
noise as signal" discipline, just gating on batted-ball events instead of
games played, since that's the real unit this data comes in.

Usage: python pipeline/scripts/recent_statcast_form.py
Requires PIPELINE_WEBHOOK_SECRET in the environment — see the accompanying
GitHub Actions workflow, which sources it from a repo secret.
"""
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests
from pybaseball import statcast

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))

from lovable_forward import compute_signature, serialize_payload  # noqa: E402

WINDOW_DAYS = 21
MIN_BATTED_BALL_EVENTS = 15
BARREL_LAUNCH_SPEED_ANGLE_BUCKET = 6  # Baseball Savant's own "Barrel" classification value

DEFAULT_WRITE_URL = "https://tastypickems.lovable.app/api/public/recent-statcast-form-write"
REQUEST_TIMEOUT_SECONDS = 30


def compute_batted_ball_profile(pitches: pd.DataFrame) -> pd.DataFrame:
    """
    Pure aggregation, no network — real batted-ball-event rows only
    (bb_type present), grouped by batter (MLBAM id, Statcast's own real
    column name for it — same convention fetch_batted_ball_profile.py
    already uses). Returns one row per real batter with mlbam_id,
    recent_barrel_pct, recent_fb_pct, recent_avg_launch_angle,
    recent_batted_ball_events — BEFORE the sample-size gate (see
    apply_sample_size_gate) is applied, so every real batter with at
    least one batted-ball event shows up here regardless of sample size.

    recent_avg_launch_angle uses pandas' default skipna=True mean — a
    batted-ball row with a real bb_type but a missing launch_angle
    (imperfect tracking) is excluded from that specific average rather
    than treated as a real 0-degree launch angle, which would be a
    fabricated data point, not a real one.
    """
    bbe = pitches.dropna(subset=["bb_type"]).copy()
    bbe["is_fb"] = bbe["bb_type"] == "fly_ball"
    bbe["is_barrel"] = bbe["launch_speed_angle"] == BARREL_LAUNCH_SPEED_ANGLE_BUCKET

    profile = (
        bbe.groupby("batter")
        .agg(
            recent_batted_ball_events=("is_fb", "size"),
            fb_count=("is_fb", "sum"),
            barrel_count=("is_barrel", "sum"),
            recent_avg_launch_angle=("launch_angle", "mean"),
        )
        .reset_index()
    )
    profile["recent_fb_pct"] = (profile["fb_count"] / profile["recent_batted_ball_events"] * 100).round(1)
    profile["recent_barrel_pct"] = (profile["barrel_count"] / profile["recent_batted_ball_events"] * 100).round(1)
    profile["recent_avg_launch_angle"] = profile["recent_avg_launch_angle"].round(1)

    return profile.rename(columns={"batter": "mlbam_id"})[
        ["mlbam_id", "recent_barrel_pct", "recent_fb_pct", "recent_avg_launch_angle", "recent_batted_ball_events"]
    ]


def apply_sample_size_gate(profile: pd.DataFrame, min_batted_ball_events: int = MIN_BATTED_BALL_EVENTS) -> pd.DataFrame:
    """
    Below min_batted_ball_events, the three rate/average metrics are set
    to None — a real small sample is noise, not a trustworthy real rate —
    but recent_batted_ball_events itself is NEVER hidden, even when tiny;
    still recorded at its real value so a real small-sample player isn't
    silently indistinguishable from one with no data at all. Returns a
    fresh copy, not a mutation in place, so a caller holding the ungated
    profile for its own purposes isn't surprised by it changing underneath.
    """
    gated = profile.copy()
    too_small = gated["recent_batted_ball_events"] < min_batted_ball_events
    gated.loc[too_small, ["recent_barrel_pct", "recent_fb_pct", "recent_avg_launch_angle"]] = None
    return gated


def fetch_recent_pitches(window_days: int = WINDOW_DAYS) -> tuple:
    """
    Real bulk pull, ONE call for the whole trailing window — not one call
    per player, which is exactly the per-player cost this whole design
    exists to avoid (see module docstring). end_dt is yesterday: this
    typically runs early morning ET, before today's games are even
    scheduled to start, let alone final — see the accompanying workflow's
    cron schedule. Returns (pitches, window_start_iso, window_end_iso).
    """
    window_end = date.today() - timedelta(days=1)
    window_start = window_end - timedelta(days=window_days - 1)
    pitches = statcast(start_dt=window_start.isoformat(), end_dt=window_end.isoformat())
    return pitches, window_start.isoformat(), window_end.isoformat()


def build_write_rows(profile: pd.DataFrame, window_start: str, window_end: str) -> list:
    """Shapes a gated profile into recent-statcast-form-write's real
    expected row shape — real NaN (pandas' representation of a gated-out
    or genuinely missing value) becomes real JSON null, never a fabricated
    0 or omitted key."""
    rows = []
    for _, r in profile.iterrows():
        rows.append({
            "mlbam_id": int(r["mlbam_id"]),
            "recent_barrel_pct": None if pd.isna(r["recent_barrel_pct"]) else float(r["recent_barrel_pct"]),
            "recent_fb_pct": None if pd.isna(r["recent_fb_pct"]) else float(r["recent_fb_pct"]),
            "recent_avg_launch_angle": None if pd.isna(r["recent_avg_launch_angle"]) else float(r["recent_avg_launch_angle"]),
            "recent_batted_ball_events": int(r["recent_batted_ball_events"]),
            "window_start": window_start,
            "window_end": window_end,
        })
    return rows


def forward_to_write_endpoint(rows: list, secret: str, write_url: str) -> dict:
    """Same real signed-POST pattern as curate_shelves.py's
    fetch_todays_scored_picks — compute_signature()/serialize_payload()
    from lovable_forward.py, reused unmodified, not reimplemented."""
    payload_str = serialize_payload(rows)
    signature = compute_signature(secret, payload_str)
    response = requests.post(
        write_url,
        data=payload_str.encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Signature": signature},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


if __name__ == "__main__":
    secret = os.environ.get("PIPELINE_WEBHOOK_SECRET")
    if not secret:
        print("PIPELINE_WEBHOOK_SECRET is not set in the environment — cannot sign the write request. Aborting.")
        sys.exit(1)
    write_url = os.environ.get("RECENT_STATCAST_FORM_WRITE_URL", DEFAULT_WRITE_URL)

    print(f"Pulling real Statcast pitch data for the trailing {WINDOW_DAYS}-day window...")
    pitches, window_start, window_end = fetch_recent_pitches()
    print(f"Real window: {window_start} to {window_end} — {len(pitches)} real pitches pulled.")

    if len(pitches) == 0:
        print("Zero real pitches returned for this window (real off-day span, or Savant is unreachable) — nothing to write. Exiting cleanly, not an error.")
        sys.exit(0)

    profile = compute_batted_ball_profile(pitches)
    print(f"Real batters with at least one real batted-ball event: {len(profile)}")

    gated = apply_sample_size_gate(profile)
    below_gate = int(gated["recent_barrel_pct"].isna().sum())
    print(f"Real batters below the {MIN_BATTED_BALL_EVENTS}-event sample-size gate (metrics published as null, event count still real): {below_gate}")

    rows = build_write_rows(gated, window_start, window_end)
    print(f"Forwarding {len(rows)} real rows to {write_url}...")
    result = forward_to_write_endpoint(rows, secret, write_url)
    print(f"Real response: {result}")
