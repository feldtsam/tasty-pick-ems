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

from card_writer_common import call_claude_with_tool, system_blocks  # noqa: E402

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
                            "enum": [
                                "from_the_desk", "big_one", "what_changed", "market_knows_something",
                                "who_it_affects", "tasty_connection", "watchlist",
                            ],
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


def build_candidate_pool_input(fixtures: list) -> str:
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
    return json.dumps(
        {
            "issue_week": "Calibration Fixture V2 -- synthetic data, not a real week",
            "candidate_story_objects": candidates,
            "live_picks": [],
            "live_picks_note": "No real TPE Picks data exists for this synthetic fixture -- Tasty Connection should reflect that honestly (empty or near-empty), not invent picks to fill the section.",
        },
        indent=2,
    )


def run_weekly_editor_agent(fixtures: list, api_key: str) -> dict:
    # PROMPT CACHING: this prompt is a checked-in markdown file with no
    # per-call interpolation, so the whole system prompt is the cached prefix
    # -- one block, one cache_control breakpoint, tool schema cached with it.
    # Re-read per call as before; the file's bytes are what the cache keys on,
    # and they only change when someone edits the prompt (which SHOULD miss).
    system_prompt = system_blocks(PROMPT_PATH.read_text())
    user_prompt = build_candidate_pool_input(fixtures)
    return call_claude_with_tool(api_key, system_prompt, user_prompt, WEEKLY_BRIEF_TOOL_SCHEMA, max_tokens=MAX_TOKENS)


if __name__ == "__main__":
    import os

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY not set")

    fixtures = json.loads(FIXTURE_PATH.read_text())
    result = run_weekly_editor_agent(fixtures, api_key)
    print(json.dumps(result, indent=2))
