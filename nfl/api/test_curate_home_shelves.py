"""
Tests curate_home_shelves.py against real historical data — real players,
real scored pillars, no mocks. Real seasons never captured real ATTD odds
(Market Value has only ever been polled live, never backfilled — see
market_value.py's own docstring), so consensus_price_american is a
clearly-labeled SYNTHETIC re-attachment of real-looking odds onto real
player rows, the same technique already validated earlier this session
(shelves.py's own Phase 3 validation, market_intelligence.py's synthetic
contrast case) — used specifically to exercise real mechanism, not to
claim anything about a real player's real market price.

Run: python3 nfl/api/test_curate_home_shelves.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from curate_home_shelves import (
    CONFIG,
    ODDS_SHELVES,
    SHELF_ORDER,
    TREND_SHELVES,
    apply_shelf_cap,
    assign_home_shelves,
    curate_nfl_shelves,
    select_tasty_six,
    shape_content_draft_rows,
)

WEEKLY_PATH = Path(__file__).resolve().parent.parent / "scripts" / "player_redzone_weekly.csv"


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


if __name__ == "__main__":
    if not WEEKLY_PATH.exists():
        print(f"SKIPPED — {WEEKLY_PATH} not present in this environment.")
        raise SystemExit(0)

    weekly = pd.read_csv(WEEKLY_PATH)
    results = []

    # ============================================================
    # Real 2025 Week 10, synthetic odds re-attached (real seasons never
    # captured real odds — see module docstring).
    # ============================================================
    sub = weekly[(weekly["season"] == 2025) & (weekly["week"] == 10)].copy()
    rng = np.random.default_rng(11)
    sub["consensus_price_american"] = np.where(
        sub["tpe_score"].notna(), rng.integers(250, 1500, size=len(sub)), np.nan,
    )
    print(f"Real pool: {len(sub)} rows, 2025 Week 10 (synthetic odds)\n")

    result = curate_nfl_shelves(sub, season=2025, week=10)
    home = result["home_assignments"]
    capped = result["capped"]
    tasty_six = result["tasty_six"]
    draft_rows = result["content_draft_rows"]

    print("Home shelf distribution:")
    print(home["home_shelf"].value_counts().to_string())
    print()

    # ============================================================
    # Eligibility filter (build step 1) — every home-assigned player is
    # genuinely ATTD >= 300, and no player appears twice.
    # ============================================================
    results.append(check(
        "every home-assigned player has real consensus_price_american >= 300",
        (home["consensus_price_american"] >= CONFIG["attd_odds_floor"]).all(),
    ))
    results.append(check("every home-assigned player appears at most once", home["player_id"].is_unique))
    results.append(check("every home_shelf value is one of the fixed seven", home["home_shelf"].isin(SHELF_ORDER).all()))

    # ============================================================
    # Proposal 1 — trend beats odds (unaffected by the percentile fix);
    # trend-vs-trend now uses PERCENTILE rank within each shelf's own
    # population, not raw signal value.
    # ============================================================
    trend_rows = home[home["home_shelf"].isin(TREND_SHELVES)]
    results.append(check("at least one real player home-assigned to a trend shelf", len(trend_rows) > 0))

    odds_rows = home[home["home_shelf"].isin(ODDS_SHELVES)]
    if len(odds_rows) > 0:
        sample = odds_rows.iloc[0]
        results.append(check(
            f"a real odds-shelf home assignment ({sample['player_name']}) has NO qualifying trend shelf "
            f"(confirms the fallback path only fires when trend truly doesn't apply — unaffected by the "
            f"percentile fix, since this branch never compares scores at all)",
            not any(s in TREND_SHELVES for s in sample["qualifying_shelves"]),
        ))
    else:
        print("(skipped the odds-fallback check — no real player fell through to an odds shelf this run)")

    # Real regression guard for the percentile fix itself, replacing the
    # old raw-score-share narration (which is now stale — the code no
    # longer does a raw comparison at all). Checks the CONTESTED overlap
    # groups directly (the fairest real comparison, isolated from Red
    # Zone Trends' larger pool size inflating its raw home-assignment
    # count for structural reasons unrelated to the tiebreak itself) and
    # asserts the win rate is no longer pinned near the old 82-86%
    # raw-score finding — it should move with real week-to-week pool
    # composition, not sit at a fixed lopsided number every week.
    from curate_home_shelves import _shelf_qualifying_pools, _trend_percentiles
    pools = _shelf_qualifying_pools(sub)
    pcts = _trend_percentiles(pools)
    rz_ids = set(pools["Red Zone Trends"]["player_id"])
    for pos_shelf in ("RB Trends", "WR Trends", "TE Trends"):
        pos_ids = set(pools[pos_shelf]["player_id"])
        both = rz_ids & pos_ids
        if len(both) < 5:
            print(f"(skipped the {pos_shelf} contested-win-rate check — only {len(both)} real overlap cases this week, too thin to read anything from)")
            continue
        rz_wins = sum(1 for pid in both if pcts["Red Zone Trends"][pid] > pcts[pos_shelf][pid])
        win_rate = rz_wins / len(both)
        print(f"Real {pos_shelf} contested overlap: Red Zone Trends wins {rz_wins} of {len(both)} ({win_rate*100:.0f}%) under percentile comparison")
        results.append(check(
            f"{pos_shelf}: percentile-normalized contested win rate ({win_rate*100:.0f}%) is meaningfully "
            f"below the old raw-score finding (82-86%), confirming the fix actually changed real outcomes",
            win_rate < 0.80,
        ))

    # ============================================================
    # Max-6-per-shelf cap (build step 3) — never exceeded, and a capped
    # player keeps their real qualifying_shelves tag data (not dropped).
    # ============================================================
    kept = capped[~capped["capped"]]
    per_shelf_kept_counts = kept.groupby("home_shelf").size()
    results.append(check(
        f"no shelf's surviving (uncapped) count exceeds max_per_shelf ({CONFIG['max_per_shelf']}) "
        f"(real counts: {per_shelf_kept_counts.to_dict()})",
        (per_shelf_kept_counts <= CONFIG["max_per_shelf"]).all(),
    ))
    if capped["capped"].any():
        a_capped_row = capped[capped["capped"]].iloc[0]
        results.append(check(
            f"a real capped player ({a_capped_row['player_name']}) still has their real qualifying_shelves "
            f"tag data (not dropped from the dataset, just excluded from this shelf's written rows)",
            isinstance(a_capped_row["qualifying_shelves"], list),
        ))
    else:
        print("(skipped the capped-player tag check — no real shelf exceeded 6 candidates this run)")

    # ============================================================
    # Tasty Six (build step 5) — approved threshold, sparse is fine,
    # never manufactured.
    # ============================================================
    results.append(check("Tasty Six has exactly 7 entries (one slot per shelf, sparse allowed)", len(tasty_six) == 7))
    non_none = {k: v for k, v in tasty_six.items() if v is not None}
    for shelf_name, row in non_none.items():
        results.append(check(
            f"{shelf_name}'s real Tasty Six pick ({row['player_name']}) genuinely clears the approved threshold "
            f"(tpe_score {row['tpe_score']:.1f} >= {CONFIG['tasty_six_tpe_threshold']}, "
            f"evidence_quality {row['evidence_quality']:.1f} >= {CONFIG['tasty_six_evidence_threshold']})",
            row["tpe_score"] >= CONFIG["tasty_six_tpe_threshold"] and row["evidence_quality"] >= CONFIG["tasty_six_evidence_threshold"],
        ))
    results.append(check(
        f"Tasty Six is legitimately sparse on this real pool, not manufactured ({len(non_none)} of 7 filled)",
        len(non_none) < 7,
    ))
    if non_none:
        results.append(check("every real Tasty Six pick also has a real written content_draft_row with is_tasty_six=True",
            all(any(r["player_id"] == row["player_id"] and r["shelf"] == shelf and r["is_tasty_six"] for r in draft_rows)
                for shelf, row in non_none.items())))

    # ============================================================
    # content_draft_rows shaping (build step 7, minus content) — every
    # real field this task owns is populated; content fields are
    # explicitly None, not fabricated; review_status is always
    # pending_review, never anything else.
    # ============================================================
    results.append(check("content_draft_rows count matches the uncapped home-assignment count", len(draft_rows) == len(kept)))
    results.append(check("every row has review_status='pending_review'", all(r["review_status"] == "pending_review" for r in draft_rows)))
    results.append(check("every row has a real shelf, rank, and player_id", all(r["shelf"] and r["rank"] and r["player_id"] for r in draft_rows)))

    # ============================================================
    # Real write-schema shape (confirmed against the live Lovable
    # route, not the earlier placeholder field names): title/why_
    # reasons, not headline/why_its_tasty -- stale assertions from
    # before that fix updated here, not left silently crashing (the
    # old assertions referenced fields that no longer exist at all).
    # Regular (non-Tasty-Six) rows get real deterministic content from
    # shelves.py's own story generators, reshaped into the real why_
    # reasons array; Tasty-Six rows without a real anthropic_api_key
    # stay content-less (None/[]) but STILL get a required, real
    # confidence_band (see nfl_writer_common.py).
    # ============================================================
    regular_rows = [r for r in draft_rows if not r["is_tasty_six"]]
    tasty_rows = [r for r in draft_rows if r["is_tasty_six"]]
    results.append(check(
        f"every real regular (non-Tasty-Six) row has a real, non-empty title and a real why_reasons array "
        f"({len(regular_rows)} rows checked)",
        len(regular_rows) > 0 and all(
            isinstance(r["title"], str) and r["title"] and isinstance(r["why_reasons"], list) and len(r["why_reasons"]) > 0
            for r in regular_rows
        ),
    ))
    results.append(check(
        "every real regular row's why_reasons uses the real inner shape (pillar/stars/text/citation)",
        all(
            set(wr.keys()) == {"pillar", "stars", "text", "citation"}
            for r in regular_rows for wr in r["why_reasons"]
        ),
    ))
    results.append(check(
        f"every real Tasty-Six row still has title=None/why_reasons=[] (no anthropic_api_key given this run "
        f"-- reserved for Part C's real LLM writer, not this deterministic system) ({len(tasty_rows)} rows checked)",
        all(r["title"] is None and r["why_reasons"] == [] for r in tasty_rows) if tasty_rows else True,
    ))
    if not tasty_rows:
        print("(no real Tasty-Six content_draft_row this run to check the exclusion against — see the Tasty Six sparsity check above)")
    results.append(check(
        "editorial_sentence is None for every regular row (MLB's own regular-card-has-no-editorial-sentence convention, reused)",
        all(r["editorial_sentence"] is None for r in regular_rows),
    ))
    results.append(check(
        "confidence_band is a real, non-null string on EVERY row, regular or Tasty Six (required by the real schema)",
        all(isinstance(r["confidence_band"], str) and r["confidence_band"] for r in draft_rows),
    ))
    results.append(check(
        "writer_type is 'shelf_card' for every regular row, 'tasty_six' for every Tasty Six row",
        all(r["writer_type"] == "shelf_card" for r in regular_rows)
        and all(r["writer_type"] == "tasty_six" for r in tasty_rows),
    ))
    results.append(check(
        "validation_passed/validation_issues are real, non-null on every row (True/[] for the deterministic path, never hardcoded for Tasty Six)",
        all(isinstance(r["validation_passed"], bool) and isinstance(r["validation_issues"], list) for r in draft_rows),
    ))

    print("\nReal reconnected content, spot-check across multiple real players/shelves:")
    seen_shelves = set()
    for r in regular_rows:
        if r["shelf"] in seen_shelves:
            continue
        seen_shelves.add(r["shelf"])
        print(f"  [{r['shelf']}] {r['player_name']} (confidence_band={r['confidence_band']})")
        print(f"    title: {r['title']}")
        print(f"    why_reasons: {r['why_reasons']}")
    print()

    # ============================================================
    # Synthetic, deterministic unit test of the trend-vs-trend tiebreak
    # mechanism itself, isolated from real-data variance — player A
    # qualifies for both Red Zone Trends (td_opportunity=80) and RB
    # Trends (role_momentum=60): must land on Red Zone Trends. Player B
    # qualifies for both with the SCORES REVERSED (role_momentum=85,
    # td_opportunity=40): must land on RB Trends instead — proves the
    # comparison is genuinely per-player, not a fixed shelf preference.
    # ============================================================
    synthetic = pd.DataFrame([
        {
            "player_id": "SYN_A", "player_name": "Synthetic A", "posteam": "TST", "position_group": "RB",
            "td_opportunity": 80.0, "td_opportunity_completeness": 90.0,
            "role_momentum": 60.0, "role_momentum_completeness": 90.0,
            "tpe_score": 50.0, "evidence_quality": 90.0, "consensus_price_american": 400,
        },
        {
            "player_id": "SYN_B", "player_name": "Synthetic B", "posteam": "TST", "position_group": "RB",
            "td_opportunity": 40.0, "td_opportunity_completeness": 90.0,
            "role_momentum": 85.0, "role_momentum_completeness": 90.0,
            "tpe_score": 50.0, "evidence_quality": 90.0, "consensus_price_american": 400,
        },
    ])
    syn_home = assign_home_shelves(synthetic)
    syn_a = syn_home[syn_home["player_id"] == "SYN_A"].iloc[0]
    syn_b = syn_home[syn_home["player_id"] == "SYN_B"].iloc[0]
    results.append(check(
        "synthetic trend-vs-trend: Player A (td_opportunity 80 > role_momentum 60) lands on Red Zone Trends",
        syn_a["home_shelf"] == "Red Zone Trends",
    ))
    results.append(check(
        "synthetic trend-vs-trend: Player B (role_momentum 85 > td_opportunity 40, scores REVERSED) lands on RB Trends instead",
        syn_b["home_shelf"] == "RB Trends",
    ))

    # ============================================================
    # Second synthetic case, specifically for the percentile fix: A and
    # B above use 2-player pools, where percentile trivially preserves
    # raw ordering (proves the WIRING works, not the calibration fix
    # itself). Player C is built to make RAW and PERCENTILE actively
    # DISAGREE: raw td_opportunity (70) > raw role_momentum (45), so a
    # raw-score comparison would pick Red Zone Trends — but decoy
    # players shift each shelf's own reference population so C's REAL
    # relative standing is the opposite: bottom of a Red Zone Trends
    # pool stacked with higher scores (0th percentile) vs. top of an RB
    # Trends pool stacked with lower scores (75th percentile). If the
    # fix is genuinely percentile-based, C lands on RB Trends despite
    # having the numerically higher raw Red Zone Trends score.
    # ============================================================
    decoys = (
        [{
            "player_id": f"SYN_DECOY_RZ_{i}", "player_name": f"Decoy RZ {i}", "posteam": "TST", "position_group": "WR",
            "td_opportunity": val, "td_opportunity_completeness": 90.0,
            "role_momentum": np.nan, "role_momentum_completeness": np.nan,
            "tpe_score": 50.0, "evidence_quality": 90.0, "consensus_price_american": 400,
        } for i, val in enumerate([85.0, 90.0, 95.0])]
        + [{
            "player_id": f"SYN_DECOY_RB_{i}", "player_name": f"Decoy RB {i}", "posteam": "TST", "position_group": "RB",
            "td_opportunity": np.nan, "td_opportunity_completeness": np.nan,
            "role_momentum": val, "role_momentum_completeness": 90.0,
            "tpe_score": 50.0, "evidence_quality": 90.0, "consensus_price_american": 400,
        } for i, val in enumerate([10.0, 15.0, 20.0])]
    )
    player_c = {
        "player_id": "SYN_C", "player_name": "Synthetic C", "posteam": "TST", "position_group": "RB",
        "td_opportunity": 70.0, "td_opportunity_completeness": 90.0,
        "role_momentum": 45.0, "role_momentum_completeness": 90.0,
        "tpe_score": 50.0, "evidence_quality": 90.0, "consensus_price_american": 400,
    }
    synthetic_divergence = pd.DataFrame([player_c] + decoys)
    syn_div_home = assign_home_shelves(synthetic_divergence)
    syn_c = syn_div_home[syn_div_home["player_id"] == "SYN_C"].iloc[0]
    results.append(check(
        "synthetic raw-vs-percentile DIVERGENCE case: Player C has the numerically HIGHER raw Red Zone "
        "Trends score (70 vs 45), but ranks 0th percentile there (stacked with higher decoys) vs. 75th "
        "percentile in RB Trends (stacked with lower decoys) — lands on RB Trends, proving the comparison "
        "is genuinely percentile-based, not just raw comparison with extra steps",
        syn_c["home_shelf"] == "RB Trends",
    ))

    # ============================================================
    # pbp threading fix: WR/TE Trends' target_share/target_share_trend
    # role_signal candidates via the LIVE write path (shape_content_
    # draft_rows -> _story_for_row), not just the shelves.py fixture
    # path (build_wr_trends/build_te_trends). Real player (Keenan
    # Allen, a real WR in this real 2025 Week 10 pool), synthetic pbp
    # -- same "minimal synthetic pbp, real player/game_id/week" pattern
    # test_shelves.py's own WR target-share test already uses, kept
    # network-free/deterministic rather than depending on a live
    # nfl_data_py pull inside a test.
    # ============================================================
    wr_survivors = capped[(~capped["capped"]) & (capped["home_shelf"] == "WR Trends")]
    real_wr_pid = wr_survivors.iloc[0]["player_id"]
    real_wr = sub[sub["player_id"] == real_wr_pid].iloc[0]
    wr_pid, wr_team, wr_week10_game = real_wr["player_id"], real_wr["posteam"], real_wr["game_id"]
    wr_week9_game = f"2025_09_{wr_team}_OPP"

    def pass_play(game_id, week, posteam, receiver):
        return {
            "game_id": game_id, "season": 2025, "week": week, "posteam": posteam,
            "pass_attempt": 1, "rush_attempt": 0,
            "receiver_player_id": receiver, "receiver_player_name": receiver,
            "rusher_player_id": None, "rusher_player_name": None,
            "pass_touchdown": 0, "rush_touchdown": 0, "yardline_100": 50,
        }
    synthetic_pbp = pd.DataFrame([
        pass_play(wr_week9_game, 9, wr_team, wr_pid), pass_play(wr_week9_game, 9, wr_team, wr_pid),
        pass_play(wr_week9_game, 9, wr_team, wr_pid), pass_play(wr_week9_game, 9, wr_team, "DECOY1"),
        pass_play(wr_week10_game, 10, wr_team, wr_pid),
        pass_play(wr_week10_game, 10, wr_team, "DECOY1"), pass_play(wr_week10_game, 10, wr_team, "DECOY2"),
        pass_play(wr_week10_game, 10, wr_team, "DECOY3"),
    ])

    without_pbp = shape_content_draft_rows(capped, tasty_six, 2025, 10, weekly=sub)
    with_pbp = shape_content_draft_rows(capped, tasty_six, 2025, 10, weekly=sub, pbp=synthetic_pbp)

    wr_row_without = next((r for r in without_pbp if r["player_id"] == wr_pid), None)
    wr_row_with = next((r for r in with_pbp if r["player_id"] == wr_pid), None)

    results.append(check(
        f"real WR ({real_wr['player_name']}) IS home-assigned to a shelf that reaches shape_content_draft_rows "
        "(sanity check the rest of this block's assertions are actually exercised, not vacuously true)",
        wr_row_without is not None and wr_row_with is not None,
    ))
    if wr_row_without is not None and wr_row_with is not None:
        labels_without = {s["label"] for s in wr_row_without["role_signals"]}
        labels_with = {s["label"] for s in wr_row_with["role_signals"]}
        results.append(check(
            "WITHOUT pbp: target_share/target_share_trend are NOT in role_signals via the live write path "
            "(confirms the pre-fix gap was real, not already silently working)",
            "Target Share" not in labels_without and "Target Share Trend" not in labels_without,
        ))
        results.append(check(
            "WITH pbp threaded through shape_content_draft_rows: Target Share now appears in this real WR's "
            "role_signals via the live write path (_story_for_row), matching what build_wr_trends already had",
            "Target Share" in labels_with,
        ))
        results.append(check(
            "WITH pbp: Target Share Trend also appears (both candidates from add_whole_game_target_share_trend "
            "become eligible together, not just one)",
            "Target Share Trend" in labels_with,
        ))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
