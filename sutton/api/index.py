"""Sutton's HTTP surface: POST /api/sutton-run plus a paired GET health check.

Mirrors the Flask-on-Vercel shape used by nfl/api/index.py and
pipeline/api/index.py -- a module-level `app`, a sys.path bootstrap so sibling
modules import cleanly under Vercel's loader, an auth helper returning a Flask
response tuple, and a GET health check paired with every POST route.

It imports NOTHING from nfl/ or pipeline/. sutton/ is self-contained, which is
what keeps a Sutton failure unable to touch production.

Flow (SPEC.md):
    redact -> validate input -> store observations -> read state
    -> checks on STORED observations -> packet
    -> (interpret -> validate) when status != GREEN
    -> store incidents -> respond

Two rules that shape the whole file:
  - redact.py runs FIRST, before anything is stored, logged or inspected.
    The raw request body is never logged, not even on error.
  - Code owns tier and status. The LLM cannot change either, and a rejected
    interpretation still produces a correct email.
"""

from __future__ import annotations

import hmac
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Vercel loads this file directly, so api/ and its parent both need to be
# importable. Same bootstrap, same reason, as nfl/api/index.py.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, jsonify, request  # noqa: E402

import store  # noqa: E402
from checks import CLASS_HARNESS, TIER_LOG, evaluate, should_deliver_escalation  # noqa: E402
from interpret import interpret  # noqa: E402
from packet import build_packet  # noqa: E402
from redact import redact_free_text  # noqa: E402
from render import render_email  # noqa: E402
from validate import validate_interpretation  # noqa: E402

app = Flask(__name__)

WATCHED_SCENARIOS = (6186710, 6241867, 6152892, 6079077)
VALID_MODES = ("collect", "daily_radar")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _shadow_enabled() -> bool:
    return (os.environ.get("SUTTON_SHADOW") or "").strip().lower() not in (
        "false",
        "0",
        "no",
        "",
    )


def check_incoming_secret():
    """Returns a Flask (response, status) tuple on failure, else None."""
    expected = os.environ.get("SUTTON_INCOMING_SECRET")
    if not expected:
        return jsonify({"error": "SUTTON_INCOMING_SECRET is not configured"}), 500
    provided = request.headers.get("X-Sutton-Secret")
    if not provided or not hmac.compare_digest(provided, expected):
        return jsonify({"error": "Missing or invalid X-Sutton-Secret header"}), 401
    return None


def _harness_signal(signal_id: str, tier: str, facts: dict, reason: str | None = None):
    sig = {
        "signal_id": signal_id,
        "class": CLASS_HARNESS,
        "tier": tier,
        "scenario_id": None,
        "scenario": None,
        "facts": facts,
        "recent_edits": [],
    }
    if reason:
        sig["reason"] = reason
    return sig


def _validate_input(payload) -> str | None:
    if not isinstance(payload, dict):
        return "body must be a JSON object"
    mode = payload.get("mode")
    if mode not in VALID_MODES:
        return f"mode must be one of {VALID_MODES}"
    if not payload.get("collected_at"):
        return "collected_at is required"
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        return "scenarios must be a non-empty array"
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            return "each scenario must be an object"
        if scenario.get("scenario_id") is None:
            return "each scenario needs a scenario_id"
        for key in ("executions", "events"):
            value = scenario.get(key)
            if value is not None and not isinstance(value, list):
                return f"{key} must be an array when present"
    return None


@app.route("/api/sutton-run", methods=["POST"])
def sutton_run():
    auth_failure = check_incoming_secret()
    if auth_failure is not None:
        return auth_failure

    # --- STEP 1: redact, before anything else touches the payload ---------
    # request.get_json() parses but does not log. The raw body is never
    # printed anywhere in this function, on any path.
    try:
        raw = request.get_json(force=True, silent=True)
    except Exception:  # noqa: BLE001
        raw = None
    if raw is None:
        return jsonify({"error": "body must be valid JSON"}), 400

    payload = redact_free_text(raw)
    del raw  # nothing downstream may reach the unredacted object

    problem = _validate_input(payload)
    if problem:
        return jsonify({"error": problem}), 400

    mode = payload["mode"]
    shadow = _shadow_enabled()
    harness_extra = []
    storage_ok = True

    # --- STEP 2: store observations (idempotent upsert) -------------------
    observation_rows = store.shape_observations(payload)
    obs_ok, obs_data, obs_error = store.write_observations(observation_rows)
    if not obs_ok:
        storage_ok = False
        harness_extra.append(
            _harness_signal(
                "H_OBSERVATION_WRITE_FAILED",
                TIER_LOG,
                {"rows_attempted": len(observation_rows), "error": obs_error},
                reason="STORAGE_DEGRADED",
            )
        )

    # --- STEP 3: read state (includes 8 days of stored observations) ------
    state_ok, state, state_error = store.read_state()
    if state_ok:
        # Stored rows PLUS this run's rows, deduped on the table's unique key.
        # The overlap is deliberate: if the observation write above failed, the
        # stored copy does not contain this run's executions, and running the
        # rules on it alone would report GREEN on a scenario that just failed
        # twice. Merging is safe because the upsert is idempotent.
        scenarios_for_checks = store.rebuild_scenarios(
            list(state["observations"]) + observation_rows, payload
        )
    else:
        storage_ok = False
        # Degraded: the payload alone is a much shorter history, so baselines
        # may be immature. Say so rather than silently reporting on it.
        scenarios_for_checks = store.rebuild_scenarios(observation_rows, payload)
        state = {"last_collection_at": None, "escalations_last_24h": [], "observations": []}
        harness_extra.append(
            _harness_signal(
                "H_STATE_READ_FAILED",
                TIER_LOG,
                {"error": state_error, "baseline": "payload_only"},
                reason="DEGRADED_BASELINE",
            )
        )

    # --- STEP 4: checks on stored observations ---------------------------
    evaluation = evaluate(
        {
            "collected_at": payload["collected_at"],
            "mode": mode,
            "scenarios": scenarios_for_checks,
        },
        state={"last_collection_at": state.get("last_collection_at")},
        now=payload["collected_at"],
    )
    for sig in harness_extra:
        evaluation["signals"].append(sig)
        evaluation["harness_signals"].append(sig)

    # --- STEP 5: packet --------------------------------------------------
    packet = build_packet(
        evaluation, watched_scenarios=len(WATCHED_SCENARIOS)
    )
    status = packet["status"]

    # --- STEP 6: one LLM call, only when status != GREEN -----------------
    interpretation = None
    llm_ok = True
    model_name = None
    if status != "GREEN":
        ok, raw_text, model_name, llm_error = interpret(packet)
        reason = llm_error
        if ok:
            ok, interpretation, reason = validate_interpretation(raw_text, packet)
        if not ok:
            llm_ok = False
            interpretation = None
            h2 = _harness_signal(
                "H2_INTERPRETATION_FAILED",
                TIER_LOG,
                {"reason": reason, "model_configured": bool(os.environ.get("SUTTON_MODEL"))},
                reason="INTERPRETATION_FAILED",
            )
            evaluation["signals"].append(h2)
            evaluation["harness_signals"].append(h2)

    # --- STEP 7: delivery decisions --------------------------------------
    prior_escalations = state.get("escalations_last_24h") or []
    new_escalations = [
        sig
        for sig in evaluation["tpe_signals"]
        if should_deliver_escalation(sig, {"escalations_last_24h": prior_escalations})
    ]
    deliver_escalation = bool(new_escalations) and not shadow and mode == "collect"
    deliver_radar = mode == "daily_radar"

    subject, body = render_email(
        packet,
        interpretation,
        harness_signals=evaluation["harness_signals"],
        shadow=shadow and mode == "daily_radar",
    )

    # --- STEP 8: store incidents (append-only) ---------------------------
    incident_rows = store.shape_incidents(
        evaluation,
        packet,
        interpretation=interpretation,
        model_name=model_name,
        llm_ok=llm_ok,
        shadow=shadow,
        emailed=deliver_escalation or deliver_radar,
        detected_at=_now_iso(),
        record_type="radar" if mode == "daily_radar" else "signal",
    )
    inc_ok, inc_data, inc_error = store.write_incidents(incident_rows)
    if not inc_ok:
        storage_ok = False

    # --- STEP 9: respond -------------------------------------------------
    return jsonify(
        {
            "status": status,
            "shadow": shadow,
            "deliver_escalation": deliver_escalation,
            "deliver_radar": deliver_radar,
            "subject": subject,
            "body": body,
            "storage_ok": storage_ok,
            "llm_ok": llm_ok,
            "signals": [
                {
                    "signal_id": s["signal_id"],
                    "class": s["class"],
                    "tier": s["tier"],
                    "scenario_id": s.get("scenario_id"),
                }
                for s in evaluation["signals"]
                if s["tier"] != TIER_LOG or s["class"] == CLASS_HARNESS
            ],
            "new_escalations": [
                {"signal_id": s["signal_id"], "scenario_id": s.get("scenario_id")}
                for s in new_escalations
            ],
            "observations_written": len(observation_rows),
            "incidents_written": len(incident_rows) if inc_ok else 0,
            # The routes' own responses, so a smoke test can confirm rows
            # actually landed. sutton-state-read only returns the WATCHED
            # scenarios, so a synthetic scenario_id is invisible there.
            "storage_detail": {
                "observations": obs_data if obs_ok else None,
                "incidents": inc_data if inc_ok else None,
            },
            "storage_errors": [e for e in (obs_error, state_error, inc_error) if e],
        }
    ), 200


@app.route("/api/sutton-run", methods=["GET"])
def sutton_run_health_check():
    """Paired GET health check, same convention as nfl/ and pipeline/.

    `?probe=state` performs the signed sutton-state-read round trip using the
    deployed environment's own SUTTON_WRITE_SECRET and reports only shape and
    counts. It exists so the signed 200 can be confirmed without any human or
    agent ever holding the secret. It requires the incoming secret header, and
    it reads only -- there is no write probe.
    """
    body = {
        "status": "ok",
        "usage": (
            'POST /api/sutton-run with header X-Sutton-Secret and body '
            '{"collected_at": ISO, "mode": "collect"|"daily_radar", "scenarios": [...]}. '
            "Returns {status, shadow, deliver_escalation, deliver_radar, subject, body, "
            "storage_ok, llm_ok}."
        ),
        "watched_scenarios": list(WATCHED_SCENARIOS),
        "shadow": _shadow_enabled(),
        "env_configured": {
            "ANTHROPIC_API_KEY": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "SUTTON_MODEL": bool(os.environ.get("SUTTON_MODEL")),
            "SUTTON_INCOMING_SECRET": bool(os.environ.get("SUTTON_INCOMING_SECRET")),
            "SUTTON_WRITE_SECRET": bool(os.environ.get("SUTTON_WRITE_SECRET")),
            "SUTTON_SHADOW": bool(os.environ.get("SUTTON_SHADOW")),
        },
        "deployed_via": "github-auto-deploy",
    }

    if request.args.get("probe") == "state":
        auth_failure = check_incoming_secret()
        if auth_failure is not None:
            return auth_failure
        started = time.time()
        ok, state, error = store.read_state()
        body["state_probe"] = {
            "ok": ok,
            "elapsed_ms": int((time.time() - started) * 1000),
            "error": error,
            "keys": sorted(state.keys()) if ok else None,
            "last_collection_at": state.get("last_collection_at") if ok else None,
            "escalations_last_24h": len(state["escalations_last_24h"]) if ok else None,
            "observations": len(state["observations"]) if ok else None,
        }

    return jsonify(body)


@app.route("/api", methods=["GET"])
@app.route("/", methods=["GET"])
def root_health_check():
    return jsonify(
        {
            "status": "ok",
            "service": "sutton",
            "routes": ["POST /api/sutton-run", "GET /api/sutton-run"],
        }
    )
