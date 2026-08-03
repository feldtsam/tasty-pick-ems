"""
Tests grade_official_picks_live.py's full read -> grade -> write chain
against REAL data — real players, real completed games, real MLB Stats
API calls via grading.py's grade_pick() (unmodified, the same function
test_grading.py already validates) — no mocking of the grading logic
itself.

Reuses the exact real (mlbam_id, game_pk) pairs test_grading.py already
hand-verified (see live_data/test_grading.py) rather than the cached
odds-derived shelf_test_pool.json — that pool is gone from /tmp (expired)
and regenerating it spends real, budgeted Odds API requests. Grading only
ever reads mlbam_id/game_pk/shelf off a pick (see official_pick_grading.py),
so real odds/scoring data isn't needed here — only real players in real
completed games, which is exactly what test_grading.py already has.

THE PART THIS TEST EXISTS TO PROVE THAT test_grading.py AND
test_official_pick_grading.py DON'T: the anti-join itself. Both of those
already prove grade_pick()/grade_official_picks() are individually
idempotent (same input -> same output). Neither proves that the LIVE
CHAIN avoids re-grading something already graded — that guarantee lives
in the Lovable-side read endpoint's query (shelf_assignments rows with no
matching official_pick_results row), which doesn't exist as deployable
code yet. So this test spins up a STATEFUL local Flask double — an
in-memory shelf_assignments/official_pick_results pair that the read
endpoint genuinely queries against and the write endpoint genuinely
mutates — because a stateless double could confirm the write step upserts
correctly without ever proving the read step's exclusion logic does
anything at all.

Run: python3 pipeline/api/test_grade_official_picks_live.py
"""
import hashlib
import hmac
import json
import socket
import threading
import time
from datetime import datetime, timedelta, timezone

import requests
from flask import Flask, jsonify, request

from grade_official_picks_live import grade_official_picks_for_pending
from lovable_forward import forward_to_lovable

SECRET = "test-double-shared-secret"
WRONG_SECRET = "not-the-real-secret"
DEFAULT_LOOKBACK_DAYS = 3


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


def _verify_signature(raw_body: bytes, header_sig: str, secret: str) -> bool:
    """Mirrors verifySignature() in the drafted TypeScript routes exactly."""
    if not header_sig:
        return False
    provided = header_sig[7:] if header_sig.startswith("sha256=") else header_sig
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided, expected)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _to_num(v):
    if v is None or v == "":
        return None
    try:
        return float(v) if not float(v).is_integer() else int(float(v))
    except (TypeError, ValueError):
        return None


def make_test_double(real_secret: str):
    """
    A genuine local stand-in for BOTH not-yet-deployed Lovable routes,
    sharing ONE mutable state dict — this is what makes the anti-join
    check meaningful. picks-needing-grading-read queries `state` live on
    every call; official-pick-results-write mutates the same `state`.
    """
    app = Flask(__name__)
    state = {
        "shelf_assignments": [],   # seeded by the test before the server starts handling real requests
        "official_pick_results": [],
    }

    def _graded_key(row):
        return (row["mlbam_id"], row["game_pk"], row["shelf"])

    @app.route("/api/public/picks-needing-grading-read", methods=["POST"])
    def read_endpoint():
        raw_body = request.get_data()
        sig = request.headers.get("X-Signature", "")
        if not _verify_signature(raw_body, sig, real_secret):
            return jsonify({"ok": False, "error": "Invalid signature"}), 401

        body = json.loads(raw_body) if raw_body else {}
        lookback_days = body.get("lookback_days", DEFAULT_LOOKBACK_DAYS)
        window_start = datetime.now(timezone.utc) - timedelta(days=lookback_days)

        shelf_rows = [
            r for r in state["shelf_assignments"]
            if datetime.fromisoformat(r["assigned_at"].replace("Z", "+00:00")) >= window_start
        ]
        graded_rows = [
            r for r in state["official_pick_results"]
            if datetime.fromisoformat(r["graded_at"].replace("Z", "+00:00")) >= window_start
        ]
        graded_set = {_graded_key(r) for r in graded_rows}

        pending = [r for r in shelf_rows if _graded_key(r) not in graded_set]
        distinct_game_pks = {r["game_pk"] for r in pending}

        return jsonify({
            "ok": True,
            "lookback_days": lookback_days,
            "total_shelf_assignments_in_window": len(shelf_rows),
            "already_graded_count": len(graded_set),
            "row_count": len(pending),
            "distinct_game_pk_count": len(distinct_game_pks),
            "picks_needing_grading": pending,
        })

    @app.route("/api/public/official-pick-results-write", methods=["POST"])
    def write_endpoint():
        raw_body = request.get_data()
        sig = request.headers.get("X-Signature", "")
        if not _verify_signature(raw_body, sig, real_secret):
            return jsonify({"ok": False, "error": "Invalid signature"}), 401

        rows = json.loads(raw_body)
        if len(rows) == 0:
            return jsonify({
                "ok": True, "received": 0, "upserted": 0, "deduped": 0,
                "dropped_invalid_mlbam_id_count": 0, "dropped_invalid_mlbam_id": [],
            })

        # Same schema-level enforcement as the drafted TS route: a stray
        # "pending" status fails the WHOLE batch loud, not a per-row drop.
        for r in rows:
            if r.get("status") not in ("won", "lost", "void"):
                return jsonify({"ok": False, "error": "Invalid payload", "details": f"bad status: {r.get('status')!r}"}), 400

        dropped = []
        normalized = []
        for r in rows:
            mlbam_id = _to_num(r.get("mlbam_id"))
            game_pk = str(r.get("game_pk"))
            if mlbam_id is None:
                dropped.append({"mlbam_id": r.get("mlbam_id"), "game_pk": game_pk, "shelf": r.get("shelf"), "reason": "invalid mlbam_id"})
                continue
            normalized.append({
                "mlbam_id": mlbam_id,
                "game_pk": game_pk,
                "shelf": r["shelf"],
                "status": r["status"],
                "home_runs": _to_num(r.get("home_runs")),
                "plate_appearances": _to_num(r.get("plate_appearances")),
                "reason": r.get("reason"),
                "game_detailed_state": r.get("game_detailed_state"),
                "graded_at": datetime.now(timezone.utc).isoformat(),
            })

        deduped_map = {}
        for row in normalized:
            deduped_map[_graded_key(row)] = row
        deduped = list(deduped_map.values())

        # Real upsert simulation: replace-by-key if present, else append.
        existing_by_key = {_graded_key(r): i for i, r in enumerate(state["official_pick_results"])}
        for row in deduped:
            key = _graded_key(row)
            if key in existing_by_key:
                state["official_pick_results"][existing_by_key[key]] = row
            else:
                state["official_pick_results"].append(row)
                existing_by_key[key] = len(state["official_pick_results"]) - 1

        return jsonify({
            "ok": True,
            "received": len(rows),
            "upserted": len(deduped),
            "deduped": len(normalized) - len(deduped),
            "dropped_invalid_mlbam_id_count": len(dropped),
            "dropped_invalid_mlbam_id": dropped,
        })

    return app, state


if __name__ == "__main__":
    now_iso = datetime.now(timezone.utc).isoformat()

    # Real players, real completed games — the exact same
    # hand-verified (mlbam_id, game_pk) pairs live_data/test_grading.py
    # already validated against real MLB box scores. Gunnar Henderson
    # appears on two shelves (same real multi-shelf case already proven in
    # official_pick_grading.py) to exercise the fan-out through this live
    # chain too.
    GAME_1 = 824243
    GAME_2 = 824490
    GAME_3 = 823598  # postponed/rescheduled — real, uncertain-in-advance final status

    seed_rows = [
        {"mlbam_id": 680664, "game_pk": str(GAME_1), "shelf": "+300-499", "rank": 1, "is_tasty_six": True, "shelf_score": 71.2, "shelf_score_label": "final_score", "assigned_at": now_iso, "player_name": "Eduardo Valencia"},
        {"mlbam_id": 650402, "game_pk": str(GAME_1), "shelf": "Hot Hitters", "rank": 1, "is_tasty_six": False, "shelf_score": 0.910, "shelf_score_label": "recent_ops", "assigned_at": now_iso, "player_name": "Gleyber Torres"},
        {"mlbam_id": 702616, "game_pk": str(GAME_1), "shelf": "+500-699", "rank": 1, "is_tasty_six": False, "shelf_score": 65.4, "shelf_score_label": "final_score", "assigned_at": now_iso, "player_name": "Jackson Holliday"},
        {"mlbam_id": 701162, "game_pk": str(GAME_1), "shelf": "Going Nuclear", "rank": 1, "is_tasty_six": False, "shelf_score": 58.9, "shelf_score_label": "final_score", "assigned_at": now_iso, "player_name": "Ben Malgeri"},
        {"mlbam_id": 701678, "game_pk": str(GAME_1), "shelf": "Cold Pitchers to Attack", "rank": 1, "is_tasty_six": False, "shelf_score": 5.14, "shelf_score_label": "recent_era", "assigned_at": now_iso, "player_name": "Hao-Yu Lee"},
        {"mlbam_id": 669236, "game_pk": str(GAME_1), "shelf": "Weather Factors", "rank": 1, "is_tasty_six": True, "shelf_score": 82.0, "shelf_score_label": "environment_score", "assigned_at": now_iso, "player_name": "Jeremiah Jackson"},
        {"mlbam_id": 701398, "game_pk": str(GAME_2), "shelf": "+300-499", "rank": 2, "is_tasty_six": False, "shelf_score": 60.1, "shelf_score_label": "final_score", "assigned_at": now_iso, "player_name": "Sal Stewart"},
        {"mlbam_id": 682829, "game_pk": str(GAME_2), "shelf": "Hot Hitters", "rank": 2, "is_tasty_six": False, "shelf_score": 0.845, "shelf_score_label": "recent_ops", "assigned_at": now_iso, "player_name": "Elly De La Cruz"},
        {"mlbam_id": 683002, "game_pk": str(GAME_3), "shelf": "+500-699", "rank": 2, "is_tasty_six": False, "shelf_score": 55.0, "shelf_score_label": "final_score", "assigned_at": now_iso, "player_name": "Gunnar Henderson"},
        {"mlbam_id": 683002, "game_pk": str(GAME_3), "shelf": "Going Nuclear", "rank": 3, "is_tasty_six": False, "shelf_score": 55.0, "shelf_score_label": "final_score", "assigned_at": now_iso, "player_name": "Gunnar Henderson"},
    ]
    real_unique_games = {GAME_1, GAME_2, GAME_3}
    real_unique_picks = {(r["mlbam_id"], r["game_pk"]) for r in seed_rows}

    app, state = make_test_double(SECRET)
    state["shelf_assignments"] = seed_rows
    port = _free_port()
    server_thread = threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=port, use_reloader=False),
        daemon=True,
    )
    server_thread.start()
    time.sleep(0.5)

    read_url = f"http://127.0.0.1:{port}/api/public/picks-needing-grading-read"
    write_url = f"http://127.0.0.1:{port}/api/public/official-pick-results-write"

    results = []

    # --- Sanity: nothing graded yet, all 10 real seeded picks are "needing grading" ---
    r1 = requests.post(read_url, data=json.dumps({}), headers={
        "X-Signature": f"sha256={hmac.new(SECRET.encode(), json.dumps({}).encode(), hashlib.sha256).hexdigest()}",
    })
    first_read = r1.json()
    print(f"First read (nothing graded yet): {first_read}\n")
    results.append(check("first read sees all 10 real seeded picks as needing grading", first_read["row_count"] == 10))
    results.append(check("first read reports the real 3 distinct games", first_read["distinct_game_pk_count"] == len(real_unique_games)))
    results.append(check("first read reports zero already-graded", first_read["already_graded_count"] == 0))

    # --- Run the FULL real chain: read -> grade (real MLB API calls) -> write ---
    first_chain = grade_official_picks_for_pending(SECRET, read_url, write_url)
    print(f"First chain run: picks_needing_grading_count={first_chain['picks_needing_grading_count']} "
          f"graded_count={first_chain['graded_count']} still_pending_count={first_chain['still_pending_count']} "
          f"grading_errors={first_chain['grading_errors']}\n")

    results.append(check("first chain run had no top-level error", first_chain["error"] is None))
    results.append(check("first chain run saw all 10 real picks", first_chain["picks_needing_grading_count"] == 10))
    results.append(check("no real grading errors (all 3 game_pks are valid, real games)", len(first_chain["grading_errors"]) == 0))
    results.append(check(
        "every graded pick is accounted for as either terminal or still-pending",
        first_chain["graded_count"] + first_chain["still_pending_count"] == 10,
    ))
    results.append(check(
        "at least one real pick graded terminal (games 824243/824490 were confirmed Final in prior real testing)",
        first_chain["graded_count"] >= 8,
    ))

    graded_terminal_count = first_chain["graded_count"]
    if graded_terminal_count > 0:
        results.append(check("terminal results were forwarded successfully to the real write endpoint", first_chain["forwarded"]["success"] is True))
        results.append(check(
            "the double's official_pick_results state now holds exactly the graded terminal rows",
            len(state["official_pick_results"]) == graded_terminal_count,
        ))
        # Gunnar Henderson appears on 2 shelves for the same real game — if
        # his game graded terminal, both shelf appearances must report an
        # IDENTICAL verdict (the one genuinely new thing official_pick_
        # grading.py exists to guarantee), now proven through the live chain.
        henderson_results = [r for r in first_chain["results_written"] if r["mlbam_id"] == 683002]
        if len(henderson_results) == 2:
            results.append(check(
                "Gunnar Henderson's two real shelf appearances report an identical verdict",
                henderson_results[0]["status"] == henderson_results[1]["status"]
                and henderson_results[0]["home_runs"] == henderson_results[1]["home_runs"],
            ))
    else:
        results.append(check("SKIPPED forward/state checks — 0 terminal results this run (unexpected)", False))

    # --- THE key check: a second read must now EXCLUDE whatever just got graded ---
    r2 = requests.post(read_url, data=json.dumps({}), headers={
        "X-Signature": f"sha256={hmac.new(SECRET.encode(), json.dumps({}).encode(), hashlib.sha256).hexdigest()}",
    })
    second_read = r2.json()
    print(f"Second read (after grading): {second_read}\n")
    results.append(check(
        "second read excludes exactly the picks graded terminal in the first run — real anti-join correctness",
        second_read["row_count"] == 10 - graded_terminal_count,
    ))
    results.append(check(
        "second read's already_graded_count matches what was actually written",
        second_read["already_graded_count"] == graded_terminal_count,
    ))

    # --- Write-step idempotency: re-send the SAME terminal results directly ---
    if graded_terminal_count > 0:
        forward_again = forward_to_lovable(first_chain["results_written"], SECRET, write_url)
        results.append(check("re-sending the same terminal results a second time still succeeds", forward_again["success"]))
        results.append(check(
            "state did not grow — re-upserting identical results doesn't duplicate rows",
            len(state["official_pick_results"]) == graded_terminal_count,
        ))

    # --- Full-chain idempotency: running the whole thing again must not re-grade what's already graded ---
    second_chain = grade_official_picks_for_pending(SECRET, read_url, write_url)
    print(f"Second chain run: picks_needing_grading_count={second_chain['picks_needing_grading_count']} "
          f"graded_count={second_chain['graded_count']}\n")
    results.append(check(
        "second full chain run only sees the still-pending remainder, not the already-graded picks",
        second_chain["picks_needing_grading_count"] == second_read["row_count"],
    ))

    # --- Targeted write-endpoint robustness checks ---
    bad_mlbam_batch = [{
        "mlbam_id": "not-a-number", "game_pk": "999999", "shelf": "Hot Hitters",
        "status": "won", "home_runs": 1, "plate_appearances": 4, "reason": "test", "game_detailed_state": "Final",
    }]
    bad_result = forward_to_lovable(bad_mlbam_batch, SECRET, write_url)
    resp_body = json.loads(bad_result["response_body"])
    results.append(check(
        "a synthetic invalid mlbam_id is dropped and named in the response, not silently swallowed",
        resp_body["dropped_invalid_mlbam_id_count"] == 1 and resp_body["received"] == 1 and resp_body["upserted"] == 0,
    ))

    stray_pending_batch = [{
        "mlbam_id": 1, "game_pk": "999999", "shelf": "Hot Hitters",
        "status": "pending", "home_runs": None, "plate_appearances": None, "reason": None, "game_detailed_state": "Live",
    }]
    stray_result = forward_to_lovable(stray_pending_batch, SECRET, write_url)
    results.append(check(
        "a stray 'pending' status is rejected with a real 400 for the whole batch, not silently dropped",
        stray_result["success"] is False and stray_result["status_code"] == 400,
    ))

    # --- Wrong secret genuinely rejected ---
    try:
        grade_official_picks_for_pending(WRONG_SECRET, read_url, write_url)
        results.append(check("wrong secret is rejected by real signature verification", False))
    except requests.exceptions.HTTPError as e:
        results.append(check(
            "wrong secret is rejected by real signature verification (real HTTP 401 over the wire)",
            e.response.status_code == 401,
        ))

    print(f"\n{sum(results)}/{len(results)} checks passed")
    raise SystemExit(0 if all(results) else 1)
