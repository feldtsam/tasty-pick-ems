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

from defensive_trends import CONFIG, _methodology_for_games_played, _trend_delta, build_defensive_trends_stories
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
        "every real story's sample_size exceeds ITS OWN methodology.trend_window_games -- a thin defense "
        "structurally CANNOT produce a story below its dynamic window (not just softened language), since a "
        "masked trend delta is never material enough to clear the threshold. Updated for the dynamic-window "
        "change: the window a story actually used is no longer always CONFIG['trend_window'] (a window=1 "
        "'thin'-maturity story is real and expected now, with sample_size as low as 3) -- so this checks each "
        "story against its OWN recorded window, not the fixed config constant.",
        all(st["sample_size"] > st["methodology"]["trend_window_games"] for st in wk15),
    ))

    # Broader check across every real week in the data.
    all_weeks_ok = True
    all_position_groups = set()
    min_sample_size = 999
    maturity_counts = {"thin": 0, "developing": 0, "confirmed": 0}
    all_backfill_stories = []
    for (season, week), _ in weekly.groupby(["season", "week"]):
        wk_stories = build_defensive_trends_stories(weekly, season, week)
        all_backfill_stories.extend(wk_stories)
        for st in wk_stories:
            all_position_groups.add(st["entity"]["position_group"])
            min_sample_size = min(min_sample_size, st["sample_size"])
            maturity_counts[st["methodology_maturity"]] += 1
            if st["trend_strength"] < CONFIG["trend_threshold"] or st["sample_size"] <= st["methodology"]["trend_window_games"]:
                all_weeks_ok = False
    results.append(check("threshold + structural sample-size guarantee (against each story's OWN window) holds across every real season/week in the backfill", all_weeks_ok))
    results.append(check("no QB entity ever appears (defensive_matchup_vulnerability's own position scope is RB/WR/TE)", "QB" not in all_position_groups))
    results.append(check(
        f"minimum real sample_size observed across the whole backfill is now structurally >= 2, never 0 or 1 -- "
        f"games_played=2 stories are now POSSIBLE (the games_played off-by-one this session's Editorial "
        f"Intelligence investigation found and fixed: games_played used to be 0-indexed cumcount(), so a defense's "
        f"real 2nd week read as games_played=1 and missed thin_games_played_min=2 by one), though whether a real "
        f"games_played=2 story actually appears in THIS backfill also depends on that week's own delta clearing "
        f"trend_threshold -- a separate, real magnitude gate this fix doesn't touch (the boundary-condition test "
        f"below proves games_played=2 reaches 'thin' directly, independent of whether the real backfill data "
        f"happens to be large enough at that exact point) (got {min_sample_size}, maturity distribution {maturity_counts})",
        min_sample_size >= 2 and maturity_counts["thin"] > 0,
    ))

    # ============================================================
    # Dynamic trend-window schedule -- unit-level boundary check
    # (_methodology_for_games_played directly, every boundary named in
    # the approved schedule) plus real-data confirmation that each
    # tier actually produces correctly-shaped output.
    # ============================================================
    boundary_cases = pd.Series([0, 1, 2, 3, 4, 8, 9, 20])
    schedule = _methodology_for_games_played(boundary_cases, CONFIG)
    expected = [
        (float("nan"), None), (float("nan"), None),  # games_played 0, 1: no trend at all
        (1.0, "thin"), (1.0, "thin"),                  # games_played 2, 3: thin
        (3.0, "developing"), (3.0, "developing"),      # games_played 4, 8: developing
        (3.0, "confirmed"), (3.0, "confirmed"),        # games_played 9, 20: confirmed
    ]
    schedule_ok = all(
        (pd.isna(w) and pd.isna(ew)) or (w == ew and m == em)
        for (w, m), (ew, em) in zip(zip(schedule["_trend_window"], schedule["_methodology_maturity"]), expected)
    )
    results.append(check(
        f"_methodology_for_games_played matches the approved schedule at every named boundary "
        f"(games_played=[0,1,2,3,4,8,9,20] -> window/maturity={list(zip(schedule['_trend_window'].tolist(), schedule['_methodology_maturity'].tolist()))})",
        schedule_ok,
    ))

    # ============================================================
    # Off-by-one regression guard -- a defense with EXACTLY 2 real
    # reconciled weeks must land in "thin" (games_played=2), not below
    # it. Direct regression test for the real bug this session's
    # Editorial Intelligence investigation found and fixed: games_played
    # used to be raw 0-indexed cumcount(), so a defense's own real
    # SECOND reconciled week read as games_played=1 and missed
    # thin_games_played_min=2 by exactly one -- with only 2 real
    # reconciled weeks on file (the real 2026 Week 2 state this was
    # traced against), this made Defensive Trends produce zero stories
    # regardless of any real signal. Exercises _trend_delta directly
    # (not build_defensive_trends_stories' own pool filter) so this
    # isolates the games_played computation itself from the trend_
    # threshold gate. Synthetic, not backfill-dependent: the real
    # historical CSV never has a defense with EXACTLY 2 games on file
    # (every real season there already has 17+ weeks), so this exact
    # boundary can only be exercised directly, not found in real data.
    # ============================================================
    two_week_defense = pd.DataFrame({
        "defteam": ["KC", "KC"],
        "position_group": ["RB", "RB"],
        "season": [2099, 2099],
        "week": [1, 2],
        "defensive_matchup_vulnerability_last1": [40.0, 55.0],
        "defensive_matchup_vulnerability_last3": [40.0, 47.5],
        "defensive_matchup_vulnerability_season_avg": [40.0, 47.5],
    })
    two_week_trend = _trend_delta(two_week_defense, CONFIG)
    wk1_maturity, wk2_maturity = two_week_trend["_methodology_maturity"].tolist()
    wk1_window, wk2_window = two_week_trend["_trend_window"].tolist()
    wk2_delta = two_week_trend["_delta"].iloc[1]
    results.append(check(
        f"a defense's own FIRST reconciled week (games_played=1) still correctly produces no trend at all "
        f"(got maturity={wk1_maturity!r}, window={wk1_window})",
        pd.isna(wk1_maturity) and pd.isna(wk1_window),
    ))
    results.append(check(
        f"a defense's own SECOND reconciled week (games_played=2) now correctly lands in 'thin', not below it -- "
        f"the exact off-by-one this fix closes (got maturity={wk2_maturity!r}, window={wk2_window}, delta={wk2_delta})",
        wk2_maturity == "thin" and wk2_window == 1.0 and pd.notna(wk2_delta),
    ))

    real_thin = [s for s in all_backfill_stories if s["methodology_maturity"] == "thin"]
    real_developing = [s for s in all_backfill_stories if s["methodology_maturity"] == "developing"]
    real_confirmed = [s for s in all_backfill_stories if s["methodology_maturity"] == "confirmed"]
    results.append(check(
        f"real 'thin' stories exist (games_played 2-3, window=1) and every one is correctly shaped "
        f"(got {len(real_thin)})",
        len(real_thin) > 0 and all(
            s["methodology"]["trend_window_games"] == 1 and s["methodology"]["games_played"] in (2, 3)
            and s["methodology"]["baseline_type"] == "expanding_season_mean"
            for s in real_thin
        ),
    ))
    results.append(check(
        f"real 'developing' stories exist (games_played 4-8, window=3) and every one is correctly shaped "
        f"(got {len(real_developing)})",
        len(real_developing) > 0 and all(
            s["methodology"]["trend_window_games"] == 3 and 4 <= s["methodology"]["games_played"] <= 8
            for s in real_developing
        ),
    ))
    results.append(check(
        f"real 'confirmed' stories exist (games_played >8, window=3) and every one is correctly shaped "
        f"(got {len(real_confirmed)})",
        len(real_confirmed) > 0 and all(
            s["methodology"]["trend_window_games"] == 3 and s["methodology"]["games_played"] > 8
            for s in real_confirmed
        ),
    ))
    results.append(check(
        "no real story anywhere in the backfill has games_played 0 or 1 (structurally impossible by the schedule's "
        "own design, not just a convention)",
        all(s["methodology"]["games_played"] >= 2 for s in all_backfill_stories),
    ))
    thin_example = real_thin[0]
    results.append(check(
        f"a real 'thin' story's narrative text says '1 game' (singular), never '1 games' or a hardcoded '3 games' "
        f"(got what_changed[0]={thin_example['what_changed'][0]['observation']!r})",
        "1 game" in thin_example["what_changed"][0]["observation"] and "1 games" not in thin_example["what_changed"][0]["observation"],
    ))
    results.append(check(
        f"that same 'thin' story's time_window field also reflects the real window used, not a hardcoded 3 "
        f"(got {thin_example['time_window']!r})",
        "last 1 game " in thin_example["time_window"],
    ))
    confirmed_example = real_confirmed[0]
    results.append(check(
        f"a real 'confirmed' story's narrative text correctly says '3 games' (plural) (got "
        f"what_changed[0]={confirmed_example['what_changed'][0]['observation']!r})",
        "3 games" in confirmed_example["what_changed"][0]["observation"],
    ))

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
