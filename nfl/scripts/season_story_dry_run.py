"""
Season Story dry run -- OFFLINE, PRINTS ONLY. Never calls a write route,
never calls any tastypickems.com production endpoint, never writes a row
anywhere. Reports who WOULD get a new Season Story version this run, and
who would be capped, without generating or publishing anything.

Two sections:
  1. FIXTURE dry run -- synthetic, hand-built packages covering each real
     rule branch (first appearance, a metric over threshold, an outlier
     week, nothing new, and enough fired candidates to actually exercise
     the cap). Deterministic, no network of any kind.
  2. REAL dry run -- real Table 1 FACTS, computed locally from real
     nflverse play-by-play for season 2026 weeks 3 and 4 (the same
     pbp-derived reconstruction already validated in this project's own
     history, e.g. test_run_pipeline_integration.py) -- NOT a call to
     this project's own tastypickems.com API, which is the thing "no
     production endpoints" actually rules out.

     IMPORTANT HONEST GAP, not glossed over: real Season Story
     ELIGIBILITY (eligible_players_for_season_story) needs real
     nfl_content_drafts/nfl_intelligence_stories rows, which exist ONLY
     in the live Supabase-backed read routes -- calling those IS a
     production endpoint call, so this script does not. The REAL section
     below therefore treats "has a real Table 1 row" as an eligibility
     STAND-IN for demonstration purposes only, clearly labeled as such --
     it is not a claim about how many players are actually eligible.

Run: python3 nfl/scripts/season_story_dry_run.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vendor"))

from player_season_history import build_player_history_package
from season_story import (
    SEASON_STORY_MATERIAL_CHANGE_CONFIG,
    is_material_change,
    rank_and_cap_material_changes,
)


def _row(player_id, week, season=2026, snap_share=0.5, targets=2, carries=2, red_zone_opportunities=1, goal_line_opportunities=0):
    return {
        "player_id": player_id, "season": season, "week": week,
        "snap_share": snap_share, "targets": targets, "carries": carries,
        "red_zone_opportunities": red_zone_opportunities, "goal_line_opportunities": goal_line_opportunities,
    }


def run_fixture_dry_run():
    print("=" * 70)
    print("SECTION 1 -- FIXTURE dry run (fully synthetic, offline)")
    print("=" * 70)

    season = 2026
    reconciled_weeks_v1 = [1, 2, 3]
    reconciled_weeks_v2 = [1, 2, 3, 4]

    # A deliberately small, low cap for THIS fixture run only, so the
    # cap actually has something to do -- the real provisional default
    # (40) would never bind on a handful of fixture players.
    demo_config = {**SEASON_STORY_MATERIAL_CHANGE_CONFIG, "max_new_versions_per_run": 2}

    scenarios = {}

    # FIRST_APPEARANCE: no previous story at all.
    rows_fa = [_row("FIRST_APPEARANCE", w) for w in (1, 2, 3)]
    scenarios["FIRST_APPEARANCE"] = (rows_fa, None)

    # OVER_THRESHOLD: season_baseline.targets moves well past its own threshold.
    rows_ot_v1 = [_row("OVER_THRESHOLD", w, targets=2) for w in (1, 2, 3)]
    rows_ot_v2 = rows_ot_v1 + [_row("OVER_THRESHOLD", 4, targets=10)]
    scenarios["OVER_THRESHOLD"] = (rows_ot_v2, build_player_history_package("OVER_THRESHOLD", season, 3, rows_ot_v1, reconciled_weeks_v1))

    # OUTLIER_WEEK: season_baseline barely moves, but this week alone is a real outlier.
    rows_ow_v1 = [_row("OUTLIER_WEEK", w, carries=3) for w in (1, 2, 3)]
    rows_ow_v2 = rows_ow_v1 + [_row("OUTLIER_WEEK", 4, carries=20)]
    scenarios["OUTLIER_WEEK"] = (rows_ow_v2, build_player_history_package("OUTLIER_WEEK", season, 3, rows_ow_v1, reconciled_weeks_v1))

    # NOTHING_NEW: a real new week exists, but every metric is steady.
    rows_nn_v1 = [_row("NOTHING_NEW", w, targets=4, carries=4) for w in (1, 2, 3)]
    rows_nn_v2 = rows_nn_v1 + [_row("NOTHING_NEW", 4, targets=4, carries=4)]
    scenarios["NOTHING_NEW"] = (rows_nn_v2, build_player_history_package("NOTHING_NEW", season, 3, rows_nn_v1, reconciled_weeks_v1))

    # NO_NEW_ROW: this player simply has no real week-4 row at all (a bye,
    # or no real red-zone touch) -- absence, not a zero. Must never fire.
    rows_nr_v1 = [_row("NO_NEW_ROW", w, targets=6) for w in (1, 2, 3)]
    scenarios["NO_NEW_ROW"] = (rows_nr_v1, build_player_history_package("NO_NEW_ROW", season, 3, rows_nr_v1, reconciled_weeks_v1))

    # A fifth scenario, deliberately BELOW every threshold (a small, real
    # bump that doesn't cross the rule's own bar) -- included so the
    # printed output shows "fired" isn't just "everyone with a new week,"
    # and so there are real non-firing candidates alongside the capped one.
    rows_c1_v1 = [_row("SMALL_REAL_BUMP", w, targets=3) for w in (1, 2, 3)]
    rows_c1_v2 = rows_c1_v1 + [_row("SMALL_REAL_BUMP", 4, targets=5)]
    scenarios["SMALL_REAL_BUMP"] = (rows_c1_v2, build_player_history_package("SMALL_REAL_BUMP", season, 3, rows_c1_v1, reconciled_weeks_v1))

    evaluations = {}
    for player_id, (rows_v2, prev_package) in scenarios.items():
        new_package = build_player_history_package(player_id, season, 4, rows_v2, reconciled_weeks_v2)
        evaluations[player_id] = is_material_change(new_package, prev_package, demo_config)

    capped_results = rank_and_cap_material_changes(evaluations, demo_config)

    print(f"\nmax_new_versions_per_run for this demo: {demo_config['max_new_versions_per_run']} (real provisional default is "
          f"{SEASON_STORY_MATERIAL_CHANGE_CONFIG['max_new_versions_per_run']} -- lowered here only to actually exercise capping)")
    print()
    for player_id in sorted(capped_results, key=lambda p: (-capped_results[p]["magnitude"], p)):
        r = capped_results[player_id]
        status = "FIRES (published)" if r["fires"] and not r["capped"] else ("FIRES (CAPPED this run)" if r["fires"] else "no change")
        mag = "inf" if r["magnitude"] == float("inf") else f"{r['magnitude']:.2f}"
        print(f"  {player_id:24s} magnitude={mag:>5s}  {status:24s}  {r['reason']}")

    fired = sum(1 for r in capped_results.values() if r["fires"])
    published = sum(1 for r in capped_results.values() if r["fires"] and not r["capped"])
    capped = sum(1 for r in capped_results.values() if r["capped"])
    print(f"\nFixture totals: {fired} fired uncapped, {published} would publish this run, {capped} capped (deferred, not discarded)")


def run_real_week3_week4_dry_run():
    print("\n" + "=" * 70)
    print("SECTION 2 -- REAL dry run: real pbp-derived Table 1 facts,")
    print("season 2026, weeks 3 and 4. ELIGIBILITY IS A STAND-IN (see")
    print("this script's own module docstring) -- real eligibility needs")
    print("a production endpoint call this script deliberately never makes.")
    print("=" * 70)

    try:
        import nfl_data_py as nfl
        from redzone import aggregate_redzone_game, aggregate_whole_game_carries, aggregate_whole_game_targets
    except Exception as e:
        print(f"\n(skipped -- could not import real pbp tooling: {type(e).__name__}: {e})")
        return

    SEASON = 2026
    try:
        pbp = nfl.import_pbp_data([SEASON], downcast=True)
        rosters = nfl.import_seasonal_rosters([SEASON])
    except Exception as e:
        print(f"\n(skipped -- real pbp/roster fetch failed: {type(e).__name__}: {e})")
        return

    weekly = aggregate_redzone_game(pbp, rosters)
    carries = aggregate_whole_game_carries(pbp)
    targets = aggregate_whole_game_targets(pbp)[["game_id", "player_id", "season", "week", "targets"]]
    weekly = weekly.merge(carries, on=["player_id", "season", "week"], how="left")
    weekly = weekly.merge(targets, on=["game_id", "player_id", "season", "week"], how="left")
    weekly["snap_share"] = None  # not computed in this dry run -- needs snap_counts+id_crosswalk, not needed to exercise the rule
    weekly = weekly.rename(columns={"rz_touches": "red_zone_opportunities", "gl_touches": "goal_line_opportunities"})
    rows = weekly[[
        "player_id", "season", "week", "snap_share", "targets", "carries",
        "red_zone_opportunities", "goal_line_opportunities",
    ]].to_dict("records")

    week3_rows = [r for r in rows if r["week"] == 3]
    week4_rows = [r for r in rows if r["week"] == 4]
    print(f"\nReal week 3 rows: {len(week3_rows)}")
    print(f"Real week 4 rows: {len(week4_rows)}")
    print(
        "WARNING: only two real reconciled weeks exist in Table 1 for this season as of this "
        "writing, and week 4 itself looks incomplete (far fewer real rows than week 3) -- "
        "nothing below should be read as a validated threshold calibration."
    )

    week3_players = {r["player_id"] for r in week3_rows}
    week4_players = {r["player_id"] for r in week4_rows}
    overlap = sorted(week3_players & week4_players)
    print(f"\nReal players with a row in BOTH week 3 and week 4 (eligibility stand-in: 'has a real Table 1 row'): {len(overlap)}")

    evaluations = {}
    for player_id in overlap:
        pkg_w3 = build_player_history_package(player_id, SEASON, 3, rows, reconciled_weeks=[3])
        pkg_w4 = build_player_history_package(player_id, SEASON, 4, rows, reconciled_weeks=[3, 4])
        evaluations[player_id] = is_material_change(pkg_w4, pkg_w3, SEASON_STORY_MATERIAL_CHANGE_CONFIG)

    capped_results = rank_and_cap_material_changes(evaluations, SEASON_STORY_MATERIAL_CHANGE_CONFIG)
    print(f"\nReal provisional cap (max_new_versions_per_run={SEASON_STORY_MATERIAL_CHANGE_CONFIG['max_new_versions_per_run']}):")
    for player_id in sorted(capped_results, key=lambda p: (-capped_results[p]["magnitude"], p)):
        r = capped_results[player_id]
        print(f"  {player_id}: fires={r['fires']} capped={r['capped']} magnitude={r['magnitude']:.2f}  ({r['reason']})")

    fired = sum(1 for r in capped_results.values() if r["fires"])
    capped = sum(1 for r in capped_results.values() if r["capped"])
    print(
        f"\nReal totals: {fired} of {len(overlap)} overlapping real players would fire uncapped, "
        f"{capped} capped -- at this sample size ({len(overlap)} players) the provisional cap of "
        f"{SEASON_STORY_MATERIAL_CHANGE_CONFIG['max_new_versions_per_run']} never actually binds; "
        f"it would only matter at real full-population scale (hundreds of eligible players/week)."
    )


if __name__ == "__main__":
    run_fixture_dry_run()
    run_real_week3_week4_dry_run()
