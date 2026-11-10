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
from weekly_brief_shape import SECTION_PRIORITY, cap_errors, finalize_weekly_brief  # noqa: E402

PROMPT_PATH = Path(__file__).resolve().parent / "weekly_editor_agent_prompt_v2.md"
FIXTURE_PATH = Path(__file__).resolve().parent / "fixture_v2.json"

# Generous, learned directly from story_interrogation.py/eps.py's own
# real truncation bug (card_writer_common's own MAX_TOKENS=1024 default
# is tuned for a single short section, not a full multi-section
# newsletter issue with narrated prose for up to six stories).
MAX_TOKENS = 8192

# The Editor Agent drafts a whole newsletter in ONE forced-tool call, so
# card_writer_common's shared 60s read timeout -- right for the per-card
# writers -- does not fit it. Measured 2026-11-09 against a real 12-story
# pool: 60.46 seconds for an HTTP 200. The shared ceiling was losing that
# call by less than half a second, as a ReadTimeout, after the tokens had
# already been generated and billed. 300s is the same budget the Make.com
# scenarios already give this project's own endpoints.
EDITOR_AGENT_TIMEOUT_SECONDS = 300


class WeeklyBriefContractFailed(ValueError):
    """
    Raised when no tool_use block passes the structural contract, after
    the one capped retry. Carries the best-effort finalized issue and the
    per-attempt reports so a caller can SAVE and show what the model
    actually produced.

    Subclasses ValueError deliberately: this replaces a bare
    `raise ValueError(...)`, so any existing `except ValueError` keeps
    working unchanged. It exists because that bare raise discarded
    everything -- run_weekly_brief.main caught only
    EvidenceValidationFailed, so a structural failure killed the run
    with no draft written, after the call had been billed.
    """

    def __init__(self, message: str, issue: dict | None, attempts: list[dict]):
        super().__init__(message)
        self.issue = issue
        self.attempts = attempts


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


def build_candidate_pool(fixtures: list, issue_week: str | None = None) -> dict:
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
    not a gap in the test. The same is true of a real week today: no
    caller wires live Picks in yet, so the note names the real reason
    rather than claiming synthetic data.

    issue_week names the week being drafted. It defaults to the Fixture
    V2 label so every existing caller behaves exactly as before; the
    production caller (run_weekly_brief.py) passes the real week, which
    matters because this string is the first thing the model reads and
    telling it a real week is synthetic fixture data is not a harmless
    inaccuracy.

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
            # Added 2026-11-10. These four were being dropped, and their
            # absence caused real, traced damage:
            #
            #   team -- the Week 9 draft put Makai Lemon (PHI) on "the
            #     Lions" and four Titans "in Chicago". The model had no
            #     team field at all; `entity` arrives flattened to a
            #     display string, so team was only ever inferable from
            #     prose, and it inferred wrong.
            #   related_players / team -- the Evidence Validator's name
            #     pool reads entity.player_name, entity.team and
            #     related_players[].display_label. With two of those
            #     three missing, the pool was effectively just the
            #     subject's own name, so real names flagged as possibly
            #     fabricated: "Rashid Shaheed" and "Detroit" were both
            #     needs_review in the 2026-11-10 run.
            #   what_changed -- the per-change evidence the prompt's own
            #     Hard Rule 2 tells the model to copy facts from.
            #   evidence_classification -- the validator's
            #     check_evidence_confidence_alignment needs it and
            #     reported it missing on every story. It is a plain
            #     column on nfl_intelligence_stories (values seen:
            #     'strong', 'limited'), returned by the read route and
            #     already read by prep_interrogation_eps; the validator's
            #     own guess that a get_published_nfl_intelligence_latest
            #     join was missing was wrong -- it was simply not being
            #     passed through here.
            #
            # .get() throughout: Calibration Fixture V2 predates all four
            # and must keep working unchanged.
            "team": f.get("team"),
            "related_players": f.get("related_players"),
            "what_changed": f.get("what_changed"),
            "evidence_classification": f.get("evidence_classification"),
            "interrogation": f["interrogation"],
            "eps": f["eps"],
        })
    synthetic = issue_week is None
    return {
        "issue_week": issue_week or "Calibration Fixture V2 -- synthetic data, not a real week",
        "candidate_story_objects": candidates,
        "live_picks": [],
        "live_picks_note": (
            "No real TPE Picks data exists for this synthetic fixture -- Tasty Connection should reflect that honestly (empty or near-empty), not invent picks to fill the section."
            if synthetic else
            "No live TPE Picks were supplied to this run -- Tasty Connection should reflect that honestly (empty or near-empty), not invent picks to fill the section."
        ),
    }


RETRY_INSTRUCTION = (
    "Your previous attempt was rejected for the reasons above. Return the complete brief again "
    "with every error fixed. Do not shorten or drop sections to fix it."
)

# Added 2026-11-10 after a real retry returned the whole brief as JSON
# text inside the `sections` string rather than as structured fields.
RETRY_ENVELOPE_INSTRUCTION = (
    "Return the brief through the tool as structured fields, not as JSON text inside a string."
)


def build_retry_feedback(reports: list[dict]) -> str | None:
    """
    The follow-up text appended to the SECOND attempt's user turn: the
    first attempt's rejection list VERBATIM, then RETRY_INSTRUCTION.

    Errors are passed through exactly as finalize_weekly_brief produced
    them -- not summarised, not reworded, not truncated, and not reduced
    to the first one. The 2026-11-09 run rejected attempt 1 with one
    hard_error and four shape_errors; all five belong in front of the
    model, because fixing only the first would still fail.

    Every block from the attempt is included (the API can emit more than
    one tool_use block for a forced tool), de-duplicated in order so a
    repeated error across blocks is stated once. Returns None when there
    is nothing to report, so a caller can tell "no feedback" from
    "empty feedback".
    """
    hard: list[str] = []
    shape: list[str] = []
    for report in reports:
        for err in report.get("hard_errors") or []:
            if err not in hard:
                hard.append(err)
        for err in report.get("shape_errors") or []:
            if err not in shape:
                shape.append(err)
    if not hard and not shape:
        return None

    lines = ["The brief you just returned was REJECTED by the structural contract. Errors:", ""]
    # Capped for TRANSMISSION only -- the stored report keeps every error.
    # A real run produced 15,210 near-identical errors; sending all of
    # them would have buried the instruction under its own evidence.
    def _emit(kind, errs):
        shown = cap_errors(errs)
        for err in shown:
            # The cap sentinel is a count, not an error -- rendering it
            # as "- hard_error: ...and 75 more" would read as a 26th
            # error with a nonsense message.
            if err.startswith("...and ") and err.endswith(" more"):
                lines.append(f"  ({err})")
            else:
                lines.append(f"- {kind}: {err}")

    _emit("hard_error", hard)
    _emit("shape_error", shape)
    lines += ["", RETRY_INSTRUCTION, RETRY_ENVELOPE_INSTRUCTION]
    return "\n".join(lines)


def build_candidate_pool_input(fixtures: list, issue_week: str | None = None) -> str:
    """Serialized form of build_candidate_pool() -- the user prompt."""
    return json.dumps(build_candidate_pool(fixtures, issue_week), indent=2)


def run_weekly_editor_agent(fixtures: list, api_key: str, issue_week: str | None = None,
                            on_raw_blocks=None) -> dict:
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
    pool = build_candidate_pool(fixtures, issue_week)
    user_prompt = json.dumps(pool, indent=2)
    candidates = pool["candidate_story_objects"]
    live_picks = pool["live_picks"]

    attempt_reports: list[dict] = []
    last_finalized: dict | None = None
    retry_feedback: str | None = None
    for attempt in range(2):  # the first call + exactly one capped retry
        # ATTEMPT 1 IS BYTE-IDENTICAL to what it has always been.
        # ATTEMPT 2 carries the first attempt's verbatim rejection list
        # appended to the SAME user turn (same system prompt, same pool,
        # then the feedback). Before this, both attempts received
        # identical input and attempt 2 learned nothing -- confirmed
        # 2026-11-09, when the two attempts failed in two DIFFERENT ways
        # (attempt 1 omitted all four provenance fields on the Big One;
        # attempt 2 cited an empty intelligence_story_ids on What
        # Changed). That is two independent draws from one prompt, not a
        # correction, which is exactly what a retry with no feedback
        # produces. The system prompt is untouched so it stays the cached
        # prefix; only the user turn grows.
        this_user_prompt = user_prompt if retry_feedback is None else f"{user_prompt}\n\n{retry_feedback}"
        blocks = call_claude_with_tool(
            api_key, system_prompt, this_user_prompt, WEEKLY_BRIEF_TOOL_SCHEMA,
            max_tokens=MAX_TOKENS, return_all_tool_use_blocks=True,
            timeout=EDITOR_AGENT_TIMEOUT_SECONDS,
        )
        # RAW SAVE, before a single line of post-processing. The call
        # above is the only billed step in this function; everything
        # after it is deterministic local work that can and did raise
        # (2026-11-09: a string in `sections` -> AttributeError deep in
        # complete_derivable_fields). write_outputs lives at the END of
        # run_weekly_brief.main, so such a raise discarded a draft that
        # had already been paid for. This hook makes that impossible:
        # the bytes are on disk before anything can reject them.
        if on_raw_blocks is not None:
            try:
                on_raw_blocks(blocks, attempt + 1)
            except Exception as e:  # never let saving break the run
                print(f"[weekly_editor] WARNING: raw-output save failed: {e!r}", flush=True)
        for block_index, block in enumerate(blocks):
            issue, report = finalize_weekly_brief(block, candidates, live_picks)
            last_finalized = issue
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
            f"hard_errors={cap_errors(attempt_reports[-1]['hard_errors'])} "
            f"shape_errors={cap_errors(attempt_reports[-1]['shape_errors'])}",
            flush=True,
        )
        # Build the feedback the NEXT attempt will carry, from every
        # block this attempt produced. Only set when a retry is still to
        # come, so a passing run never builds one (it returns above).
        retry_feedback = build_retry_feedback(
            [r for r in attempt_reports if r["attempt"] == attempt + 1]
        )

    # The MESSAGE is capped; the exception still carries every report
    # untouched on .attempts, and run_weekly_brief writes those in full to
    # the record .json. Before this, the message alone was 1.6 MB.
    capped_for_message = [
        {**rep,
         "hard_errors": cap_errors(rep.get("hard_errors") or []),
         "shape_errors": cap_errors(rep.get("shape_errors") or [])}
        for rep in attempt_reports
    ]
    raise WeeklyBriefContractFailed(
        "Weekly Editor Agent output failed the structural contract after one capped retry; refusing to return "
        "a brief that would persist with missing provenance. Attempts: " + json.dumps(capped_for_message, default=str),
        last_finalized, attempt_reports,
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
