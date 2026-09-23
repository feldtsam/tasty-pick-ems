"""
Tests intelligence_lifecycle.py — the pure state-transition functions in
isolation, a real historical NYJ RB Defensive Trends progression (the
known real climbing case), and a real Coaching Trends multi-signal
independence check across a real season.

Deliberately does NOT re-test the four families' own build_*_stories()
functions or intelligence_schema.build_story() — those have their own
test suites, confirmed unaffected by this module (it never imports from
or writes back into any of them).

Run: python3 nfl/test_intelligence_lifecycle.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

from intelligence_lifecycle import (
    ARCHIVE_MISS_COUNT, CONFIRM_STREAK, FAMILY_SIGNAL_THRESHOLDS,
    _compute_lifecycle_state, _compute_lifecycle_state_for_miss, apply_lifecycle, entity_key_for,
)

WEEKLY_PATH = Path(__file__).resolve().parent / "scripts" / "player_redzone_weekly.csv"


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


if __name__ == "__main__":
    results = []

    # ============================================================
    # entity_key_for — the real fix for the Coaching Trends collision.
    # ============================================================
    results.append(check(
        "player entity keys on player_id",
        entity_key_for({"type": "player", "player_id": "00-1234567"}) == "00-1234567",
    ))
    results.append(check(
        "defense entity keys on team:position_group",
        entity_key_for({"type": "defense", "team": "SF", "position_group": "RB"}) == "SF:RB",
    ))
    results.append(check(
        "team entity keys on team alone",
        entity_key_for({"type": "team", "team": "SF"}) == "SF",
    ))

    # ============================================================
    # Synthetic full state-machine sequence — real threshold (21.7,
    # defensive_matchup_vulnerability's real p75), constructed values,
    # to directly prove every transition the real NYJ case below didn't
    # happen to exercise (that family's own detection-worthiness gate
    # meant the real story never showed 2 consecutive CONFIRMING weeks
    # of further climbing — see the real validation below for why).
    # ============================================================
    threshold = FAMILY_SIGNAL_THRESHOLDS["defensive_matchup_vulnerability"]
    seq_history = {}
    seq_family = "defensive_trends"

    def _run(value, week):
        global seq_history
        story = {
            "entity": {"type": "defense", "team": "TST", "position_group": "RB"},
            "primary_signal": {"name": "defensive_matchup_vulnerability", "value": value},
            "trend_strength": value,
        }
        result = apply_lifecycle([story], seq_history, seq_family, 2099, week)
        seq_history = result["updated_history"]
        return result["history_rows"][0]

    r1 = _run(40.0, 1)
    results.append(check(f"synthetic week 1: first appearance -> Detected (got {r1['lifecycle_state']})", r1["lifecycle_state"] == "Detected"))

    r2 = _run(42.0, 2)  # delta vs baseline(40.0) = 2.0, well under threshold -- not yet Active (appearance_count=2)
    results.append(check(f"synthetic week 2: small move, still building toward Active (got {r2['lifecycle_state']})", r2["lifecycle_state"] == "Detected"))

    r3 = _run(65.0, 3)  # delta vs baseline(avg(40,42)=41.0) = 24.0 >= 21.7 -- FIRST qualifying week, not yet confirmed
    results.append(check(f"synthetic week 3: first qualifying move (delta=24.0>=21.7) -- NOT yet confirmed (only 1 of 2), settles on appearance-count basis (got {r3['lifecycle_state']})", r3["lifecycle_state"] == "Active"))

    r4 = _run(90.0, 4)  # baseline = avg(40,42,65)=49.0, delta=41.0 -- SAME direction again -- streak reaches 2 -- CONFIRMED
    results.append(check(f"synthetic week 4: SAME direction confirmed 2nd consecutive time -> Strengthening fires exactly here (got {r4['lifecycle_state']}, streak={r4['streak_count']})", r4["lifecycle_state"] == "Strengthening" and r4["streak_count"] == 2))

    r5 = _run(91.0, 5)  # baseline=avg(65,90)... real recent_values window is last 3: [42,65,90] avg=65.67, delta=25.3 -- still exceeds threshold, SAME direction -- streak=3
    results.append(check(f"synthetic week 5: still moving in the same direction -- stays Strengthening, streak keeps growing (got {r5['lifecycle_state']}, streak={r5['streak_count']})", r5["lifecycle_state"] == "Strengthening" and r5["streak_count"] == 3))

    r6 = _run(91.5, 6)  # baseline=avg(65,90,91)=82.0, delta=9.5 -- levels off, below threshold
    results.append(check(f"synthetic week 6: levels off (delta below threshold) -- settles to Active, not stuck on Strengthening (got {r6['lifecycle_state']})", r6["lifecycle_state"] == "Active"))

    r7 = _run(60.0, 7)  # baseline=avg(90,91,91.5)=90.83, delta=-30.8 -- crosses -threshold -- first qualifying WEAKENING week
    results.append(check(f"synthetic week 7: real drop crosses -threshold -- first qualifying Weakening week, not yet confirmed (got {r7['lifecycle_state']})", r7["lifecycle_state"] == "Active"))

    r8 = _run(30.0, 8)  # baseline=avg(91,91.5,60)=80.83, delta=-50.8 -- SAME direction again -- confirmed
    results.append(check(f"synthetic week 8: SAME weakening direction confirmed 2nd time -> Weakening fires (got {r8['lifecycle_state']}, streak={r8['streak_count']})", r8["lifecycle_state"] == "Weakening" and r8["streak_count"] == 2))

    # Two consecutive real misses -- archives.
    def _miss(week):
        global seq_history
        result = apply_lifecycle([], seq_history, seq_family, 2099, week)
        seq_history = result["updated_history"]
        return result["history_rows"]

    m1 = _miss(9)
    results.append(check(
        f"synthetic week 9: first miss -- PAUSES, does not reset (state carries forward as Weakening, miss=1) (got {m1[0] if m1 else None})",
        len(m1) == 1 and m1[0]["lifecycle_state"] == "Weakening" and m1[0]["miss_count"] == 1,
    ))
    m2 = _miss(10)
    results.append(check(
        f"synthetic week 10: SECOND consecutive miss -> Archived fires exactly here (got {m2[0] if m2 else None})",
        len(m2) == 1 and m2[0]["lifecycle_state"] == "Archived",
    ))
    m3 = _miss(11)
    results.append(check(
        "synthetic week 11: already Archived -- NO new history row at all, the approved bounded exception (stop re-writing 'still archived')",
        m3 == [],
    ))

    # ============================================================
    # Methodology re-baseline guard (dynamic trend-window change,
    # 2026-09) — synthetic, real threshold, constructed values chosen
    # to prove the guard fires exactly on a maturity transition and
    # stands down again the moment maturity stops changing.
    # ============================================================
    rb_history = {}
    rb_family = "defensive_trends"

    def _run_rb(value, week, maturity):
        global rb_history
        story = {
            "entity": {"type": "defense", "team": "RBT", "position_group": "RB"},
            "primary_signal": {"name": "defensive_matchup_vulnerability", "value": value},
            "trend_strength": value,
            "methodology_maturity": maturity,
        }
        result = apply_lifecycle([story], rb_history, rb_family, 2098, week)
        rb_history = result["updated_history"]
        return result["history_rows"][0]

    rb1 = _run_rb(40.0, 1, "thin")
    results.append(check(f"re-baseline week 1: first appearance -> Detected, methodology_maturity persisted (got {rb1['lifecycle_state']}, maturity={rb1['methodology_maturity']})", rb1["lifecycle_state"] == "Detected" and rb1["methodology_maturity"] == "thin"))

    rb2 = _run_rb(42.0, 2, "thin")
    results.append(check(f"re-baseline week 2: SAME maturity ('thin'), small move -- ordinary comparison, still Detected (got {rb2['lifecycle_state']})", rb2["lifecycle_state"] == "Detected"))

    # Week 3: maturity transitions thin->developing. Without the guard,
    # delta = 90.0 - avg(40,42)=41.0 -> 49.0, well past threshold(21.7)
    # -- would read as a real qualifying Strengthening move. The guard
    # must suppress that: no direction, no Strengthening/Weakening,
    # this appearance still counts (appearance_count=3 -> ACTIVE_
    # APPEARANCE_COUNT reached -> falls to Active, not Detected, not a
    # directional state), and recent_values reseeds to JUST this value.
    rb3 = _run_rb(90.0, 3, "developing")
    results.append(check(
        f"re-baseline week 3 (thin->developing transition): NOT Strengthening/Weakening despite a delta (49.0) "
        f"that would have qualified against the old baseline -- the methodology changed, not (provably) the "
        f"signal (got lifecycle_state={rb3['lifecycle_state']}, pending_direction={rb3['pending_direction']}, streak={rb3['streak_count']})",
        rb3["lifecycle_state"] not in ("Strengthening", "Weakening") and rb3["pending_direction"] is None and rb3["streak_count"] == 0,
    ))
    results.append(check(
        f"re-baseline week 3: still a REAL counted appearance (reuses the existing Active/Detected fallback, "
        f"no new lifecycle_state invented -- approved decision) (got {rb3['lifecycle_state']}, appearance_count={rb3['appearance_count']})",
        rb3["lifecycle_state"] == "Active" and rb3["appearance_count"] == 3,
    ))
    results.append(check(
        "re-baseline week 3: recent_values reseeded to JUST this observation ([90.0]), discarding the old "
        "thin-methodology baseline ([40.0, 42.0]) entirely",
        rb_history[(rb_family, "RBT:RB", "defensive_matchup_vulnerability")]["recent_values"] == [90.0],
    ))

    # Week 4: SAME maturity as week 3 ("developing") -- normal
    # comparison resumes on its own, no special handling needed. First
    # qualifying move off the REAL reseeded baseline (90.0).
    rb4 = _run_rb(115.0, 4, "developing")
    results.append(check(
        f"re-baseline week 4 (maturity unchanged): normal comparison resumed -- a real qualifying move off the "
        f"reseeded baseline (delta=25.0>=21.7), first confirmation, not yet Strengthening (got {rb4['lifecycle_state']}, pending={rb4['pending_direction']}, streak={rb4['streak_count']})",
        rb4["pending_direction"] == "Strengthening" and rb4["streak_count"] == 1 and rb4["lifecycle_state"] != "Strengthening",
    ))

    # Week 5: second consecutive real Strengthening confirmation off the
    # new baseline -- proves the guard didn't leave comparison
    # permanently disabled, only suppressed it for the one transition
    # week.
    rb5 = _run_rb(140.0, 5, "developing")
    results.append(check(
        f"re-baseline week 5: SAME direction confirmed a 2nd consecutive time off the reseeded baseline -> "
        f"Strengthening fires normally, proving the guard was a one-week suppression, not a lasting change "
        f"(got {rb5['lifecycle_state']}, streak={rb5['streak_count']})",
        rb5["lifecycle_state"] == "Strengthening" and rb5["streak_count"] == 2,
    ))

    # Week 6: a SECOND transition (developing->confirmed) that ALSO
    # happens to be a sanity-failed (NaN) reading -- the specific edge
    # case combination flagged during planning. recent_values must be
    # left UNCHANGED here (same as an ordinary NaN week), never
    # reseeded with a NaN/garbage value.
    rb6 = _run_rb(float("nan"), 6, "confirmed")
    results.append(check(
        f"re-baseline week 6 (developing->confirmed transition, ALSO a NaN reading): no directional claim, "
        f"still a real counted appearance (got {rb6['lifecycle_state']}, pending={rb6['pending_direction']})",
        rb6["pending_direction"] is None and rb6["lifecycle_state"] not in ("Strengthening", "Weakening"),
    ))
    results.append(check(
        "re-baseline week 6: recent_values left UNCHANGED ([90.0, 115.0, 140.0]) -- a NaN reading never reseeds "
        "the baseline, transition or not, matching the ordinary NaN-handling precedent exactly",
        rb_history[(rb_family, "RBT:RB", "defensive_matchup_vulnerability")]["recent_values"] == [90.0, 115.0, 140.0],
    ))

    # Week 7: maturity unchanged from week 6 ("confirmed") -- normal
    # comparison resumes again, against the REAL (never-corrupted)
    # baseline the NaN week correctly left intact.
    rb7 = _run_rb(150.0, 7, "confirmed")
    results.append(check(
        f"re-baseline week 7 (maturity unchanged, post-NaN): comparison resumes against the real, uncorrupted "
        f"baseline (avg(90,115,140)=115.0, delta=35.0>=threshold) -- a genuinely new qualifying move, not yet "
        f"confirmed since week 6 carried no pending direction to match (got {rb7['lifecycle_state']}, pending={rb7['pending_direction']}, streak={rb7['streak_count']})",
        rb7["pending_direction"] == "Strengthening" and rb7["streak_count"] == 1,
    ))

    # ============================================================
    # REAL DATA: NYJ RB, Defensive Trends, real 2025 season (the known
    # climbing case referenced in test_defensive_trends.py).
    # ============================================================
    if not WEEKLY_PATH.exists():
        print("(skipped the real NYJ RB validation — player_redzone_weekly.csv not present in this environment)")
    else:
        from defensive_trends import build_defensive_trends_stories
        weekly = pd.read_csv(WEEKLY_PATH)
        real_history = {}
        real_rows = {}
        print("\nReal NYJ RB defensive_trends lifecycle progression, 2025 season:")
        for week in range(1, 19):
            stories, _ = build_defensive_trends_stories(weekly, 2025, week)
            nyj_rb = [s for s in stories if s["entity"].get("team") == "NYJ" and s["entity"].get("position_group") == "RB"]
            result = apply_lifecycle(nyj_rb, real_history, "defensive_trends", 2025, week)
            real_history = result["updated_history"]
            for r in result["history_rows"]:
                if r["entity_key"] == "NYJ:RB":
                    real_rows[week] = r
                    print(f"  week {week:2d}: trend_strength={r['trend_strength']} primary_signal={r['primary_signal_value']} "
                          f"-> {r['lifecycle_state']} (streak={r['streak_count']}, miss={r['miss_count']})")

        results.append(check(
            "real NYJ RB story first appears at week 15 (the family's OWN trend_threshold gate, not a lifecycle bug — "
            "confirmed by reading build_defensive_trends_stories directly) -> Detected",
            real_rows.get(15, {}).get("lifecycle_state") == "Detected",
        ))
        results.append(check(
            "real week 16 (a genuine bye/gap — no NYJ RB story that week) correctly PAUSES, not resets: "
            "state stays Detected, miss_count=1, NOT archived or dropped",
            real_rows.get(16, {}).get("lifecycle_state") == "Detected" and real_rows.get(16, {}).get("miss_count") == 1,
        ))
        results.append(check(
            "real week 17 (reappears after the gap) correctly resumes with miss_count reset to 0",
            real_rows.get(17, {}).get("miss_count") == 0,
        ))
        results.append(check(
            "real week 18: third real appearance (weeks 15/17/18), no further qualifying directional move "
            "(the real climb had already leveled off near its real peak by week 15) -> settles to Active, "
            "the honest 'this is now an established real finding' read, not stuck on Detected",
            real_rows.get(18, {}).get("lifecycle_state") == "Active",
        ))

    # ============================================================
    # REAL DATA: Coaching Trends multi-signal independence — the exact
    # collision entity_key_for's own docstring warns about (all three
    # team_tendencies.py detectors share one entity, {"type":"team",
    # "team":...}), proven here with a real team's real 2025 season
    # rather than just asserted from the identity-key design alone.
    # ============================================================
    try:
        import nfl_data_py as nfl
        pbp2025 = nfl.import_pbp_data([2025], downcast=True)
    except Exception as e:
        print(f"(skipped the real Coaching Trends multi-signal validation — could not pull real pbp data: {e}. "
              f"Try: export SSL_CERT_FILE=$(python3 -m certifi))")
    else:
        from team_tendencies import (
            build_fourth_down_aggressiveness_stories, build_pace_stories, build_redzone_play_calling_stories,
        )
        weekly = pd.read_csv(WEEKLY_PATH) if WEEKLY_PATH.exists() else None
        if weekly is None:
            print("(skipped the real Coaching Trends multi-signal validation — player_redzone_weekly.csv not present)")
        else:
            ct_history = {}
            ct_rows = {}  # week -> {signal_name: row}
            print("\nReal DEN coaching_trends lifecycle progression, 2025 season (three independent signals, one shared entity):")
            for week in range(1, 19):
                stories = (
                    [s for s in build_redzone_play_calling_stories(pbp2025, weekly, 2025, week) if s["entity"]["team"] == "DEN"]
                    + [s for s in build_fourth_down_aggressiveness_stories(pbp2025, weekly, 2025, week) if s["entity"]["team"] == "DEN"]
                    + [s for s in build_pace_stories(pbp2025, weekly, 2025, week) if s["entity"]["team"] == "DEN"]
                )
                result = apply_lifecycle(stories, ct_history, "coaching_trends", 2025, week)
                ct_history = result["updated_history"]
                for r in result["history_rows"]:
                    ct_rows.setdefault(week, {})[r["primary_signal_name"]] = r
                    print(f"  week {week:2d}: {r['primary_signal_name']:26s} -> {r['lifecycle_state']:12s} (streak={r['streak_count']}, miss={r['miss_count']})")

            results.append(check(
                "real week 12: pace_score archives (2 consecutive real misses, weeks 11 and 12) while "
                "redzone_run_tendency — SAME real team, SAME real week — is still alive at Detected: "
                "proof the shared-entity collision is genuinely resolved by primary_signal_name, not just "
                "asserted by the identity-key design",
                ct_rows.get(12, {}).get("pace_score", {}).get("lifecycle_state") == "Archived"
                and ct_rows.get(12, {}).get("redzone_run_tendency", {}).get("lifecycle_state") == "Detected",
            ))
            results.append(check(
                "real week 14: redzone_run_tendency has already graduated to Active (its 3rd real appearance: "
                "weeks 11/13/14) while fourth_down_aggressiveness — SAME real team, SAME real week, first real "
                "appearance — is Detected: two signals on one entity at two different lifecycle states simultaneously",
                ct_rows.get(14, {}).get("redzone_run_tendency", {}).get("lifecycle_state") == "Active"
                and ct_rows.get(14, {}).get("fourth_down_aggressiveness", {}).get("lifecycle_state") == "Detected",
            ))
            results.append(check(
                "real week 17: redzone_run_tendency and fourth_down_aggressiveness both reach Archived here, but "
                "independently -- each from its OWN real 2-consecutive-miss streak (weeks 16/17), not because they "
                "share any state",
                ct_rows.get(17, {}).get("redzone_run_tendency", {}).get("lifecycle_state") == "Archived"
                and ct_rows.get(17, {}).get("fourth_down_aggressiveness", {}).get("lifecycle_state") == "Archived",
            ))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
