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

from curate_home_shelves import (
    curate_nfl_shelves,
    read_content_draft_review_states,
    write_content_draft_rows,
)
from intelligence_generate import FAMILIES, generate_and_write_intelligence
from intelligence_write import process_family, write_intelligence_rows
from lovable_forward import forward_to_lovable, resolve_url_env, truncate_for_log
from market_value import (
    PRICE_HISTORY_COLUMNS,
    match_attd_players,
    new_price_history_rows,
    parse_attd_event,
    snapshot_scoring_inputs,
)
from build_stub_week import build_stub_week
from reconcile_week import reconcile_week, week_is_complete
from stub_store import shape_stub_rows, stub_week_snapshot, write_stub_rows

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


def _week_lookup_for_season(season: int, schedules: pd.DataFrame, team_desc: pd.DataFrame) -> dict:
    """
    {(home_team_full_name, away_team_full_name): week} for one season,
    keyed by The Odds API's own full-name format (e.g. "Seattle
    Seahawks") so a caller can look up directly from a raw event's own
    home_team/away_team with no per-row translation at lookup time.

    UNLIKE _season_for_commence_time (pure date math, no schedule
    needed), week genuinely requires a real schedule lookup — NFL week
    boundaries don't align to calendar boundaries consistently enough
    to derive from commence_time alone.

    REUSES, not invents: the exact (home_team, away_team) exact-order
    matching convention scripts/poll_market_value_for_stub.py's own
    fetch_week_events() already established and validated against real
    data — confirmed directly there (real 2026 Week 1 data: SEA/NE,
    PIT/ATL, KC/DEN all matched without flipping) that The Odds API's
    home_team/away_team already matches nflverse's schedule convention
    exactly. Deliberately NOT falling back to a flipped-orientation
    match: that was tried in that same investigation and caused real
    contamination (a divisional rematch's second meeting, home/away
    reversed, incorrectly matched the FIRST meeting's week too).

    A game missing from this dict (e.g. team_desc has no abbreviation
    for one of the two names) simply isn't resolvable — the caller's
    own .get() on this dict returns None for that event, an honest
    "week not resolved," not a guess.
    """
    name_to_abbr = dict(zip(team_desc["team_name"], team_desc["team_abbr"]))
    abbr_to_name = {v: k for k, v in name_to_abbr.items()}
    season_games = schedules[schedules["season"] == season]
    return {
        (abbr_to_name.get(row["home_team"]), abbr_to_name.get(row["away_team"])): int(row["week"])
        for _, row in season_games.iterrows()
    }


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

    A MALFORMED INDIVIDUAL EVENT NEVER FAILS THE WHOLE BATCH — real fix,
    confirmed before this endpoint was scheduled hourly-ish via Make.com:
    each event's parse (parse_attd_event + the commence_time -> season
    resolution) is wrapped in its own try/except; an event that raises for
    any reason (missing/null/malformed commence_time, or a bookmakers/
    markets/outcomes level that arrived wrong-typed) is skipped and
    recorded in the response's events_with_parse_errors, exactly the same
    "graceful skip, not fatal" treatment events_with_no_market already
    got — every other event in the same request still processes normally.
    Before this fix there was no try/except anywhere in this loop, so one
    bad event crashed the entire request with a generic 500 and left zero
    trace of which event caused it, in either the response or the logs
    (the diagnostic print() only ran after all processing completed).

    Events are grouped by season (derived from commence_time) before
    matching, so seasonal_rosters/team_desc/schedules are only fetched
    once per distinct season present in the batch, not once per event —
    batches will typically be all one season in practice, but this stays
    correct if that's ever not true (e.g. very late in one season with
    next season's early lines already posted).

    season/week ARE NOW REAL on every written row — CONFIRMED FIX, not
    new scope: new_price_history_rows() used to hardcode both to None
    unconditionally, discovered when a real downstream query (scripts/
    reconcile_week.py's own live deployment test) needed to filter
    nfl_price_history by (player_id, season, week) and got zero real
    rows back — every row had season=week=NULL by construction, not by
    missing data. season is a single real value per season-batch (see
    the loop below); week is resolved per row via _week_lookup_for_
    season, since one batch can span multiple weeks within a season.
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
    # REAL BUG FIXED HERE, found before this endpoint was scheduled via
    # Make.com: event["commence_time"] below used to be a direct-index
    # access with no try/except anywhere around this loop — a malformed
    # event with real market data (so it isn't caught by the "no market"
    # skip above) but a missing/null/non-ISO commence_time raised an
    # uncaught KeyError/AttributeError/ValueError that crashed the ENTIRE
    # request with a generic 500, including every other event in the same
    # batch that would otherwise have processed fine. Confirmed via a full
    # audit (not just this one line): parse_attd_event() itself is also a
    # real crash surface for the same reason (AttributeError if `event`,
    # or any bookmaker/market/outcome nested inside it, isn't actually a
    # dict — the same "a nesting level arrived wrong-typed" incident class
    # pipeline/api/flatten_hr_props.py's own docstring already documents
    # as a real, previously-hit MLB-side issue, not hypothetical). Both
    # are covered by wrapping the WHOLE per-event body in one broad
    # except, not narrowly `except KeyError` — a narrow catch would leave
    # this exact crash reachable via any of the other exception types for
    # the same malformed field.
    events_with_parse_errors = []
    for event in events:
        event_id = event.get("id") if isinstance(event, dict) else None
        try:
            parsed = parse_attd_event(event)
            if len(parsed) == 0:
                events_with_no_market.append(event_id)
                continue
            season = _season_for_commence_time(event["commence_time"])
            parsed_by_season.setdefault(season, []).append(parsed)
        except Exception as e:
            events_with_parse_errors.append({"event_id": event_id, "error": str(e)})
            print(
                f"[poll-market-value] event_id={event_id!r} parse_error={e!r} — "
                f"skipping this event, batch continues",
                flush=True,
            )
            continue

    price_history_parts = []
    match_summary = {"matched": 0, "unmatched": 0, "by_issue_type": {}}
    for season, parts in parsed_by_season.items():
        parsed_season = pd.concat(parts, ignore_index=True)
        seasonal_rosters = nfl.import_seasonal_rosters([season])
        team_desc = nfl.import_team_desc()
        schedules = nfl.import_schedules([season])
        matched, unmatched = match_attd_players(parsed_season, seasonal_rosters, team_desc, season)

        # week: resolved per-row, not a single constant like season above
        # — a batch CAN span multiple weeks within one season (see
        # _week_lookup_for_season's own docstring). Built once per season,
        # not once per row/event, then applied via home_team/away_team,
        # which both matched_snapshot (see snapshot_scoring_inputs' own
        # group_cols) and unmatched (never dropped by match_attd_players)
        # already carry.
        week_lookup = _week_lookup_for_season(season, schedules, team_desc)

        snap = snapshot_scoring_inputs(matched) if len(matched) else pd.DataFrame(columns=PRICE_HISTORY_COLUMNS)
        if len(snap) and "home_team" in snap.columns:
            snap["week"] = snap.apply(lambda r: week_lookup.get((r["home_team"], r["away_team"])), axis=1)
        if len(unmatched) and "home_team" in unmatched.columns:
            unmatched["week"] = unmatched.apply(lambda r: week_lookup.get((r["home_team"], r["away_team"])), axis=1)

        price_history_parts.append(new_price_history_rows(snap, unmatched, poll_timestamp, season))

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
        f"events_with_parse_errors={len(events_with_parse_errors)} "
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
        "events_with_parse_errors": events_with_parse_errors,
        "events_processed": len(events) - len(events_with_no_market) - len(events_with_parse_errors),
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
                 "an event is a normal result (see events_with_no_market in the response), not an "
                 "error. A malformed event (see events_with_parse_errors in the response) is also "
                 "skipped, not fatal to the rest of the batch.",
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

    NOW DOES A REAL LOVABLE WRITE (Phase 1 of the Role Changes/Defensive
    Trends live-wiring project) — CORRECTED from an earlier version of
    this docstring, which described reconcile_week() as CSV-only.
    reconcile_week() itself now persists to the real nfl_player_redzone_
    weekly table (upsert on player_id/season/week) instead of the local
    player_redzone_weekly.csv (confirmed broken in production: never
    git-tracked, and even if deployed would write to Vercel's read-only
    bundle path / non-persistent /tmp). NFL_PIPELINE_WEBHOOK_SECRET is
    resolved internally by reconcile_week() itself (not passed here
    explicitly) — same env var this endpoint's own outbound writes
    already use elsewhere in this file. A failed persistence write
    raises inside reconcile_week() and is caught by the existing
    except block below as a real status="error" response — no new
    error-handling needed here for that case. reconcile_week() also
    flags this week's nfl_stub_weeks rows `reconciled = true` (Phase A
    of the weekly-automation plan — replaced the old stub-file archive
    move); a failure there is logged, not fatal.

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
                 "runs reconciliation. Only calls reconcile_week() (destructive: upserts this week's "
                 "rows into nfl_player_redzone_weekly and flags nfl_stub_weeks.reconciled) once every "
                 "game is confirmed final.",
        "deployed_via": "github-auto-deploy",
    })


STUB_WEEKS_DIR = Path(__file__).resolve().parent.parent / "data" / "stub_weeks"


def _load_stub_csv(stub_csv: str) -> pd.DataFrame:
    """
    The `--from-csv` escape hatch shared by /api/build-stub-week and
    /api/curate-and-write-drafts: read a stub week from a CSV instead of
    the nfl_stub_weeks table, so the committed data/stub_weeks/*.csv
    fixtures (and any local CSV) stay usable for a real end-to-end test
    without seeding the table first.

    An http(s) value is read directly; anything else is treated as a
    filename under data/stub_weeks/ in the deployed bundle (so a caller
    can pass just "2026_wk1.csv"). Only reachable behind
    check_pipeline_secret() — a Make.com-only trigger, not a public
    surface.
    """
    if str(stub_csv).startswith(("http://", "https://")):
        return pd.read_csv(stub_csv)
    return pd.read_csv(STUB_WEEKS_DIR / Path(str(stub_csv)).name)


def _rebind_stub_frame(stub_week: pd.DataFrame, season: int, week: int) -> pd.DataFrame:
    """
    Rebind a stub frame loaded from a fixture (via _load_stub_csv) to a
    target (season, week): overwrite the season/week partition columns
    AND rewrite the {season}_{week} prefix of every game_id.

    The game_id rewrite is the part that matters for safety.
    curate_home_shelves derives event_id DIRECTLY from game_id, and
    nfl_content_drafts' natural key is (player_id, event_id, shelf,
    writer_type) — no season/week in it. Without rewriting game_id, a
    curated draft row from a rebound fixture keeps the SOURCE week's
    event_id and its write silently upserts onto whatever real
    production row already exists for that source week. That is exactly
    what corrupted 4 real Week-1 rows during the first live smoke test
    (fixture 2026_wk1.csv relabeled to week 18, but game_id stayed
    2026_01_NE_SEA → collided with the real Week-1 NE@SEA drafts).

    game_id's format is "{season}_{week:02d}_{away}_{home}" (4 tokens,
    the same shape _matchup_from_game_id parses). Team tokens are left
    as-is — only the season/week prefix moves. A malformed or non-string
    game_id is passed through untouched.
    """
    out = stub_week.assign(season=season, week=week)
    if "game_id" in out.columns:
        prefix = f"{season}_{week:02d}_"

        def _rebind_gid(gid):
            if not isinstance(gid, str):
                return gid
            parts = gid.split("_")
            if len(parts) != 4:
                return gid
            return prefix + parts[2] + "_" + parts[3]

        out["game_id"] = out["game_id"].map(_rebind_gid)
    return out


@app.route("/api/build-stub-week", methods=["POST"])
def build_stub_week_endpoint():
    """
    POST body: {"season": int, "week": int, "preview_only": bool
    (optional), "stub_csv": str (optional escape hatch)}.

    Builds one upcoming week's pre-game stub rows (build_stub_week ->
    run_pipeline against real trailing play-by-play, with skeleton rows
    injected for the not-yet-played week) and upserts them into the
    nfl_stub_weeks table via stub_store.write_stub_rows(). Phase A of the
    NFL weekly-automation plan: this replaces the local
    `python scripts/build_stub_week.py SEASON WEEK` + git commit + Vercel
    redeploy that a stub refresh used to require.

    AUTH: check_pipeline_secret() — same small-fixed-Make.com-trigger
    reasoning as /api/reconcile-week and /api/curate-and-write-drafts.

    SINGLE-SEASON SCOPED (historical_seasons=[season]) — identical
    reasoning to /api/reconcile-week's own scoping: add_rolling_windows
    groups by (player_id, season) / (defteam, position_group, season),
    so trailing windows already reset at the season boundary and a
    stub week never needs another season's play-by-play. Keeps the
    run_pipeline load to one season (~16-20s measured locally, well
    inside the function's maxDuration) instead of the 4-season default
    the CLI still uses.

    preview_only: build + shape the rows and report a small sample, but
    write nothing to the table.

    stub_csv: skip the build entirely and load the week from a CSV
    (a URL, or a filename under data/stub_weeks/ in the bundle) — used
    to seed / re-seed the table from a committed fixture without
    re-running the pipeline. _rebind_stub_frame() rewrites the season/
    week columns AND the {season}_{week} prefix of every game_id to the
    REQUEST BODY values, so the curated draft rows' event_id (= game_id)
    also lands in the target week and can't upsert onto a real
    production row for the fixture's original week. Team tokens /
    matchups still come from the source CSV — use the synthetic
    synthetic_smoke_test.csv fixture (fake season 2099, fake team
    codes) for anything that writes to production, never 2026_wk1.csv.
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

    preview_only = bool(data.get("preview_only"))
    stub_csv = data.get("stub_csv")

    print(f"[build-stub-week] season={season} week={week} preview_only={preview_only} "
          f"stub_csv={stub_csv!r} status=building", flush=True)
    try:
        if stub_csv:
            # Rebind season/week AND every game_id to the request body so
            # a fixture from one week can seed another without its curated
            # draft rows colliding onto real production rows via a stale
            # event_id (see _rebind_stub_frame). build_stub_week() already
            # produces correct keys, so this only applies on the stub_csv path.
            stub_week = _rebind_stub_frame(_load_stub_csv(stub_csv), season, week)
        else:
            stub_week = build_stub_week(season, week, historical_seasons=[season])
    except Exception as e:
        print(f"[build-stub-week] season={season} week={week} status=error error={e!r}", flush=True)
        return jsonify({"status": "error", "season": season, "week": week, "error": str(e)}), 500

    rows = _json_safe(shape_stub_rows(stub_week))
    row_count = len(rows)

    if preview_only:
        print(f"[build-stub-week] season={season} week={week} status=preview rows={row_count}", flush=True)
        return jsonify({
            "status": "preview",
            "season": season,
            "week": week,
            "preview_only": True,
            "row_count": row_count,
            "sample_rows": rows[:3],
        }), 200

    secret = os.environ.get("NFL_PIPELINE_WEBHOOK_SECRET")
    if not secret:
        return jsonify({"error": "NFL_PIPELINE_WEBHOOK_SECRET is not configured"}), 500

    write_result = write_stub_rows(rows, secret)
    print(
        f"[build-stub-week] season={season} week={week} rows={row_count} "
        f"forward_success={write_result['success']} forward_status={write_result['status_code']} "
        f"forward_error={truncate_for_log(write_result['error'], 500)!r} "
        f"forward_response_body={truncate_for_log(write_result.get('response_body'))!r}",
        flush=True,
    )
    if write_result["success"] is False:
        return jsonify({
            "status": "error",
            "season": season,
            "week": week,
            "row_count": row_count,
            "forwarded": False,
            "lovable_status_code": write_result["status_code"],
            "forward_error": write_result["error"],
        }), 502

    return jsonify({
        "status": "success",
        "season": season,
        "week": week,
        "row_count": row_count,
        "forwarded": True,
        "lovable_status_code": write_result["status_code"],
        "forward_response_body": write_result.get("response_body"),
    }), 200


@app.route("/api/build-stub-week", methods=["GET"])
def build_stub_week_health_check():
    return jsonify({
        "status": "ok",
        "usage": "POST {\"season\": int, \"week\": int, \"preview_only\": bool (optional), "
                 "\"stub_csv\": str (optional)}. Builds the given upcoming week's pre-game stub "
                 "rows (single-season run_pipeline with skeleton rows for the unplayed week) and "
                 "upserts them into nfl_stub_weeks. Replaces the old local build_stub_week.py + "
                 "git commit + redeploy.",
        "deployed_via": "github-auto-deploy",
    })


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
    (optional), "player_ids_to_write": [str, ...] (optional),
    "force": bool (optional)}.

    RE-RUN GUARD (Phase C): before running any curation, the endpoint
    reads existing nfl_content_drafts review states for (season, week)
    via read_content_draft_review_states(). If a human has already
    actioned any row (review_status != 'pending_review'), it returns
    409 {"status": "locked", "reviewed_rows": N, ...} and runs no
    curation at all — so the real Tasty Six LLM call is never made for a
    week already under review. `force: true` skips this pre-flight and
    proceeds. A structural DB trigger (20260902000000_guard_nfl_content_
    drafts_review_status.sql) is the backstop: even under force, or via
    a direct write-route call, an automated writer cannot revert a row's
    review_status from a non-pending value back to 'pending_review'.

    AUTH: check_pipeline_secret() — same mechanism /api/reconcile-week
    already uses for its own incoming trigger, same reasoning (a small,
    fixed, Make.com-only trigger with no attacker-shaped body worth
    HMAC-signing — see check_pipeline_secret's own docstring).

    DATA SOURCE: the nfl_stub_weeks table (Phase A of the NFL weekly-
    automation plan) via stub_store.stub_week_snapshot() — the real,
    live, pre-game scored data build_stub_week() produces for the CURRENT
    upcoming week (and Phase 2's odds poller updates). Replaces the old
    read of the committed nfl/data/stub_weeks/{season}_wk{week}.csv,
    which could only be refreshed by a git commit + Vercel redeploy. This
    is deliberately NOT nfl_player_redzone_weekly (the historical,
    already-reconciled table) — curation is a pre-game, upcoming-picks
    feature; the stub week is the only real source with live ATTD odds on
    it at all before a week goes final and gets reconciled away.

    stub_csv (optional): read the week from a CSV instead of the table
    (a URL, or a filename under data/stub_weeks/ in the bundle) — keeps
    the committed fixtures usable for a real preview_only test without
    seeding the table first.

    ALSO fetches nfl_data_py.import_pbp_data([season]) — a NEW load
    this endpoint didn't do before (confirmed directly: not reused from
    anywhere else already reachable here) — so WR/TE Trends' target_
    share/target_share_trend role_signal candidates are available via
    this live write path, matching what build_wr_trends/build_te_trends
    already had. Same try/except-and-degrade shape as `schedules`
    directly below: a real, EXPECTED failure mode is nflverse not
    having published pbp for the current season yet this early (a 404,
    not a fluke) — pbp=None in that case, and curation proceeds exactly
    as it did before this fix (those two candidates simply ineligible).
    Timing: ~2.5s for a full single season, measured locally against a
    completed season (2025) — a LOCAL number, not a Vercel benchmark
    (same caveat scripts/backfill_redzone.py's own docstring already
    gives itself for a comparable fetch); worth re-checking against a
    real deployed invocation if this ever looks slow in practice.

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
    stub_csv = data.get("stub_csv")
    force = bool(data.get("force"))

    # RE-RUN GUARD (Phase C) — pre-flight before any curation work.
    # If a human has already started reviewing this week's drafts (any
    # nfl_content_drafts row for (season, week) with review_status other
    # than 'pending_review'), a blind re-curate would upsert those rows
    # back to pending_review via the write route's default and drop
    # approved picks straight off the live board. Refuse with 409 and run
    # NO curation — this short-circuits before curate_nfl_shelves(), so
    # the real Tasty Six LLM call is never made. `force: true` is the
    # explicit operator override (re-runs anyway; draft content is
    # refreshed, but the DB trigger still protects each row's review
    # decision — see 20260902000000_guard_nfl_content_drafts_review_
    # status.sql). A pre-flight READ failure fails closed for the same
    # reason: if review state can't be verified, don't risk clobbering it.
    if not force:
        preflight_secret = os.environ.get("NFL_PIPELINE_WEBHOOK_SECRET")
        if not preflight_secret:
            return jsonify({"error": "NFL_PIPELINE_WEBHOOK_SECRET is not configured"}), 500
        preflight = read_content_draft_review_states(season, week, preflight_secret)
        if not preflight["ok"]:
            print(f"[curate-and-write-drafts] season={season} week={week} preflight_failed "
                  f"status={preflight['status_code']} error={preflight['error']!r}", flush=True)
            return jsonify({
                "status": "preflight_failed",
                "season": season,
                "week": week,
                "error": f"Could not verify existing review state: {preflight['error']}",
                "hint": "retry, or pass force:true to skip the pre-flight check",
            }), 502
        if preflight["reviewed_count"] > 0:
            print(f"[curate-and-write-drafts] season={season} week={week} status=locked "
                  f"reviewed_rows={preflight['reviewed_count']} (curation not run)", flush=True)
            return jsonify({
                "status": "locked",
                "season": season,
                "week": week,
                "reviewed_rows": preflight["reviewed_count"],
                "hint": "pass force:true to overwrite",
            }), 409

    # DATA SOURCE: the nfl_stub_weeks table (Phase A of the NFL weekly-
    # automation plan) — stub_week_snapshot() reads one week's rows back
    # and reconstitutes build_stub_week()'s original frame (typed columns
    # + `extra` unpacked, reconciled rows dropped). Replaces the old
    # pd.read_csv() of the committed data/stub_weeks/{season}_wk{week}.csv,
    # which could only be refreshed by a git commit + redeploy.
    #
    # stub_csv (optional): read the week from a CSV instead — a URL, or a
    # filename under data/stub_weeks/ in the bundle. Keeps the committed
    # fixtures (2026_wk1.csv, …) usable for a real end-to-end preview_only
    # test without seeding the table first.
    #
    # A read transport failure raises inside stub_week_snapshot and is
    # caught by the outer curate try/except as a 500; an empty frame
    # (build_stub_week hasn't run for this week, or every row was
    # reconciled) is the 404 below — same split the old `.exists()` +
    # read gave, just sourced from the table.
    try:
        if stub_csv:
            weekly = _load_stub_csv(stub_csv)
        else:
            secret_for_read = os.environ.get("NFL_PIPELINE_WEBHOOK_SECRET")
            if not secret_for_read:
                return jsonify({"error": "NFL_PIPELINE_WEBHOOK_SECRET is not configured"}), 500
            weekly = stub_week_snapshot(season, week, secret_for_read)
    except Exception as e:
        print(f"[curate-and-write-drafts] season={season} week={week} stub_read_failed error={e!r}", flush=True)
        return jsonify({"status": "error", "season": season, "week": week, "error": str(e)}), 500

    if len(weekly) == 0:
        return jsonify({
            "error": f"No stub rows for season={season} week={week} in nfl_stub_weeks — "
                     f"/api/build-stub-week hasn't run for this week yet.",
        }), 404

    try:
        schedules = nfl.import_schedules([season])
    except Exception as e:
        print(f"[curate-and-write-drafts] season={season} week={week} schedules_fetch_failed error={e!r}", flush=True)
        schedules = None  # kickoff_utc stays None for every row -- honest gap, not a hard failure

    # WR/TE Trends' target_share/target_share_trend role_signal candidates
    # (curate_home_shelves.shape_content_draft_rows -> add_whole_game_
    # target_share_trend) -- same "fetch, catch, degrade to None" shape as
    # schedules directly above, not a hard failure. CONFIRMED, not assumed:
    # nfl_data_py.import_pbp_data([season]) raises a real 404 for a season
    # nflverse hasn't published pbp for yet (e.g. the current season before
    # enough of it has been played/ingested upstream) -- this is the
    # EXPECTED path early in a season, not a rare edge case; pbp=None here
    # means those two candidates are simply ineligible, same graceful
    # degradation as every other missing-input case in this module.
    try:
        pbp = nfl.import_pbp_data([season], downcast=True)
    except Exception as e:
        print(f"[curate-and-write-drafts] season={season} week={week} pbp_fetch_failed error={e!r}", flush=True)
        pbp = None

    anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY")

    try:
        result = curate_nfl_shelves(
            weekly, season, week, schedules=schedules, anthropic_api_key=anthropic_api_key, pbp=pbp,
        )
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
                 "\"player_ids_to_write\": [str, ...] (optional), \"force\": bool (optional)}. "
                 "Curates the given week's real stub-week data (home-shelf assignment, Tasty Six, "
                 "real content — including a real Claude call for Tasty Six rows) and writes the "
                 "result to nfl_content_drafts. Returns 409 status=locked (no curation run) if any "
                 "row for the week has already been reviewed; pass force:true to override. "
                 "max_rows_to_write/player_ids_to_write scope the actual write only; curation always "
                 "runs against the full real pool.",
        "deployed_via": "github-auto-deploy",
    })


@app.route("/api/write-intelligence", methods=["POST"])
def write_intelligence_endpoint():
    """
    POST body: {"family": str, "season": int, "week": int, "stories":
    [...] (real 13-field story dicts — see intelligence_schema.py),
    "prior_history": [[[family, entity_key, signal_name], {...}], ...]
    (optional, default [] — a JSON-safe encoding of apply_lifecycle's
    own {tuple: dict} history shape, since JSON has no tuple-keyed
    object; reconstructed into real tuple keys below),
    "lifecycle_eligible": bool (optional, default true — pass false for
    Market Intelligence stories, per the approved deferral),
    "preview_only": bool (optional)}.

    AUTH: check_pipeline_secret() — same small-fixed-trigger reasoning
    as /api/curate-and-write-drafts.

    EXISTS SPECIFICALLY so NFL_PIPELINE_WEBHOOK_SECRET (a Vercel
    "Sensitive" env var, confirmed write-only outside a running deployed
    function — see /api/curate-and-write-drafts's own docstring on this
    exact constraint) can actually be exercised for a real single-row/
    small-batch write test, the same reason that endpoint had to exist
    as a real deployment rather than being tested locally.

    Does NOT fetch or curate anything itself — this is the write-
    connection's own wiring (intelligence_write.process_family +
    write_intelligence_rows), not a curation trigger. Real story dicts
    are built LOCALLY (from real historical data, via each family's own
    already-tested build_*_stories()) and sent in as this request's
    `stories`, exactly the same "curate for real first, then submit a
    controlled real subset to the deployed write path" pattern the
    Picks write-connection task established (there via preview_only +
    player_ids_to_write against curate_nfl_shelves' own internal data
    source; here via directly supplying the already-built real story
    dicts, since there's no equivalent single internal data source this
    endpoint could fetch on its own — Market Intelligence needs a live
    odds snapshot, the other three families need real reconciled
    historical data, on two different real cadences).

    KNOWN GAP, not fixed here: prior_history has no real read-back
    source yet (nfl_intelligence_story_history has no confirmed read
    route, unlike nfl_shelf_signal_history's read route for stickiness)
    — every real call through this endpoint today necessarily runs with
    whatever prior_history the caller supplies (empty, for a genuinely
    first real test). A live multi-week production run needs that read
    connection built as a real follow-up; this endpoint's own job (the
    write half) is unaffected by when that happens.
    """
    auth_error = check_pipeline_secret()
    if auth_error:
        return auth_error

    data = request.get_json(force=True, silent=True) or {}
    family = data.get("family")
    try:
        season = int(data.get("season"))
        week = int(data.get("week"))
    except (TypeError, ValueError):
        return jsonify({"error": "Expected {\"family\": str, \"season\": int, \"week\": int, \"stories\": [...]} in the request body."}), 400
    stories = data.get("stories") or []
    if not family or not isinstance(stories, list):
        return jsonify({"error": "Expected {\"family\": str, \"season\": int, \"week\": int, \"stories\": [...]} in the request body."}), 400

    prior_history = {}
    for pair in (data.get("prior_history") or []):
        key, value = pair
        prior_history[tuple(key)] = value

    lifecycle_eligible = data.get("lifecycle_eligible", True)
    preview_only = bool(data.get("preview_only"))

    try:
        result = process_family(family, stories, prior_history, season, week, lifecycle_eligible=bool(lifecycle_eligible))
    except Exception as e:
        print(f"[write-intelligence] family={family} season={season} week={week} status=error error={e!r}", flush=True)
        return jsonify({"status": "error", "family": family, "season": season, "week": week, "error": str(e)}), 500

    story_rows = _json_safe(result["story_rows"])
    history_rows = _json_safe(result["history_rows"])

    forward_result = {"success": None, "status_code": None, "error": None}
    if not preview_only:
        secret = os.environ.get("NFL_PIPELINE_WEBHOOK_SECRET")
        if not secret:
            return jsonify({"error": "NFL_PIPELINE_WEBHOOK_SECRET is not configured"}), 500
        forward_result = write_intelligence_rows(story_rows, history_rows, secret)

    print(
        f"[write-intelligence] family={family} season={season} week={week} "
        f"stories_in={len(stories)} story_rows={len(story_rows)} history_rows={len(history_rows)} "
        f"sanity_failures={sum(1 for r in story_rows if not r['sanity_check_passed'])} "
        f"forward_success={forward_result['success']} forward_status={forward_result['status_code']} "
        f"forward_error={truncate_for_log(forward_result['error'], 500)!r} "
        f"forward_response_body={truncate_for_log(forward_result.get('response_body'))!r}",
        flush=True,
    )

    return jsonify({
        "family": family,
        "season": season,
        "week": week,
        "preview_only": preview_only,
        "story_rows": story_rows,
        "history_rows": history_rows,
        "forwarded": forward_result["success"],
        "lovable_status_code": forward_result["status_code"],
        "forward_error": forward_result["error"],
        "forward_response_body": forward_result.get("response_body"),
    }), (502 if forward_result["success"] is False else 200)


@app.route("/api/write-intelligence", methods=["GET"])
def write_intelligence_health_check():
    return jsonify({
        "status": "ok",
        "usage": "POST {\"family\": str, \"season\": int, \"week\": int, \"stories\": [...], "
                 "\"prior_history\": [[[family, entity_key, signal_name], {...}], ...] (optional), "
                 "\"lifecycle_eligible\": bool (optional, default true), \"preview_only\": bool (optional)}. "
                 "Sanity-checks each story, applies lifecycle (unless lifecycle_eligible=false, for Market "
                 "Intelligence), and writes both story rows and history rows to nfl_intelligence_stories / "
                 "nfl_intelligence_story_history via one combined signed call.",
        "deployed_via": "github-auto-deploy",
    })


@app.route("/api/generate-and-write-intelligence", methods=["POST"])
def generate_and_write_intelligence_endpoint():
    """
    Phase 3 of the live-wiring project: the real, family-agnostic
    generation endpoint. Unlike /api/write-intelligence (which requires
    the caller to already have real story dicts in hand), this endpoint
    fetches each family's real input itself, builds real stories, reads
    Phase 2's real prior_history back, and writes — nothing has ever run
    this on a live cadence before; every existing caller anywhere in this
    codebase is a test file.

    POST body: {"season": int, "week": int, "families": [str, ...]
    (optional, default every family in intelligence_generate.FAMILIES —
    role_changes, defensive_trends, market_intelligence; Coaching Trends
    is never included, see that module's own docstring), "preview_only":
    bool (optional)}.

    AUTH: check_pipeline_secret() — same small-fixed-Make.com-trigger
    reasoning as /api/curate-and-write-drafts and /api/write-intelligence.

    preview_only: real generation still runs in full for every requested
    family (including a real prior_history read-back for lifecycle-
    eligible families) — nothing is written to Lovable. Same semantics
    /api/curate-and-write-drafts already established, and the concrete
    tool this endpoint's own local test suite uses to prove the real
    round trip without a live secret.

    Does NOT write anything to nfl_intelligence_story_history/
    nfl_intelligence_stories in preview mode, and does not run Coaching
    Trends (still deferred — no persisted read-back exists yet for its
    primary pbp input, a genuinely different, larger problem).
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

    families = data.get("families")
    if families is not None:
        if not isinstance(families, list) or not all(isinstance(f, str) for f in families):
            return jsonify({"error": "\"families\" must be a list of strings if provided."}), 400
        unknown = [f for f in families if f not in FAMILIES]
        if unknown:
            return jsonify({"error": f"Unknown families: {unknown}. Expected one of {sorted(FAMILIES)}."}), 400

    preview_only = bool(data.get("preview_only"))

    secret = os.environ.get("NFL_PIPELINE_WEBHOOK_SECRET")
    if not secret:
        return jsonify({"error": "NFL_PIPELINE_WEBHOOK_SECRET is not configured"}), 500

    try:
        result = generate_and_write_intelligence(season, week, secret, families=families, preview_only=preview_only)
    except Exception as e:
        print(f"[generate-and-write-intelligence] season={season} week={week} status=error error={e!r}", flush=True)
        return jsonify({"status": "error", "season": season, "week": week, "error": str(e)}), 500

    result = _json_safe(result)

    print(
        f"[generate-and-write-intelligence] season={season} week={week} "
        f"families={list(result['families'])} "
        f"story_rows_generated={result['story_rows_generated']} history_rows_generated={result['history_rows_generated']} "
        f"story_rows_written={result['story_rows_written']} history_rows_written={result['history_rows_written']} "
        f"forward_success={result['forwarded']} forward_status={result['lovable_status_code']} "
        f"forward_error={truncate_for_log(result['forward_error'], 500)!r}",
        flush=True,
    )

    return jsonify(result), (502 if result["forwarded"] is False else 200)


@app.route("/api/generate-and-write-intelligence", methods=["GET"])
def generate_and_write_intelligence_health_check():
    return jsonify({
        "status": "ok",
        "usage": "POST {\"season\": int, \"week\": int, \"families\": [str, ...] (optional, default all of "
                 f"{sorted(FAMILIES)}), \"preview_only\": bool (optional)}}. "
                 "Fetches each family's real input, builds real stories, reads real prior lifecycle state back "
                 "(Phase 2), sanity-checks + applies lifecycle + shapes rows (Phase 2/existing), and writes "
                 "everything in one combined signed call to nfl_intelligence_stories / nfl_intelligence_story_history.",
        "deployed_via": "github-auto-deploy",
    })
