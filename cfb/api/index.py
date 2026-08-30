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
from datetime import datetime, timezone
from pathlib import Path

# Vercel's Python runtime doesn't put this file's own directory (or its
# parent) on the import path — same root cause + fix nfl/api/index.py
# already documents for its own sibling imports.
sys.path.insert(0, str(Path(__file__).resolve().parent))          # cfb/api/  -> lovable_forward
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # cfb/      -> ids, plays_stats, roster, redzone

from flask import Flask, jsonify, request

from ids import CFBDError
from lovable_forward import forward_to_lovable, resolve_url_env, truncate_for_log
from plays_stats import completed_games, fetch_games, fetch_week_play_stats
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

    started = datetime.now(timezone.utc).isoformat()

    try:
        games = fetch_games(season, week, season_type=season_type)
        completed = completed_games(games)
        play_stats, fetch_diag = fetch_week_play_stats(completed, season_type=season_type)

        schools = sorted(
            {g.get("homeTeam") for g in games if g.get("homeTeam")}
            | {g.get("awayTeam") for g in games if g.get("awayTeam")}
        )
        raw_pos = raw_position_lookup(season, fallback_teams=schools)

        player_rows, player_diag = aggregate_redzone_game_cfb(
            play_stats, games, raw_pos, season=season, week=week
        )
        defense_rows, defense_diag = aggregate_redzone_allowed_cfb(
            play_stats, games, raw_pos, season=season, week=week
        )
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
        forwards["player"] = _forward(player_rows, secret, PLAYER_WRITE_URL_ENV, DEFAULT_PLAYER_WRITE_URL)
        forwards["defense"] = _forward(defense_rows, secret, DEFENSE_WRITE_URL_ENV, DEFAULT_DEFENSE_WRITE_URL)

    result = {
        "status": "ok",
        "started_at": started,
        "season": season,
        "week": week,
        "season_type": season_type,
        "preview_only": preview_only,
        "games_total": len(games),
        "games_completed": len(completed),
        "fetch": fetch_diag,
        "player_rows": len(player_rows),
        "defense_rows": len(defense_rows),
        "player_diagnostics": player_diag,
        "defense_diagnostics": defense_diag,
        "forwards": forwards,
        "player_sample": player_rows[:3],
        "defense_sample": defense_rows[:3],
    }

    p_fwd, d_fwd = forwards["player"], forwards["defense"]
    print(
        f"[ingest-and-write-redzone] season={season} week={week} season_type={season_type} "
        f"preview_only={preview_only} games_completed={len(completed)} "
        f"play_stat_rows={fetch_diag['play_stat_rows_total']} "
        f"player_rows={len(player_rows)} defense_rows={len(defense_rows)} "
        f"td_unmatched={player_diag.get('td_attribution', {}).get('unmatched')} "
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
                "(optional, default \"regular\"), \"preview_only\": bool (optional)}. "
                "Auth: X-Pipeline-Secret header. Pulls CFBD /games + per-game "
                "/plays/stats, aggregates red-zone band touch/TD counts per player "
                "(cfb_player_redzone_weekly) and per defense+position "
                "(cfb_defense_redzone_allowed_weekly), and forwards each to its "
                "Lovable write route in one HMAC-signed POST."
            ),
            "scope": "TD Opportunity (§2) + Situation defensive-matchup (§3) raw weekly counts only. "
                     "No scoring, no rolling windows, no Environment/Role/Evidence/Market Value.",
        }
    )
