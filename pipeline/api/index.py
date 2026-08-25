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
import hmac
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Vercel's Python runtime doesn't put this file's own directory on the
# import path, so a plain `from flatten_hr_props import ...` fails at
# runtime with ModuleNotFoundError even though it works locally. Fix: add
# this file's directory explicitly before importing.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "live_data"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "content_writer"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "content_writer" / "voice"))

from flask import Flask, jsonify, request
import requests

from flatten_hr_props import flatten_any, flatten_hr_props, flatten_hr_props_batch
from lovable_forward import forward_to_lovable, resolve_url_env
from build_game_candidates import build_candidates_for_game
from game_lookup import resolve_game_pk
from scored_picks import build_scored_picks_for_game, fetch_recent_statcast_form
from curate_shelves import curate_shelves_for_date
from shelf_curation import DEFAULT_SHELF_SIZE
from grade_official_picks_live import grade_official_picks_for_pending
from grade_bookmarks_live import grade_bookmarks_for_pending
from content_draft_generation_live import generate_content_drafts_for_pending
from generate_tasty_six_content import draft_for_write, generate_tasty_six_draft
from generate_shelf_card_content import (
    draft_for_write as shelf_card_draft_for_write,
    generate_shelf_card_draft,
)

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

# Same status as DEFAULT_SCORED_PICKS_URL above when it was first added:
# these two routes are drafted and staged for review, not yet applied to
# the live Lovable app. Placeholders document the expected naming
# convention; the real *_WEBHOOK_URL env vars below are the source of
# truth once the routes exist.
DEFAULT_SCORED_PICKS_READ_URL = "https://tastypickems.lovable.app/api/public/scored-picks-read"
DEFAULT_SHELF_ASSIGNMENTS_WRITE_URL = "https://tastypickems.lovable.app/api/public/shelf-assignments-write"
DEFAULT_RECENT_STATCAST_FORM_READ_URL = "https://tastypickems.lovable.app/api/public/recent-statcast-form-read"

# Same status as the two URLs above — drafted, staged for review, not yet
# applied to the live Lovable app at the time this was written.
DEFAULT_PICKS_NEEDING_GRADING_READ_URL = "https://tastypickems.lovable.app/api/public/picks-needing-grading-read"
DEFAULT_OFFICIAL_PICK_RESULTS_WRITE_URL = "https://tastypickems.lovable.app/api/public/official-pick-results-write"

# Same status as the URLs above — drafted, staged for review, not yet
# applied to the live Lovable app at the time this was written.
DEFAULT_CONTENT_DRAFTS_WRITE_URL = "https://tastypickems.lovable.app/api/public/content-drafts-write"

# Same status as the URLs above — drafted, staged for review, not yet
# applied to the live Lovable app at the time this was written.
DEFAULT_BOOKMARKS_NEEDING_GRADING_READ_URL = "https://tastypickems.lovable.app/api/public/bookmarks-needing-grading-read"
DEFAULT_BOOKMARK_RESULTS_WRITE_URL = "https://tastypickems.lovable.app/api/public/bookmark-results-write"

# Same status as the URLs above — drafted, staged for review, not yet
# applied to the live Lovable app at the time this was written.
DEFAULT_CONTENT_DRAFTS_NEEDING_GENERATION_READ_URL = "https://tastypickems.lovable.app/api/public/content-drafts-needing-generation-read"


def check_pipeline_secret():
    """
    Shared-secret header check for the four endpoints only ever called by
    Make.com directly (curate-shelves, generate-content-drafts,
    grade-official-picks, grade-bookmarks) — deliberately NOT full HMAC
    signing like content-drafts-write/official-pick-results-write/etc use.
    Those sign a real, variable, attacker-shaped BODY going out TO Lovable;
    these four instead receive a small, fixed, Make.com-only trigger with
    no meaningful body to forge — a constant-time shared-secret comparison
    is the right amount of protection for "only Make.com should be able to
    kick this off", not a mismatched upgrade to signature verification.

    Returns a (401, jsonify(...)) Flask response tuple if the request
    should be rejected, or None if it's authorized to proceed. Constant-
    time comparison (hmac.compare_digest) avoids a timing side-channel on
    the secret, same reasoning as every other secret comparison in this
    codebase (see lovable_forward.py / the TS routes' verifySignature).
    """
    expected = os.environ.get("PIPELINE_INCOMING_SECRET")
    if not expected:
        return jsonify({"error": "PIPELINE_INCOMING_SECRET is not configured"}), 500
    provided = request.headers.get("X-Pipeline-Secret")
    if not provided or not hmac.compare_digest(provided, expected):
        return jsonify({"error": "Missing or invalid X-Pipeline-Secret header"}), 401
    return None


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

    url = resolve_url_env("LOVABLE_WEBHOOK_URL", DEFAULT_LOVABLE_URL)
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


def _score_and_forward(game_pk, data, raw_body, log_label):
    """
    Shared by both score-game-props routes below: run the orchestrator,
    forward any resulting scored picks to Lovable, log, and build the
    JSON response + status code. The only difference between the two
    routes is how they arrive at `game_pk` — one gets it directly, the
    other resolves it first — everything downstream of that is identical,
    so it lives in one place rather than two.
    """
    # Fetched here, not inside build_scored_picks_for_game(), which stays
    # PURE/network-free to Lovable by design (see its own docstring) —
    # every OTHER signed Lovable call in this pipeline's orchestrator
    # functions is likewise made by the Flask route, not the pure function
    # underneath it.
    #
    # DELIBERATELY DEGRADES GRACEFULLY on failure here, unlike
    # batter_form/pitcher_form (an MLB Stats API failure inside
    # build_scored_picks_for_game DOES fail this whole request, via the
    # except block below) — those are core, load-bearing data every real
    # scored pick needs; recent_statcast_form is a daily BATCH job that
    # can legitimately not have run yet on any given real day without that
    # being a genuine emergency. A read failure here logs a warning and
    # proceeds with an empty lookup (every candidate gets the real, honest
    # null-shaped Statcast form — see scored_picks.py's
    # _EMPTY_STATCAST_FORM), not a 502 for the whole game.
    recent_statcast_form = {}
    secret_for_read = os.environ.get("LOVABLE_WEBHOOK_SECRET")
    if secret_for_read:
        statcast_read_url = resolve_url_env("LOVABLE_RECENT_STATCAST_FORM_READ_URL", DEFAULT_RECENT_STATCAST_FORM_READ_URL)
        try:
            recent_statcast_form = fetch_recent_statcast_form(secret_for_read, statcast_read_url)
        except requests.exceptions.RequestException as e:
            print(f"[{log_label}] game_pk={game_pk} recent_statcast_form_fetch_failed={e} — proceeding without it", flush=True)

    try:
        result = build_scored_picks_for_game(game_pk, data, recent_statcast_form=recent_statcast_form)
    except requests.exceptions.RequestException as e:
        print(f"[{log_label}] game_pk={game_pk} network_error={e}", flush=True)
        return jsonify({"error": "Network error reaching an upstream API.", "detail": str(e)}), 502

    if result["matchup"] is None:
        # build_scored_picks_for_game sets matchup=None only when the odds
        # payload itself wasn't a recognized shape — matches
        # /api/flatten-and-forward's 400 for the same class of bad input,
        # rather than a misleading 200.
        print(f"[{log_label}] game_pk={game_pk} bad_odds_shape={result['errors']}", flush=True)
        return jsonify({"error": result["errors"][0]["error"]}), 400

    scored_picks = result["scored_picks"]
    forward_result = {"success": None, "status_code": None, "error": None}
    if scored_picks:
        secret = os.environ.get("LOVABLE_WEBHOOK_SECRET")
        if not secret:
            forward_result = {"success": False, "status_code": None, "error": "LOVABLE_WEBHOOK_SECRET is not configured"}
        else:
            url = resolve_url_env("LOVABLE_SCORED_PICKS_WEBHOOK_URL", DEFAULT_SCORED_PICKS_URL)
            forward_result = forward_to_lovable(scored_picks, secret, url)

    print(
        f"[{log_label}] game_pk={game_pk} "
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


@app.route("/api/score-game-props/game/<int:game_pk>", methods=["POST"])
def score_game_props_endpoint(game_pk):
    """
    The full orchestration: raw odds event data (POST body — the EXACT
    same shape /api/flatten-and-forward already accepts: a single event
    object, a list of events, or {"events": [...]}) + a game_pk (URL path,
    same as /api/live-data/game/<game_pk>) in, real scored picks out,
    forwarded to Lovable's scored_picks webhook. See scored_picks.py for
    the full pipeline (flatten -> live-data fetch -> name-match -> score).

    Use this route when the caller already knows MLB's game_pk. Make.com's
    real odds-fetch loop does NOT — see /api/score-game-props/by-event
    below for the route built for that actual situation.
    """
    raw_body = request.get_data()
    data = request.get_json(force=True, silent=True)

    try:
        return _score_and_forward(game_pk, data, raw_body, "score-game-props")
    except ValueError as e:
        print(f"[score-game-props] game_pk={game_pk} not_found={e}", flush=True)
        return jsonify({"error": str(e)}), 404


@app.route("/api/score-game-props/by-event", methods=["POST"])
def score_game_props_by_event_endpoint():
    """
    The route built for Make.com's ACTUAL situation: its odds-fetch loop
    has The Odds API's own event ID, home_team, away_team, and
    commence_time per game — never MLB's numeric game_pk. Rather than
    requiring a separate lookup call before this one, this route resolves
    game_pk internally from fields that are ALREADY top-level on the raw
    odds event Make.com already has to send anyway (see game_lookup.py for
    the full reasoning, including two real scheduling gotchas it had to
    handle: a real cross-UTC-midnight game and a real doubleheader).

    POST body: a SINGLE raw Odds API event object (must have home_team,
    away_team, commence_time, and bookmakers) — not a list or
    {"events": [...]}, since resolution needs exactly one target matchup.
    Everything after resolution is identical to /game/<game_pk> above.
    """
    raw_body = request.get_data()
    data = request.get_json(force=True, silent=True)

    if not isinstance(data, dict) or not all(k in data for k in ("home_team", "away_team", "commence_time")):
        return jsonify({
            "error": "Expected a single Odds API event object with home_team, away_team, commence_time, "
                     "and bookmakers fields — not a list or {'events': [...]}.",
        }), 400

    try:
        resolution = resolve_game_pk(data["home_team"], data["away_team"], data["commence_time"])
    except ValueError as e:
        print(f"[score-game-props:by-event] resolution_failed={e}", flush=True)
        return jsonify({"error": str(e)}), 404
    except requests.exceptions.RequestException as e:
        print(f"[score-game-props:by-event] network_error={e}", flush=True)
        return jsonify({"error": "Network error reaching the MLB Stats API.", "detail": str(e)}), 502

    game_pk = resolution["game_pk"]
    print(
        f"[score-game-props:by-event] resolved {data['away_team']!r} @ {data['home_team']!r} "
        f"near {data['commence_time']} -> game_pk={game_pk} "
        f"disambiguated_by_time={resolution['disambiguated_by_time']} candidates={resolution['candidates']}",
        flush=True,
    )

    try:
        response, status = _score_and_forward(game_pk, data, raw_body, "score-game-props:by-event")
    except ValueError as e:
        # A resolved game_pk turning out invalid against feed/live would be
        # a genuine internal inconsistency (resolution and scoring reading
        # different MLB data), not a caller error — still surfaced as 404
        # rather than a raw 500, but logged distinctly for that reason.
        print(f"[score-game-props:by-event] game_pk={game_pk} unexpectedly not_found={e}", flush=True)
        return jsonify({"error": str(e)}), 404

    response_data = response.get_json()
    response_data["resolved_game_pk"] = game_pk
    response_data["resolved_via"] = "team_name_and_commence_time"
    response_data["disambiguated_by_time"] = resolution["disambiguated_by_time"]
    return jsonify(response_data), status


@app.route("/api/score-game-props", methods=["GET"])
def score_game_props_health_check():
    return jsonify({
        "status": "ok",
        "usage": "POST /api/score-game-props/game/<game_pk>, or POST /api/score-game-props/by-event "
                 "with a raw Odds API event (no game_pk needed — resolved internally), to get real "
                 "scored picks for that game.",
        "deployed_via": "github-auto-deploy",
    })


@app.route("/api/curate-shelves", methods=["POST"])
def curate_shelves_endpoint():
    """
    Whole-slate shelf curation: reads a full day's scored_picks from
    Lovable's signed read endpoint, sanity-checks the slate isn't
    suspiciously incomplete, runs shelf_curation.py's tested logic, and
    forwards the resulting shelf_assignments (including Tasty Six flags)
    to Lovable's write endpoint. See curate_shelves.py for the full
    reasoning behind this shape.

    NOT wired into any Make.com scenario yet — deployed and independently
    callable once the two Lovable routes it depends on are live, but that
    connection is a deliberately separate step.

    POST body (all optional): {"date": "YYYY-MM-DD", "shelf_size": 8,
    "include_rows": false}. Defaults to today (UTC) and shelf_curation.py's
    DEFAULT_SHELF_SIZE. include_rows, off by default, adds the real, FULL
    curated candidate data (shelf_candidates_detailed — pillar_detail, all
    four pillar scores, recent-form extras) to the response, not just the
    thin shelf_assignments shape that gets forwarded to Lovable. Useful for
    pulling real candidate data for local testing (e.g. the content
    writer, which needs pillar_detail) without direct DB read access;
    never needed by Make.com's real read->curate->write flow, which
    already gets the thin rows via the write forward, not this response.
    """
    auth_error = check_pipeline_secret()
    if auth_error:
        return auth_error

    data = request.get_json(force=True, silent=True) or {}
    date = data.get("date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    shelf_size = data.get("shelf_size", DEFAULT_SHELF_SIZE)
    include_rows = bool(data.get("include_rows", False))

    secret = os.environ.get("LOVABLE_WEBHOOK_SECRET")
    if not secret:
        return jsonify({"error": "LOVABLE_WEBHOOK_SECRET is not configured"}), 500

    read_url = resolve_url_env("LOVABLE_SCORED_PICKS_READ_URL", DEFAULT_SCORED_PICKS_READ_URL)
    write_url = resolve_url_env("LOVABLE_SHELF_ASSIGNMENTS_WRITE_URL", DEFAULT_SHELF_ASSIGNMENTS_WRITE_URL)

    try:
        result = curate_shelves_for_date(date, secret, read_url, shelf_size=shelf_size)
    except requests.exceptions.RequestException as e:
        print(f"[curate-shelves] date={date} network_error_reading={e}", flush=True)
        return jsonify({"error": "Network error reaching the scored-picks-read endpoint.", "detail": str(e)}), 502

    if result["error"] is not None:
        print(f"[curate-shelves] date={date} aborted error={result['error']}", flush=True)
        return jsonify({
            "date": date,
            "curated": False,
            "error": result["error"],
            "sanity_check": result["sanity_check"],
        }), 422

    forward_result = {"success": None, "status_code": None, "error": None}
    if result["shelf_assignments"]:
        forward_result = forward_to_lovable(result["shelf_assignments"], secret, write_url)

    print(
        f"[curate-shelves] date={date} "
        f"sanity_check={result['sanity_check']} "
        f"shelf_sizes={result['shelf_sizes']} "
        f"tasty_six_repeats={result['tasty_six_repeats']} "
        f"rows={len(result['shelf_assignments'])} "
        f"forward_success={forward_result['success']} forward_status={forward_result['status_code']}",
        flush=True,
    )

    response = {
        "date": date,
        "curated": True,
        "sanity_check": result["sanity_check"],
        "shelf_sizes": result["shelf_sizes"],
        "tasty_six_repeats": result["tasty_six_repeats"],
        "rows_curated": len(result["shelf_assignments"]),
        "forwarded": forward_result["success"],
        "lovable_status_code": forward_result["status_code"],
        "forward_error": forward_result["error"],
    }
    if include_rows:
        response["shelf_candidates_detailed"] = result["shelf_candidates_detailed"]

    return jsonify(response), (502 if forward_result["success"] is False else 200)


@app.route("/api/curate-shelves", methods=["GET"])
def curate_shelves_health_check():
    return jsonify({
        "status": "ok",
        "usage": "POST /api/curate-shelves with an optional {\"date\": \"YYYY-MM-DD\", \"shelf_size\": 8, "
                 "\"include_rows\": false} body to curate that day's six shelves + Tasty Six from scored_picks "
                 "and forward them to Lovable. include_rows adds the real full candidate data "
                 "(shelf_candidates_detailed) to the response.",
        "deployed_via": "github-auto-deploy",
    })


@app.route("/api/grade-official-picks", methods=["POST"])
def grade_official_picks_endpoint():
    """
    Grades every official pick (shelf_assignments row) that doesn't yet
    have a matching official_pick_results row: reads the ungraded set from
    Lovable's signed picks-needing-grading-read endpoint, grades it via
    official_pick_grading.py (real MLB lookups, deduplicated per unique
    game), forwards only the terminal (won/lost/void) results to
    official-pick-results-write. See grade_official_picks_live.py for the
    full reasoning, including why this is an anti-join rather than a
    game-status pre-filter.

    NOT wired into any Make.com scenario yet — deployed and independently
    callable once the two Lovable routes it depends on are live, but that
    connection is a deliberately separate step.

    POST body (all optional): {"lookback_days": 3}. A batch of 0 graded
    picks is a normal result, not an error — see the module docstring.
    """
    auth_error = check_pipeline_secret()
    if auth_error:
        return auth_error

    data = request.get_json(force=True, silent=True) or {}
    lookback_days = data.get("lookback_days")

    secret = os.environ.get("LOVABLE_WEBHOOK_SECRET")
    if not secret:
        return jsonify({"error": "LOVABLE_WEBHOOK_SECRET is not configured"}), 500

    read_url = resolve_url_env("LOVABLE_PICKS_NEEDING_GRADING_READ_URL", DEFAULT_PICKS_NEEDING_GRADING_READ_URL)
    write_url = resolve_url_env("LOVABLE_OFFICIAL_PICK_RESULTS_WRITE_URL", DEFAULT_OFFICIAL_PICK_RESULTS_WRITE_URL)

    try:
        result = grade_official_picks_for_pending(secret, read_url, write_url, lookback_days=lookback_days)
    except requests.exceptions.RequestException as e:
        print(f"[grade-official-picks] network_error_reading={e}", flush=True)
        return jsonify({"error": "Network error reaching the picks-needing-grading-read endpoint.", "detail": str(e)}), 502

    if result["error"] is not None:
        print(f"[grade-official-picks] aborted error={result['error']}", flush=True)
        return jsonify({"graded": False, "error": result["error"]}), 502

    forward = result["forwarded"]
    forward_success = forward["success"] if forward is not None else True  # nothing to forward is not a failure

    print(
        f"[grade-official-picks] picks_needing_grading={result['picks_needing_grading_count']} "
        f"graded={result['graded_count']} still_pending={result['still_pending_count']} "
        f"grading_errors={len(result['grading_errors'])} "
        f"forward_success={forward['success'] if forward else None} "
        f"forward_status={forward['status_code'] if forward else None}",
        flush=True,
    )

    return jsonify({
        "graded": True,
        "total_shelf_assignments_in_window": result["total_shelf_assignments_in_window"],
        "already_graded_count": result["already_graded_count"],
        "picks_needing_grading_count": result["picks_needing_grading_count"],
        "graded_count": result["graded_count"],
        "still_pending_count": result["still_pending_count"],
        "grading_errors": result["grading_errors"],
        "forwarded": forward["success"] if forward else None,
        "lovable_status_code": forward["status_code"] if forward else None,
        "forward_error": forward["error"] if forward else None,
        "lovable_response": forward.get("response_body") if forward else None,
    }), (502 if forward_success is False else 200)


@app.route("/api/grade-official-picks", methods=["GET"])
def grade_official_picks_health_check():
    return jsonify({
        "status": "ok",
        "usage": "POST /api/grade-official-picks with an optional {\"lookback_days\": 3} body to grade every "
                 "ungraded official pick (won/lost/void only) and forward results to Lovable.",
        "deployed_via": "github-auto-deploy",
    })


@app.route("/api/grade-bookmarks", methods=["POST"])
def grade_bookmarks_endpoint():
    """
    Grades every user-saved pick (bookmarks row) that has real mlbam_id/
    game_pk, hasn't been graded yet, and whose game has already started:
    reads the ungraded set from Lovable's signed bookmarks-needing-grading-
    read endpoint, grades it via bookmark_grading.py (real MLB lookups,
    deduplicated per unique game, same grade_pick() as official picks),
    forwards only the terminal (won/lost/void) results to
    bookmark-results-write, which UPDATEs each bookmark row in place. See
    grade_bookmarks_live.py for the full reasoning, including how this
    differs from official-picks grading (per-user fan-out, UPDATE instead
    of insert/upsert, win/loss/void vocabulary mapped server-side).

    Deliberately separate from /api/grade-official-picks — never shares a
    table, a query, or a request body shape with it.

    POST body: none required. A batch of 0 graded picks is a normal
    result, not an error — same reasoning as official-picks grading.
    """
    auth_error = check_pipeline_secret()
    if auth_error:
        return auth_error

    secret = os.environ.get("LOVABLE_WEBHOOK_SECRET")
    if not secret:
        return jsonify({"error": "LOVABLE_WEBHOOK_SECRET is not configured"}), 500

    read_url = resolve_url_env("LOVABLE_BOOKMARKS_NEEDING_GRADING_READ_URL", DEFAULT_BOOKMARKS_NEEDING_GRADING_READ_URL)
    write_url = resolve_url_env("LOVABLE_BOOKMARK_RESULTS_WRITE_URL", DEFAULT_BOOKMARK_RESULTS_WRITE_URL)

    try:
        result = grade_bookmarks_for_pending(secret, read_url, write_url)
    except requests.exceptions.RequestException as e:
        print(f"[grade-bookmarks] network_error_reading={e}", flush=True)
        return jsonify({"error": "Network error reaching the bookmarks-needing-grading-read endpoint.", "detail": str(e)}), 502

    if result["error"] is not None:
        print(f"[grade-bookmarks] aborted error={result['error']}", flush=True)
        return jsonify({"graded": False, "error": result["error"]}), 502

    forward = result["forwarded"]
    forward_success = forward["success"] if forward is not None else True  # nothing to forward is not a failure

    print(
        f"[grade-bookmarks] picks_needing_grading={result['picks_needing_grading_count']} "
        f"graded={result['graded_count']} still_pending={result['still_pending_count']} "
        f"grading_errors={len(result['grading_errors'])} "
        f"forward_success={forward['success'] if forward else None} "
        f"forward_status={forward['status_code'] if forward else None}",
        flush=True,
    )

    return jsonify({
        "graded": True,
        "picks_needing_grading_count": result["picks_needing_grading_count"],
        "graded_count": result["graded_count"],
        "still_pending_count": result["still_pending_count"],
        "grading_errors": result["grading_errors"],
        "forwarded": forward["success"] if forward else None,
        "lovable_status_code": forward["status_code"] if forward else None,
        "forward_error": forward["error"] if forward else None,
        "lovable_response": forward.get("response_body") if forward else None,
    }), (502 if forward_success is False else 200)


@app.route("/api/grade-bookmarks", methods=["GET"])
def grade_bookmarks_health_check():
    return jsonify({
        "status": "ok",
        "usage": "POST /api/grade-bookmarks (no body needed) to grade every ungraded user-saved pick "
                 "(won/lost/void only) whose game has started, and forward results to Lovable.",
        "deployed_via": "github-auto-deploy",
    })


@app.route("/api/generate-content-drafts", methods=["POST"])
def generate_content_drafts_endpoint():
    """
    Closes the automation gap between curate-shelves and the content
    writers: reads today's real shelf_assignments rows that don't yet have
    a matching content_drafts row (via the signed
    content-drafts-needing-generation-read anti-join), generates a real
    draft for each via generate_tasty_six_draft()/generate_shelf_card_draft()
    (unmodified — the exact same functions the manual /content-writer/*/
    generate routes already use), and forwards each real draft to
    content_drafts, one at a time, same as those routes do internally. See
    content_draft_generation_live.py for the full reasoning.

    IDEMPOTENT BY CONSTRUCTION: running this twice in a row is safe —
    the second run's read step returns nothing for candidates the first
    run already wrote a draft for. No separate "already attempted"
    tracking needed, same anti-join philosophy as official-picks grading.

    Deliberately a single endpoint doing the internal per-candidate
    fan-out (one real Claude call + one real forward each), so Make.com
    calls this once per scheduled run instead of looping through every
    curated candidate itself — same shape as /api/grade-official-picks
    and /api/grade-bookmarks.

    POST body (optional): {"slate": "YYYY-MM-DD"}. Defaults to today
    (America/New_York) on the Lovable side. A batch of 0 candidates
    needing content is a normal result, not an error — the same slate can
    legitimately be fully covered already.
    """
    auth_error = check_pipeline_secret()
    if auth_error:
        return auth_error

    data = request.get_json(force=True, silent=True) or {}
    slate = data.get("slate")

    secret = os.environ.get("LOVABLE_WEBHOOK_SECRET")
    if not secret:
        return jsonify({"error": "LOVABLE_WEBHOOK_SECRET is not configured"}), 500

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return jsonify({"error": "ANTHROPIC_API_KEY is not configured"}), 500

    read_url = resolve_url_env("LOVABLE_CONTENT_DRAFTS_NEEDING_GENERATION_READ_URL", DEFAULT_CONTENT_DRAFTS_NEEDING_GENERATION_READ_URL)
    write_url = resolve_url_env("LOVABLE_CONTENT_DRAFTS_WRITE_URL", DEFAULT_CONTENT_DRAFTS_WRITE_URL)

    try:
        result = generate_content_drafts_for_pending(secret, read_url, write_url, api_key, slate=slate)
    except requests.exceptions.RequestException as e:
        print(f"[generate-content-drafts] network_error_reading={e}", flush=True)
        return jsonify({"error": "Network error reaching the content-drafts-needing-generation-read endpoint.", "detail": str(e)}), 502

    if result["error"] is not None:
        print(f"[generate-content-drafts] aborted error={result['error']}", flush=True)
        return jsonify({"generated": False, "error": result["error"]}), 502

    print(
        f"[generate-content-drafts] candidates_found={result['candidates_found']} "
        f"(tasty_six={result['tasty_six_found']} shelf_card={result['shelf_card_found']}) "
        f"generated={result['generated_count']} "
        f"validation_passed={result['validation_passed_count']} validation_failed={result['validation_failed_count']} "
        f"generation_errors={len(result['generation_errors'])} "
        f"forwarded={result['forwarded_count']} forward_errors={len(result['forward_errors'])}",
        flush=True,
    )

    return jsonify({
        "generated": True,
        "candidates_found": result["candidates_found"],
        "tasty_six_found": result["tasty_six_found"],
        "shelf_card_found": result["shelf_card_found"],
        "generated_count": result["generated_count"],
        "validation_passed_count": result["validation_passed_count"],
        "validation_failed_count": result["validation_failed_count"],
        "generation_errors": result["generation_errors"],
        "forwarded_count": result["forwarded_count"],
        "forward_errors": result["forward_errors"],
        "results": result["results"],
    }), 200


@app.route("/api/generate-content-drafts", methods=["GET"])
def generate_content_drafts_health_check():
    return jsonify({
        "status": "ok",
        "usage": "POST /api/generate-content-drafts with an optional {\"slate\": \"YYYY-MM-DD\"} body to "
                 "generate real content_drafts for every curated candidate that doesn't have one yet, and "
                 "forward each to Lovable. Idempotent — safe to call repeatedly.",
        "deployed_via": "github-auto-deploy",
    })


@app.route("/api/content-writer/tasty-six/generate", methods=["POST"])
def generate_tasty_six_endpoint():
    """
    Generates ONE real Tasty Six card for ONE real candidate: builds the
    real prompt (shelf personality + confidence band + real source facts),
    calls Claude via forced tool-use, runs the full deterministic
    validation suite, and forwards the result to content_drafts.

    NEVER auto-approves or publishes anything — see generate_tasty_six_
    content.py for the full reasoning. A draft that fails validation is
    still returned and still forwarded, marked "flagged" rather than
    "pending_review" — never silently discarded.

    POST body: {"candidate": {...}} — one entry from /api/curate-shelves's
    shelf_candidates_detailed response (include_rows: true). The pipeline
    has no independent read access to Lovable's tables, same boundary as
    every other endpoint here — the caller supplies the real candidate
    directly, same shape as score-game-props taking odds data directly.

    Optional "debug_inject_violation_instruction" (string): TESTING ONLY
    — appends an extra, deliberately rule-breaking instruction to the
    real system prompt, so the validators can be proven against a genuine
    adversarial model response rather than only a hand-crafted fixture.
    Never set by real content generation.
    """
    data = request.get_json(force=True, silent=True) or {}
    candidate = data.get("candidate")
    debug_inject_violation_instruction = data.get("debug_inject_violation_instruction")
    if not isinstance(candidate, dict) or "candidate" not in candidate or "shelf" not in candidate:
        return jsonify({
            "error": "POST body must include a real candidate entry: "
                     "{\"candidate\": {...one shelf_candidates_detailed entry, with its own "
                     "\"candidate\"/\"shelf\" keys...}}",
        }), 400

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return jsonify({"error": "ANTHROPIC_API_KEY is not configured"}), 500

    try:
        draft = generate_tasty_six_draft(candidate, api_key, debug_inject_violation_instruction)
    except ValueError as e:
        print(f"[content-writer:tasty-six:generate] bad input: {e}", flush=True)
        return jsonify({"error": str(e)}), 400
    except requests.exceptions.RequestException as e:
        print(f"[content-writer:tasty-six:generate] network_error={e}", flush=True)
        return jsonify({"error": "Network error reaching the Claude API.", "detail": str(e)}), 502

    secret = os.environ.get("LOVABLE_WEBHOOK_SECRET")
    write_url = resolve_url_env("LOVABLE_CONTENT_DRAFTS_WRITE_URL", DEFAULT_CONTENT_DRAFTS_WRITE_URL)
    forward_result = {"success": None, "status_code": None, "error": None}
    if secret:
        forward_result = forward_to_lovable([draft_for_write(draft)], secret, write_url)

    print(
        f"[content-writer:tasty-six:generate] mlbam_id={draft['mlbam_id']} shelf={draft['shelf']} "
        f"confidence_band={draft['confidence_band']} validation_passed={draft['validation_passed']} "
        f"issues={len(draft['validation_issues'])} "
        f"forward_success={forward_result['success']} forward_status={forward_result['status_code']}",
        flush=True,
    )

    return jsonify({
        "draft": draft,
        "forwarded": forward_result["success"],
        "lovable_status_code": forward_result["status_code"],
        "forward_error": forward_result["error"],
    }), 200


@app.route("/api/content-writer/tasty-six/generate", methods=["GET"])
def generate_tasty_six_health_check():
    return jsonify({
        "status": "ok",
        "usage": "POST /api/content-writer/tasty-six/generate with {\"candidate\": {...one "
                 "shelf_candidates_detailed entry...}} to generate one real, validated Tasty Six "
                 "draft and forward it to content_drafts.",
        "deployed_via": "github-auto-deploy",
    })


@app.route("/api/content-writer/shelf-card/generate", methods=["POST"])
def generate_shelf_card_endpoint():
    """
    Generates ONE real regular shelf card for ONE real candidate — same
    chain as /api/content-writer/tasty-six/generate (shared validators,
    shared Claude-calling code, same content_drafts destination), except
    the card is title + why_reasons only, no editorial_sentence. See
    generate_shelf_card_content.py and shelf_card_writer_schema.py for the
    one real structural difference from the Tasty Six writer.

    NEVER auto-approves or publishes anything. POST body: {"candidate": {...}}
    — one entry from /api/curate-shelves's shelf_candidates_detailed
    response (include_rows: true).

    Optional "debug_inject_violation_instruction" (string): TESTING ONLY,
    same as the Tasty Six endpoint.
    """
    data = request.get_json(force=True, silent=True) or {}
    candidate = data.get("candidate")
    debug_inject_violation_instruction = data.get("debug_inject_violation_instruction")
    if not isinstance(candidate, dict) or "candidate" not in candidate or "shelf" not in candidate:
        return jsonify({
            "error": "POST body must include a real candidate entry: "
                     "{\"candidate\": {...one shelf_candidates_detailed entry, with its own "
                     "\"candidate\"/\"shelf\" keys...}}",
        }), 400

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return jsonify({"error": "ANTHROPIC_API_KEY is not configured"}), 500

    try:
        draft = generate_shelf_card_draft(candidate, api_key, debug_inject_violation_instruction)
    except ValueError as e:
        print(f"[content-writer:shelf-card:generate] bad input: {e}", flush=True)
        return jsonify({"error": str(e)}), 400
    except requests.exceptions.RequestException as e:
        print(f"[content-writer:shelf-card:generate] network_error={e}", flush=True)
        return jsonify({"error": "Network error reaching the Claude API.", "detail": str(e)}), 502

    secret = os.environ.get("LOVABLE_WEBHOOK_SECRET")
    write_url = resolve_url_env("LOVABLE_CONTENT_DRAFTS_WRITE_URL", DEFAULT_CONTENT_DRAFTS_WRITE_URL)
    forward_result = {"success": None, "status_code": None, "error": None}
    if secret:
        forward_result = forward_to_lovable([shelf_card_draft_for_write(draft)], secret, write_url)

    print(
        f"[content-writer:shelf-card:generate] mlbam_id={draft['mlbam_id']} shelf={draft['shelf']} "
        f"confidence_band={draft['confidence_band']} validation_passed={draft['validation_passed']} "
        f"issues={len(draft['validation_issues'])} "
        f"forward_success={forward_result['success']} forward_status={forward_result['status_code']}",
        flush=True,
    )

    return jsonify({
        "draft": draft,
        "forwarded": forward_result["success"],
        "lovable_status_code": forward_result["status_code"],
        "forward_error": forward_result["error"],
    }), 200


@app.route("/api/content-writer/shelf-card/generate", methods=["GET"])
def generate_shelf_card_health_check():
    return jsonify({
        "status": "ok",
        "usage": "POST /api/content-writer/shelf-card/generate with {\"candidate\": {...one "
                 "shelf_candidates_detailed entry...}} to generate one real, validated regular "
                 "shelf card draft (title + why_reasons only) and forward it to content_drafts.",
        "deployed_via": "github-auto-deploy",
    })
