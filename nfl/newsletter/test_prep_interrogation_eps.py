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
    CostMeter, PREP_CONFIG, content_fingerprint, interrogation_reuse_decision, prefilter_candidates,
    process_story, rows_for_write, run_execution, stamp_provenance, write_results,
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
    r.append(check("limited stories are not excluded outright: capped at 35 they compete, and two clear the 30 floor (35, 32); the third (35 - 6 penalty = 29) correctly misses it",
                   {"r0", "r1"} <= sel and "r2" not in sel and any(n["story_key"] == "r2" and n["adjusted_score"] == 29.0 for n in pre["near_misses"])))
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
