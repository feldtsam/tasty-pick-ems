"""
The wrapper's own acceptance-test runner — the specific test EPS spec
§12 #7 ("receipts-freeze reconciliation") was blocked on. Runs the real
boundary (minus the live network write, see the honest caveat below)
against Fixture V2, then hands the PERSISTED (shaped/frozen) rows, not
transient Editor output, to the Evidence Validator: validate_
newsletter_story per row, and validate_editorial_contract — the check
that matters most here, check_gate_consistency — over the whole issue.

NOT a live end-to-end test: the actual signed POST to newsletter-
write.ts is never executed. NFL_PIPELINE_WEBHOOK_SECRET is redacted in
this environment (returns the literal string "[SENSITIVE]" when read),
so there is no way to construct a real HMAC signature from here. This
script exercises persist_weekly_brief.shape_newsletter_rows() (the
pure shaping/freezing function — no I/O) directly, and treats its
output as the row content that WOULD be persisted. "Persisted" in this
script's own results means "correctly shaped and gate-frozen," not
"confirmed present in the live table" — that distinction is real and
is called out again in the results doc this script produces.

Second real caveat, load-bearing for anyone re-running this: Fixture
V2's `entity` field is a plain descriptive string ("RB Dorsey Keane"),
matching story_interrogation.py/eps.py's real output shape (what it
was built and confirmed against). The Evidence Validator's own
`stories_by_id` contract is a different, stricter one, per its module
docstring — the real production join of get_published_nfl_
intelligence_stories + get_published_nfl_intelligence_latest, where
`entity` is a dict ({player_name, team, ...}). Fixture V2 was never
confirmed against THAT contract. Running validate_newsletter_story
against Fixture V2's raw stories_by_id crashes inside _real_name_pool()
(entity.get(...) on a bare string) — confirmed directly, not assumed.
This script reshapes each fixture's `entity` string into {"player_
name": <string>} before handing it to the Validator, purely so the
acceptance test can run — moving existing information into the shape
a real caller would supply, not inventing anything new. Flagged here
plainly: this is a real, pre-existing gap between Fixture V2 and the
Validator's actual contract, not something this wrapper task fixes.
"""
import json
import os
from pathlib import Path

from evidence_validator import validate_editorial_contract, validate_newsletter_story
from persist_weekly_brief import shape_newsletter_rows
from run_weekly_editor_agent import run_weekly_editor_agent

HERE = Path(__file__).resolve().parent
FIXTURE_PATH = HERE / "fixture_v2.json"


def _load_env(path: Path) -> dict:
    env = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _validator_stories_by_id(fixtures: list) -> dict:
    """See module docstring's second caveat — reshapes entity: str into
    entity: {player_name: str} so the Validator's own contract is met."""
    out = {}
    for f in fixtures:
        reshaped = dict(f)
        reshaped["entity"] = {"player_name": f["entity"]}
        out[f["intelligence_story_id"]] = reshaped
    return out


def _issue_dict_from_persisted_rows(story_rows: list) -> dict:
    """Reconstructs the {"sections": [...]} shape validate_editorial_
    contract expects, from the FLAT persisted newsletter_story rows --
    the actual "hand the persisted rows to the Validator" step. Grouped
    back by section_type; cross_references are not reconstructable
    (newsletter_story has no column for them — see persist_weekly_
    brief.shape_newsletter_rows()'s own docstring), so this issue dict
    always has empty cross_references per section. That's a real,
    already-flagged fidelity loss for the scoring_language_leak scan,
    not a bug in this reconstruction step."""
    sections_by_type: dict[str, dict] = {}
    for row in story_rows:
        st = row["section_type"]
        sections_by_type.setdefault(st, {"section_type": st, "stories": [], "cross_references": []})
        sections_by_type[st]["stories"].append({
            "headline": row["headline"],
            "body": row["body"],
            "intelligence_story_ids": row["intelligence_story_ids"],
            "player_ids": row["player_ids"],
            "pick_ids": row["pick_ids"],
            "eps_scores": row["eps_scores"],
        })
    return {"sections": list(sections_by_type.values())}


def main():
    env = _load_env(HERE.parent / ".env.local")
    api_key = os.environ.get("ANTHROPIC_API_KEY") or env.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY not set (checked env and nfl/.env.local)")

    fixtures = json.loads(FIXTURE_PATH.read_text())
    fixture_stories_by_id = {f["intelligence_story_id"]: f for f in fixtures}

    editor_output = run_weekly_editor_agent(fixtures, api_key)
    (HERE / "wrapper_acceptance_run1_editor_output.json").write_text(json.dumps(editor_output, indent=2))

    shaped = shape_newsletter_rows(editor_output, fixture_stories_by_id, season=2025, week=99)
    (HERE / "wrapper_acceptance_run1_shaped.json").write_text(json.dumps(shaped, indent=2))

    validator_stories_by_id = _validator_stories_by_id(fixtures)

    report = {"issue": shaped["issue"], "skipped": shaped["skipped"], "per_story": [], "editorial_contract": None}

    for row in shaped["stories"]:
        result = validate_newsletter_story(
            row["headline"], row["body"], row["intelligence_story_ids"], validator_stories_by_id
        )
        report["per_story"].append({
            "section_type": row["section_type"],
            "intelligence_story_ids": row["intelligence_story_ids"],
            "result": result,
        })

    issue_for_validator = _issue_dict_from_persisted_rows(shaped["stories"])
    report["editorial_contract"] = validate_editorial_contract(issue_for_validator)

    (HERE / "wrapper_acceptance_run1_validator_results.json").write_text(json.dumps(report, indent=2))

    all_story_pass = all(s["result"]["passed"] for s in report["per_story"])
    contract_pass = report["editorial_contract"]["passed"]
    gate_results = report["editorial_contract"]["gate_consistency"]
    gate_pass = bool(gate_results) and all(r["status"] == "pass" for r in gate_results)

    print(f"per-story (4-check) all pass: {all_story_pass}")
    print(f"gate_consistency clean pass ({len(gate_results)} placement(s) checked): {gate_pass}")
    print(f"editorial_contract overall pass: {contract_pass}")
    print(f"skipped (broken references): {shaped['skipped']}")


if __name__ == "__main__":
    main()
