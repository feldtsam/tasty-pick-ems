"""
Tests for the MASKED-HEAT TEMPLATE FIX (2026-10-01) in shelves.red_zone_
story and shelves.position_story -- both functions used to print a
percentile-trend sentence (and, for position_story, a directional
headline) straight off touch_share_trend_pct/snap_share_trend_pct (or
their _role variants) with no check for whether those fields were a
real measured trend or the neutral-50 masked-fallback sentinel
scoring._trend_delta produces on too little games-played history.
Confirmed on real 2026 data before this fix: Pat Freiermuth (wk1) and
Blake Whiteheart (wk2) both read "trending 50th percentile" on both
fields while both were masked.

Synthetic, controlled fixtures, same convention as test_shelves.py's
own DEFAULTS/make_row pattern -- these prove the MECHANISM (masked
fields swap in the honest sentence; real fields are completely
unaffected), not a re-validation of the real-data cases already
confirmed directly against live rows.

Run: python3 nfl/test_shelves_story_templates.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

from shelves import position_story, red_zone_story


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return bool(condition)


RED_ZONE_DEFAULTS = {
    "proven_heat": 20.0, "emerging_heat": 50.0,
    "touch_share_trend_pct": 50.0, "snap_share_trend_pct": 50.0,
    # Rolling per-red-zone-game means from add_rolling_windows -- the
    # production inputs red_zone_story reads (2026-10). Zero = no
    # qualifying production.
    "i10_touches_last3": 0.0, "gl_touches_last3": 0.0, "rz_tds_last3": 0.0,
}

POSITION_DEFAULTS = {
    "role_trend": 50.0, "external_opportunity": 0.0,
    "touch_share_trend_pct_role": 50.0, "snap_share_trend_pct_role": 50.0,
    "depth_chart_movement_pct": 50.0,
    "snap_share_last1": 0.30, "snap_share_season_avg": 0.30,
    "ahead_injury_statuses": [],
}


def rz_row(**overrides):
    row = dict(RED_ZONE_DEFAULTS)
    row.update(overrides)
    return pd.Series(row)


def pos_row(**overrides):
    row = dict(POSITION_DEFAULTS)
    row.update(overrides)
    return pd.Series(row)


if __name__ == "__main__":
    results = []

    # ============================================================
    # red_zone_story
    # ============================================================

    # --- both trend fields masked (the real Freiermuth/Whiteheart shape):
    # no "climbing" headline, no percentile text, honest fallback instead.
    row = rz_row()  # both trend fields at the neutral-50 sentinel
    story = red_zone_story(row)
    results.append(check(
        "red_zone_story: both trend fields masked -> honest headline, not 'climbing'",
        story["headline"] == "The early role hasn't shown enough yet to call a direction.",
    ))
    results.append(check(
        "red_zone_story: both trend fields masked -> honest why_this_hits, no percentile text, no '50'",
        story["why_this_hits"] == (
            "Touch-share and snap-share trend data isn't reliable yet -- "
            "too early in the season to read this role's direction."
        ) and "percentile" not in story["why_this_hits"] and "50" not in story["why_this_hits"],
    ))

    # --- only ONE of the two masked -- still drops the whole sentence
    # (a half-true sentence citing one real number next to one fabricated
    # one is worse than an honest fallback for both).
    row = rz_row(touch_share_trend_pct=72.0)  # real, snap_share_trend_pct still masked at 50.0
    story = red_zone_story(row)
    results.append(check(
        "red_zone_story: only ONE trend field masked still triggers the honest fallback (not a half-true sentence)",
        story["headline"] == "The early role hasn't shown enough yet to call a direction."
        and "72" not in story["why_this_hits"],
    ))

    # --- neither masked -- real trend values, old percentile sentence
    # untouched (confirms this fix changed behavior ONLY for the masked
    # case, not the real-signal path).
    row = rz_row(touch_share_trend_pct=71.0, snap_share_trend_pct=63.0)
    story = red_zone_story(row)
    results.append(check(
        "red_zone_story: real (unmasked) trend values still get the real percentile sentence, unchanged",
        story["headline"] == "The opportunity is climbing before the touchdowns have arrived."
        and story["why_this_hits"] == "Red-zone touch share trending 71th percentile, snap share trending 63th percentile",
    ))

    # ============================================================
    # Production inputs (2026-10): the production branch reads the
    # rolling per-red-zone-game means (gl/i10/rz_tds _last3) the row
    # already carries, not the _trail3 sums that are NaN on the live
    # one-week stub frame. Level language only, no trend word.
    # ============================================================
    PRODUCTION_WHY = (
        "Averaging 1 goal-line touch, 1.5 inside-the-10 touches, and 1.5 red-zone touchdowns "
        "per red-zone game over his most recent red-zone games (up to three)"
    )
    # (1) populated production -> production branch, level-based headline
    row = rz_row(proven_heat=95.7, emerging_heat=50.0, i10_touches_last3=1.5, gl_touches_last3=1.0, rz_tds_last3=1.5)
    story = red_zone_story(row)
    results.append(check(
        "red_zone_story: populated rolling production -> level-based goal-line headline (the 2026 wk7 Foster Moreau shape)",
        story["headline"] == "He's getting goal-line opportunities." and story["why_this_hits"] == PRODUCTION_WHY,
    ))
    row = rz_row(proven_heat=71.9, emerging_heat=50.0, i10_touches_last3=0.5, gl_touches_last3=0.0, rz_tds_last3=1.0)
    story = red_zone_story(row)
    results.append(check(
        "red_zone_story: inside-10 touches but no goal-line touches -> inside-the-10 wording, never a goal-line claim",
        story["headline"] == "He's getting opportunities inside the 10."
        and story["why_this_hits"] == (
            "Averaging 0 goal-line touches, 0.5 inside-the-10 touches, and 1 red-zone touchdown "
            "per red-zone game over his most recent red-zone games (up to three)"
        ),
    ))
    row = rz_row(proven_heat=80.0, emerging_heat=20.0, i10_touches_trail3=2.0, gl_touches_trail3=1.0, rz_tds_trail3=1.0)
    story = red_zone_story(row)
    results.append(check(
        "red_zone_story: _trail3 sums alone (the NaN-on-live-path inputs) no longer open the production branch",
        story["headline"] == "The early role hasn't shown enough yet to call a direction.",
    ))
    results.append(check(
        "red_zone_story: the old growth headline is gone and no trend-lexicon word survives in any production headline",
        all(w not in red_zone_story(rz_row(proven_heat=90.0, i10_touches_last3=v, gl_touches_last3=g, rz_tds_last3=1.0))["headline"]
            for v, g in ((1.0, 1.0), (0.5, 0.0))
            for w in ("becoming", "climbing", "rising", "building", "growing", "trending")),
    ))

    # (2) no qualifying production keeps the masked fallback
    for label, overrides in (
        ("all three means at zero", dict(proven_heat=90.0, i10_touches_last3=0.0, gl_touches_last3=0.0, rz_tds_last3=0.0)),
        ("a red-zone TD from outside the 10 with no inside-10 touch", dict(proven_heat=90.0, i10_touches_last3=0.0, gl_touches_last3=0.0, rz_tds_last3=1.0)),
        ("rolling means absent from the row (NaN)", dict(proven_heat=90.0, i10_touches_last3=float("nan"), gl_touches_last3=float("nan"), rz_tds_last3=float("nan"))),
    ):
        story = red_zone_story(rz_row(**overrides))
        results.append(check(
            f"red_zone_story: no qualifying production ({label}) + masked trends -> masked fallback stays",
            story["headline"] == "The early role hasn't shown enough yet to call a direction.",
        ))

    # (3)+(4) the real validators: no trend claim under masked evidence;
    # every number in the why-text is a value on the row; the window
    # and denominator are named honestly.
    sys.path.insert(0, str(Path(__file__).resolve().parent / "content_writer"))
    from factual_validation import find_trend_claims, trend_evidence_for_row, validate_trend_claims  # noqa: E402
    from card_writer_common import validate_numeric_grounding  # noqa: E402
    from nfl_writer_common import nfl_tolerance_for_key  # noqa: E402
    checked = 0
    ok_trend = ok_numbers = ok_window = True
    for i10, gl, tds in ((1.5, 1.0, 1.5), (0.5, 0.0, 1.0), (1.0, 0.6667, 0.6667), (1.0, 1.0, 1.0), (0.5, 0.5, 0.5)):
        row = rz_row(proven_heat=90.0, emerging_heat=50.0, i10_touches_last3=i10, gl_touches_last3=gl, rz_tds_last3=tds)
        story = red_zone_story(row)
        evidence = trend_evidence_for_row(row, "Red Zone Trends")
        ok_trend &= evidence["status"] == "masked" and find_trend_claims(story["headline"]) == [] and validate_trend_claims(
            {"title": story["headline"], "story": story["why_this_hits"]}, evidence) == []
        facts = {k: float(v) for k, v in row.items()}
        ok_numbers &= validate_numeric_grounding(
            [{"reason_text": story["why_this_hits"], "source_fact_keys": ["gl_touches_last3", "i10_touches_last3", "rz_tds_last3"]}],
            facts, nfl_tolerance_for_key) == []
        ok_window &= "per red-zone game" in story["why_this_hits"] and "up to three" in story["why_this_hits"] and "last three games" not in story["why_this_hits"]
        checked += 1
    results.append(check(f"red_zone_story: masked trend inputs never yield a trend claim in headline or why-text ({checked} production rows)", ok_trend))
    results.append(check(f"red_zone_story: every number in the why-text is a row value within numeric-grounding tolerance ({checked} rows, incl. 2/3 means)", ok_numbers))
    results.append(check("red_zone_story: why-text names the real window and denominator (per red-zone game, up to three), never 'last three games'", ok_window))

    # (5) scoring / eligibility untouched: red_zone_story is a pure read
    # of the row, and build_red_zone_trends' card membership and order on
    # a fixed fixture is the same before and after this change (expected
    # values computed on main before the edit).
    from shelves import build_red_zone_trends  # noqa: E402
    row = rz_row(proven_heat=95.7, emerging_heat=50.0, i10_touches_last3=1.5, gl_touches_last3=1.0, rz_tds_last3=1.5)
    before = row.copy()
    red_zone_story(row)
    results.append(check("red_zone_story: does not mutate the row (no scoring field touched)", row.equals(before)))
    # Same fixture helpers test_shelves.py already uses for this function.
    from test_shelves import make_row, make_weekly  # noqa: E402
    fixture = make_weekly([
        make_row(pid, td_opportunity=td, proven_heat=ph, i10_touches_last3=i10, gl_touches_last3=gl, rz_tds_last3=tds)
        for pid, td, ph, i10, gl, tds in (
            ("P0", 96.4, 95.7, 1.5, 1.0, 1.5), ("P1", 81.9, 81.9, 1.0, 0.5, 1.0),
            ("P2", 65.6, 65.6, 0.0, 0.0, 1.0), ("P3", 40.0, 30.0, 0.0, 0.0, 0.0),
        )
    ])
    cards = build_red_zone_trends(fixture)["cards"]
    results.append(check(
        "build_red_zone_trends: card membership and rank order on a fixed fixture are exactly what main produced (P0..P3, ranks 1-4)",
        [(c["player_id"], c.get("rank")) for c in cards] == [("P0", 1), ("P1", 2), ("P2", 3), ("P3", 4)],
    ))
    results.append(check(
        "build_red_zone_trends: only the headline differs -- P2 (a red-zone TD but no inside-10 touch) now keeps the masked fallback instead of the old growth claim",
        cards[0]["headline"] == "He's getting goal-line opportunities."
        and cards[2]["headline"] == "The early role hasn't shown enough yet to call a direction."
        and "becoming" not in " ".join(c["headline"] for c in cards),
    ))

    # ============================================================
    # position_story
    # ============================================================

    # --- role_trend wins, snap share NOT really up, BOTH _role trend
    # fields masked (the real Kyle Juszczyk / Travis Kelce shape): no
    # "may already be changing hands" headline, no percentile text.
    row = pos_row()
    story = position_story(row, "RB")
    results.append(check(
        "position_story: masked _role trend fields -> honest headline, not 'may already be changing hands'",
        story["headline"] == "This RB role hasn't shown enough yet to call a direction.",
    ))
    results.append(check(
        "position_story: masked _role trend fields -> honest why_this_hits, no percentile text",
        story["why_this_hits"] == (
            "Touch-share and snap-share trend data isn't reliable yet -- "
            "too early in the season to read this role's direction."
        ) and "percentile" not in story["why_this_hits"],
    ))
    results.append(check(
        "position_story: headline correctly carries the real position label (TE, not RB)",
        position_story(row, "TE")["headline"] == "This TE role hasn't shown enough yet to call a direction.",
    ))

    # --- only one of the two _role fields masked -- still the honest path
    row = pos_row(touch_share_trend_pct_role=68.0)  # snap_share_trend_pct_role still masked
    story = position_story(row, "WR")
    results.append(check(
        "position_story: only ONE _role trend field masked still triggers the honest fallback",
        story["headline"] == "This WR role hasn't shown enough yet to call a direction."
        and "68" not in story["why_this_hits"],
    ))

    # --- neither masked -- real trend values, old percentile sentence
    # (including depth-chart movement) untouched.
    row = pos_row(touch_share_trend_pct_role=74.0, snap_share_trend_pct_role=81.0, depth_chart_movement_pct=66.0)
    story = position_story(row, "RB")
    results.append(check(
        "position_story: real (unmasked) _role trend values still get the real percentile sentence, unchanged",
        story["headline"] == "This RB role may already be changing hands."
        and story["why_this_hits"] == "Touch-share trend 74th percentile, snap-share trend 81th percentile, depth-chart movement 66th percentile",
    ))

    # --- snap_really_up branch is completely untouched by this fix --
    # it never reads either _role trend field, real snap share wins outright.
    row = pos_row(snap_share_last1=0.55, snap_share_season_avg=0.30)
    story = position_story(row, "WR")
    results.append(check(
        "position_story: a real, measured snap-share increase still wins outright, unaffected by the masked-trend fix",
        story["headline"] == "This WR role may already be changing hands."
        and story["why_this_hits"] == "Snap share 30% (season) → 55% (most recent)",
    ))

    # --- external_opportunity branch (role_trend losing) is completely
    # untouched -- different code path entirely, never reads the _role
    # trend fields either.
    row = pos_row(role_trend=20.0, external_opportunity=40.0, ahead_injury_statuses=["Questionable"])
    story = position_story(row, "RB")
    results.append(check(
        "position_story: the external_opportunity branch (role_trend losing) is unaffected by the masked-trend fix",
        story["headline"] == "An opportunity may be opening up ahead of him on the RB depth chart."
        and "Questionable" in story["why_this_hits"],
    ))

    # ============================================================
    # Item 3 (2026-10): "climbing" needs an upward delta, not just an
    # unmasked read. Unmasked + delta 0 is "no change", its own wording.
    # ============================================================
    row = rz_row(touch_share_trend_pct=64.0, snap_share_trend_pct=61.0,
                 rz_touch_share_last3=0.2, rz_touch_share_season_avg=0.2,
                 rz_touches_last3=2.0, rz_touches_season_avg=2.0,
                 snap_share_last3=0.5, snap_share_season_avg=0.5)
    story = red_zone_story(row)
    results.append(check(
        "red_zone_story: both trend fields unmasked but every raw delta is 0 -> 'holding steady' headline, no trend word, percentile sentence kept",
        story["headline"] == "The red-zone opportunity is holding steady so far."
        and "climbing" not in story["headline"]
        and story["why_this_hits"] == "Red-zone touch share trending 64th percentile, snap share trending 61th percentile",
    ))
    row = rz_row(touch_share_trend_pct=64.0, snap_share_trend_pct=61.0,
                 rz_touch_share_last3=0.3, rz_touch_share_season_avg=0.2)
    story = red_zone_story(row)
    results.append(check(
        "red_zone_story: unmasked with a real upward raw delta -> the 'climbing' headline",
        story["headline"] == "The opportunity is climbing before the touchdowns have arrived.",
    ))
    row = rz_row(touch_share_trend_pct=64.0, snap_share_trend_pct=61.0,
                 rz_touch_share_last3=0.1, rz_touch_share_season_avg=0.2,
                 rz_touches_last3=1.0, rz_touches_season_avg=2.0,
                 snap_share_last3=0.4, snap_share_season_avg=0.5)
    story = red_zone_story(row)
    results.append(check(
        "red_zone_story: unmasked with every raw delta negative -> never 'climbing'",
        "climbing" not in story["headline"],
    ))

    print()
    p = sum(results)
    print(f"{p}/{len(results)} checks passed")
    raise SystemExit(0 if p == len(results) else 1)
