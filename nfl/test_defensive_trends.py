"""
Tests for defensive_trends.py — the third NFL Intelligence family, reusing
intelligence_schema.py's shared story schema and scoring.score_situation's
defensive_matchup_vulnerability (unchanged) plus the new defensive_
matchup_completeness column (additive extension, this task).

Real-data-first, same discipline the first two families established:
checked against the actual full historical backfill
(player_redzone_weekly.csv), specifically the real 2025 Weeks 12-18 NYJ
RB run-defense collapse (defensive_matchup_vulnerability climbing from
~40-50 to ~82-92) already referenced as validated earlier this session.

Run: python3 nfl/test_defensive_trends.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

from defensive_trends import CONFIG, build_defensive_trends_stories
from intelligence_schema import STORY_FIELDS

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
    # Real Week 15 2025 stories — the flagship NYJ collapse week.
    # ============================================================
    wk15 = build_defensive_trends_stories(weekly, 2025, 15)
    results.append(check(f"Week 15 2025 produces a real, non-trivial set of stories (got {len(wk15)})", 1 <= len(wk15) <= 20))

    by_entity = {(s["entity"]["team"], s["entity"]["position_group"]): s for s in wk15}
    nyj_rb = by_entity.get(("NYJ", "RB"))
    results.append(check("NYJ RB defense has a real Week 15 2025 story", nyj_rb is not None))
    if nyj_rb:
        results.append(check("NYJ RB defense reads as growing-vulnerability (the real collapse)", nyj_rb["trend_direction"] == "growing-vulnerability"))
        results.append(check("NYJ RB defense's primary_signal is a real, high vulnerability reading", nyj_rb["primary_signal"]["value"] >= 80.0))
        results.append(check(
            "NYJ RB defense's headline conveys a growing red-zone vulnerability (voice-retoned off 'getting worse')",
            "leak" in nyj_rb["headline"].lower() and "red zone" in nyj_rb["headline"].lower(),
        ))

    # every schema field genuinely populated
    s = wk15[0]
    results.append(check("every schema field is present on a real story", all(f in s for f in STORY_FIELDS)))
    results.append(check("headline is real, non-empty text", isinstance(s["headline"], str) and len(s["headline"]) > 10))
    results.append(check("story is real, non-empty text distinct from the headline", s["story"] != s["headline"] and len(s["story"]) > 20))
    results.append(check("primary_signal has a real name+value pair", s["primary_signal"]["name"] == "defensive_matchup_vulnerability" and isinstance(s["primary_signal"]["value"], float)))
    results.append(check("supporting_evidence has multiple real, concrete facts", all(len(st["supporting_evidence"]) >= 3 for st in wk15)))

    # ============================================================
    # Entity genericity — a defense entity, NOT a player, proving the
    # shared schema doesn't secretly assume "entity is always a player"
    # (Market Intelligence/Role Changes were both player-entity families).
    # ============================================================
    results.append(check(
        "every real story's entity is a defense (type='defense'), not a player",
        all(st["entity"]["type"] == "defense" and "player_id" not in st["entity"] for st in wk15),
    ))
    results.append(check(
        "entity granularity is (team, position_group), not team-wide -- confirmed distinct readings coexist",
        len({(st["entity"]["team"], st["entity"]["position_group"]) for st in wk15}) == len(wk15),
    ))

    # ============================================================
    # Materiality threshold + structural thin-sample guarantee.
    # ============================================================
    results.append(check(
        "every real story's trend_strength clears the configured trend_threshold",
        all(st["trend_strength"] >= CONFIG["trend_threshold"] for st in wk15),
    ))
    results.append(check(
        "every real story's sample_size exceeds the trend window -- a thin defense structurally CANNOT produce a story "
        "(not just softened language), since a masked trend delta is never material enough to clear the threshold",
        all(st["sample_size"] > CONFIG["trend_window"] for st in wk15),
    ))

    # Broader check across every real week in the data.
    all_weeks_ok = True
    all_position_groups = set()
    min_sample_size = 999
    for (season, week), _ in weekly.groupby(["season", "week"]):
        wk_stories = build_defensive_trends_stories(weekly, season, week)
        for st in wk_stories:
            all_position_groups.add(st["entity"]["position_group"])
            min_sample_size = min(min_sample_size, st["sample_size"])
            if st["trend_strength"] < CONFIG["trend_threshold"] or st["sample_size"] <= CONFIG["trend_window"]:
                all_weeks_ok = False
    results.append(check("threshold + structural sample-size guarantee holds across every real season/week in the backfill", all_weeks_ok))
    results.append(check("no QB entity ever appears (defensive_matchup_vulnerability's own position scope is RB/WR/TE)", "QB" not in all_position_groups))
    results.append(check(f"minimum real sample_size observed across the whole backfill is > trend_window (got {min_sample_size})", min_sample_size > CONFIG["trend_window"]))

    # ============================================================
    # related_players — REVERSED direction vs. the first two families
    # (defense -> offensive players, not player -> teammates/market).
    # Universal Card v2 shape: player_id/display_label/entity_type/
    # direction_indicator/note.
    # ============================================================
    if nyj_rb:
        wk15_2025 = weekly[(weekly["season"] == 2025) & (weekly["week"] == 15)]
        posteam_by_player = dict(zip(wk15_2025["player_id"], wk15_2025["posteam"]))
        results.append(check(
            "related_players are real offensive players, on the OPPOSING (offensive) team, not the defense's own team (cross-checked against real weekly posteam, since related_players itself no longer carries a team field)",
            len(nyj_rb["related_players"]) > 0 and all(posteam_by_player.get(r["player_id"]) != "NYJ" for r in nyj_rb["related_players"]),
        ))
        results.append(check(
            "every related_players entry is a real player entity with a real player_id/display_label, and the note cites the real faces-this-defense relationship",
            all(
                r["entity_type"] == "player" and r["player_id"] and r["display_label"]
                and "Faces this defense this week" in r["note"]
                for r in nyj_rb["related_players"]
            ),
        ))
        results.append(check(
            "direction_indicator matches the real story direction for every entry (growing-vulnerability -> up, since this NYJ RB story is real growing-vulnerability)",
            all(r["direction_indicator"] == "up" for r in nyj_rb["related_players"]) and nyj_rb["trend_direction"] == "growing-vulnerability",
        ))
        results.append(check("related_players is capped, not an unbounded dump", len(nyj_rb["related_players"]) <= CONFIG["related_players_limit"]))
        td_by_player = dict(zip(wk15_2025["player_id"], wk15_2025["td_opportunity"]))
        tds = [td_by_player.get(r["player_id"]) for r in nyj_rb["related_players"]]
        results.append(check(
            "related_players are ranked by real td_opportunity, highest first (cross-checked against real weekly data)",
            tds == sorted(tds, reverse=True),
        ))

    # ============================================================
    # Storytelling honesty — the real bug found and fixed during this
    # family's own build (2025 Week 15 CAR WR defense): a specific raw
    # red-zone-TD claim must never contradict the direction claimed.
    # Checked across the ENTIRE real backfill, not just the one case
    # that surfaced it.
    # ============================================================
    import re
    mismatches = 0
    total_specific = 0
    for (season, week), _ in weekly.groupby(["season", "week"]):
        for st in build_defensive_trends_stories(weekly, season, week):
            specific = [e for e in st["supporting_evidence"] if "red-zone TDs/game over the last 3 games" in e]
            if not specific:
                continue
            total_specific += 1
            m = re.search(r"Allowed ([\d.]+) red-zone TDs/game over the last 3 games \(season average ([\d.]+)\)", specific[0])
            last3, season_avg = float(m.group(1)), float(m.group(2))
            if st["trend_direction"] == "growing-vulnerability" and not (last3 > season_avg):
                mismatches += 1
            if st["trend_direction"] == "growing-resistance" and not (last3 < season_avg):
                mismatches += 1
    results.append(check(
        f"every specific raw-TD citation across the full real backfill agrees with its claimed direction "
        f"(checked {total_specific} real specific citations, {mismatches} mismatches)",
        total_specific > 0 and mismatches == 0,
    ))

    car_wr = by_entity.get(("CAR", "WR"))
    if car_wr:
        results.append(check(
            "the real CAR WR Week 15 case (score trending resistant off a low base while raw red-zone TDs had "
            "already ticked back up) correctly falls back to generic language, not a contradicting specific claim",
            "red-zone TDs/game" not in car_wr["story"] and "combined red-zone/inside-10/goal-line" in car_wr["story"],
        ))

    # ============================================================
    # Universal Card v2 fields — checked across the FULL real backfill,
    # not just one hand-picked case each way.
    # ============================================================
    all_v2_stories = []
    for (season, week), _ in weekly.groupby(["season", "week"]):
        all_v2_stories += build_defensive_trends_stories(weekly, season, week)

    results.append(check(
        f"signal_direction is framed from the real bettor-opportunity perspective across every real story "
        f"(growing-vulnerability -> favorable, growing-resistance -> unfavorable) -- checked {len(all_v2_stories)} real stories",
        all(
            (st["signal_direction"] == "favorable") == (st["trend_direction"] == "growing-vulnerability")
            for st in all_v2_stories
        ),
    ))
    results.append(check(
        "hero_metric is populated if and only if the story's own specific raw-TD citation is present in supporting_evidence "
        "(the exact same td_agrees honesty gate, never relaxed to force a hero number in) -- checked across the full real backfill",
        all(
            (st["hero_metric"] is not None) == any("red-zone TDs/game over the last 3 games" in e for e in st["supporting_evidence"])
            for st in all_v2_stories
        ),
    ))
    hero_stories = [st for st in all_v2_stories if st["hero_metric"] is not None]
    results.append(check(
        f"every real populated hero_metric has before/after values that genuinely agree with its own trend_direction "
        f"(checked {len(hero_stories)} real stories with a populated hero_metric)",
        all(
            (st["hero_metric"]["after_value"] > st["hero_metric"]["before_value"]) == (st["trend_direction"] == "growing-vulnerability")
            for st in hero_stories
        ),
    ))
    results.append(check(
        "what_changed is always a real, non-empty list capped at 3 items, every real story",
        all(isinstance(st["what_changed"], list) and 1 <= len(st["what_changed"]) <= 3 for st in all_v2_stories),
    ))
    results.append(check(
        "what_changed never leaks an internal field name (the real distinction from supporting_evidence's own backend-only role)",
        all(
            not any(bad in item["observation"] for bad in ("defensive_matchup_vulnerability", "recent_tds_allowed_pct", "conversion_rate_allowed_pct"))
            for st in all_v2_stories for item in st["what_changed"]
        ),
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
    real_classification_dist = {c: sum(1 for st in all_v2_stories if st["evidence_classification"] == c) for c in ("strong", "moderate", "limited")}
    results.append(check(
        f"REAL FINDING, not a bug: since confidence and completeness are the SAME real value for this family "
        f"(both set from defensive_matchup_completeness, confirmed always exactly 100.0 among every real story "
        f"that clears the trend gate), evidence_classification is currently a real constant for Defensive Trends "
        f"-- every one of the 176 real stories lands on 'strong' (got {real_classification_dist}). Not driven by "
        f"a varying confidence signal so much as there being no real variation in either input at all for this "
        f"family today -- worth knowing before this same formula is applied to a family where completeness/"
        f"confidence genuinely differ or vary.",
        real_classification_dist["strong"] == len(all_v2_stories) and real_classification_dist["moderate"] == 0 and real_classification_dist["limited"] == 0,
    ))

    nyj_rb_18 = next((st for st in build_defensive_trends_stories(weekly, 2025, 18) if st["entity"]["team"] == "NYJ" and st["entity"]["position_group"] == "RB"), None)
    if nyj_rb_18:
        results.append(check(
            f"real NYJ RB week 18: hero_metric populated with a real before/after TD-rate pair, after > before matching growing-vulnerability (got {nyj_rb_18['hero_metric']})",
            nyj_rb_18["hero_metric"] is not None and nyj_rb_18["hero_metric"]["after_value"] > nyj_rb_18["hero_metric"]["before_value"],
        ))
        results.append(check(
            f"real NYJ RB week 18: signal_direction is favorable (a bettor-relevant opportunity signal, not a defense-quality judgment) (got {nyj_rb_18['signal_direction']})",
            nyj_rb_18["signal_direction"] == "favorable",
        ))

    car_wr_15 = by_entity.get(("CAR", "WR"))
    if car_wr_15:
        results.append(check(
            f"real CAR WR week 15 (the known td_agrees=False case): hero_metric correctly stays null, never a forced/relaxed number (got {car_wr_15['hero_metric']})",
            car_wr_15["hero_metric"] is None,
        ))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
