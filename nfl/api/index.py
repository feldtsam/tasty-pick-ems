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
# parent) on the import path. Confirmed directly, not just by analogy:
# the nfl_data_py vendoring fix resolved the earlier crash cleanly, and
# the very next deploy failed at `from lovable_forward import ...` with
# ModuleNotFoundError — lovable_forward.py sits right next to this file
# in nfl/api/, but that directory was never added to sys.path, only its
# parent (nfl/) below. Same root cause and same fix pipeline/api/index.py
# already documents and applies for its own sibling imports
# (flatten_hr_props, etc.) — add this file's own directory first.
sys.path.insert(0, str(Path(__file__).resolve().parent))
# nfl/ itself, so `from market_value import ...` etc. resolve.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# nfl_data_py is vendored, not pip-installed — see nfl/vendor/README.md.
# This is the actual fix for the ModuleNotFoundError this comment sits
# next to: nfl_data_py was never really installable via a plain
# `pip install -r requirements.txt` in the first place (0.3.3's own PyPI
# metadata pins pandas<2.0, which conflicts outright with this project's
# pandas 2.x requirement — confirmed directly, pip refuses the combination
# with ResolutionImpossible, not a --no-deps-able situation), so it was
# never really being installed by Vercel's build even before this file
# started importing it.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vendor"))
# nfl/scripts/, so `from reconcile_week import ...` resolves — the poller
# endpoint above never needed this (market_value.py lives in nfl/ itself),
# but reconcile_week.py lives in scripts/ alongside backfill_redzone.py.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from flask import Flask, jsonify, request
import nfl_data_py as nfl
import numpy as np
import pandas as pd

from curate_home_shelves import curate_nfl_shelves, write_content_draft_rows
from lovable_forward import forward_to_lovable, resolve_url_env, truncate_for_log
from market_value import (
    PRICE_HISTORY_COLUMNS,
    match_attd_players,
    new_price_history_rows,
    parse_attd_event,
    snapshot_scoring_inputs,
)
from reconcile_week import reconcile_week, week_is_complete

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
    # `if not secret` above only catches missing/empty — a value saved with
    # a stray leading/trailing space (easy to introduce copy-pasting into
    # Vercel's env var UI) is still truthy and would silently produce a
    # signature Lovable's side never matches. Logs length and a whitespace
    # flag only, never the value itself, so that failure mode is
    # diagnosable from Vercel logs without ever exposing the secret.
    if secret != secret.strip():
        print(
            f"[env-config] WARNING: NFL_PIPELINE_WEBHOOK_SECRET has leading/trailing "
            f"whitespace (len={len(secret)}, stripped_len={len(secret.strip())}) — "
            f"this will produce a signature Lovable's side won't match.",
            flush=True,
        )
    write_url = resolve_url_env("LOVABLE_NFL_PRICE_HISTORY_WRITE_URL", DEFAULT_NFL_PRICE_HISTORY_WRITE_URL)

    forward_result = {"success": None, "status_code": None, "error": None}
    if rows:
        forward_result = forward_to_lovable(rows, secret, write_url)

    # forward_result['error']/['response_body'] are now FULL/untruncated
    # (see forward_to_lovable's own docstring for the real bug that fix
    # closes) — truncate_for_log() applies the same bound this print
    # statement always relied on, right here at the actual print site,
    # so Vercel's own function logs (which otherwise show just a bare
    # status code, not Lovable's actual response text) stay just as
    # readable/bounded as before, without capping the value every OTHER
    # caller of forward_to_lovable also receives.
    print(
        f"[poll-market-value] events_received={len(events)} "
        f"events_with_no_market={len(events_with_no_market)} "
        f"matched={match_summary['matched']} unmatched={match_summary['unmatched']} "
        f"by_issue_type={match_summary['by_issue_type']} "
        f"rows_written={len(rows)} "
        f"forward_success={forward_result['success']} forward_status={forward_result['status_code']} "
        f"forward_error={truncate_for_log(forward_result['error'], 500)!r} "
        f"forward_response_body={truncate_for_log(forward_result.get('response_body'))!r}",
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


@app.route("/api/reconcile-week", methods=["POST"])
def reconcile_week_endpoint():
    """
    POST body: {"season": int, "week": int}.

    AUTH: check_pipeline_secret() (X-Pipeline-Secret / PIPELINE_INCOMING_
    SECRET) — the SAME mechanism /api/poll-market-value already uses for
    its own incoming trigger, not HMAC/compute_signature. That distinction
    matters and was confirmed directly, not assumed: compute_signature/
    serialize_payload (NFL_PIPELINE_WEBHOOK_SECRET) are this codebase's
    OUTBOUND signing for a genuinely variable, attacker-shaped body being
    written TO Lovable (see check_pipeline_secret's own docstring) —
    reconciliation has no such outbound Lovable write at all (see below),
    so there's nothing here to sign that way; a small, fixed, Make.com-
    only incoming trigger is exactly what check_pipeline_secret is for.

    THE HARD SAFETY GATE: week_is_complete() runs FIRST, always, before
    anything destructive. If any of the week's real games aren't final
    yet, returns 200 status="not_ready" and reconcile_week() is never
    called — even if Make.com's own scheduling/filter logic is somehow
    wrong, this is the backstop that makes running reconciliation against
    an incomplete week structurally impossible from this endpoint, not
    just discouraged by convention.

    NO LOVABLE FORWARDING HERE, deliberately — unlike /api/poll-market-
    value, reconcile_week() only ever writes to a local CSV file
    (player_redzone_weekly.csv) and archives the stub file; there's no
    webhook write step to report a lovable_status_code/forward_error
    for. Using the parts of that endpoint's response convention that
    actually apply here (structured status, clear error reporting) and
    skipping the parts that don't, rather than forcing in fields that
    would always be null.

    SCOPED TO THE TARGET SEASON ONLY (historical_seasons=[season]), not
    reconcile_week()'s own default (backfill_redzone.SEASONS — ALL of
    2022/2024/2025 plus the target season, every call). Correctness:
    add_rolling_windows groups by (player_id, season), so trailing
    windows already reset at season boundaries — a season's own
    reconciliation never needs a different season's play-by-play.
    Performance, measured directly: single-season run_pipeline (2025
    alone) completes in ~16s locally vs. ~45s for the full 3-season
    default — roughly a 3x reduction, though this is a LOCAL timing, not
    a real Vercel benchmark; treat it as directional, not a guarantee
    against Vercel's actual configured timeout.

    THIS REQUIRED A REAL FIX FIRST, not just a config change — an earlier
    attempt at this exact scoping crashed (KeyError: 'position', real
    2025 Week 10 test): redzone._skill_position_depth_chart unconditionally
    read a "position" column that only exists in the pre-2025 depth-chart
    schema, and a depth_charts pull scoped to ONLY a 2025+ season has none
    of that schema's columns at all (confirmed directly: nfl_data_py.
    import_depth_charts([2025]) alone returns just dt/team/player_name/
    gsis_id/pos_abb/pos_rank/...) — _combined_depth_chart's old+new-schema
    concat needs at least one old-schema season actually present in the
    pull for that column to exist. Fixed at the source in redzone.py
    (_skill_position_depth_chart returns an empty, correctly-shaped frame
    when "position" isn't present, instead of raising) — confirmed this
    is a strict no-op for every existing multi-season call site (0 diff
    cells across all 107 columns, full historical backfill re-run before
    vs. after) and confirmed it actually fixes the single-season case
    (real 2025-only run_pipeline: depth_rank now populated for 84.7% of
    Week 10 rows, not silently all-NaN). See redzone.py's own docstring
    for the full investigation.
    """
    auth_error = check_pipeline_secret()
    if auth_error:
        return auth_error

    data = request.get_json(force=True, silent=True) or {}
    try:
        season = int(data.get("season"))
        week = int(data.get("week"))
    except (TypeError, ValueError):
        return jsonify({"error": "Expected {\"season\": int, \"week\": int} in the request body."}), 400

    readiness = week_is_complete(season, week)
    if not readiness["all_final"]:
        reason = f"{readiness['total_games'] - readiness['final_games']} of {readiness['total_games']} games not yet final"
        print(f"[reconcile-week] season={season} week={week} status=not_ready reason={reason!r}", flush=True)
        return jsonify({
            "status": "not_ready",
            "reason": reason,
            "pending_games": readiness["pending_games"],
        }), 200

    print(f"[reconcile-week] season={season} week={week} status=ready -- proceeding to reconcile", flush=True)
    try:
        reconciled = reconcile_week(season, week, historical_seasons=[season])
    except Exception as e:
        print(f"[reconcile-week] season={season} week={week} status=error error={e!r}", flush=True)
        return jsonify({"status": "error", "season": season, "week": week, "error": str(e)}), 500

    rows_reconciled = len(reconciled)
    rows_with_market_value = int(reconciled["market_value_score"].notna().sum())
    print(
        f"[reconcile-week] season={season} week={week} status=success "
        f"rows_reconciled={rows_reconciled} rows_with_market_value={rows_with_market_value}",
        flush=True,
    )
    return jsonify({
        "status": "success",
        "season": season,
        "week": week,
        "rows_reconciled": rows_reconciled,
        "rows_with_market_value": rows_with_market_value,
    }), 200


@app.route("/api/reconcile-week", methods=["GET"])
def reconcile_week_health_check():
    return jsonify({
        "status": "ok",
        "usage": "POST {\"season\": int, \"week\": int}. Checks week_is_complete() first — if any "
                 "of that week's real games aren't final yet, returns status=\"not_ready\" and never "
                 "runs reconciliation. Only calls reconcile_week() (destructive: replaces this week's "
                 "rows in player_redzone_weekly.csv and archives the stub file) once every game is "
                 "confirmed final.",
        "deployed_via": "github-auto-deploy",
    })


STUB_WEEKS_DIR = Path(__file__).resolve().parent.parent / "data" / "stub_weeks"


def _json_safe(obj):
    """
    Recursively converts numpy/pandas scalar types to plain Python so
    both this endpoint's own jsonify() response AND write_content_draft_
    rows()'s underlying serialize_payload (a plain json.dumps with no
    numpy-aware default) don't crash on a numpy.float64/int64/bool_ that
    curate_nfl_shelves' output can genuinely contain (values sourced from
    pandas Series columns throughout the curation pipeline). Applied
    once, right after shape_content_draft_rows produces rows, so both
    consumers see the same already-safe Python-native values — never a
    silent, separate re-serialization that could drift from what was
    actually reported back in this endpoint's own response.
    """
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return None if np.isnan(obj) else float(obj)
    if isinstance(obj, float) and pd.isna(obj):
        return None
    return obj


@app.route("/api/curate-and-write-drafts", methods=["POST"])
def curate_and_write_drafts_endpoint():
    """
    POST body: {"season": int, "week": int, "max_rows_to_write": int
    (optional), "player_ids_to_write": [str, ...] (optional)}.

    AUTH: check_pipeline_secret() — same mechanism /api/reconcile-week
    already uses for its own incoming trigger, same reasoning (a small,
    fixed, Make.com-only trigger with no attacker-shaped body worth
    HMAC-signing — see check_pipeline_secret's own docstring).

    DATA SOURCE: nfl/data/stub_weeks/{season}_wk{week}.csv — the real,
    live, pre-game scored data Phase 1 (build_stub_week.py) and Phase 2
    (poll_market_value_for_stub.py, the live odds poller) already
    produce for the CURRENT upcoming week. This is deliberately NOT
    player_redzone_weekly.csv (the historical, already-reconciled
    table) — curation is a pre-game, upcoming-picks feature; the stub
    file is the only real source with live ATTD odds on it at all
    before a week goes final and gets reconciled away.

    Runs the full pipeline (curate_nfl_shelves: eligibility -> home-
    shelf assignment -> cap -> Tasty Six -> content shaping, INCLUDING a
    real Part C LLM call for Tasty Six rows whenever ANTHROPIC_API_KEY
    is configured) and writes the result to nfl_content_drafts via
    write_content_draft_rows() — inside this deployed function,
    ANTHROPIC_API_KEY / NFL_PIPELINE_WEBHOOK_SECRET / LOVABLE_NFL_
    CONTENT_DRAFTS_WRITE_URL all resolve correctly via os.environ.get(),
    the same way every other secret already does for the existing
    endpoints above — this was the whole reason this endpoint needed to
    exist as a real deployment rather than being tested locally (Vercel
    "Sensitive" env vars are write-only; `vercel env pull` cannot
    retrieve their real value outside a running deployed function).

    preview_only: if true, curation still runs in full (including real
    Tasty Six LLM generation) but NOTHING is written to Lovable — the
    response's curated_rows carries every real row instead, for
    inspecting what a real run would produce (and picking specific real
    player_ids) before committing to an actual write. max_rows_to_write /
    player_ids_to_write: OPTIONAL scoping for the actual write step
    only (ignored when preview_only is set) — curation still runs
    against the FULL real stub-week pool either way, but only the
    matching subset of rows is actually sent to Lovable. Built for
    controlled single-row/small-batch real testing without needing
    multiple different real weeks of data; omit all three to write
    everything curation produced (the real eventual Make.com Part 3
    shape).

    KNOWN, NOT FIXED HERE: a Tasty Six LLM generation failure (bad key,
    rate limit, Claude API timeout) raises inside shape_content_draft_
    rows() and aborts curation entirely for this request, including
    every correctly-generated deterministic regular-row card in the
    same batch — flagged directly in the response as a 500 if it
    happens, not silently retried. A real, contained follow-up (partial-
    failure handling), not addressed as part of this task's scope.
    """
    auth_error = check_pipeline_secret()
    if auth_error:
        return auth_error

    data = request.get_json(force=True, silent=True) or {}
    try:
        season = int(data.get("season"))
        week = int(data.get("week"))
    except (TypeError, ValueError):
        return jsonify({"error": "Expected {\"season\": int, \"week\": int} in the request body."}), 400
    max_rows_to_write = data.get("max_rows_to_write")
    player_ids_to_write = data.get("player_ids_to_write")

    stub_path = STUB_WEEKS_DIR / f"{season}_wk{week}.csv"
    if not stub_path.exists():
        return jsonify({
            "error": f"No stub file for season={season} week={week} at {stub_path} — "
                     f"build_stub_week.py hasn't run for this week yet.",
        }), 404
    weekly = pd.read_csv(stub_path)

    try:
        schedules = nfl.import_schedules([season])
    except Exception as e:
        print(f"[curate-and-write-drafts] season={season} week={week} schedules_fetch_failed error={e!r}", flush=True)
        schedules = None  # kickoff_utc stays None for every row -- honest gap, not a hard failure

    anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY")

    try:
        result = curate_nfl_shelves(weekly, season, week, schedules=schedules, anthropic_api_key=anthropic_api_key)
    except Exception as e:
        print(f"[curate-and-write-drafts] season={season} week={week} status=error error={e!r}", flush=True)
        return jsonify({"status": "error", "season": season, "week": week, "error": str(e)}), 500

    all_rows = _json_safe(result["content_draft_rows"])
    preview_only = bool(data.get("preview_only"))

    # Real schema requires title (non-empty string), why_reasons must be
    # a real array (empty is fine per the schema, but a row with no
    # generated content at all has why_reasons=[] AND title=None, and a
    # null title alone would fail real validation outright) -- rows with
    # no real content (no anthropic_api_key given, or the writer call
    # never produced anything) are never sent, regardless of scoping.
    # Genuinely reportable in curated_rows either way (see shape_
    # content_draft_rows) -- just never written incomplete.
    content_ready_rows = [r for r in all_rows if r.get("title")]
    rows_without_content = len(all_rows) - len(content_ready_rows)

    rows_to_write = [] if preview_only else content_ready_rows
    if not preview_only:
        if player_ids_to_write:
            rows_to_write = [r for r in rows_to_write if r["player_id"] in set(player_ids_to_write)]
        if isinstance(max_rows_to_write, int):
            rows_to_write = rows_to_write[:max_rows_to_write]

    forward_result = {"success": None, "status_code": None, "error": None}
    if preview_only:
        pass  # real curation still ran (including real Tasty Six generation) -- just never written to Lovable
    else:
        secret = os.environ.get("NFL_PIPELINE_WEBHOOK_SECRET")
        if not secret:
            return jsonify({"error": "NFL_PIPELINE_WEBHOOK_SECRET is not configured"}), 500
        if rows_to_write:
            forward_result = write_content_draft_rows(rows_to_write, secret)

    print(
        f"[curate-and-write-drafts] season={season} week={week} "
        f"rows_curated={len(all_rows)} rows_without_content={rows_without_content} "
        f"rows_written={len(rows_to_write)} "
        f"tasty_six_curated={sum(1 for r in all_rows if r['is_tasty_six'])} "
        f"forward_success={forward_result['success']} forward_status={forward_result['status_code']} "
        f"forward_error={truncate_for_log(forward_result['error'], 500)!r} "
        f"forward_response_body={truncate_for_log(forward_result.get('response_body'))!r}",
        flush=True,
    )

    return jsonify({
        "season": season,
        "week": week,
        "preview_only": preview_only,
        "rows_curated": len(all_rows),
        "rows_without_content": rows_without_content,
        "curated_rows": all_rows if preview_only else None,
        "rows_written": len(rows_to_write),
        "written_rows": rows_to_write,
        "forwarded": forward_result["success"],
        "lovable_status_code": forward_result["status_code"],
        "forward_error": forward_result["error"],
        "forward_response_body": forward_result.get("response_body"),
    }), (502 if forward_result["success"] is False else 200)


@app.route("/api/curate-and-write-drafts", methods=["GET"])
def curate_and_write_drafts_health_check():
    return jsonify({
        "status": "ok",
        "usage": "POST {\"season\": int, \"week\": int, \"max_rows_to_write\": int (optional), "
                 "\"player_ids_to_write\": [str, ...] (optional)}. Curates the given week's real "
                 "stub-week data (home-shelf assignment, Tasty Six, real content — including a real "
                 "Claude call for Tasty Six rows) and writes the result to nfl_content_drafts. "
                 "max_rows_to_write/player_ids_to_write scope the actual write only; curation always "
                 "runs against the full real pool.",
        "deployed_via": "github-auto-deploy",
    })
