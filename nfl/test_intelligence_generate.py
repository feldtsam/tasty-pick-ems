"""
Tests for the optional market_week parameter on generate_and_write_
intelligence() (intelligence_generate.py) and its request-body validation
on the /api/generate-and-write-intelligence endpoint (api/index.py).

Function-level tests use data_overrides + season 2099 (per this feature's
own review requirement) -- no real network/secret needed, since every
family's stories/prior_history are supplied synthetically. Endpoint-level
validation tests use Flask's own test client; an invalid market_week is
rejected before generate_and_write_intelligence() is ever called, so
those need no monkeypatching either.

Run: python3 nfl/test_intelligence_generate.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "api"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))

os.environ.setdefault("PIPELINE_INCOMING_SECRET", "test-incoming")
os.environ.setdefault("NFL_PIPELINE_WEBHOOK_SECRET", "test-webhook")

from intelligence_generate import generate_and_write_intelligence

AUTH = {"X-Pipeline-Secret": "test-incoming"}


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


def _story(family, entity_key, signal_name="test_signal"):
    """A minimal, real-schema-shaped story dict -- enough for
    process_family()'s sanity-check/shape_story_row path to run cleanly,
    with no dependency on any real family's own build_*_stories() logic."""
    return {
        "intelligence_family": family,
        "entity": {"type": "player", "player_id": entity_key},
        "headline": f"Test headline for {entity_key}.",
        "story": f"Test story body for {entity_key}, long enough to be non-trivial text.",
        "primary_signal": {"name": signal_name, "value": 42.0},
        "supporting_evidence": ["Test evidence."],
        "trend_direction": "opportunity-driven",
        "trend_strength": 42.0,
        "sample_size": 1,
        "completeness": 100.0,
        "confidence": 100.0,
        "time_window": "Season 2099, through Week 1",
        "related_players": [],
    }


if __name__ == "__main__":
    results = []

    # ============================================================
    # Function level -- season 2099 fixture, data_overrides throughout
    # (no real network, no real secret use).
    # ============================================================
    market_story = _story("market_intelligence", "00-1111111", "deviation_pp")
    role_story = _story("role_changes", "00-2222222", "role_momentum")

    # ---- absent market_week: behavior identical to today ----
    result = generate_and_write_intelligence(
        2099, 5, "test-webhook", families=["market_intelligence", "role_changes"], preview_only=True,
        data_overrides={
            "market_intelligence": {"stories": [market_story], "prior_history": {}},
            "role_changes": {"stories": [role_story], "prior_history": {}},
        },
    )
    market_row = result["families"]["market_intelligence"]["story_rows"][0]
    role_row = result["families"]["role_changes"]["story_rows"][0]
    results.append(check(
        "absent market_week: response's own market_week field is None",
        result["market_week"] is None,
    ))
    results.append(check(
        "absent market_week: market_intelligence's story row is stamped with `week` (5), same as every other family",
        market_row["week"] == 5 and role_row["week"] == 5,
    ))

    # ---- valid market_week: only market_intelligence is redirected ----
    result = generate_and_write_intelligence(
        2099, 5, "test-webhook", families=["market_intelligence", "role_changes"], preview_only=True,
        market_week=7,
        data_overrides={
            "market_intelligence": {"stories": [market_story], "prior_history": {}},
            "role_changes": {"stories": [role_story], "prior_history": {}},
        },
    )
    market_row = result["families"]["market_intelligence"]["story_rows"][0]
    role_row = result["families"]["role_changes"]["story_rows"][0]
    results.append(check(
        "valid market_week=7: response echoes market_week=7 while week stays 5",
        result["week"] == 5 and result["market_week"] == 7,
    ))
    results.append(check(
        "valid market_week=7: market_intelligence's story row is stamped week=7, NOT week=5",
        market_row["week"] == 7,
    ))
    results.append(check(
        "valid market_week=7: role_changes' own story row is completely unaffected, still week=5",
        role_row["week"] == 5,
    ))

    # ---- valid market_week with a real (still-empty) history row shape:
    # market_intelligence has lifecycle_eligible=False, so history_rows is
    # always [] regardless -- confirmed here so this feature's own "and
    # history rows" requirement is checked against the real current
    # behavior, not assumed. ----
    results.append(check(
        "market_intelligence has no history_rows to stamp either way (lifecycle_eligible=False, confirmed) -- "
        "market_week's row-stamping requirement is a real no-op for history today, not untested",
        result["families"]["market_intelligence"]["history_rows"] == [],
    ))

    # ---- market_week must not affect a family that isn't even requested,
    # or leak into a family's own independent prior_history lookup ----
    result = generate_and_write_intelligence(
        2099, 5, "test-webhook", families=["role_changes"], preview_only=True, market_week=9,
        data_overrides={"role_changes": {"stories": [role_story], "prior_history": {}}},
    )
    results.append(check(
        "market_week=9 with market_intelligence not even in `families`: role_changes is untouched, no crash",
        result["market_week"] == 9 and "market_intelligence" not in result["families"]
        and result["families"]["role_changes"]["story_rows"][0]["week"] == 5,
    ))

    # ============================================================
    # Endpoint level -- /api/generate-and-write-intelligence's own
    # request-body validation. An invalid market_week is rejected before
    # generate_and_write_intelligence() is ever called, so this needs no
    # monkeypatching or real data.
    # ============================================================
    import api.index as idx
    client = idx.app.test_client()

    r = client.post("/api/generate-and-write-intelligence", headers=AUTH,
                     json={"season": 2099, "week": 5, "market_week": "not-an-int"})
    results.append(check(f"endpoint: non-integer market_week -> 400 (got {r.status_code})", r.status_code == 400))

    r = client.post("/api/generate-and-write-intelligence", headers=AUTH,
                     json={"season": 2099, "week": 5, "market_week": 0})
    results.append(check(f"endpoint: market_week=0 (< 1) -> 400 (got {r.status_code})", r.status_code == 400))

    r = client.post("/api/generate-and-write-intelligence", headers=AUTH,
                     json={"season": 2099, "week": 5, "market_week": 4})
    results.append(check(f"endpoint: market_week=4 < week=5 -> 400 (got {r.status_code})", r.status_code == 400))

    # market_week == week passes validation and reaches real generation --
    # monkeypatched here (same pattern test_build_stub_week_endpoint.py
    # already uses) so this stays a fast, isolated unit test rather than a
    # real network round trip; the goal is confirming validation let it
    # through and passed it down correctly, not re-testing generation
    # itself (already covered at the function level above).
    calls = {}

    def fake_generate(season, week, secret, families=None, preview_only=False, data_overrides=None, market_week=None):
        calls["args"] = {"season": season, "week": week, "market_week": market_week, "preview_only": preview_only}
        return {
            "season": season, "week": week, "market_week": market_week, "preview_only": preview_only,
            "families": {}, "story_rows_written": 0, "history_rows_written": 0,
            "story_rows_generated": 0, "history_rows_generated": 0,
            "forwarded": None, "lovable_status_code": None, "forward_error": None,
        }

    orig_generate = idx.generate_and_write_intelligence
    idx.generate_and_write_intelligence = fake_generate
    try:
        r = client.post("/api/generate-and-write-intelligence", headers=AUTH,
                         json={"season": 2099, "week": 5, "market_week": 5, "preview_only": True})
        results.append(check(
            f"endpoint: market_week == week (5) passes validation and reaches generation with market_week=5 intact (got status={r.status_code}, calls={calls.get('args')})",
            r.status_code == 200 and calls.get("args") == {"season": 2099, "week": 5, "market_week": 5, "preview_only": True},
        ))
    finally:
        idx.generate_and_write_intelligence = orig_generate

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
