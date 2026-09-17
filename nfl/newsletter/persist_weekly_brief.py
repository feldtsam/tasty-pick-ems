"""
The production invocation boundary for the Weekly Editor Agent:
qualified Story Objects -> Editor Agent -> persist with frozen gates ->
Evidence Validator. Deliberately narrow — see the module docstring
sections below for what each function does and does not do, and
nfl/newsletter/README.md for what this closes (EPS spec §12 acceptance
test #7, "receipts-freeze reconciliation").

Nothing here is a scheduler, a publishing pipeline, an admin surface,
or new Editor Agent behavior. `run_weekly_editor_agent()` (this same
directory) is called completely unchanged.

Two-layer split, matching this codebase's own established convention
(intelligence_write.py's shape_story_row/write_intelligence_rows;
nfl-intelligence-write.ts's payload-shaping vs. the actual
supabaseAdmin.upsert call): everything through shape_newsletter_rows()
is a pure function — no I/O, fully testable without a live webhook
secret or a real HTTP call. Only persist_weekly_brief()/run_and_
persist() touch the network.
"""
import uuid

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))

from lovable_forward import forward_to_lovable  # noqa: E402

from run_weekly_editor_agent import run_weekly_editor_agent  # noqa: E402

NEWSLETTER_WRITE_URL_ENV = "NFL_NEWSLETTER_WRITE_URL"
DEFAULT_WRITE_URL = "https://tastypickems.com/api/public/newsletter-write"

_ZERO_EPS_SCORES = {
    "significance": 0,
    "evidence_strength": 0,
    "betting_relevance": 0,
    "novelty": 0,
    "story_tension": 0,
    "audience_relevance": 0,
    "eps_total": 0,
    "big_one_eligible": False,
    "watchlist_eligible": False,
}


def freeze_eps_scores(story: dict) -> dict:
    """
    Flattens ONE Story Object's live `eps` (dimensions + composite_score
    + gates, per eps.compute_eps()'s real output shape) into the flat
    snapshot shape newsletter_story.eps_scores stores — the receipts
    frozen at the moment this issue is drafted, never live-recomputed
    or re-fetched later.

    Reads `big_one_eligible`/`watchlist_eligible` from THIS story's own
    `eps.gates` — the authoritative upstream source — not from any
    downstream copy (e.g. the Editor Agent's own output `eps_scores`
    field, which only carries the six dimensions + eps_total, no gates
    at all: confirmed directly against run_weekly_editor_agent.py's own
    WEEKLY_BRIEF_TOOL_SCHEMA before writing this). Never recomputes
    eligibility from the dimension scores either — that would have this
    function doing exactly what the gate-consistency check exists to
    verify NOBODY does.

    Existing EPS-spec fallback, not new logic: if `eps` is None on the
    story this function was handed, the Editor Agent has already
    excluded that story as a candidate per that same fallback (a null
    eps means eps.compute_eps() itself declined to score it) — so this
    function is never expected to receive such a story from a real
    Editor run. It's handled here defensively anyway, returning the
    same zeroed snapshot multi-/zero-source entries get, so a caller
    that hands this an unexpected null `eps` gets an honest, inert
    snapshot instead of a KeyError two layers down.
    """
    eps = story.get("eps")
    if not eps:
        return dict(_ZERO_EPS_SCORES)

    dims = eps.get("dimensions", {})
    gates = eps.get("gates", {})
    return {
        "significance": dims.get("significance", {}).get("score", 0),
        "evidence_strength": dims.get("evidence_strength", {}).get("score", 0),
        "betting_relevance": dims.get("betting_relevance", {}).get("score", 0),
        "novelty": dims.get("novelty", {}).get("score", 0),
        "story_tension": dims.get("story_tension", {}).get("score", 0),
        "audience_relevance": dims.get("audience_relevance", {}).get("score", 0),
        "eps_total": eps.get("composite_score", 0),
        "big_one_eligible": bool(gates.get("big_one_eligible", False)),
        "watchlist_eligible": bool(gates.get("watchlist_eligible", False)),
    }


def shape_newsletter_rows(editor_output: dict, stories_by_id: dict, season: int, week: int) -> dict:
    """
    PURE function — no I/O, no side effects. Takes one Weekly Editor
    Agent run's raw output (run_weekly_editor_agent()'s own return
    shape: {issue_week, sections: [{section_type, stories: [...],
    cross_references: [...]}], watchlist_populated, notes_for_human_
    reviewer}) plus the same `stories_by_id` lookup the Editor's own
    candidate pool was built from, and produces the exact rows
    newsletter-write.ts expects: one newsletter_issue row (client-
    generated uuid4 id, since newsletter_issue.id is gen_random_uuid()-
    defaulted but Postgres accepts an explicit client id on insert —
    this is what lets one combined payload carry both the issue and its
    stories, each already referencing the real issue id, in a single
    round trip) and a flat list of newsletter_story rows, one per
    section's per-story entry.

    Gate-freezing (the whole point of this module): a story entry
    resolves to a single source Story Object when it cites exactly one
    id present in `stories_by_id` — in that case freeze_eps_scores()
    reads that story's own live eps.gates. Entries citing zero ids
    (from_the_desk, which legitimately carries no intelligence_story_
    ids per the prompt's own convention) or more than one id (a cross-
    story comparison, e.g. a "who_it_affects" entry naming two players)
    get the zeroed eps_scores snapshot instead of a frozen gate --
    there is no single authoritative gate to freeze when a story entry
    isn't actually about one specific Story Object's eligibility.

    `cross_references[]` entries are read from editor_output only to
    build the `skipped` diagnostic list below (specifically: to confirm
    a cross-reference's target id was a real, resolvable story) --
    they are never turned into rows. newsletter_story has no column for
    them (20260914200000 defines section_type/stories fields only), and
    they're structurally incapable of being a treatment in the first
    place (no headline/body/eps_scores) -- so there's nothing to
    persist. This IS a real fidelity loss for downstream validation:
    Editorial Contract Validation's scoring-language-leak scan reads
    reader-facing prose off persisted rows, so cross-reference text
    never gets scanned once this module is the only path stories take
    to the database. Flagged here plainly rather than worked around,
    since adding a cross_references column is a schema change outside
    this task's explicit scope.

    Returns {"issue": {...}, "stories": [...], "skipped": [...]} --
    `skipped` tracks any cited intelligence_story_id that resolved to
    nothing in stories_by_id (from either a stories[] entry or a
    cross_references[] entry), so a caller can see a broken reference
    without this function crashing on it.
    """
    issue_id = str(uuid.uuid4())
    issue_row = {"id": issue_id, "season": season, "week": week}

    story_rows: list[dict] = []
    skipped: list[dict] = []

    for section in editor_output.get("sections", []):
        section_type = section.get("section_type")
        for sort_order, story in enumerate(section.get("stories", [])):
            cited_ids = story.get("intelligence_story_ids", []) or []
            resolved = [stories_by_id[sid] for sid in cited_ids if sid in stories_by_id]
            missing = [sid for sid in cited_ids if sid not in stories_by_id]
            for sid in missing:
                skipped.append({
                    "reason": "intelligence_story_id not found in stories_by_id",
                    "intelligence_story_id": sid,
                    "section_type": section_type,
                })

            if len(resolved) == 1:
                eps_scores = freeze_eps_scores(resolved[0])
            else:
                # Zero source stories (from_the_desk) or multiple
                # (cross-story comparisons) -- no single authoritative
                # gate to freeze. Not an error case; see docstring.
                eps_scores = dict(_ZERO_EPS_SCORES)

            story_rows.append({
                "newsletter_issue_id": issue_id,
                "section_type": section_type,
                "sort_order": sort_order,
                "intelligence_story_ids": cited_ids,
                "player_ids": story.get("player_ids", []) or [],
                "pick_ids": story.get("pick_ids", []) or [],
                "eps_scores": eps_scores,
                "headline": story.get("headline", ""),
                "body": story.get("body", ""),
            })

        for xref in section.get("cross_references", []):
            target = xref.get("refers_to_intelligence_story_id")
            if target and target not in stories_by_id:
                skipped.append({
                    "reason": "cross_reference target not found in stories_by_id",
                    "intelligence_story_id": target,
                    "section_type": section_type,
                })

    return {"issue": issue_row, "stories": story_rows, "skipped": skipped}


def persist_weekly_brief(
    editor_output: dict,
    stories_by_id: dict,
    season: int,
    week: int,
    secret: str,
    write_url: str | None = None,
) -> dict:
    """
    Shapes the rows (shape_newsletter_rows(), pure), then makes the one
    real network call: a single signed POST carrying {"issue": {...},
    "stories": [...]} to newsletter-write.ts, matching intelligence_
    write.py's own combined-payload convention for stories+history.

    Returns {"shaped": <shape_newsletter_rows() return>, "write_result":
    <forward_to_lovable() return>} -- both the shaping result (useful
    even if the write fails, for diagnosis) and the raw HTTP result.
    """
    url = write_url or DEFAULT_WRITE_URL
    shaped = shape_newsletter_rows(editor_output, stories_by_id, season, week)
    write_result = forward_to_lovable(
        {"issue": shaped["issue"], "stories": shaped["stories"]}, secret, url
    )
    return {"shaped": shaped, "write_result": write_result}


def run_and_persist(
    fixtures: list,
    season: int,
    week: int,
    api_key: str,
    webhook_secret: str,
    write_url: str | None = None,
) -> dict:
    """
    The full boundary, start to finish: qualified Story Objects (real
    or Fixture V2) -> run_weekly_editor_agent() (unchanged) -> persist_
    weekly_brief() (shape + freeze + write).

    `fixtures` is the SAME list run_weekly_editor_agent() itself takes
    -- this function does not filter or pre-qualify it. "Qualified"
    Story Objects are the caller's own responsibility to have selected
    before calling this, exactly as run_weekly_editor_agent.py already
    assumes for its own `fixtures` argument.

    Returns {"editor_output": <raw Editor output>, "shaped": ...,
    "write_result": ...} -- the full trail, not just the final result,
    so a caller can inspect what the Editor actually produced alongside
    what got persisted from it.
    """
    stories_by_id = {f["intelligence_story_id"]: f for f in fixtures}
    editor_output = run_weekly_editor_agent(fixtures, api_key)
    persisted = persist_weekly_brief(editor_output, stories_by_id, season, week, webhook_secret, write_url)
    return {"editor_output": editor_output, **persisted}
