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

DEFAULT_LOVABLE_URL = "https://project--d20928a7-86ec-44bf-bb99-cb5c7e320bd0.lovable.app/api/public/pipeline-write"


def _parse_events(data):
    """Shared input handling for both endpoints — same three accepted
    shapes as the original /api/flatten."""
    if isinstance(data, dict) and "events" in data:
        return flatten_hr_props_batch(data["events"])
    if isinstance(data, list):
        return flatten_hr_props_batch(data)
    if isinstance(data, dict) and "bookmakers" in data:
        return flatten_hr_props(data)
    return None


EXPECTED_INPUT_ERROR = (
    "Expected a single event object (with a 'bookmakers' key), "
    "a list of event objects, or {\"events\": [...]}."
)


@app.route("/api/flatten", methods=["POST"])
@app.route("/api", methods=["POST"])
def flatten_endpoint():
    data = request.get_json(force=True, silent=True)
    result = _parse_events(data)

    if result is None:
        return jsonify({"error": EXPECTED_INPUT_ERROR}), 400

    return jsonify(result)


@app.route("/api/flatten-and-forward", methods=["POST"])
def flatten_and_forward_endpoint():
    data = request.get_json(force=True, silent=True)
    rows = _parse_events(data)

    if rows is None:
        return jsonify({"error": EXPECTED_INPUT_ERROR}), 400

    secret = os.environ.get("LOVABLE_WEBHOOK_SECRET")
    if not secret:
        # Never happens once the Vercel env var is set; fails loudly rather
        # than silently sending an unsigned request if it's ever missing.
        return jsonify({"success": False, "error": "LOVABLE_WEBHOOK_SECRET is not configured"}), 500

    url = os.environ.get("LOVABLE_WEBHOOK_URL", DEFAULT_LOVABLE_URL)
    result = forward_to_lovable(rows, secret, url)

    return jsonify({
        "success": result["success"],
        "rows_sent": len(rows),
        "lovable_status_code": result["status_code"],
        "error": result["error"],
    }), (200 if result["success"] else 502)


@app.route("/api/flatten", methods=["GET"])
@app.route("/api", methods=["GET"])
def health_check():
    return jsonify({
        "status": "ok",
        "usage": "POST an Odds API event (or list of events) to this URL",
        "deployed_via": "github-auto-deploy",
    })
