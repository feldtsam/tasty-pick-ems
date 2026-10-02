"""
Tests for story_interrogation.py's own deterministic pieces — the
input-contract assembly (build_interrogation_input) and the two post-
generation scans (confidence-escalation, reader-framing). All purely
synthetic, no network dependency: the actual LLM call (interrogate_
story -> call_claude_with_tool) is validated separately, against real
live data, not here — see the session's own real-data runs against
Week 1 2026 Market Intelligence and Role Changes rows (both real
LIMITED/thin-sample cases) for that half.

Run: python3 nfl/test_story_interrogation.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "content_writer"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "newsletter"))

import story_interrogation as si
from story_interrogation import (
    CACHED_SYSTEM_BLOCKS,
    CACHED_SYSTEM_BLOCKS_WITH_PRIOR_HISTORY,
    INTERROGATION_TOOL_SCHEMA,
    READER_FRAMING_LANGUAGE,
    RELATIONSHIP_NOT_ESTABLISHED_LANGUAGE,
    SYSTEM_PROMPT,
    SYSTEM_PROMPT_WITH_PRIOR_HISTORY,
    _dict_field,
    _entity_display_name,
    build_interrogation_input,
    interrogate_story,
    scan_evidence_significance_for_reader_framing,
    scan_for_confidence_escalation,
    scan_relationship_established_for_contradiction,
)


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


if __name__ == "__main__":
    results = []

    # ============================================================
    # build_interrogation_input — deterministic change/context mapping.
    # ============================================================
    story_with_hero = {
        "intelligence_family": "defensive_trends",
        "entity": {"type": "defense", "team": "SEA", "position_group": "RB"},
        "headline": "Seattle has grown more vulnerable to RB scores.",
        "time_window": "Season 2026, last 3 games through Week 4 vs. season-to-date",
        "sample_size": 4,
        "supporting_evidence": ["Allowed 2.1 red-zone TDs/game over the last 3 games (season average 1.2)"],
        "related_players": [],
        "hero_metric": {
            "label": "RB Red-Zone TDs Allowed", "before_value": 1.2, "after_value": 2.1,
            "unit": " TD/G", "period_before_label": "SEASON",
        },
    }
    inp = build_interrogation_input(story_with_hero)
    results.append(check(
        "change.what_changed reuses the story's own headline verbatim, no interpretation added",
        inp["change"]["what_changed"] == story_with_hero["headline"],
    ))
    results.append(check(
        "change.magnitude is derived from hero_metric's real before/after values",
        inp["change"]["magnitude"] == "1.2 TD/G -> 2.1 TD/G",
    ))
    results.append(check(
        "change.window reuses the story's own time_window directly, not a second independent value",
        inp["change"]["window"] == story_with_hero["time_window"],
    ))
    results.append(check(
        "context.sample_size reuses the story's own sample_size directly",
        inp["context"]["sample_size"] == 4,
    ))
    results.append(check(
        "context.prior_baseline is derived from hero_metric's before_value + its own period label",
        inp["context"]["prior_baseline"] == "SEASON: 1.2 TD/G",
    ))
    results.append(check(
        "context.historical_normal is always null in V1 -- no family computes a longer baseline than hero_metric today",
        inp["context"]["historical_normal"] is None,
    ))
    results.append(check(
        "entity resolves to a real display string for a defense entity",
        inp["entity"] == "SEA RB defense",
    ))

    story_no_hero = {
        "intelligence_family": "role_changes",
        "entity": {"type": "player", "player_id": "00-1", "player_name": "Test Player"},
        "headline": "An early opportunity has opened up.",
        "time_window": "Season 2099, through Week 1",
        "sample_size": 1,
        "supporting_evidence": None,
        "related_players": None,
        "hero_metric": None,
    }
    inp2 = build_interrogation_input(story_no_hero)
    results.append(check(
        "magnitude/prior_baseline are honestly null when hero_metric is absent, not fabricated from anything else",
        inp2["change"]["magnitude"] is None and inp2["context"]["prior_baseline"] is None,
    ))
    results.append(check(
        "a null supporting_evidence stays null, not coerced to an empty list (distinct absence, per the spec's own input-contract rule)",
        inp2["supporting_evidence"] is None,
    ))
    results.append(check(
        "a null related_players IS coerced to an empty list -- the schema's own related_players field is always a real array, never null, on the Story Object itself",
        inp2["related_players"] == [],
    ))
    results.append(check(
        "prior_history/market_data pass through as None when the caller doesn't supply them, present as real keys either way",
        set(["prior_history", "market_data"]).issubset(inp2.keys()) and inp2["prior_history"] is None and inp2["market_data"] is None,
    ))
    results.append(check(
        "entity resolves to the real player_name for a player entity",
        inp2["entity"] == "Test Player",
    ))
    results.append(check(
        "entity falls back to a player_id-based label when player_name is missing, never fabricates a name",
        _entity_display_name({"type": "player", "player_id": "00-9"}) == "player 00-9",
    ))
    results.append(check(
        "entity resolves to a real team label for a team entity",
        _entity_display_name({"type": "team", "team": "KC"}) == "KC",
    ))
    results.append(check(
        "a missing/None entity degrades honestly rather than raising",
        _entity_display_name(None) == "unknown entity",
    ))

    # ============================================================
    # scan_for_confidence_escalation — every string field, every nesting level.
    # ============================================================
    clean_response = {
        "challenge": {"alternate_explanations": [
            {"explanation": "x", "evidence": "y", "test": "z", "result": "the signal held up", "status": "WEAKENED"},
        ]},
        "confirmation": {"supporting_signals": "a", "contradicting_signals": "b", "market_reaction": "moved +700 to +525"},
        "judgment": {"what_we_know": "c", "what_we_dont_know": "d", "evidence_significance": "e"},
    }
    results.append(check(
        "a genuinely clean response (real Example A language) produces zero confidence-escalation violations",
        scan_for_confidence_escalation(clean_response) == [],
    ))

    dirty_response = {
        "challenge": {"alternate_explanations": [
            {"explanation": "This confirmed the signal is real", "evidence": "y", "test": "z", "result": "r", "status": "WEAKENED"},
        ]},
        "confirmation": {"supporting_signals": "a", "contradicting_signals": "b", "market_reaction": "m"},
        "judgment": {"what_we_know": "c", "what_we_dont_know": "d", "evidence_significance": "This is clearly settled."},
    }
    violations = scan_for_confidence_escalation(dirty_response)
    results.append(check(
        "confidence-escalation scan catches a violation nested inside alternate_explanations[], not just top-level fields",
        any(v["field"] == "challenge.alternate_explanations[0].explanation" and v["phrase"] == "confirmed" for v in violations),
    ))
    results.append(check(
        "confidence-escalation scan catches multiple distinct violating phrases across different fields in one pass",
        {"clearly", "settled"} <= {v["phrase"] for v in violations},
    ))
    results.append(check(
        "a WEAKENED status alone (no escalating language in the text) is never itself flagged -- status value and word-list matching are independent checks",
        scan_for_confidence_escalation(clean_response) == [] and clean_response["challenge"]["alternate_explanations"][0]["status"] == "WEAKENED",
    ))

    # ============================================================
    # scan_evidence_significance_for_reader_framing — scoped ONLY to
    # judgment.evidence_significance, per §8's guardrail (not a
    # general-purpose scan of every field the way confidence-escalation
    # is -- reader framing elsewhere isn't this check's job).
    # ============================================================
    good_significance = {"judgment": {"evidence_significance": "The usage increase persisted after the starter returned, strengthening the case that this represents a genuine role change."}}
    bad_significance = {"judgment": {"evidence_significance": "Fantasy managers and bettors should pay attention before the market catches up."}}
    results.append(check(
        "the spec's own §8 GOOD example produces zero reader-framing violations",
        scan_evidence_significance_for_reader_framing(good_significance) == [],
    ))
    bad_violations = scan_evidence_significance_for_reader_framing(bad_significance)
    results.append(check(
        "the spec's own §8 BAD example is caught -- both 'fantasy managers' and 'before the market' phrases flagged",
        {"fantasy managers", "before the market"} <= {v["phrase"] for v in bad_violations},
    ))
    results.append(check(
        "reader-framing language elsewhere (not evidence_significance) is NOT flagged by this scan -- confirmation.market_reaction is a different field with a different job",
        scan_evidence_significance_for_reader_framing({
            "confirmation": {"market_reaction": "bettors should watch this"},
            "judgment": {"evidence_significance": "A clean, scoped sentence."},
        }) == [],
    ))
    results.append(check(
        "READER_FRAMING_LANGUAGE and confidence-escalating language are two genuinely separate lists, not the same list reused",
        set(READER_FRAMING_LANGUAGE).isdisjoint(__import__("story_interrogation").CONFIDENCE_ESCALATING_LANGUAGE),
    ))

    # ============================================================
    # Tool schema / system prompt shape — the two things the task
    # explicitly said must not change (empty-array allowance, and the
    # V1 data-boundary instruction being present at all).
    # ============================================================
    alt_schema = INTERROGATION_TOOL_SCHEMA["input_schema"]["properties"]["challenge"]["properties"]["alternate_explanations"]
    results.append(check(
        "alternate_explanations has no minItems -- an empty array must stay a valid, accepted response, per the spec's own explicit instruction",
        "minItems" not in alt_schema,
    ))
    results.append(check(
        "the system prompt states the empty-array allowance explicitly, not just implied by schema shape alone",
        "empty array" in SYSTEM_PROMPT.lower() or "Do not manufacture one" in SYSTEM_PROMPT,
    ))
    results.append(check(
        "the system prompt states the hard V1 data-access boundary (no beat reporting/press conferences/quotes)",
        "beat reporting" in SYSTEM_PROMPT and "press conferences" in SYSTEM_PROMPT,
    ))

    # --- Real bug fix: a malformed response missing a required top-level
    # key must degrade gracefully, not crash with an uncaught KeyError
    # (confirmed live: 2 of 36 real calls in one measurement session hit
    # exactly this before the fix). Mocked call_claude_with_tool -- this
    # tests the reshaping code path itself, not a real live call. ---
    malformed_response = {
        "challenge": {"alternate_explanations": []},
        # "confirmation" key entirely absent -- the real observed shape
        "judgment": {"what_we_know": "x", "what_we_dont_know": "y", "evidence_significance": "z"},
        "signal_verdict": "UNRESOLVED",
    }
    orig_call = si.call_claude_with_tool
    si.call_claude_with_tool = lambda *a, **kw: malformed_response
    try:
        result = interrogate_story(
            {"intelligence_family": "nfl_picks", "entity": {"type": "player", "player_id": "P1"},
             "headline": "h", "hero_metric": None, "time_window": None, "sample_size": None,
             "supporting_evidence": None, "related_players": []},
            "fake-key",
        )
    finally:
        si.call_claude_with_tool = orig_call
    results.append(check(
        "a response missing the 'confirmation' key entirely degrades to a real object with confirmation.* "
        "all None, not an uncaught KeyError propagating out of interrogate_story()",
        result is not None
        and result["confirmation"] == {"supporting_signals": None, "contradicting_signals": None, "market_reaction": None}
        and result["signal_verdict"] == "UNRESOLVED",
    ))

    # --- relationship_established: schema declares it, reshaping passes
    # it through untouched, for both a real True and a real False value,
    # and stays honestly None when the model omits it (e.g. a SURVIVES/
    # FAILS response, where it's not applicable). ---
    prop = INTERROGATION_TOOL_SCHEMA["input_schema"]["properties"]["relationship_established"]
    results.append(check(
        "schema declares relationship_established as a nullable boolean, required as a key "
        "(present, honestly null when not applicable -- same convention as every other field here)",
        prop["type"] == ["boolean", "null"]
        and "relationship_established" in INTERROGATION_TOOL_SCHEMA["input_schema"]["required"],
    ))
    for value in (True, False, None):
        resp = {
            "challenge": {"alternate_explanations": []},
            "confirmation": {"supporting_signals": "a", "contradicting_signals": "b", "market_reaction": "c"},
            "judgment": {"what_we_know": "x", "what_we_dont_know": "y", "evidence_significance": "z"},
            "signal_verdict": "UNRESOLVED",
            "relationship_established": value,
        }
        si.call_claude_with_tool = lambda *a, **kw: resp
        try:
            result = interrogate_story(
                {"intelligence_family": "nfl_picks", "entity": {"type": "player", "player_id": "P1"},
                 "headline": "h", "hero_metric": None, "time_window": None, "sample_size": None,
                 "supporting_evidence": None, "related_players": []},
                "fake-key",
            )
        finally:
            si.call_claude_with_tool = orig_call
        results.append(check(
            f"relationship_established={value!r} passes through interrogate_story()'s reshaping unchanged",
            result is not None and result["relationship_established"] is value,
        ))

    # ============================================================
    # Fix 1 -- relationship_established/evidence_significance
    # consistency scan. Fixtures are the two REAL contradiction cases
    # from the validation session (verbatim evidence_significance text),
    # not synthetic examples.
    # ============================================================
    REAL_CASE_036637 = (
        "The input does not establish a coherent signal of change — five of six metrics sit close to "
        "a neutral midpoint with no baseline for comparison, and the one standout metric (situation) "
        "lacks any explanatory data. The oscillating, non-directional odds history neither reinforces "
        "nor undermines a specific directional claim, since no single directional claim is being made "
        "by the scored numbers themselves. Overall, the evidence available is too thin and internally "
        "ambiguous to treat this as an established shift in either direction."
    )
    REAL_CASE_037263 = (
        "The interrogation does not add independent support for the detected signal. The near-uniform "
        "mid-range values across most metrics do not by themselves indicate a real, differentiated "
        "change, and the one large divergence (season-long heat) has no explanation available in the "
        "data."
    )
    CLEAN_ESTABLISHED_CASE = (
        "The internal split between the situation score and the other five scores is real and visible "
        "in the raw magnitudes, but the input does not supply the context needed to explain it or "
        "establish whether it reflects a durable pattern rather than a single-week reading."
    )

    def _resp_with_sig(established, sig_text):
        return {
            "relationship_established": established,
            "judgment": {"what_we_know": "x", "what_we_dont_know": "y", "evidence_significance": sig_text},
        }

    results.append(check(
        "consistency scan: catches the real 00-0036637 case (established=True, prose says 'too thin "
        "and internally ambiguous... in either direction')",
        len(scan_relationship_established_for_contradiction(_resp_with_sig(True, REAL_CASE_036637))) > 0,
    ))
    results.append(check(
        "consistency scan: catches the real 00-0037263 case (established=True, prose says 'do not by "
        "themselves indicate a real, differentiated change')",
        len(scan_relationship_established_for_contradiction(_resp_with_sig(True, REAL_CASE_037263))) > 0,
    ))
    results.append(check(
        "consistency scan: a genuinely consistent established=True case (prose says the split 'is real "
        "and visible in the raw magnitudes') produces NO violation -- not a blanket penalty on every "
        "hedge-shaped sentence",
        scan_relationship_established_for_contradiction(_resp_with_sig(True, CLEAN_ESTABLISHED_CASE)) == [],
    ))
    results.append(check(
        "consistency scan: scoped to established=True only -- the SAME contradiction-shaped prose "
        "produces NO violation when established is False (a False value already means 'not established', "
        "so this specific mismatch can't occur there)",
        scan_relationship_established_for_contradiction(_resp_with_sig(False, REAL_CASE_036637)) == []
        and scan_relationship_established_for_contradiction(_resp_with_sig(None, REAL_CASE_036637)) == [],
    ))

    # End-to-end: a first response that violates the new scan gets
    # retried once, same mechanism as the other two scans; a clean
    # second response is accepted.
    violating_then_clean = [
        {
            "challenge": {"alternate_explanations": []},
            "confirmation": {"supporting_signals": "a", "contradicting_signals": "b", "market_reaction": "c"},
            "judgment": {"what_we_know": "x", "what_we_dont_know": "y", "evidence_significance": REAL_CASE_036637},
            "signal_verdict": "UNRESOLVED",
            "relationship_established": True,
        },
        {
            "challenge": {"alternate_explanations": []},
            "confirmation": {"supporting_signals": "a", "contradicting_signals": "b", "market_reaction": "c"},
            "judgment": {"what_we_know": "x", "what_we_dont_know": "y", "evidence_significance": CLEAN_ESTABLISHED_CASE},
            "signal_verdict": "UNRESOLVED",
            "relationship_established": True,
        },
    ]
    call_log = []

    def fake_call_with_retry(*a, **kw):
        call_log.append(1)
        return violating_then_clean[len(call_log) - 1]

    si.call_claude_with_tool = fake_call_with_retry
    try:
        result = interrogate_story(
            {"intelligence_family": "nfl_picks", "entity": {"type": "player", "player_id": "P1"},
             "headline": "h", "hero_metric": None, "time_window": None, "sample_size": None,
             "supporting_evidence": None, "related_players": []},
            "fake-key",
        )
    finally:
        si.call_claude_with_tool = orig_call
    results.append(check(
        "end-to-end: a violating first response triggers exactly one retry (2 real calls total), and "
        "the clean retried response is accepted",
        len(call_log) == 2 and result is not None and result["judgment"]["evidence_significance"] == CLEAN_ESTABLISHED_CASE,
    ))

    # ============================================================
    # Fix 2 -- _dict_field: wrong-type (string, not dict) crash guard.
    # ============================================================
    results.append(check(
        "_dict_field: a genuine dict value passes through unchanged",
        _dict_field({"challenge": {"alternate_explanations": [1, 2]}}, "challenge") == {"alternate_explanations": [1, 2]},
    ))
    results.append(check(
        "_dict_field: a missing key degrades to {} honestly",
        _dict_field({}, "challenge") == {},
    ))
    results.append(check(
        "_dict_field: a present-but-WRONG-TYPE value (the real observed bug -- a plain string instead "
        "of a dict) degrades to {} instead of crashing",
        _dict_field({"challenge": "not a dict"}, "challenge") == {},
    ))

    string_valued_response = {
        "challenge": "oops a string",
        "confirmation": "also a string",
        "judgment": "also also a string",
        "signal_verdict": "UNRESOLVED",
        "relationship_established": None,
    }
    results.append(check(
        "scan_for_confidence_escalation does not crash when challenge/confirmation/judgment are strings",
        scan_for_confidence_escalation(string_valued_response) == [],
    ))
    results.append(check(
        "scan_evidence_significance_for_reader_framing does not crash on the same malformed shape",
        scan_evidence_significance_for_reader_framing(string_valued_response) == [],
    ))

    si.call_claude_with_tool = lambda *a, **kw: string_valued_response
    try:
        result = interrogate_story(
            {"intelligence_family": "nfl_picks", "entity": {"type": "player", "player_id": "P1"},
             "headline": "h", "hero_metric": None, "time_window": None, "sample_size": None,
             "supporting_evidence": None, "related_players": []},
            "fake-key",
        )
    finally:
        si.call_claude_with_tool = orig_call
    results.append(check(
        "end-to-end: a response with challenge/confirmation/judgment all wrong-typed (strings, not "
        "dicts -- the real observed AttributeError case) degrades to a real object with everything "
        "honestly None, not an uncaught AttributeError",
        result is not None
        and result["challenge"] == {"alternate_explanations": []}
        and result["confirmation"] == {"supporting_signals": None, "contradicting_signals": None, "market_reaction": None}
        and result["judgment"] == {"what_we_know": None, "what_we_dont_know": None, "evidence_significance": None},
    ))

    # ============================================================
    # Primary-alternate signal (INTERROGATION_VERSION v3_primary_alternate):
    # schema, Task 1b instruction, output-consistency scan, reshaping,
    # retry wiring, and the fixture that carries it. Model mocked
    # throughout -- no network.
    # ============================================================
    P = " ".join(SYSTEM_PROMPT.split())  # whitespace-normalized: the prompt wraps mid-sentence
    scan_pa = si.scan_primary_alternate_consistency

    alt_items = alt_schema["items"]
    results.append(check(
        "schema: alternate_explanations[].alternate_id is declared AND required",
        "alternate_id" in alt_items["properties"] and "alternate_id" in alt_items["required"],
    ))
    top = INTERROGATION_TOOL_SCHEMA["input_schema"]
    results.append(check(
        "schema: top-level primary_alternate is declared, nullable, required, with alternate_id + selection_reason both required",
        "primary_alternate" in top["required"]
        and set(top["properties"]["primary_alternate"]["type"]) == {"object", "null"}
        and set(top["properties"]["primary_alternate"]["required"]) == {"alternate_id", "selection_reason"},
    ))
    results.append(check(
        "schema: alternate_explanations still has no minItems/maxItems -- exhaustiveness and the empty-array allowance are unchanged",
        "minItems" not in alt_schema and "maxItems" not in alt_schema,
    ))
    results.append(check(
        "prompt: Task 1b states the criterion -- most important to interpreting a meaningful change, NOT the most plausible, the one that would most seriously change the interpretation",
        "most important to interpreting whether the detected signal represents a meaningful change" in P
        and "NOT the most plausible alternate" in P
        and "would most seriously change the interpretation of the signal" in P,
    ))
    results.append(check(
        "prompt: Task 1b says the selection prunes nothing, the NOT_TESTABLE rule is unchanged, and primary_alternate is null when the array is empty",
        "prunes nothing" in P and "NOT_TESTABLE rule above is unchanged" in P
        and "When alternate_explanations is empty, primary_alternate is null" in P,
    ))
    results.append(check(
        "prompt: alternate_id is defined as a label, not a rank; the existing exhaustiveness instruction is still present verbatim",
        "not a rank" in P and "Do not manufacture one" in SYSTEM_PROMPT
        and "rather than silently dropping the explanation from the array" in P,
    ))
    results.append(check("INTERROGATION_VERSION bumped for the schema change", si.INTERROGATION_VERSION == "v3_primary_alternate"))

    def _alt(aid, status="WEAKENED"):
        return {"alternate_id": aid, "explanation": "e", "evidence": "v", "test": "t", "result": "r", "status": status}

    def _resp(alts, primary):
        return {
            "challenge": {"alternate_explanations": alts}, "primary_alternate": primary,
            "confirmation": {"supporting_signals": "s", "contradicting_signals": "c", "market_reaction": "m"},
            "judgment": {"what_we_know": "k", "what_we_dont_know": "d", "evidence_significance": "x"},
            "signal_verdict": "UNRESOLVED", "relationship_established": None,
        }

    story_stub = {"intelligence_family": "nfl_picks", "entity": {"type": "player", "player_id": "P1"}, "headline": "h",
                  "hero_metric": None, "time_window": None, "sample_size": None, "supporting_evidence": None, "related_players": []}

    clean = _resp([_alt("alt_1"), _alt("alt_2")], {"alternate_id": "alt_2", "selection_reason": "why"})
    results.append(check("scan: resolving primary_alternate + unique ids -> clean", scan_pa(clean) == []))
    results.append(check("scan: empty array + null primary -> clean", scan_pa(_resp([], None)) == []))
    dangling = scan_pa(_resp([_alt("alt_1")], {"alternate_id": "alt_9", "selection_reason": "why"}))
    results.append(check(
        "scan: a primary_alternate.alternate_id that resolves to no entry is a violation on primary_alternate.alternate_id",
        any(v["field"] == "primary_alternate.alternate_id" and "does not resolve" in v["phrase"] for v in dangling),
    ))
    results.append(check(
        "scan: non-empty array with a null primary -> violation (required when the array is non-empty)",
        any(v["field"] == "primary_alternate" and "required when alternate_explanations is non-empty" in v["phrase"]
            for v in scan_pa(_resp([_alt("alt_1")], None))),
    ))
    results.append(check(
        "scan: empty array with a non-null primary -> violation (must be null)",
        any("must be null" in v["phrase"] for v in scan_pa(_resp([], {"alternate_id": "alt_1", "selection_reason": "x"}))),
    ))
    results.append(check(
        "scan: duplicate alternate_ids -> violation (a pointer target must be unambiguous)",
        any(v["phrase"] == "duplicate alternate_id" and v["text"] == "alt_1"
            for v in scan_pa(_resp([_alt("alt_1"), _alt("alt_1")], {"alternate_id": "alt_1", "selection_reason": "x"}))),
    ))
    results.append(check(
        "scan: an entry missing alternate_id -> violation naming its index",
        any(v["field"] == "challenge.alternate_explanations[0].alternate_id"
            for v in scan_pa(_resp([{"explanation": "e", "status": "WEAKENED"}], {"alternate_id": "alt_1", "selection_reason": "x"}))),
    ))
    results.append(check(
        "scan: empty selection_reason -> violation",
        any(v["field"] == "primary_alternate.selection_reason"
            for v in scan_pa(_resp([_alt("alt_1")], {"alternate_id": "alt_1", "selection_reason": ""}))),
    ))
    results.append(check(
        "scan: wrong-typed challenge (a string) degrades to 'empty array' -- null primary is clean, no crash",
        scan_pa({"challenge": "oops", "primary_alternate": None}) == [],
    ))

    # --- reshaping keeps the new fields (and the existing key filter no longer drops alternate_id) ---
    orig_call = si.call_claude_with_tool
    si.call_claude_with_tool = lambda *a, **kw: clean
    try:
        result = interrogate_story(story_stub, "fake-key")
    finally:
        si.call_claude_with_tool = orig_call
    results.append(check(
        "reshaping: alternate_id survives the per-entry key filter, primary_alternate is returned top-level with exactly its two keys, version is v3",
        result is not None
        and [a["alternate_id"] for a in result["challenge"]["alternate_explanations"]] == ["alt_1", "alt_2"]
        and result["primary_alternate"] == {"alternate_id": "alt_2", "selection_reason": "why"}
        and result["interrogation_version"] == "v3_primary_alternate",
    ))
    si.call_claude_with_tool = lambda *a, **kw: _resp([], None)
    try:
        result_empty = interrogate_story(story_stub, "fake-key")
    finally:
        si.call_claude_with_tool = orig_call
    results.append(check(
        "reshaping: empty array -> primary_alternate is None (present as a key, not missing)",
        result_empty is not None and "primary_alternate" in result_empty and result_empty["primary_alternate"] is None,
    ))

    # --- wiring: the scan rides the same one-shot retry as the other three ---
    calls = {"n": 0}

    def dangling_call(*a, **kw):
        calls["n"] += 1
        return _resp([_alt("alt_1")], {"alternate_id": "alt_9", "selection_reason": "why"})

    si.call_claude_with_tool = dangling_call
    try:
        result_bad = interrogate_story(story_stub, "fake-key")
    finally:
        si.call_claude_with_tool = orig_call
    results.append(check(
        "wiring: a dangling primary_alternate.alternate_id triggers exactly one retry and, if it persists, returns None -- never a record pointing at nothing",
        result_bad is None and calls["n"] == 2,
    ))
    seq = [
        _resp([_alt("alt_1")], {"alternate_id": "alt_9", "selection_reason": "why"}),
        _resp([_alt("alt_1")], {"alternate_id": "alt_1", "selection_reason": "why"}),
    ]
    calls2 = {"n": 0}

    def fixed_on_retry(*a, **kw):
        out = seq[calls2["n"]]
        calls2["n"] += 1
        return out

    si.call_claude_with_tool = fixed_on_retry
    try:
        result_fixed = interrogate_story(story_stub, "fake-key")
    finally:
        si.call_claude_with_tool = orig_call
    results.append(check(
        "wiring: a retry that resolves the id is accepted (two calls, real record returned)",
        result_fixed is not None and result_fixed["primary_alternate"]["alternate_id"] == "alt_1" and calls2["n"] == 2,
    ))
    retry_text = si._retry_prompt({"x": 1}, dangling)
    results.append(check(
        "retry prompt quotes the structural problem back (field + problem), no longer worded as 'language rules'",
        "primary_alternate.alternate_id" in retry_text and "does not resolve" in retry_text and "language rules" not in retry_text,
    ))

    # --- the fixture carries the signal correctly, and the scan agrees ---
    fixtures = __import__("json").loads((Path(__file__).resolve().parent / "newsletter" / "fixture_v2.json").read_text())
    ok_all = len(fixtures) == 7
    for f in fixtures:
        intr = f["interrogation"]
        alts = intr["challenge"]["alternate_explanations"]
        ids = [a.get("alternate_id") for a in alts]
        if alts:
            ok_all &= all(isinstance(x, str) and x for x in ids) and len(set(ids)) == len(ids)
            ok_all &= isinstance(intr.get("primary_alternate"), dict) and intr["primary_alternate"]["alternate_id"] in ids \
                and bool(intr["primary_alternate"]["selection_reason"].strip())
        else:
            ok_all &= "primary_alternate" in intr and intr["primary_alternate"] is None
        ok_all &= intr["interrogation_version"] == "v3_primary_alternate"
        ok_all &= scan_pa(intr) == []
    results.append(check(
        "fixture_v2.json: all 7 interrogations carry unique alternate_ids and a resolving primary_alternate (null exactly when the array is empty), the v3 label, and pass the scan",
        ok_all,
    ))
    f7 = next(f for f in fixtures if f["fixture_id"] == "fixture_7")["interrogation"]
    results.append(check(
        "fixture 7: primary_alternate is the coach's stated-plan alternate (alt_1), chosen on the interpretation criterion, with the reason recorded",
        f7["primary_alternate"]["alternate_id"] == "alt_1"
        and f7["challenge"]["alternate_explanations"][0]["explanation"].startswith("The head coach")
        and "durability" in f7["primary_alternate"]["selection_reason"]
        and len(f7["challenge"]["alternate_explanations"]) == 2,  # still exhaustive -- nothing pruned
    ))

    # ============================================================
    # PRIOR_HISTORY PROMPT IDENTITY (Follow the Story, 2026-10-02) --
    # proves the flag-off / prior_history=None path sends the exact same
    # system-prompt bytes as before this addition existed, by intercepting
    # what interrogate_story() actually hands call_claude_with_tool, not
    # by re-deriving the claim from source inspection alone.
    # ============================================================
    results.append(check(
        "SYSTEM_PROMPT itself carries no detailed prior_history guidance -- only the pre-existing generic "
        "'whatever fields are populated' mention; the new section/task additions live only in the second variant",
        "## Reading prior_history" not in SYSTEM_PROMPT
        and "single-week\noutlier" not in SYSTEM_PROMPT
        and "consistency with its own recent_weeks" not in SYSTEM_PROMPT,
    ))
    results.append(check(
        "SYSTEM_PROMPT_WITH_PRIOR_HISTORY is strictly SYSTEM_PROMPT plus real content, not a different base text",
        SYSTEM_PROMPT_WITH_PRIOR_HISTORY != SYSTEM_PROMPT
        and SYSTEM_PROMPT_WITH_PRIOR_HISTORY.startswith(SYSTEM_PROMPT.split("## Task 1")[0])
        and "## Reading prior_history" in SYSTEM_PROMPT_WITH_PRIOR_HISTORY,
    ))
    results.append(check(
        "the with-variant never splits a sentence across the insertion seam (a real bug caught while building this)",
        "must be\n\nWhen prior_history" not in SYSTEM_PROMPT_WITH_PRIOR_HISTORY
        and "must be\ndetermined INDEPENDENTLY" in SYSTEM_PROMPT_WITH_PRIOR_HISTORY,
    ))
    results.append(check(
        "no doubled blank line at any of the three insertion seams",
        "\n\n\n" not in SYSTEM_PROMPT_WITH_PRIOR_HISTORY,
    ))

    _captured_system_blocks = {}

    def _fake_call_claude_with_tool(api_key, system_blocks_arg, user_prompt, tool_schema, max_tokens=None):
        _captured_system_blocks["value"] = system_blocks_arg
        return {
            "challenge": {"alternate_explanations": []},
            "primary_alternate": None,
            "confirmation": {
                "supporting_signals": "nothing else in the input",
                "contradicting_signals": "nothing in the input",
                "market_reaction": "not available",
            },
            "judgment": {
                "what_we_know": "a baseline test record",
                "what_we_dont_know": "nothing relevant to this check",
                "evidence_significance": "no real interpretation needed for this check",
            },
            "signal_verdict": "UNRESOLVED",
            "relationship_established": "No real alternate explanation existed to test, so nothing establishes a relationship here.",
        }

    _original_call_claude_with_tool = si.call_claude_with_tool
    si.call_claude_with_tool = _fake_call_claude_with_tool
    try:
        fixture_story = {
            "intelligence_family": "nfl_picks",
            "entity": {"type": "player", "player_id": "00-TEST", "player_name": "Test Player"},
            "headline": "Test headline",
            "hero_metric": None,
            "time_window": None,
            "sample_size": None,
            "supporting_evidence": None,
            "related_players": [],
        }

        interrogate_story(fixture_story, "fake-api-key", prior_history=None)
        results.append(check(
            "prior_history=None (today's hardcoded default at the one real call site) sends CACHED_SYSTEM_BLOCKS exactly -- byte-identical to before this addition existed",
            _captured_system_blocks["value"] == CACHED_SYSTEM_BLOCKS,
        ))

        interrogate_story(fixture_story, "fake-api-key", prior_history={"weeks_present": [3], "coverage": "1 of 1 reconciled weeks"})
        results.append(check(
            "a real prior_history dict sends CACHED_SYSTEM_BLOCKS_WITH_PRIOR_HISTORY, not the base blocks",
            _captured_system_blocks["value"] == CACHED_SYSTEM_BLOCKS_WITH_PRIOR_HISTORY
            and _captured_system_blocks["value"] != CACHED_SYSTEM_BLOCKS,
        ))

        interrogate_story(fixture_story, "fake-api-key")
        results.append(check(
            "omitting prior_history entirely (the real default) is identical to passing prior_history=None explicitly",
            _captured_system_blocks["value"] == CACHED_SYSTEM_BLOCKS,
        ))
    finally:
        si.call_claude_with_tool = _original_call_claude_with_tool

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
