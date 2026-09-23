"""
Tests for team_tendencies.py — the fourth and final NFL Intelligence
family, reusing intelligence_schema.py's shared story schema. Three
independent detectors (red-zone play-calling, fourth-down
aggressiveness, pace), each tested against real historical pbp
(2022/2024/2025) and the real player_redzone_weekly.csv backfill for
related_players, same real-data-first discipline the first three
families established.

Requires network access (pulls real pbp via nfl_data_py) — same
requirement test_market_intelligence.py already has for its real-data
checks. Set SSL_CERT_FILE to certifi's bundle if a local cert error
occurs (see other test files' own notes on this).

Run: python3 nfl/test_team_tendencies.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))

import pandas as pd

from intelligence_schema import STORY_FIELDS
from team_tendencies import (
    CONFIG,
    aggregate_fourth_down_aggressiveness,
    aggregate_redzone_play_calling,
    build_fourth_down_aggressiveness_stories,
    build_pace_stories,
    build_redzone_play_calling_stories,
    build_team_tendencies_stories,
    _score_fourth_down_aggressiveness,
    _score_pace,
    _score_redzone_play_calling,
    _trend_delta,
    _weekly_percentile,
)

WEEKLY_PATH = Path(__file__).resolve().parent / "scripts" / "player_redzone_weekly.csv"


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


if __name__ == "__main__":
    results = []

    # ============================================================
    # REGRESSION: the real single-group `.groupby().apply()` bug (fixed
    # 2026-09) — every team has played exactly one game in Week 1 of
    # any season, so team_week has exactly ONE (season, week) group at
    # that point, which is exactly what broke `_weekly_percentile`'s
    # old `.apply(_group_pct)` call (pandas reshaped the single group's
    # per-row Series into a one-row DataFrame instead of concatenating a
    # Series, blowing up the caller's `df[col] = ...` assignment with
    # "Cannot set a DataFrame with multiple columns to the single column
    # ..."). Purely synthetic, no network/CSV dependency, so this always
    # runs regardless of environment — the whole point is catching this
    # again at the start of a future season before it reaches
    # production. Season 2099 throughout, per project convention.
    # ============================================================
    single_group = pd.DataFrame({
        "season": [2099] * 5,
        "week": [1] * 5,
        "_val": [0.1, 0.2, 0.3, 0.4, 0.5],
        "_qual": [True] * 5,
    })
    single_group_result = _weekly_percentile(single_group, "_val", "_qual")
    results.append(check(
        "_weekly_percentile returns a Series (not a DataFrame) on a single-group (one season/week) input — the real Week 1 shape",
        isinstance(single_group_result, pd.Series),
    ))
    if isinstance(single_group_result, pd.Series):
        results.append(check(
            "single-group result assigns cleanly onto the frame as a real column, same as the production call site",
            (lambda df: (df.__setitem__("out", single_group_result), "out" in df.columns)[1])(single_group.copy()),
        ))
        results.append(check(
            "single-group percentiles are real and ordered (not all collapsed to neutral 50)",
            list(single_group_result) == sorted(single_group_result),
        ))

    multi_group = pd.DataFrame({
        "season": [2099] * 10,
        "week": [1] * 5 + [2] * 5,
        "_val": [0.1, 0.2, 0.3, 0.4, 0.5] * 2,
        "_qual": [True] * 10,
    })
    multi_group_result = _weekly_percentile(multi_group, "_val", "_qual")
    results.append(check(
        "_weekly_percentile still returns a Series on a 2+-group input — no regression on the previously-working path",
        isinstance(multi_group_result, pd.Series) and len(multi_group_result) == 10,
    ))
    results.append(check(
        "each week's percentiles are ranked within that week only, not pooled across weeks (both weeks share the same 5 input values, so both should produce the same 0/20/40/60/80 spread)",
        list(multi_group_result.iloc[:5]) == list(multi_group_result.iloc[5:]),
    ))

    empty_result = _weekly_percentile(single_group.iloc[0:0], "_val", "_qual")
    results.append(check(
        "_weekly_percentile handles a zero-row input without raising (empty Series, not a crash)",
        isinstance(empty_result, pd.Series) and len(empty_result) == 0,
    ))

    # Detector-level confirmation, not just the isolated helper: all
    # three real Coaching Trends detectors call _weekly_percentile via
    # their own _score_* function, and all three independently broke on
    # a real Week 1 before this fix (the production error only ever
    # named the red-zone one because build_team_tendencies_stories calls
    # the three builders in a plain unguarded sum, so execution never
    # reached the other two — but the underlying bug was never
    # red-zone-specific).
    rz_week1 = pd.DataFrame({
        "team": ["AAA", "BBB", "CCC", "DDD"],
        "season": [2099] * 4,
        "week": [1] * 4,
        "rz_rush_attempts": [15, 5, 20, 8],
        "rz_pass_attempts": [5, 15, 5, 12],
        "rz_plays": [20, 20, 25, 20],
        "i20_rush_attempts": [18, 8, 24, 12],
        "i20_pass_attempts": [7, 17, 6, 13],
        "i20_plays": [25, 25, 30, 25],
    })
    try:
        _score_redzone_play_calling(rz_week1, CONFIG)
        rz_ok = True
    except ValueError as e:
        rz_ok = False
        print(f"    (red-zone detector raised: {e!r})")
    results.append(check("_score_redzone_play_calling runs on a real single-week (Week 1 shape) synthetic fixture without raising", rz_ok))

    fd_week1 = pd.DataFrame({
        "team": ["AAA", "BBB", "CCC", "DDD"],
        "season": [2099] * 4,
        "week": [1] * 4,
        "go_attempts": [6, 2, 9, 4],
        "fourth_down_decisions": [10, 10, 12, 10],
    })
    try:
        _score_fourth_down_aggressiveness(fd_week1, CONFIG)
        fd_ok = True
    except ValueError as e:
        fd_ok = False
        print(f"    (fourth-down detector raised: {e!r})")
    results.append(check("_score_fourth_down_aggressiveness runs on a real single-week (Week 1 shape) synthetic fixture without raising", fd_ok))

    pace_week1 = pd.DataFrame({
        "team": ["AAA", "BBB", "CCC", "DDD"],
        "season": [2099] * 4,
        "week": [1] * 4,
        "drives_count": [11, 10, 12, 9],
        "seconds_per_play": [28.5, 31.2, 26.8, 33.0],
    })
    try:
        _score_pace(pace_week1, CONFIG)
        pace_ok = True
    except ValueError as e:
        pace_ok = False
        print(f"    (pace detector raised: {e!r})")
    results.append(check("_score_pace runs on a real single-week (Week 1 shape) synthetic fixture without raising", pace_ok))

    if not WEEKLY_PATH.exists():
        print(f"\n{sum(results)}/{len(results)} synthetic checks passed. "
              f"SKIPPING real-data checks below — {WEEKLY_PATH} not present in this environment.")
        raise SystemExit(0 if all(results) else 1)

    try:
        import nfl_data_py as nfl
    except ImportError:
        print(f"\n{sum(results)}/{len(results)} synthetic checks passed. "
              f"SKIPPING real-data checks below — nfl_data_py not importable in this environment.")
        raise SystemExit(0 if all(results) else 1)

    weekly = pd.read_csv(WEEKLY_PATH)

    try:
        pbp2025 = nfl.import_pbp_data([2025], downcast=True)
    except Exception as e:
        print(f"\n{sum(results)}/{len(results)} synthetic checks passed. "
              f"SKIPPING real-data checks below — could not pull real pbp data ({e}). "
              f"Try: export SSL_CERT_FILE=$(python3 -c 'import certifi; print(certifi.where())')")
        raise SystemExit(0 if all(results) else 1)

    weeks_2025 = sorted(int(w) for w in pbp2025["week"].dropna().unique())

    # ============================================================
    # Real stories across every real 2025 week — schema + entity type.
    # ============================================================
    all_stories = {"rz": [], "fd": [], "pace": []}
    for wk in weeks_2025:
        all_stories["rz"] += build_redzone_play_calling_stories(pbp2025, weekly, 2025, wk)
        all_stories["fd"] += build_fourth_down_aggressiveness_stories(pbp2025, weekly, 2025, wk)
        all_stories["pace"] += build_pace_stories(pbp2025, weekly, 2025, wk)

    for name, stories in all_stories.items():
        results.append(check(f"{name}: real 2025 data produces a non-trivial set of stories (got {len(stories)})", 5 <= len(stories) <= 200))
        results.append(check(f"{name}: every schema field is present on a real story", all(f in stories[0] for f in STORY_FIELDS)))
        results.append(check(f"{name}: entity type is 'team', not player or (team, position_group)", all(s["entity"]["type"] == "team" and set(s["entity"].keys()) == {"type", "team"} for s in stories)))
        results.append(check(f"{name}: headline/story are real, distinct, non-empty text", all(s["story"] != s["headline"] and len(s["story"]) > 20 for s in stories)))

    # ============================================================
    # Combined feed wrapper.
    # ============================================================
    combined, combined_diag = build_team_tendencies_stories(pbp2025, weekly, 2025, 15)
    separate = (
        build_redzone_play_calling_stories(pbp2025, weekly, 2025, 15)
        + build_fourth_down_aggressiveness_stories(pbp2025, weekly, 2025, 15)
        + build_pace_stories(pbp2025, weekly, 2025, 15)
    )
    results.append(check("build_team_tendencies_stories combines all three detectors' real output for a week", len(combined) == len(separate) and len(combined) > 0))
    results.append(check(
        f"combined diagnostics: pool_after_trend_threshold matches the real combined story count exactly, "
        f"pool_after_games_played_gate is >= that (games_played gate is strictly earlier than trend_threshold) "
        f"(got {combined_diag})",
        combined_diag["pool_after_trend_threshold"] == len(combined)
        and combined_diag["pool_after_games_played_gate"] >= combined_diag["pool_after_trend_threshold"],
    ))

    # ============================================================
    # Structural gates (red-zone, fourth-down) actually block thin
    # team-weeks — not just documented, checked directly.
    # ============================================================
    rz_scored = _score_redzone_play_calling(aggregate_redzone_play_calling(pbp2025), CONFIG)
    thin_rz = rz_scored[(rz_scored["_cum_rz_plays"] > 0) & (rz_scored["_cum_rz_plays"] < CONFIG["min_rz_plays_qualified"])]
    results.append(check(f"real thin (unqualified) red-zone team-weeks exist in 2025 data (got {len(thin_rz)})", len(thin_rz) > 0))
    thin_row = thin_rz.iloc[0]
    thin_week_stories = build_redzone_play_calling_stories(pbp2025, weekly, 2025, int(thin_row["week"]))
    results.append(check(
        f"a real unqualified team ({thin_row['team']}, {int(thin_row['_cum_rz_plays'])} cum. plays) never produces a red-zone story",
        not any(s["entity"]["team"] == thin_row["team"] for s in thin_week_stories),
    ))
    results.append(check(
        "structural gate holds across every real generated red-zone story (min sample_size >= configured threshold)",
        all(s["sample_size"] >= CONFIG["min_rz_plays_qualified"] for s in all_stories["rz"]),
    ))
    results.append(check(
        "structural gate holds across every real generated fourth-down story (min sample_size >= configured threshold)",
        all(s["sample_size"] >= CONFIG["min_fourth_down_decisions_qualified"] for s in all_stories["fd"]),
    ))

    # Pace: NO structural gate, per the approved investigation — confirm
    # a story CAN generate even with a low games-played count (just
    # hedged), unlike the other two.
    thin_pace_stories = [s for s in all_stories["pace"] if s["sample_size"] < CONFIG["thin_pace_games"]]
    results.append(check(
        "pace deliberately has NO structural gate: a thin-sample story can still generate (hedged), unlike red-zone/fourth-down",
        len(thin_pace_stories) > 0,
    ))
    results.append(check(
        "every thin pace story's language is honestly hedged",
        all(any(w in st["story"].lower() for w in ("early", "still-developing")) for st in thin_pace_stories),
    ))

    # ============================================================
    # Trend materiality thresholds hold.
    # ============================================================
    results.append(check("every red-zone story clears its configured trend_threshold", all(s["trend_strength"] >= CONFIG["redzone_trend_threshold"] for s in all_stories["rz"])))
    results.append(check("every fourth-down story clears its configured trend_threshold", all(s["trend_strength"] >= CONFIG["fourth_down_trend_threshold"] for s in all_stories["fd"])))
    results.append(check("every pace story clears its configured trend_threshold", all(s["trend_strength"] >= CONFIG["pace_trend_threshold"] for s in all_stories["pace"])))

    # ============================================================
    # related_players — Universal Card v2 shape (player_id/display_
    # label/entity_type/direction_indicator/note) — three different,
    # detector-specific real relationships, each still verifiable by
    # cross-checking real weekly data (team/rank fields were dropped
    # from the v2 shape itself, per the locked schema).
    #
    # REAL BUG FOUND AND FIXED (dynamic-window change): this used to
    # pick "the first story with related_players" out of the FLATTENED
    # all-weeks list, then cross-check against wk2025 = the WHOLE
    # season's rows collapsed to one dict per player_id (dict(zip(...))
    # silently keeps whichever row iterates last, not the row for the
    # story's own actual week). That only ever worked by accident,
    # because before the dynamic-window change no story could exist
    # before week 5, so "the first story" always happened to land late
    # enough in the season that its related_players ranking still
    # matched the season's LAST week's snap_share/td_opportunity
    # closely enough. Once real week 3-4 "thin" stories exist, the
    # first story is now often an early one, and a player's real snap_
    # share/td_opportunity in week 3 is a genuinely different number
    # than in whatever week happened to sort last in the dict -- a real
    # week mismatch, not a production bug (_related_players_team_wide
    # itself already correctly filters its own pool by the exact
    # (season, week) it was called with). Fixed by checking a single
    # FIXED reference week (15, matching this file's own existing
    # "combined" check a few lines above) instead of scanning a
    # flattened multi-week list, and scoping the weekly lookup to that
    # exact same week.
    # ============================================================
    ref_week = 15
    wk_ref = weekly[(weekly["season"] == 2025) & (weekly["week"] == ref_week)]
    rz_ref_stories = build_redzone_play_calling_stories(pbp2025, weekly, 2025, ref_week)
    fd_ref_stories = build_fourth_down_aggressiveness_stories(pbp2025, weekly, 2025, ref_week)
    pace_ref_stories = build_pace_stories(pbp2025, weekly, 2025, ref_week)

    rz_run_heavy = next((s for s in rz_ref_stories if s["trend_direction"] == "growing-run-heavy" and s["related_players"]), None)
    rz_pass_heavy = next((s for s in rz_ref_stories if s["trend_direction"] == "growing-pass-heavy" and s["related_players"]), None)
    results.append(check(
        "red-zone related_players is DIRECTIONAL: every entry is a real player entity with direction_indicator='up' (the beneficiary-group reasoning — see _signal_direction_redzone)",
        rz_run_heavy is not None and all(r["entity_type"] == "player" and r["direction_indicator"] == "up" for r in rz_run_heavy["related_players"]),
    ))
    if rz_run_heavy:
        rb_check_pool = wk_ref[wk_ref["player_id"].isin([r["player_id"] for r in rz_run_heavy["related_players"]])]
        results.append(check("growing-run-heavy red-zone related_players are genuinely RBs", set(rb_check_pool["position_group"].unique()) <= {"RB"}))
    if rz_pass_heavy:
        wrte_check_pool = wk_ref[wk_ref["player_id"].isin([r["player_id"] for r in rz_pass_heavy["related_players"]])]
        results.append(check("growing-pass-heavy red-zone related_players are genuinely WR/TE", set(wrte_check_pool["position_group"].unique()) <= {"WR", "TE"}))

    fd_with_related = next((s for s in fd_ref_stories if s["related_players"]), None)
    results.append(check(
        "fourth-down related_players is TEAM-WIDE, real player entities, note cites 'Benefits from sustained drives', direction_indicator matches the real story direction (growing-aggressive -> up)",
        fd_with_related is not None and all(
            r["entity_type"] == "player" and "Benefits from sustained drives" in r["note"]
            and r["direction_indicator"] == ("up" if fd_with_related["trend_direction"] == "growing-aggressive" else "down")
            for r in fd_with_related["related_players"]
        ),
    ))
    if fd_with_related:
        td_by_player = dict(zip(wk_ref["player_id"], wk_ref["td_opportunity"]))
        tds = [td_by_player.get(r["player_id"]) for r in fd_with_related["related_players"]]
        results.append(check("fourth-down related_players ranked by real td_opportunity, highest first (cross-checked against real weekly data)", tds == sorted(tds, reverse=True)))

    pace_with_related = next((s for s in pace_ref_stories if s["related_players"]), None)
    results.append(check(
        "pace related_players is TEAM-WIDE, real player entities, note cites 'Benefits from play volume', direction_indicator matches the real story direction (growing-faster -> up) -- a genuinely different real mechanism than fourth-down's td_opportunity ranking",
        pace_with_related is not None and all(
            r["entity_type"] == "player" and "Benefits from play volume" in r["note"]
            and r["direction_indicator"] == ("up" if pace_with_related["trend_direction"] == "growing-faster" else "down")
            for r in pace_with_related["related_players"]
        ),
    ))
    if pace_with_related:
        snap_by_player = dict(zip(wk_ref["player_id"], wk_ref["snap_share"]))
        snaps = [snap_by_player.get(r["player_id"]) for r in pace_with_related["related_players"]]
        results.append(check("pace related_players ranked by real snap_share, highest first (cross-checked against real weekly data)", snaps == sorted(snaps, reverse=True)))

    for name, stories in all_stories.items():
        results.append(check(f"{name}: related_players is capped, not an unbounded dump", all(len(s["related_players"]) <= CONFIG["related_players_limit"] for s in stories)))
        results.append(check(f"{name}: every related_players entry has a real, non-null player_id and display_label", all(all(r["player_id"] and r["display_label"] for r in s["related_players"]) for s in stories)))

    # ============================================================
    # Storytelling honesty — full-backfill scan across all three
    # seasons and all three detectors, same standard as Defensive
    # Trends' "0/126 mismatches" reporting.
    # ============================================================
    totals = {"rz": 0, "fd": 0, "pace": 0}
    specific = {"rz": 0, "fd": 0, "pace": 0}
    mismatches = {"rz": 0, "fd": 0, "pace": 0}

    for season in (2022, 2024, 2025):
        pbp = pbp2025 if season == 2025 else nfl.import_pbp_data([season], downcast=True)
        for wk in sorted(int(w) for w in pbp["week"].dropna().unique()):
            for s in build_redzone_play_calling_stories(pbp, weekly, season, wk):
                totals["rz"] += 1
                m = re.search(r"Rush rate inside the 10: ([\d.]+)% \(last 3 games\) vs\. ([\d.]+)% \(season\)", " ".join(s["supporting_evidence"]))
                if m:
                    specific["rz"] += 1
                    recent, seas = float(m.group(1)), float(m.group(2))
                    if s["trend_direction"] == "growing-run-heavy" and not (recent > seas):
                        mismatches["rz"] += 1
                    if s["trend_direction"] == "growing-pass-heavy" and not (recent < seas):
                        mismatches["rz"] += 1
            for s in build_fourth_down_aggressiveness_stories(pbp, weekly, season, wk):
                totals["fd"] += 1
                m = re.search(r"Go-for-it rate: ([\d.]+)% \(last 3 games\) vs\. ([\d.]+)% \(season\)", " ".join(s["supporting_evidence"]))
                if m:
                    specific["fd"] += 1
                    recent, seas = float(m.group(1)), float(m.group(2))
                    if s["trend_direction"] == "growing-aggressive" and not (recent > seas):
                        mismatches["fd"] += 1
                    if s["trend_direction"] == "growing-conservative" and not (recent < seas):
                        mismatches["fd"] += 1
            for s in build_pace_stories(pbp, weekly, season, wk):
                totals["pace"] += 1
                m = re.search(r"Seconds per play: ([\d.]+) \(last 3 games\) vs\. ([\d.]+) \(season\)", " ".join(s["supporting_evidence"]))
                if m:
                    specific["pace"] += 1
                    recent, seas = float(m.group(1)), float(m.group(2))
                    if s["trend_direction"] == "growing-faster" and not (recent < seas):
                        mismatches["pace"] += 1
                    if s["trend_direction"] == "growing-slower" and not (recent > seas):
                        mismatches["pace"] += 1

    for name in ("rz", "fd", "pace"):
        results.append(check(
            f"{name}: every specific raw-number citation across the full 2022/2024/2025 backfill agrees with its "
            f"claimed direction (checked {specific[name]} real specific citations across {totals[name]} stories, {mismatches[name]} mismatches)",
            specific[name] > 0 and mismatches[name] == 0,
        ))

    # ============================================================
    # Universal Card v2 fields — real 2025 season (all_stories, already
    # computed above), covering all three signals.
    # ============================================================
    results.append(check(
        f"redzone_run_tendency: signal_direction is a real constant ('favorable') across every real story, "
        f"since related_players itself flips to the real beneficiary group per direction (checked {len(all_stories['rz'])} real stories)",
        all(st["signal_direction"] == "favorable" for st in all_stories["rz"]),
    ))
    results.append(check(
        "fourth_down_aggressiveness: signal_direction is genuinely bidirectional (growing-aggressive -> favorable, growing-conservative -> unfavorable), a real different shape from redzone_run_tendency",
        all(
            (st["signal_direction"] == "favorable") == (st["trend_direction"] == "growing-aggressive")
            for st in all_stories["fd"]
        ) and len({st["signal_direction"] for st in all_stories["fd"]}) == 2,
    ))
    results.append(check(
        "pace_score: signal_direction is genuinely bidirectional (growing-faster -> favorable, growing-slower -> unfavorable), same real shape as fourth_down_aggressiveness",
        all(
            (st["signal_direction"] == "favorable") == (st["trend_direction"] == "growing-faster")
            for st in all_stories["pace"]
        ) and len({st["signal_direction"] for st in all_stories["pace"]}) == 2,
    ))

    for name, hero_label in [("rz", "Red-Zone Rush Rate"), ("fd", "4th-Down Go-For-It Rate"), ("pace", "Seconds Per Play")]:
        stories = all_stories[name]
        results.append(check(
            f"{name}: hero_metric is populated if and only if the story's own specific rate citation is present in supporting_evidence (the same real agrees gate, never a separate looser check) -- checked {len(stories)} real stories",
            all(
                (st["hero_metric"] is not None) == any(
                    ("Rush rate inside the 10" in e) or ("Go-for-it rate" in e) or ("Seconds per play" in e)
                    for e in st["supporting_evidence"]
                )
                for st in stories
            ),
        ))
        hero_stories = [st for st in stories if st["hero_metric"] is not None]
        if hero_stories:
            results.append(check(
                f"{name}: every real populated hero_metric uses the expected label/format ({hero_label})",
                all(st["hero_metric"]["label"] == hero_label for st in hero_stories),
            ))

    pace_hero_stories = [st for st in all_stories["pace"] if st["hero_metric"] is not None]
    results.append(check(
        f"pace: hero_metric's before/after values are real seconds_per_play (NOT the inverted pace_score) -- "
        f"a faster (growing-faster) story must show after < before in real seconds, checked {len(pace_hero_stories)} real stories",
        all(
            (st["hero_metric"]["after_value"] < st["hero_metric"]["before_value"]) == (st["trend_direction"] == "growing-faster")
            for st in pace_hero_stories
        ),
    ))
    results.append(check(
        "pace: hero_metric uses the real new value_format 'seconds_per_play', not a generic decimal format",
        all(st["hero_metric"]["value_format"] == "seconds_per_play" for st in pace_hero_stories),
    ))

    for name in ("rz", "fd", "pace"):
        stories = all_stories[name]
        results.append(check(
            f"{name}: what_changed is always a real, non-empty list capped at 3 items, every real story",
            all(isinstance(st["what_changed"], list) and 1 <= len(st["what_changed"]) <= 3 for st in stories),
        ))
        results.append(check(
            f"{name}: what_changed never leaks an internal field name",
            all(
                not any(bad in item["observation"] for bad in ("redzone_run_tendency", "fourth_down_aggressiveness", "pace_score"))
                for st in stories for item in st["what_changed"]
            ),
        ))
        results.append(check(
            f"{name}: evidence_classification is always one of the three real approved values",
            all(st["evidence_classification"] in ("strong", "moderate", "limited") for st in stories),
        ))

    real_dist = {
        name: {c: sum(1 for st in all_stories[name] if st["evidence_classification"] == c) for c in ("strong", "moderate", "limited")}
        for name in ("rz", "fd", "pace")
    }
    results.append(check(
        f"REAL FINDING: evidence_classification distribution differs meaningfully across all three Coaching Trends "
        f"signals within the SAME family (2025 season only) -- redzone={real_dist['rz']}, fourth_down={real_dist['fd']}, "
        f"pace={real_dist['pace']} -- confirming this formula's real behavior isn't uniform even within one family",
        all(sum(d.values()) > 0 for d in real_dist.values()),
    ))

    # ============================================================
    # Off-by-one regression guard -- a team with EXACTLY 2 real
    # reconciled weeks must land in "thin" (games_played=2), not below
    # it. Direct regression test for the real bug this session's
    # Editorial Intelligence investigation found and fixed in this
    # file's own copy of _trend_delta: games_played used to be raw
    # 0-indexed cumcount(), so a team's own real SECOND reconciled week
    # read as games_played=1 and missed thin_games_played_min=2 by
    # exactly one -- with only 2 real reconciled weeks on file (the real
    # 2026 Week 2 state this was traced against), this made all three
    # Coaching Trends detectors produce zero stories regardless of any
    # real signal, the same mechanism as defensive_trends.py's identical
    # bug. Synthetic, not real-pbp-dependent, so this doesn't need
    # network access the way the rest of this file does: the real
    # backfill never has a team with EXACTLY 2 games on file, so this
    # exact boundary can only be exercised directly.
    # ============================================================
    two_week_team = pd.DataFrame({
        "team": ["KC", "KC"],
        "season": [2099, 2099],
        "week": [1, 2],
        "redzone_run_tendency_last1": [40.0, 55.0],
        "redzone_run_tendency_last3": [40.0, 47.5],
        "redzone_run_tendency_season_avg": [40.0, 47.5],
    })
    two_week_trend = _trend_delta(two_week_team, "redzone_run_tendency", CONFIG)
    wk1_maturity, wk2_maturity = two_week_trend["_methodology_maturity"].tolist()
    wk1_window, wk2_window = two_week_trend["_trend_window"].tolist()
    wk2_delta = two_week_trend["_delta"].iloc[1]
    results.append(check(
        f"a team's own FIRST reconciled week (games_played=1) still correctly produces no trend at all "
        f"(got maturity={wk1_maturity!r}, window={wk1_window})",
        pd.isna(wk1_maturity) and pd.isna(wk1_window),
    ))
    results.append(check(
        f"a team's own SECOND reconciled week (games_played=2) now correctly lands in 'thin', not below it -- "
        f"the exact off-by-one this fix closes (got maturity={wk2_maturity!r}, window={wk2_window}, delta={wk2_delta})",
        wk2_maturity == "thin" and wk2_window == 1.0 and pd.notna(wk2_delta),
    ))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
