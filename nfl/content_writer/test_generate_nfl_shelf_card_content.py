"""
NFL Content Generation V1, Part 1 — test_generate_nfl_shelf_card_content.py.

Tests the regular shelf card writer's building blocks, focused on the
Editorial Voice Spec's "Find the Tension" addition (`story`, the tension
prompt block, and the new field-narration validator) — same scope
discipline as nfl/content_writer/test_nfl_tasty_six_writer.py: hand-
constructed "model output" shapes, no real Claude API call.

Run: python3 nfl/content_writer/test_generate_nfl_shelf_card_content.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "voice"))

from generate_nfl_shelf_card_content import run_all_validators, validate_no_field_narration
from nfl_shelf_card_prompt import build_system_prompt
from nfl_shelf_card_writer_schema import validate_schema_shape
from nfl_tension import find_tension
from nfl_writer_common import validate_pillar_field_consistency


def check(label, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    return bool(cond)


CANDIDATE = {
    "player_name": "Matthew Golden", "posteam": "GB", "position_group": "WR",
    "consensus_price_american": 310,
    "market_value_score": 72.9, "market_value_completeness": 100.0,
    "td_opportunity": 57.1, "td_opportunity_completeness": 30.0,
    "role_momentum": 50.0, "role_momentum_completeness": 100.0,
    "situation": 50.0, "situation_completeness": 100.0,
    "evidence_quality": 45.0, "tpe_score": 60.0,
}

SOURCE_FACTS = {k: v for k, v in CANDIDATE.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}

CLEAN_STORY = (
    "The market keeps respecting Golden even though his usage hasn't really moved. "
    "There isn't a dramatic role surge behind that price -- and that's actually what makes this one interesting."
)

FIELD_NARRATING_STORY = (
    "Market value scored 72.9, with TD opportunity grading out at 57.1 on 30% completeness."
)

VALID_WHY_REASONS = [
    {"pillar": "market_value", "stars": 4, "reason_text": "Consensus price sits at +310.", "source_fact_keys": ["consensus_price_american"]},
    {"pillar": "td_opportunity", "stars": 3, "reason_text": "TD opportunity grades out at 57.", "source_fact_keys": ["td_opportunity"]},
]


if __name__ == "__main__":
    r = []

    # --- validate_no_field_narration ---
    r.append(check("clean story has no field-narration issues", validate_no_field_narration(CLEAN_STORY, SOURCE_FACTS) == []))
    issues = validate_no_field_narration(FIELD_NARRATING_STORY, SOURCE_FACTS)
    r.append(check("field-narrating story is caught", len(issues) >= 2))
    r.append(check("caught issue names the real field", any(i["field"] == "market_value_score" for i in issues)))
    r.append(check("caught issue names the real field (td_opportunity too)", any(i["field"] == "td_opportunity" for i in issues)))
    r.append(check("a rounded narration ('73' for 72.9) is also caught", len(validate_no_field_narration("Scored a 73 today.", SOURCE_FACTS)) >= 1))
    r.append(check("small values (position-group counts, star ratings) are never flagged",
                    validate_no_field_narration("He's their No. 2 option.", {"depth_rank": 2}) == []))

    # --- validate_schema_shape now requires story ---
    r.append(check("schema shape fails with no story field at all",
                    any("story" in e for e in validate_schema_shape({"title": "T", "why_reasons": VALID_WHY_REASONS}))))
    r.append(check("schema shape fails with an empty story",
                    any("story" in e for e in validate_schema_shape({"title": "T", "story": "  ", "why_reasons": VALID_WHY_REASONS}))))
    r.append(check("schema shape passes with a real, in-range story",
                    validate_schema_shape({"title": "T", "story": CLEAN_STORY, "why_reasons": VALID_WHY_REASONS}) == []))

    # --- build_system_prompt renders the real Tension Object ---
    lens = {"primary": "market_value", "supporting": ("td_opportunity", "evidence_quality")}
    tension = find_tension(CANDIDATE, lens)
    prompt = build_system_prompt("ATTD +300-499", "developing_angle", lens, tension)
    r.append(check("prompt includes the FIND THE TENSION block header", "FIND THE TENSION" in prompt))
    r.append(check("prompt states the real detected tension type", f"Type: {tension['tension_type']}" in prompt))
    r.append(check("prompt carries the real editorial_claim through", tension["editorial_claim"] in prompt))
    r.append(check("thin-evidence tension tells the model to hedge, not hide it", "THINLY supported" in prompt))
    r.append(check("prompt states the field-narration hard rule explicitly", "field aloud in prose" in prompt))
    r.append(check("prompt forbids raw numbers in story", "raw number" in prompt.lower()))
    r.append(check("prompt still carries the no-manufactured-drama guardrail", "manufacture drama" in prompt))

    # --- Stage 1G: the new trail3_games_played sentence, added without touching the no-numbers rule above ---
    r.append(check(
        "prompt tells the model to check trail3_games_played before calling a thin change a trend/climbing",
        "trail3_games_played" in prompt and "climbing" in prompt,
    ))
    r.append(check(
        "the no-numbers HARD RULE's own exact wording is still present, byte-for-byte, after the new sentence was added",
        "Do not put ANY raw number, percentage, or score in `story` -- not even a rounded one." in prompt,
    ))

    # A strong-evidence tension renders the confident instruction instead.
    strong_candidate = dict(CANDIDATE, evidence_quality=90.0, td_opportunity_completeness=100.0)
    strong_tension = find_tension(strong_candidate, lens)
    strong_prompt = build_system_prompt("ATTD +300-499", "strong_setup", lens, strong_tension)
    r.append(check("strong-evidence tension tells the model to state it directly", "state it directly and" in strong_prompt))
    r.append(check("strong-evidence prompt does NOT contain the thin-evidence hedge language", "THINLY supported" not in strong_prompt))

    # --- Stage 1 approval item 3: the "forming"-only prose instruction ---
    forming_candidate = dict(
        CANDIDATE,
        market_value_score=50.0, td_opportunity=50.0, td_opportunity_completeness=100.0,
        situation=50.0, situation_completeness=100.0,
        role_momentum=50.0, role_momentum_completeness=0.0,
        role_trend=None, proven_heat=None, emerging_heat=None,
    )
    forming_tension = find_tension(forming_candidate, lens)
    forming_prompt = build_system_prompt("Red Zone Trends", "developing_angle", lens, forming_tension)
    r.append(check(
        "a forming-type tension gets the new sample-size/one-concrete-thing instruction",
        forming_tension["tension_type"] == "forming" and "state the sample-size limit ONCE" in forming_prompt,
    ))
    r.append(check(
        "the forming instruction tells the model to stop repeating the sample-size caveat",
        "stop repeating it" in forming_prompt,
    ))
    r.append(check(
        "a non-forming tension (the real Golden divergence case) does NOT get the forming instruction",
        tension["tension_type"] != "forming" and "state the sample-size limit ONCE" not in prompt,
    ))

    # --- run_all_validators end-to-end (hand-built model output, no real API call) ---
    clean_output = {"title": "The Market Won't Let Golden Drift", "story": CLEAN_STORY, "why_reasons": VALID_WHY_REASONS}
    clean_issues = run_all_validators(clean_output, SOURCE_FACTS)
    field_narration_issues = [i for i in clean_issues if i["check"] == "field_narration"]
    r.append(check("a clean, real output produces no field_narration issues", field_narration_issues == []))

    bad_output = {"title": "The Market Won't Let Golden Drift", "story": FIELD_NARRATING_STORY, "why_reasons": VALID_WHY_REASONS}
    bad_issues = run_all_validators(bad_output, SOURCE_FACTS)
    r.append(check("a field-narrating story is caught by the full validator suite",
                    any(i["check"] == "field_narration" for i in bad_issues)))

    # --- Stage 1 approval item 2: trail3_games_played's pillar mapping ---
    # Before this fix, NFL_PILLAR_FIELD_GROUPS["td_opportunity"] had no
    # entry for trail3_games_played at all (it was added to editorial_
    # lenses.SIGNAL_TO_CITABLE_FIELDS, a SEPARATE constant, and missed
    # here) -- a why_reason correctly tagged td_opportunity but citing
    # ONLY trail3_games_played was a false-positive mismatch.
    td_opp_reason_citing_only_sample_size = [{
        "pillar": "td_opportunity", "stars": 3, "reason_text": "x",
        "source_fact_keys": ["trail3_games_played"],
    }]
    r.append(check(
        "a why_reason tagged td_opportunity citing only trail3_games_played is NOT a false pillar mismatch",
        validate_pillar_field_consistency(td_opp_reason_citing_only_sample_size) == [],
    ))
    role_momentum_reason_citing_trail3_games_played = [{
        "pillar": "role_momentum", "stars": 3, "reason_text": "x",
        "source_fact_keys": ["trail3_games_played"],
    }]
    r.append(check(
        "a why_reason tagged role_momentum citing trail3_games_played IS still correctly flagged (it's a td_opportunity field)",
        len(validate_pillar_field_consistency(role_momentum_reason_citing_trail3_games_played)) == 1,
    ))

    print()
    p = sum(r)
    print(f"{p}/{len(r)} checks passed")
    raise SystemExit(0 if p == len(r) else 1)
