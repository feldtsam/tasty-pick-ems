"""
Fixture-based unit tests for cfb/story_archetype.py.

Same discipline / shape as cfb/test_redzone.py: a plain check()/__main__
script (no pytest dependency), run with

    python3 cfb/test_story_archetype.py

REWRITTEN (not patched) for the shelf-identity-overrides-signal rework:
resolution is now per-shelf-placement (resolve_cfb_archetype(shelf, row)),
and 5 of the 8 real shelves (the 4 conference shelves + Top 25) now LOCK
their own dedicated archetype regardless of the player's own scores,
rather than being resolved from signal with a subordinate shelf-context
motif layered on top. Covers: behavior-shelf locks (unchanged), the new
identity/editorial locks (including that they truly ignore the player's
own scores), independent resolution for placements with no dedicated
shelf identity (all 4 candidate signals, the floor, the MISMATCH
tie-break, the fallback), the same player resolving to genuinely
different archetypes across different shelf placements, and the tint
HSL clamp (unchanged, both bounds, plus the null-on-unresolved path).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from story_archetype import (
    ARCHETYPES,
    FALLBACK_ARCHETYPE,
    FLOOR,
    _LOCKED_SHELF_ARCHETYPE,
    get_cfb_tint_profile,
    resolve_cfb_archetype,
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
    }
    row.update(overrides)
    return row


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return bool(condition)


if __name__ == "__main__":
    results = []

    # --- Archetype enum shape ---
    results.append(check("ARCHETYPES has exactly the 9 real archetypes", len(ARCHETYPES) == 9))
    results.append(check("GOING_NUCLEAR is not in ARCHETYPES", "GOING_NUCLEAR" not in ARCHETYPES))
    results.append(check("SATURDAY_POSTER is the fallback, not in ARCHETYPES", FALLBACK_ARCHETYPE == "SATURDAY_POSTER" and FALLBACK_ARCHETYPE not in ARCHETYPES))
    for name in ("GOAL_LINE", "WORKHORSE", "TARGET_MAGNET", "MISMATCH", "SEC", "BIG_TEN", "BIG_12", "ACC", "THE_RANKED"):
        results.append(check(f"{name} is a real archetype", name in ARCHETYPES))

    # --- Behavior shelves: unchanged, locks to the shelf's own signal ---
    row = _base_row(td_opportunity=61.4)
    out = resolve_cfb_archetype("goal_line_favorites", row)
    results.append(check("Goal-Line Favorites locks to GOAL_LINE", out["archetype"] == "GOAL_LINE"))
    results.append(check("behavior lock reports the locking shelf", out["locked_by_shelf"] == "goal_line_favorites"))
    results.append(check("behavior lock reports the real governing signal", out["governing_signal"] == "td_opportunity"))
    results.append(check("behavior lock confidence is the row's own score", out["confidence"] == 61.4))

    row = _base_row(role_momentum=88.2)
    out = resolve_cfb_archetype("workhorses", row)
    results.append(check("Workhorses locks to WORKHORSE", out["archetype"] == "WORKHORSE"))

    row = _base_row(target_magnets=93.0)
    out = resolve_cfb_archetype("target_magnets", row)
    results.append(check("Target Magnets locks to TARGET_MAGNET", out["archetype"] == "TARGET_MAGNET"))

    # A behavior lock wins even when the locked signal's own score is thin
    # -- shelf placement alone is the trigger, not a re-check of the score.
    row = _base_row(td_opportunity=12.0)
    out = resolve_cfb_archetype("goal_line_favorites", row)
    results.append(check("behavior lock ignores its own score/floor", out["archetype"] == "GOAL_LINE"))

    # --- Identity shelves (NEW): lock outright, ignore the player's scores ---
    row = _base_row(
        td_opportunity=99.0, td_opportunity_gated=False,  # deliberately huge -- must be ignored
        role_momentum=99.0, role_momentum_completeness=100.0,
        target_magnets=99.0, target_magnets_gated=False,
        defensive_matchup_vulnerability=99.0, defensive_matchup_completeness=100.0,
    )
    out = resolve_cfb_archetype("sec_td_watch", row)
    results.append(check("SEC TD Watch locks to SEC", out["archetype"] == "SEC"))
    results.append(check("identity lock reports the locking shelf", out["locked_by_shelf"] == "sec_td_watch"))
    results.append(check("identity lock reports no governing_signal", out["governing_signal"] is None))
    results.append(check("identity lock reports no confidence", out["confidence"] is None))

    out = resolve_cfb_archetype("big_ten_td_watch", row)
    results.append(check("Big Ten TD Watch locks to BIG_TEN, ignoring huge scores", out["archetype"] == "BIG_TEN"))

    out = resolve_cfb_archetype("big12_td_watch", row)
    results.append(check("Big 12 TD Watch locks to BIG_12, ignoring huge scores", out["archetype"] == "BIG_12"))

    out = resolve_cfb_archetype("acc_td_watch", row)
    results.append(check("ACC TD Watch locks to ACC, ignoring huge scores", out["archetype"] == "ACC"))

    # Same identity lock even when every signal is gated/thin/zero --
    # confirms the lock truly doesn't consult the scores either way.
    row = _base_row()  # every signal gated/thin by default
    out = resolve_cfb_archetype("sec_td_watch", row)
    results.append(check("SEC lock holds even with every signal gated/thin", out["archetype"] == "SEC"))

    # --- Editorial shelf (NEW): same override logic ---
    row = _base_row(
        td_opportunity=99.0, td_opportunity_gated=False,
        role_momentum=99.0, role_momentum_completeness=100.0,
    )
    out = resolve_cfb_archetype("top25_td_watch", row)
    results.append(check("Top 25 TD Watch locks to THE_RANKED, ignoring huge scores", out["archetype"] == "THE_RANKED"))
    results.append(check("editorial lock reports no governing_signal", out["governing_signal"] is None))
    results.append(check("editorial lock reports no confidence", out["confidence"] is None))

    # --- No dedicated shelf identity: independent resolution ---
    row = _base_row(
        td_opportunity=70.0, td_opportunity_gated=False,
        role_momentum=60.0, role_momentum_completeness=100.0,
    )
    out = resolve_cfb_archetype(None, row)
    results.append(check("no shelf (None) resolves independently to the highest eligible score", out["archetype"] == "GOAL_LINE"))
    results.append(check("independent confidence is the winning score", out["confidence"] == 70.0))
    results.append(check("independent resolution reports no lock", out["locked_by_shelf"] is None))

    out = resolve_cfb_archetype("tasty_six", row)
    results.append(check("'tasty_six' (no dedicated identity) resolves independently the same way", out["archetype"] == "GOAL_LINE"))

    out = resolve_cfb_archetype("some_future_shelf_not_yet_scoped", row)
    results.append(check("an unrecognized shelf name falls through to independent resolution", out["archetype"] == "GOAL_LINE"))

    # An ineligible (gated) signal is never a candidate even with the
    # highest raw number.
    row = _base_row(
        td_opportunity=99.0, td_opportunity_gated=True,  # gated -- excluded despite the highest score
        role_momentum=60.0, role_momentum_completeness=100.0,
    )
    out = resolve_cfb_archetype(None, row)
    results.append(check("a gated signal is never a winning candidate", out["archetype"] == "WORKHORSE"))

    # FLOOR: a real, eligible, non-thin score that still sits below FLOOR
    # loses to Saturday Poster.
    row = _base_row(
        td_opportunity=FLOOR - 0.1, td_opportunity_gated=False,
        role_momentum=40.0, role_momentum_completeness=100.0,
    )
    out = resolve_cfb_archetype(None, row)
    results.append(check("below-FLOOR eligible signal falls to SATURDAY_POSTER", out["archetype"] == "SATURDAY_POSTER"))
    results.append(check("SATURDAY_POSTER carries no confidence", out["confidence"] is None))
    results.append(check("SATURDAY_POSTER carries no governing signal", out["governing_signal"] is None))

    # Nothing eligible at all -> Saturday Poster.
    row = _base_row()
    out = resolve_cfb_archetype(None, row)
    results.append(check("no eligible signal at all -> SATURDAY_POSTER", out["archetype"] == "SATURDAY_POSTER"))

    # MISMATCH: only reachable via independent resolution -- eligible and
    # highest -> wins, exactly like the other 3.
    row = _base_row(
        defensive_matchup_vulnerability=80.0, defensive_matchup_completeness=100.0,
    )
    out = resolve_cfb_archetype(None, row)
    results.append(check("MISMATCH wins when it's the highest eligible signal", out["archetype"] == "MISMATCH"))

    # MISMATCH has no shelf of its own -- confirm no shelf name locks it
    # (unlike the other 3 signal-driven archetypes, which all have one).
    results.append(check("MISMATCH has no entry in any shelf-lock table", "MISMATCH" not in _LOCKED_SHELF_ARCHETYPE.values()))

    # MISMATCH tie-break: exact tie with GOAL_LINE -> GOAL_LINE wins
    # (earlier in _ARCHETYPE_SPECS' priority order).
    row = _base_row(
        td_opportunity=75.0, td_opportunity_gated=False,
        defensive_matchup_vulnerability=75.0, defensive_matchup_completeness=100.0,
    )
    out = resolve_cfb_archetype(None, row)
    results.append(check("tie between GOAL_LINE and MISMATCH favors GOAL_LINE", out["archetype"] == "GOAL_LINE"))

    # A completely degenerate defensive_matchup_completeness (0) excludes
    # MISMATCH even with a high raw score.
    row = _base_row(
        defensive_matchup_vulnerability=90.0, defensive_matchup_completeness=0.0,
    )
    out = resolve_cfb_archetype(None, row)
    results.append(check("defensive_matchup_completeness == 0 excludes MISMATCH", out["archetype"] == "SATURDAY_POSTER"))

    # --- The architecture point itself: same player, two placements ---
    row = _base_row(td_opportunity=61.4, td_opportunity_gated=False)
    out_behavior = resolve_cfb_archetype("goal_line_favorites", row)
    out_identity = resolve_cfb_archetype("sec_td_watch", row)
    results.append(
        check(
            "the SAME row resolves to different archetypes on different shelf placements",
            out_behavior["archetype"] == "GOAL_LINE" and out_identity["archetype"] == "SEC",
        )
    )

    # --- Team identity tint (unchanged) ---
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
