"""
Tests for intelligence_schema.py + market_intelligence.py — the first
NFL Intelligence family and the shared story-object schema the other
three (Role Changes, Defensive Trends, Coaching Trends) will reuse.

Two kinds of checks, deliberately: schema-genericity checks (does
build_story actually work for a shape that ISN'T Market Intelligence's
own, proving the schema isn't secretly Market-specific) use small
synthetic fixtures; Market Intelligence's own story content is checked
against REAL data (the same cached NE@SEA snapshot already validated
earlier this session), not synthetic price data — the whole point of
sample-size honesty is that it has to hold on a real, currently-thin
real-world market, not just a contrived test case.

Run: python3 nfl/test_market_intelligence.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))

import pandas as pd

from intelligence_schema import STORY_FIELDS, build_story
from market_intelligence import CONFIG, build_market_intelligence_stories

CACHED_EVENT_PATH = Path(
    "/private/tmp/claude-501/-Users-samfeldt-Claude-Code/63cf71ee-ff8a-4b41-9fc4-0e0f4d5c91b0/scratchpad/ne_sea_attd_raw.json"
)


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


if __name__ == "__main__":
    results = []

    # ============================================================
    # Schema genericity — build_story must work for a shape that is
    # NOT Market Intelligence's own (proves the schema doesn't secretly
    # assume "entity is always a player" or similar).
    # ============================================================
    try:
        defense_story = build_story(
            intelligence_family="defensive_trends", entity={"type": "defense", "team": "SEA"},
            headline="Test", story="Test story.", primary_signal={"name": "allowed_rz_tds_last3", "value": 4.0},
            supporting_evidence=["x"], trend_direction="worsening", trend_strength=70.0,
            sample_size=6, completeness=90.0, confidence=85.0, time_window="Last 3 games",
            related_players=[],
        )
        results.append(check("build_story works for a non-player entity type (defense), proving genericity", True))
    except Exception as e:
        results.append(check(f"build_story works for a non-player entity type (defense), proving genericity ({e})", False))

    missing_field_raised = False
    try:
        build_story(intelligence_family="market")
    except ValueError:
        missing_field_raised = True
    results.append(check("build_story raises on missing required fields rather than returning a partial story", missing_field_raised))

    extra_field_raised = False
    try:
        build_story(**{f: None for f in STORY_FIELDS}, made_up_field="x")
    except ValueError:
        extra_field_raised = True
    results.append(check("build_story raises on an unexpected field not in the shared schema", extra_field_raised))

    # ============================================================
    # Real Market Intelligence stories from real data
    # ============================================================
    if not CACHED_EVENT_PATH.exists():
        print(f"SKIPPED real-data checks — {CACHED_EVENT_PATH} not present in this environment.")
        stories = []
    else:
        import nfl_data_py as nfl
        from market_value import match_attd_players, parse_attd_event, snapshot_scoring_inputs
        from scoring import CONFIG as SCORING_CONFIG
        from scoring import score_market_value

        event = json.loads(CACHED_EVENT_PATH.read_text())
        season = 2026
        seasonal_rosters = nfl.import_seasonal_rosters([season])
        team_desc = nfl.import_team_desc()
        parsed = parse_attd_event(event)
        matched, _unmatched = match_attd_players(parsed, seasonal_rosters, team_desc, season)
        snap = snapshot_scoring_inputs(matched)
        snap["season"] = season
        snap["week"] = 1
        scored = score_market_value(snap, SCORING_CONFIG)

        stories = build_market_intelligence_stories(scored)
        results.append(check(f"real data produces one story per real matched player (expect 20)", len(stories) == 20))

        # every field genuinely populated, not a placeholder
        s = stories[0]
        results.append(check("every schema field is present on a real story", all(f in s for f in STORY_FIELDS)))
        results.append(check("headline is real, non-empty text", isinstance(s["headline"], str) and len(s["headline"]) > 10))
        results.append(check("story is real, non-empty text distinct from the headline", s["story"] != s["headline"] and len(s["story"]) > 20))
        results.append(check("primary_signal has a real name+value pair", s["primary_signal"]["name"] == "market_value_score" and isinstance(s["primary_signal"]["value"], float)))
        results.append(check("supporting_evidence has multiple real, concrete facts", len(s["supporting_evidence"]) >= 3))

        # sample-size honesty (section: "Don't let a thin-book player
        # generate as confident a headline as a well-covered one")
        real_n_books = {st["sample_size"] for st in stories}
        results.append(check("real captured data is genuinely thin (n_books=1 for every real story) -- confirmed, not assumed", real_n_books == {1}))
        results.append(check(
            "every real (thin) story's language is honestly hedged (contains a thin/early qualifier)",
            all(any(w in st["headline"].lower() or w in st["story"].lower() for w in ("early", "one book", "thin")) for st in stories),
        ))
        thin_completeness = {round(st["completeness"], 1) for st in stories}
        results.append(check(
            "thin (1-book) real stories all show a penalized completeness, not a false-confident 100",
            all(c < 100.0 for c in thin_completeness),
        ))

        # related_players correctness
        jsn = next(st for st in stories if st["entity"]["player_name"] == "Jaxon Smith-Njigba")
        results.append(check("related_players excludes the entity itself", all(r["player_id"] != jsn["entity"]["player_id"] for r in jsn["related_players"])))
        results.append(check("related_players is scoped to the SAME team only", all(r["team"] == jsn["entity"]["team"] for r in jsn["related_players"])))
        results.append(check("related_players is capped, not a dump of the entire game", len(jsn["related_players"]) <= 5))

        # trend_direction / trend_strength are honest standing reads
        favorite = next(st for st in stories if st["primary_signal"]["value"] == max(st["primary_signal"]["value"] for st in stories))
        longshot = next(st for st in stories if st["primary_signal"]["value"] == min(st["primary_signal"]["value"] for st in stories))
        results.append(check("the highest market_value_score in the real pool reads as market-favored", favorite["trend_direction"] == "market-favored"))
        results.append(check("the lowest market_value_score in the real pool reads as market-longshot", longshot["trend_direction"] == "market-longshot"))
        results.append(check("trend_strength is near its max for the most extreme real reading", longshot["trend_strength"] >= 90.0))

    # ============================================================
    # Synthetic contrast case: a well-covered (multi-book) reading must
    # produce different (more confident, less hedged) output than a
    # thin one, given real data today has no real multi-book example to
    # demonstrate this against -- clearly labeled synthetic, not a claim
    # about a real player's real coverage.
    # ============================================================
    synthetic = pd.DataFrame([
        {
            "event_id": "TEST", "commence_time": "2026-09-10T00:15:00Z",
            "home_team": "Seattle Seahawks", "away_team": "New England Patriots",
            "player_id": "SYN1", "player_name_raw": "Synthetic Well-Covered Player", "team": "SEA",
            "position_group": "WR", "n_books": 6, "best_price": -110, "best_book": "DraftKings",
            "consensus_implied_probability": 0.52, "consensus_price_american": -108,
            "season": 2026, "week": 1,
        },
        {
            "event_id": "TEST", "commence_time": "2026-09-10T00:15:00Z",
            "home_team": "Seattle Seahawks", "away_team": "New England Patriots",
            "player_id": "SYN2", "player_name_raw": "Synthetic Thin Player", "team": "SEA",
            "position_group": "WR", "n_books": 1, "best_price": -110, "best_book": "DraftKings",
            "consensus_implied_probability": 0.52, "consensus_price_american": -108,
            "season": 2026, "week": 1,
        },
    ])
    from scoring import score_market_value as _smv
    from scoring import CONFIG as _SC
    synthetic_scored = _smv(synthetic, _SC)
    synthetic_stories = build_market_intelligence_stories(synthetic_scored)
    well_covered = next(st for st in synthetic_stories if st["entity"]["player_name"] == "Synthetic Well-Covered Player")
    thin = next(st for st in synthetic_stories if st["entity"]["player_name"] == "Synthetic Thin Player")
    results.append(check(
        "SYNTHETIC: identical market_value_score, but a well-covered (6-book) reading shows HIGHER completeness than a thin (1-book) one",
        well_covered["completeness"] > thin["completeness"],
    ))
    results.append(check(
        "SYNTHETIC: the well-covered headline uses confident language, not hedged 'early/thin' language",
        not any(w in well_covered["headline"].lower() for w in ("early", "one book")),
    ))

    # ============================================================
    # time_window: the real production-blocking bug this task fixes --
    # Lovable's real schema caps this field at 100 chars, and the old
    # "(not a trend — see module docstring)" aside pushed every real
    # Market Intelligence write past that cap. Confirmed here that the
    # fix (a) actually drops that internal aside, (b) still clears the
    # cap even at the two real longest real NFL team names paired
    # together (not just this synthetic SEA/NE matchup), and (c) the
    # real DST-aware ET kickoff conversion is genuinely correct, not
    # just plausible-looking -- this exact synthetic commence_time
    # ("2026-09-10T00:15:00Z", a real TNF-shaped late kickoff that
    # crosses into the next UTC calendar day) is a real, non-trivial
    # case for that conversion, not the early-Sunday happy path.
    # ============================================================
    results.append(check(
        f"time_window no longer contains the internal, non-user-appropriate 'module docstring' aside (got {well_covered['time_window']!r})",
        "module docstring" not in well_covered["time_window"] and "not a trend" not in well_covered["time_window"],
    ))
    results.append(check(
        f"time_window correctly rolls the UTC-crossing-midnight kickoff back to the real LOCAL Eastern date (got {well_covered['time_window']!r})",
        well_covered["time_window"] == "Live snapshot, New England Patriots @ Seattle Seahawks, kickoff Sep 9, 8:15 PM ET",
    ))

    from market_intelligence import _format_kickoff_et
    real_longest_pair_time_window = (
        f"Live snapshot, Washington Commanders @ Jacksonville Jaguars, "
        f"kickoff {_format_kickoff_et('2026-11-08T18:00:00Z')}"
    )
    results.append(check(
        f"time_window clears the real 100-char cap even at the two real longest real NFL team names paired together "
        f"(got {len(real_longest_pair_time_window)} chars: {real_longest_pair_time_window!r})",
        len(real_longest_pair_time_window) < 100,
    ))
    results.append(check(
        "the real November DST transition is handled correctly (EST, not a stale EDT offset) for that same longest-pair case",
        real_longest_pair_time_window.endswith("kickoff Nov 8, 1:00 PM ET"),
    ))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
