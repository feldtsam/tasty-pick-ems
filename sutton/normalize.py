"""Raw Make API responses -> the normalized shape checks.py expects.

Single source of truth, imported by BOTH `build_fixture.py` (which builds the
historical replay fixture from recorded pages) and `api/index.py` (which
receives live collector payloads). They used to hold separate copies of this
logic; a drift between them would mean the rules were validated against one
normalization and run against another.

Everything SPEC.md "Data realities" says about raw Make data lives here:

  #2  Executions and timeline events arrive in one array with different
      shapes. Executions carry `eventType: "EXECUTION_END"` and their `type`
      means run type; timeline events have no `eventType` and their `type`
      means the event kind. Never read `type` without checking which it is.
  #3  Timeline events carry no `scenarioId`. It is attached from context.
  #4  `endedAt` is sometimes missing, so it is derived from `timestamp` +
      `duration`. No rule depends on it.
  #8  Scenario names change over time, so identity is `scenario_id` and the
      display name comes from the current scenarios list.

This module does NOT redact. Callers redact the raw payload first -- see
redact.redact_all() and the endpoint's step 1. Pure functions, no network.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

__all__ = [
    "derive_ended_at",
    "is_execution",
    "normalize_event",
    "normalize_execution",
    "normalize_raw_payload",
    "scenario_meta",
    "unwrap_logs",
    "unwrap_scenarios",
]


def is_execution(row: dict) -> bool:
    """SPEC.md "Data realities" #2."""
    return row.get("eventType") == "EXECUTION_END"


def derive_ended_at(started_at: Any, duration_ms: Any) -> str | None:
    """SPEC.md #4: derive `ended_at` when Make omits it."""
    if not started_at or duration_ms is None:
        return None
    text = str(started_at)
    text = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        dt = datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return None
    try:
        shifted = dt + timedelta(milliseconds=duration_ms)
    except TypeError:
        return None
    return shifted.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_execution(row: dict) -> dict:
    error = row.get("error") or {}
    if not isinstance(error, dict):
        error = {}
    cause = error.get("causeModule") or {}
    if not isinstance(cause, dict):
        cause = {}
    started = row.get("timestamp")
    return {
        "execution_id": row.get("id"),
        "started_at": started,
        "ended_at": row.get("endedAt") or derive_ended_at(started, row.get("duration")),
        "ended_at_derived": row.get("endedAt") is None,
        "duration_ms": row.get("duration"),
        "status": row.get("status"),
        "run_type": row.get("type"),
        "error_name": error.get("name"),
        "error_message": error.get("message"),
        "cause_module": cause.get("name"),
        "author_name": row.get("authorName"),
    }


def normalize_event(row: dict) -> dict:
    detail = row.get("detail") or {}
    if not isinstance(detail, dict):
        detail = {}
    extra: dict[str, Any] = {}
    if "delay" in detail:
        extra["delay_minutes"] = detail["delay"]
    author = detail.get("author")
    if isinstance(author, dict):
        extra["author"] = author.get("name")
    return {
        "event_id": str(row.get("id") or row.get("imtId")),
        "at": row.get("timestamp"),
        "event_type": row.get("type"),
        "detail": detail.get("reason"),
        "author_name": row.get("authorName"),
        "extra": extra,
    }


# --- unwrapping Make's two response shapes --------------------------------


def _unwrap(raw: Any, key: str) -> list[dict] | None:
    """A bare array, or an object wrapping one under `key`.

    Returns None when the shape is not recognisable, so callers can treat that
    as a skippable problem rather than a crash.
    """
    if isinstance(raw, list):
        rows = raw
    elif isinstance(raw, dict):
        inner = raw.get(key)
        if not isinstance(inner, list):
            return None
        rows = inner
    else:
        return None
    return [r for r in rows if isinstance(r, dict)]


def unwrap_logs(raw_logs: Any) -> list[dict] | None:
    """`GET /api/v2/scenarios/<id>/logs` -- bare array or {"scenarioLogs": [...]}."""
    return _unwrap(raw_logs, "scenarioLogs")


def unwrap_scenarios(raw_scenarios: Any) -> list[dict] | None:
    """`GET /api/v2/scenarios` -- bare array or {"scenarios": [...]}."""
    return _unwrap(raw_scenarios, "scenarios")


# --- the two halves of a raw payload --------------------------------------


def scenario_meta(raw_scenarios: Any, watched_ids) -> tuple[dict[int, dict], list[int]]:
    """Per-watched-scenario name / is_active / is_paused, plus the missing ids.

    Only those three fields are read. The scenarios response carries the whole
    blueprint for every scenario on the team and can be large; nothing else in
    it is evidence for any V1 rule.

    A watched id absent from the response is treated as INACTIVE. That is the
    conservative reading and the one that matters: a scenario Make no longer
    lists is a scenario that is not running, which is exactly what I1 exists to
    escalate.
    """
    rows = unwrap_scenarios(raw_scenarios) or []
    by_id: dict[int, dict] = {}
    for row in rows:
        sid = row.get("id")
        if sid is None:
            continue
        try:
            sid = int(sid)
        except (TypeError, ValueError):
            continue
        if sid not in watched_ids:
            continue
        by_id[sid] = {
            "name": (row.get("name") or f"scenario {sid}").strip(),
            "is_active": bool(row.get("isActive", False)),
            "is_paused": bool(row.get("isPaused", False)),
        }

    missing = [sid for sid in watched_ids if sid not in by_id]
    for sid in missing:
        by_id[sid] = {
            "name": f"scenario {sid}",
            "is_active": False,
            "is_paused": False,
        }
    return by_id, missing


def normalize_raw_payload(payload: dict, watched_ids) -> tuple[list[dict], list[dict]]:
    """Raw collector payload -> (scenarios in checks.py shape, problems).

    Defensive by design: a scenario whose `raw_logs` is malformed or empty is
    SKIPPED and recorded as a problem, and the other scenarios still run. One
    bad Make response must not cost a whole collection.

    `problems` entries are {scenario_id, reason} and become HARNESS_HEALTH LOG
    signals in the endpoint.
    """
    watched = list(watched_ids)
    meta, missing = scenario_meta(payload.get("raw_scenarios"), watched)

    problems: list[dict] = []
    for sid in missing:
        problems.append({"scenario_id": sid, "reason": "MISSING_FROM_SCENARIOS_LIST"})

    scenarios: list[dict] = []
    for entry in payload.get("scenarios") or []:
        if not isinstance(entry, dict):
            problems.append({"scenario_id": None, "reason": "SCENARIO_ENTRY_NOT_AN_OBJECT"})
            continue
        sid = entry.get("scenario_id")
        try:
            sid = int(sid)
        except (TypeError, ValueError):
            problems.append({"scenario_id": entry.get("scenario_id"),
                             "reason": "SCENARIO_ID_NOT_AN_INT"})
            continue

        rows = unwrap_logs(entry.get("raw_logs"))
        if rows is None:
            problems.append({"scenario_id": sid, "reason": "RAW_LOGS_MALFORMED"})
            continue
        if not rows:
            problems.append({"scenario_id": sid, "reason": "RAW_LOGS_EMPTY"})
            continue

        executions, events = [], []
        for row in rows:
            try:
                if is_execution(row):
                    executions.append(normalize_execution(row))
                else:
                    events.append(normalize_event(row))
            except Exception:  # noqa: BLE001 - one bad row is not a bad scenario
                problems.append({"scenario_id": sid, "reason": "LOG_ROW_UNPARSEABLE"})

        executions.sort(key=lambda e: e.get("started_at") or "")
        events.sort(key=lambda e: e.get("at") or "")

        info = meta.get(sid) or {"name": f"scenario {sid}", "is_active": True,
                                 "is_paused": False}
        scenarios.append(
            {
                "scenario_id": sid,
                "name": info["name"],
                "is_active": info["is_active"],
                "is_paused": info["is_paused"],
                "executions": executions,
                "events": events,
            }
        )

    # A watched scenario missing from the scenarios list but still reporting
    # logs is already covered above. One that is missing AND has no usable logs
    # still needs to reach I1, so add it with an empty history.
    seen = {s["scenario_id"] for s in scenarios}
    for sid in missing:
        if sid not in seen:
            info = meta[sid]
            scenarios.append(
                {
                    "scenario_id": sid,
                    "name": info["name"],
                    "is_active": info["is_active"],
                    "is_paused": info["is_paused"],
                    "executions": [],
                    "events": [],
                }
            )

    scenarios.sort(key=lambda s: watched.index(s["scenario_id"])
                   if s["scenario_id"] in watched else len(watched))
    return scenarios, problems
