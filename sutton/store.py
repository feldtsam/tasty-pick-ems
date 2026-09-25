"""Storage client for the three `sutton-*` Lovable routes.

SPEC.md "Storage" -> "Signing contract (as built)":
  - HMAC-SHA256 with SUTTON_WRITE_SECRET, hex, in the `X-Signature` header.
    A `sha256=` prefix is optional; we send the bare hex.
  - POST routes sign the EXACT raw request body bytes, and accept a JSON
    array only. We therefore build the bytes once and send those same bytes,
    never a re-serialised dict, because any whitespace or key-order change
    would invalidate the signature.
  - sutton-state-read signs the string `ts=<unix seconds>` and rejects a `ts`
    more than 300s from server time.

These are the only hosts Sutton talks to besides the Anthropic API. Nothing
here can reach a production TPE endpoint.

BASE_URL note: `tastypickems.lovable.app` answers every request with a
redirect to `tastypickems.com` (302 for GET, 307 for POST). Following a
redirect risks dropping the `X-Signature` header on the hop, so we address
the canonical host directly and never follow redirects.

Every function returns (ok, data, error) and never raises. Storage failing
must degrade Sutton, not stop it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any

__all__ = [
    "BASE_URL",
    "OBSERVATIONS_ROUTE",
    "INCIDENTS_ROUTE",
    "STATE_ROUTE",
    "rebuild_scenarios",
    "read_state",
    "shape_incidents",
    "shape_observations",
    "sign",
    "write_incidents",
    "write_observations",
]

BASE_URL = "https://tastypickems.com/api/public"
OBSERVATIONS_ROUTE = f"{BASE_URL}/sutton-observations-write"
INCIDENTS_ROUTE = f"{BASE_URL}/sutton-incidents-write"
STATE_ROUTE = f"{BASE_URL}/sutton-state-read"

TIMEOUT_SECONDS = 20
STATE_TIMEOUT_SECONDS = 30


def _secret() -> str | None:
    return os.environ.get("SUTTON_WRITE_SECRET")


def sign(payload: bytes | str, secret: str) -> str:
    """HMAC-SHA256 hex over exactly these bytes."""
    data = payload if isinstance(payload, bytes) else payload.encode("utf-8")
    return hmac.new(secret.encode("utf-8"), data, hashlib.sha256).hexdigest()


# --- shaping --------------------------------------------------------------


def shape_observations(payload: dict) -> list[dict]:
    """Normalized Make payload -> sutton_observations rows.

    The payload must already have been through redact.py. Unique on
    (scenario_id, kind, source_id), so re-sends are ignored by the route and
    this function is safe to call with overlapping windows.
    """
    collected_at = payload.get("collected_at")
    rows: list[dict] = []
    for scenario in payload.get("scenarios") or []:
        sid = scenario.get("scenario_id")
        for ex in scenario.get("executions") or []:
            rows.append(
                {
                    "scenario_id": sid,
                    "kind": "execution",
                    "source_id": str(ex.get("execution_id")),
                    "occurred_at": ex.get("started_at"),
                    "ended_at": ex.get("ended_at"),
                    "duration_ms": ex.get("duration_ms"),
                    "status": ex.get("status"),
                    "run_type": ex.get("run_type"),
                    "event_type": None,
                    "error_name": ex.get("error_name"),
                    "error_message": ex.get("error_message"),
                    "cause_module": ex.get("cause_module"),
                    "detail": None,
                    "collected_at": collected_at,
                    "extra": ex.get("extra") or {},
                }
            )
        for ev in scenario.get("events") or []:
            rows.append(
                {
                    "scenario_id": sid,
                    "kind": "event",
                    "source_id": str(ev.get("event_id")),
                    "occurred_at": ev.get("at"),
                    "ended_at": None,
                    "duration_ms": None,
                    "status": None,
                    "run_type": None,
                    "event_type": ev.get("event_type"),
                    "error_name": None,
                    "error_message": None,
                    "cause_module": None,
                    "detail": ev.get("detail"),
                    "collected_at": collected_at,
                    "extra": ev.get("extra") or {},
                }
            )
    return rows


def shape_incidents(evaluation: dict, packet: dict, *, interpretation: dict | None,
                    model_name: str | None, llm_ok: bool, shadow: bool,
                    emailed: bool, detected_at: str,
                    record_type: str = "signal") -> list[dict]:
    """checks.py signals -> sutton_incidents rows (append-only)."""
    rows = []
    for sig in evaluation.get("signals") or []:
        if sig.get("tier") == "LOG" and sig.get("class") != "HARNESS_HEALTH":
            continue  # LOG inspection/learning noise is not an incident
        rows.append(
            {
                "detected_at": detected_at,
                "record_type": record_type,
                "signal_id": sig.get("signal_id"),
                "signal_class": sig.get("class"),
                "scenario_id": sig.get("scenario_id"),
                "tier": sig.get("tier"),
                "status_color": evaluation.get("status"),
                "evidence": {
                    "facts": sig.get("facts") or {},
                    "reason": sig.get("reason"),
                    "recent_edits": sig.get("recent_edits") or [],
                    "packet_date": packet.get("date"),
                },
                "interpretation": interpretation,
                "model_name": model_name,
                "llm_ok": bool(llm_ok),
                "shadow": bool(shadow),
                "emailed": bool(emailed),
            }
        )
    return rows


def rebuild_scenarios(observations: list[dict], payload: dict) -> list[dict]:
    """sutton_observations rows -> the shape checks.py expects.

    Baselines are computed from Sutton's own stored copy (SPEC.md Flow), so
    this is the input the rules actually run on. `is_active` / `is_paused` and
    the display name are not stored per-observation, so they come from the
    incoming payload; a scenario seen only in storage defaults to
    active/not-paused rather than risking a false I1 escalation.
    """
    meta = {
        s.get("scenario_id"): s
        for s in (payload.get("scenarios") or [])
        if s.get("scenario_id") is not None
    }

    # Dedupe on the table's own unique constraint, so a caller can safely pass
    # stored rows AND the rows it just tried to store. That overlap is the
    # normal case: if the observation write failed, the stored copy is missing
    # this run's data, and running the rules on it alone would report GREEN on
    # a scenario that just failed twice.
    deduped: dict[tuple, dict] = {}
    for row in observations or []:
        if row.get("scenario_id") is None:
            continue
        deduped[(row.get("scenario_id"), row.get("kind"), row.get("source_id"))] = row

    grouped: dict[Any, dict] = {}
    for row in deduped.values():
        sid = row.get("scenario_id")
        bucket = grouped.setdefault(sid, {"executions": [], "events": []})
        if row.get("kind") == "execution":
            bucket["executions"].append(
                {
                    "execution_id": row.get("source_id"),
                    "started_at": row.get("occurred_at"),
                    "ended_at": row.get("ended_at"),
                    "duration_ms": row.get("duration_ms"),
                    "status": row.get("status"),
                    "run_type": row.get("run_type"),
                    "error_name": row.get("error_name"),
                    "error_message": row.get("error_message"),
                    "cause_module": row.get("cause_module"),
                }
            )
        elif row.get("kind") == "event":
            bucket["events"].append(
                {
                    "event_id": row.get("source_id"),
                    "at": row.get("occurred_at"),
                    "event_type": row.get("event_type"),
                    "detail": row.get("detail"),
                }
            )

    out = []
    for sid in sorted(set(grouped) | set(meta), key=lambda v: str(v)):
        info = meta.get(sid) or {}
        bucket = grouped.get(sid) or {"executions": [], "events": []}
        out.append(
            {
                "scenario_id": sid,
                "name": info.get("name") or f"scenario {sid}",
                "is_active": bool(info.get("is_active", True)),
                "is_paused": bool(info.get("is_paused", False)),
                "executions": bucket["executions"],
                "events": bucket["events"],
            }
        )
    return out


# --- transport ------------------------------------------------------------


def _post_array(url: str, rows: list[dict]) -> tuple[bool, Any, str | None]:
    secret = _secret()
    if not secret:
        return False, None, "SUTTON_WRITE_SECRET is not configured"
    if not rows:
        return True, {"skipped": "no rows"}, None

    try:
        import requests
    except Exception as exc:  # noqa: BLE001
        return False, None, f"requests unavailable: {exc}"

    # Build the bytes ONCE and send exactly these -- the route signs the raw
    # body, so re-serialising would break the signature.
    body = json.dumps(rows, separators=(",", ":"), sort_keys=True).encode("utf-8")
    try:
        resp = requests.post(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Signature": sign(body, secret),
            },
            timeout=TIMEOUT_SECONDS,
            allow_redirects=False,
        )
    except Exception as exc:  # noqa: BLE001
        return False, None, f"{type(exc).__name__}: {exc}"

    if resp.status_code != 200:
        return False, None, f"HTTP {resp.status_code}"
    try:
        return True, resp.json(), None
    except ValueError:
        return True, {"ok": True}, None


def write_observations(rows: list[dict]) -> tuple[bool, Any, str | None]:
    return _post_array(OBSERVATIONS_ROUTE, rows)


def write_incidents(rows: list[dict]) -> tuple[bool, Any, str | None]:
    return _post_array(INCIDENTS_ROUTE, rows)


def read_state(now: int | None = None) -> tuple[bool, Any, str | None]:
    """Signed GET of sutton-state-read.

    Signs the canonical string `ts=<unix seconds>`. The route rejects a `ts`
    more than 300s from its own clock, so this must not be cached.
    """
    secret = _secret()
    if not secret:
        return False, None, "SUTTON_WRITE_SECRET is not configured"

    try:
        import requests
    except Exception as exc:  # noqa: BLE001
        return False, None, f"requests unavailable: {exc}"

    ts = int(now if now is not None else time.time())
    canonical = f"ts={ts}"
    try:
        resp = requests.get(
            f"{STATE_ROUTE}?{canonical}",
            headers={"X-Signature": sign(canonical, secret)},
            timeout=STATE_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
    except Exception as exc:  # noqa: BLE001
        return False, None, f"{type(exc).__name__}: {exc}"

    if resp.status_code != 200:
        return False, None, f"HTTP {resp.status_code}"
    try:
        data = resp.json()
    except ValueError:
        return False, None, "state response was not JSON"

    return True, {
        "last_collection_at": data.get("last_collection_at"),
        "escalations_last_24h": data.get("escalations_last_24h") or [],
        "observations": data.get("observations") or [],
    }, None
