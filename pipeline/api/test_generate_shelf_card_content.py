"""
Tests generate_shelf_card_content.py's schema, prompt construction, and
validation orchestration against real candidate shapes and adversarial
model outputs — everything except the actual Claude API call itself
(needs ANTHROPIC_API_KEY; the real, live-model test happens through the
deployed endpoint).

Deliberately does NOT re-test validate_citations/validate_numeric_
grounding/validate_star_consistency in depth — those are shared,
already-tested code in card_writer_common.py (covered by
test_tasty_six_writer_schema.py, which now tests card_writer_common
directly). This file covers what's actually NEW for this writer type:
shelf_card_writer_schema.py's own validate_schema_shape() (no
editorial_sentence field), SHELF_CARD_TOOL_SCHEMA's shape, the prompt
text, and run_all_validators' combination without editorial_sentence.

Uses the same real Jeremy Pena / real shelf_candidates_detailed shape as
test_generate_tasty_six_content.py for consistency.

Run: python3 pipeline/api/test_generate_shelf_card_content.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "content_writer"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "content_writer" / "voice"))

from card_writer_common import (  # noqa: E402
    RECENT_FORM_CITABLE_FIELDS,
    RECENT_FORM_SKIP_FIELDS,
    TOP_LEVEL_CITABLE_FIELDS,
    flatten_source_facts,
)
from content_writer.shelf_card_prompt import build_system_prompt, build_user_prompt  # noqa: E402
from content_writer.shelf_card_writer_schema import SHELF_CARD_TOOL_SCHEMA, validate_schema_shape  # noqa: E402
from generate_shelf_card_content import draft_for_write, run_all_validators  # noqa: E402

REAL_CANDIDATE = {
    "candidate": {
        "player_name": "Jeremy Peña", "mlbam_id": 665161, "team": "HOU",
        "opp_pitcher_name": "Shane Bieber", "opp_pitcher_mlbam_id": 669456,
        "game_pk": "824160", "home_team": "HOU", "away_team": "TOR",
        "venue_name": "Daikin Park", "odds": 550, "batting_order_slot": 1,
        "skill_score": 65.8, "matchup_score": 89, "environment_score": 58.5,
        "opportunity_score": 80, "final_score": 72.1, "star_rating": 5,
        "score_tier": "Elite", "temp_f": 73, "wind_speed_mph": 0,
        "wind_description": "None", "roof_status": "closed",
        "pillar_detail": {
            "skill": {"score": 65.8, "components": {"contact_quality": 48.2, "power_production": 67.8, "track_record": 87.4}},
            "matchup": {"score": 89, "components": {"contact_allowed": 90.8, "rate_outcome": 90.7, "platoon_adjustment": -1.76, "platoon_note": "same-handed (R vs R)"}},
            "environment": {"score": 58.5, "components": {"park": 61, "park_factor_hr": 108.3, "temp": 59.4, "weather": 54.7, "wind": 50}},
            "opportunity": {"score": 80, "components": {"batting_order": 100, "bullpen": 50}},
        },
        "notes": ["No opposing bullpen metrics provided — using neutral (50)."],
    },
    "shelf": "Hot Hitters",
    "rank": 1,
    "is_tasty_six": False,
    "shelf_score": 1.284,
    "recent_form": {
        "recent_games_sampled": 15, "recent_home_runs": 7,
        "recent_hr_per_pa": 0.10294117647058823, "recent_ops": 1.2840073529411764,
        "recent_plate_appearances": 68,
        "recent_window_dates": {"first": "2026-07-17", "last": "2026-08-03"},
    },
}


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


if __name__ == "__main__":
    results = []

    # --- Tool schema shape: title + why_reasons only, no editorial_sentence ---
    results.append(check(
        "SHELF_CARD_TOOL_SCHEMA has no editorial_sentence property at all",
        "editorial_sentence" not in SHELF_CARD_TOOL_SCHEMA["input_schema"]["properties"],
    ))
    results.append(check(
        "SHELF_CARD_TOOL_SCHEMA requires exactly title and why_reasons",
        set(SHELF_CARD_TOOL_SCHEMA["input_schema"]["required"]) == {"title", "why_reasons"},
    ))
    results.append(check(
        "SHELF_CARD_TOOL_SCHEMA names the real, distinct emit_shelf_card tool",
        SHELF_CARD_TOOL_SCHEMA["name"] == "emit_shelf_card",
    ))

    # --- validate_schema_shape: no editorial_sentence check ---
    results.append(check(
        "a real card with only title + why_reasons (no editorial_sentence) passes shape validation",
        validate_schema_shape({
            "title": "Real Title",
            "why_reasons": [
                {"pillar": "skill", "stars": 3, "reason_text": "x", "source_fact_keys": ["skill_score"]},
                {"pillar": "matchup", "stars": 3, "reason_text": "y", "source_fact_keys": ["matchup_score"]},
            ],
        }) == [],
    ))
    results.append(check(
        "missing title is still caught (shape validation isn't a no-op)",
        any("title" in e for e in validate_schema_shape({"why_reasons": [
            {"pillar": "skill", "stars": 3, "reason_text": "x", "source_fact_keys": ["skill_score"]},
            {"pillar": "matchup", "stars": 3, "reason_text": "y", "source_fact_keys": ["matchup_score"]},
        ]})),
    ))

    # --- Prompt construction against real data ---
    facts = flatten_source_facts(
        REAL_CANDIDATE, TOP_LEVEL_CITABLE_FIELDS, ("pillar_detail",), RECENT_FORM_CITABLE_FIELDS, RECENT_FORM_SKIP_FIELDS,
    )
    system_prompt = build_system_prompt("Hot Hitters", "strong_setup")
    user_prompt = build_user_prompt(facts)

    results.append(check(
        "system prompt includes the real shelf's personality description",
        "can't cool off" in system_prompt.lower() or "seeing it well" in system_prompt.lower(),
    ))
    results.append(check(
        "system prompt includes the real confidence band's assertiveness guidance",
        "confident and declarative" in system_prompt.lower(),
    ))
    results.append(check(
        "system prompt explicitly bans real guarantee/betting-slang phrases",
        "guaranteed" in system_prompt.lower() and "wager" in system_prompt.lower(),
    ))
    results.append(check(
        "system prompt explicitly states there is no separate editorial sentence for this writer type",
        "no separate editorial sentence" in system_prompt.lower(),
    ))
    results.append(check(
        "user prompt references the real emit_shelf_card tool, not emit_tasty_six_card",
        "emit_shelf_card" in user_prompt and "emit_tasty_six_card" not in user_prompt,
    ))
    try:
        build_system_prompt("Not A Real Shelf", "strong_setup")
        results.append(check("build_system_prompt raises KeyError for an unrecognized shelf", False))
    except KeyError:
        results.append(check("build_system_prompt raises KeyError for an unrecognized shelf", True))

    # --- Validator orchestration: a genuinely clean output, no editorial_sentence field at all ---
    clean_output = {
        "title": "Pena's Bat Is Loud Right Now",
        "why_reasons": [
            {
                "pillar": "matchup", "stars": 5,
                "reason_text": "A real 89 matchup grade against a same-handed arm he sees well.",
                "source_fact_keys": ["pillar_detail.matchup.score"],
            },
            {
                "pillar": "opportunity", "stars": 5,
                "reason_text": "Leadoff spot means max plate appearances, a real 100 batting-order grade.",
                "source_fact_keys": ["pillar_detail.opportunity.components.batting_order"],
            },
        ],
    }
    clean_issues = run_all_validators(clean_output, facts, REAL_CANDIDATE)
    results.append(check("a genuinely clean output (no editorial_sentence field) produces zero combined validation issues", clean_issues == []))

    # --- Adversarial: banned guarantee language in the title ---
    banned_output = {
        "title": "This Is A Real Lock Tonight",
        "why_reasons": clean_output["why_reasons"],
    }
    banned_issues = run_all_validators(banned_output, facts, REAL_CANDIDATE)
    results.append(check(
        "banned guarantee language in the title is caught by the combined validator, labeled by check type",
        any(i["check"] == "banned_language" and i["field"] == "title" and "lock" in i["phrases"] for i in banned_issues),
    ))

    # --- Adversarial: fabricated citation key ---
    bad_citation_output = {
        "title": "Pena's Bat Is Loud Right Now",
        "why_reasons": [
            {
                "pillar": "skill", "stars": 4,
                "reason_text": "Elite barrel rate backs this up.",
                "source_fact_keys": ["pillar_detail.skill.components.barrel_rate"],
            },
            clean_output["why_reasons"][1],
        ],
    }
    citation_issues = run_all_validators(bad_citation_output, facts, REAL_CANDIDATE)
    results.append(check(
        "a fabricated citation key is caught by the combined validator, labeled by check type",
        any(i["check"] == "citation" and "barrel_rate" in i["issue"] for i in citation_issues),
    ))

    # --- Adversarial: malformed schema shape short-circuits the rest ---
    malformed_output = {"title": "", "why_reasons": []}
    shape_issues = run_all_validators(malformed_output, facts, REAL_CANDIDATE)
    results.append(check(
        "a malformed schema shape is caught and short-circuits before citation/numeric checks run",
        len(shape_issues) > 0 and all(i["check"] == "schema_shape" for i in shape_issues),
    ))

    # --- draft_for_write strips the local-only raw model output ---
    fake_draft = {"mlbam_id": 1, "title": "x", "_raw_model_output": {"huge": "blob"}}
    write_payload = draft_for_write(fake_draft)
    results.append(check(
        "draft_for_write strips _raw_model_output before forwarding to Lovable",
        "_raw_model_output" not in write_payload and write_payload["mlbam_id"] == 1,
    ))
    results.append(check(
        "draft_for_write payload never contains an editorial_sentence key for this writer type",
        "editorial_sentence" not in write_payload,
    ))

    print(f"\n{sum(results)}/{len(results)} checks passed")
    raise SystemExit(0 if all(results) else 1)
