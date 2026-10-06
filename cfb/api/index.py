"""
HTTP entry point for Vercel — CFB v1 shared red-zone ingestion.

A manually-triggered endpoint, mirroring how NFL Intelligence's
/api/generate-and-write-intelligence was built and tested before any
Make.com wiring existed (spec §8: "formal scheduling is a later phase").
No cron, no Make.com scenario in v1 — a human (or a one-off curl) POSTs
{season, week} and this runs the whole ingest.

Deliberately a SEPARATE Vercel deployment from nfl/api/index.py and
pipeline/api/index.py — cfb/ is a self-contained domain (its own venv,
its own requirements.txt, "duplicate rather than cross-import" as the
stated rule everywhere else in this codebase). check_pipeline_secret()
(this file) and the signed-webhook forwarder (cfb/api/lovable_forward.py)
are COPIED from the NFL deployment, not imported.

POST /api/ingest-and-write-redzone
    body: {"season": int, "week": int,
           "season_type": str (optional, default "regular"),
           "preview_only": bool (optional)}
    auth: X-Pipeline-Secret header == PIPELINE_INCOMING_SECRET

    1. GET /games?year=&week=&classification=fbs
    2. per completed game: GET /plays/stats?gameId=   (cap-safe)
    3. resolve athleteId -> position via /roster (season-cached)
    4. aggregate -> cfb_player_redzone_weekly rows  (TD Opportunity, §2)
                 -> cfb_defense_redzone_allowed_weekly rows  (Situation, §3)
    5. unless preview_only: one HMAC-signed POST per aggregation to its
       Lovable write route (CFB_PIPELINE_WEBHOOK_SECRET).

Returns the full diagnostics bundle from both aggregations plus each
forward's echo — the only way to verify a real run (both tables are
service-role-only, no public read path), same as the NFL redzone write
route's own .select()-after-.upsert() echo.
"""
import hmac
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Vercel's Python runtime doesn't put this file's own directory (or its
# parent) on the import path — same root cause + fix nfl/api/index.py
# already documents for its own sibling imports.
sys.path.insert(0, str(Path(__file__).resolve().parent))          # cfb/api/  -> lovable_forward
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # cfb/      -> ids, plays_stats, roster, redzone

import requests
from flask import Flask, jsonify, request

from curate_cfb_shelves import (
    UpcomingWeekConflict,
    assign_cfb_shelves,
    cfb_defense_redzone_allowed_weekly_snapshot,
    cfb_player_receiving_weekly_snapshot,
    cfb_player_redzone_weekly_snapshot,
    cfb_player_role_weekly_snapshot,
    curate_cfb_shelves,
    curate_cfb_upcoming_week,
    shape_cfb_shelf_placement_rows,
    write_cfb_player_shelf_scores,
)
from ids import CFBDError, fbs_team_ids, fetch_ap_top25, team_conference_map
from lovable_forward import forward_to_lovable, resolve_url_env, truncate_for_log
from poll_cfb_attd_odds import (
    EXPECTED_INPUT_ERROR as ATTD_EXPECTED_INPUT_ERROR,
    forward_rows_in_chunks,
    normalize_events_input,
    process_events,
    utc_now_iso,
)
from plays_stats import (
    completed_games,
    estimate_week_cost,
    fetch_games,
    fetch_scoring_td_play_ids,
    fetch_week_play_stats,
)
from redzone import aggregate_redzone_allowed_cfb, aggregate_redzone_game_cfb
from roster import raw_position_lookup

app = Flask(__name__)

# Fallbacks only — the real values should come from Vercel env vars so a
# future URL change is a config update, not a redeploy. These routes exist
# on the Lovable side as part of this same build (see
# tastypickems/src/routes/api/public/cfb-*-write.ts); until the matching
# CFB_PIPELINE_WEBHOOK_SECRET is set on both sides a forward will 401
# harmlessly (not silently succeed).
DEFAULT_PLAYER_WRITE_URL = "https://tastypickems.lovable.app/api/public/cfb-player-redzone-weekly-write"
DEFAULT_DEFENSE_WRITE_URL = "https://tastypickems.lovable.app/api/public/cfb-defense-redzone-allowed-weekly-write"

PLAYER_WRITE_URL_ENV = "LOVABLE_CFB_PLAYER_REDZONE_WEEKLY_WRITE_URL"
DEFENSE_WRITE_URL_ENV = "LOVABLE_CFB_DEFENSE_REDZONE_ALLOWED_WEEKLY_WRITE_URL"
WEBHOOK_SECRET_ENV = "CFB_PIPELINE_WEBHOOK_SECRET"


def check_pipeline_secret():
    """
    Copied from nfl/api/index.py's function of the same name. Same
    reasoning: a small, fixed, human/Make.com-only trigger with no
    attacker-shaped body worth signing — a constant-time shared-secret
    header check is the right amount of protection here (the outbound
    HMAC X-Signature on the Lovable write is the genuinely-variable
    attacker-shaped body).

    PIPELINE_INCOMING_SECRET is configured independently in THIS Vercel
    project's own env vars — a separate deployment doesn't inherit
    another project's value. Reuse the same string across projects or
    not; either way it must be set here too.
    """
    expected = os.environ.get("PIPELINE_INCOMING_SECRET")
    if not expected:
        return jsonify({"error": "PIPELINE_INCOMING_SECRET is not configured"}), 500
    provided = request.headers.get("X-Pipeline-Secret")
    if not provided or not hmac.compare_digest(provided, expected):
        return jsonify({"error": "Missing or invalid X-Pipeline-Secret header"}), 401
    return None


def _resolve_secret():
    secret = os.environ.get(WEBHOOK_SECRET_ENV)
    if not secret:
        return None, (jsonify({"error": f"{WEBHOOK_SECRET_ENV} is not configured"}), 500)
    if secret != secret.strip():
        # truthy-but-whitespace-padded value — logs length + a flag only,
        # never the value. Same guard nfl/api/index.py applies.
        print(
            f"[env-config] WARNING: {WEBHOOK_SECRET_ENV} has leading/trailing whitespace "
            f"(len={len(secret)}, stripped_len={len(secret.strip())}) — this will produce "
            f"a signature Lovable's side won't match.",
            flush=True,
        )
    return secret, None


def _forward(rows, secret, url_env, default_url):
    """One signed POST of `rows` (JSON-safe list of dicts) to a write
    route. Returns forward_to_lovable's result dict; a no-op success when
    there are zero rows."""
    if not rows:
        return {"success": None, "status_code": None, "error": None, "response_body": None, "rows": 0}
    url = resolve_url_env(url_env, default_url)
    result = forward_to_lovable(rows, secret, url)
    result["rows"] = len(rows)
    result["url"] = url
    return result


@app.route("/api/ingest-and-write-redzone", methods=["POST"])
def ingest_and_write_redzone_endpoint():
    auth_error = check_pipeline_secret()
    if auth_error:
        return auth_error

    data = request.get_json(force=True, silent=True) or {}
    try:
        season = int(data.get("season"))
        week = int(data.get("week"))
    except (TypeError, ValueError):
        return jsonify({"error": "Expected {\"season\": int, \"week\": int} in the request body."}), 400
    season_type = str(data.get("season_type") or "regular")
    preview_only = bool(data.get("preview_only"))
    dry_run = bool(data.get("dry_run"))
    # full_rows: with preview_only, echo EVERY aggregated row (not just the
    # 3-row sample) so an offline scoring sanity-check can assemble a real
    # multi-week frame. Only meaningful alongside preview_only.
    full_rows = bool(data.get("full_rows")) and preview_only

    started = datetime.now(timezone.utc).isoformat()
    clock = time.monotonic
    timing: dict = {}

    try:
        t = clock()
        games = fetch_games(season, week, season_type=season_type)
        completed = completed_games(games)
        timing["games_s"] = round(clock() - t, 2)

        if dry_run:
            est = estimate_week_cost(len(completed))
            return jsonify({
                "status": "ok", "dry_run": True, "season": season, "week": week,
                "games_total": len(games), "games_completed": len(completed),
                "estimate": est, "games_call_s": timing["games_s"],
            }), 200

        t = clock()
        play_stats, fetch_diag = fetch_week_play_stats(completed, season_type=season_type)
        timing["play_stats_s"] = round(clock() - t, 2)

        schools = sorted(
            {g.get("homeTeam") for g in games if g.get("homeTeam")}
            | {g.get("awayTeam") for g in games if g.get("awayTeam")}
        )
        t = clock()
        raw_pos = raw_position_lookup(season, fallback_teams=schools)
        timing["roster_s"] = round(clock() - t, 2)

        completed_ids = {int(g["id"]) for g in completed if g.get("id") is not None}
        t = clock()
        td_play_ids, td_diag = fetch_scoring_td_play_ids(
            season, week, completed_game_ids=completed_ids, season_type=season_type,
        )
        timing["td_plays_s"] = round(clock() - t, 2)

        t = clock()
        player_rows, player_diag = aggregate_redzone_game_cfb(
            play_stats, games, raw_pos, td_play_ids, season=season, week=week
        )
        defense_rows, defense_diag = aggregate_redzone_allowed_cfb(
            play_stats, games, raw_pos, td_play_ids, season=season, week=week
        )
        timing["aggregation_s"] = round(clock() - t, 2)
    except CFBDError as e:
        print(f"[ingest-and-write-redzone] season={season} week={week} status=cfbd_error error={e!r}", flush=True)
        return jsonify({"status": "error", "stage": "cfbd", "season": season, "week": week, "error": str(e)}), 502
    except Exception as e:  # noqa: BLE001
        print(f"[ingest-and-write-redzone] season={season} week={week} status=error error={e!r}", flush=True)
        return jsonify({"status": "error", "season": season, "week": week, "error": str(e)}), 500

    # JSON-safe already (aggregations emit plain ints / str / None / nested
    # dicts), but round-trip through json to be certain nothing numpy-typed
    # slipped into an `extra` object.
    player_rows = json.loads(json.dumps(player_rows, default=str))
    defense_rows = json.loads(json.dumps(defense_rows, default=str))

    forwards = {"player": {"skipped": "preview_only"}, "defense": {"skipped": "preview_only"}}
    if not preview_only:
        secret, secret_error = _resolve_secret()
        if secret_error:
            return secret_error
        t = clock()
        forwards["player"] = _forward(player_rows, secret, PLAYER_WRITE_URL_ENV, DEFAULT_PLAYER_WRITE_URL)
        forwards["defense"] = _forward(defense_rows, secret, DEFENSE_WRITE_URL_ENV, DEFAULT_DEFENSE_WRITE_URL)
        timing["forward_s"] = round(clock() - t, 2)

    timing["total_s"] = round(sum(v for v in timing.values()), 2)
    est = estimate_week_cost(len(completed), workers=fetch_diag.get("workers", 8))

    result = {
        "status": "ok",
        "started_at": started,
        "season": season,
        "week": week,
        "season_type": season_type,
        "preview_only": preview_only,
        "games_total": len(games),
        "games_completed": len(completed),
        "roster_positions_resolved": len(raw_pos),
        "timing": timing,
        "cost_estimate": est,
        "fetch": fetch_diag,
        "td_plays": td_diag,
        "player_rows": len(player_rows),
        "defense_rows": len(defense_rows),
        "player_diagnostics": player_diag,
        "defense_diagnostics": defense_diag,
        "forwards": forwards,
        "player_sample": player_rows[:3],
        "defense_sample": defense_rows[:3],
    }
    if full_rows:
        result["player_rows_full"] = player_rows
        result["defense_rows_full"] = defense_rows

    p_fwd, d_fwd = forwards["player"], forwards["defense"]
    print(
        f"[ingest-and-write-redzone] season={season} week={week} season_type={season_type} "
        f"preview_only={preview_only} games_completed={len(completed)} "
        f"play_stat_rows={fetch_diag['play_stat_rows_total']} "
        f"player_rows={len(player_rows)} defense_rows={len(defense_rows)} "
        f"td_unmatched={player_diag.get('td_attribution', {}).get('unmatched')} "
        f"timing={timing} "
        f"player_forward={p_fwd.get('success')}/{p_fwd.get('status_code')} "
        f"defense_forward={d_fwd.get('success')}/{d_fwd.get('status_code')} "
        f"player_forward_body={truncate_for_log(p_fwd.get('response_body'), 500)!r} "
        f"defense_forward_body={truncate_for_log(d_fwd.get('response_body'), 500)!r}",
        flush=True,
    )

    forward_failed = any(
        forwards[k].get("success") is False for k in ("player", "defense")
    )
    return jsonify(result), (502 if forward_failed else 200)


@app.route("/api/ingest-and-write-redzone", methods=["GET"])
def ingest_and_write_redzone_health_check():
    return jsonify(
        {
            "status": "ok",
            "usage": (
                "POST {\"season\": int, \"week\": int, \"season_type\": str "
                "(optional, default \"regular\"), \"preview_only\": bool (optional), "
                "\"dry_run\": bool (optional — returns only the CFBD call-count / "
                "wall-clock estimate for the week, no ingest)}. "
                "Auth: X-Pipeline-Secret header. Pulls CFBD /games, one "
                "/plays?playType=TD, one /roster, and /plays/stats per completed "
                "game (fetched concurrently); aggregates red-zone band touch/TD "
                "counts per player (cfb_player_redzone_weekly) and per "
                "defense+position (cfb_defense_redzone_allowed_weekly), and "
                "forwards each to its Lovable write route in one HMAC-signed POST. "
                "The response carries `timing` and `cost_estimate` blocks."
            ),
            "scope": "TD Opportunity (§2) + Situation defensive-matchup (§3) raw weekly counts only. "
                     "No scoring, no rolling windows, no Environment/Role/Evidence/Market Value.",
        }
    )


# ===========================================================================
# Track B (2026-09) — curation/orchestration + read endpoint. See cfb/api/
# curate_cfb_shelves.py's own module docstring for the three real gaps this
# depends on (the two raw tables + the RPC this route reads didn't exist in
# Supabase before this task's migration; cfb_player_role_weekly has no
# ingestion endpoint yet; the Lovable-side read/write routes these env vars
# point at don't exist yet either — all confirmed 2026-09-04, all flagged in
# the investigation report, none of them fixed by writing this file).
# ===========================================================================
@app.route("/api/curate-and-write-cfb-shelves", methods=["POST"])
def curate_and_write_cfb_shelves_endpoint():
    """
    POST body: {"season": int, "week": int, "preview_only": bool (optional)}
    auth: X-Pipeline-Secret header == PIPELINE_INCOMING_SECRET (same
    mechanism /api/ingest-and-write-redzone already uses — a small, fixed,
    human/Make.com-only trigger, not a genuinely attacker-shaped body).

    Reads the whole season back from the four raw ingestion tables
    (season-scoped signed reads — see curate_cfb_shelves.py), runs the
    real scoring chain, assigns the 8 real shelves (assign_cfb_shelves),
    resolves each shelf placement's own story archetype (story_
    archetype.resolve_cfb_archetype, via shape_cfb_shelf_placement_rows),
    and forwards those placement rows to cfb_player_shelf_scores — unless
    preview_only, same convention ingest-and-write-redzone already uses.
    Reuses CFB_PIPELINE_WEBHOOK_SECRET for BOTH the reads and the write,
    same as NFL's own reconcile_week.py reuses NFL_PIPELINE_WEBHOOK_
    SECRET for both nfl-price-history-read and nfl-player-redzone-
    weekly-write (confirmed: identical env var both directions on that
    side).

    STORY ARCHETYPE RESOLVER WIRING (this task) — a real behavior change
    from this endpoint's prior form, flagged explicitly: this used to
    forward `shelf_score_rows` (curate_cfb_shelves()'s own one-row-per-
    scored-player output — the WHOLE population, `shelf` always null,
    Target Magnets/8-shelf assignment never actually run here despite
    existing in curate_cfb_shelves.py since Phase 5). Investigation
    before this task confirmed assign_cfb_shelves/add_shelf_convergence/
    resolve_cfb_archetype had NEVER been called from this endpoint —
    only exercised locally via cfb/scripts/shelf_sanity.py. This endpoint
    now forwards shape_cfb_shelf_placement_rows' output instead: ONLY
    players who made at least one of the 8 real shelves get written, and
    a player on N shelves gets N rows — one per shelf, each with that
    shelf's own real (possibly different) resolved archetype. See
    shape_cfb_shelf_placement_rows' and story_archetype.resolve_cfb_
    archetype's own docstrings for why. This requires the companion
    migration that changes cfb_player_shelf_scores' unique constraint
    from (player_id, season, week) to (player_id, season, week, shelf)
    and the matching tastypickems write-route change (onConflict) — see
    that migration's own file for both.

    Every real per-request external call (fbs_team_ids, the four signed
    reads, fetch_ap_top25, team_conference_map, the signed write) is
    wrapped so a real failure returns a diagnostic 502/500 with `stage`,
    not a bare Flask 500 — same shape ingest-and-write-redzone's own
    CFBDError/Exception handling already uses.

    UPCOMING-WEEK MODE (2026-10): body {"upcoming": true} scores a week
    that has NOT been played yet. The normal mode can't: the raw tables
    only ever hold completed games, so an unplayed week has zero rows at
    (season, week) and the normal mode returns zero placements. Upcoming
    mode fetches that week's CFBD /games (one extra call), keeps the
    games that are not completed and are FBS-vs-FBS, builds in-memory
    skeleton rows for every player who already has a real row this season
    on a team that is playing (plus a defense skeleton per playing team x
    RB/WR/TE), concatenates them onto the real season frame, and runs the
    exact same scoring/shelf/archetype/placement path as the normal mode
    -- see curate_cfb_shelves.curate_cfb_upcoming_week for the mechanism
    and why nothing is ever persisted to the raw tables. Placement rows
    additionally carry kickoff_utc (omitted, not null, when CFBD marks
    the start time TBD). Extra guards: season_type must be "regular"
    (400), and a target week that already has real rows is refused (409)
    rather than silently mixed. With upcoming absent/false this endpoint
    behaves exactly as before.
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
    upcoming = bool(data.get("upcoming"))
    if upcoming:
        season_type = str(data.get("season_type") or "regular")
        if season_type != "regular":
            return jsonify({
                "status": "error", "stage": "validate", "season": season, "week": week,
                "error": (
                    f"upcoming mode supports season_type \"regular\" only (got {season_type!r}); "
                    f"postseason weeks are out of scope -- the raw tables key on (player_id, season, week) "
                    f"with no season-type column, so a bowl week would collide with regular-season week {week}."
                ),
            }), 400

    secret, secret_error = _resolve_secret()
    if secret_error:
        return secret_error

    try:
        ids = fbs_team_ids(season)
    except CFBDError as e:
        print(f"[curate-and-write-cfb-shelves] season={season} week={week} stage=fbs_ids error={e!r}", flush=True)
        return jsonify({"status": "error", "stage": "fbs_ids", "season": season, "week": week, "error": str(e)}), 502

    try:
        ap_ranks = fetch_ap_top25(season, week)
        team_conference = team_conference_map(season)
    except CFBDError as e:
        print(f"[curate-and-write-cfb-shelves] season={season} week={week} stage=shelf_lookups error={e!r}", flush=True)
        return jsonify({"status": "error", "stage": "shelf_lookups", "season": season, "week": week, "error": str(e)}), 502

    player_weekly = cfb_player_redzone_weekly_snapshot(season, secret)
    allowed_weekly = cfb_defense_redzone_allowed_weekly_snapshot(season, secret)
    role_weekly = cfb_player_role_weekly_snapshot(season, secret)
    receiving_weekly = cfb_player_receiving_weekly_snapshot(season, secret)

    if len(player_weekly) == 0:
        return jsonify({
            "status": "error", "stage": "read", "season": season, "week": week,
            "error": (
                "No cfb_player_redzone_weekly rows for this season — either ingestion hasn't "
                "run yet (POST /api/ingest-and-write-redzone), or the Lovable read route this "
                "endpoint calls doesn't exist yet. Not a scoring bug."
            ),
        }), 404

    upcoming_diag = None
    kickoff_by_game_id = None
    if upcoming:
        # One extra CFBD call versus the normal mode: that week's /games,
        # unplayed games included (fetch_games never filters on
        # `completed`; curate_cfb_upcoming_week does, the other way round).
        try:
            games = fetch_games(season, week, season_type="regular")
        except CFBDError as e:
            print(f"[curate-and-write-cfb-shelves] season={season} week={week} stage=games error={e!r}", flush=True)
            return jsonify({"status": "error", "stage": "games", "season": season, "week": week, "error": str(e)}), 502
        try:
            result = curate_cfb_upcoming_week(
                player_weekly, allowed_weekly, role_weekly, season, week, ids, games,
                receiving_weekly=receiving_weekly,
            )
        except UpcomingWeekConflict as e:
            print(f"[curate-and-write-cfb-shelves] season={season} week={week} stage=upcoming_conflict error={e!r}", flush=True)
            return jsonify({"status": "error", "stage": "upcoming_conflict", "season": season, "week": week, "error": str(e)}), 409
        upcoming_diag = result["upcoming"]
        kickoff_by_game_id = result["kickoff_by_game_id"]
    else:
        result = curate_cfb_shelves(
            player_weekly, allowed_weekly, role_weekly, season, week, ids, receiving_weekly=receiving_weekly,
        )
    week_rows = result["week_rows"]
    shelves = assign_cfb_shelves(week_rows, ap_ranks, team_conference)
    if upcoming:
        placement_rows = shape_cfb_shelf_placement_rows(shelves, kickoff_by_game_id=kickoff_by_game_id)
    else:
        placement_rows = shape_cfb_shelf_placement_rows(shelves)

    forward = {"skipped": "preview_only"}
    if not preview_only:
        forward = _forward(placement_rows, secret, "LOVABLE_CFB_PLAYER_SHELF_SCORES_WRITE_URL",
                            "https://tastypickems.lovable.app/api/public/cfb-player-shelf-scores-write")

    shelf_counts = {name: len(shelves.get(name, [])) for name in shelves}
    response = {
        "status": "ok",
        "season": season,
        "week": week,
        "preview_only": preview_only,
        "players_read": len(player_weekly),
        "defense_rows_read": len(allowed_weekly),
        "role_rows_read": len(role_weekly),
        "role_momentum_available": bool(len(role_weekly)),
        "receiving_rows_read": len(receiving_weekly),
        "target_magnets_available": bool(len(receiving_weekly)),
        "week_rows": len(week_rows),
        "shelf_counts": shelf_counts,
        "placement_rows": len(placement_rows),
        "forward": forward,
        "sample": placement_rows[:3],
    }
    if upcoming:
        response.update({
            "mode": "upcoming",
            "games_in_week": upcoming_diag["games_in_week"],
            "games_skipped_completed": upcoming_diag["games_skipped_completed"],
            "games_skipped_non_fbs": upcoming_diag["games_skipped_non_fbs"],
            "games_qualifying": upcoming_diag["games_qualifying"],
            "player_skeletons": upcoming_diag["player_skeletons"],
            "defense_skeletons": upcoming_diag["defense_skeletons"],
            # /teams/fbs (fbs_team_ids) + /rankings + /teams/fbs (team_
            # conference_map, its own cache) + /games -- the normal mode's
            # three plus this mode's one. Upper bound: the two /teams/fbs
            # reads are per-process cached, so a warm instance makes fewer.
            "cfbd_calls": 4,
            "rows_with_kickoff": sum(1 for r in placement_rows if r.get("kickoff_utc")),
            "upcoming_diagnostics": upcoming_diag,
        })
    print(
        f"[curate-and-write-cfb-shelves] season={season} week={week} preview_only={preview_only} "
        f"{'mode=upcoming ' if upcoming else ''}"
        f"players_read={len(player_weekly)} role_rows_read={len(role_weekly)} "
        f"receiving_rows_read={len(receiving_weekly)} week_rows={len(week_rows)} "
        f"shelf_counts={shelf_counts} placement_rows={len(placement_rows)} "
        f"forward={forward.get('success')}/{forward.get('status_code')} "
        f"forward_body={truncate_for_log(forward.get('response_body'), 500)!r}",
        flush=True,
    )
    return jsonify(response), (502 if forward.get("success") is False else 200)


@app.route("/api/curate-and-write-cfb-shelves", methods=["GET"])
def curate_and_write_cfb_shelves_health_check():
    return jsonify({
        "status": "ok",
        "usage": (
            "POST {\"season\": int, \"week\": int, \"preview_only\": bool (optional)}. "
            "Auth: X-Pipeline-Secret header. Reads the whole season back from "
            "cfb_player_redzone_weekly / cfb_defense_redzone_allowed_weekly / "
            "cfb_player_role_weekly / cfb_player_receiving_weekly, runs the real TD "
            "Opportunity + Situation + Role & Momentum + Target Magnets + Evidence "
            "Quality + Universal TPE scoring chain (cfb/scoring.py, unmodified), "
            "assigns the 8 real shelves (assign_cfb_shelves), resolves each shelf "
            "placement's own story archetype (story_archetype.resolve_cfb_archetype), "
            "and forwards one row per real (player, shelf) placement to "
            "cfb_player_shelf_scores."
        ),
        "scope": "Curation/orchestration only (Track B). Does not re-implement any scoring math.",
    })


# ===========================================================================
# CFB anytime-TD odds poller (2026-10). Make.com fetches raw Odds API event
# JSON and POSTs it here; this endpoint never calls The Odds API. See
# cfb/api/poll_cfb_attd_odds.py for the processing layer.
# ===========================================================================
@app.route("/api/poll-cfb-attd-odds", methods=["POST"])
def poll_cfb_attd_odds_endpoint():
    """
    POST body: {"season": int, "week": int, "events": [<raw Odds API
    event objects for market player_anytime_td>], "preview_only": bool
    (optional)}. Like NFL's /api/poll-market-value, a single event object
    or a bare list is also accepted as `events`, but season and week are
    required here (the odds table keys on them, and the pipeline has no
    schedule math to derive a CFB week from a commence_time).
    auth: X-Pipeline-Secret header == PIPELINE_INCOMING_SECRET.

    Per event: parse the player_anytime_td market (every book), detect
    the outcome schema, match players to the CFBD roster constrained to
    the event's two schools (attd_match.match_cfb_attd_players, unmatched
    reasons kept), resolve game_id/team_id/opponent from that week's CFBD
    /games, shape one row per matched player with book_odds, n_books,
    best_price/best_book and NFL's consensus rule, then forward every row
    to cfb_player_attd_odds_weekly's write route in signed chunks.
    preview_only runs everything except the write and returns a sample.
    A failed forward is a 502 carrying Lovable's status and error text.
    CFBD cost: at most /games + /roster per request, both cached per
    process and the roster fetched only when some event has the market.
    """
    auth_error = check_pipeline_secret()
    if auth_error:
        return auth_error

    data = request.get_json(force=True, silent=True)
    if data is None:
        return jsonify({"error": ATTD_EXPECTED_INPUT_ERROR}), 400
    # Envelope form {"season","week","events":...} is the normal shape. A
    # bare list or a single event object (has "bookmakers") is accepted
    # too, NFL-style -- then season/week must come as ?season=&week=
    # query parameters, since those shapes have nowhere to carry them.
    if isinstance(data, dict) and "bookmakers" not in data:
        envelope, raw_events = data, data.get("events")
    else:
        envelope, raw_events = {}, data
    try:
        season = int(envelope.get("season", request.args.get("season")))
        week = int(envelope.get("week", request.args.get("week")))
    except (TypeError, ValueError):
        return jsonify({
            "error": "season and week are required integers -- in the body ({\"season\": 2026, \"week\": 7, "
                     "\"events\": [...]}) or, for a bare list / single-event body, as ?season=&week= query parameters.",
        }), 400
    preview_only = bool(envelope.get("preview_only")) or str(request.args.get("preview_only", "")).lower() in ("1", "true")

    events = normalize_events_input(raw_events)
    if events is None:
        print(f"[poll-cfb-attd-odds] bad_input content_type={request.content_type!r} raw_body_len={len(request.get_data())}", flush=True)
        return jsonify({"error": ATTD_EXPECTED_INPUT_ERROR}), 400

    poll_timestamp = utc_now_iso()
    try:
        processed = process_events(events, season, week, poll_timestamp=poll_timestamp)
    except CFBDError as e:
        print(f"[poll-cfb-attd-odds] season={season} week={week} stage=cfbd error={e!r}", flush=True)
        return jsonify({"status": "error", "stage": "cfbd", "season": season, "week": week, "error": str(e)}), 502
    rows = processed["rows"]
    diag = processed["diagnostics"]

    forward = {"skipped": "preview_only"}
    if not preview_only:
        secret, secret_error = _resolve_secret()
        if secret_error:
            return secret_error
        forward = forward_rows_in_chunks(rows, secret)

    response = {
        "status": "ok" if forward.get("success") is not False else "error",
        "season": season,
        "week": week,
        "preview_only": preview_only,
        "poll_timestamp": poll_timestamp,
        "events_received": diag["events_received"],
        "events_with_no_market": diag["events_with_no_market"],
        "events_with_parse_errors": diag["events_with_parse_errors"],
        "events_processed": diag["events_processed"],
        "events_game_unresolved": diag["events_game_unresolved"],
        "schema_seen": diag["schema_seen"],
        "matched": diag["matched"],
        "unmatched_by_reason": diag["unmatched_by_reason"],
        "unmatched_sample": diag["unmatched_sample"],
        "players_priced": diag["players_priced"],
        "rows_written": 0 if preview_only or forward.get("success") is not True else forward.get("rows_sent", 0),
        "forward": forward,
        "cfbd_calls": diag["cfbd_calls"],
        "sample": rows[:5],
    }
    print(
        f"[poll-cfb-attd-odds] season={season} week={week} preview_only={preview_only} "
        f"events_received={diag['events_received']} no_market={len(diag['events_with_no_market'])} "
        f"parse_errors={len(diag['events_with_parse_errors'])} processed={diag['events_processed']} "
        f"schema_seen={diag['schema_seen']} matched={diag['matched']} unmatched={diag['unmatched_by_reason']} "
        f"players_priced={diag['players_priced']} cfbd_calls={diag['cfbd_calls']} "
        f"forward={forward.get('success')}/{forward.get('status_code')} "
        f"forward_body={truncate_for_log(forward.get('response_body'), 500)!r}",
        flush=True,
    )
    return jsonify(response), (502 if forward.get("success") is False else 200)


@app.route("/api/poll-cfb-attd-odds", methods=["GET"])
def poll_cfb_attd_odds_health_check():
    return jsonify({
        "status": "ok",
        "usage": (
            "POST {\"season\": int, \"week\": int, \"events\": [raw Odds API event objects for the "
            "player_anytime_td market], \"preview_only\": bool (optional)} -- a single event object or a "
            "bare list is also accepted as events. Auth: X-Pipeline-Secret header. Parses every book's "
            "anytime-TD outcomes, matches players to the CFBD roster (two-school constrained), resolves "
            "game/team ids from CFBD /games, and forwards one row per matched player (book_odds, n_books, "
            "best price, consensus) to cfb_player_attd_odds_weekly in signed chunks. Never calls The Odds API."
        ),
        "scope": "Odds ingest only. Curation reads the table separately.",
    })


@app.route("/api/cfb-shelves", methods=["GET"])
def cfb_shelves_endpoint():
    """
    GET /api/cfb-shelves?season=<int>&week=<int optional>

    Public, read-only, NO X-Pipeline-Secret — this is frontend
    consumption, not a pipeline-to-pipeline trigger (same reasoning NFL's
    read paths use the public get_published_nfl_intelligence_stories RPC
    directly rather than a signed route). Calls the real
    get_published_cfb_shelf_scores(p_season, p_week) SECURITY DEFINER RPC
    (this task's own Supabase migration) via a plain PostgREST POST, using
    SUPABASE_URL + SUPABASE_PUBLISHABLE_KEY — the SAME public anon key the
    frontend itself already uses for the equivalent NFL/MLB RPCs, not a
    pipeline secret. NEITHER env var exists on this Vercel project yet
    (confirmed 2026-09-04: cfb's env list is CFB_PIPELINE_WEBHOOK_SECRET/
    PIPELINE_INCOMING_SECRET/CFBD_API_KEY/ODDS_API_KEY/PORT only) — this
    route 502s with a clear message until they're added, same "loud,
    diagnosable failure, not a silent wrong answer" convention every other
    missing-config path in this codebase already follows.
    """
    season = request.args.get("season", type=int)
    week = request.args.get("week", type=int)

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_PUBLISHABLE_KEY")
    if not url or not key:
        return jsonify({
            "error": "SUPABASE_URL / SUPABASE_PUBLISHABLE_KEY are not configured on this Vercel project yet.",
        }), 502

    try:
        resp = requests.post(
            f"{url.rstrip('/')}/rest/v1/rpc/get_published_cfb_shelf_scores",
            json={"p_season": season, "p_week": week},
            headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            timeout=15,
        )
    except requests.RequestException as e:
        print(f"[cfb-shelves] season={season} week={week} error={e!r}", flush=True)
        return jsonify({"error": f"Supabase request failed: {e}"}), 502

    if resp.status_code != 200:
        print(f"[cfb-shelves] season={season} week={week} status={resp.status_code} body={truncate_for_log(resp.text, 500)!r}", flush=True)
        return jsonify({"error": "Supabase RPC call failed", "status_code": resp.status_code, "body": resp.text[:2000]}), 502

    rows = resp.json()
    return jsonify({"season": season, "week": week, "count": len(rows), "rows": rows}), 200
