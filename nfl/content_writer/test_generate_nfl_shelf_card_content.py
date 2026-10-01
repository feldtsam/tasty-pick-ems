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

import inspect

import generate_nfl_shelf_card_content as gnscc
import generate_tasty_six_content as gtsc
from generate_nfl_shelf_card_content import run_all_validators, strip_masked_role_fields, validate_no_field_narration
from nfl_shelf_card_prompt import build_system_prompt
from nfl_shelf_card_writer_schema import validate_schema_shape
from nfl_tension import find_tension
from banned_language import find_banned_phrases
from card_writer_common import MAX_TOKENS as SHARED_MAX_TOKENS, validate_numeric_grounding
from nfl_writer_common import nfl_tolerance_for_key, validate_pillar_field_consistency


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
    r.append(check(
        "the no-numbers HARD RULE's own exact wording is present, byte-for-byte",
        "Do not put ANY raw number, percentage, or score in `story` -- not even a rounded one." in prompt,
    ))

    # A strong-evidence tension renders the confident instruction instead.
    strong_candidate = dict(CANDIDATE, evidence_quality=90.0, td_opportunity_completeness=100.0)
    strong_tension = find_tension(strong_candidate, lens)
    strong_prompt = build_system_prompt("ATTD +300-499", "strong_setup", lens, strong_tension)
    r.append(check("strong-evidence tension tells the model to state it directly", "state it directly and" in strong_prompt))
    r.append(check("strong-evidence prompt does NOT contain the thin-evidence hedge language", "THINLY supported" not in strong_prompt))

    # --- the "forming"-only prose instruction (already on main) ---
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
        "a forming-type tension gets the sample-size/one-concrete-thing instruction",
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

    # --- strip_masked_role_fields (Fant's real masked-field card) ---
    fant_masked_facts = {
        "player_name": "Noah Fant", "posteam": "NO", "position_group": "TE",
        "td_opportunity": 56.1, "td_opportunity_completeness": 70.0,
        "role_momentum": 50.0, "role_momentum_completeness": 0.0,
        "touch_share_trend_pct": 50.0, "snap_share_trend_pct": 50.0, "touch_volume_trend_pct": 50.0,
    }
    fant_candidate_masked = dict(fant_masked_facts)
    stripped = strip_masked_role_fields(fant_masked_facts, fant_candidate_masked)
    r.append(check(
        "strip_masked_role_fields removes role_momentum when masked (completeness=0.0)",
        "role_momentum" not in stripped,
    ))
    r.append(check(
        "strip_masked_role_fields removes the three bare trend-gate fields sitting at the 50.0 fallback",
        "touch_share_trend_pct" not in stripped and "snap_share_trend_pct" not in stripped and "touch_volume_trend_pct" not in stripped,
    ))
    r.append(check(
        "strip_masked_role_fields leaves every real (non-masked) field untouched",
        stripped.get("td_opportunity") == 56.1 and stripped.get("td_opportunity_completeness") == 70.0,
    ))

    # A role_momentum reading with real completeness must NOT be stripped.
    real_role_facts = dict(fant_masked_facts, role_momentum=73.2, role_momentum_completeness=80.0,
                            touch_share_trend_pct=62.0, snap_share_trend_pct=58.0, touch_volume_trend_pct=55.0)
    real_role_candidate = dict(real_role_facts)
    stripped_real = strip_masked_role_fields(real_role_facts, real_role_candidate)
    r.append(check(
        "strip_masked_role_fields leaves a REAL (unmasked) role_momentum reading and its trend fields alone",
        stripped_real.get("role_momentum") == 73.2 and stripped_real.get("touch_share_trend_pct") == 62.0,
    ))

    # Prompt carries the masked-role-fields instruction, no-numbers rule still untouched.
    masked_role_prompt_lens = {"primary": "td_opportunity", "supporting": ("role_momentum",)}
    masked_role_tension = find_tension(dict(fant_masked_facts), masked_role_prompt_lens)
    masked_role_prompt = build_system_prompt("Red Zone Trends", "developing_angle", masked_role_prompt_lens, masked_role_tension)
    r.append(check(
        "prompt instructs: a role_momentum reason must say the real reading isn't available, not reconstruct a flat one",
        "state that the real reading isn't available yet" in masked_role_prompt,
    ))
    r.append(check(
        "the no-numbers HARD RULE's own exact wording is STILL present after this addition too",
        "Do not put ANY raw number, percentage, or score in `story` -- not even a rounded one." in masked_role_prompt,
    ))

    # --- tpe_score/market_value grounding instruction (the real 58.0 card) ---
    r.append(check(
        "prompt states tpe_score is the overall blended reading, never a pillar-specific one",
        "tpe_score, when it appears in the facts below, is the OVERALL blended reading" in prompt,
    ))
    r.append(check(
        "prompt forbids describing tpe_score as \"the market read\"",
        '"the market read,"' in prompt,
    ))
    r.append(check(
        "prompt requires a market_value reason to ground in consensus_price_american when market_value_score isn't present",
        "ground that reason in consensus_price_american instead" in prompt,
    ))
    r.append(check(
        "the no-numbers HARD RULE's own exact wording is STILL present after the tpe_score/market_value addition",
        "Do not put ANY raw number, percentage, or score in `story` -- not even a rounded one." in prompt,
    ))

    # --- games-played hotfix: no prompt sentence must ever ask the model to
    # cite a specific games-played figure; a new sentence bans stating one
    # at all, in any form, regardless of what's in the facts.
    r.append(check(
        "prompt bans stating a games-played count in any form (title, story, or why_reasons)",
        "Do not state how many games a player has played" in prompt,
    ))
    r.append(check(
        "the games-played ban names the real example phrasings seen in production",
        '"last three games"' in prompt,
    ))

    # New banned-phrase list: price-movement predictions, checked via the
    # same deterministic find_banned_phrases mechanism title/story already go through.
    price_movement_examples = [
        "He's outrunning his price at this point in the season.",
        "There's real room to move here once the market catches up.",
        "This is a case of the price hasn't caught up yet.",
    ]
    for text in price_movement_examples:
        found = find_banned_phrases(text)
        r.append(check(f"price-movement prediction phrase caught: {text!r}", len(found) >= 1))
    r.append(check(
        "a clean, non-predictive sentence about the gap is NOT flagged by the new list",
        find_banned_phrases("The opportunity here is real; the price hasn't fully reflected it yet.") == [],
    ))

    # --- yard-line exemption (validate_numeric_grounding) -- the 13 real
    # flagged texts from the 10- and 30-candidate dry runs, verbatim, each
    # paired with real-shaped source_facts a Red Zone Trends card would
    # actually have. All 13 must now ground clean.
    YARDLINE_SOURCE_FACTS = {
        "i10_touches_trail3": 1.0, "gl_touches_trail3": 1.0, "rz_tds_trail3": 1.0, "td_opportunity": 56.0,
    }
    REAL_YARDLINE_TEXTS = [
        "Three red-zone touchdowns over the last three games, built on 2 trips inside the 10 and 1 goal-line touch in that span.",
        "5 goal-line touches and 6 inside-the-10 touches over the last three games, with 3 red-zone touchdowns in that span.",
        "Three touches inside the 10 over the last three games, with one goal-line touch in that span -- real but modest red-zone work.",
        "Goal-line touches, inside-10 touches, and red-zone TDs over the last three games are all at zero -- no recent close-range work recorded.",
        "Red-zone opportunity grades well above average, with 6 inside-the-10 touches and 4 goal-line touches over the last three games, plus 2 red-zone touchdowns in that span.",
        "No goal-line touches, no inside-the-10 touches, and no red-zone touchdowns over the last three games -- the opportunity score of 56 is built on very little actual red-zone real estate so far.",
        "No inside-the-10 or goal-line touches and zero red-zone TDs recorded over the trail window (i10_touches_trail3: 0, gl_touches_trail3: 0, rz_tds_trail3: 0) -- there's simply no red-zone work logged yet to evaluate.",
        "Only 1 game played in the trailing sample, with 0 goal-line touches and 0 inside-the-10 touches recorded over that span -- too small to call a trend either way.",
        "Just 1 touch inside the 10 and 0 goal-line touches over the available sample, with no red-zone TDs logged yet.",
        "Only 1 game played so far -- 4 touches inside the 10 and 2 red-zone touchdowns in that span, but it's a single-game sample, not yet a trend.",
        "Only 1 game played so far (trail3_games_played: 1), with 1 touch inside the 10 and 1 goal-line touch in that sample -- too small to read as a trend yet.",
        "Only 1 game played so far this season, with 1 target inside the 10-yard line and 1 red-zone touchdown in that span -- too small a window to call a trend.",
        "Only 1 game played in the trailing window, with 0 goal-line touches and 0 inside-the-10 touches recorded so far -- too small a sample to read as a trend yet.",
    ]
    for text in REAL_YARDLINE_TEXTS:
        issues = validate_numeric_grounding(
            [{"reason_text": text, "source_fact_keys": ["i10_touches_trail3"]}],
            YARDLINE_SOURCE_FACTS, nfl_tolerance_for_key,
        )
        # These 13 texts come from 13 different real rows with their own
        # real (and different) touch/TD counts -- this one shared fixture
        # can't ground every number in every text, and isn't trying to.
        # What this checks is narrower and precise: the yard-line number
        # itself (10, 20, or 50) must never be the one flagged -- any
        # OTHER real number in the text failing to ground against this
        # shared fixture is an artifact of reusing one fixture across 13
        # different real rows, not a yard-line regression.
        yardline_flagged = any(
            i["issue"].split()[1] in ("10.0", "20.0", "50.0") for i in issues
        )
        r.append(check(f"yard-line number itself never flagged: {text[:55]!r}...", not yardline_flagged))

    # A real, unrelated ungrounded number in the SAME sentence as a yard-
    # line reference must still fail -- the exemption must not swallow a
    # genuinely fabricated statistic just because "the 10" sits nearby.
    mixed_issues = validate_numeric_grounding(
        [{"reason_text": "He had 12 touches inside the 10 this week, a real surge.", "source_fact_keys": ["i10_touches_trail3"]}],
        YARDLINE_SOURCE_FACTS, nfl_tolerance_for_key,
    )
    r.append(check(
        "a genuinely ungrounded number (12) right next to an exempted yard-line reference (10) still fails",
        len(mixed_issues) == 1 and "12" in mixed_issues[0]["issue"],
    ))

    # Plain ungrounded numbers, no yard-line phrasing at all, must still fail.
    plain_bad_issues = validate_numeric_grounding(
        [{"reason_text": "His overall opportunity score checks in at 91.4 this week.", "source_fact_keys": ["td_opportunity"]}],
        YARDLINE_SOURCE_FACTS, nfl_tolerance_for_key,
    )
    r.append(check(
        "a plain ungrounded number with no yard-line phrasing still fails",
        len(plain_bad_issues) == 1,
    ))

    # Negative controls: "the 10"/"the 50" WITHOUT a preposition, or with a
    # disqualifying suffix, must never be exempted -- these are real counts/
    # percentages/rankings, not yard lines.
    negative_controls = [
        "He is the 10-game sample leader in the league this year.",
        "This pick is one of the 50 best picks on the board tonight.",
        "The conversion rate allowed is at the 50% mark for this defense.",
    ]
    for text in negative_controls:
        issues = validate_numeric_grounding(
            [{"reason_text": text, "source_fact_keys": []}], YARDLINE_SOURCE_FACTS, nfl_tolerance_for_key,
        )
        r.append(check(f"negative control still flags an ungrounded number: {text!r}", len(issues) >= 1))

    # --- games-played hotfix: trail3_games_played and the three trail3
    # sums must no longer be citable under ANY pillar -- removed entirely
    # from source_facts upstream (generate_nfl_shelf_card_draft), and from
    # the pillar-consistency groups so a stray citation is still caught as
    # a real mismatch rather than silently waved through.
    reason_citing_removed_fields = [{
        "pillar": "td_opportunity", "stars": 3, "reason_text": "x",
        "source_fact_keys": ["trail3_games_played", "i10_touches_trail3", "gl_touches_trail3", "rz_tds_trail3"],
    }]
    r.append(check(
        "a why_reason citing ONLY the removed trail3 fields under td_opportunity is now a real mismatch (nothing backs the tag)",
        len(validate_pillar_field_consistency(reason_citing_removed_fields)) == 1,
    ))

    # --- NFL-only max_tokens: NFL's shelf-card writer gets its own
    # higher override; the SHARED default (every other writer, MLB
    # included, still gets it) is untouched.
    r.append(check(
        "the shared card_writer_common.MAX_TOKENS default is still 1024, untouched by the NFL override",
        SHARED_MAX_TOKENS == 1024,
    ))
    r.append(check(
        "call_claude_for_nfl_shelf_card's own default is the named NFL_SHELF_CARD_MAX_TOKENS constant (2048)",
        gnscc.NFL_SHELF_CARD_MAX_TOKENS == 2048
        and inspect.signature(gnscc.call_claude_for_nfl_shelf_card).parameters["max_tokens"].default == 2048,
    ))

    # call_claude_for_nfl_shelf_card can be overridden to a real custom
    # value (proving the new parameter actually reaches call_claude_
    # with_tool) without mutating the shared or NFL default constants.
    captured = {}
    orig_call = gnscc.call_claude_with_tool

    def fake_call_claude_with_tool(api_key, system_prompt, user_prompt, tool_schema, max_tokens=SHARED_MAX_TOKENS):
        captured["max_tokens"] = max_tokens
        return {"title": "t", "story": "s", "why_reasons": []}

    gnscc.call_claude_with_tool = fake_call_claude_with_tool
    try:
        gnscc.call_claude_for_nfl_shelf_card("key", "sys", "user", max_tokens=3072)
    finally:
        gnscc.call_claude_with_tool = orig_call
    r.append(check(
        "call_claude_for_nfl_shelf_card(max_tokens=3072) really reaches the shared call with 3072, not the 2048 default",
        captured.get("max_tokens") == 3072,
    ))
    r.append(check(
        "overriding one call's max_tokens does not mutate NFL_SHELF_CARD_MAX_TOKENS or the shared default",
        gnscc.NFL_SHELF_CARD_MAX_TOKENS == 2048 and SHARED_MAX_TOKENS == 1024,
    ))

    # MLB (and NFL's OWN Tasty Six writer, the closest in-repo stand-in
    # for "every other writer") never overrides max_tokens at all --
    # confirmed by inspecting the real wrapper's own call, not assumed.
    tasty_six_source = inspect.getsource(gtsc.call_claude_for_nfl_tasty_six_card)
    r.append(check(
        "NFL's own Tasty Six writer (closest real in-repo parallel to MLB's shared-default callers) "
        "does NOT pass max_tokens at all -- still rides the shared 1024 default, unaffected by this change",
        "max_tokens" not in tasty_six_source,
    ))

    print()
    p = sum(r)
    print(f"{p}/{len(r)} checks passed")
    raise SystemExit(0 if p == len(r) else 1)
