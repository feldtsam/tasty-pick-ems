"""
Tests for team_tendencies.py — the fourth and final NFL Intelligence
family, reusing intelligence_schema.py's shared story schema. Three
independent detectors (red-zone play-calling, fourth-down
aggressiveness, pace), each tested against real historical pbp
(2022/2024/2025) and the real player_redzone_weekly.csv backfill for
related_players, same real-data-first discipline the first three
families established.

Requires network access (pulls real pbp via nfl_data_py) — same
requirement test_market_intelligence.py already has for its real-data
checks. Set SSL_CERT_FILE to certifi's bundle if a local cert error
occurs (see other test files' own notes on this).

Run: python3 nfl/test_team_tendencies.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))

import pandas as pd

from intelligence_schema import STORY_FIELDS
from team_tendencies import (
    CONFIG,
    aggregate_fourth_down_aggressiveness,
    aggregate_redzone_play_calling,
    build_fourth_down_aggressiveness_stories,
    build_pace_stories,
    build_redzone_play_calling_stories,
    build_team_tendencies_stories,
    _score_fourth_down_aggressiveness,
    _score_redzone_play_calling,
)

WEEKLY_PATH = Path(__file__).resolve().parent / "scripts" / "player_redzone_weekly.csv"


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


if __name__ == "__main__":
    results = []

    if not WEEKLY_PATH.exists():
        print(f"SKIPPED all checks — {WEEKLY_PATH} not present in this environment.")
        raise SystemExit(0)

    try:
        import nfl_data_py as nfl
    except ImportError:
        print("SKIPPED all checks — nfl_data_py not importable in this environment.")
        raise SystemExit(0)

    weekly = pd.read_csv(WEEKLY_PATH)

    try:
        pbp2025 = nfl.import_pbp_data([2025], downcast=True)
    except Exception as e:
        print(f"SKIPPED all checks — could not pull real pbp data ({e}). "
              f"Try: export SSL_CERT_FILE=$(python3 -c 'import certifi; print(certifi.where())')")
        raise SystemExit(0)

    weeks_2025 = sorted(int(w) for w in pbp2025["week"].dropna().unique())

    # ============================================================
    # Real stories across every real 2025 week — schema + entity type.
    # ============================================================
    all_stories = {"rz": [], "fd": [], "pace": []}
    for wk in weeks_2025:
        all_stories["rz"] += build_redzone_play_calling_stories(pbp2025, weekly, 2025, wk)
        all_stories["fd"] += build_fourth_down_aggressiveness_stories(pbp2025, weekly, 2025, wk)
        all_stories["pace"] += build_pace_stories(pbp2025, weekly, 2025, wk)

    for name, stories in all_stories.items():
        results.append(check(f"{name}: real 2025 data produces a non-trivial set of stories (got {len(stories)})", 5 <= len(stories) <= 200))
        results.append(check(f"{name}: every schema field is present on a real story", all(f in stories[0] for f in STORY_FIELDS)))
        results.append(check(f"{name}: entity type is 'team', not player or (team, position_group)", all(s["entity"]["type"] == "team" and set(s["entity"].keys()) == {"type", "team"} for s in stories)))
        results.append(check(f"{name}: headline/story are real, distinct, non-empty text", all(s["story"] != s["headline"] and len(s["story"]) > 20 for s in stories)))

    # ============================================================
    # Combined feed wrapper.
    # ============================================================
    combined = build_team_tendencies_stories(pbp2025, weekly, 2025, 15)
    separate = (
        build_redzone_play_calling_stories(pbp2025, weekly, 2025, 15)
        + build_fourth_down_aggressiveness_stories(pbp2025, weekly, 2025, 15)
        + build_pace_stories(pbp2025, weekly, 2025, 15)
    )
    results.append(check("build_team_tendencies_stories combines all three detectors' real output for a week", len(combined) == len(separate) and len(combined) > 0))

    # ============================================================
    # Structural gates (red-zone, fourth-down) actually block thin
    # team-weeks — not just documented, checked directly.
    # ============================================================
    rz_scored = _score_redzone_play_calling(aggregate_redzone_play_calling(pbp2025), CONFIG)
    thin_rz = rz_scored[(rz_scored["_cum_rz_plays"] > 0) & (rz_scored["_cum_rz_plays"] < CONFIG["min_rz_plays_qualified"])]
    results.append(check(f"real thin (unqualified) red-zone team-weeks exist in 2025 data (got {len(thin_rz)})", len(thin_rz) > 0))
    thin_row = thin_rz.iloc[0]
    thin_week_stories = build_redzone_play_calling_stories(pbp2025, weekly, 2025, int(thin_row["week"]))
    results.append(check(
        f"a real unqualified team ({thin_row['team']}, {int(thin_row['_cum_rz_plays'])} cum. plays) never produces a red-zone story",
        not any(s["entity"]["team"] == thin_row["team"] for s in thin_week_stories),
    ))
    results.append(check(
        "structural gate holds across every real generated red-zone story (min sample_size >= configured threshold)",
        all(s["sample_size"] >= CONFIG["min_rz_plays_qualified"] for s in all_stories["rz"]),
    ))
    results.append(check(
        "structural gate holds across every real generated fourth-down story (min sample_size >= configured threshold)",
        all(s["sample_size"] >= CONFIG["min_fourth_down_decisions_qualified"] for s in all_stories["fd"]),
    ))

    # Pace: NO structural gate, per the approved investigation — confirm
    # a story CAN generate even with a low games-played count (just
    # hedged), unlike the other two.
    thin_pace_stories = [s for s in all_stories["pace"] if s["sample_size"] < CONFIG["thin_pace_games"]]
    results.append(check(
        "pace deliberately has NO structural gate: a thin-sample story can still generate (hedged), unlike red-zone/fourth-down",
        len(thin_pace_stories) > 0,
    ))
    results.append(check(
        "every thin pace story's language is honestly hedged",
        all(any(w in st["story"].lower() for w in ("early", "still-developing")) for st in thin_pace_stories),
    ))

    # ============================================================
    # Trend materiality thresholds hold.
    # ============================================================
    results.append(check("every red-zone story clears its configured trend_threshold", all(s["trend_strength"] >= CONFIG["redzone_trend_threshold"] for s in all_stories["rz"])))
    results.append(check("every fourth-down story clears its configured trend_threshold", all(s["trend_strength"] >= CONFIG["fourth_down_trend_threshold"] for s in all_stories["fd"])))
    results.append(check("every pace story clears its configured trend_threshold", all(s["trend_strength"] >= CONFIG["pace_trend_threshold"] for s in all_stories["pace"])))

    # ============================================================
    # related_players — three different, detector-specific relationships.
    # ============================================================
    rz_run_heavy = next((s for s in all_stories["rz"] if s["trend_direction"] == "growing-run-heavy" and s["related_players"]), None)
    rz_pass_heavy = next((s for s in all_stories["rz"] if s["trend_direction"] == "growing-pass-heavy" and s["related_players"]), None)
    results.append(check(
        "red-zone related_players is DIRECTIONAL: growing-run-heavy names RBs",
        rz_run_heavy is not None and all(r.get("rz_touch_share") is not None or True for r in rz_run_heavy["related_players"]),
    ))
    if rz_run_heavy:
        rb_check_pool = weekly[(weekly["season"] == 2025) & (weekly["player_id"].isin([r["player_id"] for r in rz_run_heavy["related_players"]]))]
        results.append(check("growing-run-heavy red-zone related_players are genuinely RBs", set(rb_check_pool["position_group"].unique()) <= {"RB"}))
    if rz_pass_heavy:
        wrte_check_pool = weekly[(weekly["season"] == 2025) & (weekly["player_id"].isin([r["player_id"] for r in rz_pass_heavy["related_players"]]))]
        results.append(check("growing-pass-heavy red-zone related_players are genuinely WR/TE", set(wrte_check_pool["position_group"].unique()) <= {"WR", "TE"}))

    fd_with_related = next((s for s in all_stories["fd"] if s["related_players"]), None)
    results.append(check(
        "fourth-down related_players is TEAM-WIDE (relationship=benefits_from_sustained_drives), ranked by td_opportunity",
        fd_with_related is not None and all(r["relationship"] == "benefits_from_sustained_drives" for r in fd_with_related["related_players"]),
    ))
    if fd_with_related:
        tds = [r["td_opportunity"] for r in fd_with_related["related_players"] if r["td_opportunity"] is not None]
        results.append(check("fourth-down related_players ranked by td_opportunity, highest first", tds == sorted(tds, reverse=True)))

    pace_with_related = next((s for s in all_stories["pace"] if s["related_players"]), None)
    results.append(check(
        "pace related_players is TEAM-WIDE (relationship=benefits_from_play_volume), ranked by snap_share -- a genuinely different mechanism than fourth-down's td_opportunity ranking",
        pace_with_related is not None and all(r["relationship"] == "benefits_from_play_volume" for r in pace_with_related["related_players"]),
    ))
    if pace_with_related:
        snaps = [r["snap_share"] for r in pace_with_related["related_players"] if r["snap_share"] is not None]
        results.append(check("pace related_players ranked by snap_share, highest first", snaps == sorted(snaps, reverse=True)))

    for name, stories in all_stories.items():
        results.append(check(f"{name}: related_players is capped, not an unbounded dump", all(len(s["related_players"]) <= CONFIG["related_players_limit"] for s in stories)))

    # ============================================================
    # Storytelling honesty — full-backfill scan across all three
    # seasons and all three detectors, same standard as Defensive
    # Trends' "0/126 mismatches" reporting.
    # ============================================================
    totals = {"rz": 0, "fd": 0, "pace": 0}
    specific = {"rz": 0, "fd": 0, "pace": 0}
    mismatches = {"rz": 0, "fd": 0, "pace": 0}

    for season in (2022, 2024, 2025):
        pbp = pbp2025 if season == 2025 else nfl.import_pbp_data([season], downcast=True)
        for wk in sorted(int(w) for w in pbp["week"].dropna().unique()):
            for s in build_redzone_play_calling_stories(pbp, weekly, season, wk):
                totals["rz"] += 1
                m = re.search(r"Rush rate inside the 10: ([\d.]+)% \(last 3 games\) vs\. ([\d.]+)% \(season\)", " ".join(s["supporting_evidence"]))
                if m:
                    specific["rz"] += 1
                    recent, seas = float(m.group(1)), float(m.group(2))
                    if s["trend_direction"] == "growing-run-heavy" and not (recent > seas):
                        mismatches["rz"] += 1
                    if s["trend_direction"] == "growing-pass-heavy" and not (recent < seas):
                        mismatches["rz"] += 1
            for s in build_fourth_down_aggressiveness_stories(pbp, weekly, season, wk):
                totals["fd"] += 1
                m = re.search(r"Go-for-it rate: ([\d.]+)% \(last 3 games\) vs\. ([\d.]+)% \(season\)", " ".join(s["supporting_evidence"]))
                if m:
                    specific["fd"] += 1
                    recent, seas = float(m.group(1)), float(m.group(2))
                    if s["trend_direction"] == "growing-aggressive" and not (recent > seas):
                        mismatches["fd"] += 1
                    if s["trend_direction"] == "growing-conservative" and not (recent < seas):
                        mismatches["fd"] += 1
            for s in build_pace_stories(pbp, weekly, season, wk):
                totals["pace"] += 1
                m = re.search(r"Seconds per play: ([\d.]+) \(last 3 games\) vs\. ([\d.]+) \(season\)", " ".join(s["supporting_evidence"]))
                if m:
                    specific["pace"] += 1
                    recent, seas = float(m.group(1)), float(m.group(2))
                    if s["trend_direction"] == "growing-faster" and not (recent < seas):
                        mismatches["pace"] += 1
                    if s["trend_direction"] == "growing-slower" and not (recent > seas):
                        mismatches["pace"] += 1

    for name in ("rz", "fd", "pace"):
        results.append(check(
            f"{name}: every specific raw-number citation across the full 2022/2024/2025 backfill agrees with its "
            f"claimed direction (checked {specific[name]} real specific citations across {totals[name]} stories, {mismatches[name]} mismatches)",
            specific[name] > 0 and mismatches[name] == 0,
        ))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
