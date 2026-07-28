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
sys.path.insert(0, str(Path(__file__).resolve().parent / "live_data"))

from flask import Flask, jsonify, request
import requests

from flatten_hr_props import flatten_any, flatten_hr_props, flatten_hr_props_batch
from lovable_forward import forward_to_lovable
from build_game_candidates import build_candidates_for_game
from scored_picks import build_scored_picks_for_game

app = Flask(__name__)

# Fallback only — the real value should come from the LOVABLE_WEBHOOK_URL
# Vercel env var (see README) so a future URL change is a config update,
# not a code change + redeploy. Kept in sync as a defense-in-depth default
# in case that env var is ever accidentally unset.
DEFAULT_LOVABLE_URL = "https://tastypickems.lovable.app/api/public/pipeline-write"

# NOT a real endpoint yet — the scored_picks table/route doesn't exist on
# the Lovable side at the time this was written (see README for the
# handoff schema). LOVABLE_SCORED_PICKS_WEBHOOK_URL is the real source of
# truth once that route exists; this placeholder just documents the
# expected naming convention and will 502 harmlessly (not silently
# succeed) until it's set.
DEFAULT_SCORED_PICKS_URL = "https://tastypickems.lovable.app/api/public/scored-picks-write"


def _parse_events(data, diagnostics=None):
    """Thin wrapper — the actual shape-detection logic now lives in
    flatten_hr_props.flatten_any() so scored_picks.py's orchestrator can
    share it instead of duplicating it. Kept as a named wrapper here only
    to avoid touching the two call sites below."""
    return flatten_any(data, diagnostics=diagnostics)


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


@app.route("/api/live-data/game/<int:game_pk>", methods=["GET"])
def live_data_game_endpoint(game_pk):
    """
    One game's lineup/weather/stats context, score_candidate()-ready —
    see api/live_data/build_game_candidates.py for the full pipeline.
    Deliberately per-game, not per-day: mirrors /api/flatten-and-forward's
    one-call-per-event shape so Make.com's existing Iterator-over-games
    pattern (already built for the odds pipeline) can call this the same
    way — GET /api/live-data/game/<game_pk>, no request body needed.

    NOT wired into any Make.com scenario yet — deployed and independently
    callable, but that connection is still a separate step.
    """
    try:
        result = build_candidates_for_game(game_pk)
    except ValueError as e:
        # Confirmed real behavior: feed/live returns HTTP 200 with a
        # near-empty placeholder body for an unknown game_pk rather than a
        # 404 — game_data.py turns that into a ValueError instead of
        # letting it surface as a confusing downstream KeyError.
        print(f"[live-data:game] game_pk={game_pk} not_found={e}", flush=True)
        return jsonify({"error": str(e)}), 404
    except requests.exceptions.RequestException as e:
        print(f"[live-data:game] game_pk={game_pk} network_error={e}", flush=True)
        return jsonify({"error": "Network error reaching the MLB Stats API.", "detail": str(e)}), 502

    print(
        f"[live-data:game] game_pk={game_pk} "
        f"matchup={result['game']['away_team']}@{result['game']['home_team']} "
        f"lineup_status={result['game']['lineup_status']} "
        f"candidates={len(result['game']['candidates'])}",
        flush=True,
    )
    return jsonify(result)


@app.route("/api/live-data", methods=["GET"])
def live_data_health_check():
    return jsonify({
        "status": "ok",
        "usage": "GET /api/live-data/game/<game_pk> for that game's lineup/weather/stats, score_candidate()-ready.",
        "deployed_via": "github-auto-deploy",
    })


@app.route("/api/score-game-props/game/<int:game_pk>", methods=["POST"])
def score_game_props_endpoint(game_pk):
    """
    The full orchestration: raw odds event data (POST body — the EXACT
    same shape /api/flatten-and-forward already accepts: a single event
    object, a list of events, or {"events": [...]}) + a game_pk (URL path,
    same as /api/live-data/game/<game_pk>) in, real scored picks out,
    forwarded to Lovable's scored_picks webhook. See scored_picks.py for
    the full pipeline (flatten -> live-data fetch -> name-match -> score).

    NOT wired into any Make.com scenario yet, and LOVABLE_SCORED_PICKS_WEBHOOK_URL
    isn't a real endpoint yet either (see README for the scored_picks
    table schema handed off for the Lovable side to build) — this is
    deployed and independently callable/testable, but forwarding will
    502 with a clear error until that URL exists and the env var is set.
    """
    raw_body = request.get_data()
    data = request.get_json(force=True, silent=True)

    try:
        result = build_scored_picks_for_game(game_pk, data)
    except ValueError as e:
        print(f"[score-game-props] game_pk={game_pk} not_found={e}", flush=True)
        return jsonify({"error": str(e)}), 404
    except requests.exceptions.RequestException as e:
        print(f"[score-game-props] game_pk={game_pk} network_error={e}", flush=True)
        return jsonify({"error": "Network error reaching an upstream API.", "detail": str(e)}), 502

    if result["matchup"] is None:
        # build_scored_picks_for_game sets matchup=None only when the odds
        # payload itself wasn't a recognized shape — matches
        # /api/flatten-and-forward's 400 for the same class of bad input,
        # rather than a misleading 200.
        print(f"[score-game-props] game_pk={game_pk} bad_odds_shape={result['errors']}", flush=True)
        return jsonify({"error": result["errors"][0]["error"]}), 400

    scored_picks = result["scored_picks"]
    forward_result = {"success": None, "status_code": None, "error": None}
    if scored_picks:
        secret = os.environ.get("LOVABLE_WEBHOOK_SECRET")
        if not secret:
            forward_result = {"success": False, "status_code": None, "error": "LOVABLE_WEBHOOK_SECRET is not configured"}
        else:
            url = os.environ.get("LOVABLE_SCORED_PICKS_WEBHOOK_URL", DEFAULT_SCORED_PICKS_URL)
            forward_result = forward_to_lovable(scored_picks, secret, url)

    print(
        f"[score-game-props] game_pk={game_pk} "
        f"content_type={request.content_type!r} raw_body_len={len(raw_body)} "
        f"matchup={result['matchup']} "
        f"odds_entries={result['match_summary']['odds_entries_total']} "
        f"matched={result['match_summary']['matched']} "
        f"unmatched={result['match_summary']['unmatched_odds_count']} "
        f"scoring_errors={len(result['errors'])} "
        f"forward_success={forward_result['success']} forward_status={forward_result['status_code']}",
        flush=True,
    )

    return jsonify({
        "game_pk": game_pk,
        "matchup": result["matchup"],
        "scored_count": len(scored_picks),
        "match_summary": result["match_summary"],
        "errors": result["errors"],
        "forwarded": forward_result["success"],
        "lovable_status_code": forward_result["status_code"],
        "forward_error": forward_result["error"],
        "lovable_response": forward_result.get("response_body"),
    }), (502 if forward_result["success"] is False else 200)


@app.route("/api/score-game-props", methods=["GET"])
def score_game_props_health_check():
    return jsonify({
        "status": "ok",
        "usage": "POST /api/score-game-props/game/<game_pk> with a raw Odds API event "
                 "(same shape as /api/flatten-and-forward) to get real scored picks for that game.",
        "deployed_via": "github-auto-deploy",
    })
