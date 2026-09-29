"""Acceptance tests 1-4, 10 (checks-level), 11, 13 from SPEC.md v1.4, plus
unit tests for the v1.3 and v1.4 rule changes.

Real Make history drives test 1. Everything else uses synthetic fixtures,
clearly labelled, because the real 17-day window doesn't contain those cases.

Under v1.4 all six acceptance-test-1 must-haves pass on real data, so there
are no xfails left. The two that failed earlier were fixed by rule-semantics
changes in the v1.3 and v1.4 batch reviews, not by tuning thresholds or
loosening assertions -- each earlier failure was reported and ruled on first.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checks import (  # noqa: E402
    CONFIG,
    classify_execution,
    evaluate,
    rescue_runs,
    rescued_failures,
    should_deliver_escalation,
)
from radar import aggregate_window, daily_radars  # noqa: E402
from replay import load_fixture, run  # noqa: E402

UTC = timezone.utc

PICKS, GRADE, POLLER, SPLIT2 = 6186710, 6241867, 6152892, 6079077


# --- helpers --------------------------------------------------------------


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def synth_scenario(scenario_id=PICKS, name="SYNTHETIC", executions=(), events=(),
                   is_active=True, is_paused=False):
    """SYNTHETIC fixture builder -- not real Make data.

    Defaults to the Picks id because L1 only applies to CONFIG.l1_scenarios.
    """
    return {
        "scenario_id": scenario_id,
        "name": name,
        "is_active": is_active,
        "is_paused": is_paused,
        "executions": list(executions),
        "events": list(events),
    }


def ex(at, status=1, run_type="auto", duration_ms=100_000, error_name=None,
       error_message=None, cause_module=None, execution_id=None):
    return {
        "execution_id": execution_id or f"synthetic-{at}",
        "started_at": at if isinstance(at, str) else iso(at),
        "ended_at": None,
        "duration_ms": duration_ms,
        "status": status,
        "run_type": run_type,
        "error_name": error_name,
        "error_message": error_message,
        "cause_module": cause_module,
    }


def evt(at, event_type, detail=None, event_id=None):
    return {
        "event_id": event_id or f"synthetic-{at}-{event_type}",
        "at": at if isinstance(at, str) else iso(at),
        "event_type": event_type,
        "detail": detail,
    }


def payload(*scenarios, collected_at="2026-10-01T00:00:00Z"):
    return {"collected_at": collected_at, "mode": "collect", "scenarios": list(scenarios)}


def signals_for(out, scenario_id=None, signal_id=None, tier=None):
    result = out["signals"]
    if scenario_id is not None:
        result = [s for s in result if s["scenario_id"] == scenario_id]
    if signal_id is not None:
        result = [s for s in result if s["signal_id"] == signal_id]
    if tier is not None:
        result = [s for s in result if s["tier"] == tier]
    return result


@pytest.fixture(scope="module")
def replay_results():
    return run(load_fixture())


def loud_for(replay_results, scenario_id, tiers=("RADAR", "ESCALATE"), signal_id=None):
    """Ticks where the scenario fired a loud signal."""
    out = []
    for row in replay_results:
        for sig in row["result"]["tpe_signals"]:
            if sig["scenario_id"] != scenario_id or sig["tier"] not in tiers:
                continue
            if signal_id and sig["signal_id"] != signal_id:
                continue
            out.append(row["tick"])
    return sorted(set(out))


def days_with(replay_results, scenario_id, tiers=("RADAR", "ESCALATE"), signal_id=None):
    return {t.strftime("%Y-%m-%d") for t in loud_for(replay_results, scenario_id, tiers, signal_id)}


# --- acceptance test 1: historical replay (v1.3 must-haves) ---------------


def test_1_mh1_sep_8_to_11_green_on_every_tick(replay_results):
    # Every tick, not one per day: L1 can flicker within a day, so sampling
    # the last tick of each day would hide it.
    not_green = [
        (row["tick"].strftime("%Y-%m-%d %H:%M"), row["result"]["status"])
        for row in replay_results
        if row["tick"] < datetime(2026, 9, 12, tzinfo=UTC)
        and row["result"]["status"] != "GREEN"
    ]
    assert not not_green, f"{len(not_green)} non-GREEN ticks, first: {not_green[0]}"


def test_1_mh2_grade_bookmarks_escalate_on_sep_12(replay_results):
    assert "2026-09-12" in days_with(replay_results, GRADE, tiers=("ESCALATE",))


def test_1_mh3_picks_l1_radar_fires_before_first_picks_escalate(replay_results):
    l1 = loud_for(replay_results, PICKS, tiers=("RADAR",), signal_id="L1_DURATION_DRIFT")
    esc = loud_for(replay_results, PICKS, tiers=("ESCALATE",))
    assert l1, "Picks L1 never fired"
    assert esc, "Picks never escalated"
    assert l1[0] < esc[0], f"L1 first fired {l1[0]}, ESCALATE first fired {esc[0]}"


def test_1_mh4_picks_escalate_on_sep_21(replay_results):
    assert "2026-09-21" in days_with(replay_results, PICKS, tiers=("ESCALATE",))


def test_1_mh5_grade_bookmarks_no_escalate_after_restart_and_no_l1(replay_results):
    """The L2 RADAR from the three outage restarts is expected (v1.3/v1.4)."""
    restart = datetime(2026, 9, 15, 23, 25, tzinfo=UTC)
    late_escalations = [
        t for t in loud_for(replay_results, GRADE, tiers=("ESCALATE",)) if t > restart
    ]
    assert not late_escalations, f"escalated after the restart at {late_escalations}"

    any_l1 = [
        row["tick"]
        for row in replay_results
        for sig in row["result"]["signals"]
        if sig["scenario_id"] == GRADE
        and sig["signal_id"] == "L1_DURATION_DRIFT"
        and sig["tier"] != "LOG"
    ]
    assert not any_l1, f"L1 fired on Grade Bookmarks at {any_l1[:3]}"


def test_1_mh6_split2_and_poller_not_continuously_yellow_from_sep_16(replay_results):
    """Passes under v1.4. Split 2's 8 rescue runs all target the single Sep 15
    12:00 failure, so counting distinct rescued failures gives 1, below the
    threshold of 3. Under v1.3 (rescue runs) this failed for Split 2."""
    from_sep16 = [r for r in replay_results if r["tick"] >= datetime(2026, 9, 16, tzinfo=UTC)]
    all_days = {r["tick"].strftime("%Y-%m-%d") for r in from_sep16}
    offenders = {}
    for sid, label in ((SPLIT2, "Split2"), (POLLER, "ATTDPoller")):
        loud_days = days_with(from_sep16, sid)
        if loud_days == all_days:
            offenders[label] = sorted(loud_days)
    assert not offenders, f"continuously loud: {list(offenders)}"


def test_1_poller_is_silent_throughout(replay_results):
    """The poller is out of L1 (v1.3) and never failed, so it should be quiet."""
    assert not loud_for(replay_results, POLLER)


# --- acceptance test 2: green stays quiet ---------------------------------


def test_2_seven_normal_days_are_green_and_call_no_llm():
    """SYNTHETIC: seven days of identical successful auto runs."""
    start = datetime(2026, 10, 1, 12, tzinfo=UTC)
    runs = [ex(start + timedelta(days=i)) for i in range(7)]
    out = evaluate(payload(synth_scenario(executions=runs)), now=start + timedelta(days=7))
    assert out["status"] == "GREEN"
    assert not [s for s in out["signals"] if s["tier"] in ("RADAR", "ESCALATE")]
    # "zero LLM calls" is structural: checks.py imports nothing that can reach
    # a network. Guard against that changing.
    import checks
    src = open(checks.__file__).read()
    for forbidden in ("import requests", "import httpx", "urllib.request", "anthropic"):
        assert forbidden not in src


# --- acceptance test 3: insufficient baseline -----------------------------


def test_3_fewer_than_five_post_edit_runs_is_log():
    """SYNTHETIC: 4 post-edit runs, the last one wildly slower."""
    start = datetime(2026, 10, 1, 12, tzinfo=UTC)
    runs = [ex(start + timedelta(days=i), duration_ms=d)
            for i, d in enumerate([100_000, 100_000, 100_000, 900_000])]
    out = evaluate(payload(synth_scenario(executions=runs)), now=start + timedelta(days=5))
    l1 = signals_for(out, signal_id="L1_DURATION_DRIFT")
    assert len(l1) == 1
    assert l1[0]["tier"] == "LOG"
    assert l1[0]["reason"] == "INSUFFICIENT_BASELINE"
    assert out["status"] == "GREEN"


# --- acceptance test 4: learning fires before inspection ------------------


def test_4_no_picks_escalate_on_the_day_l1_first_fires(replay_results):
    l1_days = days_with(replay_results, PICKS, tiers=("RADAR",), signal_id="L1_DURATION_DRIFT")
    esc_days = days_with(replay_results, PICKS, tiers=("ESCALATE",))
    first = min(l1_days)
    assert first not in esc_days, f"Picks escalated on {first}, the day L1 first fired"
    assert first < min(esc_days)


# --- v1.3: the 30s absolute floor ----------------------------------------


def _l1_pool(start, durations):
    """SYNTHETIC: enough successful auto runs to make every one evaluable."""
    return [ex(start + timedelta(days=i), duration_ms=d) for i, d in enumerate(durations)]


def test_l1_floor_blocks_a_large_ratio_on_a_small_scenario():
    """SYNTHETIC, modelled on the real poller: 0.2s median, 0.6s runs. The
    ratio clears 1.5x easily; the 30s floor stops it."""
    start = datetime(2026, 10, 1, 12, tzinfo=UTC)
    base = [200] * 5
    spikes = [600, 600, 600, 600, 600]
    runs = _l1_pool(start, base + spikes)
    out = evaluate(payload(synth_scenario(executions=runs)), now=start + timedelta(days=20))
    l1 = signals_for(out, signal_id="L1_DURATION_DRIFT")
    assert l1[0]["tier"] == "LOG", l1[0]["facts"]
    assert l1[0]["facts"]["flagged_runs_of_last_5"] == 0


def test_l1_floor_allows_a_large_ratio_on_a_large_scenario():
    """SYNTHETIC: same 3x ratio, but 100s -> 300s clears the 30s floor."""
    start = datetime(2026, 10, 1, 12, tzinfo=UTC)
    runs = _l1_pool(start, [100_000] * 5 + [300_000] * 5)
    out = evaluate(payload(synth_scenario(executions=runs)), now=start + timedelta(days=20))
    l1 = signals_for(out, signal_id="L1_DURATION_DRIFT")
    assert l1[0]["tier"] == "RADAR"
    assert l1[0]["facts"]["flagged_runs_of_last_5"] >= 2
    assert l1[0]["facts"]["absolute_floor_s"] == 30.0
    assert len(l1[0]["facts"]["flagged_runs"]) == l1[0]["facts"]["flagged_runs_of_last_5"]


def test_l1_needs_both_ratio_and_floor():
    """SYNTHETIC: +40s clears the floor but only 1.4x, so it is not flagged."""
    start = datetime(2026, 10, 1, 12, tzinfo=UTC)
    runs = _l1_pool(start, [100_000] * 5 + [140_000] * 5)
    out = evaluate(payload(synth_scenario(executions=runs)), now=start + timedelta(days=20))
    l1 = signals_for(out, signal_id="L1_DURATION_DRIFT")
    assert l1[0]["tier"] == "LOG"
    assert l1[0]["facts"]["flagged_runs_of_last_5"] == 0


def test_l1_only_applies_to_configured_scenarios():
    """SYNTHETIC: the same drift on the poller and Split 2 produces no L1."""
    start = datetime(2026, 10, 1, 12, tzinfo=UTC)
    runs = _l1_pool(start, [100_000] * 5 + [300_000] * 5)
    for sid in (POLLER, SPLIT2):
        out = evaluate(payload(synth_scenario(scenario_id=sid, executions=runs)),
                       now=start + timedelta(days=20))
        assert not signals_for(out, signal_id="L1_DURATION_DRIFT"), sid
        assert out["status"] == "GREEN"
    for sid in (PICKS, GRADE):
        out = evaluate(payload(synth_scenario(scenario_id=sid, executions=runs)),
                       now=start + timedelta(days=20))
        assert signals_for(out, signal_id="L1_DURATION_DRIFT", tier="RADAR"), sid


# --- v1.3: the evaluable-run count ---------------------------------------


@pytest.mark.parametrize(
    "pool_size,expect_evaluable,expect_tier",
    [
        (6, 1, "LOG"),    # 1 evaluable run
        (8, 3, "LOG"),    # 3 evaluable runs -- would have fired under v1.2
        (9, 4, "LOG"),    # 4 evaluable runs -- still one short
        (10, 5, "RADAR"), # 5 evaluable runs -- the test can now fire
    ],
)
def test_l1_needs_five_evaluable_runs(pool_size, expect_evaluable, expect_tier):
    """SYNTHETIC: an evaluable run needs 5 prior post-edit successes, so the
    2-of-5 test needs about 10 successful post-edit auto runs.

    Durations are shaped so every evaluable run is flagged, isolating the
    count as the only thing under test.
    """
    start = datetime(2026, 10, 1, 12, tzinfo=UTC)
    durations = [100_000] * 5 + [900_000] * (pool_size - 5)
    runs = _l1_pool(start, durations)
    out = evaluate(payload(synth_scenario(executions=runs)),
                   now=start + timedelta(days=pool_size + 2))
    l1 = signals_for(out, signal_id="L1_DURATION_DRIFT")[0]
    assert l1["tier"] == expect_tier, l1["facts"]
    if expect_tier == "LOG":
        assert l1["reason"] == "INSUFFICIENT_BASELINE"
        assert l1["facts"]["evaluable_runs"] == expect_evaluable
        assert l1["facts"]["evaluable_runs_required"] == 5
    else:
        assert l1["facts"]["runs_considered"] == 5


# --- v1.3: the rescue-run definition -------------------------------------


def _rescues(executions, events=()):
    from checks import _scoped
    scenario = synth_scenario(executions=executions, events=events)
    now = datetime(2026, 11, 1, tzinfo=UTC)
    scoped = _scoped(scenario, now, CONFIG)
    return rescue_runs(scoped["all_executions"], scoped["windows"])


def test_rescue_manual_after_failed_auto_counts():
    """SYNTHETIC: the compensation pattern L2 exists to see."""
    start = datetime(2026, 10, 1, 12, tzinfo=UTC)
    runs = [
        ex(start, status=3, error_name="ConnectionError"),
        ex(start + timedelta(hours=1), run_type="manual"),
    ]
    r = _rescues(runs)
    assert len(r) == 1
    assert r[0]["run_type"] == "manual"


def test_rescue_manual_after_successful_auto_does_not_count():
    """SYNTHETIC: development, not compensation."""
    start = datetime(2026, 10, 1, 12, tzinfo=UTC)
    runs = [
        ex(start, status=1),
        ex(start + timedelta(hours=1), run_type="manual"),
    ]
    assert _rescues(runs) == []


def test_rescue_manual_inside_a_testing_window_does_not_count():
    """SYNTHETIC: a failed auto, an edit, then a manual run 10 minutes later."""
    start = datetime(2026, 10, 1, 12, tzinfo=UTC)
    runs = [
        ex(start, status=3, error_name="X"),
        ex(start + timedelta(hours=1, minutes=10), run_type="manual"),
    ]
    events = [evt(start + timedelta(hours=1), "modify")]
    assert _rescues(runs, events) == []
    # ...and the same manual run 3 hours after the edit does count.
    runs_late = [
        ex(start, status=3, error_name="X"),
        ex(start + timedelta(hours=4), run_type="manual"),
    ]
    assert len(_rescues(runs_late, events)) == 1


def test_rescue_manual_with_no_prior_auto_does_not_count():
    """SYNTHETIC: a brand-new scenario's first manual runs are development."""
    start = datetime(2026, 10, 1, 12, tzinfo=UTC)
    runs = [ex(start + timedelta(minutes=i), run_type="manual") for i in range(5)]
    assert _rescues(runs) == []


def test_rescue_ignores_status_2_without_error_when_finding_prior_auto():
    """SYNTHETIC: a clean status-2 auto is ignored by every rule, so it cannot
    mask the failed auto behind it."""
    start = datetime(2026, 10, 1, 12, tzinfo=UTC)
    runs = [
        ex(start, status=3, error_name="X"),
        ex(start + timedelta(hours=1), status=2, error_name=None),
        ex(start + timedelta(hours=2), run_type="manual"),
    ]
    assert len(_rescues(runs)) == 1


def test_l2_needs_three_rescued_failures_in_seven_days():
    """SYNTHETIC: two rescued failures stay quiet, three fire RADAR. Each
    failure here gets exactly one rescue run."""
    start = datetime(2026, 10, 1, 12, tzinfo=UTC)

    def build(n):
        runs = []
        for i in range(n):
            runs.append(ex(start + timedelta(days=i), status=3, error_name="X"))
            runs.append(ex(start + timedelta(days=i, hours=1), run_type="manual"))
        return runs

    quiet = evaluate(payload(synth_scenario(executions=build(2))),
                     now=start + timedelta(days=3))
    assert not signals_for(quiet, signal_id="L2_HUMAN_COMPENSATION")

    loud = evaluate(payload(synth_scenario(executions=build(3))),
                    now=start + timedelta(days=4))
    l2 = signals_for(loud, signal_id="L2_HUMAN_COMPENSATION")
    assert l2 and l2[0]["tier"] == "RADAR"
    assert l2[0]["facts"]["rescued_failures"] == 3
    assert l2[0]["facts"]["rescue_runs"] == 3
    assert l2[0]["facts"]["window_days"] == 7


def test_l2_rescued_failures_age_out_of_the_window():
    """SYNTHETIC: the same three rescued failures, evaluated 8 days later."""
    start = datetime(2026, 10, 1, 12, tzinfo=UTC)
    runs = []
    for i in range(3):
        runs.append(ex(start + timedelta(hours=i * 2), status=3, error_name="X"))
        runs.append(ex(start + timedelta(hours=i * 2 + 1), run_type="manual"))
    assert signals_for(
        evaluate(payload(synth_scenario(executions=runs)), now=start + timedelta(days=1)),
        signal_id="L2_HUMAN_COMPENSATION",
    )
    assert not signals_for(
        evaluate(payload(synth_scenario(executions=runs)), now=start + timedelta(days=8)),
        signal_id="L2_HUMAN_COMPENSATION",
    )


def test_l2_is_not_reset_by_an_edit():
    """SYNTHETIC: a modify after the rescue runs must not clear L2."""
    start = datetime(2026, 10, 1, 12, tzinfo=UTC)
    runs = []
    for i in range(3):
        runs.append(ex(start + timedelta(hours=i * 4), status=3, error_name="X"))
        runs.append(ex(start + timedelta(hours=i * 4 + 1), run_type="manual"))
    events = [evt(start + timedelta(days=1), "modify")]
    out = evaluate(payload(synth_scenario(executions=runs, events=events)),
                   now=start + timedelta(days=2))
    assert signals_for(out, signal_id="L2_HUMAN_COMPENSATION", tier="RADAR")


def test_l2_applies_to_every_watched_scenario():
    """L2 has no scenario allow-list, unlike L1."""
    start = datetime(2026, 10, 1, 12, tzinfo=UTC)
    runs = []
    for i in range(3):
        runs.append(ex(start + timedelta(days=i), status=3, error_name="X"))
        runs.append(ex(start + timedelta(days=i, hours=1), run_type="manual"))
    for sid in (PICKS, GRADE, POLLER, SPLIT2):
        out = evaluate(payload(synth_scenario(scenario_id=sid, executions=runs)),
                       now=start + timedelta(days=4))
        assert signals_for(out, signal_id="L2_HUMAN_COMPENSATION", tier="RADAR"), sid


# --- v1.3: daily radar aggregation ---------------------------------------


def _tick(when, status, tpe=(), harness=()):
    return {"tick": when, "result": {"status": status, "tpe_signals": list(tpe),
                                     "harness_signals": list(harness)}}


def _sig(signal_id, tier="RADAR", scenario_id=PICKS, cls="LEARNING"):
    return {"signal_id": signal_id, "tier": tier, "scenario_id": scenario_id,
            "class": cls, "scenario": "SYNTHETIC", "facts": {}, "recent_edits": []}


def test_radar_reports_worst_status_across_the_window():
    """SYNTHETIC: a RED tick in the middle, GREEN at the end."""
    t0 = datetime(2026, 10, 1, 0, tzinfo=UTC)
    rows = [
        _tick(t0, "GREEN"),
        _tick(t0 + timedelta(hours=2), "RED", [_sig("I2_CONSECUTIVE_FAILURES", "ESCALATE")]),
        _tick(t0 + timedelta(hours=4), "GREEN"),
    ]
    out = aggregate_window(rows)
    assert out["status"] == "RED"
    assert out["ticks"] == 3


def test_radar_reports_every_signal_that_fired_not_just_the_last_tick():
    """SYNTHETIC: the flicker case. L1 fires, clears, and the radar still
    reports it -- marked cleared."""
    t0 = datetime(2026, 10, 1, 0, tzinfo=UTC)
    rows = [
        _tick(t0, "YELLOW", [_sig("L1_DURATION_DRIFT")]),
        _tick(t0 + timedelta(hours=2), "GREEN"),
        _tick(t0 + timedelta(hours=4), "YELLOW", [_sig("L1_DURATION_DRIFT")]),
        _tick(t0 + timedelta(hours=6), "GREEN"),
    ]
    out = aggregate_window(rows)
    assert out["status"] == "YELLOW"
    assert len(out["signals"]) == 1
    sig = out["signals"][0]
    assert sig["signal_id"] == "L1_DURATION_DRIFT"
    assert sig["cleared"] is True
    assert sig["ticks_seen"] == 2
    assert sig["first_seen"] == "2026-10-01T00:00:00Z"
    assert sig["last_seen"] == "2026-10-01T04:00:00Z"


def test_radar_marks_a_still_firing_signal_as_not_cleared():
    t0 = datetime(2026, 10, 1, 0, tzinfo=UTC)
    rows = [
        _tick(t0, "YELLOW", [_sig("L1_DURATION_DRIFT")]),
        _tick(t0 + timedelta(hours=2), "YELLOW", [_sig("L1_DURATION_DRIFT")]),
    ]
    out = aggregate_window(rows)
    assert out["signals"][0]["cleared"] is False
    assert out["signals"][0]["ticks_seen"] == 2


def test_radar_keeps_harness_signals_separate():
    t0 = datetime(2026, 10, 1, 0, tzinfo=UTC)
    rows = [_tick(t0, "GREEN", [], [_sig("H1_COLLECTION_GAP", "RADAR", None, "HARNESS_HEALTH")])]
    out = aggregate_window(rows)
    assert out["status"] == "GREEN"
    assert out["signals"] == []
    assert len(out["harness_signals"]) == 1


def test_radar_drops_log_tier_signals():
    t0 = datetime(2026, 10, 1, 0, tzinfo=UTC)
    rows = [_tick(t0, "GREEN", [_sig("L1_DURATION_DRIFT", "LOG")])]
    assert aggregate_window(rows)["signals"] == []


def test_radar_empty_window_is_green():
    out = aggregate_window([])
    assert out["status"] == "GREEN"
    assert out["signals"] == []


def test_daily_radars_split_at_the_radar_hour():
    """SYNTHETIC: 2-hourly ticks across two days; one radar per 12:00 UTC."""
    t0 = datetime(2026, 10, 1, 0, tzinfo=UTC)
    rows = [_tick(t0 + timedelta(hours=2 * i), "GREEN") for i in range(24)]
    radars = daily_radars(rows, radar_hour_utc=12)
    sent = [r for r in radars if r["sent_at"]]
    assert len(sent) == 2
    assert [r["sent_at"].strftime("%Y-%m-%d %H:%M") for r in sent] == [
        "2026-10-01 12:00", "2026-10-02 12:00"
    ]
    assert radars[-1].get("partial") is True


def test_daily_radars_over_the_real_replay(replay_results):
    """Every real replay day yields exactly one radar, and no radar is
    quieter than the ticks it covers."""
    radars = daily_radars(replay_results)
    sent = [r for r in radars if r["sent_at"]]
    assert len(sent) == 17, [r["sent_at"] for r in sent]
    rank = {"GREEN": 0, "YELLOW": 1, "RED": 2}
    for r in radars:
        for sig in r["signals"]:
            assert sig["tier"] in ("RADAR", "ESCALATE")
        if any(s["tier"] == "ESCALATE" for s in r["signals"]):
            assert r["status"] == "RED"
        elif r["signals"]:
            assert rank[r["status"]] >= rank["YELLOW"]


# --- acceptance test 10: idempotency (checks-level) -----------------------


def test_10_same_payload_twice_is_identical():
    start = datetime(2026, 10, 1, 12, tzinfo=UTC)
    runs = [ex(start + timedelta(hours=i), status=3, error_name="X") for i in range(3)]
    data = payload(synth_scenario(executions=runs))
    now = start + timedelta(days=1)
    assert evaluate(data, now=now) == evaluate(data, now=now)


def test_10_escalation_is_not_redelivered_within_24h():
    start = datetime(2026, 10, 1, 12, tzinfo=UTC)
    runs = [ex(start + timedelta(hours=i), status=3, error_name="X") for i in range(3)]
    out = evaluate(payload(synth_scenario(executions=runs)), now=start + timedelta(days=1))
    esc = signals_for(out, tier="ESCALATE")
    assert esc, "expected an ESCALATE to test suppression against"
    sig = esc[0]

    assert should_deliver_escalation(sig, {"escalations_last_24h": []}) is True
    already = {"escalations_last_24h": [
        {"signal_id": sig["signal_id"], "scenario_id": sig["scenario_id"]}
    ]}
    assert should_deliver_escalation(sig, already) is False
    other = {"escalations_last_24h": [
        {"signal_id": sig["signal_id"], "scenario_id": 123456}
    ]}
    assert should_deliver_escalation(sig, other) is True


# --- acceptance test 11: harness health is separate -----------------------


def test_11_thirty_hour_gap_is_harness_radar_and_tpe_stays_green():
    start = datetime(2026, 10, 1, 12, tzinfo=UTC)
    runs = [ex(start + timedelta(days=i)) for i in range(7)]
    now = start + timedelta(days=7)
    state = {"last_collection_at": iso(now - timedelta(hours=30))}
    out = evaluate(payload(synth_scenario(executions=runs)), state=state, now=now)

    harness = out["harness_signals"]
    assert len(harness) == 1
    assert harness[0]["signal_id"] == "H1_COLLECTION_GAP"
    assert harness[0]["class"] == "HARNESS_HEALTH"
    assert harness[0]["tier"] == "RADAR"
    assert harness[0]["facts"]["gap_hours"] == 30.0

    assert out["status"] == "GREEN"
    assert all(s["class"] != "HARNESS_HEALTH" for s in out["tpe_signals"])


def test_11_gap_under_threshold_does_not_fire():
    now = datetime(2026, 10, 8, 12, tzinfo=UTC)
    state = {"last_collection_at": iso(now - timedelta(hours=25))}
    out = evaluate(payload(synth_scenario()), state=state, now=now)
    assert not out["harness_signals"]


# --- acceptance test 13: status 2 handling --------------------------------


def test_13_status_2_with_error_is_a_failure():
    assert classify_execution({"status": 2, "error_name": "ConnectionError"}) == "failure"


def test_13_status_2_without_error_is_ignored():
    assert classify_execution({"status": 2, "error_name": None}) == "ignored"


def test_13_status_2_with_error_counts_toward_i2():
    """SYNTHETIC: one status-3 then one status-2-with-error = 2 consecutive."""
    start = datetime(2026, 10, 1, 12, tzinfo=UTC)
    runs = [
        ex(start, status=3, error_name="ConnectionError"),
        ex(start + timedelta(hours=1), status=2, error_name="ConnectionError"),
    ]
    out = evaluate(payload(synth_scenario(executions=runs)), now=start + timedelta(days=1))
    i2 = signals_for(out, signal_id="I2_CONSECUTIVE_FAILURES")
    assert i2 and i2[0]["tier"] == "ESCALATE"
    assert i2[0]["facts"]["consecutive_failures"] == 2
    assert out["status"] == "RED"


def test_13_status_2_without_error_changes_nothing():
    """SYNTHETIC: a clean status-2 between two failures neither breaks nor
    extends the streak."""
    start = datetime(2026, 10, 1, 12, tzinfo=UTC)
    runs = [
        ex(start, status=3, error_name="X"),
        ex(start + timedelta(hours=1), status=2, error_name=None),
        ex(start + timedelta(hours=2), status=3, error_name="X"),
    ]
    out = evaluate(payload(synth_scenario(executions=runs)), now=start + timedelta(days=1))
    i2 = signals_for(out, signal_id="I2_CONSECUTIVE_FAILURES")
    assert i2 and i2[0]["facts"]["consecutive_failures"] == 2


def test_13_status_2_never_enters_the_l1_baseline():
    """SYNTHETIC: eleven status-2 runs (no error) plus one slow success. The
    status-2 runs must not form a baseline, so L1 stays LOG."""
    start = datetime(2026, 10, 1, 12, tzinfo=UTC)
    runs = [ex(start + timedelta(hours=i), status=2, duration_ms=1000) for i in range(11)]
    runs.append(ex(start + timedelta(hours=11), status=1, duration_ms=900_000))
    out = evaluate(payload(synth_scenario(executions=runs)), now=start + timedelta(days=1))
    l1 = signals_for(out, signal_id="L1_DURATION_DRIFT")
    assert l1[0]["tier"] == "LOG"
    assert l1[0]["reason"] == "INSUFFICIENT_BASELINE"
    assert l1[0]["facts"]["post_edit_successful_auto_runs"] == 1


# --- testing window / post-edit scope ------------------------------------


def test_testing_window_excludes_all_run_types():
    """SYNTHETIC: a modify, then failures inside the 2h window, then a
    failure outside it."""
    start = datetime(2026, 10, 1, 12, tzinfo=UTC)
    events = [evt(start, "modify")]
    runs = [
        ex(start + timedelta(minutes=10), status=3, run_type="auto", error_name="X"),
        ex(start + timedelta(minutes=20), status=3, run_type="manual", error_name="X"),
        ex(start + timedelta(hours=3), status=3, run_type="auto", error_name="X"),
    ]
    out = evaluate(payload(synth_scenario(executions=runs, events=events)),
                   now=start + timedelta(hours=4))
    assert signals_for(out, signal_id="I2b_UNRECOVERED_FAILURE")
    assert not signals_for(out, signal_id="I2_CONSECUTIVE_FAILURES")


def test_schedule_is_a_config_change_and_start_stop_are_not():
    start = datetime(2026, 10, 1, 12, tzinfo=UTC)
    runs = [
        ex(start, status=3, error_name="X"),
        ex(start + timedelta(hours=3), status=3, error_name="X"),
    ]
    # start/stop must not reset scope -> both failures count
    events = [evt(start + timedelta(hours=1), "start"), evt(start + timedelta(hours=1), "stop")]
    out = evaluate(payload(synth_scenario(executions=runs, events=events)),
                   now=start + timedelta(hours=4))
    assert signals_for(out, signal_id="I2_CONSECUTIVE_FAILURES")

    # schedule must reset scope -> only the later failure survives
    events = [evt(start + timedelta(minutes=30), "schedule")]
    out = evaluate(payload(synth_scenario(executions=runs, events=events)),
                   now=start + timedelta(hours=4))
    assert signals_for(out, signal_id="I2b_UNRECOVERED_FAILURE")
    assert not signals_for(out, signal_id="I2_CONSECUTIVE_FAILURES")


def test_i1_reconnect_warning_clears_after_a_success():
    start = datetime(2026, 10, 1, 12, tzinfo=UTC)
    events = [evt(start, "warning", "All 9 attempts to reconnect the process failed")]
    out = evaluate(payload(synth_scenario(events=events)), now=start + timedelta(hours=1))
    assert signals_for(out, signal_id="I1_SCENARIO_DISABLED", tier="ESCALATE")

    recovered = evaluate(
        payload(synth_scenario(executions=[ex(start + timedelta(hours=1))], events=events)),
        now=start + timedelta(hours=2),
    )
    assert not signals_for(recovered, signal_id="I1_SCENARIO_DISABLED")


def test_i1_paused_scenario_escalates():
    out = evaluate(payload(synth_scenario(is_paused=True)), now=datetime(2026, 10, 1, tzinfo=UTC))
    sig = signals_for(out, signal_id="I1_SCENARIO_DISABLED")
    assert sig and sig[0]["tier"] == "ESCALATE"
    assert out["status"] == "RED"


# --- structural guarantees -----------------------------------------------


def test_config_dict_holds_every_threshold():
    """SPEC.md: thresholds live in one CONFIG dict."""
    assert CONFIG["l1_scenarios"] == frozenset({6186710, 6241867})
    assert CONFIG["duration_multiplier"] == 1.5
    assert CONFIG["duration_floor_ms"] == 30_000
    assert CONFIG["baseline_runs"] == 5
    assert CONFIG["evaluable_runs_required"] == 5
    assert CONFIG["drift_window"] == 5
    assert CONFIG["drift_flagged_required"] == 2
    assert CONFIG["rescued_failures_threshold"] == 3
    assert CONFIG["rescue_window_days"] == 7
    assert CONFIG["testing_window_hours"] == 2
    assert CONFIG["collection_gap_hours"] == 26


def test_l1_never_escalates(replay_results):
    for row in replay_results:
        for sig in row["result"]["signals"]:
            if sig["signal_id"] == "L1_DURATION_DRIFT":
                assert sig["tier"] != "ESCALATE"


# --- v1.4: rescued failures, not rescue runs -----------------------------


def _rescued(executions, events=()):
    from checks import _scoped
    scenario = synth_scenario(executions=executions, events=events)
    scoped = _scoped(scenario, datetime(2026, 11, 1, tzinfo=UTC), CONFIG)
    return rescued_failures(scoped["all_executions"], scoped["windows"])


def test_v14_many_rescue_runs_for_one_failure_count_once():
    """SYNTHETIC, modelled on the real Split 2 case: one failed auto run
    followed by eight manual attempts is ONE rescued failure."""
    start = datetime(2026, 10, 1, 12, tzinfo=UTC)
    runs = [ex(start, status=3, error_name="X", execution_id="the-failure")]
    runs += [ex(start + timedelta(hours=3 * i + 3), run_type="manual") for i in range(8)]

    assert len(_rescues(runs)) == 8, "all eight are rescue runs"
    rescued = _rescued(runs)
    assert len(rescued) == 1, "but they rescue one failure"
    assert rescued[0]["rescue_runs"] == 8
    assert rescued[0]["failure"]["execution_id"] == "the-failure"

    # One rescued failure is below the threshold of 3, so L2 stays quiet.
    out = evaluate(payload(synth_scenario(executions=runs)), now=start + timedelta(days=2))
    assert not signals_for(out, signal_id="L2_HUMAN_COMPENSATION")


def test_v14_three_distinct_failures_each_rescued_once_fire():
    """SYNTHETIC, modelled on the real Grade Bookmarks case."""
    start = datetime(2026, 10, 1, 12, tzinfo=UTC)
    runs = []
    for i in range(3):
        runs.append(ex(start + timedelta(days=i), status=3, error_name="X",
                       execution_id=f"fail-{i}"))
        runs.append(ex(start + timedelta(days=i, hours=1), run_type="manual"))
    rescued = _rescued(runs)
    assert len(rescued) == 3
    assert [r["rescue_runs"] for r in rescued] == [1, 1, 1]

    out = evaluate(payload(synth_scenario(executions=runs)), now=start + timedelta(days=4))
    l2 = signals_for(out, signal_id="L2_HUMAN_COMPENSATION")
    assert l2 and l2[0]["tier"] == "RADAR"
    assert l2[0]["facts"]["rescued_failures"] == 3
    assert len(l2[0]["facts"]["failures"]) == 3


def test_v14_rescued_failure_is_timed_by_its_first_rescue():
    """SYNTHETIC: a failure straddling the window boundary.

    To attribute a rescue to a failure, the failure must be the most recent
    decisive auto run before it, so the sequence has to be F1 R1 F2 R2 F3 R3.
    Here F1 is 7 days older than `now` while R1 is minutes old. Timing by the
    failure would drop F1 and leave 2 rescued failures (no RADAR); timing by
    the first rescue keeps all 3 and fires.
    """
    d0 = datetime(2026, 10, 1, 12, tzinfo=UTC)
    late = d0 + timedelta(days=7)
    runs = [
        ex(d0, status=3, error_name="X", execution_id="f1"),
        ex(late, run_type="manual"),
        ex(late + timedelta(hours=1), status=3, error_name="X", execution_id="f2"),
        ex(late + timedelta(hours=2), run_type="manual"),
        ex(late + timedelta(hours=3), status=3, error_name="X", execution_id="f3"),
        ex(late + timedelta(hours=4), run_type="manual"),
    ]
    rescued = _rescued(runs)
    assert [r["failure"]["execution_id"] for r in rescued] == ["f1", "f2", "f3"]
    for r in rescued:
        assert r["first_rescue"]["_at"] > r["failure"]["_at"]

    now = late + timedelta(hours=5)
    # f1 failed more than 7 days ago; its first rescue was 5 hours ago.
    assert (now - rescued[0]["failure"]["_at"]) > timedelta(days=7)
    assert (now - rescued[0]["first_rescue"]["_at"]) < timedelta(days=1)

    out = evaluate(payload(synth_scenario(executions=runs)), now=now)
    l2 = signals_for(out, signal_id="L2_HUMAN_COMPENSATION")
    assert l2, "rescued failures are timed by first rescue, not by the failure"
    assert l2[0]["facts"]["rescued_failures"] == 3


def test_v14_real_data_split2_has_one_rescued_failure_from_eight_rescue_runs():
    """The real Split 2 case that drove the v1.4 change."""
    from checks import _scoped
    fixture = load_fixture()
    split2 = next(s for s in fixture["scenarios"] if s["scenario_id"] == SPLIT2)
    scoped = _scoped(split2, datetime(2026, 9, 24, 23, 59, tzinfo=UTC), CONFIG)
    assert len(rescue_runs(scoped["all_executions"], scoped["windows"])) == 8
    rescued = rescued_failures(scoped["all_executions"], scoped["windows"])
    assert len(rescued) == 1
    assert rescued[0]["failure"]["started_at"].startswith("2026-09-15T12:00")
    assert rescued[0]["rescue_runs"] == 8


def test_v14_real_data_grade_bookmarks_has_three_rescued_failures():
    from checks import _scoped
    fixture = load_fixture()
    grade = next(s for s in fixture["scenarios"] if s["scenario_id"] == GRADE)
    scoped = _scoped(grade, datetime(2026, 9, 24, 23, 59, tzinfo=UTC), CONFIG)
    rescued = rescued_failures(scoped["all_executions"], scoped["windows"])
    assert len(rescued) == 3
    assert [r["failure"]["started_at"][:10] for r in rescued] == [
        "2026-09-13", "2026-09-14", "2026-09-15"
    ]


def test_v14_real_data_split2_is_silent_for_the_whole_replay(replay_results):
    assert not loud_for(replay_results, SPLIT2)


# --- v1.4: L1 facts describe the flagged runs ----------------------------


def _numbers_outside(obj, skip_keys=()):
    """Every number reachable in `obj`, skipping the named sub-objects."""
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in skip_keys:
                continue
            found += _numbers_outside(v, skip_keys)
    elif isinstance(obj, list):
        for v in obj:
            found += _numbers_outside(v, skip_keys)
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        found.append(float(obj))
    return found


def test_v14_sep_18_l1_facts_carry_no_unflagged_duration_outside_latest_run():
    """The case that prompted the change: on 2026-09-18 the latest evaluable
    run was 321.0s against a 298.2s median -- unflagged -- while the RADAR came
    from three earlier runs. 321.0 must appear only inside `latest_run`."""
    out = evaluate(load_fixture(), now=datetime(2026, 9, 18, 16, tzinfo=UTC))
    sig = next(
        s for s in out["signals"]
        if s["signal_id"] == "L1_DURATION_DRIFT" and s["scenario_id"] == PICKS
    )
    assert sig["tier"] == "RADAR"
    facts = sig["facts"]

    # The unflagged latest run is quarantined.
    assert facts["latest_run"] == {
        "at": "2026-09-18T15:00:09Z", "duration_s": 321.0, "flagged": False
    }
    assert 321.0 not in _numbers_outside(facts, skip_keys=("latest_run",))

    # The old flat keys are gone.
    for gone in ("latest_duration_s", "baseline_median_s", "excess_over_median_s"):
        assert gone not in facts

    # The evidence is the three flagged runs, with their own baselines.
    assert facts["flagged_runs_of_last_5"] == 3
    assert [r["duration_s"] for r in facts["flagged_runs"]] == [298.2, 320.9, 307.8]
    assert [r["at"][:10] for r in facts["flagged_runs"]] == [
        "2026-09-15", "2026-09-16", "2026-09-17"
    ]
    for r in facts["flagged_runs"]:
        assert r["duration_s"] > r["baseline_median_s"] * facts["threshold_multiplier"]
        assert r["excess_s"] >= facts["absolute_floor_s"]


def test_v14_a_flagged_latest_run_gets_no_latest_run_key():
    """SYNTHETIC: when the latest evaluable run IS flagged it already appears
    in flagged_runs, so there is nothing to quarantine.

    The spike sits at the END of the series on purpose. A step early in the
    pool gets absorbed by the rolling median within about 5 runs, which is the
    "detects change, not level" limitation SPEC.md records.
    """
    start = datetime(2026, 10, 1, 12, tzinfo=UTC)
    runs = _l1_pool(start, [100_000] * 8 + [400_000] * 2)
    facts = signals_for(
        evaluate(payload(synth_scenario(executions=runs)), now=start + timedelta(days=20)),
        signal_id="L1_DURATION_DRIFT",
    )[0]["facts"]
    assert "latest_run" not in facts
    assert facts["flagged_runs_of_last_5"] == len(facts["flagged_runs"]) == 2
    assert facts["flagged_runs"][-1]["at"].startswith("2026-10-10")


def test_v14_every_l1_signal_in_the_replay_keeps_the_invariant(replay_results):
    """Across all 205 ticks: no L1 signal ever exposes an unflagged duration
    outside latest_run."""
    checked = 0
    for row in replay_results:
        for sig in row["result"]["signals"]:
            if sig["signal_id"] != "L1_DURATION_DRIFT":
                continue
            facts = sig["facts"]
            if "flagged_runs" not in facts:
                continue  # INSUFFICIENT_BASELINE carries counts only
            checked += 1
            latest = facts.get("latest_run")
            if latest:
                assert latest["flagged"] is False
                assert latest["duration_s"] not in _numbers_outside(
                    facts, skip_keys=("latest_run",)
                )
            for r in facts["flagged_runs"]:
                assert r["excess_s"] >= facts["absolute_floor_s"]
    assert checked, "expected at least one L1 signal with a computed window"
