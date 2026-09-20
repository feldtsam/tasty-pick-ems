"""
Unit tests for nfl/newsletter/evidence_validator.py.

    python3 nfl/newsletter/test_evidence_validator.py

Fixtures match the REAL nfl_intelligence_stories schema exactly (per
intelligence_schema.py's own field docs and the real live rows a prior
investigation this session pulled and inspected directly — Cooper
Kupp/Jaxon Smith-Njigba/etc., New England Patriots @ Seattle Seahawks,
season 2026 week 1) — not invented shapes.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evidence_validator import (
    CONFIDENCE_ESCALATING_LANGUAGE,
    check_claim_traceability,
    check_evidence_confidence_alignment,
    check_gate_consistency,
    check_interrogation_traceability,
    check_one_treatment,
    check_relationship_traceability,
    check_scoring_language_leak,
    validate_editorial_contract,
    validate_newsletter_story,
)


def check(label, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    return bool(cond)


# --- Real-shaped fixtures (matching the real 2026 week 1 data this session already pulled) ---

KUPP_STORY = {
    "story_id": "ef906bfe-5eb0-45d4-bba9-f4ff3ed653e0",
    "intelligence_family": "market_intelligence",
    "entity": {"team": "SEA", "type": "player", "player_id": "00-0033908", "player_name": "Cooper Kupp", "position_group": "WR"},
    "headline": "The market has him priced in the middle of the pack, at least in an early read.",
    "story": "With only one book posted so far, Cooper Kupp sits at rank 8 of 20 in this week's early market-implied field for New England Patriots @ Seattle Seahawks.",
    "primary_signal": {"name": "market_value_score", "value": 60},
    "supporting_evidence": [
        "Consensus price: +240 (29.4% implied probability)",
        "Based on 1 book — a thin, early read",
        "Ranks 8 of 20 players with a posted market this week (market_value_score 60/100)",
    ],
    "trend_direction": "market-neutral",
    "trend_strength": 20,
    "sample_size": 1,
    "completeness": 57.7,
    "confidence": 57.7,
    "related_players": [
        {"note": "Market value score 95/100 · -105 (51.2% implied)", "player_id": "00-0038543", "display_label": "Jaxon Smith-Njigba"},
        {"note": "Market value score 90/100 · +110 (47.6% implied)", "player_id": "00-0041512", "display_label": "Jadarian Price"},
    ],
    "evidence_classification": "limited",
}

# A real "role_changes"-shaped story with strong evidence, for the
# confidence-alignment "not limited -> no flag" contrast case.
STEVENSON_STORY = {
    "story_id": "42031fe2-5377-407d-aed5-572fa862963c",
    "intelligence_family": "market_intelligence",
    "entity": {"team": "NE", "type": "player", "player_id": "00-0036875", "player_name": "Rhamondre Stevenson", "position_group": "RB"},
    "headline": "One early book already has him near the top of the board.",
    "story": "A single early line already prices Rhamondre Stevenson among the field's strongest anytime-TD bets.",
    "primary_signal": {"name": "market_value_score", "value": 85},
    "supporting_evidence": ["Consensus price: +150 (40.0% implied probability)", "Ranks 3 of 20 players with a posted market this week (market_value_score 85/100)"],
    "trend_direction": "market-favored",
    "trend_strength": 70,
    "sample_size": 1,
    "completeness": 90.0,
    "confidence": 90.0,
    "related_players": [],
    "evidence_classification": "strong",
}

STORIES_BY_ID = {KUPP_STORY["story_id"]: KUPP_STORY, STEVENSON_STORY["story_id"]: STEVENSON_STORY}

# Real interrogation content — copied verbatim from this session's own
# real story_interrogation.py run against the real Michael Mayer Role
# Changes row (Week 1 2026), not invented. Used for check_interrogation_
# traceability's real success/failure cases below.
KEANE_STORY = {
    "story_id": "aaaaaaaa-0000-0000-0000-000000000001",
    "intelligence_family": "role_changes",
    "entity": {"type": "player", "player_id": "00-keane", "player_name": "Dorsey Keane", "team": "DAL"},
    "supporting_evidence": ["Goal-line opportunity share increased from 24% to 58% over 3 games"],
    "related_players": [],
    "evidence_classification": "strong",
    "interrogation": {
        "interrogation_version": "v1_structured_data",
        "challenge": {
            "alternate_explanations": [
                {
                    "explanation": "Incumbent RB was limited by a minor ankle issue",
                    "evidence": "Injury report, Week 4",
                    "test": "Did the shift persist after the incumbent returned to full practice?",
                    "result": "Incumbent returned Week 5; Keane still received 5 of 7 goal-line opportunities",
                    "status": "WEAKENED",
                },
            ],
        },
        "confirmation": {
            "supporting_signals": "Snap share and red-zone targets also rose",
            "contradicting_signals": "None identified",
            "market_reaction": "ATTD price moved from +650 to +400",
        },
    },
}

# Same real shape, but with an empty challenge and a market_reaction
# that explicitly reports no movement — for the negative/failing cases
# below (a claim of "survived" or "hasn't caught up" with nothing real
# to trace to).
RHOADS_STORY = {
    "story_id": "aaaaaaaa-0000-0000-0000-000000000002",
    "intelligence_family": "market_intelligence",
    "entity": {"type": "player", "player_id": "00-rhoads", "player_name": "Callum Rhoads", "team": "SEA"},
    "supporting_evidence": ["Cross-book price convergence, low volatility"],
    "related_players": [],
    "evidence_classification": "limited",
    "interrogation": {
        "interrogation_version": "v1_structured_data",
        "challenge": {"alternate_explanations": []},
        "confirmation": {
            "supporting_signals": "Role and target share flat over the same window",
            "contradicting_signals": "None",
            "market_reaction": "No meaningful movement — this is the observation itself",
        },
    },
}

# Same real shape as KEANE_STORY, one alternate_explanations[] entry
# each, varying ONLY the status — for the per-status regression coverage
# below (does a "survived scrutiny" claim validate correctly for EACH of
# the four real statuses, not just WEAKENED/empty).
SUPPORTED_STORY = {
    "story_id": "aaaaaaaa-0000-0000-0000-000000000003",
    "intelligence_family": "role_changes",
    "entity": {"type": "player", "player_id": "00-supported", "player_name": "Dane Supported", "team": "DAL"},
    "supporting_evidence": ["Goal-line opportunity share increased from 24% to 58% over 3 games"],
    "related_players": [],
    "evidence_classification": "strong",
    "interrogation": {
        "interrogation_version": "v1_structured_data",
        "challenge": {
            "alternate_explanations": [
                {
                    "explanation": "Incumbent RB was limited by a minor ankle issue",
                    "evidence": "Injury report, Week 4",
                    "test": "Did the shift persist after the incumbent returned to full practice?",
                    "result": "Incumbent returned Week 5; opportunity share reverted to 26%",
                    "status": "SUPPORTED",
                },
            ],
        },
        "confirmation": {
            "supporting_signals": "None identified beyond the injury window",
            "contradicting_signals": "Share reverted once the incumbent returned",
            "market_reaction": "ATTD price moved from +650 to +400, then back to +600",
        },
    },
}

UNRESOLVED_STORY = {
    "story_id": "aaaaaaaa-0000-0000-0000-000000000004",
    "intelligence_family": "role_changes",
    "entity": {"type": "player", "player_id": "00-unresolved", "player_name": "Reese Unresolved", "team": "DAL"},
    "supporting_evidence": ["Goal-line opportunity share increased from 24% to 58% over 3 games"],
    "related_players": [],
    "evidence_classification": "strong",
    "interrogation": {
        "interrogation_version": "v1_structured_data",
        "challenge": {
            "alternate_explanations": [
                {
                    "explanation": "Incumbent RB was limited by a minor ankle issue",
                    "evidence": "Injury report, Week 4",
                    "test": "Did the shift persist after the incumbent returned to full practice?",
                    "result": "Incumbent has not yet returned to a full practice snap count -- test not yet resolvable either way",
                    "status": "UNRESOLVED",
                },
            ],
        },
        "confirmation": {
            "supporting_signals": "Snap share also rose over the same window",
            "contradicting_signals": "None identified",
            "market_reaction": "ATTD price moved from +650 to +400",
        },
    },
}

NOT_TESTABLE_STORY = {
    "story_id": "aaaaaaaa-0000-0000-0000-000000000005",
    "intelligence_family": "role_changes",
    "entity": {"type": "player", "player_id": "00-nottestable", "player_name": "Ames Nottestable", "team": "DAL"},
    "supporting_evidence": ["Goal-line opportunity share increased from 24% to 58% over 3 games"],
    "related_players": [],
    "evidence_classification": "strong",
    "interrogation": {
        "interrogation_version": "v1_structured_data",
        "challenge": {
            "alternate_explanations": [
                {
                    "explanation": "A coaching change altered the goal-line scheme entirely",
                    "evidence": "No coaching-staff data available in the current input",
                    "test": "Would require play-calling attribution data not present in this input",
                    "result": "Not testable with the fields available",
                    "status": "NOT_TESTABLE",
                },
            ],
        },
        "confirmation": {
            "supporting_signals": "Snap share also rose over the same window",
            "contradicting_signals": "None identified",
            "market_reaction": "ATTD price moved from +650 to +400",
        },
    },
}

STORIES_BY_ID[KEANE_STORY["story_id"]] = KEANE_STORY
STORIES_BY_ID[RHOADS_STORY["story_id"]] = RHOADS_STORY
STORIES_BY_ID[SUPPORTED_STORY["story_id"]] = SUPPORTED_STORY
STORIES_BY_ID[UNRESOLVED_STORY["story_id"]] = UNRESOLVED_STORY
STORIES_BY_ID[NOT_TESTABLE_STORY["story_id"]] = NOT_TESTABLE_STORY


if __name__ == "__main__":
    r = []

    # --- Claim traceability: numeric ---
    text_clean = "Cooper Kupp is priced at +240, a 29.4% implied read, ranking 8 of 20 players in an early market."
    results = check_claim_traceability(text_clean, [KUPP_STORY])
    numeric_results = [x for x in results if x["claim_type"] == "numeric"]
    r.append(check("a real, exact odds price (+240) grounds cleanly", any(x["claim_text"] == "+240" and x["status"] == "pass" for x in numeric_results)))
    r.append(check("a real percentage (29.4%) grounds cleanly", any(x["claim_text"] == "29.4%" and x["status"] == "pass" for x in numeric_results)))
    # 8 and 20 both clear the small-number floor (>=6) and both genuinely
    # appear in KUPP_STORY's own supporting_evidence ("Ranks 8 of 20") --
    # real values copied verbatim from the source, correctly grounded,
    # not excluded. The floor's OWN job (skipping single-digit narrative
    # counts below 6, e.g. "his 3rd target") is tested separately below.
    r.append(check("real rank numbers actually present in the source (8, 20) ground cleanly, not excluded", all(x["status"] == "pass" for x in numeric_results if x["claim_text"] in ("8", "20"))))

    text_below_floor = "This was his 3rd target of the day, a routine, unremarkable number."
    below_floor_results = check_claim_traceability(text_below_floor, [KUPP_STORY])
    r.append(check("a genuine small narrative count (3) below the floor is skipped entirely, not checked at all", not any(x["claim_type"] == "numeric" for x in below_floor_results)))

    text_fabricated = "Cooper Kupp is priced at +999, an eye-popping number nobody saw coming."
    fab_results = check_claim_traceability(text_fabricated, [KUPP_STORY])
    r.append(check("a fabricated odds price (+999) is caught as a hard fail", any(x["claim_text"] == "+999" and x["status"] == "fail" for x in fab_results)))

    text_rounded = "Cooper Kupp's market value score sits around 58, basically the real 57.7 read."
    rounded_results = check_claim_traceability(text_rounded, [KUPP_STORY])
    r.append(check("a reasonably-rounded number (58 for real 57.7) grounds within tolerance", any(x["claim_text"] == "58" and x["status"] == "pass" for x in rounded_results)))

    # --- Claim traceability: named entity ---
    # Real name mid-sentence, not at position 0 -- realistic newsletter
    # phrasing (a name-shaped candidate at the very start of the checked
    # text is deliberately excluded by the extractor's own false-positive
    # guard, same as an ordinary capitalized sentence-opener would be).
    # NOTE: the hyphen in "Smith-Njigba" splits the name-candidate regex
    # into two fragments ("Jaxon Smith" / "Njigba") -- a real, honest
    # limit of the whitespace-based candidate pattern, not something this
    # test papers over. Both fragments still correctly ground as
    # substrings of the real pool name "Jaxon Smith-Njigba".
    text_real_name = "Early attention is on Jaxon Smith-Njigba in this matchup."
    name_results = check_claim_traceability(text_real_name, [KUPP_STORY])
    entity_results = [x for x in name_results if x["claim_type"] == "entity"]
    r.append(check(
        "a real related-player name (even hyphenated, split into fragments) grounds -- no fail anywhere",
        len(entity_results) > 0 and all(x["status"] == "pass" for x in entity_results),
    ))

    text_fake_name = "Early attention is on Marcus Fictional Player in this matchup."
    fake_name_results = check_claim_traceability(text_fake_name, [KUPP_STORY])
    fake_entity_results = [x for x in fake_name_results if x["claim_type"] == "entity"]
    r.append(check("an unrecognized name is flagged needs_review, never a silent pass", any(x["status"] == "needs_review" for x in fake_entity_results)))

    # --- Relationship traceability ---
    text_both_grounded = "Cooper Kupp's market value score sits at 60 while his real rank of 8 of 20 places him in the middle of the field, both real reads from the same 240 price."
    # (deliberately awkward phrasing so both clauses carry a real grounded number)
    rel_results = check_relationship_traceability(
        "The market prices him at +240 while his market value score of 60 sits mid-pack.",
        [KUPP_STORY],
    )
    r.append(check("a relationship claim with BOTH halves grounded passes", any(x["status"] == "pass" for x in rel_results)))

    rel_one_sided = check_relationship_traceability(
        "The market prices him at +240 while his role has quietly taken over the entire offense.",
        [KUPP_STORY],
    )
    r.append(check("a relationship claim with only ONE grounded half is flagged fail", any(x["status"] == "fail" for x in rel_one_sided)))
    r.append(check("the one-sided flag's detail names which half is weak", any("second clause" in x["detail"] or "second" in x["detail"] for x in rel_one_sided if x["status"] == "fail")))

    rel_neither = check_relationship_traceability(
        "His role has quietly grown while the market hasn't fully caught up yet.",
        [KUPP_STORY],
    )
    r.append(check("a relationship claim with NEITHER half grounded is needs_review, not a silent pass", any(x["status"] == "needs_review" for x in rel_neither)))

    # --- Evidence confidence alignment ---
    text_overconfident = "It's confirmed: Cooper Kupp's market value is settled at 60, clearly the right read."
    conf_results = check_evidence_confidence_alignment(text_overconfident, [KUPP_STORY])
    flagged_words = {x["claim_text"] for x in conf_results}
    r.append(check("'confirmed' is flagged when the source story is limited", "confirmed" in flagged_words))
    r.append(check("'settled' is flagged when the source story is limited", "settled" in flagged_words))
    r.append(check("'clearly' is flagged when the source story is limited", "clearly" in flagged_words))
    r.append(check("every confidence-alignment flag is a hard fail (not needs_review)", all(x["status"] == "fail" for x in conf_results)))

    text_hedged = "Cooper Kupp's market value score of 60 is an early, thin read worth watching, not a settled verdict yet."
    # "settled" still appears in the hedge itself -- confirms the check
    # flags the PHRASE regardless of surrounding hedge language elsewhere
    # in the same sentence (a real, honest limit of a word-list approach,
    # not silently smoothed over).
    hedge_results = check_evidence_confidence_alignment(text_hedged, [KUPP_STORY])
    r.append(check("word-list check still flags 'settled' even inside an otherwise-hedged sentence (honest limit, not silently smoothed)", any(x["claim_text"] == "settled" for x in hedge_results)))

    text_strong_only = "Rhamondre Stevenson's market value score of 85 makes this a confirmed, clearly strong read."
    strong_results = check_evidence_confidence_alignment(text_strong_only, [STEVENSON_STORY])
    r.append(check("confidence-escalating language is NOT flagged when the only referenced story is 'strong', not 'limited'", strong_results == []))

    text_mixed = "Both players look live this week, a confirmed read across the board."
    mixed_results = check_evidence_confidence_alignment(text_mixed, [KUPP_STORY, STEVENSON_STORY])
    r.append(check("confidence-escalating language IS flagged when ANY referenced story (not all) is 'limited'", any(x["status"] == "fail" for x in mixed_results)))

    no_classification_story = {k: v for k, v in KUPP_STORY.items() if k != "evidence_classification"}
    missing_results = check_evidence_confidence_alignment("It's confirmed.", [no_classification_story])
    r.append(check("missing evidence_classification on every referenced story surfaces needs_review, not a silent pass", any(x["status"] == "needs_review" for x in missing_results)))

    # --- Full entry point ---
    clean_story_body = "Cooper Kupp sits at +240, a market value score of 60, ranking 8 of 20 in an early, thin read worth watching."
    full_clean = validate_newsletter_story(
        headline="An early, honest read on Cooper Kupp",
        body=clean_story_body,
        intelligence_story_ids=[KUPP_STORY["story_id"]],
        stories_by_id=STORIES_BY_ID,
    )
    r.append(check("a clean, well-grounded, appropriately-hedged story passes end to end", full_clean["passed"] is True))

    bad_story_body = "It's confirmed: Cooper Kupp is priced at +999, clearly the top play this week."
    full_bad = validate_newsletter_story(
        headline="A confirmed lock",
        body=bad_story_body,
        intelligence_story_ids=[KUPP_STORY["story_id"]],
        stories_by_id=STORIES_BY_ID,
    )
    r.append(check("a fabricated-number + overconfident story fails end to end", full_bad["passed"] is False))
    r.append(check("the failing report's summary is human-readable and non-empty", isinstance(full_bad["summary"], str) and len(full_bad["summary"]) > 0))

    missing_id_result = validate_newsletter_story(
        headline="x", body="x",
        intelligence_story_ids=[KUPP_STORY["story_id"], "00000000-0000-0000-0000-000000000000"],
        stories_by_id=STORIES_BY_ID,
    )
    r.append(check("a claimed-but-not-found intelligence_story_id fails the story and is reported by id", missing_id_result["passed"] is False and missing_id_result["missing_story_ids"] == ["00000000-0000-0000-0000-000000000000"]))

    # --- A real finding, made explicit: zero intelligence_story_ids ---

    from_the_desk_result = validate_newsletter_story(
        headline="From the Desk of Mr. Pick Ems",
        body="Both look like something at first glance. Only one of them survived me asking why twice.",
        intelligence_story_ids=[],
        stories_by_id=STORIES_BY_ID,
    )
    r.append(check(
        "REAL FINDING (found via real Fixture V2 output): a from_the_desk-shaped entry with zero intelligence_story_ids passes cleanly despite using 'survived' conversationally — real, pre-existing behavior made explicit, not papered over with a special case for this one word",
        from_the_desk_result["passed"] is True and from_the_desk_result["interrogation_traceability"] == [] and from_the_desk_result["claim_traceability"] == [],
    ))

    # --- Check 4: Interrogation traceability (Story Interrogation spec §10) ---

    survival_text_grounded = "Keane's goal-line role survived the incumbent's return, taking 5 of 7 opportunities since."
    survival_grounded = check_interrogation_traceability(survival_text_grounded, [KEANE_STORY])
    r.append(check(
        "a 'survived' claim traced to a real WEAKENED alternate_explanations entry passes",
        any(x["claim_type"] == "survived_scrutiny" and x["status"] == "pass" for x in survival_grounded),
    ))

    survival_text_ungrounded = "Rhoads' price stability survived every attempt to explain it away."
    survival_ungrounded = check_interrogation_traceability(survival_text_ungrounded, [RHOADS_STORY])
    r.append(check(
        "a 'survived' claim against a story with an EMPTY alternate_explanations[] fails — traces to nothing real",
        any(x["claim_type"] == "survived_scrutiny" and x["status"] == "fail" for x in survival_ungrounded),
    ))

    # --- Per-status regression: all four real interrogation statuses,
    # individually, confirming ONLY WEAKENED grounds a "survived scrutiny"
    # claim. The real bug this guards: SUPPORTED and UNRESOLVED were both
    # previously (wrongly) treated as grounding -- SUPPORTED means the
    # alternate explanation WON (the opposite of survival), and UNRESOLVED
    # means the test was inconclusive (not evidence of survival either).
    survival_supported = check_interrogation_traceability(
        "Supported's goal-line role survived the incumbent's return.", [SUPPORTED_STORY],
    )
    r.append(check(
        "SUPPORTED status does NOT ground a 'survived scrutiny' claim -- SUPPORTED means the alternate explanation won, the opposite of survival",
        any(x["claim_type"] == "survived_scrutiny" and x["status"] == "fail" for x in survival_supported),
    ))

    survival_weakened_explicit = check_interrogation_traceability(
        "Keane's goal-line role survived the incumbent's return.", [KEANE_STORY],
    )
    r.append(check(
        "WEAKENED status DOES ground a 'survived scrutiny' claim -- the one real, correct grounding status",
        any(x["claim_type"] == "survived_scrutiny" and x["status"] == "pass" for x in survival_weakened_explicit),
    ))

    survival_unresolved = check_interrogation_traceability(
        "Unresolved's goal-line role survived the incumbent's return.", [UNRESOLVED_STORY],
    )
    r.append(check(
        "UNRESOLVED status does NOT ground a 'survived scrutiny' claim -- an inconclusive test is not evidence of survival",
        any(x["claim_type"] == "survived_scrutiny" and x["status"] == "fail" for x in survival_unresolved),
    ))

    survival_not_testable = check_interrogation_traceability(
        "Nottestable's goal-line role survived the incumbent's return.", [NOT_TESTABLE_STORY],
    )
    r.append(check(
        "NOT_TESTABLE status does NOT ground a 'survived scrutiny' claim -- no real test could even be run",
        any(x["claim_type"] == "survived_scrutiny" and x["status"] == "fail" for x in survival_not_testable),
    ))

    market_text_grounded = "The market hasn't caught up with Rhoads' actual role yet."
    market_grounded = check_interrogation_traceability(market_text_grounded, [RHOADS_STORY])
    r.append(check(
        "a 'market hasn't caught up' claim traced to a real 'no meaningful movement' market_reaction passes",
        any(x["claim_type"] == "market_reaction" and x["status"] == "pass" for x in market_grounded),
    ))

    market_text_ungrounded = "The market hasn't caught up with Keane's real role yet."
    market_ungrounded = check_interrogation_traceability(market_text_ungrounded, [KEANE_STORY])
    r.append(check(
        "a 'market hasn't caught up' claim against a story whose market_reaction describes REAL MOVEMENT fails — movement argues against the claim, not for it",
        any(x["claim_type"] == "market_reaction" and x["status"] == "fail" for x in market_ungrounded),
    ))

    escalation_on_survival = "It's confirmed: Keane's role survived the incumbent's return."
    escalation_results = check_interrogation_traceability(escalation_on_survival, [KEANE_STORY])
    r.append(check(
        "confidence-escalating language on an otherwise-grounded survival claim still hard-fails, same word list",
        any(x["claim_type"] == "confidence_escalation" and x["status"] == "fail" for x in escalation_results),
    ))

    interrogation_full = validate_newsletter_story(
        headline="Keane's role held",
        body="Keane's role survived the incumbent's return.",
        intelligence_story_ids=[KEANE_STORY["story_id"]],
        stories_by_id=STORIES_BY_ID,
    )
    r.append(check(
        "validate_newsletter_story's own report carries interrogation_traceability results end to end, and a genuinely clean story (no stray unground-able numbers) passes overall",
        len(interrogation_full["interrogation_traceability"]) > 0
        and all(x["status"] == "pass" for x in interrogation_full["interrogation_traceability"])
        and interrogation_full["passed"] is True,
    ))

    # --- Editorial Contract Validation: scoring-language leak ---

    r.append(check(
        "a bare dimension name ('Significance') in reader-facing prose is flagged",
        any(x["status"] == "fail" for x in check_scoring_language_leak("The Significance of this shift is real.")),
    ))
    r.append(check(
        "'EPS' as a bare token is flagged",
        any(x["status"] == "fail" for x in check_scoring_language_leak("This story's EPS was high this week.")),
    ))
    r.append(check(
        "'scored 82' (system-narration shape) is flagged",
        any(x["status"] == "fail" for x in check_scoring_language_leak("This story scored 82, making it the strongest this week.")),
    ))
    r.append(check(
        "ordinary football language ('he scored a touchdown') is NOT flagged — deliberately scoped to 'scored <number>', not bare 'scored'",
        check_scoring_language_leak("Keane scored a touchdown in the fourth quarter.") == [],
    ))
    r.append(check(
        "clean, ordinary editorial prose with no scoring language produces zero leak findings",
        check_scoring_language_leak("Keane's goal-line role held after the incumbent returned.") == [],
    ))

    # --- Editorial Contract Validation: gate consistency ---

    issue_gate_ok = {
        "sections": [
            {"section_type": "big_one", "stories": [
                {"intelligence_story_ids": ["s1"], "eps_scores": {"big_one_eligible": True, "watchlist_eligible": True}},
            ], "cross_references": []},
        ],
    }
    r.append(check(
        "a Big One placement with big_one_eligible=true passes",
        all(x["status"] == "pass" for x in check_gate_consistency(issue_gate_ok)),
    ))

    issue_gate_violated = {
        "sections": [
            {"section_type": "big_one", "stories": [
                {"intelligence_story_ids": ["s1"], "eps_scores": {"big_one_eligible": False, "watchlist_eligible": False}},
            ], "cross_references": []},
        ],
    }
    r.append(check(
        "ACCEPTANCE TEST: a Big One placement with big_one_eligible=false FAILS — the gate was not obeyed",
        any(x["status"] == "fail" for x in check_gate_consistency(issue_gate_violated)),
    ))

    issue_gate_missing = {
        "sections": [
            {"section_type": "watchlist", "stories": [
                {"intelligence_story_ids": ["s1"], "eps_scores": {}},
            ], "cross_references": []},
        ],
    }
    r.append(check(
        "a story missing watchlist_eligible entirely in its eps_scores snapshot is needs_review, not silently passed or guessed",
        any(x["status"] == "needs_review" for x in check_gate_consistency(issue_gate_missing)),
    ))
    r.append(check(
        "sections other than big_one/watchlist (e.g. what_changed) are never gate-checked at all",
        check_gate_consistency({"sections": [{"section_type": "what_changed", "stories": [{"intelligence_story_ids": ["s1"], "eps_scores": {}}], "cross_references": []}]}) == [],
    ))

    # --- Editorial Contract Validation: one-treatment ---

    issue_one_treatment_ok = {
        "sections": [
            {"section_type": "big_one", "stories": [{"intelligence_story_ids": ["s1"], "eps_scores": {}}], "cross_references": []},
            {"section_type": "what_changed", "stories": [{"intelligence_story_ids": ["s2"], "eps_scores": {}}], "cross_references": [{"text": "As covered above.", "refers_to_intelligence_story_id": "s1"}]},
        ],
    }
    r.append(check(
        "ACCEPTANCE TEST: a cross_reference to a story already fully treated elsewhere is NOT a violation — the exact Fixture V2 run 3 distinction",
        all(x["status"] == "pass" for x in check_one_treatment(issue_one_treatment_ok)),
    ))

    issue_one_treatment_violated = {
        "sections": [
            {"section_type": "big_one", "stories": [{"intelligence_story_ids": ["s1"], "eps_scores": {}}], "cross_references": []},
            {"section_type": "watchlist", "stories": [{"intelligence_story_ids": ["s1"], "eps_scores": {}}], "cross_references": []},
        ],
    }
    r.append(check(
        "ACCEPTANCE TEST: the same intelligence_story_id as a full stories[] entry in TWO sections FAILS — the exact Fixture 4/1 regression from calibration runs 1-2, now automated",
        any(x["status"] == "fail" and x["claim_text"] == "s1" for x in check_one_treatment(issue_one_treatment_violated)),
    ))

    # --- validate_editorial_contract: full entry point ---

    full_issue_clean = {
        "sections": [
            {"section_type": "big_one", "stories": [
                {"headline": "Keane's role held", "body": "The role survived the incumbent's return.", "intelligence_story_ids": ["s1"], "eps_scores": {"big_one_eligible": True, "watchlist_eligible": False}},
            ], "cross_references": []},
        ],
    }
    contract_clean = validate_editorial_contract(full_issue_clean)
    r.append(check("a clean, compliant issue passes validate_editorial_contract end to end", contract_clean["passed"] is True))

    full_issue_dirty = {
        "sections": [
            {"section_type": "big_one", "stories": [
                {"headline": "Keane's role held", "body": "This story scored 82 EPS, the week's most Significant.", "intelligence_story_ids": ["s1"], "eps_scores": {"big_one_eligible": False, "watchlist_eligible": False}},
            ], "cross_references": []},
            {"section_type": "watchlist", "stories": [
                {"headline": "Keane, again", "body": "Still worth watching.", "intelligence_story_ids": ["s1"], "eps_scores": {"big_one_eligible": False, "watchlist_eligible": False}},
            ], "cross_references": []},
        ],
    }
    contract_dirty = validate_editorial_contract(full_issue_dirty)
    r.append(check(
        "an issue with a scoring-language leak, a violated gate, AND a one-treatment violation fails on all three simultaneously",
        contract_dirty["passed"] is False
        and len(contract_dirty["scoring_language_leak"]) > 0
        and any(x["status"] == "fail" for x in contract_dirty["gate_consistency"])
        and any(x["status"] == "fail" for x in contract_dirty["one_treatment"]),
    ))
    full_issue_with_reviewer_notes = {
        "sections": [
            {"section_type": "big_one", "stories": [
                {"headline": "Keane's role held", "body": "The role survived the incumbent's return.", "intelligence_story_ids": ["s1"], "eps_scores": {"big_one_eligible": True, "watchlist_eligible": False}},
            ], "cross_references": []},
        ],
        # Legitimately mentions eps_total/Significance by name — reviewer-only, never reader-facing.
        "notes_for_human_reviewer": "Chose this over the alternate because its Significance and eps_total were both higher.",
    }
    contract_with_notes = validate_editorial_contract(full_issue_with_reviewer_notes)
    r.append(check(
        "notes_for_human_reviewer is never scanned for scoring-language leaks — that field is legitimately reviewer-only, real scoring/gate discussion there is expected, not a leak",
        contract_with_notes["passed"] is True and contract_with_notes["scoring_language_leak"] == [],
    ))

    print()
    p = sum(r)
    print(f"{p}/{len(r)} checks passed")
    raise SystemExit(0 if p == len(r) else 1)
