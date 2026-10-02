"""
Weekly Editor Agent -- structural contract enforcement for one drafted
issue, applied to the model's raw tool_use input BEFORE anything reads it.

WHY THIS EXISTS (two real bugs from Calibration Fixture V2 run 7, see
fixture_v2_run7_raw.json):

1. The `big_one` and `from_the_desk` stories[] entries came back carrying
   only headline/body -- no intelligence_story_ids, player_ids, pick_ids,
   or eps_scores -- even though WEEKLY_BRIEF_TOOL_SCHEMA marks all four
   `required`. Forced tool use does not enforce that: card_writer_common.
   call_claude_with_tool sends `tool_choice: {"type": "tool", ...}` under
   anthropic-version 2023-06-01 with no strict/structured-outputs mode,
   which guarantees the tool is INVOKED, not that its `input` conforms to
   the schema. Schema conformance is best-effort. Every other writer in
   this repo already treats it that way (nfl_shelf_card_writer_schema.
   validate_schema_shape: "independent of trusting forced tool-use
   alone"); the newsletter runner simply had no equivalent.

   The downstream cost was silent, not loud: persist_weekly_brief.shape_
   newsletter_rows reads `story.get("intelligence_story_ids", [])`, so a
   Big One entry missing that field persists as a zero-source row with
   zeroed eps_scores -- the issue's lead story loses its provenance with
   no error anywhere.

2. Fixture 7 received two full stories[] treatments (big_one AND who_it_
   affects). evidence_validator.check_one_treatment already catches this,
   but the runner never called it (only run_double_gate_fix_test.py did,
   as a diagnostic grade, not enforcement). And because that check keys
   on `.get("intelligence_story_ids", [])`, bug 1 let an entry escape
   bug 2's check entirely -- the two compound.

WHAT IS GENUINELY MODEL-SOURCED. Of the four required fields, only
`intelligence_story_ids` carries information the pipeline doesn't already
hold. `eps_scores` is "a copy, not a computation" per the prompt's own
Output Format section -- the pipeline is the faithful copier, and persist
already ignores the model's copy in favor of upstream (freeze_eps_scores).
`player_ids` is derivable from the candidate pool by story id. `pick_ids`
comes from live_picks. So completion here is DERIVATION from the same
candidate pool the model was handed, never invention: the one field that
can't be derived (`intelligence_story_ids`) is a hard failure when missing
from any section other than from_the_desk, where the prompt's own contract
says it is legitimately empty.

ONE-TREATMENT ENFORCEMENT is deterministic and LLM-free. Section priority
is the schema's own section order (SECTION_PRIORITY below, the single
source of truth the runner's enum reads from). The first stories[] entry
in priority order keeps a story id; any later entry citing it has that id
removed and a cross_references pointer added in its place, using the
entry's OWN generated headline as the pointer text -- real model output
repurposed, never fabricated prose. An entry left with no ids of its own
(and not from_the_desk) was never a treatment of anything else, so it is
demoted entirely: removed from stories[], its pointers kept. Nothing is
silently discarded -- every repair, including a demoted entry's full
original content, is recorded in the returned report.

No prompt text is changed by any of this.
"""
from __future__ import annotations

import copy

# Schema section order == one-treatment priority. The runner's tool schema
# enum reads this list, so the two can't drift.
SECTION_PRIORITY = (
    "from_the_desk", "big_one", "what_changed", "market_knows_something",
    "who_it_affects", "tasty_connection", "watchlist",
)

EPS_SCORE_KEYS = (
    "significance", "evidence_strength", "betting_relevance",
    "novelty", "story_tension", "audience_relevance", "eps_total",
)

REQUIRED_STORY_FIELDS = ("headline", "body", "intelligence_story_ids", "player_ids", "pick_ids", "eps_scores")

# The one section whose stories[] entries legitimately carry no story id
# (prompt Output Format: "leave eps_scores fields at 0 and intelligence_
# story_ids empty for that entry unless the opening is explicitly tied to
# a specific story").
NO_SOURCE_SECTIONS = ("from_the_desk",)


def zero_eps_scores() -> dict:
    return {k: 0 for k in EPS_SCORE_KEYS}


def eps_scores_from_candidate(candidate: dict) -> dict:
    """Copy-not-compute: the six upstream dimension scores plus composite
    as eps_total, read off the candidate's own eps block -- the same
    fields and the same source freeze_eps_scores() reads downstream,
    minus the gates (the editor-output shape has never carried gates)."""
    eps = candidate.get("eps") or {}
    dims = eps.get("dimensions") or {}
    out = {k: (dims.get(k) or {}).get("score", 0) for k in EPS_SCORE_KEYS if k != "eps_total"}
    out["eps_total"] = eps.get("composite_score", 0)
    return out


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_weekly_brief_shape(issue: dict) -> list[str]:
    """
    Defense-in-depth structural check for one issue, mirroring every
    other writer's validate_schema_shape(): every stories[] entry carries
    all six fields with the right types, eps_scores carries all seven
    numeric keys, every cross_references[] entry is a real pointer.
    Returns a list of human-readable problems; empty means well-formed.
    """
    errors: list[str] = []
    if not isinstance(issue.get("sections"), list):
        return ["sections must be a list"]
    for section in issue["sections"]:
        st = section.get("section_type")
        if st not in SECTION_PRIORITY:
            errors.append(f"unknown section_type {st!r}")
        for i, story in enumerate(section.get("stories", []) or []):
            where = f"{st}.stories[{i}]"
            for field in REQUIRED_STORY_FIELDS:
                if field not in story:
                    errors.append(f"{where} missing required field {field!r}")
            for field in ("headline", "body"):
                if field in story and (not isinstance(story[field], str) or not story[field].strip()):
                    errors.append(f"{where}.{field} must be a non-empty string")
            for field in ("intelligence_story_ids", "player_ids", "pick_ids"):
                if field in story and not isinstance(story[field], list):
                    errors.append(f"{where}.{field} must be a list")
            eps = story.get("eps_scores")
            if "eps_scores" in story:
                if not isinstance(eps, dict):
                    errors.append(f"{where}.eps_scores must be an object")
                else:
                    for k in EPS_SCORE_KEYS:
                        if k not in eps:
                            errors.append(f"{where}.eps_scores missing {k!r}")
                        elif not _is_number(eps[k]):
                            errors.append(f"{where}.eps_scores.{k} must be a number")
        for i, xref in enumerate(section.get("cross_references", []) or []):
            where = f"{st}.cross_references[{i}]"
            if not isinstance(xref.get("text"), str) or not xref["text"].strip():
                errors.append(f"{where}.text must be a non-empty string")
            if not isinstance(xref.get("refers_to_intelligence_story_id"), str) or not xref["refers_to_intelligence_story_id"]:
                errors.append(f"{where}.refers_to_intelligence_story_id must be a non-empty string")
    return errors


def complete_derivable_fields(issue: dict, candidates: list[dict], live_picks: list | None = None) -> tuple[dict, list[dict], list[str]]:
    """
    Fills the three DERIVABLE required fields from the candidate pool,
    keyed by each entry's intelligence_story_ids, and settles the
    non-derivable one honestly:

      eps_scores  -- always overwritten with the deterministic copy from
                     the cited candidate (exactly one id resolves) or
                     zeros (zero or several ids -- the same rule persist's
                     shape_newsletter_rows applies). The model's own copy
                     is informational; a mismatch is recorded, not trusted.
      player_ids  -- filled from the resolved candidates' player_id when
                     the field is missing; a model-supplied list is kept.
      pick_ids    -- filled with [] when missing (no pick is derivable
                     from a story id; live_picks is what would carry one).
      intelligence_story_ids -- NOT derivable. Missing on a NO_SOURCE_
                     SECTIONS entry (from_the_desk): filled with [] per
                     the prompt's own contract. Missing or empty on any
                     other section: a hard error -- a full treatment with
                     no story behind it is a provenance break, and
                     persist would silently zero-source it.

    Returns (completed_issue, repairs, hard_errors). Never mutates input.
    """
    issue = copy.deepcopy(issue)
    by_id = {c["intelligence_story_id"]: c for c in candidates}
    repairs: list[dict] = []
    hard_errors: list[str] = []

    for section in issue.get("sections", []):
        st = section.get("section_type")
        for i, story in enumerate(section.get("stories", []) or []):
            where = f"{st}.stories[{i}]"

            if "intelligence_story_ids" not in story or not isinstance(story.get("intelligence_story_ids"), list):
                if st in NO_SOURCE_SECTIONS:
                    story["intelligence_story_ids"] = []
                    repairs.append({"where": where, "field": "intelligence_story_ids", "action": "filled_empty_per_contract"})
                else:
                    hard_errors.append(
                        f"{where} has no intelligence_story_ids -- a full treatment outside {NO_SOURCE_SECTIONS} "
                        f"must cite the Story Object it treats (not derivable; would persist as a zero-source row)"
                    )
                    continue
            elif not story["intelligence_story_ids"] and st not in NO_SOURCE_SECTIONS:
                hard_errors.append(f"{where} cites an empty intelligence_story_ids list outside {NO_SOURCE_SECTIONS}")
                continue

            resolved = [by_id[sid] for sid in story["intelligence_story_ids"] if sid in by_id]
            unknown = [sid for sid in story["intelligence_story_ids"] if sid not in by_id]
            if unknown:
                hard_errors.append(f"{where} cites unknown intelligence_story_id(s) {unknown} -- not in this week's candidate pool")

            expected_eps = eps_scores_from_candidate(resolved[0]) if len(resolved) == 1 else zero_eps_scores()
            model_eps = story.get("eps_scores")
            if model_eps != expected_eps:
                repairs.append({
                    "where": where, "field": "eps_scores",
                    "action": "set_deterministic_copy" if model_eps is None else "replaced_model_copy",
                    "model_value": model_eps, "value": expected_eps,
                })
            story["eps_scores"] = expected_eps

            if "player_ids" not in story or not isinstance(story.get("player_ids"), list):
                derived = []
                for c in resolved:
                    pid = c.get("player_id")
                    if pid and pid not in derived:
                        derived.append(pid)
                story["player_ids"] = derived
                repairs.append({"where": where, "field": "player_ids", "action": "derived_from_candidates", "value": derived})

            if "pick_ids" not in story or not isinstance(story.get("pick_ids"), list):
                story["pick_ids"] = []
                repairs.append({"where": where, "field": "pick_ids", "action": "filled_empty"})

        section.setdefault("cross_references", [])
        section.setdefault("stories", [])

    return issue, repairs, hard_errors


def enforce_one_treatment(issue: dict) -> tuple[dict, list[dict]]:
    """
    Deterministic enforcement of the one-full-treatment rule (prompt Hard
    Rules + Output Format; evidence_validator.check_one_treatment is the
    detector this is the repair for). See module docstring for the
    mechanism. Idempotent: a compliant issue comes back unchanged with no
    repairs. Never mutates input.
    """
    issue = copy.deepcopy(issue)
    sections = issue.get("sections", [])
    order = {name: rank for rank, name in enumerate(SECTION_PRIORITY)}
    ranked = sorted(range(len(sections)), key=lambda idx: order.get(sections[idx].get("section_type"), len(order)))

    treated_in: dict[str, str] = {}
    repairs: list[dict] = []

    for idx in ranked:
        section = sections[idx]
        st = section.get("section_type")
        kept_stories = []
        for i, story in enumerate(section.get("stories", []) or []):
            ids = list(story.get("intelligence_story_ids", []) or [])
            duplicates = [sid for sid in ids if sid in treated_in]
            if not duplicates:
                for sid in ids:
                    treated_in[sid] = st
                kept_stories.append(story)
                continue

            remaining = [sid for sid in ids if sid not in treated_in]
            pointer_text = (story.get("headline") or "").strip() or (story.get("body") or "").strip().split(".")[0]
            for sid in duplicates:
                section.setdefault("cross_references", []).append({
                    "text": pointer_text,
                    "refers_to_intelligence_story_id": sid,
                })

            if remaining or st in NO_SOURCE_SECTIONS:
                story["intelligence_story_ids"] = remaining
                for sid in remaining:
                    treated_in[sid] = st
                kept_stories.append(story)
                repairs.append({
                    "where": f"{st}.stories[{i}]", "action": "stripped_duplicate_ids_to_cross_references",
                    "duplicates": [{"intelligence_story_id": sid, "treated_in": treated_in[sid]} for sid in duplicates],
                    "remaining_ids": remaining,
                })
            else:
                repairs.append({
                    "where": f"{st}.stories[{i}]", "action": "demoted_entry_to_cross_references",
                    "duplicates": [{"intelligence_story_id": sid, "treated_in": treated_in[sid]} for sid in duplicates],
                    "removed_entry": story,
                })
        section["stories"] = kept_stories

    return issue, repairs


def finalize_weekly_brief(issue: dict, candidates: list[dict], live_picks: list | None = None) -> tuple[dict, dict]:
    """
    The one call the runner makes: complete derivable fields -> enforce
    one-treatment -> shape-validate. Returns (issue, report) where
    report = {"passed": bool, "hard_errors": [...], "shape_errors": [...],
    "repairs": [...]}. `passed` is False when anything non-derivable is
    missing or the result still fails the shape check -- the caller must
    retry or fail loudly, never return a brief that didn't pass.
    """
    completed, repairs, hard_errors = complete_derivable_fields(issue, candidates, live_picks)
    enforced, treatment_repairs = enforce_one_treatment(completed)
    shape_errors = validate_weekly_brief_shape(enforced)
    report = {
        "passed": not hard_errors and not shape_errors,
        "hard_errors": hard_errors,
        "shape_errors": shape_errors,
        "repairs": repairs + treatment_repairs,
    }
    return enforced, report
