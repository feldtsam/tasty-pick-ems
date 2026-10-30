"""
Stage 2 — tests for nfl/api/index.py's POST /api/generate-searched-card.

Imports index.py as a plain module (same sys.path convention index.py
itself already uses for its own siblings) rather than adding a test file
under nfl/api/ — kept here so every file this stage touches stays inside
nfl/searched_card/ and nfl/api/index.py, per the task's own scope.

MOCKED ANTHROPIC CLIENT ONLY. The "ready" test below patches
index.generate_searched_card_draft directly, so no real writer code (and
therefore no real network call) ever runs in this file either; the
bad-signature/bad-input/flag-off tests never reach that function call at
all. No real request to api.anthropic.com is made anywhere in this file.

Run: cd nfl/searched_card && ../.venv/bin/python -m pytest test_generate_searched_card_route.py -v
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))

import index  # noqa: E402

SECRET = "test-secret-do-not-use-in-prod"


def _signed_post(client, body: dict, secret: str = SECRET, signature: str = None):
    """Builds the exact raw body bytes once, signs THOSE bytes (same
    discipline lovable_forward.py's own module docstring requires of its
    outbound signing: sign and send the identical string), and POSTs
    them. `signature` overrides the computed one, for the bad-signature
    test below."""
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"))
    sig = signature if signature is not None else index.compute_signature(secret, raw)
    return client.post(
        "/api/generate-searched-card",
        data=raw.encode("utf-8"),
        headers={"X-Signature": sig},
        content_type="application/json",
    )


def _valid_body():
    return {
        "row": {"player_id": "p1", "td_opportunity": 70.0, "role_momentum": 70.0, "situation": 70.0,
                "evidence_quality": 90.0, "consensus_price_american": -150.0},
        "pool": [{"td_opportunity": 70.0, "role_momentum": 70.0, "situation": 70.0,
                  "evidence_quality": 90.0, "consensus_price_american": -150.0}] * 150,
    }


def test_bad_signature_rejected_with_401_before_parsing_body(monkeypatch):
    monkeypatch.setenv("SEARCHED_CARD_PIPELINE_SECRET", SECRET)
    client = index.app.test_client()
    with patch.object(index, "generate_searched_card_draft") as mock_writer:
        resp = _signed_post(client, _valid_body(), signature="sha256=not_the_real_signature")
        mock_writer.assert_not_called()
    assert resp.status_code == 401


def test_missing_signature_header_rejected_with_401():
    with_env = {"SEARCHED_CARD_PIPELINE_SECRET": SECRET}
    import os
    old = os.environ.get("SEARCHED_CARD_PIPELINE_SECRET")
    os.environ["SEARCHED_CARD_PIPELINE_SECRET"] = SECRET
    try:
        client = index.app.test_client()
        raw = json.dumps(_valid_body(), sort_keys=True, separators=(",", ":"))
        resp = client.post("/api/generate-searched-card", data=raw.encode("utf-8"), content_type="application/json")
        assert resp.status_code == 401
    finally:
        if old is None:
            os.environ.pop("SEARCHED_CARD_PIPELINE_SECRET", None)
        else:
            os.environ["SEARCHED_CARD_PIPELINE_SECRET"] = old


def test_flag_off_rejected_with_503_with_a_valid_signature(monkeypatch):
    monkeypatch.setenv("SEARCHED_CARD_PIPELINE_SECRET", SECRET)
    assert index.SEARCHED_CARD_WRITER_ENABLED is False
    client = index.app.test_client()
    with patch.object(index, "generate_searched_card_draft") as mock_writer:
        resp = _signed_post(client, _valid_body())
        mock_writer.assert_not_called()
    assert resp.status_code == 503


def test_bad_input_shape_rejected_with_400_once_flag_is_on(monkeypatch):
    monkeypatch.setenv("SEARCHED_CARD_PIPELINE_SECRET", SECRET)
    monkeypatch.setattr(index, "SEARCHED_CARD_WRITER_ENABLED", True)
    client = index.app.test_client()
    with patch.object(index, "generate_searched_card_draft") as mock_writer:
        resp = _signed_post(client, {"row": {"player_id": "p1"}})  # no "pool" at all
        mock_writer.assert_not_called()
    assert resp.status_code == 400


def test_ready_flows_through_to_a_200_with_the_writer_result(monkeypatch):
    monkeypatch.setenv("SEARCHED_CARD_PIPELINE_SECRET", SECRET)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-not-used")
    monkeypatch.setattr(index, "SEARCHED_CARD_WRITER_ENABLED", True)
    fake_result = {"status": "ready", "card": {"title": "A Real Title"}, "classification": {"label": "aligned"}}
    client = index.app.test_client()
    with patch.object(index, "generate_searched_card_draft", return_value=fake_result) as mock_writer:
        resp = _signed_post(client, _valid_body())
        mock_writer.assert_called_once()

    assert resp.status_code == 200
    assert resp.get_json() == fake_result
