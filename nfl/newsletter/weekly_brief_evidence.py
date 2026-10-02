"""
Weekly Editor Agent -- Evidence Validator wiring.

Runs the full evidence_validator.py suite over one finalized issue, as
the LAST step before the runner returns anything:

    writer -> finalize_weekly_brief (shape + one-treatment)
           -> run_evidence_validation (this module)
           -> result

Until this existed, evidence_validator.py was called only by run_wrapper_
acceptance_test.py, as a diagnostic grade after the fact. Nothing in the
real runner path ever read its result, so a draft could ship with an
ungrounded "survived scrutiny" claim, a fabricated number, a scoring-
language leak, or a gate violation and no code would object.

ALL SIX CHECKS, per the validator's own three-responsibility split:
  per story entry (validate_newsletter_story): claim traceability,
    relationship traceability, evidence-confidence alignment,
    interrogation traceability
  per issue (validate_editorial_contract): scoring-language leak,
    gate consistency (and one-treatment, already enforced upstream by
    finalize_weekly_brief -- re-checked here as a receipt, never
    expected to fail)

FAILURE BEHAVIOR: block and surface. A hard "fail" anywhere (or a cited
story id that isn't in the candidate pool) means the issue is NOT
publishable: the runner raises EvidenceValidationFailed with the issue
and the full report attached, so a human can read every failure. No
retry, no rewrite -- a validator failure is a judgment the model got
wrong, not a transient shape problem, and re-rolling until the checker
stops noticing would defeat the checker. "needs_review" entries do NOT
block (a name the heuristic detector couldn't ground, a relationship
sentence with no numbers either side, missing evidence_classification);
they are surfaced in the report for the human read, same as the
validator itself intends.

TWO CONTRACTS MIRRORED FROM run_wrapper_acceptance_test.py, not invented:
  1. entity reshaping -- the Validator's name pool reads
     entity.player_name / entity.team, but the candidate pool carries
     entity as a plain string. Reshaped to {"player_name": <str>} for
     validation only (the harness's _validator_stories_by_id does the
     same).
  2. gates -- check_gate_consistency reads eps_scores.big_one_eligible /
     watchlist_eligible, which the editor-output eps_scores shape never
     carries (7 dimension keys only). The harness gets them by running
     persist's shape_newsletter_rows and rebuilding the issue from flat
     rows -- which LOSES cross_references, so the leak scan never saw
     cross-reference text (a fidelity loss the harness's own docstring
     flags). Here the gates are attached to a deep copy for validation
     via the SAME persist_weekly_brief.freeze_eps_scores -- read from
     upstream eps.gates, never recomputed from dimension scores, exactly
     the "at draft time" source the check wants -- and the issue keeps
     its cross_references, so their text IS scanned. The returned issue's
     own eps_scores are left untouched (still the 7-key editor shape).

No prompt text is involved.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evidence_validator import validate_editorial_contract, validate_newsletter_story  # noqa: E402

# persist_weekly_brief.freeze_eps_scores is imported lazily inside
# enrich_gates_for_validation(), not here: persist imports the runner at
# module level, the runner imports this module, so a module-level import
# of persist from here is a cycle (confirmed: ImportError "partially
# initialized module" on first run). Gates are only read at validation
# time, by which point every module is fully loaded in either import order.


class EvidenceValidationFailed(ValueError):
    """Raised by the runner when the Evidence Validator hard-fails. Carries
    the finalized issue (with _validation and _evidence_validation already
    attached) and the full report, so nothing a human needs is lost."""

    def __init__(self, message: str, issue: dict, report: dict):
        super().__init__(message)
        self.issue = issue
        self.report = report


def validator_stories_by_id(candidates: list[dict]) -> dict[str, dict]:
    """Candidate pool -> the stories_by_id shape validate_newsletter_story
    expects, with entity reshaped for the name pool. Everything else
    (interrogation, eps, sample_size, headline, story) passes through
    unchanged -- those are exactly the fields the number pool reads."""
    out: dict[str, dict] = {}
    for c in candidates:
        reshaped = dict(c)
        entity = c.get("entity")
        if isinstance(entity, str):
            reshaped["entity"] = {"player_name": entity}
        out[c["intelligence_story_id"]] = reshaped
    return out


def enrich_gates_for_validation(issue: dict, candidates: list[dict]) -> dict:
    """Deep copy of `issue` whose single-source story entries carry
    big_one_eligible/watchlist_eligible read from the cited candidate's
    upstream eps.gates (via freeze_eps_scores -- persist's own function,
    the same values persist will freeze). Zero- or multi-source entries
    are left alone: there is no single authoritative gate to attach, and
    check_gate_consistency reports that honestly as needs_review."""
    from persist_weekly_brief import freeze_eps_scores  # lazy -- see module-level note on the import cycle

    by_id = {c["intelligence_story_id"]: c for c in candidates}
    enriched = copy.deepcopy(issue)
    for section in enriched.get("sections", []):
        for story in section.get("stories", []) or []:
            ids = story.get("intelligence_story_ids") or []
            resolved = [by_id[sid] for sid in ids if sid in by_id]
            if len(resolved) == 1:
                frozen = freeze_eps_scores(resolved[0])
                eps = dict(story.get("eps_scores") or {})
                eps["big_one_eligible"] = frozen["big_one_eligible"]
                eps["watchlist_eligible"] = frozen["watchlist_eligible"]
                story["eps_scores"] = eps
    return enriched


def run_evidence_validation(issue: dict, candidates: list[dict]) -> dict:
    """
    The full suite over one finalized issue. Pure; never mutates `issue`.

    Returns {
      "passed": bool,                 # no hard fail anywhere, no unresolved story id
      "hard_fails": [...],            # every blocking finding, flattened, with where it came from
      "needs_review_count": int,      # non-blocking findings surfaced for the human read
      "per_story": [...],             # validate_newsletter_story result per stories[] entry
      "editorial_contract": {...},    # validate_editorial_contract result for the whole issue
      "gates_source": str,
      "summary": str,
    }
    """
    stories_by_id = validator_stories_by_id(candidates)

    per_story: list[dict] = []
    hard_fails: list[dict] = []
    needs_review = 0
    for section in issue.get("sections", []):
        st = section.get("section_type")
        for i, story in enumerate(section.get("stories", []) or []):
            ids = story.get("intelligence_story_ids") or []
            result = validate_newsletter_story(story.get("headline", ""), story.get("body", ""), ids, stories_by_id)
            where = f"{st}.stories[{i}]"
            per_story.append({"where": where, "intelligence_story_ids": ids, "result": result})
            for missing in result.get("missing_story_ids", []):
                hard_fails.append({"where": where, "check": "missing_story_id", "claim_text": missing,
                                   "detail": "cited intelligence_story_id is not in this week's candidate pool"})
            for check_name in ("claim_traceability", "relationship_traceability",
                               "evidence_confidence_alignment", "interrogation_traceability"):
                for r in result.get(check_name, []):
                    if r.get("status") == "fail":
                        hard_fails.append({"where": where, "check": r.get("check", check_name),
                                           "claim_text": r.get("claim_text"), "detail": r.get("detail")})
                    elif r.get("status") == "needs_review":
                        needs_review += 1

    contract = validate_editorial_contract(enrich_gates_for_validation(issue, candidates))
    for check_name in ("scoring_language_leak", "gate_consistency", "one_treatment"):
        for r in contract.get(check_name, []):
            if r.get("status") == "fail":
                hard_fails.append({"where": "issue", "check": r.get("check", check_name),
                                   "claim_text": r.get("claim_text"), "detail": r.get("detail")})
            elif r.get("status") == "needs_review":
                needs_review += 1

    passed = all(p["result"]["passed"] for p in per_story) and bool(contract["passed"]) and not hard_fails
    return {
        "passed": passed,
        "hard_fails": hard_fails,
        "needs_review_count": needs_review,
        "per_story": per_story,
        "editorial_contract": contract,
        "gates_source": "upstream eps.gates via persist_weekly_brief.freeze_eps_scores (read at draft time, never recomputed)",
        "summary": (
            f"{'PASS' if passed else 'BLOCKED'}: {len(hard_fails)} hard fail(s), "
            f"{needs_review} flagged for human review, across {len(per_story)} story entries"
        ),
    }
