"""
HTTP entry point for Vercel. Wraps the tested flatten_hr_props logic in a
tiny Flask app — Vercel's Python runtime auto-detects a Flask `app` object
in api/index.py and serves it directly, no extra config needed.

Accepts a POST with a JSON body that's either:
  - a single event object (has a "bookmakers" key), or
  - a list of event objects, or
  - {"events": [...]}

Returns the flattened, filtered list of HR prop rows as JSON.
"""
import json
import os
import sys
from pathlib import Path

# Vercel's Python runtime doesn't put this file's own directory on the
# import path, so a plain `from flatten_hr_props import ...` fails at
# runtime with ModuleNotFoundError even though it works locally. Fix: add
# this file's directory explicitly before importing.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from flask import Flask, jsonify, request

from flatten_hr_props import flatten_hr_props, flatten_hr_props_batch
from lovable_forward import forward_to_lovable

app = Flask(__name__)

# Fallback only — the real value should come from the LOVABLE_WEBHOOK_URL
# Vercel env var (see README) so a future URL change is a config update,
# not a code change + redeploy. Kept in sync as a defense-in-depth default
# in case that env var is ever accidentally unset.
DEFAULT_LOVABLE_URL = "https://tastypickems.lovable.app/api/public/pipeline-write"


def _parse_events(data, diagnostics=None):
    """
    Shared input handling for both endpoints — same three accepted shapes
    as the original /api/flatten. If the top-level body itself arrived as
    a JSON-encoded string (some callers do this when a request body is
    built from a text template rather than a structured mapper), recover
    the real value before checking its shape, instead of rejecting it.
    """
    if isinstance(data, str):
        try:
            data = json.loads(data)
            if diagnostics is not None:
                diagnostics["top_level_recovered_from_string"] = True
        except (json.JSONDecodeError, TypeError):
            if diagnostics is not None:
                diagnostics["top_level_unparseable_string"] = True
            return None

    if isinstance(data, dict) and "events" in data:
        return flatten_hr_props_batch(data["events"], diagnostics=diagnostics)
    if isinstance(data, list):
        return flatten_hr_props_batch(data, diagnostics=diagnostics)
    if isinstance(data, dict) and "bookmakers" in data:
        return flatten_hr_props(data, diagnostics=diagnostics)
    return None


EXPECTED_INPUT_ERROR = (
    "Expected a single event object (with a 'bookmakers' key), "
    "a list of event objects, or {\"events\": [...]}."
)


def _log_request(label: str, raw_body: bytes, data, diagnostics: dict, rows) -> None:
    """Printed output is captured in Vercel's function logs (`vercel logs`).
    Exists specifically so a real caller's actual request shape can be
    inspected after the fact, not guessed at from the outside."""
    print(
        f"[{label}] content_type={request.content_type!r} "
        f"raw_body_len={len(raw_body)} "
        f"raw_body_preview={raw_body[:300]!r} "
        f"parsed_type={type(data).__name__} "
        f"diagnostics={diagnostics} "
        f"rows_found={'N/A (unrecognized input shape)' if rows is None else len(rows)}",
        flush=True,  # unbuffered — a short-lived serverless invocation can exit
                     # before a buffered print() ever reaches the log stream
    )


@app.route("/api/flatten", methods=["POST"])
@app.route("/api", methods=["POST"])
def flatten_endpoint():
    raw_body = request.get_data()
    data = request.get_json(force=True, silent=True)
    diagnostics = {}
    result = _parse_events(data, diagnostics=diagnostics)
    _log_request("flatten", raw_body, data, diagnostics, result)

    if result is None:
        return jsonify({"error": EXPECTED_INPUT_ERROR}), 400

    return jsonify(result)


@app.route("/api/flatten-and-forward", methods=["POST"])
def flatten_and_forward_endpoint():
    raw_body = request.get_data()
    data = request.get_json(force=True, silent=True)
    diagnostics = {}
    rows = _parse_events(data, diagnostics=diagnostics)
    _log_request("flatten-and-forward", raw_body, data, diagnostics, rows)

    if rows is None:
        return jsonify({"error": EXPECTED_INPUT_ERROR, "diagnostics": diagnostics}), 400

    secret = os.environ.get("LOVABLE_WEBHOOK_SECRET")
    if not secret:
        # Never happens once the Vercel env var is set; fails loudly rather
        # than silently sending an unsigned request if it's ever missing.
        return jsonify({"success": False, "error": "LOVABLE_WEBHOOK_SECRET is not configured"}), 500

    url = os.environ.get("LOVABLE_WEBHOOK_URL", DEFAULT_LOVABLE_URL)
    result = forward_to_lovable(rows, secret, url)

    # The gap that made the last real incident harder to diagnose than it
    # needed to be: _log_request above only ever logged the incoming
    # request, never the outcome of forwarding it. A failed forward used to
    # be invisible in `vercel logs` — had to be reproduced manually via curl
    # to see Lovable's actual error text. Logged here now, flushed for the
    # same reason as _log_request (a short-lived invocation can exit before
    # buffered output reaches the log stream).
    print(
        f"[flatten-and-forward:result] target_url={url!r} "
        f"success={result['success']} "
        f"lovable_status_code={result['status_code']} "
        f"lovable_error={result['error']!r}",
        flush=True,
    )

    return jsonify({
        "success": result["success"],
        "rows_sent": len(rows),
        "lovable_status_code": result["status_code"],
        "error": result["error"],
        "diagnostics": diagnostics,
    }), (200 if result["success"] else 502)


@app.route("/api/flatten", methods=["GET"])
@app.route("/api", methods=["GET"])
def health_check():
    return jsonify({
        "status": "ok",
        "usage": "POST an Odds API event (or list of events) to this URL",
        "deployed_via": "github-auto-deploy",
    })
