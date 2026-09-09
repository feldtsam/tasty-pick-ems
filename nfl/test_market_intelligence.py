"""
Tests for market_intelligence.py — Market Trends V1, Deviation only.

Covers exactly what the spec's own rules require be checked independently:
peer-tier grouping correctness (on-field pillars only, Market Value
excluded), the deviation eligibility floor and magnitude bands (§4),
evidence_state's independence from both signal_type and magnitude (§3b),
and freshness exclusion applied upstream of scoring (§6). All synthetic —
no cached real-event fixture, since this family's own real market snapshot
comes from nfl_price_history, which has no real rows to test against yet
(see market_value.py's own docstring).

Run: python3 nfl/test_market_intelligence.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

from intelligence_schema import STORY_FIELDS
from market_intelligence import (
    CONFIG,
    _assign_peer_tiers,
    _evidence_state_for_row,
    _freshness_gate,
    _magnitude_band,
    _peer_tier_core_score,
    build_deviation_stories,
)


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


NOW = pd.Timestamp("2026-09-14T18:00:00Z")


def _fresh_ts(minutes_ago: int) -> str:
    return (NOW - pd.Timedelta(minutes=minutes_ago)).isoformat()


def _market_row(**overrides) -> dict:
    row = {
        "player_id": "p0", "player_name_raw": "Test Player", "team": "KC",
        "position_group": "WR", "event_id": "e1", "away_team": "KC", "home_team": "DEN",
        "commence_time": "2026-09-14T20:00:00Z", "consensus_implied_probability": 0.30,
        "consensus_price_american": 233, "n_books": 3, "best_price": 240, "best_book": "DK",
        "poll_timestamp": _fresh_ts(30),
    }
    row.update(overrides)
    return row


results = []

# --- _peer_tier_core_score: excludes market_value_score, renormalizes present-only ---
pool = pd.DataFrame([
    {"td_opportunity": 90, "role_momentum": 80, "situation": 70, "market_value_score": 10},
    {"td_opportunity": 50, "role_momentum": 50, "situation": 50, "market_value_score": 90},
])
core = _peer_tier_core_score(pool)
# Weights (excluding market_value_score): td=30, role=20, situation=20 -> renormalized over 70.
expected_row0 = round((90 * 30 + 80 * 20 + 70 * 20) / 70, 1)
results.append(check(
    "core score composite excludes market_value_score entirely (row 0 unaffected by its 10 vs. row 1's 90)",
    core.iloc[0] == expected_row0,
))
results.append(check(
    "row 1's low market_value_score (90, ironically) doesn't drag its on-field-only composite down",
    core.iloc[1] == round((50 * 30 + 50 * 20 + 50 * 20) / 70, 1),
))

# Missing pillar column entirely -> still computes over whichever are present.
partial = pd.DataFrame([{"td_opportunity": 90, "role_momentum": 80}])
partial_core = _peer_tier_core_score(partial)
results.append(check(
    "a missing 'situation' column renormalizes over just td_opportunity+role_momentum, not a crash",
    partial_core.iloc[0] == round((90 * 30 + 80 * 20) / 50, 1),
))

# --- _assign_peer_tiers: too-few-real-values fallback ---
thin_pool = pd.DataFrame([
    {"position_group": "TE", "td_opportunity": 40, "role_momentum": 40, "situation": 40},
])
tiered_thin = _assign_peer_tiers(thin_pool, CONFIG)
results.append(check(
    "a position_group with only 1 real player gets its own tier-of-one, not a crash or a fabricated bucket",
    len(tiered_thin) == 1 and pd.notna(tiered_thin["_peer_tier"].iloc[0]),
))

# --- _freshness_gate (§6): stale and clock-skewed rows excluded upstream ---
fresh_and_stale = pd.DataFrame([
    _market_row(player_id="fresh", poll_timestamp=_fresh_ts(30)),
    _market_row(player_id="stale", poll_timestamp=_fresh_ts(180)),  # 3 hours, over the 2hr max
    _market_row(player_id="future", poll_timestamp=(NOW + pd.Timedelta(hours=1)).isoformat()),  # bad clock skew
])
gated = _freshness_gate(fresh_and_stale, CONFIG, now=NOW)
results.append(check(
    "freshness gate keeps only the fresh row, excludes both stale and clock-skewed-future rows",
    set(gated["player_id"]) == {"fresh"},
))

# --- _evidence_state_for_row (§3b): independent of magnitude, book-count only ---
results.append(check("0 books -> thin", _evidence_state_for_row(0, CONFIG) == "thin"))
results.append(check("1 book -> thin", _evidence_state_for_row(1, CONFIG) == "thin"))
results.append(check("NaN books -> thin", _evidence_state_for_row(float("nan"), CONFIG) == "thin"))
results.append(check("2 books -> developing", _evidence_state_for_row(2, CONFIG) == "developing"))
results.append(check("3 books -> confirmed", _evidence_state_for_row(3, CONFIG) == "confirmed"))
results.append(check("5 books -> confirmed", _evidence_state_for_row(5, CONFIG) == "confirmed"))

# --- _magnitude_band (§4) ---
results.append(check("2.9pp -> notable (below the strong threshold)", _magnitude_band(2.9, CONFIG) == "notable"))
results.append(check("5.0pp -> strong", _magnitude_band(5.0, CONFIG) == "strong"))
results.append(check("7.9pp -> strong", _magnitude_band(7.9, CONFIG) == "strong"))
results.append(check("8.0pp -> extreme", _magnitude_band(8.0, CONFIG) == "extreme"))

# ============================================================
# End-to-end: build_deviation_stories()
# ============================================================

# Two clean on-field tiers (strong ~85-90, weak ~20-30), 4 players each, all
# WR, same game. One weak-tier player is priced like a strong-tier player
# despite a weak on-field profile -- the one real, constructed deviation.
e2e_config = {**CONFIG, "n_peer_tiers": 2}
e2e_rows, e2e_weekly = [], []
for i, (name, td, role, sit, prob, price, books, ts) in enumerate([
    ("Strong A", 90, 88, 75, 0.60, -150, 3, _fresh_ts(30)),
    ("Strong B", 87, 85, 72, 0.58, -138, 3, _fresh_ts(30)),
    ("Strong C", 85, 83, 70, 0.55, -122, 3, _fresh_ts(30)),
    ("Strong D", 88, 86, 74, 0.57, -132, 3, _fresh_ts(30)),
    ("Weak A", 22, 25, 30, 0.19, 425, 3, _fresh_ts(30)),
    ("Weak B", 20, 23, 28, 0.18, 455, 3, _fresh_ts(30)),
    ("Weak C", 24, 27, 32, 0.20, 400, 3, _fresh_ts(30)),
    ("Outlier Weak-Priced-Strong", 21, 24, 29, 0.52, -108, 3, _fresh_ts(30)),
    ("Stale Weak-Priced-Strong", 21, 24, 29, 0.52, -108, 5, _fresh_ts(180)),  # same real gap, but stale
    ("No Weekly History", 0, 0, 0, 0.52, -108, 5, _fresh_ts(30)),  # excluded below, no pillar row
]):
    pid = f"e{i}"
    e2e_rows.append(_market_row(
        player_id=pid, player_name_raw=name, team="KC" if i % 2 == 0 else "DEN",
        consensus_implied_probability=prob, consensus_price_american=price, n_books=books,
        poll_timestamp=ts,
    ))
    if name != "No Weekly History":
        e2e_weekly.append({"player_id": pid, "td_opportunity": td, "role_momentum": role, "situation": sit})

market_snapshot = pd.DataFrame(e2e_rows)
weekly = pd.DataFrame(e2e_weekly)

# Freshness must be applied relative to the fixed NOW above, not real wall-clock time.
import market_intelligence as _mi
_orig_freshness_gate = _mi._freshness_gate
_mi._freshness_gate = lambda snapshot, config, now=None: _orig_freshness_gate(snapshot, config, now=NOW)
try:
    stories = build_deviation_stories(market_snapshot, weekly, e2e_config)
finally:
    _mi._freshness_gate = _orig_freshness_gate

results.append(check("exactly one real story generated (the one real, fresh, matched outlier)", len(stories) == 1))

if stories:
    s = stories[0]
    results.append(check(
        "the story's subject is the real outlier, not the stale or no-weekly-history look-alikes",
        s["entity"]["player_name"] == "Outlier Weak-Priced-Strong",
    ))
    results.append(check(
        "every field build_story() requires is present (schema didn't silently accept a partial story)",
        all(f in s for f in STORY_FIELDS),
    ))
    results.append(check("signal_type is 'deviation'", s["signal_type"] == "deviation"))
    results.append(check(
        "evidence_state is 'confirmed' (3 books), independent of the magnitude of the gap",
        s["evidence_state"] == "confirmed",
    ))
    results.append(check(
        "evidence_classification (the separate, shared cross-family field) is also set, additively",
        s["evidence_classification"] in ("strong", "moderate", "limited"),
    ))
    results.append(check(
        "deviation is signed correctly: market prices him MORE favorably than his peer tier (positive gap)",
        s["primary_signal"]["value"] > 0,
    ))
    results.append(check("trend_direction reflects the favorable direction", s["trend_direction"] == "deviation-favorable"))
    results.append(check("signal_direction (Universal Card v2) also reflects favorable", s["signal_direction"] == "favorable"))
    results.append(check(
        "hero_metric is a real before/after/delta triple (peer expectation -> observed), not null",
        s["hero_metric"] is not None
        and s["hero_metric"]["before_value"] is not None
        and s["hero_metric"]["after_value"] is not None,
    ))
    results.append(check(
        "hero_metric's delta matches primary_signal's own deviation value (no drift between the two)",
        s["hero_metric"]["delta_value"] == s["primary_signal"]["value"],
    ))
    results.append(check(
        "what_changed (STILL TO WATCH) is real, forward-looking content, not empty",
        len(s["what_changed"]) >= 1 and all(isinstance(c.get("observation"), str) and c["observation"] for c in s["what_changed"]),
    ))
    results.append(check(
        "supporting_evidence names the real market price and the real peer-tier expectation",
        any("-108" in e for e in s["supporting_evidence"]) and any("peer-tier expectation" in e.lower() for e in s["supporting_evidence"]),
    ))

# --- Below-floor gap: same shape, smaller real gap -> no story ---
below_floor_rows = [_market_row(player_id=f"b{i}") for i in range(6)]
below_floor_weekly = [{"player_id": f"b{i}", "td_opportunity": 50, "role_momentum": 50, "situation": 50} for i in range(6)]
# All identical on-field profile AND identical price -> zero gap by construction.
below_floor_stories = build_deviation_stories(
    pd.DataFrame(below_floor_rows), pd.DataFrame(below_floor_weekly), {**CONFIG, "n_peer_tiers": 2},
)
results.append(check(
    "identical peers (zero real gap) produce zero stories -- the floor genuinely excludes, not just labels low",
    len(below_floor_stories) == 0,
))

# --- Fully empty snapshot -> zero stories, no crash ---
empty_stories = build_deviation_stories(pd.DataFrame(columns=market_snapshot.columns), pd.DataFrame(columns=weekly.columns), CONFIG)
results.append(check("a fully empty market snapshot degrades to zero stories, not an error", empty_stories == []))

# --- All-stale snapshot -> zero stories ---
all_stale = pd.DataFrame([_market_row(player_id="s1", poll_timestamp=_fresh_ts(300))])
all_stale_weekly = pd.DataFrame([{"player_id": "s1", "td_opportunity": 50, "role_momentum": 50, "situation": 50}])
_mi._freshness_gate = lambda snapshot, config, now=None: _orig_freshness_gate(snapshot, config, now=NOW)
try:
    all_stale_stories = build_deviation_stories(all_stale, all_stale_weekly, CONFIG)
finally:
    _mi._freshness_gate = _orig_freshness_gate
results.append(check("an entirely stale snapshot degrades to zero stories, not an error", all_stale_stories == []))

total = len(results)
passed = sum(1 for r in results if r)
print(f"\n{passed}/{total} checks passed.")
if passed != total:
    sys.exit(1)
