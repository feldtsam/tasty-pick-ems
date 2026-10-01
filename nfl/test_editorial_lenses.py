"""
Tests for editorial_lenses.pct_col_is_real / is_masked_fallback -- the
shared masking-check helpers promoted out of shelves.py (Stage 1, Masked-
Value Handling) so nfl_tension.find_tension() can apply the identical
real-vs-fallback rule shelves.py's role-signal candidates already use,
rather than inventing a second one.

Run: python3 nfl/test_editorial_lenses.py
"""
from editorial_lenses import (
    ROLE_SIGNAL_COMPLETENESS_THRESHOLD,
    citable_fields_for_lens,
    is_masked_fallback,
    pct_col_is_real,
    resolve_editorial_lens,
)


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


if __name__ == "__main__":
    results = []

    # --- pct_col_is_real: the bare neutral-50 heuristic ---
    results.append(check(
        "pct_col_is_real: a real, non-50 value is real",
        pct_col_is_real({"x": 56.1}, "x") is True,
    ))
    results.append(check(
        "pct_col_is_real: exactly 50.0 is the fallback sentinel, not real",
        pct_col_is_real({"x": 50.0}, "x") is False,
    ))
    results.append(check(
        "pct_col_is_real: missing/None is not real",
        pct_col_is_real({}, "x") is False,
    ))
    results.append(check(
        "pct_col_is_real: a coincidental real 50.0 is indistinguishable from a fallback (documented limitation, unchanged)",
        pct_col_is_real({"x": 50.0}, "x") is False,
    ))

    # --- is_masked_fallback: prefers a real completeness column when given ---
    results.append(check(
        "is_masked_fallback: completeness below threshold -> masked, even if the value itself isn't 50.0",
        is_masked_fallback({"role_momentum": 73.2, "role_momentum_completeness": 0.0}, "role_momentum", "role_momentum_completeness") is True,
    ))
    results.append(check(
        "is_masked_fallback: completeness at/above threshold -> not masked",
        is_masked_fallback({"role_momentum": 73.2, "role_momentum_completeness": 80.0}, "role_momentum", "role_momentum_completeness") is False,
    ))
    results.append(check(
        "is_masked_fallback: completeness exactly at threshold counts as real (>=, not >)",
        is_masked_fallback({"role_momentum": 50.0, "role_momentum_completeness": ROLE_SIGNAL_COMPLETENESS_THRESHOLD}, "role_momentum", "role_momentum_completeness") is False,
    ))
    results.append(check(
        "is_masked_fallback: no completeness column given -> falls back to the neutral-50 heuristic on the value itself",
        is_masked_fallback({"x": 50.0}, "x") is True,
    ))
    results.append(check(
        "is_masked_fallback: completeness column named but absent from this row -> same fallback-to-value-heuristic behavior",
        is_masked_fallback({"x": 62.0}, "x", "x_completeness") is False,
    ))
    results.append(check(
        "is_masked_fallback: Fant's real Week 3 row (role_momentum=50.0, completeness=0.0) -> masked",
        is_masked_fallback({"role_momentum": 50.0, "role_momentum_completeness": 0.0}, "role_momentum", "role_momentum_completeness") is True,
    ))
    results.append(check(
        "is_masked_fallback: Rachaad White's real week-10-2025 row (role_momentum=100.0, completeness=80.0) -> not masked",
        is_masked_fallback({"role_momentum": 100.0, "role_momentum_completeness": 80.0}, "role_momentum", "role_momentum_completeness") is False,
    ))

    # --- GAMES-PLAYED HOTFIX: trail3_games_played and the three trail3
    # sums are deliberately NOT in any shelf's citable fields anymore --
    # see editorial_lenses.SIGNAL_TO_CITABLE_FIELDS["td_opportunity"]'s
    # own comment for the real incident (every live card's games-played
    # figure has always been wrong; pulled from the writer until the
    # real upstream fix lands). Supersedes the former Stage 1G test
    # asserting the opposite.
    rz_lens = resolve_editorial_lens("Red Zone Trends", {})
    rz_fields = citable_fields_for_lens(rz_lens)
    results.append(check(
        "trail3_games_played and the trail3 sums are NOT in Red Zone Trends' citable fields (games-played hotfix)",
        "trail3_games_played" not in rz_fields and "i10_touches_trail3" not in rz_fields
        and "gl_touches_trail3" not in rz_fields and "rz_tds_trail3" not in rz_fields,
    ))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED -- see above.")
        raise SystemExit(1)
