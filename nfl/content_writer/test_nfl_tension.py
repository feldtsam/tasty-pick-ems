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
    r.append(check("TENSION_TYPES has exactly the 6 real NFL-detectable types (Stage 1 added 'forming')",
                    set(TENSION_TYPES) == {"divergence", "contradiction", "change", "convergence", "uncertainty", "forming"}))

    # --- The spec's own worked example, re-derived exactly ---
    golden = _row(market_value_score=72.9, td_opportunity=57.1, td_opportunity_completeness=30.0,
                  role_momentum=50.0, situation=50.0, evidence_quality=45.0)
    out = find_tension(golden)
    r.append(check("Matthew Golden worked example resolves to type=divergence", out["tension_type"] == "divergence"))
    r.append(check("Golden: evidence_strength is thin (evidence_quality 45 < 50)", out["evidence_strength"] == "thin"))
    r.append(check("Golden: uncertainty is real and non-null", bool(out["uncertainty"])))
    r.append(check("Golden: uncertainty names the real thin field (opportunity)", "opportunity" in out["uncertainty"]))
    r.append(check("Golden: editorial_claim states the market-over-role direction",
                    "market" in out["editorial_claim"].lower() and "conviction" in out["editorial_claim"].lower()))

    # --- Divergence, the other direction: internal signals beat the market ---
    row = _row(market_value_score=30.0, td_opportunity=75.0, role_momentum=72.0, situation=68.0, evidence_quality=90.0)
    out = find_tension(row)
    r.append(check("internal-beats-market divergence resolves correctly", out["tension_type"] == "divergence"))
    r.append(check("internal-beats-market: evidence_strength is strong", out["evidence_strength"] == "strong"))
    r.append(check("internal-beats-market: uncertainty is None (strong evidence)", out["uncertainty"] is None))
    r.append(check("internal-beats-market: claim credits the player over the market",
                    "market" in out["editorial_claim"].lower() and "credit" in out["editorial_claim"].lower()))

    # --- Contradiction: two internal signals disagree sharply, market flat/absent ---
    row = _row(market_value_score=None, td_opportunity=85.0, role_momentum=30.0, situation=50.0, evidence_quality=90.0)
    out = find_tension(row)
    r.append(check("internal-vs-internal contradiction resolves correctly", out["tension_type"] == "contradiction"))
    r.append(check("contradiction claim names both real pillars", "td_opportunity" in out["editorial_claim"] and "role_momentum" in out["editorial_claim"]))

    # --- Change: role_trend diverges sharply from role_momentum's own level ---
    row = _row(market_value_score=50.0, td_opportunity=50.0, role_momentum=48.0, role_trend=80.0,
               situation=50.0, evidence_quality=85.0)
    out = find_tension(row)
    r.append(check("trend-vs-level change resolves correctly", out["tension_type"] == "change"))
    r.append(check("change claim mentions the season-long vs. recent framing", "season-long" in out["editorial_claim"]))

    # --- Convergence: everything genuinely close together, strong evidence ---
    row = _row(market_value_score=52.0, td_opportunity=55.0, role_momentum=50.0, situation=53.0, evidence_quality=90.0)
    out = find_tension(row)
    r.append(check("tight-band convergence resolves correctly", out["tension_type"] == "convergence"))
    r.append(check("convergence has no counter_signal (nothing to contrast)", out["counter_signal"] is None))
    r.append(check("convergence with strong evidence has no uncertainty hedge", out["uncertainty"] is None))

    # --- Convergence, high band: everything agrees at a genuinely strong level ---
    # role_trend/proven_heat/emerging_heat pinned to match their own level fields
    # explicitly -- otherwise _row()'s neutral 50.0 defaults would read as a real
    # trend-vs-level "change" against an overridden role_momentum of 75.
    row = _row(market_value_score=78.0, td_opportunity=80.0, role_momentum=75.0, situation=76.0,
               role_trend=75.0, proven_heat=80.0, emerging_heat=80.0, evidence_quality=90.0)
    out = find_tension(row)
    r.append(check("high-band convergence still classifies as convergence, not divergence", out["tension_type"] == "convergence"))
    r.append(check("high-band convergence claim reads as genuine agreement, not modest",
                    "same direction" in out["editorial_claim"]))

    # --- Uncertainty: thin evidence + a real but sub-confident gap, nothing clears GAP_THRESHOLD ---
    row = _row(market_value_score=58.0, td_opportunity=52.0, role_momentum=48.0, situation=50.0, evidence_quality=35.0)
    out = find_tension(row)
    r.append(check("thin evidence + a weak-but-real gap resolves to uncertainty, not convergence", out["tension_type"] == "uncertainty"))

    # --- No manufactured tension: nothing crosses NOTICE_THRESHOLD, even with thin evidence ---
    row = _row(market_value_score=50.0, td_opportunity=51.0, role_momentum=49.0, situation=50.0, evidence_quality=20.0)
    out = find_tension(row)
    r.append(check("genuinely flat signals never get promoted to uncertainty just because evidence is thin",
                    out["tension_type"] == "convergence"))
    r.append(check("but the thin-evidence hedge is still present on that convergence read", out["uncertainty"] is not None))

    # --- Guardrail: a real gap classifies normally even under thin evidence (never downgraded to 'uncertainty') ---
    row = _row(market_value_score=90.0, td_opportunity=40.0, role_momentum=35.0, situation=45.0, evidence_quality=10.0)
    out = find_tension(row)
    r.append(check("a confidently-sized gap keeps its real type even under very thin evidence", out["tension_type"] == "divergence"))
    r.append(check("...but still carries the uncertainty hedge", out["uncertainty"] is not None))

    # --- Degenerate case: no real signals at all ---
    row = {k: None for k in _row()}
    out = find_tension(row)
    r.append(check("all-missing-signals degrades to an honest convergence read, not a crash", out["tension_type"] == "convergence"))
    r.append(check("all-missing-signals: evidence_strength is thin", out["evidence_strength"] == "thin"))
    r.append(check("all-missing-signals: uncertainty explains why", bool(out["uncertainty"])))

    # --- Every real Tension Object always carries the full real shape ---
    FULL_SHAPE = {
        "tension_type", "primary_signal", "counter_signal", "evidence_strength", "editorial_claim",
        "uncertainty", "story_angle", "information_value", "interrogation_status", "story_mode",
        "reader_question_type", "allowed_claim_strength",
    }
    for candidate in (golden, row):
        obj = find_tension(candidate)
        r.append(check(
            f"Tension Object always has the full 12-key shape ({candidate.get('market_value_score')})",
            set(obj.keys()) == FULL_SHAPE,
        ))

    # ============================================================
    # signal_verdict integration -- STOP gate, information_value,
    # story_mode/reader_question_type/allowed_claim_strength.
    # ============================================================

    def _ir(status, signal_verdict=None, alt_statuses=None, relationship_established=None):
        """Builds a real Pass 3/4-shaped interrogation_result wrapper."""
        if status != "complete":
            return {"interrogation_status": status, "result": None}
        alt_explanations = [{"status": s} for s in (alt_statuses or [])]
        return {
            "interrogation_status": "complete",
            "result": {
                "signal_verdict": signal_verdict,
                "relationship_established": relationship_established,
                "challenge": {"alternate_explanations": alt_explanations},
            },
        }

    divergence_row = _row(market_value_score=90.0, td_opportunity=40.0, role_momentum=35.0, situation=45.0)
    contradiction_row = _row(market_value_score=None, td_opportunity=85.0, role_momentum=30.0, situation=50.0)
    change_row = _row(market_value_score=50.0, td_opportunity=50.0, role_momentum=48.0, role_trend=80.0, situation=50.0)
    convergence_row = _row(market_value_score=52.0, td_opportunity=55.0, role_momentum=50.0, situation=53.0)
    uncertainty_row = _row(market_value_score=58.0, td_opportunity=52.0, role_momentum=48.0, situation=50.0, evidence_quality=35.0)

    # --- STOP gate: FAILS always returns None, before any analysis ---
    r.append(check(
        "STOP gate: signal_verdict=FAILS returns None regardless of relationship_established",
        find_tension(divergence_row, interrogation_result=_ir("complete", "FAILS", relationship_established=True)) is None,
    ))
    r.append(check("STOP gate: signal_verdict=SURVIVES does NOT gate out", find_tension(divergence_row, interrogation_result=_ir("complete", "SURVIVES")) is not None))

    # --- relationship_established: the real fix for UNRESOLVED conflating two states ---
    r.append(check(
        "relationship_established=False -> STOP (real evidence the relationship isn't established)",
        find_tension(divergence_row, interrogation_result=_ir("complete", "UNRESOLVED", relationship_established=False)) is None,
    ))
    r.append(check(
        "relationship_established missing/None -> STOP -- never assume established just because it wasn't stated",
        find_tension(divergence_row, interrogation_result=_ir("complete", "UNRESOLVED")) is None,
    ))
    established_result = find_tension(divergence_row, interrogation_result=_ir("complete", "UNRESOLVED", relationship_established=True))
    r.append(check(
        "relationship_established=True -> PROCEEDS (does not gate out) -- the real fix",
        established_result is not None,
    ))
    r.append(check(
        "relationship_established=True: story_mode/reader_question_type/allowed_claim_strength are the "
        "LOCKED Discovery triple, not derived via the SURVIVES-only alternate-explanation reduction",
        (established_result["story_mode"], established_result["reader_question_type"], established_result["allowed_claim_strength"])
        == ("discovery", "why", "tentative"),
    ))
    r.append(check(
        "relationship_established=True: evidence_strength is forced 'thin' with a real non-null uncertainty "
        "hedge, consistent with the tentative claim strength",
        established_result["evidence_strength"] == "thin" and bool(established_result["uncertainty"]),
    ))
    r.append(check(
        "relationship_established=True: interrogation_status still reads 'complete' -- a real Interrogation "
        "attempt happened, this is not conflated with not_selected/failed",
        established_result["interrogation_status"] == "complete",
    ))
    # A real alternate_explanations shape that would trigger a DIFFERENT
    # outcome under _story_mode_for_survives (e.g. a SUPPORTED status,
    # which for SURVIVES means story_mode="explanation") must NOT leak
    # into the UNRESOLVED+established path -- that reduction is SURVIVES-
    # only by design; UNRESOLVED+established is always Discovery/tentative,
    # unconditionally.
    established_with_supported = find_tension(
        divergence_row, interrogation_result=_ir("complete", "UNRESOLVED", alt_statuses=["SUPPORTED"], relationship_established=True),
    )
    r.append(check(
        "relationship_established=True stays Discovery/tentative even when alternate_explanations contains "
        "a SUPPORTED status -- that reduction applies only to SURVIVES, never leaks into this path",
        established_with_supported["story_mode"] == "discovery" and established_with_supported["allowed_claim_strength"] == "tentative",
    ))

    # --- information_value: pure function of tension_type, unconditional across all interrogation_status states ---
    for label, status_kwargs in (
        ("no_interrogation", {}),
        ("not_selected", {"interrogation_result": _ir("not_selected")}),
        ("failed", {"interrogation_result": _ir("failed")}),
        ("complete+SURVIVES", {"interrogation_result": _ir("complete", "SURVIVES", ["WEAKENED"])}),
    ):
        for cand, expected_type, expected_value in (
            (divergence_row, "divergence", "HIGH"),
            (contradiction_row, "contradiction", "HIGH"),
            (change_row, "change", "MEDIUM"),
            (convergence_row, "convergence", "MEDIUM"),
        ):
            out = find_tension(cand, **status_kwargs)
            r.append(check(
                f"information_value: {label}, {expected_type} -> {expected_value}",
                out["tension_type"] == expected_type and out["information_value"] == expected_value,
            ))

    # --- interrogation_status tag itself, all four real states ---
    r.append(check("interrogation_status: no interrogation_result passed at all -> 'no_interrogation'",
                    find_tension(divergence_row)["interrogation_status"] == "no_interrogation"))
    r.append(check("interrogation_status: not_selected passes through as its own real state",
                    find_tension(divergence_row, interrogation_result=_ir("not_selected"))["interrogation_status"] == "not_selected"))
    r.append(check("interrogation_status: failed passes through as its own real state",
                    find_tension(divergence_row, interrogation_result=_ir("failed"))["interrogation_status"] == "failed"))
    r.append(check("interrogation_status: complete+SURVIVES passes through as 'complete'",
                    find_tension(divergence_row, interrogation_result=_ir("complete", "SURVIVES", []))["interrogation_status"] == "complete"))

    # --- story_mode/reader_question_type/allowed_claim_strength: None for anything not complete+SURVIVES ---
    for label, ir in (("no_interrogation", None), ("not_selected", _ir("not_selected")), ("failed", _ir("failed"))):
        out = find_tension(divergence_row, interrogation_result=ir)
        r.append(check(
            f"story_mode/reader_question_type/allowed_claim_strength are all None for {label} -- never guessed",
            out["story_mode"] is None and out["reader_question_type"] is None and out["allowed_claim_strength"] is None,
        ))

    # --- story_mode derivation, all four real alternate_explanations shapes ---
    out = find_tension(divergence_row, interrogation_result=_ir("complete", "SURVIVES", ["SUPPORTED"]))
    r.append(check("story_mode: any SUPPORTED -> explanation/what_it_means/confident",
                    (out["story_mode"], out["reader_question_type"], out["allowed_claim_strength"]) == ("explanation", "what_it_means", "confident")))

    out = find_tension(divergence_row, interrogation_result=_ir("complete", "SURVIVES", ["WEAKENED", "WEAKENED"]))
    r.append(check("story_mode: non-empty, all WEAKENED -> discovery/why/confident",
                    (out["story_mode"], out["reader_question_type"], out["allowed_claim_strength"]) == ("discovery", "why", "confident")))

    out = find_tension(divergence_row, interrogation_result=_ir("complete", "SURVIVES", []))
    r.append(check("story_mode: empty alternate_explanations array -> discovery/why/confident",
                    (out["story_mode"], out["reader_question_type"], out["allowed_claim_strength"]) == ("discovery", "why", "confident")))

    out = find_tension(divergence_row, interrogation_result=_ir("complete", "SURVIVES", ["WEAKENED", "UNRESOLVED"]))
    r.append(check("story_mode: any UNRESOLVED/NOT_TESTABLE (no SUPPORTED) -> discovery/why/tentative, "
                    "AND evidence_strength forced thin with a real non-null uncertainty hedge",
                    (out["story_mode"], out["reader_question_type"], out["allowed_claim_strength"]) == ("discovery", "why", "tentative")
                    and out["evidence_strength"] == "thin" and bool(out["uncertainty"])))

    out = find_tension(divergence_row, interrogation_result=_ir("complete", "SURVIVES", ["NOT_TESTABLE"]))
    r.append(check("story_mode: NOT_TESTABLE alone (no SUPPORTED) also forces discovery/why/tentative",
                    (out["story_mode"], out["reader_question_type"], out["allowed_claim_strength"]) == ("discovery", "why", "tentative")))

    # --- force_thin does NOT reclassify tension_type -- guardrail extends correctly to the new override ---
    strong_divergence = _row(market_value_score=90.0, td_opportunity=40.0, role_momentum=35.0, situation=45.0, evidence_quality=95.0)
    baseline = find_tension(strong_divergence)
    forced = find_tension(strong_divergence, interrogation_result=_ir("complete", "SURVIVES", ["UNRESOLVED"]))
    r.append(check("forced-thin (mixed UNRESOLVED case) still keeps the SAME real tension_type as strong evidence would",
                    baseline["tension_type"] == "divergence" and forced["tension_type"] == "divergence"))
    r.append(check("...but evidence_strength flips from strong to thin, and a real uncertainty hedge appears",
                    baseline["evidence_strength"] == "strong" and baseline["uncertainty"] is None
                    and forced["evidence_strength"] == "thin" and bool(forced["uncertainty"])))

    # --- Legacy fields are IDENTICAL across no_interrogation/not_selected/failed for the same candidate --
    # "resource allocation must never masquerade as evidence evaluation": since none of these three
    # states carry a real signal_verdict, the pre-existing gap-based classification must be completely
    # unaffected by which of the three applies -- same tension_type, same primary_signal, same claim.
    LEGACY_KEYS = ("tension_type", "primary_signal", "counter_signal", "editorial_claim", "story_angle", "evidence_strength", "uncertainty")
    no_ir = find_tension(uncertainty_row)
    not_selected_ir = find_tension(uncertainty_row, interrogation_result=_ir("not_selected"))
    failed_ir = find_tension(uncertainty_row, interrogation_result=_ir("failed"))
    r.append(check(
        "legacy fields are byte-for-byte identical across no_interrogation/not_selected/failed -- "
        "the pre-existing analysis tier is genuinely unaffected by which of these three applies",
        all(no_ir[k] == not_selected_ir[k] == failed_ir[k] for k in LEGACY_KEYS),
    ))

    # ============================================================
    # MASKED-VALUE HANDLING (Stage 1) -- role_momentum=50.0 with low/no
    # completeness must not be treated as a real signal.
    # ============================================================

    # Noah Fant's real Week 3 2026 row: role_momentum=50.0, completeness=0.0
    # (zero games' worth of real role data). Before this fix, this
    # classified as "convergence" with the editorial_claim "Every real
    # signal here is sitting in the same unremarkable middle range" --
    # confirmed directly against this exact row during Stage 1's own
    # investigation, not assumed.
    fant_week3 = _row(
        market_value_score=None,
        td_opportunity=56.1, td_opportunity_completeness=70.0,
        role_momentum=50.0, role_momentum_completeness=0.0,
        situation=59.5, situation_completeness=100.0,
        role_trend=None, proven_heat=None, emerging_heat=None,
        evidence_quality=71.6,
    )
    out = find_tension(fant_week3)
    r.append(check(
        "Fant's real Week 3 row (role_momentum masked, completeness=0.0) -> type=forming, not convergence",
        out["tension_type"] == "forming",
    ))
    r.append(check(
        "Fant: editorial_claim describes still-forming evidence, not a flat/settled role",
        "hasn't played enough of a role yet" in out["editorial_claim"],
    ))
    r.append(check(
        "Fant: information_value is LOW for forming (lower than uncertainty's MEDIUM)",
        out["information_value"] == "LOW",
    ))

    # A real, unmasked, genuine contradiction (Rachaad White, week 10 2025)
    # must classify identically to before this fix -- the masking check
    # must never touch a real signal.
    rachaad = _row(
        market_value_score=None,
        td_opportunity=92.8, td_opportunity_completeness=100.0,
        role_momentum=100.0, role_momentum_completeness=80.0,
        situation=29.3, situation_completeness=100.0,
        role_trend=82.2, proven_heat=None, emerging_heat=None,
        evidence_quality=52.3,
    )
    out_rachaad = find_tension(rachaad)
    r.append(check(
        "Rachaad White real week-10-2025 row (unmasked, genuine gap) -> type=contradiction, unaffected by the masking fix",
        out_rachaad["tension_type"] == "contradiction",
    ))

    # A masked role_momentum must never win a false contradiction against
    # a real td_opportunity/situation value merely because 50.0 happens
    # to sit far from them.
    masked_but_would_have_contradicted = _row(
        market_value_score=None,
        td_opportunity=95.0, td_opportunity_completeness=100.0,
        role_momentum=50.0, role_momentum_completeness=0.0,
        situation=90.0, situation_completeness=100.0,
        role_trend=None, proven_heat=None, emerging_heat=None,
        evidence_quality=80.0,
    )
    out_masked_gap = find_tension(masked_but_would_have_contradicted)
    r.append(check(
        "a masked role_momentum=50.0 never manufactures a false contradiction against real td_opportunity=95/situation=90 -- type=forming",
        out_masked_gap["tension_type"] == "forming",
    ))

    # The Golden worked example's own role_momentum=50.0 is a REAL reading
    # (its completeness default is 100.0, unmodified by that test's own
    # overrides) -- must still blend into internal_avg exactly as before.
    r.append(check(
        "Golden worked example's role_momentum=50.0 has real completeness (100.0, untouched) -- still type=divergence, unaffected",
        find_tension(golden)["tension_type"] == "divergence",
    ))

    print()
    p = sum(r)
    print(f"{p}/{len(r)} checks passed")
    raise SystemExit(0 if p == len(r) else 1)
