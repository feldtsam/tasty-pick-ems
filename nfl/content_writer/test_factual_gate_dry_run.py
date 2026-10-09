"""
Item 3 -- reproducible dry run of the factual gate over the committed
week-5 2026 fixture (fixtures/week5_2026_llm_cards.json: the 16 approved
cards that carried model-written text, each paired with its own stub-week
row). READ-ONLY: no network, no database, no writes outside stdout and an
optional --json path.

Two modes:
  python3 test_factual_gate_dry_run.py --report [--json out.json]
      Prints the metrics table and every card's verdict. Never fails.
  python3 test_factual_gate_dry_run.py
      Asserts the acceptance criteria of the Item 3 refinement (see
      ACCEPTANCE below) and exits non-zero on any miss.

Fidelity notes (also in the fixture's own meta): consensus_price_american
is injected from the card's stored odds (the exact generation-time
value). market_value_score at generation time is not recoverable, so a
citation/number that depends on a market key is classed UNREPLAYABLE and
excluded from the verdict -- never treated as supported, never treated as
failed. Everything else runs through the real production functions:
curate_home_shelves._enforce_factual_gate, generate_nfl_shelf_card_
content.run_all_validators, the real deterministic fallback builders.
"""
import contextlib
import io
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "voice"))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "api"))

import pandas as pd  # noqa: E402

import curate_home_shelves as chs  # noqa: E402
import generate_nfl_shelf_card_content as gnscc  # noqa: E402
from card_writer_common import flatten_source_facts  # noqa: E402
from editorial_lenses import citable_fields_for_lens, resolve_editorial_lens  # noqa: E402
from factual_validation import find_trend_claims, trend_evidence_for_row  # noqa: E402
from nfl_writer_common import build_nfl_writer_candidate  # noqa: E402
from shelves import add_red_zone_trend_windows  # noqa: E402

FIXTURE = HERE / "fixtures" / "week5_2026_llm_cards.json"
MARKET_KEYS = {"market_value_score", "market_value_completeness"}

# The six legitimate uncertainty sentences the first dry run (2026-10-09)
# found wrongly flagged in `story`. Ground truth for "legitimate
# uncertainty language causes no replacement".
KNOWN_DISCLAIMER_CARDS = {"Cole Kmet", "Zach Ertz", "Noah Fant", "Darius Cooper", "Josh Cameron", "Jahdae Walker"}
# The seven unsupported Red Zone titles. Ground truth for "still fail".
KNOWN_UNSUPPORTED_TITLES = {"Noah Fant", "Wan'Dale Robinson", "Josh Cameron", "Austin Hooper", "Matthew Hibner", "Chris Moore", "Jahdae Walker"}


def load_fixture():
    data = json.load(open(FIXTURE))
    cards = data["cards"]
    frame = pd.DataFrame([{**c["stub_row"], "consensus_price_american": float(c["odds_at_generation"]) if c["odds_at_generation"] is not None else None} for c in cards])
    frame = add_red_zone_trend_windows(frame)
    by_pid = {r["player_id"]: r for _, r in frame.iterrows()}
    return data["meta"], cards, by_pid


def _unreplayable(issue, why):
    if issue.get("check") == "citation" and any(k in (issue.get("issue") or "") for k in MARKET_KEYS):
        return True
    if issue.get("check") == "numeric_grounding":
        idx = issue.get("reason_index")
        keys = set(why[idx].get("source_fact_keys") or []) if isinstance(idx, int) and idx < len(why) else set()
        return bool(keys & MARKET_KEYS)
    return False


def replay_card(card, row):
    shelf_name = chs._shelf_unslug(card["shelf"])
    story = chs._story_for_row(row, shelf_name) if shelf_name in chs.SHELF_SLUG else None
    fallback_title = story["headline"] if story else card["title"]
    fallback_why = chs._deterministic_why_reasons(row, shelf_name, story) if story else list(card["why_reasons"] or [])
    why = [{"pillar": w.get("pillar"), "stars": w.get("stars"), "reason_text": w.get("text"), "source_fact_keys": w.get("citation")} for w in (card.get("why_reasons") or [])]
    candidate = build_nfl_writer_candidate(row.to_dict())
    lens = resolve_editorial_lens(shelf_name, candidate)
    sf = gnscc.strip_masked_role_fields(flatten_source_facts(candidate, citable_fields_for_lens(lens)), candidate)
    output = {"title": card["title"], "story": card.get("story"), "why_reasons": why}
    # Receipt normalization (trim > 3) lives in the writer, ahead of the
    # validators; a stored card re-enters the same way the writer's own
    # output would.
    output, receipt_notes = gnscc.normalize_receipts(output) if hasattr(gnscc, "normalize_receipts") else (output, [])
    issues = gnscc.run_all_validators(output, sf)
    warnings = gnscc.run_all_warnings(output) + list(receipt_notes)
    unreplayable = [i for i in issues if _unreplayable(i, why)]
    issues = [i for i in issues if not _unreplayable(i, why)]
    written_why = chs._llm_why_reasons_for_write(output["why_reasons"])
    plan = {
        "full_row": row, "gated_out": False, "r": {"home_shelf": shelf_name, "player_id": card["player_id"]},
        "title": card["title"], "story_text": card.get("story"), "editorial_sentence": card.get("editorial_sentence"),
        "model_name": "fixture-llm", "validation_passed": not issues, "validation_issues": list(issues), "validation_warnings": list(warnings),
        "why_reasons": written_why, "fallback_title": fallback_title, "fallback_why_reasons": fallback_why,
    }
    with contextlib.redirect_stdout(io.StringIO()):
        chs._enforce_factual_gate(plan)
    gate = [g for g in plan["validation_issues"] if g.get("check") == "factual_gate"]
    actions = [g.get("action") for g in gate]
    ev = trend_evidence_for_row(row, shelf_name)
    affirmative_hits = {
        field: [c for c in find_trend_claims(text) if c.get("kind", "affirmative") == "affirmative"]
        for field, text in (("title", card["title"]), ("story", card.get("story")))
    }
    return {
        "player": card["player_name"], "shelf": card["shelf"], "rank": card["rank"], "title": card["title"],
        "evidence": ev["status"], "n_receipts_in": len(card.get("why_reasons") or []),
        "hard": sorted({r for g in gate for r in (g.get("reasons") or [])}),
        "actions": actions, "withheld": bool(plan.get("gated_out")), "withheld_reason": plan.get("withheld_reason"),
        "replaced_text": "replaced_by_deterministic_fallback" in actions or "fallback_title_replaced_by_safe_title" in actions,
        "receipts_replaced": "receipts_replaced_by_deterministic" in actions,
        "new_title": plan.get("title"), "n_receipts_out": len(plan.get("why_reasons") or []),
        "issues": plan["validation_issues"], "warnings": plan.get("validation_warnings") or [],
        "unreplayable": unreplayable, "affirmative_hits": affirmative_hits,
    }


def run_all():
    meta, cards, by_pid = load_fixture()
    return meta, [replay_card(c, by_pid[c["player_id"]]) for c in cards]


def metrics(results):
    m = {}
    m["cards"] = len(results)
    m["text_replaced"] = sum(1 for r in results if r["replaced_text"])
    m["withheld"] = sum(1 for r in results if r["withheld"])
    m["receipts_only_replaced"] = sum(1 for r in results if r["receipts_replaced"] and not r["replaced_text"])
    m["hard_failures_by_check"] = dict(Counter(k for r in results for k in r["hard"]))
    m["cards_with_any_hard_failure"] = sum(1 for r in results if r["hard"] or r["withheld"])
    m["warn_only_findings_by_check"] = dict(Counter(w.get("check") for r in results for w in r["warnings"]))
    m["deferred_numeric_grounding_cards"] = sum(1 for r in results if any(i.get("would_have_gated") for i in r["issues"]))
    m["zero_receipts_in"] = sum(1 for r in results if r["n_receipts_in"] == 0)
    m["zero_receipts_out"] = sum(1 for r in results if not r["withheld"] and r["n_receipts_out"] == 0)
    surviving = [r["player"] for r in results if not r["replaced_text"] and not r["withheld"] and r["evidence"] == "masked"
                 and (r["affirmative_hits"]["title"] or r["affirmative_hits"]["story"])]
    m["unsupported_affirmative_claims_surviving"] = surviving
    wrongly = [r["player"] for r in results if r["player"] in KNOWN_DISCLAIMER_CARDS and r["replaced_text"]
               and any(i.get("check") == "trend_claim" and i.get("field") != "title" for i in r["issues"])
               and not any(i.get("check") == "trend_claim" and i.get("field") == "title" for i in r["issues"])]
    m["disclaimers_wrongly_rejected"] = wrongly
    m["valid_cards_modified_only_for_extra_receipts"] = [r["player"] for r in results if r["replaced_text"] and r["n_receipts_in"] > 3
                                                         and r["hard"] == ["schema_shape"]]
    m["unsupported_titles_still_failing"] = sorted(r["player"] for r in results if r["player"] in KNOWN_UNSUPPORTED_TITLES and r["replaced_text"])
    m["unreplayable_market_issue_cards"] = sum(1 for r in results if r["unreplayable"])
    return m


def print_report(meta, results):
    m = metrics(results)
    print(f"fixture: {meta['season']} week {meta['week']}, {m['cards']} cards")
    for k, v in m.items():
        if k != "cards":
            print(f"  {k}: {v}")
    print()
    for r in results:
        verdict = "WITHHELD" if r["withheld"] else ("REPLACED" if r["replaced_text"] else ("RECEIPTS->DET" if r["receipts_replaced"] else "kept"))
        print(f"- {r['player']} [{r['shelf']} r{r['rank']}] {verdict} hard={r['hard']} receipts {r['n_receipts_in']}->{r['n_receipts_out']} evidence={r['evidence']}")
        for i in r["issues"]:
            if i.get("check") in ("trend_claim", "trend_claim_ambiguous", "numeric_grounding", "citation", "schema_shape", "pillar_field_consistency", "star_consistency", "receipts_missing", "receipts_below_minimum"):
                desc = i.get("issue") or f"{i.get('field')} {i.get('phrases')} kind={i.get('kind')} reason={i.get('reason')}"
                print(f"    {i.get('check')}{' [deferred]' if i.get('would_have_gated') else ''}: {desc}")
        for w in r["warnings"]:
            if w.get("check") in ("receipts_trimmed", "trend_claim_ambiguous"):
                print(f"    warn {w.get('check')}: {w}")
        if r["replaced_text"] or r["withheld"]:
            print(f"    -> {r['withheld_reason'] if r['withheld'] else r['new_title']}")


if __name__ == "__main__":
    report_mode = "--report" in sys.argv
    meta, results = run_all()
    if "--json" in sys.argv:
        json.dump({"meta": meta, "metrics": metrics(results), "results": results}, open(sys.argv[sys.argv.index("--json") + 1], "w"), indent=1, default=str)
    if report_mode:
        print_report(meta, results)
        raise SystemExit(0)

    m = metrics(results)
    checks = []

    def check(label, cond):
        print(f"[{'PASS' if cond else 'FAIL'}] {label}")
        checks.append(bool(cond))

    # ACCEPTANCE (Item 3 refinement)
    check("1. all 7 unsupported Red Zone titles still fail the title gate", set(m["unsupported_titles_still_failing"]) == KNOWN_UNSUPPORTED_TITLES)
    check("2. legitimate uncertainty language causes no replacement (6 known disclaimer sentences)", m["disclaimers_wrongly_rejected"] == [])
    check("4. four valid receipts do not trigger card replacement", m["valid_cards_modified_only_for_extra_receipts"] == [])
    check("5. no card leaves the gate with zero receipts", m["zero_receipts_out"] == 0)
    check("5b. every zero-receipt input either got verified deterministic receipts or was withheld",
          all(r["withheld"] or r["n_receipts_out"] > 0 for r in results if r["n_receipts_in"] == 0))
    check("6. numeric-grounding exceptions are recorded in validation_issues with would_have_gated (none hard-gated during the window)",
          all(i.get("would_have_gated") is True and i.get("claim_text") for r in results for i in r["issues"] if i.get("check") == "numeric_grounding")
          and not any("numeric_grounding" in r["hard"] for r in results))
    check("target: no unsupported affirmative trend claim survives on a masked row", m["unsupported_affirmative_claims_surviving"] == [])
    # 3. The contradiction is fixed for FUTURE writes: the Red Zone lens now
    # makes role_momentum_completeness citable, so a 'not available' role
    # reason can cite exactly the key the prompt names. The fixture cards
    # were written under the old lens and still cite td_opportunity_
    # completeness, so pillar_field_consistency keeps flagging them -- that
    # is the historical record, not a regression; see the report.
    check("3. the citation instruction and the writer's evidence no longer conflict (role_momentum_completeness is citable on a Red Zone card)",
          "role_momentum_completeness" in citable_fields_for_lens(resolve_editorial_lens("Red Zone Trends", {})))
    print()
    print(f"{sum(checks)}/{len(checks)} acceptance checks passed")
    raise SystemExit(0 if all(checks) else 1)
