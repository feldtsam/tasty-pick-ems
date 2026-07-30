"""
Tests curate_shelves.py's full read -> sanity-check -> curate -> shape
chain against REAL data — the same 239-real-candidate pool
test_shelf_curation.py and test_official_pick_grading.py already use
(/tmp/shelf_test_pool.json) — with no mocking of the HTTP or HMAC layer.

Lovable's real scored-picks-read and shelf-assignments-write routes
aren't deployed yet (they're staged for review — see the pipeline
README), so there's no live endpoint to round-trip against. Rather than
mock `requests.post` or stub out signature verification, this spins up a
genuine local Flask server in a background thread that implements the
EXACT SAME signature-verification logic as the real drafted TypeScript
routes (HMAC-SHA256 over the exact raw body bytes, "sha256=" prefix,
timing-safe-equivalent comparison) and serves/accepts real data. That way
`fetch_todays_scored_picks()`'s actual signed-HTTP-request code, and the
real forward_to_lovable() signing code, both get genuinely exercised over
a real socket — not assumed correct by code review alone.

Run: python3 pipeline/api/test_curate_shelves.py
"""
import hashlib
import hmac
import json
import socket
import threading
import time
from pathlib import Path

import requests
from flask import Flask, jsonify, request

from curate_shelves import (
    MIN_EXPECTED_GAMES,
    curate_shelves_for_date,
    sanity_check_slate,
)
from lovable_forward import compute_signature, serialize_payload
from shelf_curation import DEFAULT_SHELF_SIZE, assign_shelves, compute_tasty_six

POOL_PATH = Path("/tmp/shelf_test_pool.json")
SEASON = 2026
TEST_DATE = "2026-07-28"
SECRET = "test-double-shared-secret"
WRONG_SECRET = "not-the-real-secret"


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


def _verify_signature(raw_body: bytes, header_sig: str, secret: str) -> bool:
    """Mirrors verifySignature() in both drafted TypeScript routes exactly."""
    if not header_sig:
        return False
    provided = header_sig[7:] if header_sig.startswith("sha256=") else header_sig
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided, expected)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_test_double(pool: list, captured_writes: list, real_secret: str):
    """
    A real local stand-in for the two not-yet-deployed Lovable routes.
    `served_pool` is mutable via closure so one test (the "suspicious
    slate" case) can point the same server at a deliberately truncated
    pool without spinning up a second server.
    """
    app = Flask(__name__)
    state = {"served_pool": pool}

    @app.route("/api/public/scored-picks-read", methods=["POST"])
    def read_endpoint():
        raw_body = request.get_data()
        sig = request.headers.get("X-Signature", "")
        if not _verify_signature(raw_body, sig, real_secret):
            return jsonify({"ok": False, "error": "Invalid signature"}), 401

        body = json.loads(raw_body) if raw_body else {}
        date = body.get("date")
        rows = state["served_pool"]
        distinct_game_pks = {r["game_pk"] for r in rows}
        return jsonify({
            "ok": True,
            "date": date,
            "row_count": len(rows),
            "distinct_game_pk_count": len(distinct_game_pks),
            "scored_picks": rows,
        })

    @app.route("/api/public/shelf-assignments-write", methods=["POST"])
    def write_endpoint():
        raw_body = request.get_data()
        sig = request.headers.get("X-Signature", "")
        if not _verify_signature(raw_body, sig, real_secret):
            return jsonify({"ok": False, "error": "Invalid signature"}), 401

        rows = json.loads(raw_body)
        captured_writes.append(rows)
        return jsonify({"ok": True, "received": len(rows), "upserted": len(rows), "deduped": 0})

    return app, state


if __name__ == "__main__":
    if not POOL_PATH.exists():
        print(f"SKIPPED — {POOL_PATH} not present in this environment. Regenerate with:\n"
              f"  python3 pipeline/scripts/build_shelf_test_pool.py")
        raise SystemExit(0)

    pool = json.loads(POOL_PATH.read_text())
    print(f"Real pool: {len(pool)} scored picks across "
          f"{len({c['game_pk'] for c in pool})} real games\n")

    captured_writes = []
    app, state = make_test_double(pool, captured_writes, SECRET)
    port = _free_port()
    server_thread = threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=port, use_reloader=False),
        daemon=True,
    )
    server_thread.start()
    time.sleep(0.5)  # real server needs a moment to bind before the first request

    read_url = f"http://127.0.0.1:{port}/api/public/scored-picks-read"
    write_url = f"http://127.0.0.1:{port}/api/public/shelf-assignments-write"

    results = []

    # --- Independently computed expected shape, straight from
    # shelf_curation.py itself, to compare curate_shelves_for_date()'s
    # output against — this is the same real validation
    # test_shelf_curation.py already proved, now checked again through
    # the new read -> curate -> write path end to end.
    expected_shelves = assign_shelves(pool, season=SEASON, shelf_size=DEFAULT_SHELF_SIZE)
    expected_tasty_six = compute_tasty_six(expected_shelves)
    expected_row_count = sum(len(v) for v in expected_shelves.values())

    result = curate_shelves_for_date(TEST_DATE, SECRET, read_url, shelf_size=DEFAULT_SHELF_SIZE)

    results.append(check("chain completed with no error", result["error"] is None))
    results.append(check("sanity check not flagged suspicious", not result["sanity_check"]["suspicious"]))
    results.append(check(
        "sanity check reports the real distinct game count",
        result["sanity_check"]["distinct_game_pk_count"] == len({c["game_pk"] for c in pool}),
    ))
    results.append(check(
        "shelf sizes match shelf_curation.py's own direct output",
        result["shelf_sizes"] == {k: len(v) for k, v in expected_shelves.items()},
    ))
    results.append(check(
        "tasty six repeats match shelf_curation.py's own direct output",
        result["tasty_six_repeats"] == expected_tasty_six["repeats"],
    ))
    results.append(check(
        "row count equals sum of all real shelf sizes",
        len(result["shelf_assignments"]) == expected_row_count,
    ))

    real_shelf_names = set(expected_shelves.keys())
    got_shelf_names = {row["shelf"] for row in result["shelf_assignments"]}
    results.append(check("all six real shelf names present in curated rows", real_shelf_names == got_shelf_names))
    results.append(check("exactly six shelves total (three odds tiers + three themed)", len(real_shelf_names) == 6))

    tasty_rows = [r for r in result["shelf_assignments"] if r["is_tasty_six"]]
    expected_tasty_count = sum(1 for v in expected_tasty_six["picks"].values() if v is not None)
    results.append(check(
        "exactly one is_tasty_six=True row per shelf with a real pick (deduplicated across repeats)",
        len(tasty_rows) == expected_tasty_count,
    ))
    results.append(check(
        "Tasty Six rows are drawn from distinct (mlbam_id, game_pk) pairs — a real deduplicated Tasty Six",
        len({(r["mlbam_id"], r["game_pk"]) for r in tasty_rows}) == len(tasty_rows),
    ))

    # --- forward to the real (test-double) write endpoint, same as the
    # /api/curate-shelves Flask route itself would do.
    from lovable_forward import forward_to_lovable
    forward_result = forward_to_lovable(result["shelf_assignments"], SECRET, write_url)
    results.append(check("forward to shelf-assignments-write succeeded", forward_result["success"]))
    results.append(check(
        "write endpoint genuinely received every curated row over the wire",
        len(captured_writes) == 1 and len(captured_writes[0]) == len(result["shelf_assignments"]),
    ))

    # --- idempotency: run the full chain twice against identical real
    # input, assert byte-identical curated output — same discipline every
    # grading/orchestration function in this pipeline has been held to.
    result_again = curate_shelves_for_date(TEST_DATE, SECRET, read_url, shelf_size=DEFAULT_SHELF_SIZE)
    results.append(check(
        "idempotent — identical real input curated twice produces identical output",
        result["shelf_assignments"] == result_again["shelf_assignments"]
        and result["tasty_six_repeats"] == result_again["tasty_six_repeats"],
    ))

    # --- suspicious slate: point the same server at a deliberately
    # truncated pool (2 real games instead of 14) and confirm the chain
    # aborts LOUDLY rather than quietly curating broken shelves.
    truncated_game_pks = list({c["game_pk"] for c in pool})[:2]
    state["served_pool"] = [c for c in pool if c["game_pk"] in truncated_game_pks]
    suspicious_result = curate_shelves_for_date(TEST_DATE, SECRET, read_url, shelf_size=DEFAULT_SHELF_SIZE)
    results.append(check(
        "suspiciously incomplete slate (2 games) is flagged, not silently curated",
        suspicious_result["error"] is not None and suspicious_result["shelf_assignments"] == [],
    ))
    results.append(check(
        "suspicious sanity_check reports suspicious=True with the real truncated game count",
        suspicious_result["sanity_check"]["suspicious"] is True
        and suspicious_result["sanity_check"]["distinct_game_pk_count"] == 2,
    ))
    state["served_pool"] = pool  # restore for anything after

    # --- direct unit check on the sanity-check boundary itself.
    results.append(check(
        "sanity_check_slate boundary: exactly MIN_EXPECTED_GAMES is NOT suspicious",
        sanity_check_slate({"distinct_game_pk_count": MIN_EXPECTED_GAMES, "row_count": 1})["suspicious"] is False,
    ))
    results.append(check(
        "sanity_check_slate boundary: one below MIN_EXPECTED_GAMES IS suspicious",
        sanity_check_slate({"distinct_game_pk_count": MIN_EXPECTED_GAMES - 1, "row_count": 1})["suspicious"] is True,
    ))

    # --- a genuinely wrong secret must be rejected by real signature
    # verification (a real 401 over the wire), not silently accepted.
    try:
        curate_shelves_for_date(TEST_DATE, WRONG_SECRET, read_url, shelf_size=DEFAULT_SHELF_SIZE)
        results.append(check("wrong secret is rejected by real signature verification", False))
    except requests.exceptions.HTTPError as e:
        results.append(check(
            "wrong secret is rejected by real signature verification (real HTTP 401 over the wire)",
            e.response.status_code == 401,
        ))

    print(f"\n{sum(results)}/{len(results)} checks passed")
    raise SystemExit(0 if all(results) else 1)
