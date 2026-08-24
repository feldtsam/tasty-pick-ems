"""
NFL Intelligence — the real write connection to Lovable, per the
approved design (see the conversation this was approved in). Ties
together three already-separate, already-tested concerns without
merging them: intelligence_schema.build_story() (shape), intelligence_
sanity.sanity_check_story() (numeric/structural well-formedness),
intelligence_lifecycle.apply_lifecycle() (state tracking) — this module
is purely the orchestration + real-row-shaping + real-POST layer on top
of all three, same role lovable_forward.py's callers already play for
Picks (curate_home_shelves.py never reimplements HMAC signing; it just
shapes rows and calls forward_to_lovable).

Real, confirmed infrastructure this module writes to:
  POST https://tastypickems.com/api/public/nfl-intelligence-write
  Body: {"stories": [...], "history": [...]} (either key optional).
  Same HMAC/X-Signature mechanism every other NFL write route already
  uses (NFL_PIPELINE_WEBHOOK_SECRET) — see lovable_forward.py.

  nfl_intelligence_stories: one row per real detected story per week —
  a row exists ONLY when a family's build_*_stories() actually produced
  a story that week (see intelligence_lifecycle.py's own module
  docstring on why a combined content+lifecycle table was rejected: a
  miss/Archived week has no content to attach, and a "content" table
  whose rows sometimes represent absence would blur real vs. placeholder
  row semantics this codebase's conventions otherwise avoid).

  nfl_intelligence_story_history: already what apply_lifecycle()
  produces — this module just adds the actual write call, no new
  shaping logic beyond what apply_lifecycle already returns.

NEVER SILENTLY DROP (approved decision #2): every story this module is
given produces exactly one nfl_intelligence_stories row, regardless of
whether it passes intelligence_sanity's checks. A sanity-failed story
gets sanity_check_passed=False, sanity_check_issues populated, and
is_visible=False — written and auditable, just not shown publicly (no
review-status string, no human workflow — see module docstring on
is_visible below).

MARKET INTELLIGENCE (approved decision #4): still gets real content
rows here (lifecycle_eligible=False path) — lifecycle_state is always
None, and no history_rows are produced for it at all, per the already-
approved lifecycle deferral (see intelligence_lifecycle.py's own module
docstring: nothing calls apply_lifecycle() for Market Intelligence;
there's no "lifecycle_state: null" branch to build inside apply_
lifecycle itself, because its stories never enter that module's world).
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from intelligence_lifecycle import apply_lifecycle, entity_key_for
from intelligence_sanity import sanity_check_story

DEFAULT_NFL_INTELLIGENCE_WRITE_URL = "https://tastypickems.com/api/public/nfl-intelligence-write"


def _json_safe_float(value):
    """
    A finite float passes through unchanged; NaN/inf (and anything not
    float-convertible at all) becomes None. Real JSON has no NaN/
    Infinity literal — Python's json.dumps will happily emit the
    non-standard tokens NaN/Infinity anyway (allow_nan defaults True),
    which a real strict receiving parser could reject outright. This is
    the wire-boundary sanitizer: intelligence_lifecycle.py deliberately
    keeps the REAL bad float value (including actual NaN) in its own
    internal history dict and even in history_rows, since that's the
    honest in-process audit record — this function is what turns that
    into something actually safe to serialize, applied only here, at
    the point rows are about to be sent, so nothing upstream has to
    know or care about JSON's own real limitations.
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _json_safe_int(value):
    """Same job as _json_safe_float, but preserves int typing for a genuinely-valid reading (sample_size is a count, not a 0-100 scale)."""
    safe = _json_safe_float(value)
    return int(safe) if safe is not None else None


def _entity_key_for_row(entity) -> tuple:
    """
    entity_key_for(), but never raises — a story whose entity is so
    malformed that identity can't be computed still gets a real content
    row (never-silently-drop, decision #2), just with a real, honest
    fallback key and an EXTRA sanity issue flagging exactly why. This is
    a genuinely rare edge case (build_story() already guarantees the
    "entity" key itself is present; what can still go wrong is entity's
    OWN inner shape, e.g. a player entity missing player_id) — flagged
    here explicitly rather than assumed, since Sam's approved design
    didn't specify this particular corner.

    Returns (entity_key: str, extra_issue: str|None).
    """
    try:
        return entity_key_for(entity), None
    except (KeyError, TypeError, ValueError) as e:
        fallback = f"UNKNOWN:{entity!r}"[:200]
        return fallback, f"entity_key could not be computed ({type(e).__name__}: {e}) — used fallback key {fallback!r}"


# The v2 Universal Card write path -- every row shaped from this point
# forward carries this, regardless of family or whether that family's
# own writer populates any of the new v2 fields yet (only Defensive
# Trends does, as of this task). This is a marker of which SHAPE OF
# WRITE PATH produced the row, not "this row's v2 fields are fully
# populated" -- a Role/Coaching/Market row written today is still a
# real v2 row (hero_metric etc. simply null), not a v1 row. Existing,
# already-written rows from before this change are untouched and stay
# whatever a reader infers for their own missing card_schema_version.
CARD_SCHEMA_VERSION = 2


def _json_safe_hero_metric(hero_metric):
    """
    Same wire-boundary NaN/inf -> null sanitizing _json_safe_float
    already does for trend_strength/completeness/confidence, applied to
    hero_metric's own numeric sub-fields -- defense-in-depth (real
    td_agrees-gated source data should already be finite) against
    exactly the class of bug this session already found and fixed once
    for intelligence_lifecycle.py's own numeric fields.
    """
    if hero_metric is None:
        return None
    safe = dict(hero_metric)
    for field in ("before_value", "after_value", "delta_value"):
        if field in safe:
            safe[field] = _json_safe_float(safe[field])
    return safe


def shape_story_row(story: dict, season: int, week: int, sanity_issues: list, lifecycle_state) -> dict:
    """
    Maps ONE real story dict (the 13-field schema — intelligence_
    schema.STORY_FIELDS — plus Defensive Trends' new, OPTIONAL v2
    fields: hero_metric/signal_direction/what_changed/evidence_
    classification, all absent/None for the other three families until
    their own writers are updated) plus this run's own findings (sanity
    issues, this identity's real lifecycle_state, if any) into the real
    nfl_intelligence_stories column shape. Pure — no I/O, no signing;
    see write_intelligence_rows for that.
    """
    entity_key, entity_key_issue = _entity_key_for_row(story.get("entity"))
    issues = list(sanity_issues) + ([entity_key_issue] if entity_key_issue else [])
    passed = len(issues) == 0

    return {
        "intelligence_family": story.get("intelligence_family"),
        "entity_key": entity_key,
        "primary_signal_name": (story.get("primary_signal") or {}).get("name"),
        "season": season,
        "week": week,
        "entity": story.get("entity"),
        "headline": story.get("headline"),
        "story": story.get("story"),
        "primary_signal": story.get("primary_signal"),
        "supporting_evidence": story.get("supporting_evidence"),
        "trend_direction": story.get("trend_direction"),
        "trend_strength": _json_safe_float(story.get("trend_strength")),
        "sample_size": _json_safe_int(story.get("sample_size")),
        "completeness": _json_safe_float(story.get("completeness")),
        "confidence": _json_safe_float(story.get("confidence")),
        "time_window": story.get("time_window"),
        "related_players": story.get("related_players"),
        "lifecycle_state": lifecycle_state,
        "is_visible": passed,
        "sanity_check_passed": passed,
        "sanity_check_issues": issues or None,
        "card_schema_version": CARD_SCHEMA_VERSION,
        "hero_metric": _json_safe_hero_metric(story.get("hero_metric")),
        "signal_direction": story.get("signal_direction"),
        "what_changed": story.get("what_changed"),
        "evidence_classification": story.get("evidence_classification"),
    }


def _shape_history_row_for_wire(row: dict) -> dict:
    """JSON-boundary sanitizing pass over one apply_lifecycle() history row — see _json_safe_float."""
    return {
        **row,
        "trend_strength": _json_safe_float(row.get("trend_strength")),
        "primary_signal_value": _json_safe_float(row.get("primary_signal_value")),
    }


def process_family(
    family: str, stories: list, prior_history: dict, season: int, week: int, lifecycle_eligible: bool = True,
) -> dict:
    """
    The real per-family orchestration this task's "wire the actual
    write calls" step needs: sanity-check every real story this family
    produced this week, run lifecycle (unless this family is deferred —
    Market Intelligence), and shape everything into the real row shapes
    the write endpoint expects.

    stories: this week's REAL story dicts from family's own build_*_
    stories() function, unmodified.
    prior_history: apply_lifecycle's own history dict as of the end of
    the prior real run for this family (ignored entirely when
    lifecycle_eligible=False). Caller-owned/sourced — see this module's
    own docstring and this task's report for the real, currently-open
    question of how prior_history gets read back from Lovable for a
    live deployed run (nfl_intelligence_story_history has no confirmed
    read route yet, unlike nfl_shelf_signal_history's real read-back
    mechanism for stickiness).

    A story whose entity/primary_signal.name is malformed enough that
    entity_key_for() itself raises cannot be given a real lifecycle
    identity at all — no policy decision can manufacture an identity
    that doesn't exist. Such a story still gets a real content row
    (shape_story_row's own fallback key + extra issue), but is excluded
    from the batch passed into apply_lifecycle, since there is nothing
    for the lifecycle state machine to key it by. Every OTHER sanity
    failure (a non-finite trend_strength being the concrete case this
    task investigates) still enters apply_lifecycle normally as a real
    appearance, per the approved decision #3 — apply_lifecycle's own
    NaN-safety fix (see its module docstring) is what keeps that safe.

    Returns {"story_rows": [...], "history_rows": [...],
    "updated_history": {...}}. updated_history is prior_history
    unchanged when lifecycle_eligible=False (nothing to update).
    """
    identifiable_stories = []
    unidentifiable = {}  # id(story) -> issue, for stories that can't reach apply_lifecycle at all

    for story in stories:
        try:
            entity_key_for(story.get("entity"))
            if not (story.get("primary_signal") or {}).get("name"):
                raise ValueError("primary_signal.name missing")
        except (KeyError, TypeError, ValueError) as e:
            unidentifiable[id(story)] = f"could not be tracked by lifecycle ({type(e).__name__}: {e})"
            continue
        identifiable_stories.append(story)

    lifecycle_states = {}
    history_rows = []
    updated_history = prior_history

    if lifecycle_eligible and identifiable_stories:
        result = apply_lifecycle(identifiable_stories, prior_history, family, season, week)
        updated_history = result["updated_history"]
        history_rows = [_shape_history_row_for_wire(r) for r in result["history_rows"]]
        for story in identifiable_stories:
            identity = (family, entity_key_for(story["entity"]), story["primary_signal"]["name"])
            lifecycle_states[id(story)] = updated_history[identity]["lifecycle_state"]

    story_rows = []
    for story in stories:
        issues = sanity_check_story(story)
        if id(story) in unidentifiable:
            issues = issues + [unidentifiable[id(story)]]
        lifecycle_state = lifecycle_states.get(id(story)) if lifecycle_eligible else None
        story_rows.append(shape_story_row(story, season, week, issues, lifecycle_state))

    return {"story_rows": story_rows, "history_rows": history_rows, "updated_history": updated_history}


def write_intelligence_rows(story_rows: list, history_rows: list, secret: str, write_url: str = None) -> dict:
    """
    Same real signed-POST mechanism every other NFL webhook write
    already uses (see lovable_forward.forward_to_lovable) — one
    combined call per curation run, matching the real route's own
    {"stories": [...], "history": [...]} body shape. Either list may be
    empty (e.g. Market Intelligence's history_rows is always [];
    a preview/dry-run caller might pass story_rows=[] to exercise just
    the history write) — the real route documents both keys as
    optional, and an empty list is a legitimate value for a key, not
    the same as omitting it, so both keys are always included here.
    """
    from lovable_forward import forward_to_lovable, resolve_url_env

    url = write_url or resolve_url_env("LOVABLE_NFL_INTELLIGENCE_WRITE_URL", DEFAULT_NFL_INTELLIGENCE_WRITE_URL)
    return forward_to_lovable({"stories": story_rows, "history": history_rows}, secret, url)


DEFAULT_NFL_INTELLIGENCE_READ_URL = "https://tastypickems.com/api/public/nfl-intelligence-read"


def read_intelligence_stories(season: int, week: int, secret: str, read_url: str = None) -> dict:
    """
    One signed POST (body {"season": season, "week": week}), returns
    {"ok": bool, "error": str|None, "status_code": int|None, "rows":
    [...]} for the ENTIRE real (season, week) across every family — the
    real nfl_intelligence_stories read route. Same real sign+POST+
    capture-response reuse of forward_to_lovable every other read route
    in this codebase already uses (see curate_home_shelves.read_shelf_
    signal_history's own docstring for why that's a deliberate reuse,
    not a misuse).

    Response body's real story-list key ("stories") and each row's real
    DB-generated fields (id, created_at, updated_at — id, not story_id;
    confirmed directly against a real round-tripped row, not guessed)
    were confirmed empirically against the live route, not assumed.
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from lovable_forward import forward_to_lovable, resolve_url_env

    url = read_url or resolve_url_env("LOVABLE_NFL_INTELLIGENCE_READ_URL", DEFAULT_NFL_INTELLIGENCE_READ_URL)
    result = forward_to_lovable({"season": season, "week": week}, secret, url)
    if not result["success"]:
        return {"ok": False, "error": result["error"], "status_code": result["status_code"], "rows": []}
    try:
        body = json.loads(result["response_body"])
    except (json.JSONDecodeError, TypeError):
        return {
            "ok": False, "error": f"non-JSON response body: {result['response_body']!r}",
            "status_code": result["status_code"], "rows": [],
        }
    return {"ok": True, "error": None, "status_code": result["status_code"], "rows": body.get("stories") or []}
