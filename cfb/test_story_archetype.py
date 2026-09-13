"""
Fixture-based unit tests for cfb/story_archetype.py.

Same discipline / shape as cfb/test_redzone.py: a plain check()/__main__
script (no pytest dependency), run with

    python3 cfb/test_story_archetype.py

Covers: behavior-shelf locking (and its priority order when a row is on
more than one), independent population-shelf resolution across all 4
candidate signals (including the FLOOR and the MISMATCH tie-break),
Saturday Poster fallback, shelf-context motifs (Top 25 rank + conference
tendency, including a player on both at once), and the tint HSL clamp
(both bounds, plus the null-on-unresolved path).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from story_archetype import (
    FLOOR,
    get_cfb_tint_profile,
    resolve_cfb_archetype,
    resolve_cfb_shelf_context,
)


def _base_row(**overrides) -> dict:
    row = {
        "player_id": "p1",
        "team_id": 100,
        "td_opportunity": 40.0,
        "td_opportunity_gated": True,
        "role_momentum": 40.0,
        "role_momentum_completeness": 0.0,
        "target_magnets": 40.0,
        "target_magnets_gated": True,
        "defensive_matchup_vulnerability": 40.0,
        "defensive_matchup_completeness": 0.0,
        "shelves": [],
    }
    row.update(overrides)
    return row


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return bool(condition)


if __name__ == "__main__":
    results = []

    # --- Behavior-shelf locking: no ambiguity, shelf tells the story ---
    row = _base_row(td_opportunity=61.4, shelves=["goal_line_favorites"])
    out = resolve_cfb_archetype(row)
    results.append(check("Goal-Line Favorites locks to GOAL_LINE", out["archetype"] == "GOAL_LINE"))
    results.append(check("locked_by_shelf reports the locking shelf", out["locked_by_shelf"] == "goal_line_favorites"))
    results.append(check("locked confidence is the row's own td_opportunity", out["confidence"] == 61.4))

    row = _base_row(role_momentum=88.2, shelves=["workhorses"])
    out = resolve_cfb_archetype(row)
    results.append(check("Workhorses locks to WORKHORSE", out["archetype"] == "WORKHORSE"))

    row = _base_row(target_magnets=93.0, shelves=["target_magnets"])
    out = resolve_cfb_archetype(row)
    results.append(check("Target Magnets locks to TARGET_MAGNET", out["archetype"] == "TARGET_MAGNET"))

    # A behavior lock wins even when the locked signal's own score is thin
    # -- shelf placement alone is the trigger, not a re-check of the score.
    row = _base_row(td_opportunity=12.0, shelves=["goal_line_favorites"])
    out = resolve_cfb_archetype(row)
    results.append(check("behavior lock ignores its own score/floor", out["archetype"] == "GOAL_LINE"))

    # A behavior lock wins over a population shelf also present (the
    # design decision this whole module is built around).
    row = _base_row(td_opportunity=61.4, shelves=["goal_line_favorites", "sec_td_watch"])
    out = resolve_cfb_archetype(row)
    results.append(check("behavior shelf beats a population shelf on the same row", out["archetype"] == "GOAL_LINE"))

    # Two behavior shelves at once -- priority order (goal_line_favorites
    # before workhorses before target_magnets).
    row = _base_row(shelves=["workhorses", "goal_line_favorites"])
    out = resolve_cfb_archetype(row)
    results.append(check("multi-behavior-shelf row uses priority order", out["locked_by_shelf"] == "goal_line_favorites"))

    # --- Independent resolution (population shelves / no shelf at all) ---
    row = _base_row(
        td_opportunity=70.0, td_opportunity_gated=False,
        role_momentum=60.0, role_momentum_completeness=100.0,
        shelves=["sec_td_watch"],
    )
    out = resolve_cfb_archetype(row)
    results.append(check("independent resolution picks the highest eligible score", out["archetype"] == "GOAL_LINE"))
    results.append(check("independent confidence is the winning score", out["confidence"] == 70.0))
    results.append(check("independent resolution reports no lock", out["locked_by_shelf"] is None))

    # An ineligible (gated) signal is never a candidate even with the
    # highest raw number.
    row = _base_row(
        td_opportunity=99.0, td_opportunity_gated=True,  # gated -- excluded despite the highest score
        role_momentum=60.0, role_momentum_completeness=100.0,
        shelves=["top25_td_watch"],
    )
    out = resolve_cfb_archetype(row)
    results.append(check("a gated signal is never a winning candidate", out["archetype"] == "WORKHORSE"))

    # FLOOR: a real, eligible, non-thin score that still sits below FLOOR
    # loses to Saturday Poster.
    row = _base_row(
        td_opportunity=FLOOR - 0.1, td_opportunity_gated=False,
        role_momentum=40.0, role_momentum_completeness=100.0,
        shelves=["acc_td_watch"],
    )
    out = resolve_cfb_archetype(row)
    results.append(check("below-FLOOR eligible signal falls to SATURDAY_POSTER", out["archetype"] == "SATURDAY_POSTER"))
    results.append(check("SATURDAY_POSTER carries no confidence", out["confidence"] is None))
    results.append(check("SATURDAY_POSTER carries no governing signal", out["governing_signal"] is None))

    # Nothing eligible at all -> Saturday Poster.
    row = _base_row(shelves=["big12_td_watch"])
    out = resolve_cfb_archetype(row)
    results.append(check("no eligible signal at all -> SATURDAY_POSTER", out["archetype"] == "SATURDAY_POSTER"))

    # MISMATCH: eligible and highest -> wins, exactly like the other 3.
    row = _base_row(
        defensive_matchup_vulnerability=80.0, defensive_matchup_completeness=100.0,
        shelves=["big_ten_td_watch"],
    )
    out = resolve_cfb_archetype(row)
    results.append(check("MISMATCH wins when it's the highest eligible signal", out["archetype"] == "MISMATCH"))

    # MISMATCH tie-break: exact tie with GOAL_LINE -> GOAL_LINE wins
    # (earlier in _ARCHETYPE_SPECS' priority order).
    row = _base_row(
        td_opportunity=75.0, td_opportunity_gated=False,
        defensive_matchup_vulnerability=75.0, defensive_matchup_completeness=100.0,
        shelves=["sec_td_watch"],
    )
    out = resolve_cfb_archetype(row)
    results.append(check("tie between GOAL_LINE and MISMATCH favors GOAL_LINE", out["archetype"] == "GOAL_LINE"))

    # A completely degenerate defensive_matchup_completeness (0) excludes
    # MISMATCH even with a high raw score.
    row = _base_row(
        defensive_matchup_vulnerability=90.0, defensive_matchup_completeness=0.0,
        shelves=["acc_td_watch"],
    )
    out = resolve_cfb_archetype(row)
    results.append(check("defensive_matchup_completeness == 0 excludes MISMATCH", out["archetype"] == "SATURDAY_POSTER"))

    # Explicit `shelves` argument overrides row["shelves"].
    row = _base_row(td_opportunity=61.4, shelves=["workhorses"])
    out = resolve_cfb_archetype(row, shelves=["goal_line_favorites"])
    results.append(check("explicit shelves= param overrides row['shelves']", out["archetype"] == "GOAL_LINE"))

    # --- Shelf-context motifs ---
    ap_ranks = {100: {"rank": 7, "school": "Team A", "conference": "SEC"}}
    team_conference = {100: "SEC"}

    ctx = resolve_cfb_shelf_context(["top25_td_watch"], 100, ap_ranks, team_conference)
    results.append(check("Top 25 shelf produces a TOP25_RANK motif", ctx == [{"motif": "TOP25_RANK", "rank": 7}]))

    ctx = resolve_cfb_shelf_context(["sec_td_watch"], 100, ap_ranks, team_conference)
    results.append(
        check(
            "conference shelf produces a CONFERENCE_TENDENCY motif",
            ctx == [{"motif": "CONFERENCE_TENDENCY", "conference": "SEC"}],
        )
    )

    ctx = resolve_cfb_shelf_context(["top25_td_watch", "sec_td_watch"], 100, ap_ranks, team_conference)
    results.append(
        check(
            "a player on both Top 25 and a conference shelf gets both motifs",
            ctx == [{"motif": "TOP25_RANK", "rank": 7}, {"motif": "CONFERENCE_TENDENCY", "conference": "SEC"}],
        )
    )

    ctx = resolve_cfb_shelf_context(["goal_line_favorites"], 100, ap_ranks, team_conference)
    results.append(check("a behavior-shelf-only row gets no shelf-context motif", ctx == []))

    # team_id not in ap_ranks at all (not this week's Top 25) -> no motif
    # even if "top25_td_watch" were (incorrectly) passed in.
    ctx = resolve_cfb_shelf_context(["top25_td_watch"], 999, ap_ranks, team_conference)
    results.append(check("unresolvable rank produces no TOP25_RANK motif", ctx == []))

    # --- Team identity tint ---
    team_colors = {
        100: ("#000000", "#ffffff"),  # near-black / near-white -- exercises both clamp bounds
        200: ("#8B0000", "#00008B"),  # ordinary saturated colors -- exercises the saturation cap path lightly
    }

    tint = get_cfb_tint_profile(100, team_colors)
    results.append(check("tint resolves for a known team", tint is not None))
    from story_archetype import _hex_to_hsl  # internal, test-only introspection

    _, _, l_primary = _hex_to_hsl(tint["primary"])
    _, _, l_secondary = _hex_to_hsl(tint["secondary"])
    results.append(check("near-black primary is floored to MIN_LIGHTNESS", abs(l_primary - 0.22) < 0.01))
    results.append(check("near-white secondary is capped to MAX_LIGHTNESS", abs(l_secondary - 0.74) < 0.01))

    results.append(check("tint is None for an unresolved team_id", get_cfb_tint_profile(9999, team_colors) is None))
    results.append(check("tint is None for a None team_id", get_cfb_tint_profile(None, team_colors) is None))

    passed = sum(results)
    print(f"{passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)
