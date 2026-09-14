"""
Unit tests for nfl_tension.find_tension (Editorial Voice Spec addition,
"Find the Tension").

    python3 nfl/content_writer/test_nfl_tension.py

Includes a direct re-derivation of the spec's own worked example
(Matthew Golden, +310) as its own check -- the one real, concrete case
this module's thresholds were calibrated against.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nfl_tension import GAP_THRESHOLD, TENSION_TYPES, find_tension


def check(label, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    return bool(cond)


def _row(**overrides):
    row = {
        "market_value_score": 50.0,
        "td_opportunity": 50.0,
        "td_opportunity_completeness": 100.0,
        "role_momentum": 50.0,
        "role_momentum_completeness": 100.0,
        "situation": 50.0,
        "situation_completeness": 100.0,
        "role_trend": 50.0,
        "proven_heat": 50.0,
        "emerging_heat": 50.0,
        "evidence_quality": 80.0,
    }
    row.update(overrides)
    return row


if __name__ == "__main__":
    r = []

    # --- Every real type is a valid, real value ---
    r.append(check("TENSION_TYPES has exactly the 5 real NFL-detectable types",
                    set(TENSION_TYPES) == {"divergence", "contradiction", "change", "convergence", "uncertainty"}))

    # --- The spec's own worked example, re-derived exactly ---
    golden = _row(market_value_score=72.9, td_opportunity=57.1, td_opportunity_completeness=30.0,
                  role_momentum=50.0, situation=50.0, evidence_quality=45.0)
    out = find_tension(golden)
    r.append(check("Matthew Golden worked example resolves to type=divergence", out["type"] == "divergence"))
    r.append(check("Golden: evidence_strength is thin (evidence_quality 45 < 50)", out["evidence_strength"] == "thin"))
    r.append(check("Golden: uncertainty is real and non-null", bool(out["uncertainty"])))
    r.append(check("Golden: uncertainty names the real thin field (opportunity)", "opportunity" in out["uncertainty"]))
    r.append(check("Golden: editorial_claim states the market-over-role direction",
                    "market" in out["editorial_claim"].lower() and "conviction" in out["editorial_claim"].lower()))

    # --- Divergence, the other direction: internal signals beat the market ---
    row = _row(market_value_score=30.0, td_opportunity=75.0, role_momentum=72.0, situation=68.0, evidence_quality=90.0)
    out = find_tension(row)
    r.append(check("internal-beats-market divergence resolves correctly", out["type"] == "divergence"))
    r.append(check("internal-beats-market: evidence_strength is strong", out["evidence_strength"] == "strong"))
    r.append(check("internal-beats-market: uncertainty is None (strong evidence)", out["uncertainty"] is None))
    r.append(check("internal-beats-market: claim credits the player over the market",
                    "market" in out["editorial_claim"].lower() and "credit" in out["editorial_claim"].lower()))

    # --- Contradiction: two internal signals disagree sharply, market flat/absent ---
    row = _row(market_value_score=None, td_opportunity=85.0, role_momentum=30.0, situation=50.0, evidence_quality=90.0)
    out = find_tension(row)
    r.append(check("internal-vs-internal contradiction resolves correctly", out["type"] == "contradiction"))
    r.append(check("contradiction claim names both real pillars", "td_opportunity" in out["editorial_claim"] and "role_momentum" in out["editorial_claim"]))

    # --- Change: role_trend diverges sharply from role_momentum's own level ---
    row = _row(market_value_score=50.0, td_opportunity=50.0, role_momentum=48.0, role_trend=80.0,
               situation=50.0, evidence_quality=85.0)
    out = find_tension(row)
    r.append(check("trend-vs-level change resolves correctly", out["type"] == "change"))
    r.append(check("change claim mentions the season-long vs. recent framing", "season-long" in out["editorial_claim"]))

    # --- Convergence: everything genuinely close together, strong evidence ---
    row = _row(market_value_score=52.0, td_opportunity=55.0, role_momentum=50.0, situation=53.0, evidence_quality=90.0)
    out = find_tension(row)
    r.append(check("tight-band convergence resolves correctly", out["type"] == "convergence"))
    r.append(check("convergence has no counter_signal (nothing to contrast)", out["counter_signal"] is None))
    r.append(check("convergence with strong evidence has no uncertainty hedge", out["uncertainty"] is None))

    # --- Convergence, high band: everything agrees at a genuinely strong level ---
    # role_trend/proven_heat/emerging_heat pinned to match their own level fields
    # explicitly -- otherwise _row()'s neutral 50.0 defaults would read as a real
    # trend-vs-level "change" against an overridden role_momentum of 75.
    row = _row(market_value_score=78.0, td_opportunity=80.0, role_momentum=75.0, situation=76.0,
               role_trend=75.0, proven_heat=80.0, emerging_heat=80.0, evidence_quality=90.0)
    out = find_tension(row)
    r.append(check("high-band convergence still classifies as convergence, not divergence", out["type"] == "convergence"))
    r.append(check("high-band convergence claim reads as genuine agreement, not modest",
                    "same direction" in out["editorial_claim"]))

    # --- Uncertainty: thin evidence + a real but sub-confident gap, nothing clears GAP_THRESHOLD ---
    row = _row(market_value_score=58.0, td_opportunity=52.0, role_momentum=48.0, situation=50.0, evidence_quality=35.0)
    out = find_tension(row)
    r.append(check("thin evidence + a weak-but-real gap resolves to uncertainty, not convergence", out["type"] == "uncertainty"))

    # --- No manufactured tension: nothing crosses NOTICE_THRESHOLD, even with thin evidence ---
    row = _row(market_value_score=50.0, td_opportunity=51.0, role_momentum=49.0, situation=50.0, evidence_quality=20.0)
    out = find_tension(row)
    r.append(check("genuinely flat signals never get promoted to uncertainty just because evidence is thin",
                    out["type"] == "convergence"))
    r.append(check("but the thin-evidence hedge is still present on that convergence read", out["uncertainty"] is not None))

    # --- Guardrail: a real gap classifies normally even under thin evidence (never downgraded to 'uncertainty') ---
    row = _row(market_value_score=90.0, td_opportunity=40.0, role_momentum=35.0, situation=45.0, evidence_quality=10.0)
    out = find_tension(row)
    r.append(check("a confidently-sized gap keeps its real type even under very thin evidence", out["type"] == "divergence"))
    r.append(check("...but still carries the uncertainty hedge", out["uncertainty"] is not None))

    # --- Degenerate case: no real signals at all ---
    row = {k: None for k in _row()}
    out = find_tension(row)
    r.append(check("all-missing-signals degrades to an honest convergence read, not a crash", out["type"] == "convergence"))
    r.append(check("all-missing-signals: evidence_strength is thin", out["evidence_strength"] == "thin"))
    r.append(check("all-missing-signals: uncertainty explains why", bool(out["uncertainty"])))

    # --- Every real Tension Object always carries the full real shape ---
    for candidate in (golden, row):
        obj = find_tension(candidate)
        r.append(check(
            f"Tension Object always has the full 7-key shape ({candidate.get('market_value_score')})",
            set(obj.keys()) == {"type", "primary_signal", "counter_signal", "evidence_strength", "editorial_claim", "uncertainty", "story_angle"},
        ))

    print()
    p = sum(r)
    print(f"{p}/{len(r)} checks passed")
    raise SystemExit(0 if p == len(r) else 1)
