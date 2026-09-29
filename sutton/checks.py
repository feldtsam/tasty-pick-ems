"""Sutton V1 deterministic rules.

Pure functions. No network, no LLM, no environment access, no clock reads
except the `now` you pass in. Everything here is what SPEC.md calls "code
establishes facts" -- the LLM never sees this module's decisions, it only
receives the evidence packet built from its output.

Code owns tier and status. The LLM never sets either.

Rules implemented (SPEC.md "Deterministic rules"):
  I1  SCENARIO_DISABLED      ESCALATE
  I2  CONSECUTIVE_FAILURES   ESCALATE
  I2b UNRECOVERED_FAILURE    RADAR
  --  RECOVERED              LOG
  L1  DURATION_DRIFT         RADAR (never ESCALATE)
  L2  HUMAN_COMPENSATION     RADAR
  H1  COLLECTION_GAP         RADAR   (HARNESS_HEALTH)
  H2  INTERPRETATION_FAILED  LOG     (HARNESS_HEALTH, raised by the caller)
"""

from __future__ import annotations

import re
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

__all__ = [
    "CONFIG",
    "CONFIG_CHANGE_EVENT_TYPES",
    "evaluate",
    "classify_execution",
    "rescue_runs",
    "rescued_failures",
    "should_deliver_escalation",
    "status_from_signals",
]

# Every threshold in the system lives here. SPEC.md: thresholds change only
# in a deliberate batch review, never in response to a single alert.
CONFIG = {
    # L1 watches only the two scenarios whose run time measures health
    # against a hard timeout. The poller's duration tracks workload (1 vs 542
    # operations) and Split 2 never matures a baseline; both keep inspection
    # and L2. See SPEC.md "Watched scenarios".
    "l1_scenarios": frozenset({6186710, 6241867}),
    "duration_multiplier": 1.5,
    # 30s is 10% of Vercel's 300s function budget. Without this floor a 0.6s
    # run against a 0.2s median counted as drift.
    "duration_floor_ms": 30_000,
    "baseline_runs": 5,
    "evaluable_runs_required": 5,
    "drift_window": 5,
    "drift_flagged_required": 2,
    "rescued_failures_threshold": 3,
    "rescue_window_days": 7,
    "testing_window_hours": 2,
    "collection_gap_hours": 26,
    "recent_edits_days": 7,
}

CONFIG_CHANGE_EVENT_TYPES = frozenset({"modify", "schedule"})

# SPEC.md I1: a "reconnect ... failed" warning. Make's real wording is
# "All 9 attempts to reconnect the process failed".
_RECONNECT_RE = re.compile(r"reconnect.*fail", re.IGNORECASE | re.DOTALL)

CLASS_INSPECTION = "INSPECTION"
CLASS_LEARNING = "LEARNING"
CLASS_HARNESS = "HARNESS_HEALTH"

TIER_ESCALATE = "ESCALATE"
TIER_RADAR = "RADAR"
TIER_LOG = "LOG"

_TIER_RANK = {TIER_LOG: 0, TIER_RADAR: 1, TIER_ESCALATE: 2}


# --- time helpers ---------------------------------------------------------


def _parse(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- execution status -----------------------------------------------------


def _has_error(execution: dict) -> bool:
    return bool(execution.get("error_name"))


def classify_execution(execution: dict) -> str:
    """'success' | 'failure' | 'ignored', per SPEC.md "Execution status".

    Success is status 1 only. Failure is status 3, or status 2 carrying an
    error object. Status 2 with no error is ignored by every rule -- it does
    not enter the L1 baseline and does not recover a failure streak.
    """
    status = execution.get("status")
    if status == 1:
        return "success"
    if status == 3:
        return "failure"
    if status == 2:
        return "failure" if _has_error(execution) else "ignored"
    return "ignored"


# --- scope ----------------------------------------------------------------


def _testing_windows(events: Iterable[dict], hours: int) -> list[tuple[datetime, datetime]]:
    windows = []
    for ev in events:
        if ev.get("event_type") not in CONFIG_CHANGE_EVENT_TYPES:
            continue
        at = _parse(ev.get("at"))
        if at is None:
            continue
        windows.append((at, at + timedelta(hours=hours)))
    return windows


def _in_testing_window(when: datetime, windows: list[tuple[datetime, datetime]]) -> bool:
    return any(start <= when < end for start, end in windows)


def _scoped(scenario: dict, now: datetime, config: dict) -> dict:
    """Split one scenario's records into the views the rules need."""
    hours = config["testing_window_hours"]

    executions = []
    for ex in scenario.get("executions") or []:
        started = _parse(ex.get("started_at"))
        if started is None or started > now:
            continue
        executions.append({**ex, "_at": started})
    executions.sort(key=lambda e: e["_at"])

    events = []
    for ev in scenario.get("events") or []:
        at = _parse(ev.get("at"))
        if at is None or at > now:
            continue
        events.append({**ev, "_at": at})
    events.sort(key=lambda e: e["_at"])

    windows = _testing_windows(events, hours)

    config_changes = [e for e in events if e.get("event_type") in CONFIG_CHANGE_EVENT_TYPES]
    last_change = config_changes[-1]["_at"] if config_changes else None

    def in_post_edit(when: datetime) -> bool:
        if last_change is not None and when <= last_change:
            return False
        return not _in_testing_window(when, windows)

    return {
        "all_executions": executions,
        "all_events": events,
        "windows": windows,
        "config_changes": config_changes,
        "last_change": last_change,
        "post_edit_executions": [e for e in executions if in_post_edit(e["_at"])],
        "post_edit_events": [e for e in events if in_post_edit(e["_at"])],
    }


def _recent_edits(scoped: dict, now: datetime, config: dict) -> list[str]:
    cutoff = now - timedelta(days=config["recent_edits_days"])
    return [_iso(e["_at"]) for e in scoped["config_changes"] if e["_at"] >= cutoff]


# --- individual rules -----------------------------------------------------


def _signal(signal_id, cls, tier, scenario, facts, recent_edits, reason=None):
    sig = {
        "signal_id": signal_id,
        "class": cls,
        "tier": tier,
        "scenario_id": scenario["scenario_id"],
        "scenario": scenario.get("name"),
        "facts": facts,
        "recent_edits": recent_edits,
    }
    if reason:
        sig["reason"] = reason
    return sig


def _rule_i1(scenario, scoped, edits):
    is_active = scenario.get("is_active", True)
    is_paused = scenario.get("is_paused", False)
    if not is_active or is_paused:
        return _signal(
            "I1_SCENARIO_DISABLED",
            CLASS_INSPECTION,
            TIER_ESCALATE,
            scenario,
            {"is_active": bool(is_active), "is_paused": bool(is_paused)},
            edits,
            reason="SCENARIO_NOT_RUNNING",
        )

    warnings = [
        e
        for e in scoped["post_edit_events"]
        if e.get("event_type") == "warning" and _RECONNECT_RE.search(str(e.get("detail") or ""))
    ]
    if not warnings:
        return None
    last_warning = warnings[-1]["_at"]
    recovered = any(
        classify_execution(e) == "success" and e["_at"] > last_warning
        for e in scoped["post_edit_executions"]
    )
    if recovered:
        return None
    return _signal(
        "I1_SCENARIO_DISABLED",
        CLASS_INSPECTION,
        TIER_ESCALATE,
        scenario,
        {
            "reconnect_warning_at": _iso(last_warning),
            "successful_runs_since_warning": 0,
            "is_active": bool(is_active),
            "is_paused": bool(is_paused),
        },
        edits,
        reason="RECONNECT_FAILED_UNRECOVERED",
    )


def _trailing_failures(executions: list[dict]) -> list[dict]:
    """Unrecovered failures at the end of the series.

    Ignored runs (status 2 with no error) are skipped: they neither count as
    failures nor recover a streak.
    """
    trailing: list[dict] = []
    for ex in reversed(executions):
        kind = classify_execution(ex)
        if kind == "ignored":
            continue
        if kind == "failure":
            trailing.append(ex)
            continue
        break  # a success ends the streak
    trailing.reverse()
    return trailing


def _rule_i2(scenario, scoped, edits):
    executions = scoped["post_edit_executions"]
    trailing = _trailing_failures(executions)
    had_failure = any(classify_execution(e) == "failure" for e in executions)

    if len(trailing) >= 2:
        return _signal(
            "I2_CONSECUTIVE_FAILURES",
            CLASS_INSPECTION,
            TIER_ESCALATE,
            scenario,
            {
                "consecutive_failures": len(trailing),
                "first_failure_at": _iso(trailing[0]["_at"]),
                "latest_failure_at": _iso(trailing[-1]["_at"]),
                "latest_error_name": trailing[-1].get("error_name"),
                "latest_cause_module": trailing[-1].get("cause_module"),
            },
            edits,
        )
    if len(trailing) == 1:
        return _signal(
            "I2b_UNRECOVERED_FAILURE",
            CLASS_INSPECTION,
            TIER_RADAR,
            scenario,
            {
                "consecutive_failures": 1,
                "latest_failure_at": _iso(trailing[0]["_at"]),
                "latest_error_name": trailing[0].get("error_name"),
                "latest_cause_module": trailing[0].get("cause_module"),
            },
            edits,
        )
    if had_failure:
        failures = [e for e in executions if classify_execution(e) == "failure"]
        return _signal(
            "I2_RECOVERED",
            CLASS_INSPECTION,
            TIER_LOG,
            scenario,
            {
                "failures_in_scope": len(failures),
                "latest_failure_at": _iso(failures[-1]["_at"]),
            },
            edits,
            reason="RECOVERED",
        )
    return None


def _rule_l1(scenario, scoped, edits, config):
    # Scope gate first: L1 does not apply to every watched scenario.
    if scenario["scenario_id"] not in config["l1_scenarios"]:
        return None

    baseline_n = config["baseline_runs"]
    floor_ms = config["duration_floor_ms"]
    pool = [
        e
        for e in scoped["post_edit_executions"]
        if e.get("run_type") == "auto" and classify_execution(e) == "success"
    ]

    # An evaluable run is a successful post-edit auto run with 5 prior
    # post-edit successful auto runs behind it.
    evaluable = []
    for i in range(baseline_n, len(pool)):
        priors = [p["duration_ms"] for p in pool[i - baseline_n : i] if p.get("duration_ms") is not None]
        if len(priors) < baseline_n:
            continue
        median = statistics.median(priors)
        duration = pool[i].get("duration_ms")
        if duration is None:
            continue
        # Flagged needs BOTH the ratio and the absolute floor.
        over_ratio = duration > median * config["duration_multiplier"]
        over_floor = (duration - median) >= floor_ms
        evaluable.append(
            {
                "run": pool[i],
                "duration_ms": duration,
                "median_ms": median,
                "excess_ms": duration - median,
                "flagged": over_ratio and over_floor,
            }
        )

    # The 2-of-5 test needs a full window of 5 evaluable runs.
    if len(evaluable) < config["evaluable_runs_required"]:
        return _signal(
            "L1_DURATION_DRIFT",
            CLASS_LEARNING,
            TIER_LOG,
            scenario,
            {
                "post_edit_successful_auto_runs": len(pool),
                "evaluable_runs": len(evaluable),
                "evaluable_runs_required": config["evaluable_runs_required"],
                "baseline_runs_required": baseline_n,
            },
            edits,
            reason="INSUFFICIENT_BASELINE",
        )

    window = evaluable[-config["drift_window"] :]
    flagged = [w for w in window if w["flagged"]]
    latest = window[-1]

    # SPEC.md "L1 facts describe the flagged runs, not the latest run."
    # The latest evaluable run is often NOT one of the flagged ones -- on
    # 2026-09-18 it was 321s against a 298s median, unflagged, while the RADAR
    # came from three earlier runs. Publishing its numbers as bare facts let
    # the anti-fabrication validator pass an interpretation built on the wrong
    # run, so unflagged durations now appear only inside `latest_run`.
    facts = {
        "flagged_runs": [
            {
                "at": _iso(w["run"]["_at"]),
                "duration_s": round(w["duration_ms"] / 1000, 1),
                "baseline_median_s": round(w["median_ms"] / 1000, 1),
                "excess_s": round(w["excess_ms"] / 1000, 1),
            }
            for w in flagged
        ],
        "flagged_runs_of_last_5": len(flagged),
        "runs_considered": len(window),
        "threshold_multiplier": config["duration_multiplier"],
        "absolute_floor_s": round(config["duration_floor_ms"] / 1000, 1),
    }
    if not latest["flagged"]:
        facts["latest_run"] = {
            "at": _iso(latest["run"]["_at"]),
            "duration_s": round(latest["duration_ms"] / 1000, 1),
            "flagged": False,
        }

    if len(flagged) >= config["drift_flagged_required"]:
        return _signal(
            "L1_DURATION_DRIFT", CLASS_LEARNING, TIER_RADAR, scenario, facts, edits
        )
    return _signal(
        "L1_DURATION_DRIFT",
        CLASS_LEARNING,
        TIER_LOG,
        scenario,
        facts,
        edits,
        reason="WITHIN_BASELINE",
    )


def rescue_runs(executions: list[dict], windows: list[tuple[datetime, datetime]]) -> list[dict]:
    """Manual executions that rescued a failed automatic run.

    SPEC.md L2: a rescue run is a `manual` execution, outside any testing
    window, whose most recent prior `auto` execution of the same scenario
    failed. A manual run that follows a *successful* auto run is development,
    not compensation, and is not counted.

    Testing-window executions are ignored by every rule, so one cannot be the
    "most recent prior auto" either. Nor can a status-2-without-error run,
    which is neither success nor failure.

    `executions` must be ascending by start time and already `now`-filtered.
    """
    out = []
    last_decisive_auto = None  # most recent auto that was success or failure
    for ex in executions:
        inside = _in_testing_window(ex["_at"], windows)
        kind = classify_execution(ex)
        if ex.get("run_type") == "manual":
            if not inside and last_decisive_auto is not None:
                if classify_execution(last_decisive_auto) == "failure":
                    out.append({**ex, "_rescued": last_decisive_auto})
        elif ex.get("run_type") == "auto" and not inside and kind != "ignored":
            last_decisive_auto = ex
    return out


def rescued_failures(
    executions: list[dict], windows: list[tuple[datetime, datetime]]
) -> list[dict]:
    """Distinct failed auto executions that received at least one rescue run.

    SPEC.md L2 (v1.4): several rescue runs aimed at the same failure count
    once. On the weekly Split 2, one Sep 15 failure drew 8 rescue runs over
    four days of fixing -- that is one fix taking several tries, not
    automation needing a human again and again.

    Each rescued failure is timed by its FIRST rescue run. Returned in
    first-rescue order.
    """
    by_failure: dict[str, dict] = {}
    order: list[str] = []
    last_decisive_auto = None

    for ex in executions:
        inside = _in_testing_window(ex["_at"], windows)
        kind = classify_execution(ex)
        if ex.get("run_type") == "manual":
            if inside or last_decisive_auto is None:
                continue
            if classify_execution(last_decisive_auto) != "failure":
                continue
            key = last_decisive_auto.get("execution_id") or _iso(last_decisive_auto["_at"])
            entry = by_failure.get(key)
            if entry is None:
                entry = {
                    "failure": last_decisive_auto,
                    "first_rescue": ex,
                    "rescue_runs": 0,
                }
                by_failure[key] = entry
                order.append(key)
            entry["rescue_runs"] += 1
        elif ex.get("run_type") == "auto" and not inside and kind != "ignored":
            last_decisive_auto = ex

    return [by_failure[k] for k in order]


def _rule_l2(scenario, scoped, now, edits, config):
    """L2 is deliberately NOT reset by edits -- editing and re-running is
    itself part of the compensation pattern. It counts rescued failures."""
    rescued = rescued_failures(scoped["all_executions"], scoped["windows"])
    cutoff = now - timedelta(days=config["rescue_window_days"])
    recent = [r for r in rescued if r["first_rescue"]["_at"] >= cutoff]
    if len(recent) < config["rescued_failures_threshold"]:
        return None
    return _signal(
        "L2_HUMAN_COMPENSATION",
        CLASS_LEARNING,
        TIER_RADAR,
        scenario,
        {
            "rescued_failures": len(recent),
            "rescue_runs": sum(r["rescue_runs"] for r in recent),
            "window_days": config["rescue_window_days"],
            "failures": [
                {
                    "failed_at": _iso(r["failure"]["_at"]),
                    "first_rescue_at": _iso(r["first_rescue"]["_at"]),
                    "rescue_runs": r["rescue_runs"],
                }
                for r in recent
            ],
        },
        edits,
    )


def _rule_h1(payload, state, now, config):
    last = _parse((state or {}).get("last_collection_at"))
    if last is None:
        return None
    gap_hours = (now - last).total_seconds() / 3600.0
    if gap_hours <= config["collection_gap_hours"]:
        return None
    return {
        "signal_id": "H1_COLLECTION_GAP",
        "class": CLASS_HARNESS,
        "tier": TIER_RADAR,
        "scenario_id": None,
        "scenario": None,
        "facts": {
            "gap_hours": round(gap_hours, 1),
            "threshold_hours": config["collection_gap_hours"],
            "last_collection_at": _iso(last),
        },
        "recent_edits": [],
    }


# --- status ---------------------------------------------------------------


def should_deliver_escalation(signal: dict, state: dict | None) -> bool:
    """SPEC.md "Escalation delivery": an ESCALATE email fires only when no
    escalation for the same signal_id + scenario_id is already listed in
    state.escalations_last_24h. Re-sending the same payload therefore cannot
    produce a second email (acceptance test 10)."""
    if signal.get("tier") != TIER_ESCALATE:
        return False
    recent = (state or {}).get("escalations_last_24h") or []
    for prior in recent:
        if (
            prior.get("signal_id") == signal["signal_id"]
            and prior.get("scenario_id") == signal["scenario_id"]
        ):
            return False
    return True


def status_from_signals(signals: Iterable[dict]) -> str:
    """GREEN / YELLOW / RED from TPE signals only.

    HARNESS_HEALTH is reported under its own heading and never moves the TPE
    status line (SPEC.md, and acceptance test 11).
    """
    worst = TIER_LOG
    for sig in signals:
        if sig.get("class") == CLASS_HARNESS:
            continue
        if _TIER_RANK[sig["tier"]] > _TIER_RANK[worst]:
            worst = sig["tier"]
    return {TIER_ESCALATE: "RED", TIER_RADAR: "YELLOW", TIER_LOG: "GREEN"}[worst]


# --- entry point ----------------------------------------------------------


def evaluate(payload: dict, state: dict | None = None, now: Any = None, config: dict | None = None) -> dict:
    """Run every rule over a normalized payload.

    Only records at or before `now` are considered, which is what makes the
    historical replay honest: each tick sees only what existed then.
    """
    cfg = dict(CONFIG)
    if config:
        cfg.update(config)

    now_dt = _parse(now) or _parse(payload.get("collected_at")) or datetime.now(timezone.utc)

    signals: list[dict] = []
    for scenario in payload.get("scenarios") or []:
        scoped = _scoped(scenario, now_dt, cfg)
        edits = _recent_edits(scoped, now_dt, cfg)

        i1 = _rule_i1(scenario, scoped, edits)
        if i1:
            signals.append(i1)
        i2 = _rule_i2(scenario, scoped, edits)
        if i2:
            signals.append(i2)
        l1 = _rule_l1(scenario, scoped, edits, cfg)
        if l1:
            signals.append(l1)
        l2 = _rule_l2(scenario, scoped, now_dt, edits, cfg)
        if l2:
            signals.append(l2)

    h1 = _rule_h1(payload, state, now_dt, cfg)
    if h1:
        signals.append(h1)

    tpe = [s for s in signals if s["class"] != CLASS_HARNESS]
    harness = [s for s in signals if s["class"] == CLASS_HARNESS]

    return {
        "evaluated_at": _iso(now_dt),
        "status": status_from_signals(signals),
        "watched_scenarios": len(payload.get("scenarios") or []),
        "signals": signals,
        "tpe_signals": tpe,
        "harness_signals": harness,
    }
