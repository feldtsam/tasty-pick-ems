"""
Tests for market_intelligence.py + market_value.py's
market_intelligence_snapshot_for_generation() — Market Trends V1,
Deviation only.

Covers peer-tier grouping correctness (on-field pillars only, Market
Value excluded), the deviation eligibility floor and magnitude bands
(§4), evidence_state's independence from both signal_type and magnitude
(§3b), freshness exclusion applied upstream of scoring (§6), AND the
diagnostics added to close a real, confirmed-live bug: a real generate-
and-write-intelligence call against season=2026/week=1 (nfl_price_history
holding 17,876+ real rows for that exact week) returned zero stories
with zero visibility into why. market_intelligence_snapshot_for_
generation() was silently treating a real read failure the same as a
genuine "zero rows this week" -- both produced result["rows"] == [].
This file's diagnostics tests exist specifically to make that
distinction, and the pool-size-per-stage collapse, both directly
observable rather than inferred.

All synthetic -- no cached real-event fixture; this family's own real
data lives in nfl_price_history, which this test suite has no direct
access to.

Run: python3 nfl/test_market_intelligence.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

from intelligence_schema import STORY_FIELDS
import market_intelligence as _mi
import market_value as _mv
from market_intelligence import (
    CONFIG,
    _assign_peer_tiers,
    _evidence_state_for_row,
    _freshness_gate,
    _magnitude_band,
    _peer_tier_core_score,
    build_deviation_stories,
)
from market_value import market_intelligence_snapshot_for_generation


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


def with_frozen_freshness(fn):
    """Patches _freshness_gate to use the fixed NOW above instead of real wall-clock time, for the duration of fn()."""
    orig = _mi._freshness_gate
    _mi._freshness_gate = lambda snapshot, config, now=None: orig(snapshot, config, now=NOW)
    try:
        return fn()
    finally:
        _mi._freshness_gate = orig


results = []

# --- _peer_tier_core_score: excludes market_value_score, renormalizes present-only ---
pool = pd.DataFrame([
    {"td_opportunity": 90, "role_momentum": 80, "situation": 70, "market_value_score": 10},
    {"td_opportunity": 50, "role_momentum": 50, "situation": 50, "market_value_score": 90},
])
core = _peer_tier_core_score(pool)
expected_row0 = round((90 * 30 + 80 * 20 + 70 * 20) / 70, 1)
results.append(check(
    "core score composite excludes market_value_score entirely (row 0 unaffected by its 10 vs. row 1's 90)",
    core.iloc[0] == expected_row0,
))
results.append(check(
    "row 1's low market_value_score (90, ironically) doesn't drag its on-field-only composite down",
    core.iloc[1] == round((50 * 30 + 50 * 20 + 50 * 20) / 70, 1),
))

partial = pd.DataFrame([{"td_opportunity": 90, "role_momentum": 80}])
partial_core = _peer_tier_core_score(partial)
results.append(check(
    "a missing 'situation' column renormalizes over just td_opportunity+role_momentum, not a crash",
    partial_core.iloc[0] == round((90 * 30 + 80 * 20) / 50, 1),
))

# --- _assign_peer_tiers: too-few-real-values fallback, surfaced in diagnostics ---
thin_pool = pd.DataFrame([
    {"position_group": "TE", "td_opportunity": 40, "role_momentum": 40, "situation": 40},
])
tiered_thin, thin_diag = _assign_peer_tiers(thin_pool, CONFIG)
results.append(check(
    "a position_group with only 1 real player gets its own tier-of-one, not a crash or a fabricated bucket",
    len(tiered_thin) == 1 and pd.notna(tiered_thin["_peer_tier"].iloc[0]),
))
results.append(check(
    "the too-few-real-values fallback is surfaced in diagnostics, not caught silently",
    "TE" in thin_diag["fallback_position_groups"],
))

# --- _assign_peer_tiers: uniform core score (every player identical) also collapses, and must say so ---
# This is exactly the real, concrete shape a Week 1 pool would have if
# every player's on-field pillars come back at the same neutral-fallback
# value (no prior-season-same-year games to compute td_opportunity/
# role_momentum/situation from) -- qcut has no real variance to bin on.
uniform_pool = pd.DataFrame([
    {"position_group": "WR", "td_opportunity": 50, "role_momentum": 50, "situation": 50} for _ in range(6)
])
_, uniform_diag = _assign_peer_tiers(uniform_pool, CONFIG)
# REAL BUG THIS TEST CAUGHT (confirmed empirically, then fixed in
# market_intelligence.py): pd.qcut(all-identical-values, ...,
# duplicates="drop") does NOT raise ValueError -- it silently returns
# all-NaN tier labels, which then survive into a groupby() that drops NaN
# group keys by default, producing NaN expected_probability for every row
# and, from there, zero stories with no exception anywhere. Now handled
# explicitly (checked for directly, not caught via except) and reported
# through the SAME fallback_position_groups list the too-few-values/
# ValueError branches use -- one shared tier, not a silent, undetectable
# collapse.
results.append(check(
    "uniform on-field scores across an entire position_group collapse to exactly 1 real tier",
    uniform_diag["position_groups"]["WR"] == 1,
))
results.append(check(
    "...and IS now reported via fallback_position_groups (previously silent -- see market_intelligence.py's own fix)",
    "WR" in uniform_diag["fallback_position_groups"],
))

# --- A real, varied pool forms real multiple tiers, not a false-positive collapse ---
varied_pool = pd.DataFrame([
    {"position_group": "WR", "td_opportunity": v, "role_momentum": v, "situation": v} for v in (10, 20, 30, 70, 80, 90)
])
_, varied_diag = _assign_peer_tiers(varied_pool, {**CONFIG, "n_peer_tiers": 2})
results.append(check(
    "a real, varied pool forms multiple real tiers, not a spurious collapse",
    "WR" not in varied_diag["fallback_position_groups"] and varied_diag["position_groups"]["WR"] > 1,
))

# --- _freshness_gate (§6) ---
fresh_and_stale = pd.DataFrame([
    _market_row(player_id="fresh", poll_timestamp=_fresh_ts(30)),
    _market_row(player_id="stale", poll_timestamp=_fresh_ts(180)),
    _market_row(player_id="future", poll_timestamp=(NOW + pd.Timedelta(hours=1)).isoformat()),
])
gated = _freshness_gate(fresh_and_stale, CONFIG, now=NOW)
results.append(check(
    "freshness gate keeps only the fresh row, excludes both stale and clock-skewed-future rows",
    set(gated["player_id"]) == {"fresh"},
))

# --- _evidence_state_for_row (§3b) ---
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
    ("Stale Weak-Priced-Strong", 21, 24, 29, 0.52, -108, 5, _fresh_ts(180)),
    ("No Weekly History", 0, 0, 0, 0.52, -108, 5, _fresh_ts(30)),
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

stories, e2e_diagnostics = with_frozen_freshness(
    lambda: build_deviation_stories(market_snapshot, weekly, e2e_config)
)

results.append(check("exactly one real story generated (the one real, fresh, matched outlier)", len(stories) == 1))
results.append(check(
    "diagnostics report the real pool size at every stage (input 10 -> fresh 9 -> pillar-filtered 8 -> eligible 1)",
    e2e_diagnostics["input_row_count"] == 10
    and e2e_diagnostics["fresh_row_count"] == 9
    and e2e_diagnostics["pool_after_pillar_filter"] == 8
    and e2e_diagnostics["eligible_after_floor"] == 1,
))
results.append(check(
    "diagnostics report real, multi-tier peer grouping (no collapse) for this varied pool",
    "WR" not in e2e_diagnostics["peer_tiers"]["fallback_position_groups"],
))

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

# --- Below-floor gap: identical peers, zero real gap -> no story, and diagnostics say why ---
below_floor_rows = [_market_row(player_id=f"b{i}") for i in range(6)]
below_floor_weekly = [{"player_id": f"b{i}", "td_opportunity": 50, "role_momentum": 50, "situation": 50} for i in range(6)]
below_floor_stories, below_floor_diag = with_frozen_freshness(lambda: build_deviation_stories(
    pd.DataFrame(below_floor_rows), pd.DataFrame(below_floor_weekly), {**CONFIG, "n_peer_tiers": 2},
))
results.append(check(
    "identical peers (zero real gap) produce zero stories -- the floor genuinely excludes, not just labels low",
    len(below_floor_stories) == 0,
))
results.append(check(
    "diagnostics distinguish this from an earlier-stage exclusion: pool reached the floor stage, just found nothing eligible",
    below_floor_diag["pool_before_floor"] == 6 and below_floor_diag["eligible_after_floor"] == 0,
))

# --- Fully empty snapshot -> zero stories, diagnostics explain which stage (the very first) ---
empty_stories, empty_diag = build_deviation_stories(pd.DataFrame(columns=market_snapshot.columns), pd.DataFrame(columns=weekly.columns), CONFIG)
results.append(check("a fully empty market snapshot degrades to zero stories, not an error", empty_stories == []))
results.append(check(
    "diagnostics show the exclusion happened at input, not a later stage silently reporting the same shape",
    empty_diag == {"input_row_count": 0},
))

# --- All-stale snapshot -> zero stories, diagnostics show it died at the freshness stage specifically ---
all_stale = pd.DataFrame([_market_row(player_id="s1", poll_timestamp=_fresh_ts(300))])
all_stale_weekly = pd.DataFrame([{"player_id": "s1", "td_opportunity": 50, "role_momentum": 50, "situation": 50}])
all_stale_stories, all_stale_diag = with_frozen_freshness(
    lambda: build_deviation_stories(all_stale, all_stale_weekly, CONFIG)
)
results.append(check("an entirely stale snapshot degrades to zero stories, not an error", all_stale_stories == []))
results.append(check(
    "diagnostics pinpoint the freshness gate specifically (input 1 -> fresh 0), not just 'zero stories' with no stage",
    all_stale_diag["input_row_count"] == 1 and all_stale_diag["fresh_row_count"] == 0,
))

# ============================================================
# market_intelligence_snapshot_for_generation(): the swallowed-failure fix
# ============================================================

def _with_read_price_history(fake_result, fn):
    orig = _mv.read_price_history
    _mv.read_price_history = lambda season, week, secret, read_url=None: fake_result
    try:
        return fn()
    finally:
        _mv.read_price_history = orig

# A real read FAILURE (network error, bad signature, non-JSON body -- any
# of read_price_history's own real failure branches) must be visibly
# distinguishable from a genuine "zero rows this week" read.
fail_result = {"ok": False, "error": "Network error reaching the price-history read endpoint.", "status_code": None, "rows": []}
snap_fail, diag_fail = _with_read_price_history(
    fail_result, lambda: market_intelligence_snapshot_for_generation(2026, 1, "fake-secret")
)
results.append(check(
    "a real read failure surfaces read_ok=False and the real error text, not an indistinguishable empty snapshot",
    diag_fail["read_ok"] is False and diag_fail["read_error"] == fail_result["error"],
))
results.append(check("a failed read still returns a correctly-shaped, zero-row DataFrame (no crash downstream)", len(snap_fail) == 0))

# A genuine successful read with real zero rows -- must look DIFFERENT
# from the failure case above (read_ok=True), even though both end in an
# empty DataFrame.
empty_ok_result = {"ok": True, "error": None, "status_code": 200, "rows": []}
snap_empty_ok, diag_empty_ok = _with_read_price_history(
    empty_ok_result, lambda: market_intelligence_snapshot_for_generation(2026, 1, "fake-secret")
)
results.append(check(
    "a genuine zero-row read is read_ok=True, error=None -- distinguishable from the failure case above",
    diag_empty_ok["read_ok"] is True and diag_empty_ok["read_error"] is None and diag_empty_ok["raw_row_count"] == 0,
))

# A successful read with a real mix of matched and unmatched rows -- the
# real, concrete "matching failure" hypothesis this whole diagnostics
# pass exists to make directly checkable against real production data.
mixed_rows = [
    {"player_id": "p1", "player_name_raw": "Real Player", "team": "KC", "position_group": "WR",
     "event_id": "e1", "away_team": "KC", "home_team": "DEN", "commence_time": "2026-09-14T20:00:00Z",
     "n_books": 3, "best_price": 200, "best_book": "DK", "consensus_implied_probability": 0.3,
     "consensus_price_american": 233, "poll_timestamp": _fresh_ts(30), "matched": True},
    {"player_id": None, "player_name_raw": "Unmatched Book Line", "team": None, "position_group": None,
     "event_id": "e1", "away_team": "KC", "home_team": "DEN", "commence_time": "2026-09-14T20:00:00Z",
     "n_books": None, "best_price": None, "best_book": None, "consensus_implied_probability": None,
     "consensus_price_american": None, "poll_timestamp": _fresh_ts(30), "matched": False},
    {"player_id": None, "player_name_raw": "Another Unmatched Line", "team": None, "position_group": None,
     "event_id": "e1", "away_team": "KC", "home_team": "DEN", "commence_time": "2026-09-14T20:00:00Z",
     "n_books": None, "best_price": None, "best_book": None, "consensus_implied_probability": None,
     "consensus_price_american": None, "poll_timestamp": _fresh_ts(30), "matched": False},
]
mixed_result = {"ok": True, "error": None, "status_code": 200, "rows": mixed_rows}
snap_mixed, diag_mixed = _with_read_price_history(
    mixed_result, lambda: market_intelligence_snapshot_for_generation(2026, 1, "fake-secret")
)
results.append(check(
    "raw_row_count reflects every real row read back (matched + unmatched), not just the usable ones",
    diag_mixed["raw_row_count"] == 3,
))
results.append(check(
    "matched_row_count/unmatched_row_count split correctly (1 real player_id, 2 unmatched book lines)",
    diag_mixed["matched_row_count"] == 1 and diag_mixed["unmatched_row_count"] == 2,
))
results.append(check(
    "distinct_matched_players reflects the real post-matching pool, not the raw row count",
    diag_mixed["distinct_matched_players"] == 1,
))
results.append(check("the scored snapshot itself has exactly the 1 real matched player, not 3", len(snap_mixed) == 1))

total = len(results)
passed = sum(1 for r in results if r)
print(f"\n{passed}/{total} checks passed.")
if passed != total:
    sys.exit(1)
