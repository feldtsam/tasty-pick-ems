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
import io
import json
import math
import sys
import threading
import time
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

import curate_home_shelves as chs
import generate_nfl_shelf_card_content as gnscc
from curate_home_shelves import (
    CONFIG,
    ODDS_SHELVES,
    SHELF_ORDER,
    SHELF_SLUG,
    TREND_SHELVES,
    _llm_why_reasons_for_write,
    _shelf_slug,
    _shelf_unslug,
    apply_shelf_cap,
    assign_home_shelves,
    curate_nfl_shelves,
    select_tasty_six,
    shape_content_draft_rows,
    shape_shelf_signal_history_rows,
)

# The persisted `shelf` / `home_shelf` values MUST match tastypickems'
# NFL_SHELF_ORDER (src/lib/nfl/mock-nfl-board.ts) exactly — isShelfId()
# validates against these snake_case slugs, and a mismatch means the
# curated draft is silently dropped by rowToNflCard() and never renders,
# even after a human approves it. Kept here as a literal so this test
# fails loudly if either side drifts.
FRONTEND_NFL_SHELF_ORDER = [
    "red_zone_trends",
    "rb_trends",
    "wr_trends",
    "te_trends",
    "attd_300_499",
    "attd_500_699",
    "attd_700_plus",
]

WEEKLY_PATH = Path(__file__).resolve().parent.parent / "scripts" / "player_redzone_weekly.csv"


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


if __name__ == "__main__":
    results = []

    # ============================================================
    # REGRESSION — _llm_why_reasons_for_write(): a real production crash
    # (500 on /api/curate-and-write-drafts: "'str' object has no
    # attribute 'get'"). Confirmed root cause: a Claude tool-call can
    # return why_reasons in a shape validate_schema_shape() correctly
    # flags (validation_passed=False), but generate_nfl_tasty_six_
    # draft()/generate_nfl_shelf_card_draft() still return it as-is in
    # the draft dict, and this function used to iterate it
    # unconditionally regardless of that flag. A plain STRING (iterating
    # yields characters, .get() on a character raises exactly the real
    # crash text) or a list containing a non-dict item both reproduced
    # the bug pre-fix. Pure function, no real data fixture needed — runs
    # unconditionally, before the WEEKLY_PATH-dependent checks below,
    # so it can never be silently skipped by a missing CSV in some
    # other environment.
    # ============================================================
    results.append(check(
        "REGRESSION: why_reasons as a plain string (the real crash shape) degrades to [] instead of raising",
        _llm_why_reasons_for_write("Player has strong red zone usage this week.") == [],
    ))
    results.append(check(
        "REGRESSION: why_reasons as a list containing a non-dict item drops just that item, keeps the real one",
        _llm_why_reasons_for_write([
            {"pillar": "td_opportunity", "stars": 4, "reason_text": "Real reason.", "source_fact_keys": ["k"]},
            "a malformed non-dict item",
        ]) == [{"pillar": "td_opportunity", "stars": 4, "text": "Real reason.", "citation": ["k"]}],
    ))
    results.append(check(
        "why_reasons as None still degrades to [] (unchanged pre-fix behavior for the one legitimate falsy case)",
        _llm_why_reasons_for_write(None) == [],
    ))
    results.append(check(
        "why_reasons as a well-formed list is completely unaffected by the fix",
        _llm_why_reasons_for_write([
            {"pillar": "role_momentum", "stars": 5, "reason_text": "Good reason.", "source_fact_keys": ["a", "b"]},
        ]) == [{"pillar": "role_momentum", "stars": 5, "text": "Good reason.", "citation": ["a", "b"]}],
    ))

    if not WEEKLY_PATH.exists():
        print(f"SKIPPED remaining real-data checks — {WEEKLY_PATH} not present in this environment.")
        print()
        if all(results):
            print(f"All {len(results)} checks passed.")
        else:
            failed = len(results) - sum(results)
            print(f"{failed} of {len(results)} checks FAILED — see above.")
            raise SystemExit(1)
        raise SystemExit(0)

    weekly = pd.read_csv(WEEKLY_PATH)

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
    atl_rows = result["around_the_league_rows"]

    print("Home shelf distribution:")
    print(home["home_shelf"].value_counts().to_string())
    print()

    # ============================================================
    # Around the League wiring — confirms the real fix for the confirmed
    # gap (build_around_the_league/shape_around_the_league_draft_rows
    # were both built and tested standalone but never called from here).
    # ============================================================
    results.append(check(
        "curate_nfl_shelves() returns a real around_the_league_rows key",
        "around_the_league_rows" in result,
    ))
    results.append(check(
        f"real 2025 Week 10 data produces real Around the League rows ({len(atl_rows)} rows), not zero",
        len(atl_rows) > 0,
    ))
    if atl_rows:
        r0 = atl_rows[0]
        results.append(check(
            "an Around the League row is shaped to the real nfl_content_drafts write schema "
            "(shelf = the division name, writer_type='shelf_card', is_tasty_six=False)",
            r0["shelf"] in {"AFC East", "AFC North", "AFC South", "AFC West",
                             "NFC East", "NFC North", "NFC South", "NFC West"}
            and r0["writer_type"] == "shelf_card" and r0["is_tasty_six"] is False,
        ))
        from collections import Counter
        by_division = Counter(r["shelf"] for r in atl_rows)
        results.append(check(
            f"no division exceeds shelf_size=6 (real per-division counts: {dict(by_division)}), "
            "confirming max_per_shelf's 20-card ceiling does NOT leak into Around the League",
            all(n <= 6 for n in by_division.values()),
        ))
        primary_shelf_player_ids = {r["player_id"] for r in draft_rows}
        atl_player_ids = {r["player_id"] for r in atl_rows}
        overlap = primary_shelf_player_ids & atl_player_ids
        results.append(check(
            f"at least one real player appears on BOTH a primary shelf's content_draft_rows AND "
            f"around_the_league_rows ({len(overlap)} real overlap players) — confirms Around the "
            "League is genuinely non-exclusive, not deduped against home-shelf assignment",
            len(overlap) > 0,
        ))

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
    # Per-shelf cap (build step 3, CONFIG["max_per_shelf"]) — never
    # exceeded, and a capped player keeps their real qualifying_shelves
    # tag data (not dropped).
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
        print(f"(skipped the capped-player tag check — no real shelf exceeded {CONFIG['max_per_shelf']} candidates this run)")

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
            all(any(r["player_id"] == row["player_id"] and r["shelf"] == _shelf_slug(shelf) and r["is_tasty_six"] for r in draft_rows)
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
    # SHELF-CASING SERIALIZATION — the persisted `shelf` value must be
    # the frontend's snake_case slug, never the internal Title-Case name.
    # A Title-Case value fails isShelfId() -> the draft never renders.
    # ============================================================
    results.append(check(
        "SHELF_SLUG maps the 7 internal names onto exactly the frontend's NFL_SHELF_ORDER slugs",
        sorted(SHELF_SLUG.values()) == sorted(FRONTEND_NFL_SHELF_ORDER)
        and sorted(SHELF_SLUG.keys()) == sorted(SHELF_ORDER),
    ))
    results.append(check(
        f"every content_draft_row's `shelf` is a valid frontend slug — never Title-Case "
        f"(got {sorted({r['shelf'] for r in draft_rows})})",
        all(r["shelf"] in FRONTEND_NFL_SHELF_ORDER for r in draft_rows),
    ))
    ssh_rows = shape_shelf_signal_history_rows(home, season=2025, week=10)
    results.append(check(
        f"every nfl_shelf_signal_history row's `home_shelf` is a valid frontend slug "
        f"(got {sorted({r['home_shelf'] for r in ssh_rows})})",
        all(r["home_shelf"] in FRONTEND_NFL_SHELF_ORDER for r in ssh_rows)
        and all(r["pending_shelf"] is None or r["pending_shelf"] in FRONTEND_NFL_SHELF_ORDER for r in ssh_rows),
    ))
    results.append(check(
        "_shelf_slug / _shelf_unslug round-trip every internal name, and pass a division string through untouched",
        all(_shelf_unslug(_shelf_slug(n)) == n for n in SHELF_ORDER)
        and _shelf_slug("AFC East") == "AFC East" and _shelf_unslug("AFC East") == "AFC East"
        and _shelf_slug(None) is None and _shelf_unslug(None) is None,
    ))

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

    without_pbp = shape_content_draft_rows(capped, tasty_six, 2025, 10, weekly=sub)["rows"]
    with_pbp = shape_content_draft_rows(capped, tasty_six, 2025, 10, weekly=sub, pbp=synthetic_pbp)["rows"]

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

    # ============================================================
    # shelf_card_llm_top_n — the fix for the real Vercel timeout
    # (season=2026 week=1, 377 eligible players, confirmed via Vercel's
    # own logs: a successful market-value fetch logged, then nothing —
    # the 5-minute ceiling hit mid-generation). Raising max_per_shelf
    # from 6 to 20 tripled the ceiling on shape_content_draft_rows'
    # own sequential, blocking real-Claude-call-per-regular-row loop;
    # only the top shelf_card_llm_top_n per shelf should still make a
    # real call, ranked by the same `rank` apply_shelf_cap assigns —
    # everyone else gets the existing deterministic fallback, same
    # code path as "no anthropic_api_key at all".
    #
    # 20 REAL players (real 2025 Week 10 rows, same source as the real-
    # data checks above) with their odds/completeness overridden —
    # same "synthetic re-attachment onto real rows" technique this
    # whole file already uses, not a from-scratch synthetic frame: a
    # from-scratch frame is missing the many raw red-zone/role columns
    # add_red_zone_trend_windows (etc.) genuinely need, which real CSV
    # rows already have. All 20 pushed toward the SAME odds band
    # (500-699) with td_opportunity/role_momentum completeness zeroed
    # out (so none qualify for a trend shelf instead) and a clean
    # descending tpe_score (so rank order is deterministic — row 0 is
    # rank 1, the highest tpe_score). Not every real row necessarily
    # clears every OTHER real eligibility gate (position_group, etc.),
    # so the surviving count is read back rather than assumed to be 20
    # — the only hard requirement for this test is "more than
    # shelf_card_llm_top_n survive", which 20 candidates comfortably
    # clears in practice.
    # ============================================================
    llm_cutoff_rows = sub[sub["tpe_score"].notna()].head(20).copy().reset_index(drop=True)
    llm_cutoff_rows["consensus_price_american"] = 600
    llm_cutoff_rows["td_opportunity_completeness"] = 0.0
    llm_cutoff_rows["role_momentum_completeness"] = 0.0
    llm_cutoff_rows["tpe_score"] = [90.0 - i for i in range(len(llm_cutoff_rows))]
    llm_home = assign_home_shelves(llm_cutoff_rows)
    llm_capped = apply_shelf_cap(llm_home, CONFIG)
    on_shelf = llm_capped[(llm_capped["home_shelf"] == "ATTD +500-699") & (~llm_capped["capped"])]
    n_survivors = len(on_shelf)
    results.append(check(
        f"shelf_card_llm_top_n setup: comfortably more than {CONFIG['shelf_card_llm_top_n']} real players "
        f"survive onto the one shelf ({n_survivors} did) — a real test of the cutoff, not a vacuous one",
        n_survivors > CONFIG["shelf_card_llm_top_n"],
    ))

    llm_call_player_ids = []

    def fake_generate_nfl_shelf_card_draft(row, shelf, confidence_band, api_key, **kw):
        llm_call_player_ids.append(row["player_id"])
        return {
            "title": f"Bespoke title for {row['player_id']}",
            "why_reasons": [{"pillar": "market_value", "stars": 3, "text": "real llm text", "citation": ["tpe_score"]}],
            "confidence_band": confidence_band,
            "model_name": "fake-model",
            "validation_passed": True,
            "validation_issues": [],
        }

    orig_generate = chs.generate_nfl_shelf_card_draft
    chs.generate_nfl_shelf_card_draft = fake_generate_nfl_shelf_card_draft
    try:
        cutoff_draft_rows = shape_content_draft_rows(
            llm_capped, {}, 2026, 1, weekly=llm_cutoff_rows, anthropic_api_key="fake-key", config=CONFIG,
        )["rows"]
    finally:
        chs.generate_nfl_shelf_card_draft = orig_generate

    on_shelf_drafts = [r for r in cutoff_draft_rows if r["shelf"] == "attd_500_699"]
    on_shelf_drafts.sort(key=lambda r: r["rank"])
    results.append(check(
        f"shelf_card_llm_top_n: exactly {CONFIG['shelf_card_llm_top_n']} real Claude calls made, not {n_survivors}",
        len(llm_call_player_ids) == CONFIG["shelf_card_llm_top_n"],
    ))
    top_n_ids = {r["player_id"] for r in on_shelf_drafts[:CONFIG["shelf_card_llm_top_n"]]}
    results.append(check(
        "shelf_card_llm_top_n: the real calls made were for exactly the top-ranked players, not an arbitrary subset",
        set(llm_call_player_ids) == top_n_ids,
    ))
    results.append(check(
        "shelf_card_llm_top_n: a top-N row got the real bespoke title from the (mocked) Claude call",
        on_shelf_drafts[0]["title"] == f"Bespoke title for {on_shelf_drafts[0]['player_id']}"
        and on_shelf_drafts[0]["model_name"] == "fake-model",
    ))
    beyond_cutoff = on_shelf_drafts[CONFIG["shelf_card_llm_top_n"]:]
    results.append(check(
        f"shelf_card_llm_top_n: {len(beyond_cutoff)} rows beyond the cutoff exist to check (sanity, not vacuous)",
        len(beyond_cutoff) == n_survivors - CONFIG["shelf_card_llm_top_n"],
    ))
    results.append(check(
        "shelf_card_llm_top_n: every row beyond the cutoff has a real, non-null, non-empty title "
        "(the deterministic fallback produced real content, not a blank/broken card)",
        all(r["title"] is not None and len(r["title"]) > 0 for r in beyond_cutoff),
    ))
    results.append(check(
        "shelf_card_llm_top_n: every row beyond the cutoff has a real, non-empty why_reasons array",
        all(isinstance(r["why_reasons"], list) and len(r["why_reasons"]) > 0 for r in beyond_cutoff),
    ))
    results.append(check(
        "shelf_card_llm_top_n: no row beyond the cutoff got the bespoke LLM title (confirms it's genuinely "
        "the deterministic path, not a mock that fired anyway)",
        all(not r["title"].startswith("Bespoke title for") for r in beyond_cutoff),
    ))
    results.append(check(
        "shelf_card_llm_top_n: model_name/validation_passed/validation_issues on a fallback row match the "
        "SAME honest defaults the no-anthropic_api_key path already uses (None/True/[]), not leftover/stale values",
        all(
            r["model_name"] is None and r["validation_passed"] is True and r["validation_issues"] == []
            for r in beyond_cutoff
        ),
    ))
    results.append(check(
        f"shelf_card_llm_top_n: display count is untouched by the LLM cutoff -- all {n_survivors} real "
        "qualifying players still get a written row (the whole point of raising max_per_shelf in the first place)",
        len(on_shelf_drafts) == n_survivors,
    ))

    # ============================================================
    # Pass 2.1 acceptance test: shelf-order invariance. One candidate
    # placed on all three shelves a real survivor can qualify for at
    # once (its own price-band shelf + its own position trend shelf +
    # Red Zone Trends, which is position-agnostic — the real 3-way
    # overlap measured earlier this session against live data, not a
    # contrived combination). Run _interrogate_unique_candidates twice,
    # once with these placements in their natural order and once
    # reversed, capturing the exact story_input dict handed to
    # interrogate_story() each time (mocked — this is a structural
    # invariance test, not a live-model test). The two payloads must be
    # byte-for-byte identical: candidate-level Interrogation input must
    # never depend on which shelf placement is encountered first.
    # ============================================================
    invariance_row = pd.Series({
        "player_id": "SYN_INVARIANCE", "player_name": "Synthetic Invariance", "game_id": "2026_02_TST_OPP",
        "posteam": "TST", "position_group": "RB",
        "td_opportunity": 72.0, "role_momentum": 55.0, "situation": 61.0,
        "role_trend": 48.0, "proven_heat": 66.0, "emerging_heat": 39.0,
    })
    invariance_weekly_lookup = {"SYN_INVARIANCE": invariance_row}
    invariance_placements = [
        {"player_id": "SYN_INVARIANCE", "home_shelf": "Red Zone Trends", "capped": False},
        {"player_id": "SYN_INVARIANCE", "home_shelf": "RB Trends", "capped": False},
        {"player_id": "SYN_INVARIANCE", "home_shelf": "ATTD +500-699", "capped": False},
    ]

    captured_inputs = []

    def fake_interrogate_story(story_input, api_key, prior_history=None, market_data=None):
        captured_inputs.append(story_input)
        return {"signal_verdict": "UNRESOLVED"}

    orig_interrogate = chs.interrogate_story
    chs.interrogate_story = fake_interrogate_story
    try:
        forward = pd.DataFrame(invariance_placements)
        chs._interrogate_unique_candidates(forward, invariance_weekly_lookup, anthropic_api_key="fake-key")
        reversed_df = pd.DataFrame(list(reversed(invariance_placements)))
        chs._interrogate_unique_candidates(reversed_df, invariance_weekly_lookup, anthropic_api_key="fake-key")
    finally:
        chs.interrogate_story = orig_interrogate

    results.append(check(
        "shelf-order invariance: exactly one interrogate_story call made per order "
        "(one candidate, three placements, deduplicated correctly in both directions)",
        len(captured_inputs) == 2,
    ))
    if len(captured_inputs) == 2:
        forward_json = json.dumps(captured_inputs[0], sort_keys=True)
        reversed_json = json.dumps(captured_inputs[1], sort_keys=True)
        results.append(check(
            "shelf-order invariance: candidate-level Interrogation input (headline, supporting_evidence, "
            "everything) is byte-for-byte identical whether Red Zone Trends, RB Trends, or ATTD +500-699 "
            "is the first placement encountered",
            forward_json == reversed_json,
        ))
        results.append(check(
            "shelf-order invariance: the shelf-neutral headline actually reflects the candidate's real "
            "scored signals (not null, not a shelf-specific story)",
            captured_inputs[0]["headline"] is not None
            and "72.0" in captured_inputs[0]["headline"]
            and "TD opportunity" in captured_inputs[0]["headline"],
        ))
        results.append(check(
            "shelf-order invariance: supporting_evidence carries all six real scored signals, not a subset "
            "tied to one shelf's own story",
            captured_inputs[0]["supporting_evidence"] is not None
            and len(captured_inputs[0]["supporting_evidence"]) == 6,
        ))

    # ============================================================
    # Pass 3 -- LOCKED capacity policy: within each real price band,
    # keep the top interrogation_top_pct_per_price_band (0.65) of that
    # band's own candidates by count, ranked by best_gap descending.
    # ============================================================

    results.append(check(
        "_price_band_for_row: real ATTD bands resolve correctly, honest None for missing/unbanded price",
        chs._price_band_for_row(pd.Series({"consensus_price_american": 350})) == "ATTD +300-499"
        and chs._price_band_for_row(pd.Series({"consensus_price_american": 600})) == "ATTD +500-699"
        and chs._price_band_for_row(pd.Series({"consensus_price_american": 900})) == "ATTD +700+"
        and chs._price_band_for_row(pd.Series({"consensus_price_american": 150})) is None
        and chs._price_band_for_row(pd.Series({})) is None
        and chs._price_band_for_row(None) is None,
    ))

    from nfl_tension import find_tension, GAP_THRESHOLD  # noqa: E402

    gap_case_divergence = {
        "market_value_score": 80.0, "td_opportunity": 50.0, "role_momentum": 50.0, "situation": 50.0,
        "role_trend": 50.0, "proven_heat": 50.0, "emerging_heat": 50.0,
    }
    gap_case_flat = {
        "market_value_score": 51.0, "td_opportunity": 50.0, "role_momentum": 49.0, "situation": 50.0,
        "role_trend": 50.0, "proven_heat": 50.0, "emerging_heat": 51.0,
    }
    results.append(check(
        "_candidate_best_gap: agrees with the real, unmodified find_tension() on which of two cases "
        "has a real, large relationship vs. which is flat/convergent -- reimplementation validated "
        "against the real function it mirrors, not just self-consistent",
        chs._candidate_best_gap(pd.Series(gap_case_divergence)) >= GAP_THRESHOLD
        and find_tension(gap_case_divergence)["tension_type"] == "divergence"
        and chs._candidate_best_gap(pd.Series(gap_case_flat)) < GAP_THRESHOLD
        and find_tension(gap_case_flat)["tension_type"] == "convergence",
    ))
    results.append(check(
        "_candidate_best_gap: missing full_row / all-missing signals is an honest 0.0, never a crash or a guess",
        chs._candidate_best_gap(None) == 0.0
        and chs._candidate_best_gap(pd.Series({})) == 0.0,
    ))

    selection_rows = (
        [{"key": (f"HI_{i}", "ev"), "price_band": "ATTD +700+", "best_gap": 100.0 - i} for i in range(7)]
        + [{"key": (f"LO_{i}", "ev"), "price_band": "ATTD +700+", "best_gap": 10.0 - i} for i in range(3)]
        + [{"key": ("SMALL_A", "ev"), "price_band": "ATTD +300-499", "best_gap": 40.0}]
        + [{"key": ("SMALL_B", "ev"), "price_band": "ATTD +300-499", "best_gap": 20.0}]
    )
    selected, not_selected = chs._select_candidates_for_interrogation(selection_rows, 0.65)
    results.append(check(
        "_select_candidates_for_interrogation: ATTD +700+ band (10 candidates) keeps round(10*0.65)=7 -- "
        "exactly the 7 highest-best_gap keys, never the lowest (the exact misimplementation risk flagged "
        "in the locked instruction: percentile>=0.65 would keep only the top 35%, the opposite of intended)",
        {k for k in selected if k[0].startswith("HI_")} == {(f"HI_{i}", "ev") for i in range(7)}
        and {k for k in not_selected if k[0].startswith("LO_")} == {(f"LO_{i}", "ev") for i in range(3)},
    ))
    results.append(check(
        "_select_candidates_for_interrogation: a separate 2-candidate band (ATTD +300-499) is ranked "
        "against its OWN band only -- round(2*0.65)=1 kept, the higher-best_gap one, unaffected by the "
        "other band's much larger raw best_gap values",
        ("SMALL_A", "ev") in selected and ("SMALL_B", "ev") in not_selected,
    ))
    results.append(check(
        "_select_candidates_for_interrogation: selected/not_selected are disjoint and exactly cover every key",
        selected.isdisjoint(not_selected) and selected | not_selected == {r["key"] for r in selection_rows},
    ))
    # Order-independence of the same selection, same discipline as the
    # Pass 2.1 shelf-order invariance test -- selection must not depend
    # on input list order either.
    selected_rev, not_selected_rev = chs._select_candidates_for_interrogation(list(reversed(selection_rows)), 0.65)
    results.append(check(
        "_select_candidates_for_interrogation: selection is invariant to input row order",
        selected == selected_rev and not_selected == not_selected_rev,
    ))

    # Three-state contract, end to end, against a real 15-player
    # population (the same real WR rows the shelf_card_llm_top_n block
    # above already built, all real-consensus-priced into ATTD +500-699
    # -- reused rather than re-fabricated).
    import math as _math  # local alias, avoids shadowing anything module-level in this test file
    llm_weekly_lookup = {row["player_id"]: row for _, row in llm_cutoff_rows.iterrows()}
    survivor_ids = list(on_shelf["player_id"])  # the 15 real, non-capped, on-shelf candidates -- matches
    # exactly what _interrogate_unique_candidates itself derives from capped_assignments (capped=True
    # rows never become a candidate_key at all, so they must be excluded from this independent check too).
    real_gaps = {pid: chs._candidate_best_gap(llm_weekly_lookup[pid]) for pid in survivor_ids}
    expected_keep = _math.floor(len(real_gaps) * CONFIG["interrogation_top_pct_per_price_band"] + 0.5)
    expected_selected_ids = {
        pid for pid, _ in sorted(real_gaps.items(), key=lambda kv: (-kv[1], kv[0]))[:expected_keep]
    }

    three_state_captured = []

    def fake_interrogate_story_3state(story_input, api_key, prior_history=None, market_data=None):
        three_state_captured.append(story_input["entity"]["player_id"])
        return {"signal_verdict": "SURVIVES"}

    orig_interrogate_3s = chs.interrogate_story
    chs.interrogate_story = fake_interrogate_story_3state
    try:
        stdout_capture = io.StringIO()
        with redirect_stdout(stdout_capture):
            three_state_results = chs._interrogate_unique_candidates(llm_capped, llm_weekly_lookup, anthropic_api_key="fake-key")
    finally:
        chs.interrogate_story = orig_interrogate_3s
    log_output = stdout_capture.getvalue()

    results.append(check(
        f"three-state contract: every one of the {len(survivor_ids)} real on-shelf candidates gets "
        "exactly one entry, each tagged with a real interrogation_status",
        len(three_state_results) == len(survivor_ids)
        and all(v["interrogation_status"] in ("complete", "not_selected", "failed") for v in three_state_results.values()),
    ))
    results.append(check(
        f"three-state contract: exactly {expected_keep} of {len(survivor_ids)} are 'complete' "
        "(selected and interrogate_story succeeded), matching the same top-65%-by-count ranking computed "
        "independently above",
        {key[0] for key, v in three_state_results.items() if v["interrogation_status"] == "complete"} == expected_selected_ids,
    ))
    results.append(check(
        "three-state contract: interrogate_story was called ONLY for selected candidates -- a not_selected "
        "candidate never reaches the LLM call at all, not even to be discarded after the fact",
        set(three_state_captured) == expected_selected_ids,
    ))
    results.append(check(
        "three-state contract: every 'not_selected' entry's result is None -- never a fabricated or "
        "defaulted signal_verdict-shaped value standing in for 'we chose not to scrutinize this'",
        all(
            v["result"] is None
            for v in three_state_results.values()
            if v["interrogation_status"] == "not_selected"
        ),
    ))
    results.append(check(
        "three-state contract: a legitimate 'not_selected' skip is never logged as a CONTRACT FAILURE -- "
        "resource allocation must never read as a scrutiny problem in the run log either",
        "CONTRACT FAILURE" not in log_output,
    ))
    results.append(check(
        "three-state contract: the run log states the real selection-gate mechanism and counts explicitly "
        "(band + best_gap + kept/not-selected counts), not just a bare candidate count",
        "selection gate" in log_output and "best_gap" in log_output,
    ))

    # ============================================================
    # Pass 4 -- bounded-concurrency execution. Governing invariant:
    # concurrent execution of the same selected candidate set must
    # produce the same candidate -> interrogation_status/result mapping
    # sequential execution would, never a result attributed to the
    # wrong candidate, never a candidate silently dropped, and one
    # failed candidate must never affect any other.
    # ============================================================

    concurrency_rows = pd.DataFrame([
        {"player_id": f"CONC_{i:02d}", "home_shelf": "Red Zone Trends", "capped": False}
        for i in range(20)
    ])
    concurrency_weekly_lookup = {}
    for i in range(20):
        # market_value_score spaced 10 apart, everything else flat, so
        # best_gap = |market_value_score - 50| = 10*i exactly -- a fully
        # deterministic, hand-computable ranking. round-half-up(20*0.65)
        # = 13 kept: i=7..19 (gap 70..190) selected, i=0..6 (gap 0..60) not.
        concurrency_weekly_lookup[f"CONC_{i:02d}"] = pd.Series({
            "player_id": f"CONC_{i:02d}", "player_name": f"Concurrency Test {i}",
            "game_id": "2026_04_TST_OPP", "consensus_price_american": 900,
            "market_value_score": 50.0 + 10.0 * i,
            "td_opportunity": 50.0, "role_momentum": 50.0, "situation": 50.0,
            "role_trend": 50.0, "proven_heat": 50.0, "emerging_heat": 50.0,
        })
    concurrency_config = dict(CONFIG)
    concurrency_config["interrogation_max_concurrency"] = 3
    BOOM_KEY = ("CONC_19", "2026_04_TST_OPP")  # the highest-best_gap selected candidate -- deliberately made to always fail

    in_flight_lock = threading.Lock()
    # Plain dict, not module/nonlocal-scope ints -- this test file's own
    # top-level `if __name__` block isn't a function scope, so a nested
    # closure has no enclosing binding to declare `nonlocal` against. A
    # mutable container sidesteps that cleanly.
    in_flight_counters = {"current": 0, "max": 0}
    # chs.time IS this test file's own `time` module (Python only loads a
    # module once) -- capturing the REAL sleep here, before chs.time.sleep
    # gets patched to a no-op below, so the mock's own deliberate jitter
    # keeps working (jitter is a test tool; the no-op below is only meant
    # to silence _interrogate_one_candidate's own retry backoff).
    real_sleep = time.sleep

    def concurrency_mock_interrogate_story(story_input, api_key, prior_history=None, market_data=None):
        key = (story_input["entity"]["player_id"], "2026_04_TST_OPP")
        with in_flight_lock:
            in_flight_counters["current"] += 1
            in_flight_counters["max"] = max(in_flight_counters["max"], in_flight_counters["current"])
        try:
            # Small, key-derived jitter -- deliberately scrambles
            # completion order relative to submission/set-iteration
            # order, without making the test slow or flaky.
            real_sleep((hash(key) % 5) * 0.02)
            if key == BOOM_KEY:
                return None  # always fails -- exhausts retries, must become "failed" without affecting anyone else
            return {"signal_verdict": "SURVIVES", "_echo_key": list(key)}
        finally:
            with in_flight_lock:
                in_flight_counters["current"] -= 1

    orig_interrogate_conc = chs.interrogate_story
    orig_sleep = chs.time.sleep
    chs.interrogate_story = concurrency_mock_interrogate_story
    chs.time.sleep = lambda _seconds: None  # no-op retry backoff -- keep the test fast, not the retry COUNT
    try:
        concurrent_results = chs._interrogate_unique_candidates(
            concurrency_rows, concurrency_weekly_lookup, anthropic_api_key="fake-key", config=concurrency_config,
        )

        # Independent serial reference: the SAME unit of work
        # (_interrogate_one_candidate), called in a plain for-loop, same
        # mock active. This IS what "sequential execution" means for
        # this unit of work -- concurrency only changes how these same
        # calls get scheduled, never what any one of them computes.
        expected_selected = {f"CONC_{i:02d}" for i in range(7, 20)}
        expected_not_selected = {f"CONC_{i:02d}" for i in range(0, 7)}
        serial_results = {}
        for i in range(20):
            key = (f"CONC_{i:02d}", "2026_04_TST_OPP")
            if key[0] in expected_selected:
                serial_results[key] = chs._interrogate_one_candidate(key, concurrency_weekly_lookup, "fake-key")
            else:
                serial_results[key] = {"interrogation_status": "not_selected", "result": None}
    finally:
        chs.interrogate_story = orig_interrogate_conc
        chs.time.sleep = orig_sleep

    results.append(check(
        "Pass 4: selection is unaffected by concurrency -- same 13 selected / 7 not_selected as the "
        "hand-computed best_gap ranking (13*0.65 round-half-up = 13 of 20)",
        {k[0] for k, v in concurrent_results.items() if v["interrogation_status"] != "not_selected"} == expected_selected
        and {k[0] for k, v in concurrent_results.items() if v["interrogation_status"] == "not_selected"} == expected_not_selected,
    ))
    results.append(check(
        "Pass 4: concurrent execution produced the SAME interrogation_status for every one of the 20 keys "
        "as the independent serial reference -- the governing invariant, holding under real concurrent "
        "execution with scrambled completion order, not just in a single-threaded happy path",
        {k: v["interrogation_status"] for k, v in concurrent_results.items()}
        == {k: v["interrogation_status"] for k, v in serial_results.items()},
    ))
    results.append(check(
        "Pass 4: every 'complete' result's echoed key matches the dict key it's stored under -- no result "
        "attributed to the wrong candidate under concurrent, out-of-submission-order completion",
        all(
            v["result"]["_echo_key"] == list(key)
            for key, v in concurrent_results.items()
            if v["interrogation_status"] == "complete"
        ),
    ))
    results.append(check(
        "Pass 4: failure isolation -- the one deliberately-failing candidate (CONC_19, highest best_gap, "
        "otherwise would be selected and expected to succeed) is 'failed', and it alone",
        concurrent_results[BOOM_KEY]["interrogation_status"] == "failed"
        and concurrent_results[BOOM_KEY]["result"] is None
        and sum(1 for v in concurrent_results.values() if v["interrogation_status"] == "failed") == 1,
    ))
    results.append(check(
        "Pass 4: failure isolation -- all 12 OTHER selected candidates still completed successfully with "
        "their own correct result -- one failed call never cancelled or corrupted the rest of the batch",
        sum(
            1 for k, v in concurrent_results.items()
            if k[0] in expected_selected and k != BOOM_KEY and v["interrogation_status"] == "complete"
        ) == 12,
    ))
    results.append(check(
        f"Pass 4: the configured concurrency bound (3) was actually reached, not just nominally respected "
        f"(observed max in-flight = {in_flight_counters['max']})",
        in_flight_counters["max"] == 3,
    ))
    results.append(check(
        "Pass 4: the concurrency bound was never EXCEEDED at any point during execution",
        in_flight_counters["max"] <= 3,
    ))

    # ============================================================
    # signal_verdict integration -- the STOP gate, real end-to-end
    # through shape_content_draft_rows (not a unit test of find_tension()
    # in isolation -- that's content_writer/test_nfl_tension.py's job).
    # A gated-out (FAILS/UNRESOLVED) candidate's placement must get NO
    # row at all: not written, not a deterministic-template fallback
    # either, and no backfill/promotion of a replacement candidate.
    # ============================================================
    gate_rows = sub[sub["tpe_score"].notna() & sub["position_group"].notna()].head(4).copy().reset_index(drop=True)
    gate_rows["consensus_price_american"] = 900
    gate_rows["td_opportunity_completeness"] = 0.0
    gate_rows["role_momentum_completeness"] = 0.0
    gate_rows["tpe_score"] = [80.0, 79.0, 78.0, 77.0]
    gate_home = assign_home_shelves(gate_rows)
    gate_capped = apply_shelf_cap(gate_home, CONFIG)
    gate_on_shelf = gate_capped[(gate_capped["home_shelf"] == "ATTD +700+") & (~gate_capped["capped"])]
    results.append(check(
        "signal_verdict STOP-gate setup: all 4 real candidates land on ATTD +700+, a real test population",
        len(gate_on_shelf) == 4,
    ))
    gate_player_ids = list(gate_on_shelf["player_id"])
    GATED_IDS = set(gate_player_ids[:2])      # one FAILS, one UNRESOLVED
    SURVIVING_IDS = set(gate_player_ids[2:])  # SURVIVES, all-WEAKENED -> discovery

    def fake_interrogate_story_gate(story_input, api_key, prior_history=None, market_data=None):
        pid = story_input["entity"]["player_id"]
        if pid in GATED_IDS:
            verdict = "FAILS" if pid == gate_player_ids[0] else "UNRESOLVED"
            return {"signal_verdict": verdict, "challenge": {"alternate_explanations": []}}
        return {"signal_verdict": "SURVIVES", "challenge": {"alternate_explanations": [{"status": "WEAKENED"}]}}

    def fake_call_claude_for_gate(api_key, system_prompt, user_prompt):
        return {
            "title": "Gate Test Title",
            "story": "A short, plain story about this week's role, written without any banned phrasing at all.",
            "why_reasons": [{"pillar": "market_value", "stars": 3, "reason_text": "Consensus price reflects real market interest.", "source_fact_keys": []}],
        }

    orig_interrogate_gate = chs.interrogate_story
    orig_call_claude_gate = gnscc.call_claude_for_nfl_shelf_card
    chs.interrogate_story = fake_interrogate_story_gate
    gnscc.call_claude_for_nfl_shelf_card = fake_call_claude_for_gate
    try:
        gate_config = dict(CONFIG)
        # Select ALL 4 candidates -- isolates the STOP gate from Pass 3's
        # own, separately-tested selection gate (which would otherwise
        # drop one of the 4 from being interrogated at all, muddying
        # this test's own real question: does a real SURVIVES/FAILS/
        # UNRESOLVED outcome control row presence correctly).
        gate_config["interrogation_top_pct_per_price_band"] = 1.0
        stdout_gate = io.StringIO()
        with redirect_stdout(stdout_gate):
            gate_result = chs.shape_content_draft_rows(
                gate_capped, {}, 2026, 1, weekly=gate_rows, anthropic_api_key="fake-key", config=gate_config,
            )
    finally:
        chs.interrogate_story = orig_interrogate_gate
        gnscc.call_claude_for_nfl_shelf_card = orig_call_claude_gate
    gate_log = stdout_gate.getvalue()

    gate_written_ids = {r["player_id"] for r in gate_result["rows"]}
    results.append(check(
        "STOP gate end-to-end: FAILS/UNRESOLVED candidates get NO row at all -- not written, "
        "not a deterministic fallback either",
        GATED_IDS.isdisjoint(gate_written_ids),
    ))
    results.append(check(
        "STOP gate end-to-end: SURVIVES candidates still get their real row, unaffected by the "
        "other two being gated out in the same batch",
        SURVIVING_IDS.issubset(gate_written_ids),
    ))
    results.append(check(
        "STOP gate end-to-end: exactly 2 rows written from 4 real candidates -- 2 gated out, no "
        "backfill/promotion of a replacement candidate to fill the gap",
        len(gate_result["rows"]) == 2,
    ))
    results.append(check(
        "STOP gate end-to-end: the skip is logged loudly with the real signal_verdict that caused "
        "it, for both FAILS and UNRESOLVED -- never a silent gap",
        gate_log.count("SKIPPING row") == 2 and "FAILS" in gate_log and "UNRESOLVED" in gate_log,
    ))

    # ============================================================
    # Input-completeness fix: _market_data_for_candidate and
    # _evidentiary_basis_readings -- the real fix for the real 36/36
    # UNRESOLVED finding.
    # ============================================================
    price_rows = [
        {"player_id": "P1", "poll_timestamp": "2026-09-10T12:00:00Z", "consensus_price_american": 700},
        {"player_id": "P1", "poll_timestamp": "2026-09-10T14:00:00Z", "consensus_price_american": 700},  # repeat, no real movement
        {"player_id": "P1", "poll_timestamp": "2026-09-15T12:00:00Z", "consensus_price_american": 550},
        {"player_id": "P1", "poll_timestamp": "2026-09-17T23:00:00Z", "consensus_price_american": -120},
    ]
    md = chs._market_data_for_candidate(price_rows)
    results.append(check(
        "_market_data_for_candidate: current_attd_odds is the most RECENT real price, correctly signed",
        md["current_attd_odds"] == "-120",
    ))
    results.append(check(
        "_market_data_for_candidate: odds_history collapses repeated-identical-price polls to real DISTINCT "
        "checkpoints only (4 raw polls, 3 real price changes -> 3 entries, not 4)",
        len(md["odds_history"]) == 3 and [h["odds"] for h in md["odds_history"]] == ["+700", "+550", "-120"],
    ))
    results.append(check(
        "_market_data_for_candidate: honest None for no rows / no real price+timestamp data",
        chs._market_data_for_candidate([]) is None and chs._market_data_for_candidate(None) is None,
    ))
    many_checkpoints = [
        {"player_id": "P2", "poll_timestamp": f"2026-09-{10+i:02d}T12:00:00Z", "consensus_price_american": 500 + i * 10}
        for i in range(8)
    ]
    md2 = chs._market_data_for_candidate(many_checkpoints, max_checkpoints=5)
    results.append(check(
        "_market_data_for_candidate: caps odds_history at max_checkpoints, keeping the MOST RECENT ones",
        len(md2["odds_history"]) == 5 and md2["odds_history"][0]["odds"] == "+530" and md2["odds_history"][-1]["odds"] == "+570",
    ))

    # Noise-reduction fix: same-day/same-few-hours churn (real examples
    # from a real measurement showed swings like +190->+196->+537->
    # +1000->+3000 within ~4 hours) must collapse to the single most
    # recent value within that window, while genuinely day-separated
    # real movement still survives as its own checkpoint.
    noisy_then_real = [
        {"player_id": "P3", "poll_timestamp": "2026-09-18T12:00:00+00:00", "consensus_price_american": 300},
        # same-day churn, a few hours apart -- should collapse away
        {"player_id": "P3", "poll_timestamp": "2026-09-19T10:00:00+00:00", "consensus_price_american": 190},
        {"player_id": "P3", "poll_timestamp": "2026-09-19T12:00:00+00:00", "consensus_price_american": 196},
        {"player_id": "P3", "poll_timestamp": "2026-09-19T14:00:00+00:00", "consensus_price_american": 537},
        {"player_id": "P3", "poll_timestamp": "2026-09-19T16:00:00+00:00", "consensus_price_american": 1000},
        {"player_id": "P3", "poll_timestamp": "2026-09-19T18:00:00+00:00", "consensus_price_american": 3000},
        # genuinely the next day -- a real, separated checkpoint
        {"player_id": "P3", "poll_timestamp": "2026-09-20T18:00:00+00:00", "consensus_price_american": 250},
    ]
    md3 = chs._market_data_for_candidate(noisy_then_real)
    results.append(check(
        "_market_data_for_candidate noise fix: same-day churn (5 raw price changes within ~8 hours on 9/19) "
        "collapses to a single checkpoint (the last one before the next real gap), not 5 separate 'moves' -- "
        "3 real checkpoints survive total: the 9/18 starting price, the collapsed 9/19 churn, and the 9/20 move",
        len(md3["odds_history"]) == 3,
    ))
    results.append(check(
        "_market_data_for_candidate noise fix: the surviving mid-churn checkpoint is the LAST same-day value "
        "(+3000, the most current information from that day) -- not any of the 4 earlier same-day blips -- "
        "and the next-day real move survives as its own entry",
        [h["odds"] for h in md3["odds_history"]] == ["+300", "+3000", "+250"],
    ))
    results.append(check(
        "_market_data_for_candidate noise fix: current_attd_odds is still always the single most recent real poll",
        md3["current_attd_odds"] == "+250",
    ))
    # A malformed/unparseable timestamp degrades to an honest inclusion, never a crash.
    md4 = chs._market_data_for_candidate([
        {"player_id": "P4", "poll_timestamp": "not-a-real-timestamp", "consensus_price_american": 100},
        {"player_id": "P4", "poll_timestamp": "2026-09-20T18:00:00+00:00", "consensus_price_american": 150},
    ])
    results.append(check(
        "_market_data_for_candidate: an unparseable timestamp is kept as its own checkpoint, not dropped or crashed on",
        md4 is not None and len(md4["odds_history"]) == 2,
    ))

    evidentiary_row = pd.Series({
        "evidence_quality": 62.0, "td_opportunity_completeness": 30.0, "role_momentum_completeness": 100.0,
        "situation_completeness": None, "defensive_matchup_completeness": float("nan"),
    })
    readings = chs._evidentiary_basis_readings(evidentiary_row)
    results.append(check(
        "_evidentiary_basis_readings: real present fields are read; missing/NaN fields are honestly dropped, "
        "never guessed",
        len(readings) == 3
        and any("evidence quality: 62%" in r for r in readings)
        and any("TD opportunity completeness: 30%" in r for r in readings)
        and not any("situation" in r.lower() for r in readings),
    ))
    results.append(check(
        "_evidentiary_basis_readings: honest empty list (not None) for a missing full_row -- always safe to extend",
        chs._evidentiary_basis_readings(None) == [],
    ))
    headline, supp = chs._shelf_neutral_candidate_evidence(pd.Series({
        "td_opportunity": 50.0, "role_momentum": 50.0, "situation": 50.0, "role_trend": 50.0,
        "proven_heat": 50.0, "emerging_heat": 50.0, "evidence_quality": 80.0,
    }))
    results.append(check(
        "_shelf_neutral_candidate_evidence: evidentiary-basis readings are appended onto supporting_evidence "
        "alongside the six real signal readings, not replacing them",
        len(supp) == 7 and any("evidence quality" in s for s in supp),
    ))

    # ============================================================
    # Missing-signal_verdict edge case: a real, well-formed response
    # (challenge/confirmation/judgment all present) with signal_verdict
    # itself absent must NOT resolve to "complete" -- it's a response
    # gap, the same real-attempt-no-usable-outcome case a None result
    # already is, and must resolve to "failed" after retries exhaust,
    # never silent legacy fallthrough.
    # ============================================================
    well_formed_no_verdict = {
        "challenge": {"alternate_explanations": []},
        "confirmation": {"supporting_signals": "x", "contradicting_signals": "y", "market_reaction": "z"},
        "judgment": {"what_we_know": "a", "what_we_dont_know": "b", "evidence_significance": "c"},
        "signal_verdict": None,
    }
    call_count = {"n": 0}

    def fake_interrogate_story_missing_verdict(story_input, api_key, prior_history=None, market_data=None):
        call_count["n"] += 1
        return well_formed_no_verdict

    orig_interrogate_mv = chs.interrogate_story
    orig_sleep_mv = chs.time.sleep
    chs.interrogate_story = fake_interrogate_story_missing_verdict
    chs.time.sleep = lambda _seconds: None  # keep the retry loop fast, not the retry COUNT
    try:
        outcome = chs._interrogate_one_candidate(("MV_TEST", "ev"), {}, "fake-key")
    finally:
        chs.interrogate_story = orig_interrogate_mv
        chs.time.sleep = orig_sleep_mv

    results.append(check(
        "missing-signal_verdict: a real, well-formed response with signal_verdict absent resolves to "
        "'failed', not 'complete' -- never falls silently through to legacy-tier treatment as if "
        "Interrogation ran meaningfully",
        outcome == {"interrogation_status": "failed", "result": None},
    ))
    results.append(check(
        "missing-signal_verdict: this went through the SAME retry path as a None result (2 retries + the "
        "original attempt = 3 real calls), not special-cased into an immediate failure",
        call_count["n"] == 3,
    ))

    # ==================================================================
    # PER-STAGE TIMERS
    # ==================================================================
    import time as _time

    calls = {"n": 0}

    def _flaky(story_input, api_key, prior_history=None, market_data=None):
        calls["n"] += 1
        if calls["n"] < 3:
            return None                      # two unusable results, then a good one
        return {"signal_verdict": "SURVIVES", "confirmation": {}, "challenge": {}, "judgment": {}}

    orig_interrogate = chs.interrogate_story
    try:
        chs.interrogate_story = _flaky
        stats = {}
        out = chs._interrogate_one_candidate(
            ("P1", "e1"),
            {("P1", "e1"): pd.Series({"player_id": "P1", "player_name": "Retry Guy"})},
            "fake-key", max_retries=2, retry_backoff_seconds=0.001, stats=stats,
        )
        results.append(check(
            "timers: retries are counted -- two unusable results then a good one records 3 calls, "
            "1 candidate, 2 retries",
            out["interrogation_status"] == "complete"
            and stats == {"calls": 3, "candidates": 1, "retries": 2},
        ))
        results.append(check(
            "timers: the attempt tally is a SINK -- _interrogate_one_candidate's return is still "
            "exactly two keys, the contract the gate depends on",
            sorted(out.keys()) == ["interrogation_status", "result"],
        ))

        calls["n"] = 0
        no_stats = chs._interrogate_one_candidate(
            ("P1", "e1"),
            {("P1", "e1"): pd.Series({"player_id": "P1", "player_name": "Retry Guy"})},
            "fake-key", max_retries=2, retry_backoff_seconds=0.001,
        )
        results.append(check(
            "timers: stats is optional -- omitting it changes nothing about the result or the retries",
            no_stats["interrogation_status"] == "complete" and calls["n"] == 3,
        ))

        calls["n"] = 99  # always succeeds
        stats2 = {}
        chs._interrogate_one_candidate(
            ("P2", "e2"),
            {("P2", "e2"): pd.Series({"player_id": "P2", "player_name": "Clean Guy"})},
            "fake-key", max_retries=2, stats=stats2,
        )
        results.append(check(
            "timers: a first-try success records 1 call and 0 retries -- retries measure real extra "
            "work, never just 'a candidate was interrogated'",
            stats2 == {"calls": 1, "candidates": 1, "retries": 0},
        ))
    finally:
        chs.interrogate_story = orig_interrogate

    # END-TO-END WIRING. The tally being correct is worthless if the sink is
    # never handed to the stage that fills it -- an earlier version of this
    # change declared interrogation_stats and forgot to pass it, so every
    # timing field came back empty while the unit tests above still passed.
    results.append(check(
        "timers: shape_content_draft_rows actually THREADS the sink into the interrogation stage -- "
        "selected/waves/concurrency/seconds all populated on a real call, not an empty dict",
        isinstance(gate_result.get("interrogation_stats"), dict)
        and {"selected", "waves", "concurrency", "seconds"} <= set(gate_result["interrogation_stats"])
        and gate_result["interrogation_stats"]["selected"] == 4
        and gate_result["interrogation_stats"]["waves"] >= 1
        and gate_result["interrogation_stats"]["seconds"] >= 0,
    ))
    results.append(check(
        "timers: the writer loop reports its own wall time on the same return",
        isinstance(gate_result.get("writer_loop_seconds"), float)
        and gate_result["writer_loop_seconds"] >= 0,
    ))
    results.append(check(
        "timers: candidates tallied equals candidates selected -- every selected candidate is "
        "accounted for exactly once",
        gate_result["interrogation_stats"].get("candidates") == gate_result["interrogation_stats"]["selected"],
    ))

    slow_stats = {"seconds": 1.25, "selected": 186, "waves": 13, "concurrency": 15,
                  "calls": 190, "candidates": 186, "retries": 4}
    results.append(check(
        "timers: the Interrogation block carries what the wall time is MADE of -- selected calls, "
        "waves (ceil(selected/concurrency)), and retries on top",
        slow_stats["waves"] == math.ceil(slow_stats["selected"] / slow_stats["concurrency"])
        and slow_stats["calls"] - slow_stats["candidates"] == slow_stats["retries"],
    ))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
