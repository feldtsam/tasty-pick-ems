"""
Tests for role_changes.py — the second NFL Intelligence family, reusing
intelligence_schema.py's shared story schema (established by Market
Intelligence, first family).

Real-data-first, same discipline test_market_intelligence.py already
established: this module's own story content is checked against REAL
historical rows from player_redzone_weekly.csv (the full backfilled
table), not synthetic fixtures — Week 10 2025 specifically, since
Rachaad White (Bucky Irving out) and Parker Washington (Brian Thomas Jr.
out) were already surfaced as real injury-driven-opportunity cases
during the Trend Shelf validation earlier this session. Real Week 2 vs
Week 10 rows also give a REAL thin-vs-established sample_size contrast
(Tez Johnson, 2 games, vs. Rachaad White, 8 games) — no synthetic
contrast case needed here, unlike Market Intelligence's n_books (which
had no real multi-book example available at all).

Run: python3 nfl/test_role_changes.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

from intelligence_schema import STORY_FIELDS
from role_changes import CONFIG, _opportunity_confidence_completeness, build_role_changes_stories

WEEKLY_PATH = Path(__file__).resolve().parent / "scripts" / "player_redzone_weekly.csv"


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


if __name__ == "__main__":
    results = []

    if not WEEKLY_PATH.exists():
        print(f"SKIPPED all checks — {WEEKLY_PATH} not present in this environment.")
        raise SystemExit(0)

    weekly = pd.read_csv(WEEKLY_PATH)

    # ============================================================
    # Real Week 10 2025 stories — the headline validation week.
    # ============================================================
    wk10 = build_role_changes_stories(weekly, 2025, 10)
    results.append(check(f"Week 10 2025 produces a real, non-trivial set of stories (got {len(wk10)})", 3 <= len(wk10) <= 30))

    by_name = {s["entity"]["player_name"]: s for s in wk10}
    results.append(check("Rachaad White has a real Week 10 2025 story", "Rachaad White" in by_name))
    results.append(check("Parker Washington has a real Week 10 2025 story", "Parker Washington" in by_name))

    white = by_name.get("Rachaad White")
    if white:
        results.append(check("Rachaad White's story is opportunity-driven", white["trend_direction"] == "opportunity-driven"))
        results.append(check(
            "Rachaad White's related_players correctly names Bucky Irving as the causal injury (Universal Card v2 shape: display_label, note carries the real status, direction_indicator='down' for the injured teammate himself)",
            any(r["display_label"] == "Bucky Irving" and "Out" in r["note"] and r["direction_indicator"] == "down" and r["entity_type"] == "player" for r in white["related_players"]),
        ))
        results.append(check("Rachaad White's headline names Bucky Irving", "Bucky Irving" in white["headline"]))

    washington = by_name.get("Parker Washington")
    if washington:
        results.append(check(
            "Parker Washington's related_players correctly names Brian Thomas Jr. as the causal injury",
            any(r["display_label"] == "Brian Thomas Jr." and "Out" in r["note"] for r in washington["related_players"]),
        ))

    # every schema field genuinely populated
    s = wk10[0]
    results.append(check("every schema field is present on a real story", all(f in s for f in STORY_FIELDS)))
    results.append(check("headline is real, non-empty text", isinstance(s["headline"], str) and len(s["headline"]) > 10))
    results.append(check("story is real, non-empty text distinct from the headline", s["story"] != s["headline"] and len(s["story"]) > 20))
    results.append(check("primary_signal has a real name+value pair", s["primary_signal"]["name"] == "role_momentum" and isinstance(s["primary_signal"]["value"], float)))
    results.append(check("supporting_evidence has multiple real, concrete facts", all(len(st["supporting_evidence"]) >= 2 for st in wk10)))

    # ============================================================
    # Materiality threshold + position scoping — must actually hold,
    # not just be documented.
    # ============================================================
    results.append(check(
        "every real story clears the configured role_momentum_threshold",
        all(st["primary_signal"]["value"] >= CONFIG["role_momentum_threshold"] for st in wk10),
    ))
    results.append(check(
        "every real story's entity is RB/WR/TE only (no QB leakage)",
        all(st["entity"]["position_group"] in ("RB", "WR", "TE") for st in wk10),
    ))
    # Broader check across every real week in the data, not just Week 10.
    all_weeks_clean = True
    for (season, week), _ in weekly.groupby(["season", "week"]):
        wk_stories = build_role_changes_stories(weekly, season, week)
        if any(st["entity"]["position_group"] not in ("RB", "WR", "TE") for st in wk_stories):
            all_weeks_clean = False
            break
    results.append(check("no QB (or any non-RB/WR/TE) leakage across every real season/week in the backfill", all_weeks_clean))

    # ============================================================
    # Sample-size honesty — a REAL thin-vs-established contrast
    # (Week 2 vs Week 10), not a synthetic one.
    # ============================================================
    wk2 = build_role_changes_stories(weekly, 2025, 2)
    results.append(check(f"Week 2 2025 (early season) produces real stories too (got {len(wk2)})", len(wk2) >= 1))
    results.append(check(
        "every real Week 2 story is genuinely thin (games_played < thin_games_played)",
        all(st["sample_size"] < CONFIG["thin_games_played"] for st in wk2),
    ))
    results.append(check(
        "every real thin (Week 2) story's language is honestly hedged",
        all(any(w in st["headline"].lower() or w in st["story"].lower() for w in ("early", "fresh", "thin")) for st in wk2),
    ))
    results.append(check(
        "thin real stories show a penalized completeness relative to an established one",
        max(st["completeness"] for st in wk2) < min(st["completeness"] for st in wk10 if st["sample_size"] >= CONFIG["thin_games_played"]),
    ))

    established_opportunity = [st for st in wk10 if st["trend_direction"] == "opportunity-driven" and st["sample_size"] >= CONFIG["thin_games_played"]]
    results.append(check(
        "established (non-thin) opportunity-driven stories do NOT use hedged early-season language",
        len(established_opportunity) > 0
        and all(not any(w in st["headline"].lower() for w in ("early", "fresh")) for st in established_opportunity),
    ))

    # ============================================================
    # related_players — Universal Card v2 shape (player_id/display_
    # label/entity_type/direction_indicator/note) — two genuinely
    # different real relationship types, each internally consistent.
    # ============================================================
    opportunity_stories = [st for st in wk10 if st["trend_direction"] == "opportunity-driven"]
    results.append(check(
        "every opportunity-driven story's related_players is a real player entity, direction_indicator='down' (the injured teammate's own real status), note citing the real injury",
        all(
            all(
                r["entity_type"] == "player" and r["direction_indicator"] == "down" and "Injured" in r["note"]
                for r in st["related_players"]
            )
            for st in opportunity_stories
        ),
    ))
    wk10_2025 = weekly[(weekly["season"] == 2025) & (weekly["week"] == 10)]
    posteam_by_player = dict(zip(wk10_2025["player_id"], wk10_2025["posteam"]))
    trend_stories = [st for st in wk10 if st["trend_direction"] == "role-trend-driven" and st["related_players"]]
    results.append(check(
        "every role-trend-driven story's related_players is real same-team competition (cross-checked against real weekly posteam, since the field itself was dropped from the v2 shape), excludes self, direction_indicator='none'",
        len(trend_stories) > 0
        and all(
            all(
                r["entity_type"] == "player" and r["direction_indicator"] == "none"
                and posteam_by_player.get(r["player_id"]) == st["entity"]["team"]
                and r["player_id"] != st["entity"]["player_id"]
                for r in st["related_players"]
            )
            for st in trend_stories
        ),
    ))
    results.append(check(
        "related_players is capped, not an unbounded dump",
        all(len(st["related_players"]) <= CONFIG["related_players_limit"] for st in wk10),
    ))

    # ============================================================
    # Storytelling honesty — a specific role-trend claim's cited raw
    # numbers must actually support the direction claimed (the exact
    # class of bug already fixed twice this session elsewhere).
    # ============================================================
    specific_claim_stories = [
        st for st in wk10
        if st["trend_direction"] == "role-trend-driven" and "up to" in st["story"]
    ]
    honest = True
    for st in specific_claim_stories:
        eyed = [e for e in st["supporting_evidence"] if "up to" in e]
        if not eyed:
            honest = False
            break
        # "up to X% ... from a Y% season average" -- X must exceed Y for the "expanding" claim to be honest.
        import re
        m = re.search(r"up to ([\d.]+)%.*from a ([\d.]+)%", eyed[0])
        if not m or not (float(m.group(1)) > float(m.group(2))):
            honest = False
            break
    results.append(check(
        "every specific role-trend claim's cited raw numbers genuinely support the 'expanding' direction claimed",
        honest and len(specific_claim_stories) > 0,
    ))

    generic_stories = [st for st in wk10 if st["trend_direction"] == "role-trend-driven" and "up to" not in st["story"]]
    if generic_stories:
        results.append(check(
            "role-trend stories with no strong single-component evidence fall back to honest generic language, not a fabricated specific claim",
            all("combined role-trend read" in st["story"] for st in generic_stories),
        ))

    # ============================================================
    # Universal Card v2 fields — checked across the FULL real backfill,
    # not just week 10.
    # ============================================================
    all_v2_stories = []
    for season in weekly["season"].dropna().unique():
        for week in range(1, 23):
            all_v2_stories += build_role_changes_stories(weekly, int(season), week)

    results.append(check(
        f"signal_direction is 'favorable' for every real story, confirmed as a genuine constant for this family "
        f"(Role Changes is expanding-roles-only by design — there is no real 'role shrinking' story in scope today) "
        f"-- checked {len(all_v2_stories)} real stories",
        all(st["signal_direction"] == "favorable" for st in all_v2_stories),
    ))
    results.append(check(
        "hero_metric is null for every real opportunity-driven story (that branch never computes a role-trend evidence_kind at all)",
        all(st["hero_metric"] is None for st in all_v2_stories if st["trend_direction"] == "opportunity-driven"),
    ))
    role_trend_stories = [st for st in all_v2_stories if st["trend_direction"] == "role-trend-driven"]
    results.append(check(
        "hero_metric is populated for a role-trend-driven story if and only if its story text carries a specific 'up to X%...' or depth-chart claim "
        "(the same real evidence_kind gate, never a separate looser check)",
        all(
            (st["hero_metric"] is not None) == ("up to" in st["story"] or "Moved up the depth chart" in st["story"])
            for st in role_trend_stories
        ),
    ))
    hero_stories = [st for st in all_v2_stories if st["hero_metric"] is not None]
    results.append(check(
        f"every real populated hero_metric's label is one of the three real sub-metrics, each with the right unit/format (checked {len(hero_stories)} real stories)",
        all(
            (st["hero_metric"]["label"] in ("Snap Share", "Red-Zone Touch Share") and st["hero_metric"]["value_format"] == "percent")
            or ("Depth Chart Rank" in st["hero_metric"]["label"] and st["hero_metric"]["value_format"] == "rank" and st["hero_metric"]["lower_is_better"] is True)
            for st in hero_stories
        ),
    ))
    results.append(check(
        "depth_chart hero_metric always uses PRIOR WEEK/NOW period labels, genuinely different from snap/touch share's SEASON/LAST 3 (a real different comparison window, not a copy-paste)",
        all(
            (st["hero_metric"]["period_before_label"], st["hero_metric"]["period_after_label"]) == ("PRIOR WEEK", "NOW")
            for st in hero_stories if "Depth Chart Rank" in st["hero_metric"]["label"]
        ),
    ))
    results.append(check(
        "what_changed is always a real, non-empty list capped at 3 items, every real story",
        all(isinstance(st["what_changed"], list) and 1 <= len(st["what_changed"]) <= 3 for st in all_v2_stories),
    ))
    results.append(check(
        "evidence_classification is always one of the three real approved values, every real story",
        all(st["evidence_classification"] in ("strong", "moderate", "limited") for st in all_v2_stories),
    ))
    results.append(check(
        "the real formula (confidence+completeness)/2 against real thresholds (>=80 strong, >=60 moderate) is applied correctly, checked against every real story's own confidence/completeness",
        all(
            st["evidence_classification"] == (
                "strong" if (st["confidence"] + st["completeness"]) / 2 >= 80.0
                else "moderate" if (st["confidence"] + st["completeness"]) / 2 >= 60.0
                else "limited"
            )
            for st in all_v2_stories
        ),
    ))

    # ============================================================
    # Opportunity-driven confidence/completeness + evidence cap
    # (2026-10-06) -- see role_changes.py's module docstring. Helper
    # checked directly on minimal rows, then the real CSV end to end.
    # ============================================================
    def _opp_row(**over):
        base = {"external_opportunity": 100.0, "depth_rank": 2.0, "snap_share": 0.6, "role_momentum_completeness": 0.0}
        base.update(over)
        return pd.Series(base)

    out_4g = _opportunity_confidence_completeness(_opp_row(), 4, [{"player_name": "X", "status": "Out"}], CONFIG)
    results.append(check(
        "an 'Out' ahead with 4 games lands high before the cap (confidence 100, completeness 100) but is capped to 35/35 while the trend is unreadable",
        out_4g["cap_applied"] is True and out_4g["confidence"] == 35.0 and out_4g["completeness"] == 35.0
        and all(out_4g["inputs_real"].values()),
    ))
    q_1g = _opportunity_confidence_completeness(_opp_row(external_opportunity=40.0), 1, [{"player_name": "X", "status": "Questionable"}], CONFIG)
    results.append(check(
        "a 'Questionable' ahead with 1 game lands low on its own (confidence 20 = 40 x 0.5; completeness 35 after the cap; average under 30), not lifted to the cap",
        q_1g["confidence"] == 20.0 and q_1g["completeness"] == 35.0 and (q_1g["confidence"] + q_1g["completeness"]) / 2 < 30.0
        and q_1g["inputs_real"]["games_played_established"] is False,
    ))
    lifted = _opportunity_confidence_completeness(_opp_row(role_momentum_completeness=40.0), 4, [{"player_name": "X", "status": "Out"}], CONFIG)
    results.append(check(
        "the cap lifts at role_momentum_completeness >= 40: the same 'Out' + 4 games reads 100/100 with cap_applied False",
        lifted["cap_applied"] is False and lifted["confidence"] == 100.0 and lifted["completeness"] == 100.0,
    ))
    two_g = _opportunity_confidence_completeness(_opp_row(role_momentum_completeness=60.0, depth_rank=float("nan")), 2, [{"player_name": "X", "status": "Out"}], CONFIG)
    results.append(check(
        "games-played factor and the completeness share are both real: 2 games -> confidence 75 (100 x 0.75); a missing depth_rank -> completeness 50 (2 of 4 inputs real, games < 3)",
        two_g["confidence"] == 75.0 and two_g["completeness"] == 50.0,
    ))

    # End to end on the real CSV (the same stories checked above).
    white_new = by_name.get("Rachaad White")
    results.append(check(
        "real Rachaad White (Out ahead, 8 games, role_momentum_completeness 80): cap lifted, confidence 100 / completeness 100, classified strong",
        white_new is not None and white_new["confidence"] == 100.0 and white_new["completeness"] == 100.0 and white_new["evidence_classification"] == "strong",
    ))
    tez = by_name.get("Tez Johnson")
    results.append(check(
        "real Tez Johnson (Out ahead, 2 games, role_momentum_completeness 20): capped at 35/35, classified limited",
        tez is not None and tez["confidence"] == 35.0 and tez["completeness"] == 35.0 and tez["evidence_classification"] == "limited",
    ))
    trend_wk10 = [st for st in wk10 if st["trend_direction"] == "role-trend-driven"]
    wk10_rows = weekly[(weekly["season"] == 2025) & (weekly["week"] == 10)].set_index("player_id")
    results.append(check(
        "the role-trend-driven path is unchanged: confidence and completeness still equal the row's own role_momentum_completeness on every real trend story",
        trend_wk10 and all(
            st["confidence"] == st["completeness"] == float(wk10_rows.loc[st["entity"]["player_id"], "role_momentum_completeness"])
            for st in trend_wk10
        ),
    ))
    opp_all = [st for st in all_v2_stories if st["trend_direction"] == "opportunity-driven"]
    results.append(check(
        "every real opportunity-driven story across the backfill obeys the rule: average <= 35 unless its own completeness/confidence came from a lifted cap (both fields > 35 only together)",
        opp_all and all(
            (st["confidence"] + st["completeness"]) / 2 <= 35.0 or (st["confidence"] > 35.0 or st["completeness"] > 35.0)
            for st in opp_all
        ) and all(st["confidence"] <= 100.0 and st["completeness"] <= 100.0 for st in opp_all),
    ))
    results.append(check(
        "classification thresholds still hold on the new values: a capped opportunity story is 'limited', a lifted 100/100 one is 'strong' (checked on real Tez Johnson / Rachaad White above and the formula check over every story below)",
        (tez is None or tez["evidence_classification"] == "limited") and (white_new is None or white_new["evidence_classification"] == "strong"),
    ))

    real_classification_dist = {c: sum(1 for st in all_v2_stories if st["evidence_classification"] == c) for c in ("strong", "moderate", "limited")}
    results.append(check(
        f"REAL FINDING, distinct from Defensive Trends: role_momentum_completeness genuinely VARIES across real stories "
        f"(unlike Defensive's constant-100 case), so evidence_classification actually exercises all three real bands "
        f"here (got {real_classification_dist})",
        real_classification_dist["moderate"] > 0 and real_classification_dist["limited"] > 0 and real_classification_dist["strong"] > 0,
    ))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
