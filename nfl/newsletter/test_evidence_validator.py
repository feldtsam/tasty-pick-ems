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
    check_relationship_traceability,
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

    print()
    p = sum(r)
    print(f"{p}/{len(r)} checks passed")
    raise SystemExit(0 if p == len(r) else 1)
