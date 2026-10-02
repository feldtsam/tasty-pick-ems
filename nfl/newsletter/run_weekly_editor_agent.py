"""
Minimal Weekly Editor Agent caller — built specifically to run
Calibration Fixture V2, not production calling code. No Flask endpoint,
no Make.com wiring, no retry/language-scan infrastructure the way
story_interrogation.py/eps.py have — this exists to answer one
question (does the calibrated prompt, with the EPS-consumption patch,
produce correct placement + honest prose against Fixture V2?), not to
be the real Thursday caller. Building that is separate, larger work —
see nfl/newsletter/README.md.

Uses call_claude_with_tool() (card_writer_common.py), the same
convention every other real LLM call in this codebase uses.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "content_writer"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from card_writer_common import call_claude_with_tool, system_blocks  # noqa: E402
from weekly_brief_evidence import EvidenceValidationFailed, run_evidence_validation  # noqa: E402
from weekly_brief_shape import SECTION_PRIORITY, finalize_weekly_brief  # noqa: E402

PROMPT_PATH = Path(__file__).resolve().parent / "weekly_editor_agent_prompt_v2.md"
FIXTURE_PATH = Path(__file__).resolve().parent / "fixture_v2.json"

# Generous, learned directly from story_interrogation.py/eps.py's own
# real truncation bug (card_writer_common's own MAX_TOKENS=1024 default
# is tuned for a single short section, not a full multi-section
# newsletter issue with narrated prose for up to six stories).
MAX_TOKENS = 8192

WEEKLY_BRIEF_TOOL_SCHEMA = {
    "name": "record_weekly_brief_issue",
    "description": "Records one drafted Weekly Brief newsletter issue, exactly per the Weekly Editor Agent prompt's own Output Format section.",
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "issue_week": {"type": "string"},
            "sections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "section_type": {
                            "type": "string",
                            # Single source of truth shared with weekly_brief_shape:
                            # this order IS the one-treatment priority order.
                            "enum": list(SECTION_PRIORITY),
                        },
                        "stories": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "headline": {"type": "string"},
                                    "body": {"type": "string"},
                                    "intelligence_story_ids": {"type": "array", "items": {"type": "string"}},
                                    "player_ids": {"type": "array", "items": {"type": "string"}},
                                    "pick_ids": {"type": "array", "items": {"type": "string"}},
                                    "eps_scores": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "properties": {
                                            "significance": {"type": "number"},
                                            "evidence_strength": {"type": "number"},
                                            "betting_relevance": {"type": "number"},
                                            "novelty": {"type": "number"},
                                            "story_tension": {"type": "number"},
                                            "audience_relevance": {"type": "number"},
                                            "eps_total": {"type": "number"},
                                        },
                                        "required": [
                                            "significance", "evidence_strength", "betting_relevance",
                                            "novelty", "story_tension", "audience_relevance", "eps_total",
                                        ],
                                    },
                                },
                                "required": ["headline", "body", "intelligence_story_ids", "player_ids", "pick_ids", "eps_scores"],
                            },
                        },
                        # Run 3 fix, per the prompt's own updated Output
                        # Format section: a pointer, not a treatment --
                        # deliberately has NO headline/body/eps_scores,
                        # so it is structurally incapable of being a
                        # second full entry for a Story Object already
                        # covered elsewhere. This is the actual fix for
                        # what run 2 found (a schema gap, not a phrasing
                        # gap) -- every section previously only had one
                        # writable slot shaped like a full treatment.
                        "cross_references": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "text": {"type": "string"},
                                    "refers_to_intelligence_story_id": {"type": "string"},
                                },
                                "required": ["text", "refers_to_intelligence_story_id"],
                            },
                        },
                    },
                    "required": ["section_type", "stories", "cross_references"],
                },
            },
            "watchlist_populated": {"type": "boolean"},
            "notes_for_human_reviewer": {"type": "string"},
        },
        "required": ["issue_week", "sections", "watchlist_populated", "notes_for_human_reviewer"],
    },
}


def build_candidate_pool(fixtures: list) -> dict:
    """
    The week's candidate Story Objects, each carrying its real
    interrogation/eps content — matching real weekly conditions per the
    fixture spec's own grading instructions ("the Editor sees several
    Story Objects at once, not one at a time"). Field shapes are passed
    through unchanged from fixture_v2.json: confirmed directly against
    story_interrogation.interrogate_story()'s and eps.compute_eps()'s
    own real return shapes before writing this — interrogation_version/
    change/context/challenge/confirmation/judgment and eps_version/
    dimensions/composite_score/gates all match field-for-field, so no
    remapping was needed (the fixture's own note flagged this as a
    real possibility, not assumed clean).

    live_picks is explicitly empty and named as such — Fixture V2 has
    no real TPE Picks data behind it, so an empty/near-empty Tasty
    Connection section is the correct, honest degraded-input outcome,
    not a gap in the test.

    Returned as a dict (not pre-serialized) so the SAME pool the model
    is handed is also what weekly_brief_shape.finalize_weekly_brief()
    derives eps_scores/player_ids from afterwards -- one source for both
    the prompt and the completion step, so they can't disagree.
    """
    candidates = []
    for f in fixtures:
        candidates.append({
            "intelligence_story_id": f["intelligence_story_id"],
            "player_id": f["player_id"],
            "intelligence_family": f["intelligence_family"],
            "entity": f["entity"],
            "headline": f["headline"],
            "story": f["story"],
            "time_window": f["time_window"],
            "sample_size": f["sample_size"],
            "interrogation": f["interrogation"],
            "eps": f["eps"],
        })
    return {
        "issue_week": "Calibration Fixture V2 -- synthetic data, not a real week",
        "candidate_story_objects": candidates,
        "live_picks": [],
        "live_picks_note": "No real TPE Picks data exists for this synthetic fixture -- Tasty Connection should reflect that honestly (empty or near-empty), not invent picks to fill the section.",
    }


def build_candidate_pool_input(fixtures: list) -> str:
    """Serialized form of build_candidate_pool() -- the user prompt."""
    return json.dumps(build_candidate_pool(fixtures), indent=2)


def run_weekly_editor_agent(fixtures: list, api_key: str) -> dict:
    """
    One real call, then STRUCTURAL CONTRACT ENFORCEMENT before anything
    sees the result -- see weekly_brief_shape.py's module docstring for
    the two real run-7 bugs this closes (required story fields missing;
    a second full treatment of one Story Object). Forced tool use never
    guaranteed schema conformance, so this never trusts the raw tool
    input as-is:

      1. Ask for every tool_use block (return_all_tool_use_blocks=True),
         the same free recovery the NFL shelf-card writer uses -- the API
         sometimes emits more than one block for a forced tool, and a
         later one is sometimes the complete one.
      2. finalize_weekly_brief() each block: derive the derivable fields
         from the SAME candidate pool the model was given, enforce
         one-treatment deterministically, shape-validate. First block
         that passes wins.
      3. If no block passes, exactly ONE retry call (capped, never a
         loop). If that fails too, raise -- a brief that didn't pass the
         contract is never returned for persist to silently zero-source.

    The returned issue carries an `_validation` block (underscore =
    inspection-only, same convention as the shelf-card writer's
    `_retry_stats`) recording every deterministic repair that was
    applied and whether the retry fired. No prompt text is involved.
    """
    # PROMPT CACHING: this prompt is a checked-in markdown file with no
    # per-call interpolation, so the whole system prompt is the cached prefix
    # -- one block, one cache_control breakpoint, tool schema cached with it.
    # Re-read per call as before; the file's bytes are what the cache keys on,
    # and they only change when someone edits the prompt (which SHOULD miss).
    system_prompt = system_blocks(PROMPT_PATH.read_text())
    pool = build_candidate_pool(fixtures)
    user_prompt = json.dumps(pool, indent=2)
    candidates = pool["candidate_story_objects"]
    live_picks = pool["live_picks"]

    attempt_reports: list[dict] = []
    for attempt in range(2):  # the first call + exactly one capped retry
        blocks = call_claude_with_tool(
            api_key, system_prompt, user_prompt, WEEKLY_BRIEF_TOOL_SCHEMA,
            max_tokens=MAX_TOKENS, return_all_tool_use_blocks=True,
        )
        for block_index, block in enumerate(blocks):
            issue, report = finalize_weekly_brief(block, candidates, live_picks)
            attempt_reports.append({"attempt": attempt + 1, "block_index": block_index, **report})
            if report["passed"]:
                if block_index > 0:
                    print(f"[weekly_editor] recovered a passing block at index={block_index} of {len(blocks)} in the same response", flush=True)
                issue["_validation"] = {
                    "passed": True,
                    "retry_fired": attempt == 1,
                    "attempts": attempt_reports,
                    "repairs": report["repairs"],
                }
                # EVIDENCE VALIDATOR -- last step, after the structural
                # contract, before anything is returned. Block and
                # surface: a hard fail is a judgment the model got wrong,
                # not a shape problem, so there is deliberately no retry
                # and no rewrite here -- the raise carries the issue and
                # the full report for a human to read. The receipts are
                # attached on pass too, under the same underscore
                # (inspection-only, stripped before any write) convention
                # as _validation. See weekly_brief_evidence.py.
                evidence = run_evidence_validation(issue, candidates)
                issue["_evidence_validation"] = evidence
                if not evidence["passed"]:
                    print(f"[weekly_editor] BLOCKED by Evidence Validator: {evidence['summary']}", flush=True)
                    for f in evidence["hard_fails"]:
                        print(f"[weekly_editor]   {f['where']} :: {f['check']} :: {f['claim_text']!r} -- {f['detail']}", flush=True)
                    raise EvidenceValidationFailed(
                        f"Evidence Validator blocked this issue ({evidence['summary']}); the issue and the full "
                        f"report are attached on this exception (.issue / .report) and under the issue's own "
                        f"_evidence_validation key.",
                        issue, evidence,
                    )
                return issue
        print(
            f"[weekly_editor] attempt {attempt + 1}: no block passed the structural contract -- "
            f"hard_errors={attempt_reports[-1]['hard_errors']} shape_errors={attempt_reports[-1]['shape_errors']}",
            flush=True,
        )

    raise ValueError(
        "Weekly Editor Agent output failed the structural contract after one capped retry; refusing to return "
        "a brief that would persist with missing provenance. Attempts: " + json.dumps(attempt_reports, default=str)
    )


if __name__ == "__main__":
    import os

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY not set")

    fixtures = json.loads(FIXTURE_PATH.read_text())
    try:
        result = run_weekly_editor_agent(fixtures, api_key)
    except EvidenceValidationFailed as blocked:
        # Surface everything a human needs: the finalized issue (with its
        # _validation and _evidence_validation receipts) plus the report,
        # then exit non-zero so nothing downstream mistakes this for a
        # publishable issue.
        print(json.dumps({"blocked": True, "reason": str(blocked), "issue": blocked.issue, "evidence_validation": blocked.report}, indent=2))
        raise SystemExit(1)
    print(json.dumps(result, indent=2))
