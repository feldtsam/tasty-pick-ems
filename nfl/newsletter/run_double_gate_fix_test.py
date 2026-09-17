"""
Test plan for the double-gate duplicate-treatment fix (weekly_editor_
agent_prompt_v2.md Step 2, 2026-09): reruns the full six-fixture pool
against the fixed prompt at least three times (real ANTHROPIC_API_KEY
calls, real model sampling each time -- this bug is probabilistic, not
deterministic, per the real prior evidence: the exact same fixture
passed clean in calibration run 3 and then failed on both real wrapper
acceptance runs), grading Fixture 1 (SYNTHETIC-FIXTURE-1, the real
double-gate case -- big_one_eligible AND watchlist_eligible both true)
specifically each time: exactly one stories[] entry, never a second one
in Watchlist.

Reports the full pass/fail distribution, not a single verdict -- a
clean 3/3 is real evidence the prompt fix holds; anything less means
check_one_treatment (evidence_validator.py) is the real backstop this
needs to rely on in production, not the prompt alone.

Run: python3 run_double_gate_fix_test.py
"""
import json
import os
from pathlib import Path

from evidence_validator import check_one_treatment
from run_weekly_editor_agent import run_weekly_editor_agent

HERE = Path(__file__).resolve().parent
FIXTURE_PATH = HERE / "fixture_v2.json"
TARGET_ID = "SYNTHETIC-FIXTURE-1"
N_RUNS = 3
START_RUN_NUMBER = 4  # runs 1-3 are the pre-fix calibration history; don't overwrite them


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


def _normalize_sections(editor_output: dict) -> dict:
    """
    REAL ANOMALY FOUND while running this test, not hypothetical:
    run 4 (the first post-fix run) came back from the real model call
    with editor_output["sections"] as a JSON-ENCODED STRING rather than
    a parsed nested array/list -- confirmed directly (type(sections) ==
    str, and json.loads() on it produces exactly the expected list-of-
    section-dicts shape). Runs 1/2/3 (the pre-fix calibration history)
    never showed this -- checked all three, every one has sections as
    a real list. This is a new, separate finding: an occasional tool-
    call formatting slip on a complex nested array parameter, unrelated
    to the Step 2 prompt content itself (the STRING, once parsed,
    contains coherent, well-formed story data -- the model's actual
    editorial output was fine; only the outer JSON structuring of the
    tool call's `sections` argument was malformed this one time).
    Normalizing defensively here (parse if str) so this run's real
    content can still be graded, rather than silently discarding a
    real data point or crashing -- but this is flagged plainly in the
    final report as its own thing, not folded into the double-gate
    result silently.
    """
    sections = editor_output.get("sections")
    if isinstance(sections, str):
        try:
            parsed = json.loads(sections)
        except json.JSONDecodeError:
            # Also observed directly: the string sometimes carries one
            # stray trailing character (a comma) past the array's own
            # closing bracket -- looks like an off-by-one slice
            # somewhere upstream of this function, not this test's own
            # doing. Stripped defensively so a real run's content isn't
            # thrown away over one extra character; flagged in the
            # report regardless, not silently absorbed.
            parsed = json.loads(sections.rstrip().rstrip(","))
        editor_output = dict(editor_output)
        editor_output["sections"] = parsed
    return editor_output


def _grade_fixture_1(editor_output: dict) -> dict:
    """
    Where did SYNTHETIC-FIXTURE-1 actually land this run? Returns
    {"sections_with_full_entry": [...], "cross_reference_sections": [...],
    "clean": bool} -- clean means exactly one stories[] entry and zero
    or more cross_references pointers, never a second stories[] entry.
    """
    sections_with_full_entry = []
    cross_reference_sections = []
    for section in editor_output.get("sections", []):
        section_type = section.get("section_type")
        for story in section.get("stories", []):
            if TARGET_ID in (story.get("intelligence_story_ids") or []):
                sections_with_full_entry.append(section_type)
        for xref in section.get("cross_references", []):
            if xref.get("refers_to_intelligence_story_id") == TARGET_ID:
                cross_reference_sections.append(section_type)
    return {
        "sections_with_full_entry": sections_with_full_entry,
        "cross_reference_sections": cross_reference_sections,
        "clean": len(sections_with_full_entry) == 1,
    }


def main():
    env = _load_env(HERE.parent / ".env.local")
    api_key = os.environ.get("ANTHROPIC_API_KEY") or env.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY not set (checked env and nfl/.env.local)")

    fixtures = json.loads(FIXTURE_PATH.read_text())

    results = []
    for i in range(N_RUNS):
        run_number = START_RUN_NUMBER + i
        print(f"--- run {run_number} (post-fix run {i + 1}/{N_RUNS}) ---", flush=True)
        editor_output = run_weekly_editor_agent(fixtures, api_key)
        sections_was_string = isinstance(editor_output.get("sections"), str)
        editor_output = _normalize_sections(editor_output)
        raw_path = HERE / f"fixture_v2_run{run_number}_raw.json"
        raw_path.write_text(json.dumps(editor_output, indent=2))
        if sections_was_string:
            print(f"  ANOMALY: this run's raw tool-call output had 'sections' as a JSON-encoded string, not a "
                  f"parsed array -- normalized before grading and before saving to disk. See this script's own "
                  f"_normalize_sections() docstring.")

        grade = _grade_fixture_1(editor_output)
        one_treatment_results = check_one_treatment(editor_output)
        f1_one_treatment = next(
            (r for r in one_treatment_results if r["claim_text"] == TARGET_ID), None
        )

        results.append({
            "run_number": run_number,
            "grade": grade,
            "validator_check_one_treatment": f1_one_treatment,
            "sections_field_anomaly": sections_was_string,
        })
        print(f"  sections_with_full_entry: {grade['sections_with_full_entry']}")
        print(f"  cross_reference_sections: {grade['cross_reference_sections']}")
        print(f"  manual grade CLEAN: {grade['clean']}")
        print(f"  check_one_treatment result: {f1_one_treatment}")
        print(flush=True)

    clean_count = sum(1 for r in results if r["grade"]["clean"])
    validator_pass_count = sum(
        1 for r in results
        if r["validator_check_one_treatment"] is not None and r["validator_check_one_treatment"]["status"] == "pass"
    )

    print("=" * 70)
    print(f"DISTRIBUTION: {clean_count}/{N_RUNS} runs placed Fixture 1 cleanly (exactly one stories[] entry)")
    print(f"VALIDATOR:    {validator_pass_count}/{N_RUNS} runs passed check_one_treatment for Fixture 1")
    for r in results:
        print(f"  run {r['run_number']}: manual_clean={r['grade']['clean']} sections={r['grade']['sections_with_full_entry']} xrefs={r['grade']['cross_reference_sections']}")

    summary = {
        "n_runs": N_RUNS,
        "clean_count": clean_count,
        "pass_rate": f"{clean_count}/{N_RUNS}",
        "validator_pass_count": validator_pass_count,
        "results": results,
    }
    (HERE / "double_gate_fix_test_results.json").write_text(json.dumps(summary, indent=2))
    print(f"\nFull results written to double_gate_fix_test_results.json")


if __name__ == "__main__":
    main()
