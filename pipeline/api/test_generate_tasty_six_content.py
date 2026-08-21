"""
Tests generate_tasty_six_content.py's prompt construction and validation
orchestration against real candidate shapes and adversarial model
outputs — everything except the actual Claude API call itself, which
needs ANTHROPIC_API_KEY (Vercel-only in this environment; the real,
live-model test happens through the deployed endpoint, not here).

Uses the same real Jeremy Pena / real shelf_candidates_detailed shape
pulled from production earlier tonight (see the conversation) —
hand-copied here so this test doesn't depend on network access or a
still-fresh curate-shelves response.

Run: python3 pipeline/api/test_generate_tasty_six_content.py
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
from content_writer.tasty_six_prompt import build_system_prompt, build_user_prompt  # noqa: E402
from generate_tasty_six_content import draft_for_write, run_all_validators  # noqa: E402

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
    "shelf": "+500-699",
    "rank": 1,
    "is_tasty_six": True,
    "shelf_score": 72.1,
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

    # --- Prompt construction against real data ---
    facts = flatten_source_facts(
        REAL_CANDIDATE, TOP_LEVEL_CITABLE_FIELDS, ("pillar_detail",), RECENT_FORM_CITABLE_FIELDS, RECENT_FORM_SKIP_FIELDS,
    )
    system_prompt = build_system_prompt("+500-699", "strong_setup")
    user_prompt = build_user_prompt(facts)

    results.append(check(
        "system prompt includes the real shelf's personality description",
        "The sweet spot" not in system_prompt and "bigger swings, bigger payoffs" in system_prompt.lower(),
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
        "user prompt contains the real flattened source facts as JSON",
        "power_production" in user_prompt and "67.8" in user_prompt and "550" in user_prompt,
    ))
    results.append(check(
        "build_system_prompt fails loud on an unrecognized shelf/band rather than a generic fallback",
        True,
    ))
    try:
        build_system_prompt("Not A Real Shelf", "strong_setup")
        results[-1] = check("build_system_prompt raises KeyError for an unrecognized shelf", False)
    except KeyError:
        pass

    # --- Validator orchestration: a genuinely clean output ---
    clean_output = {
        "title": "The Setup Is Loud",
        "editorial_sentence": "Every real number here points the same direction.",
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
    results.append(check("a genuinely clean output produces zero combined validation issues", clean_issues == []))

    # --- Adversarial: banned guarantee language in the title ---
    banned_output = {
        "title": "This Is A Real Lock Tonight",
        "editorial_sentence": "Every real number here points the same direction.",
        "why_reasons": clean_output["why_reasons"],
    }
    banned_issues = run_all_validators(banned_output, facts, REAL_CANDIDATE)
    results.append(check(
        "banned guarantee language in the title is caught by the combined validator, labeled by check type",
        any(i["check"] == "banned_language" and i["field"] == "title" and "lock" in i["phrases"] for i in banned_issues),
    ))

    # --- Adversarial: fabricated citation key ---
    bad_citation_output = {
        "title": "The Setup Is Loud",
        "editorial_sentence": "Every real number here points the same direction.",
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
    malformed_output = {"title": "", "editorial_sentence": "x", "why_reasons": []}
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

    print(f"\n{sum(results)}/{len(results)} checks passed")
    raise SystemExit(0 if all(results) else 1)
