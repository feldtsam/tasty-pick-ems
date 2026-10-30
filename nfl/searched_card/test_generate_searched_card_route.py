"""
Stage 2 — tests for nfl/api/index.py's POST /api/generate-searched-card,
including this revision's replay protection (X-Timestamp) and the
request-body size cap.

Imports index.py as a plain module (same sys.path convention index.py
itself already uses for its own siblings) rather than adding a test file
under nfl/api/ — kept here so every file this stage touches stays inside
nfl/searched_card/ and nfl/api/index.py, per the task's own scope.

MOCKED ANTHROPIC CLIENT ONLY. The "ready"/"flag off" tests below patch
index.generate_searched_card_draft directly, so no real writer code (and
therefore no real network call) ever runs in this file either; every
auth/replay/size-rejection test never reaches that function call at all.
No real request to api.anthropic.com is made anywhere in this file.

Run: cd nfl/searched_card && ../.venv/bin/python -m pytest test_generate_searched_card_route.py -v
"""
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))

import index  # noqa: E402

SECRET = "test-secret-do-not-use-in-prod"


def _raw(body: dict) -> str:
    """The exact string that gets signed and sent -- built once, used for
    both, so the two can never drift apart (same discipline lovable_
    forward.py's own serialize_payload already documents for its outbound
    signing)."""
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


def _signed_post(client, body: dict, secret: str = SECRET, signature: str = None,
                  timestamp: str = None, include_timestamp: bool = True,
                  include_signature: bool = True):
    """Builds the exact raw body bytes, a timestamp header (defaults to
    "now"), and signs f"{timestamp}.{raw}" (this revision's real signed
    material) -- unless `signature`/`timestamp` override that, for the
    specific rejection tests below that need a deliberately wrong or
    absent value."""
    raw = _raw(body)
    ts = timestamp if timestamp is not None else str(int(time.time()))
    sig = signature if signature is not None else index.compute_signature(secret, f"{ts}.{raw}")

    headers = {}
    if include_timestamp:
        headers[index.SEARCHED_CARD_TIMESTAMP_HEADER] = ts
    if include_signature:
        headers[index.SEARCHED_CARD_SIGNATURE_HEADER] = sig

    return client.post(
        "/api/generate-searched-card",
        data=raw.encode("utf-8"),
        headers=headers,
        content_type="application/json",
    )


def _valid_body():
    return {
        "row": {"player_id": "p1", "td_opportunity": 70.0, "role_momentum": 70.0, "situation": 70.0,
                "evidence_quality": 90.0, "consensus_price_american": -150.0},
        "pool": [{"td_opportunity": 70.0, "role_momentum": 70.0, "situation": 70.0,
                  "evidence_quality": 90.0, "consensus_price_american": -150.0}] * 150,
    }


def _with_secret(monkeypatch):
    monkeypatch.setenv("SEARCHED_CARD_PIPELINE_SECRET", SECRET)


# ---------------------------------------------------------------------------
# Signature / existing coverage, updated for the timestamp-prefixed signed
# material -- a valid request now needs a valid X-Timestamp too.
# ---------------------------------------------------------------------------

def test_bad_signature_rejected_with_401_before_parsing_body(monkeypatch):
    _with_secret(monkeypatch)
    client = index.app.test_client()
    with patch.object(index, "generate_searched_card_draft") as mock_writer:
        resp = _signed_post(client, _valid_body(), signature="sha256=not_the_real_signature")
        mock_writer.assert_not_called()
    assert resp.status_code == 401


def test_missing_signature_header_rejected_with_401(monkeypatch):
    _with_secret(monkeypatch)
    client = index.app.test_client()
    with patch.object(index, "generate_searched_card_draft") as mock_writer:
        # A valid, present timestamp -- so this fails on the MISSING
        # signature specifically, not on the timestamp check.
        resp = _signed_post(client, _valid_body(), include_signature=False)
        mock_writer.assert_not_called()
    assert resp.status_code == 401


def test_flag_off_rejected_with_503_with_a_valid_signature_and_timestamp(monkeypatch):
    """Required re-coverage for this revision: a fully valid, correctly
    signed, correctly timestamped request must still be rejected with 503
    -- not 401, not 200 -- while SEARCHED_CARD_WRITER_ENABLED is False."""
    _with_secret(monkeypatch)
    assert index.SEARCHED_CARD_WRITER_ENABLED is False
    client = index.app.test_client()
    with patch.object(index, "generate_searched_card_draft") as mock_writer:
        resp = _signed_post(client, _valid_body())
        mock_writer.assert_not_called()
    assert resp.status_code == 503


def test_bad_input_shape_rejected_with_400_once_flag_is_on(monkeypatch):
    _with_secret(monkeypatch)
    monkeypatch.setattr(index, "SEARCHED_CARD_WRITER_ENABLED", True)
    client = index.app.test_client()
    with patch.object(index, "generate_searched_card_draft") as mock_writer:
        resp = _signed_post(client, {"row": {"player_id": "p1"}})  # no "pool" at all
        mock_writer.assert_not_called()
    assert resp.status_code == 400


def test_ready_flows_through_to_a_200_with_the_writer_result(monkeypatch):
    _with_secret(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-not-used")
    monkeypatch.setattr(index, "SEARCHED_CARD_WRITER_ENABLED", True)
    fake_result = {"status": "ready", "card": {"title": "A Real Title"}, "classification": {"label": "aligned"}}
    client = index.app.test_client()
    with patch.object(index, "generate_searched_card_draft", return_value=fake_result) as mock_writer:
        resp = _signed_post(client, _valid_body())
        mock_writer.assert_called_once()

    assert resp.status_code == 200
    assert resp.get_json() == fake_result


# ---------------------------------------------------------------------------
# Replay protection -- X-Timestamp
# ---------------------------------------------------------------------------

def test_missing_timestamp_header_rejected_with_401(monkeypatch):
    _with_secret(monkeypatch)
    client = index.app.test_client()
    with patch.object(index, "generate_searched_card_draft") as mock_writer:
        resp = _signed_post(client, _valid_body(), include_timestamp=False)
        mock_writer.assert_not_called()
    assert resp.status_code == 401


def test_non_integer_timestamp_header_rejected_with_401(monkeypatch):
    _with_secret(monkeypatch)
    client = index.app.test_client()
    with patch.object(index, "generate_searched_card_draft") as mock_writer:
        resp = _signed_post(client, _valid_body(), timestamp="not-a-number")
        mock_writer.assert_not_called()
    assert resp.status_code == 401


def test_stale_timestamp_rejected_with_401(monkeypatch):
    """A timestamp more than 60 seconds in the PAST, with an otherwise
    correctly computed signature for that exact timestamp -- rejected on
    the window check, never reaching signature verification's happy path."""
    _with_secret(monkeypatch)
    client = index.app.test_client()
    stale_ts = str(int(time.time()) - 120)
    with patch.object(index, "generate_searched_card_draft") as mock_writer:
        resp = _signed_post(client, _valid_body(), timestamp=stale_ts)
        mock_writer.assert_not_called()
    assert resp.status_code == 401


def test_future_timestamp_rejected_with_401(monkeypatch):
    """Same check, other direction -- a timestamp more than 60 seconds in
    the FUTURE must be rejected too, not just a stale one; the window is
    symmetric by design."""
    _with_secret(monkeypatch)
    client = index.app.test_client()
    future_ts = str(int(time.time()) + 120)
    with patch.object(index, "generate_searched_card_draft") as mock_writer:
        resp = _signed_post(client, _valid_body(), timestamp=future_ts)
        mock_writer.assert_not_called()
    assert resp.status_code == 401


def test_valid_timestamp_but_tampered_body_rejected_with_401(monkeypatch):
    """A signature computed for one body, replayed against a DIFFERENT
    body under the same (otherwise-valid) timestamp, must fail -- the
    signed material includes the raw body, so this is a real tamper
    detection, not just a timestamp check."""
    _with_secret(monkeypatch)
    client = index.app.test_client()
    ts = str(int(time.time()))
    original_body = _valid_body()
    signature_for_original = index.compute_signature(SECRET, f"{ts}.{_raw(original_body)}")

    tampered_body = _valid_body()
    tampered_body["row"]["player_id"] = "a-different-player-entirely"

    with patch.object(index, "generate_searched_card_draft") as mock_writer:
        resp = client.post(
            "/api/generate-searched-card",
            data=_raw(tampered_body).encode("utf-8"),
            headers={
                index.SEARCHED_CARD_TIMESTAMP_HEADER: ts,
                index.SEARCHED_CARD_SIGNATURE_HEADER: signature_for_original,
            },
            content_type="application/json",
        )
        mock_writer.assert_not_called()
    assert resp.status_code == 401


def test_replay_of_an_old_valid_signed_request_rejected_with_401(monkeypatch):
    """The real replay scenario: a request that WAS fully valid at the
    moment it was signed (timestamp == signing time, signature correct
    for that timestamp+body) is captured and resent later. Modeled by
    mocking the SERVER's clock forward past the signing time, rather than
    sending a stale header -- the header/signature pair is exactly as
    valid as it ever was; only the server's notion of "now" has moved."""
    _with_secret(monkeypatch)
    client = index.app.test_client()
    signing_time = int(time.time())
    ts = str(signing_time)
    body = _valid_body()
    valid_signature = index.compute_signature(SECRET, f"{ts}.{_raw(body)}")

    monkeypatch.setattr(index.time, "time", lambda: signing_time + 120)

    with patch.object(index, "generate_searched_card_draft") as mock_writer:
        resp = client.post(
            "/api/generate-searched-card",
            data=_raw(body).encode("utf-8"),
            headers={
                index.SEARCHED_CARD_TIMESTAMP_HEADER: ts,
                index.SEARCHED_CARD_SIGNATURE_HEADER: valid_signature,
            },
            content_type="application/json",
        )
        mock_writer.assert_not_called()
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Body size cap
# ---------------------------------------------------------------------------

def test_oversized_body_rejected_with_413_before_signature_work(monkeypatch):
    """No valid signature is even constructed for this request -- the
    size check must fire first, so an attacker can't force expensive HMAC
    work (or a database-sized payload) through just by signing it
    correctly first. Body is sized relative to the REAL current
    SEARCHED_CARD_MAX_BODY_BYTES (512 KB as of this revision, was 256 KB)
    rather than a fixed row count, so this test stays a genuine over-the-
    cap case if that constant is ever changed again."""
    _with_secret(monkeypatch)
    client = index.app.test_client()
    rows_needed = index.SEARCHED_CARD_MAX_BODY_BYTES // 1000 + 100  # comfortable margin over the cap
    oversized_body = {"row": {"player_id": "p1"}, "pool": [{"pad": "x" * 1000} for _ in range(rows_needed)]}
    raw = _raw(oversized_body)
    assert len(raw.encode("utf-8")) > index.SEARCHED_CARD_MAX_BODY_BYTES

    with patch.object(index, "generate_searched_card_draft") as mock_writer, \
         patch.object(index, "compute_signature") as mock_compute_signature:
        resp = client.post(
            "/api/generate-searched-card",
            data=raw.encode("utf-8"),
            headers={index.SEARCHED_CARD_TIMESTAMP_HEADER: str(int(time.time())), index.SEARCHED_CARD_SIGNATURE_HEADER: "irrelevant"},
            content_type="application/json",
        )
        mock_compute_signature.assert_not_called()
        mock_writer.assert_not_called()
    assert resp.status_code == 413
