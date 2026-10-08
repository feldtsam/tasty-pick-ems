"""
Tests for prep_interrogation_eps.py -- pre-filter, reuse logic, cost
ceiling, dry-run vs write. Every model call and every network call is
mocked; nothing here touches the API or any route. Run:
    python3 nfl/newsletter/test_prep_interrogation_eps.py
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import prep_interrogation_eps as prep
from prep_interrogation_eps import (
    CostMeter, PREP_CONFIG, TIEBREAK_SCALES, base_score, build_summary, content_fingerprint, failure_reason_from_log,
    interrogation_reuse_decision, prefilter_candidates, process_story, rows_for_write, run_execution,
    stale_evaluations_in_pool, stamp_provenance, tiebreak_score, write_results,
)


def check(label, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    return bool(cond)


NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
CLEAN_INTERROGATION = {
    "challenge": {"alternate_explanations": [{"alternate_id": "alt_1", "status": "WEAKENED"}]},
    "primary_alternate": {"alternate_id": "alt_1", "selection_reason": "r"},
    "signal_verdict": "UNRESOLVED",
}


def story(key, family, *, week, confidence, completeness, classification="strong", season=2026, interrogation=None,
          updated_at=None, visible=True, sane=True, player=True):
    ent = {"type": "player", "player_id": f"pid-{key}", "player_name": f"Player {key}"} if player else {"type": "team", "team": key}
    return {
        "id": key, "intelligence_family": family, "entity_key": f"ek-{key}", "primary_signal_name": "sig", "season": season, "week": week,
        "entity": ent, "headline": f"Headline {key}", "story": f"Story {key}", "primary_signal": {"name": "sig", "value": 1},
        "supporting_evidence": ["e"], "confidence": confidence, "completeness": completeness, "evidence_classification": classification,
        "sample_size": 3, "trend_strength": 1.0, "time_window": "w", "related_players": [], "hero_metric": None, "what_changed": None,
        "interrogation": interrogation, "eps": None, "is_visible": visible, "sanity_check_passed": sane,
        "updated_at": (updated_at or NOW).isoformat(),
    }


if __name__ == "__main__":
    r = []

    # ============================================================
    # Pre-filter
    # ============================================================
    pool = (
        [story(f"m{i}", "market_intelligence", week=5, confidence=100, completeness=100) for i in range(8)]       # raw 110 each
        + [story(f"d{i}", "defensive_trends", week=4, confidence=100, completeness=100) for i in range(3)]        # raw 110
        + [story(f"r{i}", "role_changes", week=4, confidence=35, completeness=35, classification="limited") for i in range(3)]  # raw 35
        + [story("c_stale", "coaching_trends", week=3, confidence=100, completeness=100)]                         # stale (expected 4)
        + [story("m_last_season", "market_intelligence", week=5, season=2025, confidence=100, completeness=100)]  # stale season
        + [story("hidden", "market_intelligence", week=5, confidence=100, completeness=100, visible=False)]
        + [story("weak", "defensive_trends", week=4, confidence=10, completeness=10, classification="limited")]   # raw 10, under floor
    )
    pre = prefilter_candidates(pool, 2026, 5, dict(PREP_CONFIG, max_candidates=20))
    sel = {c["story_key"] for c in pre["candidates"]}
    r.append(check("pre-filter is labelled 'interrogation candidates'", pre["label"] == "interrogation candidates"))
    r.append(check("stale week and stale season are excluded and reported with the expected week",
                   {s["story_key"] for s in pre["stale_excluded"]} == {"c_stale", "m_last_season"}
                   and next(s for s in pre["stale_excluded"] if s["story_key"] == "c_stale")["expected_week"] == 4))
    r.append(check("hidden/sanity-failed rows are excluded and counted", pre["hidden_excluded"] == 1 and "hidden" not in sel))
    r.append(check("coaching_trends is reported as a family without current stories", pre["families_without_current_stories"] == ["coaching_trends"]))
    r.append(check("the score floor keeps a weak story out even with room left (floor 30, raw 10)", "weak" not in sel and len(sel) < 20))
    r2_raw = base_score(next(s for s in pool if s["id"] == "r2"))["raw_score"]
    r.append(check(f"limited stories are not excluded outright: capped at 35 they compete; the first two clear the 30 floor, and the third's fate follows the floor exactly (raw {r2_raw} - 6 penalty vs 30)",
                   {"r0", "r1"} <= sel and (("r2" in sel) == (round(r2_raw - 6.0, 2) >= 30.0))))
    ranks = {c["story_key"]: c["selection_rank"] for c in pre["candidates"]}
    r.append(check("family penalty interleaves equal-strength families: the three defensive stories and the first three market stories fill ranks 1-6 together, before the 4th market story",
                   max(ranks[k] for k in ("d0", "d1", "d2", "m0", "m1", "m2")) == 6 and ranks["m3"] == 7 and ranks["d0"] < ranks["m1"] < ranks["m2"]))
    pens = {c["story_key"]: c["family_penalty"] for c in pre["candidates"]}
    r.append(check("penalty values are the configured diminishing ladder (0, 3, 6, 9, 12, 15 then capped)",
                   pens["m0"] == 0 and pens["m1"] == 3 and pens["m2"] == 6 and pens["m7"] == 15 and PREP_CONFIG["family_penalty"] == (0.0, 3.0, 6.0, 9.0, 12.0, 15.0)))
    r.append(check("a strong family still dominates: all 8 market stories (110 raw) selected ahead of the 35-point limited ones",
                   all(ranks[f"m{i}"] < ranks["r0"] for i in range(8))))
    r.append(check("no candidate carries any Big One / Watchlist status", not any("big_one_eligible" in c or "watchlist_eligible" in c for c in pre["candidates"])))

    small = prefilter_candidates(pool, 2026, 5, dict(PREP_CONFIG, max_candidates=5))
    r.append(check("max_candidates caps the list and the next ranked stories are reported as near misses (capped at 10) with adjusted scores, weakest last",
                   len(small["candidates"]) == 5 and len(small["near_misses"]) == 10
                   and small["near_misses"][0]["adjusted_score"] >= small["near_misses"][-1]["adjusted_score"]
                   and small["near_misses"][-1]["story_key"] == "weak"
                   and all("adjusted_score" in n and "raw_score" in n for n in small["near_misses"])))
    r.append(check("near-miss count is capped at the configured 10",
                   len(prefilter_candidates(pool + [story(f"x{i}", "defensive_trends", week=4, confidence=60, completeness=60) for i in range(12)], 2026, 5, dict(PREP_CONFIG, max_candidates=5))["near_misses"]) == 10))
    r.append(check("pre-filter is deterministic (same input, same order twice)",
                   [c["story_key"] for c in prefilter_candidates(pool, 2026, 5)["candidates"]] == [c["story_key"] for c in pre["candidates"]]))

    # ============================================================
    # Reuse logic
    # ============================================================
    base = story("m0", "market_intelligence", week=5, confidence=100, completeness=100)
    stamped = stamp_provenance(CLEAN_INTERROGATION, base, NOW)
    fresh = dict(base, interrogation=stamped, updated_at=(NOW + timedelta(seconds=20)).isoformat())
    r.append(check("null interrogation -> compute", interrogation_reuse_decision(dict(base, interrogation=None))[0] == "compute"))
    r.append(check("stored interrogation with matching fingerprint, row rewritten seconds later (this step's own write) -> reuse",
                   interrogation_reuse_decision(fresh) == ("reuse", "fingerprint unchanged")))
    changed = dict(fresh, headline="Different headline")
    r.append(check("content change -> compute (fingerprint mismatch)", interrogation_reuse_decision(changed) == ("compute", "story content changed since the stored interrogation")))
    refreshed = dict(fresh, updated_at=(NOW + timedelta(hours=6)).isoformat())
    r.append(check("Market story rewritten hours after the interrogation (Market Refresh) -> compute",
                   interrogation_reuse_decision(refreshed) == ("compute", "Market Refresh after the stored interrogation")))
    role_refreshed = dict(story("r0", "role_changes", week=4, confidence=35, completeness=35), updated_at=(NOW + timedelta(hours=6)).isoformat())
    role_refreshed["interrogation"] = stamp_provenance(CLEAN_INTERROGATION, role_refreshed, NOW)
    r.append(check("the Market Refresh rule applies to Market stories only: a non-Market row rewritten later still reuses",
                   interrogation_reuse_decision(role_refreshed)[0] == "reuse"))
    r.append(check("--force -> compute regardless", interrogation_reuse_decision(fresh, force=True) == ("compute", "forced")))
    legacy = dict(fresh, interrogation=CLEAN_INTERROGATION)  # no provenance block
    r.append(check("an interrogation without provenance (not produced by this step) is recomputed", interrogation_reuse_decision(legacy)[0] == "compute"))
    r.append(check("fingerprint ignores fields outside the content set (updated_at, eps, is_visible)",
                   content_fingerprint(base) == content_fingerprint(dict(base, updated_at="x", eps={"a": 1}, is_visible=False))
                   and content_fingerprint(base) != content_fingerprint(dict(base, supporting_evidence=["other"]))))

    # ============================================================
    # process_story: retry once, record failed; eps always recomputed
    # ============================================================
    ctx = {"season": 2026, "as_of_week": None, "reconciled_weeks": [], "rows_by_player": {}, "price_rows_by_player": {}, "notes": []}
    calls = {"interrogate": 0, "eps": 0}

    def fake_interrogate_none(story_, api_key, prior_history=None, market_data=None):
        calls["interrogate"] += 1
        return None

    def fake_interrogate_ok(story_, api_key, prior_history=None, market_data=None):
        calls["interrogate"] += 1
        return dict(CLEAN_INTERROGATION)

    def fake_eps(story_, interrogation, prior_history, api_key):
        calls["eps"] += 1
        return {"eps_version": "v1", "computed_at": None, "dimensions": {"evidence_strength": {"score": 50}}, "composite_score": 50.0,
                "gates": {"big_one_eligible": bool(interrogation), "big_one_blocked_reason": None if interrogation else "no completed interrogation",
                          "watchlist_eligible": False, "watchlist_blocked_reason": "composite_score 50.0 below floor 55; no completed interrogation"}}

    rec = process_story(dict(base, interrogation=None), ctx, "k", force=False, now=NOW, interrogate=fake_interrogate_none, eps_fn=fake_eps)
    r.append(check("a None interrogation is retried exactly once, then recorded as failed; eps still runs",
                   calls["interrogate"] == 2 and rec["interrogation_status"] == "failed" and rec["interrogation_attempts"] == 2
                   and rec["eps_status"] == "completed" and rec["interrogation"] is None))
    calls.update(interrogate=0, eps=0)
    rec = process_story(dict(base, interrogation=None), ctx, "k", force=False, now=NOW, interrogate=fake_interrogate_ok, eps_fn=fake_eps)
    r.append(check("a completed interrogation is stamped with provenance (fingerprint + computed_at) and eps gets computed_at",
                   rec["interrogation_status"] == "completed" and rec["interrogation"]["provenance"]["content_fingerprint"] == content_fingerprint(base)
                   and rec["interrogation"]["provenance"]["computed_at"] == NOW.isoformat() and rec["eps"]["computed_at"] == NOW.isoformat()))
    calls.update(interrogate=0, eps=0)
    rec = process_story(fresh, ctx, "k", force=False, now=NOW, interrogate=fake_interrogate_ok, eps_fn=fake_eps)
    r.append(check("a reusable interrogation makes NO interrogation call but eps is still recomputed",
                   calls["interrogate"] == 0 and calls["eps"] == 1 and rec["interrogation_status"] == "reused" and rec["interrogation"] == stamped))
    calls.update(interrogate=0, eps=0)
    rec = process_story(fresh, ctx, "k", force=True, now=NOW, interrogate=fake_interrogate_ok, eps_fn=fake_eps)
    r.append(check("--force recomputes a reusable interrogation", calls["interrogate"] == 1 and rec["interrogation_status"] == "completed"))

    # ============================================================
    # Cost ceiling
    # ============================================================
    cands = prefilter_candidates([story(f"m{i}", "market_intelligence", week=5, confidence=100, completeness=100) for i in range(10)], 2026, 5)["candidates"]
    by_key = {c["story_key"]: story(c["story_key"], "market_intelligence", week=5, confidence=100, completeness=100) for c in cands}
    meter = CostMeter()  # not installed: the fakes record usage directly

    def costly_interrogate(story_, api_key, prior_history=None, market_data=None):
        meter.record({"input_tokens": 0, "output_tokens": 20000})  # $0.30 per story
        return dict(CLEAN_INTERROGATION)

    def free_eps(story_, interrogation, prior_history, api_key):
        return fake_eps(story_, interrogation, prior_history, api_key)

    ex = run_execution(cands, by_key, ctx, "k", force=False, max_cost_usd=1.0, concurrency=1, meter=meter, interrogate=costly_interrogate, eps_fn=free_eps, now=NOW)
    r.append(check("cost ceiling: with $0.30/story and a $1 ceiling, the run stops cleanly after 3 stories and reports the rest as skipped",
                   ex["cost_ceiling_hit"] is True and len(ex["results"]) == 3 and len(ex["skipped_for_cost"]) == 7 and ex["cost_at_stop"] == 0.9))
    meter2 = CostMeter()
    ex2 = run_execution(cands[:3], by_key, ctx, "k", force=False, max_cost_usd=4.0, concurrency=4, meter=meter2, interrogate=lambda *a, **k: (meter2.record({"output_tokens": 1000}) or dict(CLEAN_INTERROGATION)), eps_fn=free_eps, now=NOW)
    r.append(check("under the ceiling everything runs; results come back in selection order; concurrency 4 is the default",
                   ex2["cost_ceiling_hit"] is False and [x["selection_rank"] for x in ex2["results"]] == [1, 2, 3] and PREP_CONFIG["concurrency"] == 4))
    r.append(check("default ceiling is $4 and default candidate count is 20", PREP_CONFIG["max_cost_usd"] == 4.0 and PREP_CONFIG["max_candidates"] == 20))

    # ============================================================
    # Dry run vs write
    # ============================================================
    results = ex2["results"]
    rows = rows_for_write(results, by_key)
    r.append(check("rows for write are the read rows with interrogation/eps replaced and DB-only fields dropped",
                   len(rows) == 3 and all("id" not in row and "updated_at" not in row for row in rows)
                   and rows[0]["headline"] == by_key[rows[0]["entity_key"].replace("ek-", "")]["headline"]
                   and rows[0]["interrogation"]["provenance"]["produced_by"] == "prep_interrogation_eps" and rows[0]["eps"]["composite_score"] == 50.0))
    sent = {}

    def fake_write(story_rows, history_rows, secret):
        sent["rows"] = story_rows; sent["history"] = history_rows; sent["secret"] = secret
        return {"success": True, "status_code": 200, "error": None, "response_body": "{}"}

    out = write_results(results, by_key, "s3cret", write_fn=fake_write)
    r.append(check("--write goes through the signed write path with {stories, history=[]} and reports the outcome",
                   out["attempted"] and out["rows"] == 3 and out["success"] is True and sent["history"] == [] and sent["secret"] == "s3cret" and len(sent["rows"]) == 3))
    r.append(check("the CLI defaults to dry run: --write is an opt-in store_true flag",
                   "--write" in Path(prep.__file__).read_text() and 'action="store_true"' in Path(prep.__file__).read_text()
                   and "if args.write:" in Path(prep.__file__).read_text()))
    r.append(check("no direct database access anywhere in the module (no supabase / psycopg / sql imports)",
                   not any(tok in Path(prep.__file__).read_text().lower() for tok in ("supabase", "psycopg", "sqlalchemy", "postgrest"))))
    r.append(check("nothing with no interrogation and no eps is ever written", rows_for_write([{"story_key": "m0", "interrogation": None, "eps": None}], by_key) == []))

    # ============================================================
    # Tie-break (item 1): classification-safe, fixed scales, deterministic
    # ============================================================
    bonuses = sorted(set(PREP_CONFIG["classification_bonus"].values()))
    min_gap = min(b - a for a, b in zip(bonuses, bonuses[1:]))
    r.append(check(
        f"PROOF from the module's own constants: tiebreak_cap ({PREP_CONFIG['tiebreak_cap']}) is strictly less than the smallest classification gap ({min_gap})",
        PREP_CONFIG["tiebreak_cap"] < min_gap,
    ))
    mk = lambda **o: story("t", "market_intelligence", week=5, confidence=100, completeness=100, **o)  # noqa: E731
    big = dict(mk(), hero_metric={"delta_value": 40.0}, sample_size=12)      # saturates both metrics
    r.append(check("tie-break maxes out at exactly the cap when both metrics saturate", tiebreak_score(big)["tiebreak"] == PREP_CONFIG["tiebreak_cap"]))
    ms = TIEBREAK_SCALES["market_intelligence"]
    half = dict(mk(), hero_metric={"delta_value": ms["magnitude"] / 2}, sample_size=ms["sample"] / 4)   # 0.5 and 0.25 -> mean 0.375
    r.append(check("formula: cap x mean(clamp(metric/scale)): half the magnitude scale and a quarter of the book scale -> cap x 0.375",
                   tiebreak_score(half)["tiebreak"] == round(PREP_CONFIG["tiebreak_cap"] * 0.375, 2)))
    r.append(check("scales are fixed per family and documented (market 32 pp / 8 books; defensive & coaching 40 / 6 games; role 40 above the 60 threshold / 6 games)",
                   TIEBREAK_SCALES["market_intelligence"] == {"magnitude": 32.0, "sample": 8.0}
                   and TIEBREAK_SCALES["defensive_trends"] == {"magnitude": 40.0, "sample": 6.0} and TIEBREAK_SCALES["coaching_trends"] == {"magnitude": 40.0, "sample": 6.0}
                   and TIEBREAK_SCALES["role_changes"] == {"magnitude": 40.0, "magnitude_offset": 60.0, "sample": 6.0}))
    one_only = dict(mk(), hero_metric=None, primary_signal={"name": "deviation_pp", "value": -ms["magnitude"]}, sample_size=None)
    r.append(check("a missing metric is left out of the average, and primary_signal.value is the market fallback for magnitude",
                   tiebreak_score(one_only)["tiebreak"] == PREP_CONFIG["tiebreak_cap"] and list(tiebreak_score(one_only)["tiebreak_inputs"]) == ["magnitude"]))
    r.append(check("role_changes magnitude is measured above the family's 60 threshold: role_momentum 60 -> 0, 100 -> 1",
                   tiebreak_score(story("r", "role_changes", week=4, confidence=50, completeness=50, player=True) | {"trend_strength": 60.0, "sample_size": None})["tiebreak"] == 0.0
                   and tiebreak_score(story("r", "role_changes", week=4, confidence=50, completeness=50) | {"trend_strength": 100.0, "sample_size": None})["tiebreak"] == PREP_CONFIG["tiebreak_cap"]))
    # Classification priority: equal evidence, lower tier with max tie-break vs higher tier with zero tie-break.
    lim_max = dict(story("L", "defensive_trends", week=4, confidence=30, completeness=30, classification="limited"), trend_strength=100.0, sample_size=12)
    mod_zero = dict(story("M", "defensive_trends", week=4, confidence=30, completeness=30, classification="moderate"), trend_strength=0.0, sample_size=0)
    str_zero = dict(story("S", "defensive_trends", week=4, confidence=30, completeness=30, classification="strong"), trend_strength=0.0, sample_size=0)
    order = [c["story_key"] for c in prefilter_candidates([lim_max, mod_zero, str_zero], 2026, 5, dict(PREP_CONFIG, min_score=0.0))["candidates"]]
    r.append(check("no story ranks above a higher-classification story on the tie-break alone (equal evidence: strong > moderate > limited even with limited at max tie-break)",
                   order == ["S", "M", "L"] and base_score(lim_max)["tiebreak"] == PREP_CONFIG["tiebreak_cap"] and base_score(mod_zero)["tiebreak"] == 0.0))
    r.append(check("a tie-break only reorders stories of the SAME classification and evidence",
                   [c["story_key"] for c in prefilter_candidates([dict(mk(), id="low", hero_metric={"delta_value": 2.0}, sample_size=1), dict(mk(), id="hi", hero_metric={"delta_value": 15.0}, sample_size=5)], 2026, 5)["candidates"]] == ["hi", "low"]))
    mixed = [dict(story(f"m{i}", "market_intelligence", week=5, confidence=100, completeness=100), hero_metric={"delta_value": 5 + i}, sample_size=1 + (i % 5)) for i in range(12)]
    runs = [[c["story_key"] for c in prefilter_candidates(mixed, 2026, 5)["candidates"]] for _ in range(5)]
    r.append(check("order is identical across repeated runs (5 runs on a shuffled-looking pool)", all(x == runs[0] for x in runs)))
    r.append(check("tie-break inputs are reported on each candidate", all("tiebreak" in c and "tiebreak_inputs" in c for c in prefilter_candidates(mixed, 2026, 5)["candidates"])))

    # ============================================================
    # Failure handling (item 2): no partial writes, no stale qualifiers
    # ============================================================
    stale_story = story("stale", "market_intelligence", week=5, confidence=100, completeness=100, updated_at=NOW - timedelta(days=1))
    stale_story["interrogation"] = stamp_provenance(dict(CLEAN_INTERROGATION), stale_story, NOW - timedelta(days=1))
    stale_story["eps"] = {"eps_version": "v1", "computed_at": (NOW - timedelta(days=1)).isoformat(), "dimensions": {"evidence_strength": {"score": 100}},
                          "composite_score": 70.0, "gates": {"big_one_eligible": True, "watchlist_eligible": True}}
    stale_story["headline"] = "Changed since then"  # fingerprint no longer matches -> must recompute

    def failing_interrogate(story_, api_key, prior_history=None, market_data=None):
        print("[story_interrogation] confidence-escalation violation persisted after retry — returning None, not a violating record.")
        return None

    def failing_eps(story_, interrogation, prior_history, api_key):
        print("[eps] malformed shape persisted after retry — returning None rather than crashing downstream.")
        return None

    cand = prefilter_candidates([stale_story], 2026, 5)["candidates"]
    m3 = CostMeter()
    ex3 = run_execution(cand, {"stale": stale_story}, ctx, "k", force=False, max_cost_usd=4.0, concurrency=2, meter=m3, interrogate=failing_interrogate, eps_fn=failing_eps, now=NOW)
    rec = ex3["results"][0]
    summ = build_summary({"considered": 1, "stale_excluded": [], "hidden_excluded": 0, "families_without_current_stories": [], "candidates": cand, "near_misses": [], "family_counts": {}, "config": {}},
                         ex3, m3, season=2026, upcoming_week=5, mode="dry-run", write_result=None, started_at=NOW, ctx_notes=[], reads=[],
                         stale_in_pool=stale_evaluations_in_pool([stale_story], NOW))
    r.append(check("a story with an older, eligible-looking stored interrogation+eps whose fresh run fails is NOT a qualifier",
                   summ["big_one_qualifiers"] == [] and summ["watchlist_qualifiers"] == [] and rec["evaluated_this_run"] is False))
    r.append(check("...and is NOT written (rows_for_write is empty), so its row keeps its previous database state",
                   rows_for_write(ex3["results"], {"stale": stale_story}) == []))
    r.append(check("...and is listed under 'not written (interrogation failed)' with the marker and the per-attempt reasons",
                   len(summ["not_written_interrogation_failed"]) == 1
                   and summ["not_written_interrogation_failed"][0]["status"] == "carries stale or no evaluation, excluded from this run's qualifiers"
                   and summ["not_written_interrogation_failed"][0]["reasons"] == ["confidence-escalation check persisted after retry"] * 2
                   and summ["not_written_interrogation_failed"][0]["same_reason_both_attempts"] is True))
    r.append(check("the blocked-reason aggregates also count only stories evaluated this run", summ["big_one_blocked_reasons"] == {} and summ["watchlist_blocked_reasons"] == {}))
    r.append(check("the stale-evaluation report flags the stored evaluation (computed before this run, content changed) with its stored gates",
                   summ["stale_evaluations_in_pool"][0]["stored_big_one_eligible"] is True
                   and "interrogation computed before this run" in summ["stale_evaluations_in_pool"][0]["reasons"]
                   and "story content changed since the interrogation" in summ["stale_evaluations_in_pool"][0]["reasons"]
                   and "eps computed before this run (or undated)" in summ["stale_evaluations_in_pool"][0]["reasons"]))
    r.append(check("a stored interrogation without provenance is reported as 'age unknown'",
                   stale_evaluations_in_pool([dict(base, interrogation=CLEAN_INTERROGATION)], NOW)[0]["reasons"] == ["interrogation has no provenance (age unknown)"]))

    # EPS failed, interrogation fine -> listed under 'not written (EPS failed)', not written, not a qualifier
    ok_story = story("okint", "market_intelligence", week=5, confidence=100, completeness=100)
    ex4 = run_execution(prefilter_candidates([ok_story], 2026, 5)["candidates"], {"okint": ok_story}, ctx, "k", force=False, max_cost_usd=4.0, concurrency=1, meter=CostMeter(),
                        interrogate=fake_interrogate_ok, eps_fn=failing_eps, now=NOW)
    summ4 = build_summary({"considered": 1, "stale_excluded": [], "hidden_excluded": 0, "families_without_current_stories": [], "candidates": [], "near_misses": [], "family_counts": {}, "config": {}},
                          ex4, CostMeter(), season=2026, upcoming_week=5, mode="dry-run", write_result=None, started_at=NOW, ctx_notes=[], reads=[])
    r.append(check("EPS failure with a completed interrogation: not written, listed under 'not written (EPS failed)' with the captured reason",
                   rows_for_write(ex4["results"], {"okint": ok_story}) == [] and len(summ4["not_written_eps_failed"]) == 1
                   and summ4["not_written_eps_failed"][0]["reason"] == "malformed response shape persisted after retry"))
    r.append(check("failure_reason_from_log: no failure line -> None; API failure line -> 'API call failed'",
                   failure_reason_from_log("[story_interrogation] something fine\n", "[story_interrogation]") is None
                   and failure_reason_from_log("[eps] API call failed: ValueError('x')", "[eps]") == "API call failed"))

    # ============================================================
    # Offline inputs: diagnostics only, never combined with --write
    # ============================================================
    import contextlib, io, json, tempfile
    with tempfile.TemporaryDirectory() as d:
        pj = Path(d) / "pool.json"; cj = Path(d) / "ctx.json"
        pj.write_text(json.dumps([base])); cj.write_text(json.dumps(ctx))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = prep.main(["--season", "2026", "--week", "5", "--write", "--pool-json", str(pj), "--context-json", str(cj)])
        r.append(check("--write combined with offline inputs is refused before anything runs (exit 2)", code == 2 and "cannot be combined" in err.getvalue()))

    print()
    passed = sum(r)
    print(f"{passed}/{len(r)} checks passed")
    raise SystemExit(0 if passed == len(r) else 1)
