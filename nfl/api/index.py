"""
HTTP entry point for Vercel — NFL Market Value live polling.

Deliberately a SEPARATE Vercel deployment from pipeline/api/index.py
(MLB), not a fifth route added to that app. nfl/ has been a fully
self-contained domain throughout this project (its own venv, its own
requirements.txt, "duplicate rather than cross-import" as the stated rule
everywhere else in this codebase) — this endpoint keeps that boundary
rather than reaching into pipeline/'s deployment or bloating its
currently-minimal flask+requests footprint with pandas/numpy/nfl_data_py/
pyarrow, and avoids betting on whether Vercel's Python build would even
bundle a sibling top-level directory outside pipeline/'s own tree (no
vercel.json exists in this repo to configure that either way).

check_pipeline_secret() (this file) and the signed-webhook forwarding
pattern (lovable_forward.py, this directory) are COPIED from
pipeline/api/index.py and pipeline/api/lovable_forward.py, not imported —
same code shape, same auth mechanism, same env var naming convention, but
no cross-deployment dependency. If either pattern changes on the MLB
side, both copies need updating by hand; that's the accepted cost of
keeping the two deployments independent.

Accepts a POST with raw Odds API event data for the player_anytime_td
market — Make.com's own Odds-API-fetch step supplies it, this endpoint
never calls The Odds API itself, matching the real MLB precedent
(Make.com's odds module fetches, POSTs into the pipeline) rather than an
earlier draft of this endpoint that would have fetched independently.
Same input-shape convention as MLB's /api/flatten-and-forward: a single
event object (has a "bookmakers" key), a list of event objects, or
{"events": [...]}.

Runs the existing, already-validated market_value.py functions
unmodified (parse_attd_event / match_attd_players / snapshot_scoring_
inputs / new_price_history_rows) — this file is plumbing around them, not
new scoring or parsing logic.

THIN/ZERO BOOK COVERAGE IS THE NORMAL CASE, not a failure — an event this
far from kickoff with only 1 book posted, or even 0 books posted for this
specific market, produces fewer (or zero) price-history rows for that
event, not an error for the whole request. Found and worked around a real
gap in the process: parse_attd_event returns a zero-COLUMN empty
DataFrame when an event has no player_anytime_td market at all (not just
zero rows with the right columns), and match_attd_players crashes on that
input (KeyError: 'is_dst') — confirmed directly, not assumed. Rather than
touching the already-validated market_value.py functions to harden them
against an input shape they were never actually exercised against, events
with zero parsed rows are filtered out HERE, before they ever reach
match_attd_players — a plumbing-level guard, zero changes to the scoring/
parsing logic itself.
"""
import hmac
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Vercel's Python runtime doesn't put this file's own directory (or its
# parent) on the import path — add the parent (nfl/) explicitly so
# `from market_value import ...` etc. resolve, same fix pipeline/api/
# index.py applies for its own sibling directories.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, jsonify, request
import nfl_data_py as nfl
import pandas as pd

from lovable_forward import forward_to_lovable, resolve_url_env
from market_value import (
    PRICE_HISTORY_COLUMNS,
    match_attd_players,
    new_price_history_rows,
    parse_attd_event,
    snapshot_scoring_inputs,
)

app = Flask(__name__)

# Fallback only — the real value should come from the
# LOVABLE_NFL_PRICE_HISTORY_WRITE_URL Vercel env var so a future URL
# change is a config update, not a code change + redeploy. NOT a real
# route yet — the nfl_price_history table/route doesn't exist on the
# Lovable side at the time this was written (see the schema draft
# reported alongside this endpoint). Documents the expected naming
# convention; will 502 harmlessly (not silently succeed) until the real
# env var is set to the real route.
DEFAULT_NFL_PRICE_HISTORY_WRITE_URL = "https://tastypickems.lovable.app/api/public/nfl-price-history-write"


def check_pipeline_secret():
    """
    Copied from pipeline/api/index.py's function of the same name (see
    module docstring for why it's copied, not imported). Same reasoning:
    a small, fixed, Make.com-only trigger with no attacker-shaped body
    worth signing — a constant-time shared-secret comparison is the right
    amount of protection for "only Make.com should be able to kick this
    off", not a mismatched upgrade to full HMAC signing (that's what
    forward_to_lovable's X-Signature is for, on the outbound Lovable
    write, a genuinely variable attacker-shaped body).

    PIPELINE_INCOMING_SECRET is configured independently in THIS Vercel
    project's own env vars — being a separate deployment from the MLB
    pipeline (see module docstring), it does not automatically share that
    project's value. It's fine to reuse the same secret string across
    both projects or use a different one; either way it has to be set
    here too.

    Returns a (401, jsonify(...)) Flask response tuple if the request
    should be rejected, or None if it's authorized to proceed.
    """
    expected = os.environ.get("PIPELINE_INCOMING_SECRET")
    if not expected:
        return jsonify({"error": "PIPELINE_INCOMING_SECRET is not configured"}), 500
    provided = request.headers.get("X-Pipeline-Secret")
    if not provided or not hmac.compare_digest(provided, expected):
        return jsonify({"error": "Missing or invalid X-Pipeline-Secret header"}), 401
    return None


def _normalize_events_input(data):
    """Same input-shape convention as MLB's /api/flatten-and-forward:
    a single event object, a list of events, or {"events": [...]}."""
    if isinstance(data, dict) and "events" in data:
        return data["events"]
    if isinstance(data, dict) and "bookmakers" in data:
        return [data]
    if isinstance(data, list):
        return data
    return None


def _season_for_commence_time(commence_time: str) -> int:
    """
    NFL season year: Aug-Dec games belong to that calendar year's season;
    Jan-July games belong to the season that started the previous August
    (playoffs/Super Bowl run into the following January-February). Not
    read from anywhere else in the payload — The Odds API's event objects
    don't carry a season field, only commence_time.
    """
    dt = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
    return dt.year if dt.month >= 8 else dt.year - 1


EXPECTED_INPUT_ERROR = (
    "Expected a single Odds API event object (with a 'bookmakers' key), "
    "a list of event objects, or {\"events\": [...]}."
)


@app.route("/api/poll-market-value", methods=["POST"])
def poll_market_value_endpoint():
    """
    POST body: raw Odds API event(s) for the player_anytime_td market
    (see _normalize_events_input for the accepted shapes). For each event:
    parse it, skip it (not error) if zero books have posted this market
    yet, otherwise match player names against that event's season's
    rosters and compute snapshot scoring inputs (implied probability,
    consensus, best price). All resulting price-history rows across every
    event in the request are combined into one batch and forwarded to
    Lovable's nfl-price-history-write webhook in a single signed POST.

    Events are grouped by season (derived from commence_time) before
    matching, so seasonal_rosters/team_desc are only fetched once per
    distinct season present in the batch, not once per event — batches
    will typically be all one season in practice, but this stays correct
    if that's ever not true (e.g. very late in one season with next
    season's early lines already posted).
    """
    auth_error = check_pipeline_secret()
    if auth_error:
        return auth_error

    raw_body = request.get_data()
    data = request.get_json(force=True, silent=True)
    events = _normalize_events_input(data)
    if events is None:
        print(
            f"[poll-market-value] bad_input content_type={request.content_type!r} "
            f"raw_body_len={len(raw_body)} raw_body_preview={raw_body[:300]!r}",
            flush=True,
        )
        return jsonify({"error": EXPECTED_INPUT_ERROR}), 400

    poll_timestamp = datetime.now(timezone.utc).isoformat()

    parsed_by_season = {}
    events_with_no_market = []
    for event in events:
        parsed = parse_attd_event(event)
        if len(parsed) == 0:
            events_with_no_market.append(event.get("id"))
            continue
        season = _season_for_commence_time(event["commence_time"])
        parsed_by_season.setdefault(season, []).append(parsed)

    price_history_parts = []
    match_summary = {"matched": 0, "unmatched": 0, "by_issue_type": {}}
    for season, parts in parsed_by_season.items():
        parsed_season = pd.concat(parts, ignore_index=True)
        seasonal_rosters = nfl.import_seasonal_rosters([season])
        team_desc = nfl.import_team_desc()
        matched, unmatched = match_attd_players(parsed_season, seasonal_rosters, team_desc, season)

        snap = snapshot_scoring_inputs(matched) if len(matched) else pd.DataFrame(columns=PRICE_HISTORY_COLUMNS)
        price_history_parts.append(new_price_history_rows(snap, unmatched, poll_timestamp))

        match_summary["matched"] += len(matched)
        match_summary["unmatched"] += len(unmatched)
        if len(unmatched):
            for issue_type, count in unmatched["match_issue_type"].value_counts().items():
                match_summary["by_issue_type"][issue_type] = match_summary["by_issue_type"].get(issue_type, 0) + int(count)

    combined = pd.concat(price_history_parts, ignore_index=True) if price_history_parts else pd.DataFrame(columns=PRICE_HISTORY_COLUMNS)
    # Standard pandas idiom for JSON-safe records (NaN -> null, numpy
    # scalar types -> plain Python) -- more reliable here than
    # .to_dict("records"), which can leave numpy int64/float64/NaN in
    # place even on an object-dtype frame and break json.dumps deep
    # inside forward_to_lovable's serialize_payload.
    rows = json.loads(combined.to_json(orient="records")) if len(combined) else []

    secret = os.environ.get("NFL_PIPELINE_WEBHOOK_SECRET")
    if not secret:
        return jsonify({"error": "NFL_PIPELINE_WEBHOOK_SECRET is not configured"}), 500
    write_url = resolve_url_env("LOVABLE_NFL_PRICE_HISTORY_WRITE_URL", DEFAULT_NFL_PRICE_HISTORY_WRITE_URL)

    forward_result = {"success": None, "status_code": None, "error": None}
    if rows:
        forward_result = forward_to_lovable(rows, secret, write_url)

    print(
        f"[poll-market-value] events_received={len(events)} "
        f"events_with_no_market={len(events_with_no_market)} "
        f"matched={match_summary['matched']} unmatched={match_summary['unmatched']} "
        f"by_issue_type={match_summary['by_issue_type']} "
        f"rows_written={len(rows)} "
        f"forward_success={forward_result['success']} forward_status={forward_result['status_code']}",
        flush=True,
    )

    return jsonify({
        "events_received": len(events),
        "events_with_no_market": events_with_no_market,
        "events_processed": len(events) - len(events_with_no_market),
        "match_summary": match_summary,
        "rows_written": len(rows),
        "forwarded": forward_result["success"],
        "lovable_status_code": forward_result["status_code"],
        "forward_error": forward_result["error"],
    }), (502 if forward_result["success"] is False else 200)


@app.route("/api/poll-market-value", methods=["GET"])
def poll_market_value_health_check():
    return jsonify({
        "status": "ok",
        "usage": "POST a raw Odds API event (or list of events, or {\"events\": [...]}) for the "
                 "player_anytime_td market. Parses, matches, and scores each event's real quotes "
                 "and forwards the resulting price-history rows to Lovable. Zero books posted for "
                 "an event is a normal result (see events_with_no_market in the response), not an error.",
        "deployed_via": "github-auto-deploy",
    })
