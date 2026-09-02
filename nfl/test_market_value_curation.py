"""
Tests the Phase B market-value merge for /api/curate-and-write-drafts:
market_value.market_value_snapshot_for_curation (the nfl_price_history
read+score wrapper) and market_value.merge_market_value_and_rescore (the
drop-merge-rescore sequence shared with reconcile_week).

Focus: curation output with a live odds merge present vs. absent —
scores differ appropriately, completeness reflects coverage, and thin /
empty coverage degrades gracefully instead of raising.

Run: python3 nfl/test_market_value_curation.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "api"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))

import numpy as np
import pandas as pd

import market_value as mv_mod
from market_value import (
    CURATION_MARKET_VALUE_COLUMNS,
    market_value_snapshot_for_curation,
    merge_market_value_and_rescore,
)
from scoring import CONFIG, score_market_value

SYNTH = Path(__file__).resolve().parent / "data" / "stub_weeks" / "synthetic_smoke_test.csv"


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


def _base_weekly(n=40):
    """A fresh stub slice: real pipeline columns, but market-value
    columns stripped (as if build_stub_week just produced it)."""
    df = pd.read_csv(SYNTH).head(n).copy()
    df["season"] = 2099
    df["week"] = 1
    return df.drop(columns=[c for c in CURATION_MARKET_VALUE_COLUMNS if c in df.columns], errors="ignore")


def _mv_snapshot_for(weekly, covered_player_ids):
    """Build a market_value_snapshot_for_curation-shaped frame: real
    consensus_implied_probability for `covered_player_ids`, scored."""
    rows = []
    for i, pid in enumerate(covered_player_ids):
        rows.append({
            "player_id": pid, "season": 2099, "week": 1,
            "consensus_implied_probability": 0.30 + 0.03 * i,
            "consensus_price_american": 300 + 40 * i,
            "n_books": 3, "best_price": 320 + 40 * i, "best_book": "TestBook",
        })
    scored = score_market_value(pd.DataFrame(rows), CONFIG)
    key = ["player_id", "season", "week"]
    return scored[key + CURATION_MARKET_VALUE_COLUMNS].reset_index(drop=True)


class _FakeResp:
    def __init__(self, status, body):
        self.status_code = status
        self.text = body if isinstance(body, str) else json.dumps(body)


if __name__ == "__main__":
    results = []

    weekly = _base_weekly(40)
    all_pids = list(weekly["player_id"])
    empty_mv = pd.DataFrame(columns=["player_id", "season", "week"] + CURATION_MARKET_VALUE_COLUMNS)

    # ------------------------------------------------------------------
    # merge_market_value_and_rescore — absent vs present
    # ------------------------------------------------------------------
    no_odds = merge_market_value_and_rescore(weekly.copy(), empty_mv, CURATION_MARKET_VALUE_COLUMNS)
    results.append(check(
        "no odds: merge succeeds, every market_value_score is NaN, completeness all 0/NaN, tpe_score present",
        no_odds["market_value_score"].isna().all()
        and not no_odds["tpe_score"].isna().all()
        and float(no_odds["market_value_completeness"].fillna(0).sum()) == 0.0,
    ))

    covered = all_pids[:20]  # half the pool has live odds
    mv_snap = _mv_snapshot_for(weekly, covered)
    with_odds = merge_market_value_and_rescore(weekly.copy(), mv_snap, CURATION_MARKET_VALUE_COLUMNS)

    cov_mask = with_odds["player_id"].isin(covered)
    results.append(check(
        "with odds: covered players get a real market_value_score (not NaN) + completeness 100",
        with_odds.loc[cov_mask, "market_value_score"].notna().all()
        and (with_odds.loc[cov_mask, "market_value_completeness"] == 100).all(),
    ))
    results.append(check(
        "with odds: UNCOVERED players still get NaN market_value_score (left merge, not outer)",
        with_odds.loc[~cov_mask, "market_value_score"].isna().all()
        and len(with_odds) == len(weekly),  # no odds-only rows added
    ))

    # covered players' tpe_score / evidence_quality move once the 4th
    # pillar is real; uncovered players are unchanged from the no-odds run
    merged = no_odds.merge(
        with_odds[["player_id", "tpe_score", "evidence_quality", "core_score"]],
        on="player_id", suffixes=("_base", "_odds"),
    )
    cov_m = merged["player_id"].isin(covered)
    results.append(check(
        "with odds: covered players' tpe_score changes vs the 3-pillar run",
        (merged.loc[cov_m, "tpe_score_base"] != merged.loc[cov_m, "tpe_score_odds"]).any(),
    ))
    results.append(check(
        "with odds: covered players' evidence_quality changes (4th convergence family now present)",
        (merged.loc[cov_m, "evidence_quality_base"] != merged.loc[cov_m, "evidence_quality_odds"]).any(),
    ))
    results.append(check(
        "with odds: UNCOVERED players' tpe_score is byte-identical to the no-odds run",
        (merged.loc[~cov_m, "tpe_score_base"] == merged.loc[~cov_m, "tpe_score_odds"]).all(),
    ))
    results.append(check(
        "completeness reflects coverage: mean(fillna 0) ~= covered fraction * 100",
        abs(float(with_odds["market_value_completeness"].fillna(0).mean()) - (len(covered) / len(weekly) * 100)) < 0.1,
    ))

    # ------------------------------------------------------------------
    # thin coverage (item 4) — 1 of 40 — must not raise
    # ------------------------------------------------------------------
    try:
        thin = merge_market_value_and_rescore(weekly.copy(), _mv_snapshot_for(weekly, all_pids[:1]), CURATION_MARKET_VALUE_COLUMNS)
        thin_ok = (not thin["tpe_score"].isna().all()) and thin["market_value_score"].notna().sum() == 1
    except Exception as e:
        thin_ok = False
        print(f"  raised: {e!r}")
    results.append(check("thin coverage (1/40): merge + rescore succeeds, does not raise", thin_ok))

    # re-run idempotency: rescoring an already-scored frame overwrites, not compounds
    twice = merge_market_value_and_rescore(with_odds.copy(), mv_snap, CURATION_MARKET_VALUE_COLUMNS)
    results.append(check(
        "idempotent: merge_market_value_and_rescore on an already-scored frame reproduces the same tpe_score",
        np.allclose(twice["tpe_score"].fillna(-1), with_odds["tpe_score"].fillna(-1)),
    ))

    # ------------------------------------------------------------------
    # market_value_snapshot_for_curation — read wrapper
    # ------------------------------------------------------------------
    import lovable_forward
    orig = lovable_forward.requests.post
    try:
        lovable_forward.requests.post = lambda url, **kw: _FakeResp(200, {"ok": True, "price_history": []})
        snap = market_value_snapshot_for_curation(2099, 1, "s", read_url="https://x.test")
        results.append(check(
            "snapshot_for_curation: empty nfl_price_history -> zero-row frame with the curation columns "
            "(incl. consensus_price_american, which _for_reconciliation drops)",
            len(snap) == 0
            and list(snap.columns) == ["player_id", "season", "week"] + CURATION_MARKET_VALUE_COLUMNS
            and "consensus_price_american" in snap.columns,
        ))

        ph_rows = [
            {"player_id": "TEST-000001", "season": None, "week": None, "poll_timestamp": "2099-09-01T12:00:00Z",
             "event_id": "e1", "commence_time": "2099-09-07T17:00:00Z", "home_team": "TS01", "away_team": "TS02",
             "player_name_raw": "Test", "team": "TS01", "position_group": "WR", "matched": True,
             "match_issue_type": None, "n_books": 3, "best_price": 350, "best_book": "B",
             "consensus_implied_probability": 0.32, "consensus_price_american": 320},
        ]
        lovable_forward.requests.post = lambda url, **kw: _FakeResp(200, {"ok": True, "price_history": ph_rows})
        snap2 = market_value_snapshot_for_curation(2099, 1, "s", read_url="https://x.test")
        results.append(check(
            "snapshot_for_curation: real poll row -> season/week attached, market_value_score computed, "
            "consensus_price_american carried",
            len(snap2) == 1
            and snap2.iloc[0]["season"] == 2099 and snap2.iloc[0]["week"] == 1
            and pd.notna(snap2.iloc[0]["market_value_score"])
            and snap2.iloc[0]["consensus_price_american"] == 320,
        ))
    finally:
        lovable_forward.requests.post = orig

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
