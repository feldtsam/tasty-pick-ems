"""
Tests Proposal 2's real stickiness implementation: _compute_sticky_
assignment (the pure state-transition function) and build_prior_state_
with_walkback (the bye-week-aware read/merge logic) — see curate_home_
shelves.py's own docstrings for the full design reasoning.

Deliberately isolated from test_curate_home_shelves.py — this is
substantial enough logic to warrant its own focused suite, same
"purpose-built test file" precedent as Part C's test_nfl_tasty_six_
writer.py.

Run: python3 nfl/api/test_stickiness.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd

import curate_home_shelves
from curate_home_shelves import (
    STICKINESS_MARGIN,
    _compute_sticky_assignment,
    assign_home_shelves,
    build_prior_state_with_walkback,
    read_shelf_signal_history,
)

WEEKLY_PATH = Path(__file__).resolve().parent.parent / "scripts" / "player_redzone_weekly.csv"


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


if __name__ == "__main__":
    results = []

    # ============================================================
    # REAL DATA: Kyle Juszczyk, 2025 weeks 10 -> 17 (the design
    # investigation's own example) — real td_opportunity 56.0 -> 54.3,
    # real role_momentum 50.0 -> 52.2. Neither moves anywhere close to
    # 20 points. Confirms the "no reassignment" baseline with genuine
    # historical numbers, not invented ones. Frames it as a real
    # candidate-vs-current comparison: suppose his real home shelf were
    # Red Zone Trends (td_opportunity) and RB Trends (role_momentum)
    # were the candidate each week — real numbers, real result.
    # ============================================================
    juszczyk_state = _compute_sticky_assignment(
        candidate_shelf="RB Trends", candidate_signal=52.2,       # real week 17 role_momentum
        current_home_shelf="Red Zone Trends", current_signal=54.3,  # real week 17 td_opportunity
        prior_pending_shelf=None, prior_pending_run_count=0,
    )
    results.append(check(
        f"real Kyle Juszczyk case (td_opportunity 54.3 vs role_momentum 52.2, real 2025 wk17 data): "
        f"no real margin -> stays on Red Zone Trends, nothing pending (got {juszczyk_state})",
        juszczyk_state == {"home_shelf": "Red Zone Trends", "pending_shelf": None, "pending_run_count": 0},
    ))

    # ============================================================
    # Real-numbers-grounded SYNTHETIC SEQUENCE (clearly labeled as
    # constructed, not a coincidentally-perfect real 3-week run): uses
    # Travis Kelce's real 2025 week 8->9 td_opportunity jump (41.1 ->
    # 66.5, a genuine +25.4 real swing found in real historical data)
    # as the real number driving week 1's margin-exceeding trigger, then
    # a constructed week-2 continuation to prove the state machine fires
    # at EXACTLY the second consecutive qualifying run, not the first
    # and not a third. "Current shelf" signal held flat at a plausible
    # real role_momentum reading (45.5, itself a real value from this
    # same player's real data window) across both weeks, isolating the
    # test to the candidate side moving, the cleanest way to prove the
    # exact firing point without also varying the anchor.
    # ============================================================
    week1 = _compute_sticky_assignment(
        candidate_shelf="Red Zone Trends", candidate_signal=66.5,   # real Travis Kelce wk9 td_opportunity
        current_home_shelf="RB Trends", current_signal=45.5,         # real-valued anchor, held flat
        prior_pending_shelf=None, prior_pending_run_count=0,
    )
    results.append(check(
        f"week 1 of 2: candidate (66.5) exceeds current (45.5) by 21.0 >= {STICKINESS_MARGIN} -- "
        f"pending, home shelf UNCHANGED this week (got {week1})",
        week1 == {"home_shelf": "RB Trends", "pending_shelf": "Red Zone Trends", "pending_run_count": 1},
    ))

    week2 = _compute_sticky_assignment(
        candidate_shelf="Red Zone Trends", candidate_signal=68.0,   # still real-shaped, still clears margin
        current_home_shelf="RB Trends", current_signal=45.0,
        prior_pending_shelf=week1["pending_shelf"], prior_pending_run_count=week1["pending_run_count"],
    )
    results.append(check(
        f"week 2 of 2, SAME candidate still exceeds margin -> REASSIGNMENT FIRES exactly here, "
        f"pending resets to None/0 for the new home shelf (got {week2})",
        week2 == {"home_shelf": "Red Zone Trends", "pending_shelf": None, "pending_run_count": 0},
    ))

    # Regression: if week 2 had come back BELOW margin, must NOT fire
    # (proves it's not a "2 real weeks elapsed" timer, but a genuine
    # "margin held twice" check).
    week2_missed = _compute_sticky_assignment(
        candidate_shelf="Red Zone Trends", candidate_signal=50.0,   # margin no longer met
        current_home_shelf="RB Trends", current_signal=45.0,
        prior_pending_shelf=week1["pending_shelf"], prior_pending_run_count=week1["pending_run_count"],
    )
    results.append(check(
        f"NOT firing early: if week 2's margin isn't actually met, reassignment does NOT fire, "
        f"pending resets (got {week2_missed})",
        week2_missed == {"home_shelf": "RB Trends", "pending_shelf": None, "pending_run_count": 0},
    ))

    # A DIFFERENT candidate exceeding margin in week 2 must NOT inherit
    # week 1's count -- proves streaks don't accidentally merge across
    # different candidate shelves.
    week2_different_candidate = _compute_sticky_assignment(
        candidate_shelf="WR Trends", candidate_signal=70.0,
        current_home_shelf="RB Trends", current_signal=45.0,
        prior_pending_shelf=week1["pending_shelf"], prior_pending_run_count=week1["pending_run_count"],
    )
    results.append(check(
        f"a DIFFERENT candidate exceeding margin does NOT inherit the prior candidate's run_count -- "
        f"restarts at 1, not 2 (got {week2_different_candidate})",
        week2_different_candidate == {"home_shelf": "RB Trends", "pending_shelf": "WR Trends", "pending_run_count": 1},
    ))

    # current_signal=None -- the player's real current home shelf isn't
    # something they qualify for AT ALL this week (a real, if
    # unaddressed-by-the-approved-rules, case) -- immediate reassignment,
    # no 2-week wait, per this implementation's documented extension.
    current_shelf_gone = _compute_sticky_assignment(
        candidate_shelf="ATTD +500-699", candidate_signal=40.0,
        current_home_shelf="Red Zone Trends", current_signal=None,
        prior_pending_shelf=None, prior_pending_run_count=0,
    )
    results.append(check(
        f"current home shelf no longer qualified for at all this week -> immediate move to the fresh "
        f"candidate, no wait required (got {current_shelf_gone})",
        current_shelf_gone == {"home_shelf": "ATTD +500-699", "pending_shelf": None, "pending_run_count": 0},
    ))

    # candidate == current -- trivial, no comparison being made at all.
    same_shelf = _compute_sticky_assignment(
        candidate_shelf="Red Zone Trends", candidate_signal=80.0,
        current_home_shelf="Red Zone Trends", current_signal=80.0,
        prior_pending_shelf="RB Trends", prior_pending_run_count=1,
    )
    results.append(check(
        f"candidate shelf already IS the current home shelf -- nothing being challenged, any stale "
        f"pending state from a DIFFERENT shelf is cleared (got {same_shelf})",
        same_shelf == {"home_shelf": "Red Zone Trends", "pending_shelf": None, "pending_run_count": 0},
    ))

    # ============================================================
    # SHELF-CASING un-slug on read — nfl_shelf_signal_history stores the
    # frontend's snake_case slug (see shape_shelf_signal_history_rows /
    # SHELF_SLUG); read_shelf_signal_history must reverse it so the
    # sticky-assignment comparisons stay in the internal Title-Case
    # representation. Exercises the REAL read function with a fake
    # transport.
    # ============================================================
    import lovable_forward
    _real_forward = lovable_forward.forward_to_lovable

    def _fake_forward(payload, secret, url):
        import json as _json
        body = {"shelf_signal_history": [
            {"player_id": "P1", "home_shelf": "rb_trends", "pending_shelf": "red_zone_trends",
             "qualifying_signals": {}, "pending_run_count": 1},
            {"player_id": "P2", "home_shelf": "attd_700_plus", "pending_shelf": None,
             "qualifying_signals": {}, "pending_run_count": 0},
            {"player_id": "P3", "home_shelf": "AFC East", "pending_shelf": None,
             "qualifying_signals": {}, "pending_run_count": 0},
        ]}
        return {"success": True, "error": None, "status_code": 200, "response_body": _json.dumps(body)}

    lovable_forward.forward_to_lovable = _fake_forward
    try:
        read_result = read_shelf_signal_history(2025, 10, "fake", read_url="http://x")
    finally:
        lovable_forward.forward_to_lovable = _real_forward

    results.append(check(
        "read_shelf_signal_history un-slugs a stored slug back to the internal Title-Case name "
        f"(got home_shelf={read_result['rows'].get('P1', {}).get('home_shelf')!r}, "
        f"pending_shelf={read_result['rows'].get('P1', {}).get('pending_shelf')!r})",
        read_result["rows"]["P1"]["home_shelf"] == "RB Trends"
        and read_result["rows"]["P1"]["pending_shelf"] == "Red Zone Trends"
        and read_result["rows"]["P2"]["home_shelf"] == "ATTD +700+"
        and read_result["rows"]["P2"]["pending_shelf"] is None,
    ))
    results.append(check(
        "read_shelf_signal_history passes an Around-the-League division string through un-slug untouched",
        read_result["rows"]["P3"]["home_shelf"] == "AFC East",
    ))

    # ============================================================
    # Walk-back logic — isolated from any real network call via a fake
    # read_shelf_signal_history, monkeypatched onto the module (this
    # tests the WALK-BACK MECHANISM's own bulk/early-stop/bounded
    # behavior, not real Lovable connectivity, which nfl_shelf_signal_
    # history has no real rows in yet to walk back through anyway).
    # ============================================================
    real_read = curate_home_shelves.read_shelf_signal_history

    # Synthetic 3-week history: player A appears every week (found on
    # the very first lookback), player B has a real bye gap at week 16
    # (present week 15, ABSENT week 16, needs to walk back one further
    # week to be found), player C has never appeared at all (a genuine
    # first-appearance case, should NOT be found even after exhausting
    # the lookback).
    fake_weeks = {
        15: {"A": {"home_shelf": "Red Zone Trends", "pending_shelf": None, "pending_run_count": 0},
             "B": {"home_shelf": "RB Trends", "pending_shelf": "Red Zone Trends", "pending_run_count": 1}},
        16: {"A": {"home_shelf": "Red Zone Trends", "pending_shelf": None, "pending_run_count": 0}},
        # B genuinely absent from week 16 -- the real bye-week shape.
    }
    calls = []

    def fake_read(season, week, secret, read_url=None):
        calls.append(week)
        rows = fake_weeks.get(week, {})
        return {"ok": True, "error": None, "status_code": 200, "rows": rows}

    curate_home_shelves.read_shelf_signal_history = fake_read
    try:
        result = build_prior_state_with_walkback(
            season=2025, week=17, eligible_player_ids=["A", "B", "C"], secret="fake", max_lookback=3,
        )
    finally:
        curate_home_shelves.read_shelf_signal_history = real_read

    results.append(check(
        "player A (present the immediately-prior week) found at week 16, only ONE real call needed for them",
        result.get("A", {}).get("found_at_week") == 16,
    ))
    results.append(check(
        "player B (a real bye gap at week 16) correctly walked back to their real week-15 row -- "
        "pending_shelf/pending_run_count carried forward, NOT reset",
        result.get("B") == {"home_shelf": "RB Trends", "pending_shelf": "Red Zone Trends", "pending_run_count": 1, "found_at_week": 15},
    ))
    results.append(check(
        "player C (genuinely never appeared) is NOT in the result at all -- correctly falls through "
        "to first-appearance handling upstream, not a fabricated entry",
        "C" not in result,
    ))
    results.append(check(
        f"bounded walk-back makes exactly max_lookback=3 calls here (16, 15, 14) -- NOT the naive "
        f"'stops once A and B are found' expectation: player C never appears in ANY fake week, so "
        f"remaining={{'C'}} never empties, and the loop correctly exhausts its full bound rather than "
        f"looping forever. A REAL refinement of the design report's own 'typically just 1 call' claim: "
        f"a real week with even one genuine first-appearance player (common — rookies, new ATTD "
        f"eligibility) will hit the full bound every time, not just the early-stop path (calls={calls})",
        calls == [16, 15, 14],
    ))

    # Season-boundary stop: week 1 has no "week 0" to look back into.
    curate_home_shelves.read_shelf_signal_history = fake_read
    calls.clear()
    try:
        boundary_result = build_prior_state_with_walkback(
            season=2025, week=1, eligible_player_ids=["Z"], secret="fake", max_lookback=3,
        )
    finally:
        curate_home_shelves.read_shelf_signal_history = real_read
    results.append(check(
        "week 1 of a season: walk-back makes zero calls (no real week 0 exists) rather than looking back "
        f"into the prior season or erroring (calls={calls}, result={boundary_result})",
        calls == [] and boundary_result == {},
    ))

    # ============================================================
    # REAL, END-TO-END integration test — not just the isolated pure
    # function, the FULL assign_home_shelves() pipeline against real
    # 2025 Week 17 data (synthetic odds re-attached, same established
    # technique used throughout this session — real seasons never
    # captured real ATTD odds). Finds a real, organic (not hand-picked
    # numbers) case where two of a real player's real qualifying shelf
    # signals differ by >=20 points, then runs the real 2-week sequence
    # through assign_home_shelves itself to confirm the wiring between
    # _compute_sticky_assignment and the full per-player loop is
    # correct — not just the pure function in isolation.
    # ============================================================
    if not WEEKLY_PATH.exists():
        print(f"(skipped the real end-to-end integration test — {WEEKLY_PATH} not present in this environment)")
    else:
        weekly = pd.read_csv(WEEKLY_PATH)
        sub = weekly[(weekly["season"] == 2025) & (weekly["week"] == 17)].copy()
        rng = np.random.default_rng(11)
        sub["consensus_price_american"] = np.where(sub["tpe_score"].notna(), rng.integers(250, 1500, size=len(sub)), np.nan)

        home_fresh = assign_home_shelves(sub)
        target = None
        for _, row in home_fresh.iterrows():
            qs = row["qualifying_signals"]
            if len(qs) < 2:
                continue
            fresh = row["home_shelf"]
            for other, val in qs.items():
                if other != fresh and qs[fresh] - val >= STICKINESS_MARGIN:
                    target = (row["player_id"], row["player_name"], fresh, qs[fresh], other, val)
                    break
            if target:
                break

        if target is None:
            print("(skipped the real end-to-end integration test — no real player this week has a qualifying-shelf gap >= margin)")
        else:
            pid, name, fresh_shelf, fresh_val, prior_shelf, prior_val = target
            real_margin = fresh_val - prior_val
            print(f"\nReal end-to-end case: {name} ({pid}) — real {fresh_shelf}={fresh_val:.1f} vs real "
                  f"{prior_shelf}={prior_val:.1f}, real margin={real_margin:.1f}")

            prior1 = {pid: {"home_shelf": prior_shelf, "pending_shelf": None, "pending_run_count": 0}}
            r1 = assign_home_shelves(sub, prior_assignments=prior1)
            row1 = r1[r1["player_id"] == pid].iloc[0]
            results.append(check(
                f"real end-to-end week 1: {name} stays sticky on real prior shelf {prior_shelf!r}, "
                f"pending becomes {fresh_shelf!r} at count=1 (got home_shelf={row1['home_shelf']!r}, "
                f"pending={row1['pending_shelf']!r}/{row1['pending_run_count']})",
                row1["home_shelf"] == prior_shelf and row1["pending_shelf"] == fresh_shelf and row1["pending_run_count"] == 1,
            ))

            prior2 = {pid: {"home_shelf": prior_shelf, "pending_shelf": row1["pending_shelf"], "pending_run_count": row1["pending_run_count"]}}
            r2 = assign_home_shelves(sub, prior_assignments=prior2)
            row2 = r2[r2["player_id"] == pid].iloc[0]
            results.append(check(
                f"real end-to-end week 2: SAME real margin holds again -> REASSIGNS to {fresh_shelf!r} "
                f"exactly here, pending resets to None/0 (got home_shelf={row2['home_shelf']!r}, "
                f"pending={row2['pending_shelf']!r}/{row2['pending_run_count']})",
                row2["home_shelf"] == fresh_shelf and row2["pending_shelf"] is None and row2["pending_run_count"] == 0,
            ))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
