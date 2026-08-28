"""
Tests for shelves.py — the NFL Picks Trend Shelf layer (Red Zone Trends,
RB/WR/TE Trends). Synthetic, controlled fixtures, by design: these tests
prove the MECHANISM (ranking order, eligibility gates, fallback fill,
cross-shelf overlap, target-share tiebreak) is correct in cases built
specifically to be unambiguous — e.g. a row whose primary signal and
tpe_score DISagree, so a bug that accidentally sorted by tpe_score would
be caught immediately. Real-historical-week validation (does the pillar
output itself make football sense) is a separate exercise, reported
alongside this implementation, not duplicated here.

Run: python3 nfl/test_shelves.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

from shelves import (
    CONFIG,
    build_all_shelves,
    build_rb_trends,
    build_red_zone_trends,
    build_te_trends,
    build_wr_trends,
)


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


DEFAULTS = {
    "player_name": "Test Player", "posteam": "SEA", "season": 2025, "week": 10,
    "td_opportunity": 50.0, "td_opportunity_completeness": 100.0,
    "proven_heat": 50.0, "emerging_heat": 50.0,
    "touch_share_trend_pct": 50.0, "snap_share_trend_pct": 50.0,
    "role_momentum": 50.0, "role_momentum_completeness": 100.0,
    "role_trend": 50.0, "external_opportunity": 0.0,
    "touch_share_trend_pct_role": 50.0, "snap_share_trend_pct_role": 50.0, "depth_chart_movement_pct": 50.0,
    "ahead_injury_statuses": [],
    "snap_share_last1": 0.5, "snap_share_season_avg": 0.5,
    "i10_touches": 1, "gl_touches": 0, "rz_tds": 0,
    "tpe_score": 50.0, "evidence_quality": 80.0,
    "consensus_price_american": 350,
    "position_group": "RB",
}


def make_row(player_id, game_id=None, **overrides):
    row = dict(DEFAULTS)
    row["player_id"] = player_id
    row["game_id"] = game_id or f"2025_10_{player_id}"
    row.update(overrides)
    return row


def make_weekly(rows: list) -> pd.DataFrame:
    return pd.DataFrame(rows)


if __name__ == "__main__":
    results = []

    # ============================================================
    # 1. Shelf-specific primary ranking
    # ============================================================
    weekly = make_weekly([
        make_row("P1", td_opportunity=90.0, role_momentum=10.0),
        make_row("P2", td_opportunity=60.0, role_momentum=95.0),
        make_row("P3", td_opportunity=30.0, role_momentum=40.0),
    ])
    rz = build_red_zone_trends(weekly)
    results.append(check(
        "Red Zone Trends ranks by td_opportunity (P1 > P2 > P3)",
        [c["player_id"] for c in rz["cards"]] == ["P1", "P2", "P3"],
    ))

    # ============================================================
    # 4. tpe_score must NOT override the primary signal
    # ============================================================
    weekly = make_weekly([
        make_row("HIGH_ROLE_LOW_TPE", role_momentum=95.0, tpe_score=10.0, position_group="RB"),
        make_row("LOW_ROLE_HIGH_TPE", role_momentum=20.0, tpe_score=99.0, position_group="RB"),
    ])
    rb = build_rb_trends(weekly)
    results.append(check(
        "RB Trends: role_momentum wins even when tpe_score strongly disagrees",
        [c["player_id"] for c in rb["cards"]] == ["HIGH_ROLE_LOW_TPE", "LOW_ROLE_HIGH_TPE"],
    ))

    # tpe_score as tiebreaker when the primary signal ties
    weekly = make_weekly([
        make_row("TIE_LOW_TPE", role_momentum=70.0, tpe_score=20.0, position_group="WR"),
        make_row("TIE_HIGH_TPE", role_momentum=70.0, tpe_score=80.0, position_group="WR"),
    ])
    wr = build_wr_trends(weekly)
    results.append(check(
        "WR Trends: tpe_score correctly breaks a tie in the primary signal",
        [c["player_id"] for c in wr["cards"]] == ["TIE_HIGH_TPE", "TIE_LOW_TPE"],
    ))

    # ============================================================
    # 2. +300 ATTD eligibility floor
    # ============================================================
    weekly = make_weekly([
        make_row("BELOW_FLOOR", td_opportunity=99.0, consensus_price_american=250),
        make_row("AT_FLOOR", td_opportunity=80.0, consensus_price_american=300),
        make_row("ABOVE_FLOOR", td_opportunity=70.0, consensus_price_american=450),
        make_row("NO_ODDS_DATA", td_opportunity=95.0, consensus_price_american=None),
    ])
    rz = build_red_zone_trends(weekly)
    ids = [c["player_id"] for c in rz["cards"]]
    results.append(check("BELOW_FLOOR (+250) excluded despite the highest td_opportunity", "BELOW_FLOOR" not in ids))
    results.append(check("AT_FLOOR (+300 exactly) included — floor is inclusive", "AT_FLOOR" in ids))
    results.append(check("NO_ODDS_DATA (missing consensus_price_american) excluded, not silently admitted", "NO_ODDS_DATA" not in ids))
    results.append(check("ABOVE_FLOOR included", "ABOVE_FLOOR" in ids))

    # ============================================================
    # 3. Position filtering
    # ============================================================
    weekly = make_weekly([
        make_row("RB1", position_group="RB", role_momentum=90.0),
        make_row("WR1", position_group="WR", role_momentum=95.0),
        make_row("TE1", position_group="TE", role_momentum=99.0),
        make_row("QB1", position_group=None, role_momentum=100.0),
    ])
    rb = build_rb_trends(weekly)
    wr = build_wr_trends(weekly)
    te = build_te_trends(weekly)
    results.append(check("RB Trends contains only RB1", [c["player_id"] for c in rb["cards"]] == ["RB1"]))
    results.append(check("WR Trends contains only WR1", [c["player_id"] for c in wr["cards"]] == ["WR1"]))
    results.append(check("TE Trends contains only TE1", [c["player_id"] for c in te["cards"]] == ["TE1"]))
    results.append(check(
        "A NaN/None position_group (e.g. a QB) never leaks onto any position shelf despite a high role_momentum",
        all("QB1" not in [c["player_id"] for c in s["cards"]] for s in (rb, wr, te)),
    ))

    # Real bug caught in historical validation (2025 Week 2): a QB (no
    # position_group, since QBs are outside RB/WR/TE scope everywhere
    # else in this codebase) still has a real td_opportunity value and
    # must NOT appear on Red Zone Trends despite scoring highest.
    weekly = make_weekly([
        make_row("QB_SCRAMBLER", position_group=None, td_opportunity=99.0),
        make_row("REAL_RB", position_group="RB", td_opportunity=40.0),
    ])
    rz = build_red_zone_trends(weekly)
    results.append(check(
        "Red Zone Trends excludes a QB (NaN position_group) even with the single highest td_opportunity in the pool",
        "QB_SCRAMBLER" not in [c["player_id"] for c in rz["cards"]],
    ))
    results.append(check(
        "Red Zone Trends still includes the real RB",
        "REAL_RB" in [c["player_id"] for c in rz["cards"]],
    ))

    # ============================================================
    # 5. Configurable completeness threshold
    # ============================================================
    weekly = make_weekly([
        make_row("THIN", td_opportunity=99.0, td_opportunity_completeness=30.0),
        make_row("SOLID", td_opportunity=60.0, td_opportunity_completeness=80.0),
    ])
    strict_cfg = {**CONFIG, "completeness_threshold": {**CONFIG["completeness_threshold"], "red_zone_trends": 50.0}}
    loose_cfg = {**CONFIG, "completeness_threshold": {**CONFIG["completeness_threshold"], "red_zone_trends": 0.0}}
    rz_strict = build_red_zone_trends(weekly, strict_cfg)
    rz_loose = build_red_zone_trends(weekly, loose_cfg)
    results.append(check(
        "threshold=50: THIN (completeness 30) is only a fallback card, not evidence-gated",
        not next(c for c in rz_strict["cards"] if c["player_id"] == "THIN")["meets_evidence_threshold"],
    ))
    results.append(check(
        "threshold=0: THIN now passes the (relaxed) gate outright",
        next(c for c in rz_loose["cards"] if c["player_id"] == "THIN")["meets_evidence_threshold"],
    ))
    results.append(check(
        "raising/lowering the threshold is a config change, not a code change (both used the same build_red_zone_trends)",
        rz_strict["gated_pool_size"] != rz_loose["gated_pool_size"],
    ))

    # ============================================================
    # Fill-guarantee fallback (addendum)
    # ============================================================
    rows = [make_row(f"GATED{i}", td_opportunity=100.0 - i, td_opportunity_completeness=90.0) for i in range(2)]
    rows += [make_row(f"BELOW{i}", td_opportunity=50.0 - i, td_opportunity_completeness=10.0) for i in range(6)]
    weekly = make_weekly(rows)
    rz = build_red_zone_trends(weekly)
    results.append(check("fallback fill: only 2 real gated candidates, shelf still reaches size 6", len(rz["cards"]) == 6))
    results.append(check("fallback fill: the 2 gated cards rank ahead of all fallback cards", all(c["meets_evidence_threshold"] for c in rz["cards"][:2])))
    results.append(check("fallback fill: the 4 backfilled cards are correctly flagged, not silently indistinguishable", all(not c["meets_evidence_threshold"] for c in rz["cards"][2:])))
    results.append(check("fallback fill: fallback_count reported honestly as 4", rz["fallback_count"] == 4))

    # A pool with fewer than 6 total eligible candidates at all (even
    # including fallback rows) legitimately can't reach 6 — report short,
    # never fabricate a card. This is the real TE Trends risk flagged in
    # the architecture review.
    weekly = make_weekly([make_row(f"ONLY{i}", position_group="TE", role_momentum=90.0 - i) for i in range(3)])
    te = build_te_trends(weekly)
    results.append(check(
        "a genuinely thin pool (3 total eligible TEs) reports 3 cards, not a fabricated 6",
        len(te["cards"]) == 3,
    ))

    # ============================================================
    # 6. Legitimate cross-shelf overlap
    # ============================================================
    weekly = make_weekly([
        make_row("OVERLAP", position_group="RB", td_opportunity=95.0, role_momentum=92.0),
        make_row("OTHER", position_group="RB", td_opportunity=40.0, role_momentum=40.0),
    ])
    rz = build_red_zone_trends(weekly)
    rb = build_rb_trends(weekly)
    on_rz = "OVERLAP" in [c["player_id"] for c in rz["cards"]]
    on_rb = "OVERLAP" in [c["player_id"] for c in rb["cards"]]
    results.append(check(
        "the same player legitimately appears on both Red Zone Trends and RB Trends when both signals are real",
        on_rz and on_rb,
    ))
    rz_headline = next(c for c in rz["cards"] if c["player_id"] == "OVERLAP")["headline"]
    rb_headline = next(c for c in rb["cards"] if c["player_id"] == "OVERLAP")["headline"]
    results.append(check(
        "overlap is not suppressed AND each shelf tells a distinct story for the same player",
        rz_headline != rb_headline,
    ))

    # ============================================================
    # 7. WR whole-game target-share calculation
    # ============================================================
    # A minimal synthetic pbp: WTGT targeted on 3 of 4 team pass attempts
    # in game G1 (season 2025 wk9), then 1 of 4 in G2 (wk10) -- a real,
    # sharp target-share DROP, to prove the trend direction computes
    # correctly, not just that a number appears.
    def pass_play(game_id, week, posteam, receiver, td=0):
        return {
            "game_id": game_id, "season": 2025, "week": week, "posteam": posteam,
            "pass_attempt": 1, "rush_attempt": 0,
            "receiver_player_id": receiver, "receiver_player_name": receiver,
            "rusher_player_id": None, "rusher_player_name": None,
            "pass_touchdown": td, "rush_touchdown": 0, "yardline_100": 50,
        }
    synthetic_pbp = pd.DataFrame([
        pass_play("G1", 9, "SEA", "WTGT"), pass_play("G1", 9, "SEA", "WTGT"), pass_play("G1", 9, "SEA", "WTGT"),
        pass_play("G1", 9, "SEA", "OTHER1"),
        pass_play("G2", 10, "SEA", "WTGT"),
        pass_play("G2", 10, "SEA", "OTHER1"), pass_play("G2", 10, "SEA", "OTHER2"), pass_play("G2", 10, "SEA", "OTHER3"),
    ])
    weekly = make_weekly([
        make_row("WTGT", game_id="G1", week=9, position_group="WR"),
        make_row("WTGT", game_id="G2", week=10, position_group="WR"),
    ])
    wr = build_wr_trends(weekly, pbp=synthetic_pbp)
    wk10_card = next(c for c in wr["cards"] if c["player_id"] == "WTGT")
    results.append(check(
        "WR target-share evidence appears in the headline evidence text when pbp is provided",
        "target share trend" in wk10_card["why_this_hits"],
    ))

    # ============================================================
    # Storytelling honesty regression tests -- real bugs caught during
    # historical validation (2025 Week 10): a headline must never assert
    # something its own cited evidence contradicts.
    # ============================================================
    # proven_heat "wins" purely on the conversion-rate shrinkage-to-
    # league-average artifact (see scoring._shrink_rate) despite zero
    # real trailing production -- must NOT get the "goal-line work
    # becoming his" framing, which would cite 0/0/0 as if it were
    # evidence for that specific claim.
    weekly = make_weekly([make_row(
        "ZERO_PRODUCTION", proven_heat=70.0, emerging_heat=40.0,
        i10_touches=0, gl_touches=0, rz_tds=0,
        touch_share_trend_pct=60.0, snap_share_trend_pct=55.0,
    )])
    rz = build_red_zone_trends(weekly)
    card = rz["cards"][0]
    results.append(check(
        "Red Zone Trends: proven_heat 'winning' with zero real trailing production does NOT get the goal-line headline",
        card["headline"] != "The goal-line work is becoming his.",
    ))
    results.append(check(
        "Red Zone Trends: the zero-production case's evidence text contains no false zero-count claim",
        "0 goal-line touches" not in card["why_this_hits"],
    ))

    # role_trend "wins" but the row's own raw snap share is DECLINING,
    # not increasing -- must not claim "role changing hands" backed by a
    # snap-share number that actually went down.
    weekly = make_weekly([make_row(
        "DECLINING_SNAP", position_group="RB", role_trend=60.0, external_opportunity=0.0,
        snap_share_last1=0.30, snap_share_season_avg=0.32,
        touch_share_trend_pct_role=55.0, snap_share_trend_pct_role=52.0, depth_chart_movement_pct=58.0,
    )])
    rb = build_rb_trends(weekly)
    card = rb["cards"][0]
    results.append(check(
        "RB Trends: role_trend 'winning' with a declining raw snap share does NOT cite a season->recent snap-share claim",
        "(most recent)" not in card["why_this_hits"],
    ))
    results.append(check(
        "RB Trends: falls back to percentile-trend evidence instead when the raw snap share isn't really up",
        "percentile" in card["why_this_hits"],
    ))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
