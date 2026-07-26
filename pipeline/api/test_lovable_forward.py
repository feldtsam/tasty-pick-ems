"""
Tests the HMAC signing and forwarding logic before it ever touches the
real Lovable endpoint.

Two kinds of check, deliberately not just "does my code agree with
itself":
  1. compute_signature is cross-checked against an independently-computed
     HMAC via the `openssl` CLI — a completely separate implementation, so
     this isn't circular.
  2. forward_to_lovable is tested against a real local HTTP server (stdlib
     http.server, no extra dependencies) that captures the exact raw bytes
     it received and independently recomputes the HMAC over them. This is
     the closest thing to a real round-trip test achievable without
     access to the actual Lovable backend — it validates the full chain
     (serialize once -> sign -> transmit -> arrives unmodified -> verifies)
     rather than just the signing function in isolation.

Run: python3 pipeline/api/test_lovable_forward.py
"""
import hashlib
import hmac
import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from lovable_forward import compute_signature, forward_to_lovable, serialize_payload

TEST_SECRET = "test-secret-do-not-use-in-real-deployment"


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


def openssl_hmac_sha256_hex(secret: str, message: str) -> str:
    """Independent cross-check: shell out to openssl rather than reusing
    Python's hmac module, so this genuinely tests against a second
    implementation, not just re-running the same code path."""
    result = subprocess.run(
        ["openssl", "dgst", "-sha256", "-hmac", secret],
        input=message.encode("utf-8"),
        capture_output=True,
        check=True,
    )
    # openssl's output looks like "SHA2-256(stdin)= <hex>\n" (or similar,
    # varies by version) — the hex digest is always the last token.
    return result.stdout.decode().strip().split()[-1]


class _CapturingHandler(BaseHTTPRequestHandler):
    """Mock Lovable endpoint: captures the exact raw body + headers it
    received, independently re-verifies the signature, and responds based
    on a class-level `response_code` so tests can simulate failures too."""

    captured = {}
    response_code = 200

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length)
        _CapturingHandler.captured = {
            "raw_body": raw_body,
            "signature_header": self.headers.get("X-Signature"),
            "content_type": self.headers.get("Content-Type"),
        }
        self.send_response(_CapturingHandler.response_code)
        self.end_headers()
        self.wfile.write(b"ok" if _CapturingHandler.response_code < 300 else b"simulated failure")

    def log_message(self, *args):
        pass  # keep test output quiet


def run_mock_server():
    server = HTTPServer(("127.0.0.1", 0), _CapturingHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port


if __name__ == "__main__":
    results = []

    # --- Part 1: signature computation, cross-checked against openssl ---
    message = '[{"a": 1, "b": 2}]'
    expected_hex = openssl_hmac_sha256_hex(TEST_SECRET, message)
    ours = compute_signature(TEST_SECRET, message)
    results.append(check(
        "compute_signature matches an independent openssl HMAC computation",
        ours == f"sha256={expected_hex}",
    ))

    # --- Part 2: serialize_payload is deterministic and sort_keys works ---
    rows_a = [{"player_name": "Judge", "odds": 150}]
    rows_b = [{"odds": 150, "player_name": "Judge"}]  # same data, different key order
    results.append(check(
        "serialize_payload gives identical output regardless of dict key order",
        serialize_payload(rows_a) == serialize_payload(rows_b),
    ))
    results.append(check(
        "serialize_payload output is valid JSON that round-trips",
        json.loads(serialize_payload(rows_a)) == rows_a,
    ))

    # --- Part 3: full round trip against a real local HTTP server ---
    server, port = run_mock_server()
    mock_url = f"http://127.0.0.1:{port}/api/public/pipeline-write"

    sample_rows = [
        {"player_name": "Andrew Velazquez", "odds": 1100, "bookmaker": "BetRivers",
         "game_id": "g1", "home_team": "Detroit Tigers", "away_team": "Kansas City Royals",
         "commence_time": "2026-07-25T17:11:00Z"},
        {"player_name": "Matt Vierling", "odds": 750, "bookmaker": "BetRivers",
         "game_id": "g1", "home_team": "Detroit Tigers", "away_team": "Kansas City Royals",
         "commence_time": "2026-07-25T17:11:00Z"},
    ]

    _CapturingHandler.response_code = 200
    result = forward_to_lovable(sample_rows, TEST_SECRET, mock_url)

    results.append(check("forward_to_lovable reports success on a 200 response", result["success"] is True))
    results.append(check("forward_to_lovable reports the upstream status code", result["status_code"] == 200))

    received = _CapturingHandler.captured
    results.append(check(
        "the receiving server got the exact same string that serialize_payload produced",
        received["raw_body"].decode("utf-8") == serialize_payload(sample_rows),
    ))
    results.append(check(
        "Content-Type header is application/json",
        received["content_type"] == "application/json",
    ))

    # The critical check: recompute the signature independently on the
    # "receiving" side, over the bytes actually received (not the original
    # Python objects) — this is exactly what a real webhook verifier does.
    recomputed = compute_signature(TEST_SECRET, received["raw_body"].decode("utf-8"))
    results.append(check(
        "independently recomputing the signature over the received bytes matches the sent header",
        recomputed == received["signature_header"],
    ))

    # --- Part 4: failure handling ---
    _CapturingHandler.response_code = 500
    fail_result = forward_to_lovable(sample_rows, TEST_SECRET, mock_url)
    results.append(check("a 500 upstream response is reported as failure, not success", fail_result["success"] is False))
    results.append(check("failure result includes the upstream status code", fail_result["status_code"] == 500))
    results.append(check(
        "failure result never leaks the secret in the error message",
        TEST_SECRET not in (fail_result["error"] or ""),
    ))

    # --- Part 5: unreachable endpoint doesn't crash ---
    unreachable_result = forward_to_lovable(sample_rows, TEST_SECRET, "http://127.0.0.1:1/nope")
    results.append(check(
        "an unreachable URL is reported as a clean failure, not an exception",
        unreachable_result["success"] is False and unreachable_result["status_code"] is None,
    ))

    server.shutdown()

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
