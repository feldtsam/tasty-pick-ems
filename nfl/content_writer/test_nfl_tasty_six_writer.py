"""
NFL Content Generation, Part C — test_nfl_tasty_six_writer.py.

Tests the NFL Tasty Six writer's real building blocks (nfl_writer_
common.py's candidate shaping, tasty_six_prompt.py's prompt construction,
tasty_six_writer_schema.py's schema-shape validation, and Part B's
parameterized card_writer_common validators wired with NFL's own
parameters) against REAL scored NFL data — the real 2025 Week 10 pool
(synthetic odds re-attached, same technique already validated for
curate_home_shelves.py — real seasons never captured real ATTD odds) and
real Tasty Six picks it actually produces, not hand-invented fixtures.

Does NOT make a real Claude API call (no ANTHROPIC_API_KEY dependency,
same reasoning as MLB's own test_tasty_six_writer_schema.py) — every
check here is against hand-constructed "model output" shapes, exercising
the real deterministic validators the same way a real adversarial or
clean model response would.

Run: python3 nfl/content_writer/test_nfl_tasty_six_writer.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "pipeline" / "api" / "content_writer"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "pipeline" / "api" / "content_writer" / "voice"))

import numpy as np
import pandas as pd

from card_writer_common import (  # noqa: E402
    flatten_source_facts,
    validate_citations,
    validate_numeric_grounding,
    validate_star_consistency,
)
from banned_language import find_banned_phrases  # noqa: E402
from curate_home_shelves import curate_nfl_shelves  # noqa: E402
from nfl_writer_common import (  # noqa: E402
    NFL_PILLAR_NAMES,
    NFL_STAR_PILLAR_SCORE_KEYS,
    NFL_TOP_LEVEL_CITABLE_FIELDS,
    build_nfl_writer_candidate,
    nfl_tolerance_for_key,
)
from shelves import add_red_zone_trend_windows  # noqa: E402
from nfl_tasty_six_prompt import build_system_prompt, build_user_prompt  # noqa: E402
from nfl_tasty_six_writer_schema import validate_schema_shape  # noqa: E402

WEEKLY_PATH = Path(__file__).resolve().parent.parent / "scripts" / "player_redzone_weekly.csv"


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


if __name__ == "__main__":
    if not WEEKLY_PATH.exists():
        print(f"SKIPPED — {WEEKLY_PATH} not present in this environment.")
        raise SystemExit(0)

    results = []

    weekly = pd.read_csv(WEEKLY_PATH)
    sub = weekly[(weekly["season"] == 2025) & (weekly["week"] == 10)].copy()
    rng = np.random.default_rng(11)
    sub["consensus_price_american"] = np.where(sub["tpe_score"].notna(), rng.integers(250, 1500, size=len(sub)), np.nan)

    result = curate_nfl_shelves(sub, season=2025, week=10)
    tasty_six = result["tasty_six"]
    enriched = add_red_zone_trend_windows(sub)

    real_picks = {shelf: row for shelf, row in tasty_six.items() if row is not None}
    print(f"Real Tasty Six picks this week: {list(real_picks.keys())}\n")
    results.append(check("at least one real Tasty Six pick exists this week to test against", len(real_picks) > 0))

    # ============================================================
    # build_nfl_writer_candidate + flatten_source_facts against the
    # REAL Red Zone Trends pick.
    # ============================================================
    rz_row = enriched[enriched["player_id"] == real_picks["Red Zone Trends"]["player_id"]].iloc[0].to_dict()
    monangai = build_nfl_writer_candidate(rz_row)
    facts = flatten_source_facts(monangai, NFL_TOP_LEVEL_CITABLE_FIELDS)

    print(f"Real Red Zone Trends pick: {monangai['player_name']} — td_opportunity={monangai['td_opportunity']}, "
          f"proven_heat={monangai['proven_heat']}, emerging_heat={monangai['emerging_heat']}, "
          f"i10_trail3={monangai['i10_touches_trail3']}, rz_tds_trail3={monangai['rz_tds_trail3']}")

    results.append(check(
        "flatten_source_facts pulls real NFL top-level fields, no nesting",
        facts.get("td_opportunity") == monangai["td_opportunity"] and facts.get("player_name") == monangai["player_name"],
    ))
    results.append(check(
        "no dotted pillar_detail-style keys exist for NFL (confirms the no-nesting design choice)",
        not any("." in k for k in facts),
    ))
    results.append(check(
        "internal bookkeeping fields (player_id, game_id, season, week) are NOT citable",
        "player_id" not in facts and "game_id" not in facts and "season" not in facts and "week" not in facts,
    ))
    results.append(check(
        "market_value_score is absent from facts for this real historical row (never backfilled — a confirmed, real gap, not a bug)",
        "market_value_score" not in facts,
    ))

    # ============================================================
    # A genuinely clean, well-grounded Red Zone Trends card, built from
    # this real player's real values.
    # ============================================================
    clean_reasons = [
        {
            "pillar": "td_opportunity", "stars": 5,
            "reason_text": (
                f"His touchdown-opportunity reading sits at {monangai['td_opportunity']:.0f}, with touch-share "
                f"trending {monangai['touch_share_trend_pct']:.0f}th percentile and snap share right behind at "
                f"{monangai['snap_share_trend_pct']:.0f}th — backed by a real red-zone touchdown and a real "
                f"near-the-goal-line touch over his last three games."
            ),
            "source_fact_keys": ["td_opportunity", "touch_share_trend_pct", "snap_share_trend_pct", "i10_touches_trail3", "rz_tds_trail3"],
        },
        {
            "pillar": "role_momentum", "stars": 4,
            "reason_text": f"His overall role & momentum reading is a strong {monangai['role_momentum']:.0f}, reinforcing this usage bump is real, not a one-week blip.",
            "source_fact_keys": ["role_momentum"],
        },
        {
            "pillar": "market_value", "stars": 3,
            "reason_text": f"Priced at +{monangai['consensus_price_american']:.0f}, squarely in the long-shot range where this shelf's premise plays out honestly.",
            "source_fact_keys": ["consensus_price_american"],
        },
    ]
    results.append(check("clean card: zero citation violations", validate_citations(clean_reasons, facts) == []))
    results.append(check("clean card: zero numeric-grounding violations", validate_numeric_grounding(clean_reasons, facts, nfl_tolerance_for_key) == []))
    results.append(check("clean card: zero star-consistency violations", validate_star_consistency(clean_reasons, facts, NFL_PILLAR_NAMES, NFL_STAR_PILLAR_SCORE_KEYS) == []))
    results.append(check("clean card: passes schema-shape validation", validate_schema_shape({
        "title": "Real Title", "editorial_sentence": "Real sentence.", "why_reasons": clean_reasons,
    }) == []))
    results.append(check(
        "market_value reason with no real market_value_score on this row still passes (permissive 1-5 range — no real score to check against)",
        validate_star_consistency([clean_reasons[2]], facts, NFL_PILLAR_NAMES, NFL_STAR_PILLAR_SCORE_KEYS) == [],
    ))

    # ============================================================
    # Adversarial: fabricated citation key.
    # ============================================================
    fabricated_key_reasons = [{
        "pillar": "td_opportunity", "stars": 4,
        "reason_text": "A real elite reading here.",
        "source_fact_keys": ["td_opportunity_score"],  # real key is "td_opportunity", not "td_opportunity_score"
    }]
    citation_violations = validate_citations(fabricated_key_reasons, facts)
    results.append(check(
        "fabricated citation key ('td_opportunity_score', which doesn't exist) is caught by name",
        len(citation_violations) == 1 and "td_opportunity_score" in citation_violations[0]["issue"],
    ))

    # ============================================================
    # Adversarial: citing the real, unbacked market_value_score key on
    # this real historical row (absent — Market Value never backfilled)
    # is caught as a fabricated citation, same as any other absent key —
    # confirms the pipeline correctly treats an unpopulated pillar as
    # "not citable yet," not silently permissive.
    # ============================================================
    unbacked_market_value_reasons = [{
        "pillar": "market_value", "stars": 3,
        "reason_text": "The market itself rates him highly here.",
        "source_fact_keys": ["market_value_score"],
    }]
    unbacked_violations = validate_citations(unbacked_market_value_reasons, facts)
    results.append(check(
        "citing market_value_score on a real row where it's genuinely absent is caught, not silently allowed",
        len(unbacked_violations) == 1 and "market_value_score" in unbacked_violations[0]["issue"],
    ))

    # ============================================================
    # Adversarial: a distorted number on a validly-cited key.
    # ============================================================
    distorted_number_reasons = [{
        "pillar": "td_opportunity", "stars": 5,
        "reason_text": "His touchdown-opportunity reading is a massive 99, about as high as it gets.",
        "source_fact_keys": ["td_opportunity"],
    }]
    numeric_violations = validate_numeric_grounding(distorted_number_reasons, facts, nfl_tolerance_for_key)
    results.append(check(
        f"a distorted number (99 vs real {monangai['td_opportunity']}) on a validly-cited key is still caught",
        len(numeric_violations) == 1 and "99" in numeric_violations[0]["issue"],
    ))

    # ============================================================
    # Adversarial: an inflated star claim on a real, mediocre score
    # (environment_score=58.0 here — well below a real 5-star-worthy
    # number per _expected_star_range).
    # ============================================================
    inflated_stars_reasons = [{
        "pillar": "environment", "stars": 5,
        "reason_text": "Conditions here are about as good as it gets tonight.",
        "source_fact_keys": ["environment_score"],
    }]
    star_violations = validate_star_consistency(inflated_stars_reasons, facts, NFL_PILLAR_NAMES, NFL_STAR_PILLAR_SCORE_KEYS)
    results.append(check(
        f"an inflated 5-star claim on a real {facts.get('environment_score')} environment score is caught",
        len(star_violations) == 1 and "5 stars" in star_violations[0]["issue"],
    ))

    # ============================================================
    # Adversarial: banned guarantee language.
    # ============================================================
    banned_found = find_banned_phrases("This Is A Real Lock Tonight")
    results.append(check("banned guarantee language ('lock') is caught by the reused, unmodified banned_language.py", "lock" in banned_found))

    # ============================================================
    # THE REAL FIELD-SHAPE ISSUE THIS TASK'S OWN INVESTIGATION FOUND:
    # snap_share_last1/snap_share_season_avg are stored as 0-1 fractions
    # on the real scored table. Confirms the rescale fix in build_nfl_
    # writer_candidate actually closes it — a naturally-phrased "96%"/
    # "77%" claim against the REAL Bijan Robinson RB Trends pick must NOT
    # be flagged, and the raw un-rescaled key name must not even be
    # citable (prevention, not just tolerant detection).
    # ============================================================
    rb_pick = real_picks.get("RB Trends")
    if rb_pick is not None:
        rb_row = enriched[enriched["player_id"] == rb_pick["player_id"]].iloc[0].to_dict()
        bijan = build_nfl_writer_candidate(rb_row)
        rb_facts = flatten_source_facts(bijan, NFL_TOP_LEVEL_CITABLE_FIELDS)
        print(f"\nReal RB Trends pick: {bijan['player_name']} — snap_share_last1_pct={rb_facts.get('snap_share_last1_pct')}, "
              f"snap_share_season_avg_pct={rb_facts.get('snap_share_season_avg_pct')}")

        results.append(check(
            "snap_share_last1/season_avg were rescaled to 0-100 before flattening (raw fraction is NOT the citable value)",
            rb_facts.get("snap_share_last1_pct") == round(bijan["snap_share_last1"] * 100, 1),
        ))
        rescaled_reasons = [{
            "pillar": "role_momentum", "stars": 4,
            "reason_text": (
                f"His snap share jumped to {rb_facts['snap_share_last1_pct']:.0f}% this week from a "
                f"{rb_facts['snap_share_season_avg_pct']:.0f}% season baseline, a real, meaningful workload bump."
            ),
            "source_fact_keys": ["snap_share_last1_pct", "snap_share_season_avg_pct"],
        }]
        rescaled_violations = validate_numeric_grounding(rescaled_reasons, rb_facts, nfl_tolerance_for_key)
        results.append(check(
            "a naturally-phrased snap-share percentage claim against the RESCALED real values produces zero false-positive violations",
            rescaled_violations == [],
        ))

        raw_key_reasons = [{
            "pillar": "role_momentum", "stars": 4,
            "reason_text": "His snap share is way up this week.",
            "source_fact_keys": ["snap_share_last1"],  # the RAW, un-rescaled key -- deliberately not citable
        }]
        raw_key_violations = validate_citations(raw_key_reasons, rb_facts)
        results.append(check(
            "the RAW, un-rescaled snap_share_last1 key is not citable at all (prevention, not just tolerant detection)",
            len(raw_key_violations) == 1,
        ))
    else:
        print("(skipped the RB Trends snap-share rescale check -- no real RB Trends Tasty Six pick this week)")

    # ============================================================
    # build_system_prompt: real NFL shelf personality, real band
    # assertiveness, real banned-language list, NFL's five pillars,
    # explicit no-baseball instruction, fail-loud on a bad shelf/band.
    # ============================================================
    system_prompt = build_system_prompt("Red Zone Trends", "strong_setup")
    user_prompt = build_user_prompt(facts)

    results.append(check(
        "system prompt includes the real NFL shelf's personality description",
        "goal-line" in system_prompt.lower() or "red-zone real estate" in system_prompt.lower(),
    ))
    results.append(check(
        "system prompt includes the real confidence band's assertiveness guidance (reused, unmodified from MLB)",
        "confident and declarative" in system_prompt.lower(),
    ))
    results.append(check(
        "system prompt explicitly bans real guarantee/betting-slang phrases (reused, unmodified from MLB)",
        "guaranteed" in system_prompt.lower() and "wager" in system_prompt.lower(),
    ))
    results.append(check(
        "system prompt states NFL's real five pillars, not MLB's four",
        "td_opportunity" in system_prompt and "role_momentum" in system_prompt
        and "matchup" in system_prompt and "environment" in system_prompt and "market_value" in system_prompt
        and "skill" not in system_prompt.lower().split("hard rules")[0].lower(),
    ))
    results.append(check(
        "system prompt correctly describes this as an NFL anytime-touchdown pick'em app, not baseball",
        "anytime-touchdown" in system_prompt.lower() and "home run" not in system_prompt.lower() and "home-run" not in system_prompt.lower(),
    ))
    results.append(check(
        "system prompt explicitly instructs no baseball terminology anywhere in the card",
        "not baseball" in system_prompt.lower(),
    ))
    results.append(check(
        "user prompt references the real emit_nfl_tasty_six_card tool",
        "emit_nfl_tasty_six_card" in user_prompt,
    ))
    try:
        build_system_prompt("Not A Real Shelf", "strong_setup")
        results.append(check("build_system_prompt raises KeyError for an unrecognized shelf", False))
    except KeyError:
        results.append(check("build_system_prompt raises KeyError for an unrecognized shelf", True))

    # ============================================================
    # validate_schema_shape: NFL's real pillar enum rejects an MLB
    # pillar name outright (proves the two enums are genuinely separate,
    # not accidentally sharing MLB's PILLAR_NAMES).
    # ============================================================
    mlb_pillar_shape = {
        "title": "Real Title", "editorial_sentence": "Real sentence.",
        "why_reasons": [
            {"pillar": "skill", "stars": 4, "reason_text": "x", "source_fact_keys": ["td_opportunity"]},
            {"pillar": "matchup", "stars": 3, "reason_text": "y", "source_fact_keys": ["defensive_matchup_vulnerability"]},
        ],
    }
    shape_errors = validate_schema_shape(mlb_pillar_shape)
    results.append(check(
        "an MLB pillar name ('skill') is rejected by NFL's own schema-shape validation",
        any("skill" in e or "pillar" in e for e in shape_errors) and len(shape_errors) >= 1,
    ))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
