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
import sys
from pathlib import Path

# Vercel's Python runtime doesn't put this file's own directory on the
# import path, so a plain `from flatten_hr_props import ...` fails at
# runtime with ModuleNotFoundError even though it works locally. Fix: add
# this file's directory explicitly before importing.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from flask import Flask, jsonify, request

from flatten_hr_props import flatten_hr_props, flatten_hr_props_batch

app = Flask(__name__)


@app.route("/api/flatten", methods=["POST"])
@app.route("/api", methods=["POST"])
def flatten_endpoint():
    data = request.get_json(force=True, silent=True)

    if isinstance(data, dict) and "events" in data:
        result = flatten_hr_props_batch(data["events"])
    elif isinstance(data, list):
        result = flatten_hr_props_batch(data)
    elif isinstance(data, dict) and "bookmakers" in data:
        result = flatten_hr_props(data)
    else:
        return jsonify({
            "error": "Expected a single event object (with a 'bookmakers' key), "
                     "a list of event objects, or {\"events\": [...]}."
        }), 400

    return jsonify(result)


@app.route("/api/flatten", methods=["GET"])
@app.route("/api", methods=["GET"])
def health_check():
    return jsonify({"status": "ok", "usage": "POST an Odds API event (or list of events) to this URL"})
