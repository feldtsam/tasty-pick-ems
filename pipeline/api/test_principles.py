"""
Tests principles.py's confidence_band_for_score() boundary behavior --
specifically the real gap found and fixed on 2026-08-04: a real candidate
(Andres Gimenez, final_score=39.6) fell between quiet_signal's original
39 upper bound and developing_angle's 40 lower bound and got no band at
all. Every boundary here is checked explicitly, not just the obvious
mid-band cases, since boundary handling is exactly what broke.

Run: python3 pipeline/api/test_principles.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "content_writer" / "voice"))

from principles import confidence_band_for_score  # noqa: E402


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


if __name__ == "__main__":
    results = []

    # --- The real bug: 39.6 (Andres Gimenez, real production data) ---
    results.append(check(
        "the real value that exposed this bug (39.6) now resolves to quiet_signal",
        confidence_band_for_score(39.6) == "quiet_signal",
    ))

    # --- Every internal boundary, both sides ---
    results.append(check("39.9 -> quiet_signal (just under the old gap)", confidence_band_for_score(39.9) == "quiet_signal"))
    results.append(check("40.0 -> developing_angle (exact boundary)", confidence_band_for_score(40.0) == "developing_angle"))
    results.append(check("59.9 -> developing_angle", confidence_band_for_score(59.9) == "developing_angle"))
    results.append(check("60.0 -> strong_setup (exact boundary)", confidence_band_for_score(60.0) == "strong_setup"))
    results.append(check("74.9 -> strong_setup", confidence_band_for_score(74.9) == "strong_setup"))
    results.append(check("75.0 -> premium_signal (exact boundary)", confidence_band_for_score(75.0) == "premium_signal"))

    # --- The real lower/upper bounds, unchanged by this fix ---
    results.append(check("25.0 -> quiet_signal (inclusive lower bound)", confidence_band_for_score(25.0) == "quiet_signal"))
    results.append(check("90.0 -> premium_signal (inclusive upper bound, unchanged)", confidence_band_for_score(90.0) == "premium_signal"))
    results.append(check("24.9 -> None (below range, still deliberate-handling territory)", confidence_band_for_score(24.9) is None))
    results.append(check("90.1 -> None (above range, still deliberate-handling territory)", confidence_band_for_score(90.1) is None))

    # --- Real mid-band sanity (not just boundaries) ---
    results.append(check("72.1 (real Jeremy Pena final_score) -> strong_setup", confidence_band_for_score(72.1) == "strong_setup"))
    results.append(check("73.6 (real Shohei Ohtani final_score) -> strong_setup", confidence_band_for_score(73.6) == "strong_setup"))
    results.append(check("41.0 (real Troy Johnston final_score) -> developing_angle", confidence_band_for_score(41.0) == "developing_angle"))
    results.append(check("34.7 (real Eliezer Alfonzo final_score) -> quiet_signal", confidence_band_for_score(34.7) == "quiet_signal"))
    results.append(check("68.2 (real Osleivis Basabe final_score) -> strong_setup", confidence_band_for_score(68.2) == "strong_setup"))

    print(f"\n{sum(results)}/{len(results)} checks passed")
    raise SystemExit(0 if all(results) else 1)
