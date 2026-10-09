"""
Item 3 (2026-10) -- test_factual_validation.py.

The trend-claim rule, the factual/stylistic split, the fallback copy, and
the prompt-side prevention. The 20-title fixture is the REAL week-5 2026
Red Zone Trends shelf as published (get_published_nfl_shelf_picks,
2026-10-09): every one of those 20 rows had both trend fields masked
(why_this_hits = "Touch-share and snap-share trend data isn't reliable
yet ...") and every role-signal delta was 0, so the evidence for all 20
is the masked state. 7 LLM titles asserted a trend; 12 deterministic
titles and 1 LLM title did not.

Run: python3 nfl/content_writer/test_factual_validation.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "voice"))

from factual_validation import (  # noqa: E402
    FACTUAL_CHECKS, FALLBACK_TITLE_GENERIC, FALLBACK_TITLE_MASKED, FALLBACK_TITLE_NO_CHANGE, STYLISTIC_CHECKS,
    TREND_UNAVAILABLE_PROMPT_LINE, WARNING_CHECKS, categorize_issue, find_trend_claims, safe_fallback_title,
    split_issues, strip_trend_phrases, trend_evidence_for_row, validate_trend_claims,
)
from nfl_shelf_card_prompt import build_dynamic_system_prompt  # noqa: E402
from nfl_shelf_personalities import NFL_SHELF_PERSONALITIES, personality_for_shelf  # noqa: E402
import generate_nfl_shelf_card_content as gnscc  # noqa: E402
import generate_tasty_six_content as gtsc  # noqa: E402


def check(label, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    return bool(cond)


# Real week-5 2026 Red Zone Trends shelf, as published. (player, title).
LIVE_WEEK5_RED_ZONE = [
    ("Noah Fant", "Noah Fant's Red-Zone Looks Are Climbing Faster Than the Matchup Suggests"),
    ("Darius Cooper", "Darius Cooper: Real Red-Zone Reps, Rough Matchup"),
    ("Wan'Dale Robinson", "Wan'Dale Robinson's Red-Zone Reps Are Climbing Faster Than the Matchup Suggests"),
    ("Josh Cameron", "Josh Cameron's Red Zone Reps Are Climbing Past His Price"),
    ("Austin Hooper", "Hooper's Red-Zone Touches Keep Climbing, But the Matchup Isn't Helping"),
    ("Matthew Hibner", "Hibner's Red-Zone Share Is Outrunning His Price"),
    ("Chris Moore", "Chris Moore's Scoring Chances Are Climbing Faster Than His Price"),
    ("Jahdae Walker", "Walker's Red-Zone Touches Are Climbing Past His Price"),
    ("Foster Moreau", "The early role hasn't shown enough yet to call a direction."),
    ("Sean Tucker", "The early role hasn't shown enough yet to call a direction."),
    ("Pat Bryant", "The early role hasn't shown enough yet to call a direction."),
    ("Cooper Kupp", "The early role hasn't shown enough yet to call a direction."),
    ("Tory Horton", "The early role hasn't shown enough yet to call a direction."),
    ("Nate Adkins", "The early role hasn't shown enough yet to call a direction."),
    ("Devin Singletary", "The early role hasn't shown enough yet to call a direction."),
    ("Evan Engram", "The early role hasn't shown enough yet to call a direction."),
    ("Malachi Fields", "The early role hasn't shown enough yet to call a direction."),
    ("Treylon Burks", "The early role hasn't shown enough yet to call a direction."),
    ("Blake Whiteheart", "The early role hasn't shown enough yet to call a direction."),
    ("Dallas Goedert", "The early role hasn't shown enough yet to call a direction."),
]
EXPECTED_FLAGGED = {"Noah Fant", "Wan'Dale Robinson", "Josh Cameron", "Austin Hooper", "Matthew Hibner", "Chris Moore", "Jahdae Walker"}

# Row shapes. The masked row is exactly the live week-5 state (both
# percentile-trend fields at the neutral-50 sentinel, rolling deltas 0).
MASKED_ROW = {
    "touch_share_trend_pct": 50.0, "snap_share_trend_pct": 50.0, "touch_volume_trend_pct": 50.0,
    "rz_touch_share_last3": 0.20, "rz_touch_share_season_avg": 0.20,
    "rz_touches_last3": 2.0, "rz_touches_season_avg": 2.0,
    "snap_share_last3": 0.55, "snap_share_season_avg": 0.55,
}
UP_ROW = {**MASKED_ROW, "touch_share_trend_pct": 78.0, "rz_touch_share_last3": 0.31, "rz_touch_share_season_avg": 0.20}
FLAT_ROW = {**MASKED_ROW, "touch_share_trend_pct": 64.0}  # unmasked read, but the raw delta is 0
DOWN_ROW = {**MASKED_ROW, "touch_share_trend_pct": 22.0, "rz_touch_share_last3": 0.12, "rz_touch_share_season_avg": 0.20}

MINIMAL_TENSION = {
    "tension_type": "forming", "primary_signal": "role_momentum has not cleared enough games yet",
    "counter_signal": None, "editorial_claim": "Not enough games yet to call a direction.",
    "story_angle": "Still-forming evidence.", "uncertainty": "The role read here is still forming.",
}
LENS = {"primary": "td_opportunity", "supporting": ("situation", "evidence_quality")}


if __name__ == "__main__":
    r = []

    # ------------------------------------------------------------
    # Lexicon: verbs/adjectives only, nouns never.
    # ------------------------------------------------------------
    r.append(check("'climbing' is an up-claim", find_trend_claims("His looks are climbing") == [{"phrase": "climbing", "direction": "up"}]))
    r.append(check("'Keep Climbing' / 'Outrunning' / 'surging' / 'growing' / 'rising' are up-claims",
                   all(c["direction"] == "up" for c in find_trend_claims("Keep Climbing, Outrunning, surging, growing, rising"))
                   and len(find_trend_claims("Keep Climbing, Outrunning, surging, growing, rising")) == 5))
    r.append(check("'fading' / 'declining' / 'trending down' are down-claims",
                   [c["direction"] for c in find_trend_claims("fading, declining, trending down")] == ["down", "down", "down"]))
    r.append(check("bare 'trending' is a neutral (direction-free) claim",
                   find_trend_claims("the usage is trending his way") == [{"phrase": "trending", "direction": "neutral"}]))
    r.append(check("the shelf name 'Red Zone Trends' does NOT trigger the rule (noun forms are never matched)",
                   find_trend_claims("Red Zone Trends: the matchup is the story") == []
                   and find_trend_claims("A trend worth a look, per the trends shelf") == []))
    r.append(check("'Faster' / 'more' / 'better' alone are not trend words", find_trend_claims("faster, more, better, bigger role") == []))
    r.append(check("non-string / empty input yields no claims", find_trend_claims(None) == [] and find_trend_claims("") == []))

    # ------------------------------------------------------------
    # Evidence: masked vs no_change vs up/down are different facts.
    # ------------------------------------------------------------
    r.append(check("masked row -> status 'masked', no unmasked field", trend_evidence_for_row(MASKED_ROW, "Red Zone Trends")["status"] == "masked"))
    r.append(check("unmasked row with a positive raw delta -> status 'up' and the field is listed as positive",
                   trend_evidence_for_row(UP_ROW, "Red Zone Trends")["status"] == "up"
                   and "touch_share_trend_pct" in trend_evidence_for_row(UP_ROW, "Red Zone Trends")["positive"]))
    r.append(check("unmasked row whose raw delta is 0 -> status 'no_change' (NOT masked)", trend_evidence_for_row(FLAT_ROW, "Red Zone Trends")["status"] == "no_change"))
    r.append(check("unmasked row with a negative raw delta -> status 'down'", trend_evidence_for_row(DOWN_ROW, "Red Zone Trends")["status"] == "down"))
    r.append(check("a row with no raw columns falls back to the percentile read (78 -> up)",
                   trend_evidence_for_row({"touch_share_trend_pct": 78.0, "snap_share_trend_pct": 50.0, "touch_volume_trend_pct": 50.0}, "Red Zone Trends")["status"] == "up"))
    r.append(check("RB Trends reads the _role fields, not the Red Zone ones",
                   set(trend_evidence_for_row({}, "RB Trends")["fields"]) == {"touch_share_trend_pct_role", "snap_share_trend_pct_role"}))
    r.append(check("WR/TE Trends add target_share_trend (masked only when absent)",
                   "target_share_trend" in trend_evidence_for_row({}, "WR Trends")["fields"]
                   and trend_evidence_for_row({"target_share_trend": 0.04}, "TE Trends")["status"] == "up"))

    # ------------------------------------------------------------
    # The rule itself, on the exact cases the brief names.
    # ------------------------------------------------------------
    title = "Noah Fant's Red-Zone Looks Are Climbing Faster Than the Matchup Suggests"
    masked_issue = validate_trend_claims({"title": title}, trend_evidence_for_row(MASKED_ROW, "Red Zone Trends"))
    r.append(check("'climbing' with masked trend data FAILS, reason 'masked'",
                   len(masked_issue) == 1 and masked_issue[0]["reason"] == "masked" and masked_issue[0]["category"] == "factual"))
    r.append(check("the same title with an unmasked field and a positive delta PASSES",
                   validate_trend_claims({"title": title}, trend_evidence_for_row(UP_ROW, "Red Zone Trends")) == []))
    flat_issue = validate_trend_claims({"title": title}, trend_evidence_for_row(FLAT_ROW, "Red Zone Trends"))
    r.append(check("the same title with an unmasked field and delta 0 FAILS, reason 'no_change' (never confused with masked)",
                   len(flat_issue) == 1 and flat_issue[0]["reason"] == "no_change"))
    down_issue = validate_trend_claims({"title": title}, trend_evidence_for_row(DOWN_ROW, "Red Zone Trends"))
    r.append(check("the same title with a negative delta FAILS for 'climbing', reason 'wrong_direction'",
                   len(down_issue) == 1 and down_issue[0]["reason"] == "wrong_direction"))
    r.append(check("a down-claim ('fading') PASSES on the negative-delta row and FAILS on the up row",
                   validate_trend_claims({"title": "His role is fading"}, trend_evidence_for_row(DOWN_ROW, "Red Zone Trends")) == []
                   and validate_trend_claims({"title": "His role is fading"}, trend_evidence_for_row(UP_ROW, "Red Zone Trends"))[0]["reason"] == "wrong_direction"))
    r.append(check("a neutral claim ('trending') needs only SOME non-zero delta: passes on up and down rows, fails on masked and flat",
                   validate_trend_claims({"title": "the usage is trending his way"}, trend_evidence_for_row(UP_ROW, "Red Zone Trends")) == []
                   and validate_trend_claims({"title": "the usage is trending his way"}, trend_evidence_for_row(DOWN_ROW, "Red Zone Trends")) == []
                   and validate_trend_claims({"title": "the usage is trending his way"}, trend_evidence_for_row(MASKED_ROW, "Red Zone Trends")) != []
                   and validate_trend_claims({"title": "the usage is trending his way"}, trend_evidence_for_row(FLAT_ROW, "Red Zone Trends")) != []))
    r.append(check("the rule checks every text field it is given and names the field",
                   [i["field"] for i in validate_trend_claims({"title": "flat title", "story": "touches are climbing"}, trend_evidence_for_row(MASKED_ROW, "Red Zone Trends"))] == ["story"]))
    r.append(check("a title with the shelf name but no trend verb passes on a masked row",
                   validate_trend_claims({"title": "Red Zone Trends: Real Red-Zone Reps, Rough Matchup"}, trend_evidence_for_row(MASKED_ROW, "Red Zone Trends")) == []))

    # ------------------------------------------------------------
    # The 20 live week-5 titles, every row masked.
    # ------------------------------------------------------------
    masked_ev = trend_evidence_for_row(MASKED_ROW, "Red Zone Trends")
    flagged = {p for p, t in LIVE_WEEK5_RED_ZONE if validate_trend_claims({"title": t}, masked_ev)}
    passed = {p for p, t in LIVE_WEEK5_RED_ZONE if not validate_trend_claims({"title": t}, masked_ev)}
    print(f"       live week-5 Red Zone: flagged {len(flagged)} = {sorted(flagged)}")
    print(f"       live week-5 Red Zone: passed  {len(passed)}")
    r.append(check("the 20 live week-5 Red Zone titles: exactly the 7 named LLM titles are flagged", flagged == EXPECTED_FLAGGED))
    r.append(check("the 20 live week-5 Red Zone titles: the other 13 pass (12 deterministic + Darius Cooper's level claim)",
                   len(passed) == 13 and "Darius Cooper" in passed))

    # ------------------------------------------------------------
    # Fallback copy passes the same rule, and says the right thing.
    # ------------------------------------------------------------
    r.append(check("masked evidence -> the 'cannot say' fallback title",
                   safe_fallback_title(trend_evidence_for_row(MASKED_ROW, "Red Zone Trends")) == FALLBACK_TITLE_MASKED))
    r.append(check("no-change evidence -> the 'no change' fallback title (not the masked one)",
                   safe_fallback_title(trend_evidence_for_row(FLAT_ROW, "Red Zone Trends")) == FALLBACK_TITLE_NO_CHANGE))
    r.append(check("every fallback title passes the trend rule under every evidence state",
                   all(validate_trend_claims({"title": t}, trend_evidence_for_row(row, "Red Zone Trends")) == []
                       for t in (FALLBACK_TITLE_MASKED, FALLBACK_TITLE_NO_CHANGE, FALLBACK_TITLE_GENERIC)
                       for row in (MASKED_ROW, UP_ROW, FLAT_ROW, DOWN_ROW))))
    from shelves import red_zone_story  # noqa: E402  (deterministic template, checked against the same rule)
    import pandas as pd  # noqa: E402
    det_masked = red_zone_story(pd.Series({**MASKED_ROW, "proven_heat": 20.0, "emerging_heat": 50.0, "i10_touches_trail3": 0.0, "gl_touches_trail3": 0.0, "rz_tds_trail3": 0.0}))
    det_flat = red_zone_story(pd.Series({**FLAT_ROW, "snap_share_trend_pct": 61.0, "proven_heat": 20.0, "emerging_heat": 50.0, "i10_touches_trail3": 0.0, "gl_touches_trail3": 0.0, "rz_tds_trail3": 0.0}))
    det_up = red_zone_story(pd.Series({**UP_ROW, "snap_share_trend_pct": 61.0, "proven_heat": 20.0, "emerging_heat": 50.0, "i10_touches_trail3": 0.0, "gl_touches_trail3": 0.0, "rz_tds_trail3": 0.0}))
    r.append(check("deterministic Red Zone template: masked row -> 'cannot say' headline, passes the rule",
                   det_masked["headline"] == FALLBACK_TITLE_MASKED and validate_trend_claims({"title": det_masked["headline"]}, masked_ev) == []))
    r.append(check("deterministic Red Zone template: unmasked but delta 0 -> 'holding steady' headline (no trend word), passes the rule",
                   "holding steady" in det_flat["headline"] and validate_trend_claims({"title": det_flat["headline"]}, trend_evidence_for_row(FLAT_ROW, "Red Zone Trends")) == []))
    r.append(check("deterministic Red Zone template: unmasked with a positive delta -> the real 'climbing' headline, and it passes the rule",
                   "climbing" in det_up["headline"] and validate_trend_claims({"title": det_up["headline"]}, trend_evidence_for_row(UP_ROW, "Red Zone Trends")) == []))

    # ------------------------------------------------------------
    # Factual vs stylistic split.
    # ------------------------------------------------------------
    r.append(check("existing factual checks are in FACTUAL_CHECKS",
                   {"citation", "numeric_grounding", "star_consistency", "pillar_field_consistency", "schema_shape", "trend_claim"} <= FACTUAL_CHECKS))
    r.append(check("banned_language and field_narration are STYLISTIC (flag + overridable, never the hard gate)",
                   STYLISTIC_CHECKS == {"banned_language", "field_narration"} and not (STYLISTIC_CHECKS & FACTUAL_CHECKS)))
    r.append(check("story_length / column_name / stock_phrase are warnings", WARNING_CHECKS == {"story_length", "column_name", "stock_phrase"}))
    r.append(check("categorize_issue tags each check with its category and preserves an explicit one",
                   categorize_issue({"check": "citation"})["category"] == "factual"
                   and categorize_issue({"check": "banned_language"})["category"] == "stylistic"
                   and categorize_issue({"check": "mystery_check"})["category"] == "unknown"
                   and categorize_issue({"check": "banned_language", "category": "factual"})["category"] == "factual"))
    s = split_issues([{"check": "citation"}, {"check": "banned_language"}, {"check": "stock_phrase"}, {"check": "mystery"}])
    r.append(check("split_issues: factual (incl. unknown -> fail closed) / stylistic / other",
                   [i["check"] for i in s["factual"]] == ["citation", "mystery"] and [i["check"] for i in s["stylistic"]] == ["banned_language"]
                   and [i["check"] for i in s["other"]] == ["stock_phrase"]))
    r.append(check("the shelf-card writer's run_all_validators now tags every issue with a category",
                   all("category" in i for i in gnscc.run_all_validators({"title": "lock it in", "story": "x" * 70, "why_reasons": []}, {}))))
    r.append(check("a banned-language-only failure is stylistic: no factual issue, so it stays an overridable flag",
                   split_issues(gnscc.run_all_validators(
                       {"title": "A lock this week", "story": "The role is steady and the matchup is soft, which keeps him in play without a loud claim either way here.",
                        "why_reasons": [
                            {"pillar": "market_value", "stars": 3, "reason_text": "Consensus price sits at +310.", "source_fact_keys": ["consensus_price_american"]},
                            {"pillar": "td_opportunity", "stars": 3, "reason_text": "TD opportunity grades out at 57.", "source_fact_keys": ["td_opportunity"]},
                        ]},
                       {"consensus_price_american": 310, "market_value_score": 60.0, "td_opportunity": 57.1},
                   ))["factual"] == []))
    r.append(check("the Tasty Six writer's draft issues go through the same categorizer (import wired)",
                   gtsc.categorize_issues is not None))

    # ------------------------------------------------------------
    # Prompt-side prevention.
    # ------------------------------------------------------------
    pool = personality_for_shelf("Red Zone Trends").imagery_pool
    stripped = strip_trend_phrases(pool)
    r.append(check("the Red Zone voice pool loses its trend phrases when trend data is unavailable",
                   "the opportunities are climbing" in pool and "the touches are trending the right way" in pool
                   and "the opportunities are climbing" not in stripped and "the touches are trending the right way" not in stripped
                   and "goal-line work" in stripped))
    r.append(check("every shelf pool has at least one phrase left after stripping (the model is never handed an empty pool)",
                   all(len(strip_trend_phrases(p.imagery_pool)) >= 1 for p in NFL_SHELF_PERSONALITIES.values())))
    unavailable = build_dynamic_system_prompt("Red Zone Trends", "developing_angle", LENS, MINIMAL_TENSION, trend_data_available=False)
    available = build_dynamic_system_prompt("Red Zone Trends", "developing_angle", LENS, MINIMAL_TENSION, trend_data_available=True)
    default = build_dynamic_system_prompt("Red Zone Trends", "developing_angle", LENS, MINIMAL_TENSION)
    r.append(check("trend_data_available=False: the prompt carries the one prevention line and no trend phrase from the pool",
                   TREND_UNAVAILABLE_PROMPT_LINE in unavailable and "the opportunities are climbing" not in unavailable))
    r.append(check("trend_data_available=True: no prevention line, pool intact",
                   TREND_UNAVAILABLE_PROMPT_LINE not in available and "the opportunities are climbing" in available))
    r.append(check("default (None): byte-identical to the available prompt -- existing callers are unchanged", default == available))

    # ------------------------------------------------------------
    # draft_for_write never ships factual_validation_passed (no column).
    # ------------------------------------------------------------
    r.append(check("factual_validation_passed is local-only and stripped before a write",
                   "factual_validation_passed" not in gnscc.draft_for_write({"title": "t", "factual_validation_passed": False, "validation_issues": []})))

    print()
    p = sum(r)
    print(f"{p}/{len(r)} checks passed")
    raise SystemExit(0 if p == len(r) else 1)
