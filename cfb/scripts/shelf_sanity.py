"""
Real-data sanity check for CFB Phase 5: the Target Magnets signal
(redzone.aggregate_receiving_game_cfb + scoring.score_target_magnets_cfb)
and the 8-shelf CFB Picks curation (curate_cfb_shelves.assign_cfb_shelves /
add_shelf_convergence / select_cfb_tasty_six).

Pulls real CFBD data directly, in-process, same pattern score_sanity.py's
own real-data validation established (no deployed-endpoint dependency —
see that script's own history for why: the production deployment was
stale for its first 9 days). Ingests season weeks first_week..target_week,
runs the full scoring chain for target_week, builds all 8 shelves, and
prints:
  - per-week completeness (games, player/defense/receiving rows, unmatched
    TD attribution)
  - Target Magnets validation: top/bottom target_share, a real
    pass-catcher gut-check
  - each of the 8 shelves' top 6
  - shelf convergence ("N SHELVES AGREE") distribution
  - Tasty Six picks + which shelf each came from
  - AP Top 25 composition compared across an earlier week and target_week
    (does the eligible population actually move)

Usage:
    CFBD_API_KEY=<key> python3 cfb/scripts/shelf_sanity.py 2025 1 6
    # season, first_week, target_week — weeks first_week..target_week are
    # all ingested (role_momentum/target_magnets both need trend_window+1
    # games before producing a real, non-neutral score — target_week
    # should be at least 4 for either to show real values).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))

import pandas as pd

from ids import CFBDError, fbs_team_ids, fetch_ap_top25, team_conference_map
from plays_stats import completed_games, fetch_games, fetch_scoring_td_play_ids, fetch_week_play_stats
from redzone import aggregate_receiving_game_cfb, aggregate_redzone_allowed_cfb, aggregate_redzone_game_cfb
from role_momentum import build_role_momentum_weekly, role_weekly_frame
from roster import raw_position_lookup
from scoring import CONFIG, drop_non_fbs_opponent_rows

from curate_cfb_shelves import (  # noqa: E402
    CFB_SHELF_ORDER,
    add_shelf_convergence,
    assign_cfb_shelves,
    curate_cfb_shelves,
    select_cfb_tasty_six,
)

pd.set_option("display.width", 160)
pd.set_option("display.max_columns", 20)


def fetch_week_local(season: int, week: int) -> tuple[list, list, list]:
    """Same real-data sequence score_sanity.py's own local runner uses,
    plus the new receiving aggregation alongside the other two."""
    games = fetch_games(season, week, season_type="regular")
    completed = completed_games(games)
    play_stats, fetch_diag = fetch_week_play_stats(completed, season_type="regular")

    schools = sorted(
        {g.get("homeTeam") for g in games if g.get("homeTeam")}
        | {g.get("awayTeam") for g in games if g.get("awayTeam")}
    )
    raw_pos = raw_position_lookup(season, fallback_teams=schools)

    completed_ids = {int(g["id"]) for g in completed if g.get("id") is not None}
    td_play_ids, td_diag = fetch_scoring_td_play_ids(
        season, week, completed_game_ids=completed_ids, season_type="regular",
    )

    player_rows, player_diag = aggregate_redzone_game_cfb(
        play_stats, games, raw_pos, td_play_ids, season=season, week=week
    )
    defense_rows, defense_diag = aggregate_redzone_allowed_cfb(
        play_stats, games, raw_pos, td_play_ids, season=season, week=week
    )
    receiving_rows, receiving_diag = aggregate_receiving_game_cfb(
        play_stats, games, raw_pos, season=season, week=week
    )

    print(
        f"  week {week}: {len(completed)} games, {len(player_rows)} player rows, "
        f"{len(defense_rows)} defense rows, {len(receiving_rows)} receiving rows, "
        f"td unmatched={player_diag['td_attribution']['unmatched']}, "
        f"receiving unresolved={receiving_diag.get('unresolved_athlete_count', 0)}"
    )
    return player_rows, defense_rows, receiving_rows


if __name__ == "__main__":
    import os

    if not os.environ.get("CFBD_API_KEY"):
        sys.exit("set CFBD_API_KEY")

    season = int(sys.argv[1]) if len(sys.argv) > 1 else 2025
    first_week = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    target_week = int(sys.argv[3]) if len(sys.argv) > 3 else 6
    earlier_week = first_week + 2 if first_week + 2 < target_week else first_week

    print(f"Fetching {season} weeks {first_week}-{target_week} directly from CFBD ...")
    p_parts, d_parts, r_parts = [], [], []
    for wk in range(first_week, target_week + 1):
        try:
            pr, dr, rr = fetch_week_local(season, wk)
        except CFBDError as e:
            sys.exit(f"week {wk} CFBD error: {e}")
        p_parts += pr
        d_parts += dr
        r_parts += rr

    players = pd.DataFrame(p_parts)
    defense = pd.DataFrame(d_parts)
    receiving = pd.DataFrame(r_parts)
    raw_p, raw_d, raw_r = len(players), len(defense), len(receiving)

    fbs = fbs_team_ids(season)
    players = drop_non_fbs_opponent_rows(players, fbs)
    defense = drop_non_fbs_opponent_rows(defense, fbs)
    receiving = drop_non_fbs_opponent_rows(receiving, fbs)
    print(
        f"\nFBS filter: player-rows {raw_p} -> {len(players)}, "
        f"defense-rows {raw_d} -> {len(defense)}, "
        f"receiving-rows {raw_r} -> {len(receiving)}"
    )

    print(f"\nbuilding cfb_player_role_weekly (weeks {first_week}-{target_week}, no prior-season returning flag) ...")
    role_rows, role_diag = build_role_momentum_weekly(season, range(first_week, target_week + 1))
    role_weekly = role_weekly_frame(role_rows)
    print(
        f"  {role_diag['role_rows_total']} rows, {role_diag['distinct_players']} distinct players, "
        f"ppa_join_rate={role_diag['ppa_join_rate']}"
    )

    # ================= scoring chain =================
    result = curate_cfb_shelves(
        players, defense, role_weekly, season, target_week, fbs, receiving_weekly=receiving,
    )
    scored, week_rows = result["scored"], result["week_rows"]
    print(f"\nScored whole season: {len(scored)} rows. Target week ({target_week}) rows: {len(week_rows)}")

    # ================= Target Magnets validation =================
    print("\n" + "=" * 78)
    print(f"TARGET MAGNETS — {season} week {target_week}")
    print("=" * 78)
    tm_wk = week_rows[week_rows["position_group"].isin(["WR", "TE", "RB"])].copy()
    ungated_tm = tm_wk[tm_wk["target_magnets_gated"] == False]  # noqa: E712
    min_targets = CONFIG["target_magnets"]["min_targets_for_qualification"]
    print(
        f"\n  receiving rows this week: {len(tm_wk)}  |  gated (season targets < {min_targets}): "
        f"{int(tm_wk['target_magnets_gated'].sum())}  |  scored: {len(ungated_tm)}"
    )
    tm_cols = ["player_name", "position_group", "team", "opponent", "target_magnets", "target_magnets_completeness"]
    print("\n--- Top 10 UNGATED by target_magnets ---")
    print(ungated_tm.sort_values("target_magnets", ascending=False).head(10)[tm_cols].to_string(index=False))
    print("\n--- Bottom 10 UNGATED by target_magnets ---")
    print(ungated_tm.sort_values("target_magnets", ascending=False).tail(10)[tm_cols].to_string(index=False))

    print("\n--- Real pass-catcher gut-check: top 10 raw target_share this week (any completeness) ---")
    tgt_cols = ["player_name", "position_group", "team", "opponent"]
    receiving_wk = receiving[receiving["week"] == target_week].copy()
    top_raw = receiving_wk.sort_values("target_share", ascending=False).head(10)
    print(top_raw[tgt_cols + ["targets", "receptions", "team_targets", "target_share"]].to_string(index=False))

    # ================= 8 shelves =================
    print("\n" + "=" * 78)
    print(f"8 CFB SHELVES — {season} week {target_week}")
    print("=" * 78)

    ap_ranks_target = fetch_ap_top25(season, target_week)
    ap_ranks_earlier = fetch_ap_top25(season, earlier_week)
    team_conf = team_conference_map(season)

    shelves = assign_cfb_shelves(week_rows, ap_ranks_target, team_conf)
    week_rows_conv = add_shelf_convergence(week_rows, shelves)

    display_cols = ["player_name", "team", "position_group"]
    for shelf_name in CFB_SHELF_ORDER:
        pool = shelves[shelf_name]
        print(f"\n--- {shelf_name} ({len(pool)} players) ---")
        if pool.empty:
            print("  (empty)")
            continue
        rank_col = (
            "td_opportunity" if shelf_name == "goal_line_favorites"
            else "role_momentum" if shelf_name == "workhorses"
            else "target_magnets" if shelf_name == "target_magnets"
            else "tpe_score"
        )
        print(pool[display_cols + [rank_col]].to_string(index=False))

    # ================= convergence =================
    print("\n" + "=" * 78)
    print("SHELF CONVERGENCE (\"N SHELVES AGREE\")")
    print("=" * 78)
    conv_dist = week_rows_conv["shelf_count"].value_counts().sort_index(ascending=False)
    print(conv_dist.to_string())
    top_conv = week_rows_conv[week_rows_conv["shelf_count"] > 1].sort_values("shelf_count", ascending=False)
    if len(top_conv):
        print("\nplayers on more than one shelf:")
        print(top_conv[["player_name", "team", "shelf_count", "shelves"]].head(15).to_string(index=False))
    else:
        print("\n(no player appears on more than one shelf this week)")

    # ================= Tasty Six =================
    print("\n" + "=" * 78)
    print("TASTY SIX (default: one claim per shelf, CFB_SHELF_ORDER walk, fallback-to-next-eligible)")
    print("=" * 78)
    tasty_six = select_cfb_tasty_six(shelves)
    for i, pick in enumerate(tasty_six, 1):
        print(f"  {i}. {pick['player_name']} ({pick['team']}, {pick['position_group']}) "
              f"<- {pick['tasty_six_source']}   tpe_score={pick.get('tpe_score')}")
    if len(tasty_six) < 6:
        print(f"  (only {len(tasty_six)}/6 found — thin week, not backfilled)")

    # ================= AP Top 25 composition change =================
    print("\n" + "=" * 78)
    print(f"AP TOP 25 COMPOSITION — week {earlier_week} vs week {target_week}")
    print("=" * 78)
    earlier_ids = set(ap_ranks_earlier.keys())
    target_ids = set(ap_ranks_target.keys())
    entered = target_ids - earlier_ids
    left = earlier_ids - target_ids
    print(f"  week {earlier_week}: {len(earlier_ids)} ranked teams   week {target_week}: {len(target_ids)} ranked teams")
    print(f"  entered top 25: {[ap_ranks_target[t]['school'] for t in entered]}")
    print(f"  left top 25:    {[ap_ranks_earlier[t]['school'] for t in left]}")
    if not entered and not left:
        print("  NOTE: identical composition across these two weeks — try a wider week gap "
              "if this is meant to demonstrate real movement.")

    print("\n--- Top 25 TD Watch shelf, week", earlier_week, "vs week", target_week, "---")
    earlier_week_rows = scored[scored["week"] == earlier_week]
    earlier_top25 = earlier_week_rows[earlier_week_rows["team_id"].isin(ap_ranks_earlier.keys())]
    earlier_top25 = earlier_top25.sort_values("tpe_score", ascending=False).head(6)
    print(f"  week {earlier_week}:")
    print("   " + ", ".join(earlier_top25["player_name"].fillna("?").tolist()) if len(earlier_top25) else "  (none)")
    print(f"  week {target_week}:")
    tw6 = shelves["top25_td_watch"]
    print("   " + ", ".join(tw6["player_name"].fillna("?").tolist()) if len(tw6) else "  (none)")

    # ================= conference shelves sanity =================
    print("\n" + "=" * 78)
    print("CONFERENCE SHELVES — real names, no unmatched entities")
    print("=" * 78)
    for shelf_name in ("sec_td_watch", "big_ten_td_watch", "big12_td_watch", "acc_td_watch"):
        pool = shelves[shelf_name]
        teams = sorted(set(pool["team"].dropna()))
        print(f"  {shelf_name}: {len(pool)} players, teams = {teams}")
