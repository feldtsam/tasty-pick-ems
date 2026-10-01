"""
Tests for shelves.py's role_signals candidate builders -- previously
uncovered by any test file (test_shelves.py's own docstring scopes itself
to shelf-building mechanism, not these functions). Added for Stage 1,
Masked-Value Handling, alongside the dead-column fix: red_zone_trends_
role_signals and attd_700_plus_role_signals both called _level_candidate
on gl_touches/i10_touches expecting real *_last3/*_season_avg columns
that never existed anywhere in the real weekly pipeline output (confirmed
directly -- see scripts/backfill_redzone.py's own updated add_rolling_
windows() call). These two candidates were dead code, always
value=None/_eligible=False, on every real row, every week, until that
fix landed.

Run: python3 nfl/test_shelves_role_signals.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from shelves import (
    _external_opportunity_candidate,
    _level_candidate,
    _select_role_signals,
    _trend_candidate,
    attd_700_plus_role_signals,
    red_zone_trends_role_signals,
)


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


def _row(**overrides):
    row = {
        "rz_touch_share": 0.15, "rz_touch_share_last3": 0.10, "rz_touch_share_season_avg": 0.10,
        "touch_share_trend_pct": 60.0,
        "rz_touches": 2, "rz_touches_last3": 1.0, "rz_touches_season_avg": 1.0,
        "touch_volume_trend_pct": 55.0,
        "gl_touches": 1, "gl_touches_last3": None, "gl_touches_season_avg": None,
        "i10_touches": 1, "i10_touches_last3": None, "i10_touches_season_avg": None,
        "depth_chart_movement_pct": 50.0,
        "external_opportunity": 0.0,
    }
    row.update(overrides)
    return row


if __name__ == "__main__":
    r = []

    # ============================================================
    # Before the fix (simulated): gl_touches_last3/season_avg absent,
    # same shape as every real row before scripts/backfill_redzone.py's
    # add_rolling_windows() call was extended -- both candidates must be
    # ineligible, not crash.
    # ============================================================
    no_windows = _row()  # gl_touches_last3/i10_touches_last3 both None, matching every real row before the fix
    gl_cand = _level_candidate(no_windows, "gl_touches", "Goal-Line Touches (Recent)", "gl_touches", 3, unit="touches")
    i10_cand = _level_candidate(no_windows, "i10_touches", "Inside-10 Touches (Recent)", "i10_touches", 3, unit="touches")
    r.append(check(
        "gl_touches candidate is ineligible (value=None) when gl_touches_last3 doesn't exist on the row -- the pre-fix dead-column state",
        gl_cand["_eligible"] is False and gl_cand["value"] is None,
    ))
    r.append(check(
        "i10_touches candidate is ineligible (value=None) when i10_touches_last3 doesn't exist on the row -- same pre-fix state",
        i10_cand["_eligible"] is False and i10_cand["value"] is None,
    ))

    # ============================================================
    # After the fix: a real prior game -> gl_touches_last3/season_avg
    # are real numbers (add_rolling_windows' own shift(1)+min_periods=1
    # rolling mean), and the candidate becomes eligible.
    # ============================================================
    with_windows = _row(gl_touches_last3=0.0, gl_touches_season_avg=0.0, i10_touches_last3=1.0, i10_touches_season_avg=1.0)
    gl_cand2 = _level_candidate(with_windows, "gl_touches", "Goal-Line Touches (Recent)", "gl_touches", 3, unit="touches")
    i10_cand2 = _level_candidate(with_windows, "i10_touches", "Inside-10 Touches (Recent)", "i10_touches", 3, unit="touches")
    r.append(check(
        "gl_touches candidate is eligible once gl_touches_last3 is a real number (>=1 real prior game)",
        gl_cand2["_eligible"] is True and gl_cand2["value"] == 0.0,
    ))
    r.append(check(
        "i10_touches candidate is eligible once i10_touches_last3 is a real number",
        i10_cand2["_eligible"] is True and i10_cand2["value"] == 1.0,
    ))

    # ============================================================
    # A genuinely new player (no prior game at all, e.g. real Week 1):
    # add_rolling_windows' shift(1) correctly leaves *_last3/*_season_avg
    # as NaN -- must stay ineligible, never fabricated as a real 0.
    # ============================================================
    week1_no_prior_game = _row(gl_touches_last3=float("nan"), gl_touches_season_avg=float("nan"))
    gl_cand3 = _level_candidate(week1_no_prior_game, "gl_touches", "Goal-Line Touches (Recent)", "gl_touches", 3, unit="touches")
    r.append(check(
        "a real Week-1 row (no prior game, NaN rolling columns) correctly stays ineligible -- not fabricated as a real 0",
        gl_cand3["_eligible"] is False and gl_cand3["value"] is None,
    ))

    # ============================================================
    # End-to-end: red_zone_trends_role_signals / attd_700_plus_role_
    # signals now surface a real gl_touches/i10_touches signal once the
    # underlying columns are real, where before they never could.
    # ============================================================
    real_row = _row(
        gl_touches_last3=0.0, gl_touches_season_avg=0.0,
        i10_touches_last3=1.0, i10_touches_season_avg=1.0,
        touch_share_trend_pct=50.0, touch_volume_trend_pct=50.0,  # both masked -- only gl/i10 should surface
    )
    sigs = red_zone_trends_role_signals(real_row)
    r.append(check(
        "red_zone_trends_role_signals surfaces the i10_touches/gl_touches signals once real, even when the two TREND candidates are masked",
        len(sigs) == 2 and {s["label"] for s in sigs} == {"Inside-10 Touches (Recent)", "Goal-Line Touches (Recent)"},
    ))

    real_row_700 = _row(gl_touches_last3=0.0, gl_touches_season_avg=0.0, depth_chart_movement_pct=50.0, external_opportunity=0.0)
    sigs_700 = attd_700_plus_role_signals(real_row_700)
    r.append(check(
        "attd_700_plus_role_signals surfaces the gl_touches signal once real, even when depth_chart/external_opportunity are both ineligible",
        len(sigs_700) == 1 and sigs_700[0]["label"] == "Goal-Line Touches (Recent)",
    ))

    # ============================================================
    # STILL-FORMING PLACEHOLDER (Step 4): fires only when zero eligible
    # AND at least one candidate is masked; a genuinely-absent-forever
    # pool (every candidate real, complete, and just not noteworthy)
    # must still return [], unchanged.
    # ============================================================
    all_masked = [
        _level_candidate(no_windows, "gl_touches", "Goal-Line Touches (Recent)", "gl_touches", 3, unit="touches"),
    ]
    placeholder = _select_role_signals(all_masked)
    r.append(check(
        "zero eligible, one masked candidate -> single 'still forming' placeholder, not []",
        len(placeholder) == 1 and placeholder[0]["label"] == "Trend still forming: not enough games yet"
        and placeholder[0]["value"] is None and placeholder[0]["delta"] is None and placeholder[0]["unit"] == "",
    ))
    r.append(check(
        "placeholder's unit is '' (empty string), never null -- the real write-route Zod schema requires unit: z.string(), not nullable",
        placeholder[0]["unit"] == "" and placeholder[0]["unit"] is not None,
    ))

    genuinely_absent = [
        _external_opportunity_candidate({"external_opportunity": 0.0}),  # real, confirmed 0 -- not masked, not eligible
    ]
    r.append(check(
        "zero eligible, zero masked (a real, confirmed 'nothing noteworthy' reading) -> [], no placeholder fabricated",
        _select_role_signals(genuinely_absent) == [],
    ))

    missing_external_opportunity = [
        _external_opportunity_candidate({}),  # value never computed at all -- genuinely masked, not a confirmed 0
    ]
    r.append(check(
        "external_opportunity itself missing (never computed) IS masked, unlike a real confirmed 0 -> placeholder fires",
        len(_select_role_signals(missing_external_opportunity)) == 1,
    ))

    eligible_present = [
        _level_candidate(with_windows, "gl_touches", "Goal-Line Touches (Recent)", "gl_touches", 3, unit="touches"),
        _external_opportunity_candidate({"external_opportunity": 0.0}),
    ]
    sel = _select_role_signals(eligible_present)
    r.append(check(
        "at least one real eligible candidate -> normal real signal returned, placeholder never mixed in alongside it",
        len(sel) == 1 and sel[0]["label"] == "Goal-Line Touches (Recent)",
    ))

    print()
    p = sum(r)
    print(f"{p}/{len(r)} checks passed")
    raise SystemExit(0 if p == len(r) else 1)
