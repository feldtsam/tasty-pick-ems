"""
run_weekly_brief.py -- selection, the refusals, the persist guard, the
readable draft, and the two end-to-end paths.

Same shape as this directory's other tests: a standalone script, a
check() helper, no live API call and no network anywhere. Every model
call is monkeypatched; every "stored row" is built locally.

    python3 test_run_weekly_brief.py
"""
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "api"))
sys.path.insert(0, str(HERE.parent / "content_writer"))

import run_weekly_brief as rwb  # noqa: E402

r = []


def check(label, cond):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    return bool(cond)


def eps_blob(total=70.0, big_one=True, watchlist=False, dimensions=True):
    blob = {
        "eps_version": "v1",
        "composite_score": total,
        "gates": {"big_one_eligible": big_one, "watchlist_eligible": watchlist},
    }
    if dimensions:
        blob["dimensions"] = {
            "significance": {"score": 70}, "evidence_strength": {"score": 55},
            "betting_relevance": {"score": 60}, "novelty": {"score": 65},
            "story_tension": {"score": 68}, "audience_relevance": {"score": 72},
        }
    return blob


def row(story_id, family="market_intelligence", week=7, season=2026, *, player=True,
        interrogation=True, eps=True, composite=70.0, headline=None, story=None, name="Tory Horton"):
    """One stored nfl_intelligence_stories row, in the read route's shape."""
    entity = ({"type": "player", "player_id": f"PID-{story_id}", "player_name": name}
              if player else {"type": "team", "team": "SEA"})
    return {
        "id": story_id,
        "season": season,
        "week": week,
        "intelligence_family": family,
        "entity": entity,
        "entity_key": f"key-{story_id}",
        "headline": headline or f"{name} headline {story_id}",
        "story": story or f"Body text for {story_id}.",
        "time_window": "3 games",
        "sample_size": 11,
        "completeness": 66,
        "confidence": 70,
        "is_visible": True,
        "sanity_check_passed": True,
        "interrogation": {"interrogation_version": "v3_primary_alternate", "judgment": {}} if interrogation else None,
        "eps": eps_blob(composite) if eps else None,
    }


print("\n-- story selection and mapping")
rows = [
    row("A", "market_intelligence", week=7, composite=80.0),
    row("B", "role_changes", week=6, composite=90.0, name="Bhayshul Tuten"),
    row("C", "defensive_trends", week=6, composite=75.0, player=False),
    row("D", "market_intelligence", week=6, composite=99.0),          # market from the wrong week
    row("E", "role_changes", week=7, composite=99.0),                 # non-market from the wrong week
    row("F", "market_intelligence", week=7, eps=False, composite=0),  # not scored
    row("G", "market_intelligence", week=7, interrogation=False),     # not interrogated
]
sel = rwb.select_qualified(rows, 2026, 7, max_stories=20)
ids = [s["intelligence_story_id"] for s in sel["stories"]]
r.append(check("market comes from the upcoming week, other families from week-1", set(ids) == {"A", "B", "C"}))
r.append(check("ranked by eps.composite_score, highest first", ids == ["B", "A", "C"]))  # 90, 80, 75
r.append(check("rows for the wrong week for their family are excluded", sel["excluded_wrong_week"] == 2))
r.append(check("rows missing interrogation or eps are excluded and listed", len(sel["excluded_not_enriched"]) == 2))
r.append(check("per-family counts reported", sel["by_family"] == {"role_changes": 1, "defensive_trends": 1, "market_intelligence": 1}))
r.append(check("gate counts reported from the pool", sel["gates"]["big_one_eligible"] == 3))

mapped = {s["intelligence_story_id"]: s for s in sel["stories"]}
r.append(check("intelligence_story_id comes from the row's `id`", mapped["A"]["intelligence_story_id"] == "A"))
r.append(check("player_id is lifted out of the entity jsonb", mapped["A"]["player_id"] == "PID-A"))
r.append(check("entity jsonb is flattened to the player name", mapped["A"]["entity"] == "Tory Horton"))
r.append(check("a team-entity story flattens to the team and has no player_id",
               mapped["C"]["entity"] == "SEA" and mapped["C"]["player_id"] is None))
r.append(check("interrogation and eps pass through untouched",
               mapped["A"]["interrogation"] is rows[0]["interrogation"] and mapped["A"]["eps"] is rows[0]["eps"]))
r.append(check("the Story Object carries exactly the Editor's ten contract fields",
               set(mapped["A"]) == {"intelligence_story_id", "player_id", "intelligence_family", "entity",
                                    "headline", "story", "time_window", "sample_size", "interrogation", "eps"}))

eps_no_dims = row("H", week=7)
eps_no_dims["eps"] = {"eps_version": "v1", "composite_score": 50.0, "gates": {}}
r.append(check("an eps blob with no dimensions counts as unscored", not rwb.is_enriched(eps_no_dims)))

capped = rwb.select_qualified([row(f"S{i}", week=7, composite=float(i)) for i in range(30)], 2026, 7, max_stories=5)
r.append(check("the cap keeps the highest-scoring stories and reports what it dropped",
               len(capped["stories"]) == 5 and capped["cap_applied"] == 25
               and capped["stories"][0]["intelligence_story_id"] == "S29"))

print("\n-- refusal: fixture contamination")
fixture = json.loads((HERE / "fixture_v2.json").read_text())
fixture_pool = [{
    "intelligence_story_id": f["intelligence_story_id"], "player_id": f["player_id"],
    "intelligence_family": f["intelligence_family"], "entity": f["entity"], "headline": f["headline"],
    "story": f["story"], "time_window": f["time_window"], "sample_size": f["sample_size"],
    "interrogation": f["interrogation"], "eps": f["eps"],
} for f in fixture]
r.append(check("the real Fixture V2 pool is detected as contaminated", len(rwb.fixture_contamination(fixture_pool)) > 0))
r.append(check("a clean real pool is not flagged", rwb.fixture_contamination(sel["stories"]) == []))
r.append(check("a fixture player smuggled into a real-looking story is caught",
               len(rwb.fixture_contamination([{"intelligence_story_id": "X", "entity": "Marcus Thibodeaux",
                                               "headline": "h", "story": "s", "player_id": "p"}])) == 1))
r.append(check("a synthetic id is caught even with a real-looking name",
               len(rwb.fixture_contamination([{"intelligence_story_id": "SYNTHETIC-FIXTURE-3", "entity": "Josh Allen",
                                               "headline": "h", "story": "s", "player_id": "p"}])) == 1))

print("\n-- persist guard")
may, why = rwb.persist_guard(None)
r.append(check("no existing issue -> may persist", may))
may, why = rwb.persist_guard({"publish_status": "draft"})
r.append(check("existing draft -> may persist (replaced)", may))
may, why = rwb.persist_guard({"publish_status": "approved", "published_at": None})
r.append(check("existing APPROVED issue -> refused", not may and "approved" in why))
may, why = rwb.persist_guard({"publish_status": "published", "published_at": "2026-10-08T22:45:00Z"})
r.append(check("existing PUBLISHED issue -> refused", not may and "published" in why))
may, why = rwb.persist_guard({"publish_status": ""})
r.append(check("a blank status is treated as draft, not as a bypass", may))

print("\n-- cost ceiling")
cheap = rwb.estimate_cost_usd(sel["stories"], "prompt text")
r.append(check("a small pool estimates well under the $1.00 default", 0 < cheap < 1.0))
big = rwb.estimate_cost_usd([dict(s, story="x" * 400_000) for s in sel["stories"]], "prompt text")
r.append(check("a huge pool estimates over the default ceiling", big > rwb.DEFAULT_MAX_COST_USD))

print("\n-- readable markdown draft")
issue = {
    "issue_week": "2026 NFL Week 7",
    "sections": [
        {"section_type": "watchlist", "stories": [{"headline": "Watch this", "body": "One line.",
                                                   "intelligence_story_ids": ["B"],
                                                   "eps_scores": {"eps_total": 61, "big_one_eligible": False, "watchlist_eligible": True}}],
         "cross_references": []},
        {"section_type": "from_the_desk", "stories": [{"headline": "Desk", "body": "Para one.\n\nPara two.",
                                                       "intelligence_story_ids": [],
                                                       "eps_scores": {"eps_total": 0}}],
         "cross_references": []},
        {"section_type": "big_one", "stories": [{"headline": "The big story", "body": "Big body.",
                                                 "intelligence_story_ids": ["A"],
                                                 "eps_scores": {"eps_total": 80, "big_one_eligible": True, "watchlist_eligible": False}}],
         "cross_references": [{"text": "See also the role change.", "refers_to_intelligence_story_id": "B"}]},
    ],
    "notes_for_human_reviewer": "Close call between A and B.",
    "_validation": {"retry_fired": False, "repairs": []},
}
meta = {"season": 2026, "week": 7, "run_at": "2026-10-20T12:00:00+00:00", "selection": sel}
md = rwb.render_markdown(issue, meta, {"passed": True, "summary": "all clear", "needs_review_count": 2, "hard_fails": []})
order = [md.index("From the Desk"), md.index("The Big One"), md.index("The Watchlist")]
r.append(check("sections render in editorial order, not the order the model emitted them", order == sorted(order)))
r.append(check("the draft is labelled a draft", "DRAFT -- not published" in md))
r.append(check("the real week and season come from the data", "Week 7, 2026" in md))
r.append(check("headlines render as headings and bodies as paragraphs",
               "### The big story" in md and "Para one." in md and "Para two." in md))
r.append(check("cross-references render as quoted pointers", "> See also the role change." in md))
r.append(check("the EPS table is present with each story's total", "| EPS total |" in md and "| 80 " in md))
r.append(check("gate booleans are resolved from the candidate pool, not left blank",
               "| True " in md and "| False " in md))
r.append(check("validator result and review count are at the end", "PASSED" in md and "all clear" in md
               and md.index("Evidence Validator") > md.index("The Watchlist")))
r.append(check("editor's notes for the reviewer are surfaced", "Close call between A and B." in md))
escaped = dict(issue, notes_for_human_reviewer="line one.\\nline two.")
r.append(check("escaped newlines in the editor's notes render as real paragraphs",
               "line one.\nline two." in rwb.render_markdown(escaped, meta, None)))
md_fail = rwb.render_markdown(issue, meta, {"passed": False, "summary": "1 hard fail", "needs_review_count": 0,
                                            "hard_fails": [{"where": "big_one.stories[0]", "check": "claim_traceability",
                                                            "claim_text": "58% of carries", "detail": "not in the source"}]})
r.append(check("a hard fail is spelled out in the readable draft",
               "HARD FAIL" in md_fail and "claim_traceability" in md_fail and "not in the source" in md_fail))

print("\n-- end to end, mocked (no API, no network)")
# A key must be PRESENT for a dry run to start (the Editor Agent is the
# point of a dry run); it is never used, because the agent is mocked.
import os  # noqa: E402

os.environ["ANTHROPIC_API_KEY"] = "test-key-never-used"
os.environ.pop("NFL_PIPELINE_WEBHOOK_SECRET", None)

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    pool_path = tmp / "pool.json"
    pool_path.write_text(json.dumps(rows))

    drafted = {"n": 0}

    def fake_agent(stories, api_key, issue_week=None):
        drafted["n"] += 1
        drafted["issue_week"] = issue_week
        drafted["count"] = len(stories)
        out = json.loads(json.dumps(issue))
        out["_evidence_validation"] = {"passed": True, "summary": "all clear", "needs_review_count": 0, "hard_fails": []}
        return out

    real_agent = rwb.run_weekly_editor_agent
    rwb.run_weekly_editor_agent = fake_agent
    try:
        code = rwb.main(["--season", "2026", "--week", "7", "--pool-json", str(pool_path), "--outputs-dir", str(tmp)])
        files = sorted(p.name for p in tmp.glob("brief_*"))
        r.append(check("dry run exits 0", code == 0))
        r.append(check("dry run writes both a JSON record and a markdown draft",
                       any(f.endswith(".json") for f in files) and any(f.endswith(".md") for f in files)))
        r.append(check("the Editor Agent is told the real week, not the fixture label",
                       drafted["issue_week"] == "2026 NFL Week 7"))
        r.append(check("only the enriched stories are handed to the Editor", drafted["count"] == 3))
        record = json.loads(next(tmp.glob("brief_*.json")).read_text())
        r.append(check("the record says dry mode and persisted nothing",
                       record["meta"]["mode"] == "dry" and "persisted" not in record))

        # no enriched stories -> refuse, no draft call
        bare = tmp / "bare.json"
        bare.write_text(json.dumps([row("Z", week=7, interrogation=False, eps=False)]))
        before = drafted["n"]
        code = rwb.main(["--season", "2026", "--week", "7", "--pool-json", str(bare), "--outputs-dir", str(tmp)])
        r.append(check("no enriched stories -> exit 3 and the Editor is never called",
                       code == 3 and drafted["n"] == before))

        # fixture input -> refuse, no draft call
        fx = tmp / "fx.json"
        fx.write_text(json.dumps([dict(row("SYNTHETIC-FIXTURE-1", week=7), id="SYNTHETIC-FIXTURE-1",
                                       entity={"type": "player", "player_id": "SYNTHETIC-PLAYER-1", "player_name": "Dorsey Keane"})]))
        before = drafted["n"]
        code = rwb.main(["--season", "2026", "--week", "7", "--pool-json", str(fx), "--outputs-dir", str(tmp)])
        r.append(check("fixture-looking input -> exit 4 and the Editor is never called",
                       code == 4 and drafted["n"] == before))

        # cost ceiling -> refuse before spending
        before = drafted["n"]
        code = rwb.main(["--season", "2026", "--week", "7", "--pool-json", str(pool_path),
                         "--outputs-dir", str(tmp), "--max-cost", "0.000001"])
        r.append(check("over the cost ceiling -> exit 5 before the Editor is called",
                       code == 5 and drafted["n"] == before))

        # --pool-json with --persist is refused outright
        code = rwb.main(["--season", "2026", "--week", "7", "--pool-json", str(pool_path),
                         "--outputs-dir", str(tmp), "--persist"])
        r.append(check("--pool-json cannot be combined with --persist", code == 2))

        # Evidence Validator hard fail -> draft still written, exit 1, nothing persisted
        from weekly_brief_evidence import EvidenceValidationFailed

        report = {"passed": False, "summary": "1 hard fail", "needs_review_count": 0,
                  "hard_fails": [{"where": "big_one.stories[0]", "check": "claim_traceability",
                                  "claim_text": "58% of carries", "detail": "not in the source"}]}

        def blocked_agent(stories, api_key, issue_week=None):
            out = json.loads(json.dumps(issue))
            out["_evidence_validation"] = report
            raise EvidenceValidationFailed("blocked", out, report)

        rwb.run_weekly_editor_agent = blocked_agent
        blocked_dir = tmp / "blocked"
        code = rwb.main(["--season", "2026", "--week", "7", "--pool-json", str(pool_path), "--outputs-dir", str(blocked_dir)])
        md_files = list(blocked_dir.glob("brief_*.md"))
        blocked_md = md_files[0].read_text() if md_files else ""
        rec = json.loads(next(blocked_dir.glob("brief_*.json")).read_text())
        r.append(check("a hard Evidence Validator fail exits 1", code == 1))
        r.append(check("the blocked draft is still readable on disk",
                       "The big story" in blocked_md and "HARD FAIL" in blocked_md))
        r.append(check("the blocked record carries the failure report",
                       rec["blocked"] is True and rec["evidence_validation"]["hard_fails"]))
    finally:
        rwb.run_weekly_editor_agent = real_agent

print("\n-- persist shaping (pure; no network), and the upsert-id hazard")
from persist_weekly_brief import shape_newsletter_rows  # noqa: E402

stories_by_id = {s["intelligence_story_id"]: s for s in sel["stories"]}
shaped = shape_newsletter_rows(issue, stories_by_id, 2026, 7)
issue_row = shaped["issue"]
r.append(check("the issue row carries exactly {id, season, week}", set(issue_row) == {"id", "season", "week"}))
r.append(check("the issue row never carries publish_status, approved_at or published_at",
               not ({"publish_status", "approved_at", "published_at"} & set(issue_row))))
r.append(check("season and week are the ones asked for", issue_row["season"] == 2026 and issue_row["week"] == 7))
r.append(check("no story row carries a publish field either",
               all(not ({"publish_status", "approved_at", "published_at"} & set(row)) for row in shaped["stories"])))
r.append(check("every story row points at this issue's id",
               all(row["newsletter_issue_id"] == issue_row["id"] for row in shaped["stories"])))
r.append(check("real EPS values are frozen onto the rows from the candidate pool",
               any((row.get("eps_scores") or {}).get("eps_total") for row in shaped["stories"])))

# The second-run question, provable without a database. Minting a fresh
# id for a week that already has an issue is what makes the upsert try to
# change a primary key the story rows reference; reusing it is the fix.
again = shape_newsletter_rows(issue, stories_by_id, 2026, 7)
r.append(check("with no existing id, a second shaping call mints a DIFFERENT id (the hazard)",
               again["issue"]["id"] != issue_row["id"]))
reused = shape_newsletter_rows(issue, stories_by_id, 2026, 7, existing_issue_id=issue_row["id"])
r.append(check("a second persist for the same (season, week) REUSES the existing issue id",
               reused["issue"]["id"] == issue_row["id"]))
r.append(check("the reused id is what every story row points at",
               all(row["newsletter_issue_id"] == issue_row["id"] for row in reused["stories"])))
third = shape_newsletter_rows(issue, stories_by_id, 2026, 8)
r.append(check("a DIFFERENT week with no existing issue mints a new id",
               third["issue"]["id"] not in {issue_row["id"], again["issue"]["id"]} and third["issue"]["week"] == 8))
r.append(check("an explicitly empty existing id still mints (never writes a blank primary key)",
               bool(shape_newsletter_rows(issue, stories_by_id, 2026, 9, existing_issue_id="")["issue"]["id"])))
r.append(check("reusing an id never adds a publish field",
               set(reused["issue"]) == {"id", "season", "week"}))

print()
p = sum(r)
print(f"{p}/{len(r)} checks passed")
raise SystemExit(0 if p == len(r) else 1)
