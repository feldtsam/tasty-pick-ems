"""
Tests card_writer_common.py's citation, numeric-grounding, and
star-consistency validators (shared across every writer type) plus
tasty_six_writer_schema.py's own validate_schema_shape(), against a REAL
scored candidate shape (Corbin Carroll, PIT@AZ, 2026-07-28 — the same
real pool this session has used throughout) and deliberately adversarial
why_reasons that violate each check exactly one way at a time, so a
failure here points at a specific, explainable cause rather than a vague
"something's wrong."

Run: python3 pipeline/api/test_tasty_six_writer_schema.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "content_writer"))

from card_writer_common import (
    PILLAR_NAMES,
    RECENT_FORM_CITABLE_FIELDS,
    RECENT_FORM_SKIP_FIELDS,
    STAR_PILLAR_SCORE_KEYS,
    TOP_LEVEL_CITABLE_FIELDS,
    flatten_source_facts,
    mlb_tolerance_for_key,
    validate_citations,
    validate_numeric_grounding,
    validate_star_consistency,
)
from tasty_six_writer_schema import validate_schema_shape

REAL_CANDIDATE = {
    "candidate": {
        "player_name": "Corbin Carroll",
        "mlbam_id": 682998,
        "team": "AZ",
        "batting_order_slot": 1,
        "opp_pitcher_name": "Bubba Chandler",
        "opp_pitcher_mlbam_id": 696149,
        "game_pk": "823350",
        "home_team": "PIT",
        "away_team": "AZ",
        "matchup": "AZ @ PIT",
        "venue_name": "PNC Park",
        "odds": 600,
        "bookmaker": "BetRivers",
        "num_bookmakers": 1,
        "match_type": "exact",
        "skill_score": 70.0,
        "matchup_score": 37.4,
        "environment_score": 28.2,
        "opportunity_score": 80.0,
        "final_score": 54.0,
        "star_rating": 3,
        "score_tier": "Moderate",
        "passes_odds_filter": True,
        "temp_f": 68.8,
        "wind_speed_mph": 9.4,
        "wind_description": "blowing out to right",
        "roof_status": "open",
        "pillar_detail": {
            "skill": {"score": 70.0, "components": {"contact_quality": 61.1, "power_production": 81.1, "track_record": 64.6}},
            "matchup": {"score": 37.4, "components": {"contact_allowed": 28.5, "rate_outcome": 46.4, "platoon_adjustment": 1.76, "platoon_note": "opposite-handed (L vs R)"}},
            "environment": {"score": 28.2, "components": {"park": 0.0, "weather": 70.6, "wind": 72.5, "temp": 68.8, "park_factor_hr": 62.2}},
            "opportunity": {"score": 80.0, "components": {"batting_order": 100.0, "bullpen": 50.0}},
        },
        "notes": [
            "No opposing bullpen metrics provided — using neutral (50). KNOWN GAP: bullpen quality IS computable with the same play-by-play data source used elsewhere.",
        ],
    },
    "shelf": "+500-699",
    "rank": 2,
    "is_tasty_six": True,
    "shelf_score": 54.0,
    # Real recent-form shapes from earlier live_data/recent_form.py test
    # runs this session (Mike Trout's real batter form, Kirby's real
    # pitcher form) — both attached here for test convenience; a real
    # shelf entry only ever carries one or the other depending on whether
    # it came from Hot Hitters or Cold Pitchers to Attack.
    "recent_form": {
        "recent_games_sampled": 15,
        "recent_plate_appearances": 63,
        "recent_ops": 0.742979242979243,
        "recent_hr_per_pa": 0.0317,
        "recent_home_runs": 2,
        "recent_window_dates": {"first": "2026-07-17", "last": "2026-08-02"},
    },
    "opposing_pitcher_recent_form": {
        "recent_starts_sampled": 5,
        "recent_innings_pitched": 31.0,
        "recent_era": 3.193548387096774,
        "recent_hr_per_9": 1.7419354838709677,
        "recent_k_per_9": 7.548387096774194,
        "recent_bb_per_9": 0.8709677419354839,
        "recent_window_dates": {"first": "2026-06-29", "last": "2026-08-02"},
    },
}


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


if __name__ == "__main__":
    results = []
    facts = flatten_source_facts(
        REAL_CANDIDATE, TOP_LEVEL_CITABLE_FIELDS, ("pillar_detail",), RECENT_FORM_CITABLE_FIELDS, RECENT_FORM_SKIP_FIELDS,
    )

    results.append(check(
        "flatten_source_facts pulls the real top-level fields",
        facts["player_name"] == "Corbin Carroll" and facts["final_score"] == 54.0,
    ))
    results.append(check(
        "flatten_source_facts produces real dotted pillar_detail paths",
        facts["pillar_detail.skill.components.power_production"] == 81.1
        and facts["pillar_detail.environment.components.wind"] == 72.5,
    ))
    results.append(check(
        "internal bookkeeping fields are NOT citable",
        "mlbam_id" not in facts and "game_pk" not in facts and "bookmaker" not in facts,
    ))
    results.append(check(
        "recent_form/opposing_pitcher_recent_form are flattened into dotted per-field keys, not one opaque dict",
        facts.get("recent_form.recent_ops") == 0.742979242979243
        and facts.get("opposing_pitcher_recent_form.recent_era") == 3.193548387096774
        and "recent_form" not in facts  # the old opaque-dict key must NOT also exist
        and "recent_form.recent_window_dates" not in facts,  # deliberately skipped — dates, not a numeric/citable fact
    ))

    # --- A genuinely clean, well-grounded card: real citations, real
    # numbers, star ratings consistent with real pillar scores. ---
    clean_reasons = [
        {
            "pillar": "skill",
            "stars": 4,
            "reason_text": "His power grade sits at 81.1, real top-of-the-scale raw juice.",
            "source_fact_keys": ["pillar_detail.skill.components.power_production"],
        },
        {
            "pillar": "opportunity",
            "stars": 5,
            "reason_text": "Locked into the leadoff spot with a 100.0 batting-order grade — max plate appearances guaranteed by lineup slot alone.",
            "source_fact_keys": ["pillar_detail.opportunity.components.batting_order"],
        },
        {
            "pillar": "environment",
            "stars": 2,
            "reason_text": "Real wind reading of 9.4 mph blowing out helps, but the park factor here is modest.",
            "source_fact_keys": ["wind_speed_mph", "pillar_detail.environment.components.park_factor_hr"],
        },
    ]
    results.append(check("clean card: zero citation violations", validate_citations(clean_reasons, facts) == []))
    results.append(check("clean card: zero numeric-grounding violations", validate_numeric_grounding(clean_reasons, facts, mlb_tolerance_for_key) == []))
    results.append(check("clean card: zero star-consistency violations", validate_star_consistency(clean_reasons, facts, PILLAR_NAMES, STAR_PILLAR_SCORE_KEYS) == []))
    results.append(check("clean card: passes schema-shape validation", validate_schema_shape({
        "title": "Real Title", "editorial_sentence": "Real sentence.", "why_reasons": clean_reasons,
    }) == []))

    # --- Adversarial case 1: a fabricated citation key (barrel_rate was
    # never a real component this candidate has). ---
    fabricated_key_reasons = [{
        "pillar": "skill", "stars": 4,
        "reason_text": "Elite barrel rate backs this up.",
        "source_fact_keys": ["pillar_detail.skill.components.barrel_rate"],
    }]
    citation_violations = validate_citations(fabricated_key_reasons, facts)
    results.append(check(
        "fabricated citation key is caught by name",
        len(citation_violations) == 1 and "barrel_rate" in citation_violations[0]["issue"],
    ))

    # --- Adversarial case 2: a real, valid citation, but the prose states
    # a number that doesn't match the real value (81.1 -> claims 95). ---
    distorted_number_reasons = [{
        "pillar": "skill", "stars": 4,
        "reason_text": "His power grade is a massive 95.0, elite raw juice.",
        "source_fact_keys": ["pillar_detail.skill.components.power_production"],
    }]
    numeric_violations = validate_numeric_grounding(distorted_number_reasons, facts, mlb_tolerance_for_key)
    results.append(check(
        "a distorted number on a validly-cited key is still caught",
        len(numeric_violations) == 1 and "95.0" in numeric_violations[0]["issue"],
    ))

    # --- False-positive guard: an ordinal number in prose must NOT trip
    # numeric grounding (same discipline as banned_language's word-boundary
    # precision). ---
    ordinal_reasons = [{
        "pillar": "skill", "stars": 3,
        "reason_text": "This would be his 3rd home run of the week if it hits.",
        "source_fact_keys": ["skill_score"],
    }]
    results.append(check(
        "an ordinal number ('3rd') does not false-positive numeric grounding",
        validate_numeric_grounding(ordinal_reasons, facts, mlb_tolerance_for_key) == [],
    ))

    # --- Real bug found and fixed during NFL Content Generation Part C's
    # own validation (2026-08-21), confirmed NOT NFL-specific: a MULTI-
    # DIGIT ordinal ("89th") was silently mis-extracted as a bare, WRONG
    # number ("8") by the old lookahead-based _NUMBER_PATTERN — a real
    # regex backtracking trap that single-digit ordinals like "3rd" above
    # happened to never trigger (see _NUMBER_PATTERN's own docstring in
    # card_writer_common.py for the full mechanism). "89" is deliberately
    # far from every real value in this fixture (skill=70.0, matchup=37.4,
    # environment=28.2, opportunity=80.0, final_score=54.0) specifically
    # so this test distinguishes the fix from coincidental grounding: the
    # OLD buggy pattern would have extracted "8" and flagged it as a real
    # violation (8 is outside the 1-5 small-integer exemption); the FIXED
    # pattern excludes "89th" entirely as an ordinal, so there's nothing
    # to check at all. ---
    multi_digit_ordinal_reasons = [{
        "pillar": "skill", "stars": 3,
        "reason_text": "His underlying metrics rank in a real 89th percentile tier leaguewide this month.",
        "source_fact_keys": ["final_score"],
    }]
    results.append(check(
        "a MULTI-DIGIT ordinal number ('89th') does not false-positive numeric grounding either (the real bug this fixed)",
        validate_numeric_grounding(multi_digit_ordinal_reasons, facts, mlb_tolerance_for_key) == [],
    ))

    # --- False-positive guard: a small narrative integer (1-5, e.g. a
    # star-adjacent or small counting reference) must NOT trip grounding
    # even when not a literal source-fact value. ---
    small_int_reasons = [{
        "pillar": "opportunity", "stars": 5,
        "reason_text": "This is a top 2 lineup spot for pure volume.",
        "source_fact_keys": ["batting_order_slot"],
    }]
    results.append(check(
        "a small narrative integer (2) does not false-positive numeric grounding",
        validate_numeric_grounding(small_int_reasons, facts, mlb_tolerance_for_key) == [],
    ))

    # --- Adversarial case 3: a 5-star claim on a real pillar score that's
    # genuinely mediocre (environment_score=28.2, well below a real
    # 5-star-worthy number). ---
    inflated_stars_reasons = [{
        "pillar": "environment", "stars": 5,
        "reason_text": "Everything about tonight's park and weather screams home run.",
        "source_fact_keys": ["pillar_detail.environment.score"],
    }]
    star_violations = validate_star_consistency(inflated_stars_reasons, facts, PILLAR_NAMES, STAR_PILLAR_SCORE_KEYS)
    results.append(check(
        "an inflated 5-star claim on a real 28.2 environment score is caught",
        len(star_violations) == 1 and "5 stars" in star_violations[0]["issue"],
    ))

    # --- Adversarial case 4: schema-shape violations — too few reasons,
    # bad pillar name, out-of-range stars, empty source_fact_keys. ---
    bad_shape = {
        "title": "",
        "editorial_sentence": "Real sentence.",
        "why_reasons": [
            {"pillar": "not_a_real_pillar", "stars": 9, "reason_text": "", "source_fact_keys": []},
        ],
    }
    shape_errors = validate_schema_shape(bad_shape)
    results.append(check(
        "schema-shape validation catches empty title, bad pillar, out-of-range stars, empty reason_text, empty citations, and too-few reasons all at once",
        len(shape_errors) >= 5,
    ))

    # --- Tolerance tier 1: percentage/score-type values tolerate rounding
    # to the nearest whole number. Real power_production=81.1 written as
    # plain "81" is normal sports-writing interpretation, not fabrication. ---
    rounded_score_reasons = [{
        "pillar": "skill", "stars": 4,
        "reason_text": "His power grade sits at 81, real top-of-the-scale raw juice.",
        "source_fact_keys": ["pillar_detail.skill.components.power_production"],
    }]
    results.append(check(
        "a percentage/score value rounded to the nearest whole number (81.1 -> 81) is NOT flagged",
        validate_numeric_grounding(rounded_score_reasons, facts, mlb_tolerance_for_key) == [],
    ))

    # --- Tolerance tier 2: odds keep EXACT matching — a small deviation
    # that WOULD pass under the 0.5 rounding tolerance must still be
    # caught for odds specifically, proving odds is on the strict tier,
    # not the lenient one. Real odds=600. ---
    odds_drift_reasons = [{
        "pillar": "opportunity", "stars": 4,
        "reason_text": "Priced at 600.3 tonight, real value at this number.",
        "source_fact_keys": ["odds"],
    }]
    odds_violations = validate_numeric_grounding(odds_drift_reasons, facts, mlb_tolerance_for_key)
    results.append(check(
        "odds gets EXACT tolerance -- a small 0.3 drift (well within the 0.5 rounding tier) is still caught",
        len(odds_violations) == 1 and "600.3" in odds_violations[0]["issue"],
    ))

    # --- Tolerance tier 3: rate stats (OPS/ERA/per-9) get a small
    # decimal-level tolerance, distinct from both the whole-number
    # rounding tier and exact matching. ---
    rate_stat_rounded_reasons = [{
        "pillar": "matchup", "stars": 3,
        "reason_text": "Real recent form here -- a .74 OPS over his last 15 games, and the arm he's facing carries a 3.19 ERA in that same stretch.",
        "source_fact_keys": ["recent_form.recent_ops", "opposing_pitcher_recent_form.recent_era"],
    }]
    results.append(check(
        "rate stats rounded to 2 decimals (.7429... -> .74, 3.1935... -> 3.19) are NOT flagged",
        validate_numeric_grounding(rate_stat_rounded_reasons, facts, mlb_tolerance_for_key) == [],
    ))

    # real recent_k_per_9=7.548... rounds to 8 -- deliberately chosen
    # OUTSIDE the pre-existing, still-approved 1-5 small-integer exemption
    # (using e.g. "1" here would be masked by that unrelated exemption
    # rather than actually exercising this tolerance tier).
    rate_stat_whole_number_reasons = [{
        "pillar": "matchup", "stars": 3,
        "reason_text": "Real recent form here -- a strikeout rate of 8 per nine over that stretch.",
        "source_fact_keys": ["opposing_pitcher_recent_form.recent_k_per_9"],
    }]
    rate_stat_violations = validate_numeric_grounding(rate_stat_whole_number_reasons, facts, mlb_tolerance_for_key)
    results.append(check(
        "rate stats do NOT get the 0.5 whole-number rounding tier -- '8' for a real 7.548 K/9 is still caught",
        len(rate_stat_violations) == 1,
    ))

    # --- Tolerance tier 4: recent-form COUNT fields (games/starts/PA
    # sampled) get exact matching -- a real 15-game sample misstated as 16
    # is a real, meaningful factual error, not reasonable rounding. ---
    count_field_drift_reasons = [{
        "pillar": "skill", "stars": 3,
        "reason_text": "Sampled across his last 16 games, real recent signal.",
        "source_fact_keys": ["recent_form.recent_games_sampled"],
    }]
    count_violations = validate_numeric_grounding(count_field_drift_reasons, facts, mlb_tolerance_for_key)
    results.append(check(
        "recent-form count fields get EXACT tolerance -- 16 for a real 15-game sample is caught",
        len(count_violations) == 1,
    ))

    # --- Regression guard for the real bug this tolerance work fixed:
    # before flattening, recent_form was one opaque dict-valued key, and
    # _numbers_in() has no dict branch -- a real, validly-cited recent-form
    # number would have been wrongly flagged as ungrounded. Confirms it's
    # actually fixed, not just reclassified. ---
    real_recent_form_citation_reasons = [{
        "pillar": "matchup", "stars": 3,
        "reason_text": "A real .74 OPS backs up this angle.",
        "source_fact_keys": ["recent_form.recent_ops"],
    }]
    results.append(check(
        "a validly-cited real recent_form number is genuinely found and grounded (not silently unfindable)",
        validate_numeric_grounding(real_recent_form_citation_reasons, facts, mlb_tolerance_for_key) == [],
    ))

    # --- Real production false positive (2026-08-04, Jeremy Pena):
    # "both sit above N" where the real values round to N+1, not N, but
    # genuinely DO exceed N -- must be validated as a comparative claim,
    # not forced through point-value rounding. Uses this fixture's own
    # real matchup components (28.5, 46.4), both genuinely above 25. ---
    comparative_true_reasons = [{
        "pillar": "matchup", "stars": 3,
        "reason_text": "Contact-allowed and rate-outcome marks both sit above 25, a real tell here.",
        "source_fact_keys": ["pillar_detail.matchup.components.contact_allowed", "pillar_detail.matchup.components.rate_outcome"],
    }]
    results.append(check(
        "a TRUE comparative claim ('both above 25' when real values are 28.5/46.4) is NOT flagged, even though neither rounds to 25",
        validate_numeric_grounding(comparative_true_reasons, facts, mlb_tolerance_for_key) == [],
    ))

    # --- The exact real shape of the false positive this fix targets:
    # real values 90.7/90.8 (hand-added here to mirror Pena's real numbers
    # precisely) genuinely exceed 90 but round to 91, not 90. ---
    pena_shaped_facts = dict(facts)
    pena_shaped_facts["pillar_detail.matchup.components.contact_allowed"] = 90.8
    pena_shaped_facts["pillar_detail.matchup.components.rate_outcome"] = 90.7
    pena_shaped_reasons = [{
        "pillar": "matchup", "stars": 5,
        "reason_text": "Contact-allowed and rate-outcome marks both sit above 90, one of the strongest reads on the board.",
        "source_fact_keys": ["pillar_detail.matchup.components.contact_allowed", "pillar_detail.matchup.components.rate_outcome"],
    }]
    results.append(check(
        "the exact real Jeremy Pena false positive (90.7/90.8 vs claimed 'above 90') no longer triggers",
        validate_numeric_grounding(pena_shaped_reasons, pena_shaped_facts, mlb_tolerance_for_key) == [],
    ))

    # --- A comparative claim that is genuinely FALSE must still be caught
    # -- this fix must not turn "above/over/under" into a free pass. ---
    comparative_false_reasons = [{
        "pillar": "matchup", "stars": 5,
        "reason_text": "Contact-allowed and rate-outcome marks both sit above 200, elite by any measure.",
        "source_fact_keys": ["pillar_detail.matchup.components.contact_allowed", "pillar_detail.matchup.components.rate_outcome"],
    }]
    comparative_false_violations = validate_numeric_grounding(comparative_false_reasons, facts, mlb_tolerance_for_key)
    results.append(check(
        "a FALSE comparative claim ('above 200' when real values are 28.5/46.4) is still caught",
        len(comparative_false_violations) == 1 and "above 200" in comparative_false_violations[0]["issue"],
    ))

    # --- Real production false negative (2026-08-04, deliberate
    # adversarial test): a fabricated ".385 batting average" landed within
    # the old flat 0.5 tolerance of a real wind_speed_mph=0 (roof closed,
    # no wind) and passed as grounded by pure numeric coincidence. Uses
    # this fixture's own real near-zero value
    # (pillar_detail.environment.components.park=0.0) rather than a
    # fabricated fixture. ---
    near_zero_fabrication_reasons = [{
        "pillar": "environment", "stars": 3,
        "reason_text": "A real park factor near .3 backs up the case here.",
        "source_fact_keys": ["pillar_detail.environment.components.park"],
    }]
    near_zero_violations = validate_numeric_grounding(near_zero_fabrication_reasons, facts, mlb_tolerance_for_key)
    results.append(check(
        "a fabricated 0.3 near a real 0.0 is now caught (was silently accepted under the old flat 0.5 tolerance)",
        len(near_zero_violations) == 1 and "0.3" in near_zero_violations[0]["issue"],
    ))

    # --- The near-zero fix must not break legitimately writing '0' for a
    # real 0 -- the floor must still accept the exact real value itself. ---
    real_zero_reasons = [{
        "pillar": "environment", "stars": 2,
        "reason_text": "Real park factor here sits at 0, a neutral read.",
        "source_fact_keys": ["pillar_detail.environment.components.park"],
    }]
    results.append(check(
        "writing the real value itself (0 for a real 0.0) still passes under the tightened near-zero tolerance",
        validate_numeric_grounding(real_zero_reasons, facts, mlb_tolerance_for_key) == [],
    ))

    # --- The near-zero fix must not affect normal-magnitude rounding --
    # already covered above, but re-confirmed explicitly here since this
    # is exactly the behavior that must NOT regress. ---
    results.append(check(
        "normal-magnitude rounding tolerance is unaffected by the near-zero fix (81.1 -> 81 still passes)",
        validate_numeric_grounding(
            [{"pillar": "skill", "stars": 4, "reason_text": "Power grade of 81 here.", "source_fact_keys": ["pillar_detail.skill.components.power_production"]}],
            facts,
            mlb_tolerance_for_key,
        ) == [],
    ))

    # --- Real production false positive (2026-08-04, George Springer,
    # Cold Pitchers to Attack): "1.47 HR/9" and "6 per 9" both had their
    # "9" extracted as a separate ungrounded number. Reproduces the exact
    # real shape using this fixture's own real opposing_pitcher_recent_form
    # (era=3.19, hr_per_9=1.74, bb_per_9=0.87, starts_sampled=5). ---
    per_nine_reasons = [{
        "pillar": "matchup", "stars": 4,
        "reason_text": (
            "The opposing pitcher is leaking hard right now -- a 3.19 recent ERA and 1.74 HR/9 "
            "over his last 5 starts show real trouble, and he is also walking batters at 0.87 "
            "per 9 in that stretch."
        ),
        "source_fact_keys": [
            "opposing_pitcher_recent_form.recent_era",
            "opposing_pitcher_recent_form.recent_hr_per_9",
            "opposing_pitcher_recent_form.recent_bb_per_9",
            "opposing_pitcher_recent_form.recent_starts_sampled",
        ],
    }]
    results.append(check(
        "the exact real George Springer false positive ('1.74 HR/9' and '0.87 per 9') no longer triggers",
        validate_numeric_grounding(per_nine_reasons, facts, mlb_tolerance_for_key) == [],
    ))

    # --- The per-9 exclusion must stay narrow: a genuinely different,
    # fabricated number that happens to follow a slash or the word "per"
    # for an unrelated reason must still be caught, not swallowed by this
    # fix. "/95" and "per 90" don't match the word-boundary-anchored "9"
    # pattern (a real digit immediately follows), so they're untouched. ---
    fake_slash_reasons = [{
        "pillar": "matchup", "stars": 3,
        "reason_text": "A fabricated career mark of 8/95 backs this up, along with a rate of 12 per 90.",
        "source_fact_keys": ["opposing_pitcher_recent_form.recent_era"],
    }]
    fake_slash_violations = validate_numeric_grounding(fake_slash_reasons, facts, mlb_tolerance_for_key)
    results.append(check(
        "a genuinely fabricated number following a slash or 'per' for an unrelated reason ('8/95', '12 per 90') is still caught, not swallowed by the per-9 exclusion",
        any("95" in v["issue"] for v in fake_slash_violations) and any("12" in v["issue"] for v in fake_slash_violations),
    ))

    print(f"\n{sum(results)}/{len(results)} checks passed")
    raise SystemExit(0 if all(results) else 1)
