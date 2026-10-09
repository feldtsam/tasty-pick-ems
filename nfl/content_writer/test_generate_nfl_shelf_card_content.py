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
from generate_nfl_shelf_card_content import (
    STORY_WORD_RANGE, draft_for_write, find_column_names, run_all_validators, run_all_warnings, story_word_count,
    strip_masked_role_fields, validate_no_column_names, validate_no_field_narration, validate_story_length,
)
from nfl_shelf_card_prompt import build_system_prompt
from nfl_shelf_card_writer_schema import validate_schema_shape
from nfl_tension import find_tension
from banned_language import PRICE_MOVEMENT_PREDICTION_PHRASES, STOCK_PHRASES, find_banned_phrases, find_stock_phrases
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

    # --- MASKED-HEAT FIX (2026-10-01): proven_heat/emerging_heat/
    # role_trend added to the same gate -- real Denzel Boston wk3 shape
    # (proven_heat real and low, emerging_heat/role_trend masked at 50.0).
    boston_masked_facts = {
        "player_name": "Denzel Boston", "posteam": "SEA", "position_group": "WR",
        "proven_heat": 6.2, "emerging_heat": 50.0, "role_trend": 50.0,
    }
    boston_stripped = strip_masked_role_fields(boston_masked_facts, dict(boston_masked_facts))
    r.append(check(
        "strip_masked_role_fields removes emerging_heat and role_trend when masked at 50.0 (real Boston wk3 shape)",
        "emerging_heat" not in boston_stripped and "role_trend" not in boston_stripped,
    ))
    r.append(check(
        "strip_masked_role_fields leaves the REAL, low proven_heat alone -- masking is per-field, not a blanket strip",
        boston_stripped.get("proven_heat") == 6.2,
    ))

    # A real (non-50.0) proven_heat/emerging_heat/role_trend trio must
    # never be stripped -- same regression shape as the role_momentum
    # check above, now for the three newly-gated fields.
    real_heat_facts = {"proven_heat": 61.0, "emerging_heat": 74.0, "role_trend": 68.0}
    real_heat_stripped = strip_masked_role_fields(real_heat_facts, dict(real_heat_facts))
    r.append(check(
        "strip_masked_role_fields leaves REAL (unmasked) proven_heat/emerging_heat/role_trend untouched",
        real_heat_stripped == real_heat_facts,
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

    # --- NFL-only malformed-why_reasons retry/recovery
    # (call_claude_for_nfl_shelf_card_with_retry) -- the real tool-
    # formatting glitch confirmed 2026-09-30: the API sometimes emits
    # more than one tool_use block for the same forced tool in one
    # response, and a later block is sometimes the real, well-formed
    # card the first one failed to be. Every scenario below mocks
    # gnscc.call_claude_with_tool directly (never a real network call)
    # and counts how many times it's invoked, to prove the "free first,
    # capped-at-one-retry second" shape, not just the happy path.
    MALFORMED_BLOCK = {
        "title": "A Title", "story": "x" * 80,
        "why_reasons": '\n<parameter name="pillar">role_momentum',
    }
    PLACEHOLDER_BLOCK = {"title": "placeholder", "story": "placeholder", "why_reasons": []}
    WELL_FORMED_BLOCK = {
        "title": "A Real Title", "story": "y" * 80,
        "why_reasons": [
            {"pillar": "role_momentum", "stars": 2, "reason_text": "real reason one", "source_fact_keys": ["a"]},
            {"pillar": "matchup", "stars": 4, "reason_text": "real reason two", "source_fact_keys": ["b"]},
        ],
    }
    r.append(check(
        "_is_well_formed_card: the real malformed XML-fragment shape fails",
        gnscc._is_well_formed_card(MALFORMED_BLOCK) is False,
    ))
    r.append(check(
        "_is_well_formed_card: a degenerate placeholder block (empty why_reasons, too-short story) also fails",
        gnscc._is_well_formed_card(PLACEHOLDER_BLOCK) is False,
    ))
    r.append(check(
        "_is_well_formed_card: a real, complete card passes",
        gnscc._is_well_formed_card(WELL_FORMED_BLOCK) is True,
    ))

    def _with_mocked_call(responses):
        """responses: a list of return values, one per call_claude_with_tool
        invocation (each itself a list of block `input` dicts, matching
        return_all_tool_use_blocks=True). Returns (call_count_box, restore_fn)."""
        orig = gnscc.call_claude_with_tool
        box = {"n": 0, "kwargs": []}

        def fake(api_key, system_prompt, user_prompt, tool_schema, max_tokens=SHARED_MAX_TOKENS, return_all_tool_use_blocks=False):
            box["kwargs"].append({"max_tokens": max_tokens, "return_all_tool_use_blocks": return_all_tool_use_blocks})
            out = responses[box["n"]]
            box["n"] += 1
            return out

        gnscc.call_claude_with_tool = fake
        return box, lambda: setattr(gnscc, "call_claude_with_tool", orig)

    # Scenario 1: single well-formed block, first try -- zero extra cost, no retry.
    box, restore = _with_mocked_call([[WELL_FORMED_BLOCK]])
    try:
        out, stats = gnscc.call_claude_for_nfl_shelf_card_with_retry("key", "sys", "user")
    finally:
        restore()
    r.append(check(
        "retry wrapper, single well-formed block: returns it, makes exactly one call, no retry",
        out == WELL_FORMED_BLOCK and box["n"] == 1
        and stats == {"blocks_in_first_response": 1, "recovered_from_same_response": False, "retry_fired": False, "retry_recovered": False},
    ))

    # Scenario 2: first block malformed, second block in the SAME response
    # is well-formed -- recovered for free, still exactly one call.
    box, restore = _with_mocked_call([[MALFORMED_BLOCK, WELL_FORMED_BLOCK]])
    try:
        out, stats = gnscc.call_claude_for_nfl_shelf_card_with_retry("key", "sys", "user")
    finally:
        restore()
    r.append(check(
        "retry wrapper, malformed-then-well-formed in ONE response: recovers the second block, zero extra API calls",
        out == WELL_FORMED_BLOCK and box["n"] == 1
        and stats["recovered_from_same_response"] is True and stats["retry_fired"] is False,
    ))

    # Scenario 3: no well-formed block anywhere in the first response
    # (malformed + a degenerate placeholder) -- the one capped retry
    # fires, and the retry's own response has a well-formed block.
    box, restore = _with_mocked_call([[MALFORMED_BLOCK, PLACEHOLDER_BLOCK], [WELL_FORMED_BLOCK]])
    try:
        out, stats = gnscc.call_claude_for_nfl_shelf_card_with_retry("key", "sys", "user")
    finally:
        restore()
    r.append(check(
        "retry wrapper, nothing usable in the first response: fires exactly one retry and recovers from it",
        out == WELL_FORMED_BLOCK and box["n"] == 2
        and stats["retry_fired"] is True and stats["retry_recovered"] is True,
    ))

    # Scenario 4: the retry ALSO comes back with nothing usable -- capped
    # at exactly one retry (never a second), falls back to the ORIGINAL
    # first response's first block, unchanged, for the existing
    # schema_shape/flagged-review path downstream to catch.
    box, restore = _with_mocked_call([[MALFORMED_BLOCK], [PLACEHOLDER_BLOCK]])
    try:
        out, stats = gnscc.call_claude_for_nfl_shelf_card_with_retry("key", "sys", "user")
    finally:
        restore()
    r.append(check(
        "retry wrapper, retry also fails: makes exactly two calls total (capped, no loop), returns original first block",
        out == MALFORMED_BLOCK and box["n"] == 2
        and stats["retry_fired"] is True and stats["retry_recovered"] is False,
    ))

    # Every call the retry wrapper makes asks for ALL blocks, not just
    # the first -- the whole mechanism depends on this.
    r.append(check(
        "every call the retry wrapper makes passes return_all_tool_use_blocks=True",
        all(kw["return_all_tool_use_blocks"] is True for kw in box["kwargs"]),
    ))

    # --- card_writer_common.call_claude_with_tool itself: the new
    # return_all_tool_use_blocks parameter is opt-in and MLB-safe --
    # with it OMITTED (every existing caller, including every MLB
    # writer), a multi-block response still yields the single first
    # block's input, exactly as before this change, not a list.
    import card_writer_common as cwc

    real_requests_post = cwc.requests.post

    class _FakeResp:
        status_code = 200

        def json(self):
            return {
                "stop_reason": "tool_use",
                "usage": {},
                "content": [
                    {"type": "tool_use", "name": "emit_nfl_shelf_card", "input": MALFORMED_BLOCK},
                    {"type": "tool_use", "name": "emit_nfl_shelf_card", "input": WELL_FORMED_BLOCK},
                ],
            }

    cwc.requests.post = lambda *a, **k: _FakeResp()
    try:
        default_result = cwc.call_claude_with_tool("key", "sys", "user", {"name": "emit_nfl_shelf_card"})
        all_result = cwc.call_claude_with_tool("key", "sys", "user", {"name": "emit_nfl_shelf_card"}, return_all_tool_use_blocks=True)
    finally:
        cwc.requests.post = real_requests_post
    r.append(check(
        "call_claude_with_tool default (omitted param, every existing MLB/NFL caller): still returns ONLY the first block's input, unchanged behavior",
        default_result == MALFORMED_BLOCK,
    ))
    r.append(check(
        "call_claude_with_tool(return_all_tool_use_blocks=True): returns every matching block's input, in order",
        all_result == [MALFORMED_BLOCK, WELL_FORMED_BLOCK],
    ))

    # ============================================================
    # STORY TIGHTENING (2026-10-06): structure instruction, warn-only
    # stock-phrase list, warn-only 70-100 word check. None of these
    # touch validate_no_field_narration or the no-numbers rule, and
    # none of them can change validation_passed.
    # ============================================================

    # --- prompt: the story-structure block ---
    for needle in (
        "STORY STRUCTURE", "at most 2 short paragraphs", "RECEIPT", "INTERPRETATION",
        "UNRESOLVED PIECE", "never restates it", "One central tension only",
        "At most one uncertainty statement",
    ):
        r.append(check(f"prompt carries the story-structure instruction: {needle!r}", needle in prompt))
    # Structure change (2026-10-06, after the 12-card dry run): the price is
    # optional context inside the unresolved piece, never a required close.
    r.append(check(
        "prompt no longer asks the story to end on a price sentence",
        "End on that price sentence" not in prompt and "PRICE RELEVANCE" not in prompt,
    ))
    r.append(check(
        "prompt makes the price optional context, one clause, never a required closing beat",
        "never a required closing beat" in prompt and "never the price itself as a number" in prompt,
    ))
    r.append(check(
        "prompt carries the add-something-new rule and tells the model to stop when nothing new remains",
        "must add a new fact, qualification, implication or uncertainty" in prompt
        and "When nothing new remains, stop, even if the story is under 70 words" in prompt,
    ))
    r.append(check(
        "prompt forbids a sentence whose purpose is to summarise, rename or re-emphasise the tension",
        "summarise, rename or re-emphasise a tension already established" in prompt,
    ))
    r.append(check(
        "prompt broadens the market guardrail to the claim itself: report a discrepancy, never assume it corrects",
        "never as something that will correct" in prompt and "No forecasts of market movement in any wording" in prompt
        and '"before the market adjusts"' in prompt and '"has to correct"' in prompt,
    ))
    r.append(check(
        "prompt bans scoring/column names in story and asks for plain football language",
        "never use scoring or column names in `story`" in prompt and "proven heat" in prompt
        and "role_momentum" in prompt and "plain football" in prompt.lower(),
    ))
    r.append(check(
        "prompt keeps a 100-word ceiling without a padded floor",
        "under 100 words" in prompt and "70-100 words" not in prompt,
    ))
    r.append(check(
        "prompt no longer suggests the \"we don't fully know why yet\" register for thin evidence",
        "we don't fully know why yet" not in prompt and "we don't fully know why yet" not in forming_prompt,
    ))
    r.append(check(
        "thin-evidence instruction now points the hedge at the UNRESOLVED PIECE slot, said once",
        "THINLY supported" in prompt and "once" in prompt.split("THINLY supported", 1)[1][:400],
    ))
    r.append(check(
        "prompt names every stock phrase as something to avoid",
        all(phrase in prompt.lower() for phrase in STOCK_PHRASES),
    ))
    r.append(check(
        "the no-numbers HARD RULE's own exact wording is STILL present after the story-structure addition",
        "Do not put ANY raw number, percentage, or score in `story` -- not even a rounded one." in prompt,
    ))
    r.append(check(
        "the old open-ended '1-2 short paragraphs' story instruction is gone from the static prompt",
        "Write 1-2 short paragraphs that translate the tension above" not in prompt,
    ))
    r.append(check(
        "forming cards keep the say-it-once instruction after the restructure",
        "state the sample-size limit ONCE" in forming_prompt and "stop repeating it" in forming_prompt,
    ))

    # --- stock phrases: WARN-ONLY, via find_stock_phrases, never find_banned_phrases ---
    STOCK_EXAMPLES = {
        "something is building here": "Something is building here, even if the box score hasn't noticed.",
        "we don't fully know yet": "We don't fully know yet why the targets moved his way.",
        "early, honest signal": "Call it an early, honest signal rather than a verdict.",
        "honest watch": "This is an honest watch, nothing more.",
        "that's the whole intrigue": "He keeps getting the ball near the goal line. That's the whole intrigue.",
        "that's the real tension": "The role says yes, the price says maybe. That's the real tension.",
        "that's the puzzle": "Why the market shrugs at that usage -- that's the puzzle.",
    }
    for phrase, text in STOCK_EXAMPLES.items():
        r.append(check(f"find_stock_phrases catches {phrase!r}", phrase in find_stock_phrases(text)))
        r.append(check(f"find_banned_phrases does NOT block {phrase!r} (warn-only)", find_banned_phrases(text) == []))
    r.append(check(
        "find_stock_phrases is case-insensitive and tolerates a curly apostrophe",
        find_stock_phrases("WE DON’T FULLY KNOW YET what to make of it.") == ["we don't fully know yet"],
    ))
    r.append(check(
        "find_stock_phrases also catches the 'why yet' variant the old prompt suggested",
        "we don't fully know yet" in find_stock_phrases("we don't fully know why yet, but he keeps showing up"),
    ))
    r.append(check(
        "a clean story has no stock phrases",
        find_stock_phrases(CLEAN_STORY) == [],
    ))
    r.append(check(
        "'the price hasn't caught up' stays on the BLOCKING price-movement list and is not duplicated on the stock list",
        "the price hasn't caught up" in PRICE_MOVEMENT_PREDICTION_PHRASES and "the price hasn't caught up" not in STOCK_PHRASES,
    ))

    # --- story length: WARN-ONLY, 70-100 words ---
    r.append(check("STORY_WORD_RANGE is 55-100 (softened floor, still warn-only)", STORY_WORD_RANGE == (55, 100)))
    r.append(check(
        "story_word_count counts hyphenated and apostrophe words as one word each",
        story_word_count("He's a goal-line back -- and that's it.") == 7,
    ))
    r.append(check("story_word_count of an empty/None story is 0", story_word_count("") == 0 and story_word_count(None) == 0))
    words = lambda n: " ".join(["word"] * n)
    r.append(check("validate_story_length: 54 words warns", validate_story_length(words(54)) != []))
    r.append(check("validate_story_length: 55 words is silent", validate_story_length(words(55)) == []))
    r.append(check("validate_story_length: 70 words is silent", validate_story_length(words(70)) == []))
    r.append(check("validate_story_length: 100 words is silent", validate_story_length(words(100)) == []))
    r.append(check("validate_story_length: 101 words warns", validate_story_length(words(101)) != []))
    short_warn = validate_story_length(words(40))
    r.append(check(
        "a length warning names the check, the real word count and the range",
        short_warn[0]["check"] == "story_length" and short_warn[0]["word_count"] == 40
        and short_warn[0]["min_words"] == 55 and short_warn[0]["max_words"] == 100,
    ))

    # --- column names in story: WARN-ONLY (real Moore dry-run leak: "proven heat metrics") ---
    r.append(check(
        "find_column_names catches the real Moore leak ('proven heat metrics')",
        find_column_names("with proven heat metrics backing up that he's finding real estate") == ["proven heat"],
    ))
    r.append(check(
        "find_column_names catches a bare snake_case field name and the spaced pillar names",
        set(find_column_names("His td_opportunity reading and role momentum both grade well, and the emerging heat agrees."))
        == {"td_opportunity", "role momentum", "emerging heat"},
    ))
    r.append(check(
        "find_column_names catches scoring jargon (completeness, percentile, pillar, market value score)",
        set(find_column_names("The market value score sits in a high percentile, though completeness on that pillar is low."))
        == {"market value score", "percentile", "completeness", "pillar"},
    ))
    r.append(check(
        "find_column_names flags 'situation' only as a score/read/pillar, not the plain English word",
        find_column_names("The situation read is middling.") == ["situation read"]
        and find_column_names("The situation in front of him this week is ordinary.") == [],
    ))
    r.append(check(
        "find_column_names leaves plain football language alone",
        find_column_names("He keeps getting goal-line work against a defense that gives up scores inside the ten, and the market has him as a long shot.") == [],
    ))
    r.append(check(
        "find_column_names is case-insensitive and returns [] for an empty/None story",
        find_column_names("Proven Heat says yes") == ["proven heat"] and find_column_names("") == [] and find_column_names(None) == [],
    ))
    col_warn = validate_no_column_names("His td_opportunity grade is high.")
    r.append(check(
        "validate_no_column_names returns one warning naming the check, the field and the terms",
        col_warn == [{"check": "column_name", "field": "story", "terms": ["td_opportunity"]}],
    ))
    r.append(check("validate_no_column_names is silent on a clean story", validate_no_column_names(CLEAN_STORY) == []))
    r.append(check(
        "a column name in story is a warning, never a validation issue",
        any(w["check"] == "column_name" for w in run_all_warnings({"title": "t", "story": words(60) + " proven heat.", "why_reasons": VALID_WHY_REASONS}))
        and [i for i in run_all_validators({"title": "t", "story": words(60) + " proven heat.", "why_reasons": VALID_WHY_REASONS}, SOURCE_FACTS) if i["check"] == "column_name"] == [],
    ))
    r.append(check(
        "column names in why_reasons are NOT warned -- citing fields is that layer's job",
        not any(w["check"] == "column_name" for w in run_all_warnings({
            "title": "t", "story": words(60),
            "why_reasons": [{"pillar": "td_opportunity", "stars": 3, "reason_text": "td_opportunity reads 57.", "source_fact_keys": ["td_opportunity"]}],
        })),
    ))

    # --- run_all_warnings vs run_all_validators: warnings live in their own list ---
    long_stock_story = ("Something is building here. " + words(115)).strip()
    warn_output = {"title": "The Market Won't Let Golden Drift", "story": long_stock_story, "why_reasons": VALID_WHY_REASONS}
    warnings = run_all_warnings(warn_output)
    r.append(check(
        "run_all_warnings returns a story_length warning and a stock_phrase warning for a long, stock-laden story",
        {w["check"] for w in warnings} == {"story_length", "stock_phrase"},
    ))
    r.append(check(
        "the stock_phrase warning names the field and the phrases found",
        any(w["check"] == "stock_phrase" and w["field"] == "story" and w["phrases"] == ["something is building here"] for w in warnings),
    ))
    r.append(check(
        "run_all_validators ignores length and stock phrases entirely -- validation_passed is unaffected",
        [i for i in run_all_validators(warn_output, SOURCE_FACTS) if i["check"] in ("story_length", "stock_phrase")] == []
        and run_all_validators(warn_output, SOURCE_FACTS) == run_all_validators(clean_output, SOURCE_FACTS),
    ))
    r.append(check(
        "run_all_warnings on a malformed output (no story string) returns [] instead of crashing",
        run_all_warnings({"title": "t", "story": None, "why_reasons": "garbage"}) == [],
    ))
    r.append(check(
        "a stock phrase in a why_reason is warned with the indexed field name",
        any(w.get("field") == "why_reasons[0].reason_text" for w in run_all_warnings({
            "title": "t", "story": CLEAN_STORY,
            "why_reasons": [{"pillar": "market_value", "stars": 4, "reason_text": "That's the puzzle.", "source_fact_keys": ["consensus_price_american"]}],
        })),
    ))

    # --- the full draft: warnings ride along, never flip the gate, never reach a write ---
    WARNED_BLOCK = {
        "title": "A Real Title",
        "story": long_stock_story,
        "why_reasons": [
            {"pillar": "market_value", "stars": 4, "reason_text": "Consensus price sits at +310.", "source_fact_keys": ["consensus_price_american"]},
            {"pillar": "td_opportunity", "stars": 3, "reason_text": "TD opportunity grades out at 57.", "source_fact_keys": ["td_opportunity"]},
        ],
    }
    box, restore = _with_mocked_call([[WARNED_BLOCK]])
    try:
        draft = gnscc.generate_nfl_shelf_card_draft(dict(CANDIDATE), "ATTD +300-499", "developing_angle", "key")
    finally:
        restore()
    r.append(check(
        "generate_nfl_shelf_card_draft returns validation_warnings as its own list",
        isinstance(draft.get("validation_warnings"), list) and {w["check"] for w in draft["validation_warnings"]} == {"story_length", "stock_phrase"},
    ))
    r.append(check(
        "warnings do not change validation_passed / review_status (still a pending_review draft)",
        draft["validation_passed"] is True and draft["review_status"] == "pending_review"
        and all(i["check"] not in ("story_length", "stock_phrase") for i in draft["validation_issues"]),
    ))
    written = draft_for_write(draft)
    r.append(check(
        "draft_for_write strips validation_warnings (nfl_content_drafts has no column for it) along with the underscore fields",
        "validation_warnings" not in written and not any(k.startswith("_") for k in written) and "validation_issues" in written,
    ))
    r.append(check(
        "a clean in-range story produces an empty validation_warnings list, not a missing key",
        run_all_warnings({"title": "t", "story": words(85), "why_reasons": VALID_WHY_REASONS}) == [],
    ))

    # --- Discovery headline vocabulary cleanup (2026-10-09) ---
    # The generic endorsement phrases live week-5 titles reproduced came
    # from TPE's own voice files and Tension strings. These checks pin
    # three things: (1) none of them reach the Picks shelf-card prompt
    # from any shelf, band, tension type, or imagery pool; (2) each is a
    # warn-only stock phrase, never a blocking one; (3) the detail-card /
    # newsletter / Intelligence / MLB files that legitimately use the
    # same words were left alone.
    from pathlib import Path as _Path
    from banned_language import DISCOVERY_STOCK_PHRASES
    from emotional_intensity import EMOTIONAL_INTENSITY
    from nfl_shelf_personalities import NFL_SHELF_PERSONALITIES
    from editorial_lenses import EDITORIAL_LENSES, resolve_editorial_lens

    removed = tuple(p.lower() for p in DISCOVERY_STOCK_PHRASES)

    def _found(text):
        return [p for p in removed if p in (text or "").lower()]

    def _prompt_minus_do_not_use_line(prompt):
        # The static prompt names STOCK_PHRASES in its own "Do not use
        # these stock phrases" line; that line is the one legitimate
        # place the removed phrases may appear.
        return "\n".join(l for l in prompt.splitlines() if not l.startswith("- Do not use these stock phrases"))

    tension_rows = {
        "divergence_market_high": CANDIDATE,
        "divergence_market_low": {**CANDIDATE, "market_value_score": 30.0, "td_opportunity": 75.0, "td_opportunity_completeness": 100.0,
                                  "role_momentum": 72.0, "situation": 68.0, "evidence_quality": 90.0},
        "contradiction": {**CANDIDATE, "market_value_score": None, "td_opportunity": 80.0, "td_opportunity_completeness": 100.0,
                          "role_momentum": 30.0, "situation": 55.0, "evidence_quality": 70.0},
        "change": {**CANDIDATE, "market_value_score": None, "td_opportunity": 52.0, "td_opportunity_completeness": 100.0,
                   "role_momentum": 48.0, "role_trend": 80.0, "situation": 52.0, "evidence_quality": 70.0},
        "forming": {**CANDIDATE, "market_value_score": None, "td_opportunity": 52.0, "td_opportunity_completeness": 100.0,
                    "role_momentum": 50.0, "role_momentum_completeness": 0.0, "situation": 52.0, "evidence_quality": 70.0},
        "uncertainty": {**CANDIDATE, "market_value_score": 60.0, "td_opportunity": 50.0, "td_opportunity_completeness": 100.0,
                        "role_momentum": 50.0, "situation": 50.0, "evidence_quality": 40.0},
        "convergence": {**CANDIDATE, "market_value_score": 62.0, "td_opportunity": 60.0, "td_opportunity_completeness": 100.0,
                        "role_momentum": 61.0, "situation": 63.0, "evidence_quality": 80.0},
    }
    tensions = {name: find_tension(row, None) for name, row in tension_rows.items()}
    r.append(check(
        "the seven tension fixtures cover six real tension types (divergence twice)",
        sorted({t["tension_type"] for t in tensions.values()}) == ["change", "contradiction", "convergence", "divergence", "forming", "uncertainty"],
    ))
    tension_leaks = {name: _found(" ".join(str(v) for v in t.values())) for name, t in tensions.items()}
    r.append(check(
        "no Tension Object string (claim, angle, counter-signal) carries a removed phrase or a lagging-price claim",
        all(not leak for leak in tension_leaks.values()) and not any(
            "won't stay where it is" in (t["story_angle"] or "") or "credit for" in (t["editorial_claim"] or "")
            for t in tensions.values()),
    ))
    pool_leaks = {shelf: _found(" ".join(p.imagery_pool) + " " + p.description + " " + " ".join(p.avoid))
                  for shelf, p in NFL_SHELF_PERSONALITIES.items()}
    r.append(check("no NFL shelf imagery pool, description, or avoid note carries a removed phrase", all(not v for v in pool_leaks.values())))
    intensity_leaks = {band: _found(i.assertiveness + " " + i.title_register + " " + " ".join(i.example_opening_frames))
                       for band, i in EMOTIONAL_INTENSITY.items()}
    r.append(check("no confidence-band intensity profile (assertiveness, register, example openers) carries a removed phrase",
                   all(not v for v in intensity_leaks.values())))
    assembled_leaks = []
    for shelf in EDITORIAL_LENSES:
        for band in EMOTIONAL_INTENSITY:
            for name, t in tensions.items():
                lens = resolve_editorial_lens(shelf, tension_rows[name])
                for tda in (None, False):
                    prompt = _prompt_minus_do_not_use_line(build_system_prompt(shelf, band, lens, t, trend_data_available=tda))
                    hit = _found(prompt)
                    if hit:
                        assembled_leaks.append((shelf, band, name, tda, hit))
    r.append(check(
        "the assembled Picks shelf-card prompt carries no removed phrase for any of 7 shelves x 4 bands x 7 tensions x 2 trend states",
        assembled_leaks == [],
    ))
    rule = ("A discovery headline must state a specific, evidence-supported observation about this player or matchup. "
            "Avoid generic endorsements, vague intrigue, and any phrase that could apply unchanged to an unrelated player. "
            "You may state the odds and a level fact. Do not claim the price is wrong, lagging, or overlooked.")
    r.append(check("the DISCOVERY HEADLINE editorial rule is in the static prompt, byte-for-byte",
                   rule in build_system_prompt("ATTD +500-699", "quiet_signal", lens, tensions["convergence"])))
    r.append(check("the prompt's own stock-phrase 'do not use' line now names the removed discovery phrases",
                   all(f'"{p}"' in build_system_prompt("ATTD +700+", "quiet_signal", lens, tensions["convergence"]) for p in DISCOVERY_STOCK_PHRASES)))
    for phrase in DISCOVERY_STOCK_PHRASES:
        title = f"Cole Kmet: A Price {phrase} Right Now"
        r.append(check(f"removed discovery phrase {phrase!r} is in STOCK_PHRASES and warns in a title",
                       phrase in STOCK_PHRASES and phrase in find_stock_phrases(title)))
        r.append(check(f"removed discovery phrase {phrase!r} does NOT block (warn-only, find_banned_phrases is silent)",
                       find_banned_phrases(title) == []))
    r.append(check(
        "the full 'the price hasn't caught up' form still BLOCKS and the bare form only warns",
        find_banned_phrases("the price hasn't caught up") == ["the price hasn't caught up"]
        and find_banned_phrases("A +700 price that hasn't caught up") == []
        and find_stock_phrases("A +700 price that hasn't caught up") == ["hasn't caught up"],
    ))
    r.append(check(
        "run_all_warnings surfaces a removed discovery phrase in a title as a warn-only stock_phrase finding",
        any(w.get("check") == "stock_phrase" and w.get("field") == "title" and "worth a second look" in w.get("phrases", [])
            for w in run_all_warnings({"title": "Ertz: A Price Worth a Second Look", "story": words(85), "why_reasons": VALID_WHY_REASONS})),
    ))
    _root = _Path(__file__).resolve().parent.parent
    left_alone = {
        _root.parent / "pipeline" / "api" / "content_writer" / "voice" / "emotional_intensity.py": "Worth a second look:",
        _root.parent / "pipeline" / "api" / "content_writer" / "voice" / "shelf_personalities.py": "worth a longer look",
        _root / "story_interrogation.py": "worth watching for",
        _root / "newsletter" / "evidence_validator.py": "hasn't caught up",
        _root / "market_intelligence.py": "worth another look",
        _root / "role_changes.py": "worth watching",
    }
    for path, phrase in left_alone.items():
        r.append(check(
            f"left alone: {path.relative_to(_root.parent)} still contains {phrase!r} (MLB / newsletter / Intelligence voice untouched)",
            path.exists() and phrase in path.read_text(),
        ))

    print()
    p = sum(r)
    print(f"{p}/{len(r)} checks passed")
    raise SystemExit(0 if p == len(r) else 1)
