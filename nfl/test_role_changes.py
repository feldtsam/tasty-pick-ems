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
from role_changes import CONFIG, build_role_changes_stories

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
            "Rachaad White's related_players correctly names Bucky Irving as the causal injury",
            any(r["player_name"] == "Bucky Irving" and r["status"] == "Out" for r in white["related_players"]),
        ))
        results.append(check("Rachaad White's headline names Bucky Irving", "Bucky Irving" in white["headline"]))

    washington = by_name.get("Parker Washington")
    if washington:
        results.append(check(
            "Parker Washington's related_players correctly names Brian Thomas Jr. as the causal injury",
            any(r["player_name"] == "Brian Thomas Jr." and r["status"] == "Out" for r in washington["related_players"]),
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
    # related_players — two genuinely different relationship types,
    # each internally consistent.
    # ============================================================
    opportunity_stories = [st for st in wk10 if st["trend_direction"] == "opportunity-driven"]
    results.append(check(
        "every opportunity-driven story's related_players uses the causal injury relationship",
        all(all(r["relationship"] == "injured_ahead_on_depth_chart" for r in st["related_players"]) for st in opportunity_stories),
    ))
    trend_stories = [st for st in wk10 if st["trend_direction"] == "role-trend-driven" and st["related_players"]]
    results.append(check(
        "every role-trend-driven story's related_players uses the role-competition relationship, same team, excludes self",
        len(trend_stories) > 0
        and all(
            all(
                r["relationship"] == "same_position_group_competition" and r["team"] == st["entity"]["team"]
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

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
