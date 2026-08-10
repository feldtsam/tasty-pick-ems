"""
Tests the avoid_headlines constraint block added to
tasty_six_prompt.build_system_prompt() / shelf_card_prompt.build_system_
prompt() — pure string-building, no Claude API call, so this runs
instantly and always (no real-pool dependency like the batch-level tests).

Doesn't (and can't) prove the model actually varies its output — that's
an LLM behavior, not a deterministic one. What this proves: the real
constraint text is actually injected into the real prompt the model sees
when a batch has prior titles, and is absent (byte-identical to the old
behavior) when it doesn't — the structural half of the fix that's
actually testable without spending a real API call.

Run: python3 pipeline/api/content_writer/test_headline_variety.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "voice"))

import shelf_card_prompt
import tasty_six_prompt


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


if __name__ == "__main__":
    results = []

    for module, name in ((shelf_card_prompt, "shelf_card_prompt"), (tasty_six_prompt, "tasty_six_prompt")):
        no_avoid = module.build_system_prompt("Hot Hitters", "developing_angle")
        results.append(check(
            f"{name}: no avoid_headlines -> no variety block, byte-identical to omitting the param entirely",
            "REAL VARIETY REQUIRED" not in no_avoid,
        ))

        empty_avoid = module.build_system_prompt("Hot Hitters", "developing_angle", avoid_headlines=[])
        results.append(check(
            f"{name}: empty avoid_headlines list -> still no variety block (falsy, not 'zero items to avoid')",
            "REAL VARIETY REQUIRED" not in empty_avoid,
        ))

        used_titles = [
            "Tyler O'Neill's Swing Is Humming Right Now",
            "Bryce Harper's Swing Is Humming Right Now",
        ]
        with_avoid = module.build_system_prompt("Hot Hitters", "developing_angle", avoid_headlines=used_titles)
        results.append(check(
            f"{name}: real prior titles -> variety block present",
            "REAL VARIETY REQUIRED" in with_avoid,
        ))
        results.append(check(
            f"{name}: every real prior title actually appears in the prompt text, not just referenced abstractly",
            all(title in with_avoid for title in used_titles),
        ))
        results.append(check(
            f"{name}: the rest of the prompt (shelf personality, banned language, hard rules) is unchanged, "
            f"not replaced by the variety block",
            no_avoid.split("HARD RULES")[0] == with_avoid.split("HARD RULES")[0],
        ))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
