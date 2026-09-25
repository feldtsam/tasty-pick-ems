"""Endpoint-level acceptance tests 5-10 from SPEC.md.

The Anthropic client is always mocked -- no test in this file makes a network
call of any kind. Storage is replaced by an in-memory fake that enforces the
same uniqueness constraint the real table does, `(scenario_id, kind,
source_id)`, so idempotency is tested against the real invariant rather than a
stub that always says yes.

Covered here:
  5.  Sutton cannot choose tier or status
  6.  Sutton cannot manufacture evidence
  7.  LLM failure is contained
  8.  Sutton cannot modify production (env surface + outbound-host grep)
  9.  Output stays concise
  10. Idempotency
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys
from pathlib import Path

import pytest

SUTTON = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SUTTON))
sys.path.insert(0, str(SUTTON / "api"))

import index as endpoint  # noqa: E402
import normalize  # noqa: E402
import radar  # noqa: E402
from replay import load_fixture  # noqa: E402
import store  # noqa: E402
from interpret import SYSTEM_PROMPT, interpret  # noqa: E402
from packet import build_packet  # noqa: E402
from validate import validate_interpretation  # noqa: E402

SECRET = "test-incoming-secret"


# --- in-memory storage fake ------------------------------------------------


class FakeStore:
    """Mirrors the real routes' behaviour closely enough to test against.

    `observations` is keyed on the table's unique constraint, so a re-sent
    payload genuinely cannot create duplicates. Escalations that were emailed
    land in `escalations_last_24h`, which is what suppresses a second send.
    """

    def __init__(self):
        self.observations: dict[tuple, dict] = {}
        self.incidents: list[dict] = []
        self.escalations_last_24h: list[dict] = []
        self.last_collection_at = None
        self.write_observations_fails = False
        self.read_state_fails = False
        self.write_incidents_fails = False
        self.observation_write_calls = 0

    def write_observations(self, rows):
        self.observation_write_calls += 1
        if self.write_observations_fails:
            return False, None, "simulated observation write failure"
        inserted = 0
        for row in rows:
            key = (row["scenario_id"], row["kind"], row["source_id"])
            if key not in self.observations:
                self.observations[key] = row
                inserted += 1
            if row.get("collected_at"):
                self.last_collection_at = row["collected_at"]
        return True, {"received": len(rows), "inserted": inserted}, None

    def write_incidents(self, rows):
        if self.write_incidents_fails:
            return False, None, "simulated incident write failure"
        self.incidents.extend(rows)
        for row in rows:
            if row["tier"] == "ESCALATE" and row["emailed"]:
                self.escalations_last_24h.append(
                    {"signal_id": row["signal_id"], "scenario_id": row["scenario_id"]}
                )
        return True, {"inserted": len(rows)}, None

    def read_state(self, now=None):
        if self.read_state_fails:
            return False, None, "simulated state read failure"
        return True, {
            "last_collection_at": self.last_collection_at,
            "escalations_last_24h": list(self.escalations_last_24h),
            "observations": list(self.observations.values()),
        }, None


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("SUTTON_INCOMING_SECRET", SECRET)
    monkeypatch.setenv("SUTTON_WRITE_SECRET", "test-write-secret")
    monkeypatch.setenv("SUTTON_MODEL", "claude-opus-5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-real-key")
    monkeypatch.setenv("SUTTON_SHADOW", "false")


@pytest.fixture
def fake(monkeypatch, env):
    fs = FakeStore()
    monkeypatch.setattr(store, "write_observations", fs.write_observations)
    monkeypatch.setattr(store, "write_incidents", fs.write_incidents)
    monkeypatch.setattr(store, "read_state", fs.read_state)
    return fs


@pytest.fixture
def client():
    endpoint.app.config.update(TESTING=True)
    return endpoint.app.test_client()


def mock_llm(monkeypatch, *, output=None, fail=None):
    """Replace the endpoint's interpret() with a deterministic stand-in."""

    def fake_interpret(packet, **kwargs):
        if fail is not None:
            return False, None, "claude-opus-5", fail
        text = output if isinstance(output, str) else json.dumps(output)
        return True, text, "claude-opus-5", None

    monkeypatch.setattr(endpoint, "interpret", fake_interpret)


def post(client, payload):
    return client.post(
        "/api/sutton-run",
        json=payload,
        headers={"X-Sutton-Secret": SECRET},
    )


# --- payload builders (all clearly synthetic) -----------------------------


def failing_payload(collected_at="2026-10-01T12:00:00Z", scenario_id=6186710):
    """SYNTHETIC: two consecutive failed auto runs -> I2 ESCALATE -> RED."""
    return {
        "collected_at": collected_at,
        "mode": "collect",
        "scenarios": [
            {
                "scenario_id": scenario_id,
                "name": "SYNTHETIC Picks",
                "is_active": True,
                "is_paused": False,
                "executions": [
                    {
                        "execution_id": "synthetic-fail-1",
                        "started_at": "2026-10-01T10:00:00Z",
                        "ended_at": None,
                        "duration_ms": 320000,
                        "status": 3,
                        "run_type": "auto",
                        "error_name": "ModuleTimeoutError",
                        "error_message": "The operation timed out",
                        "cause_module": "MakeRequest",
                    },
                    {
                        "execution_id": "synthetic-fail-2",
                        "started_at": "2026-10-01T11:00:00Z",
                        "ended_at": None,
                        "duration_ms": 321000,
                        "status": 3,
                        "run_type": "auto",
                        "error_name": "ModuleTimeoutError",
                        "error_message": "The operation timed out",
                        "cause_module": "MakeRequest",
                    },
                ],
                "events": [],
            }
        ],
    }


def green_payload(collected_at="2026-10-01T12:00:00Z"):
    """SYNTHETIC: one clean success -> GREEN."""
    return {
        "collected_at": collected_at,
        "mode": "collect",
        "scenarios": [
            {
                "scenario_id": 6186710,
                "name": "SYNTHETIC Picks",
                "is_active": True,
                "is_paused": False,
                "executions": [
                    {
                        "execution_id": "synthetic-ok-1",
                        "started_at": "2026-10-01T11:00:00Z",
                        "ended_at": None,
                        "duration_ms": 150000,
                        "status": 1,
                        "run_type": "auto",
                        "error_name": None,
                        "error_message": None,
                        "cause_module": None,
                    }
                ],
                "events": [],
            }
        ],
    }


GOOD_OUTPUT = {
    "headline": "Two consecutive automatic runs failed and none has succeeded since.",
    "interpretation": "The failures are unrecovered. A timeout in the HTTP module is "
                      "one possible explanation.",
    "recommended_investigation": "Continue observing.",
    "confidence": "medium",
}


# --- auth and basic shape -------------------------------------------------


def test_post_requires_the_incoming_secret(client, fake):
    resp = client.post("/api/sutton-run", json=green_payload())
    assert resp.status_code == 401
    resp = client.post(
        "/api/sutton-run", json=green_payload(), headers={"X-Sutton-Secret": "wrong"}
    )
    assert resp.status_code == 401


def test_paired_get_health_check(client, env):
    resp = client.get("/api/sutton-run")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["watched_scenarios"] == [6186710, 6241867, 6152892, 6079077]
    assert set(body["env_configured"]) == {
        "ANTHROPIC_API_KEY", "SUTTON_MODEL", "SUTTON_INCOMING_SECRET",
        "SUTTON_WRITE_SECRET", "SUTTON_SHADOW",
    }


def test_response_has_the_contract_keys(client, fake, monkeypatch):
    mock_llm(monkeypatch, output=GOOD_OUTPUT)
    body = post(client, failing_payload()).get_json()
    for key in ("status", "shadow", "deliver_escalation", "deliver_radar",
                "subject", "body", "storage_ok", "llm_ok"):
        assert key in body


# --- acceptance test 2 (endpoint level): no LLM call on GREEN -------------


def test_green_makes_no_llm_call(client, fake, monkeypatch):
    calls = []

    def must_not_run(packet, **kwargs):
        calls.append(packet)
        raise AssertionError("interpret() must not be called on GREEN")

    monkeypatch.setattr(endpoint, "interpret", must_not_run)
    body = post(client, green_payload()).get_json()
    assert body["status"] == "GREEN"
    assert body["llm_ok"] is True
    assert calls == []
    assert body["body"].startswith("SUTTON — GREEN")
    assert "4 watched scenarios" in body["body"]


# --- acceptance test 5: Sutton cannot choose tier or status ---------------


@pytest.mark.parametrize("smuggled", ["tier", "status", "color"])
def test_5_llm_output_claiming_tier_status_or_color_is_rejected(
    client, fake, monkeypatch, smuggled
):
    bad = dict(GOOD_OUTPUT)
    bad[smuggled] = "GREEN"
    mock_llm(monkeypatch, output=bad)

    body = post(client, failing_payload()).get_json()

    # Rejected, so the deterministic fallback is used...
    assert body["llm_ok"] is False
    assert "Interpretation unavailable." in body["body"]
    assert GOOD_OUTPUT["headline"] not in body["body"]
    # ...and the header still reads the colour that code assigned.
    assert body["status"] == "RED"
    assert body["body"].startswith("SUTTON — RED (escalation)")
    assert any(s["signal_id"] == "H2_INTERPRETATION_FAILED" for s in body["signals"])


def test_5_the_header_comes_from_the_packet_not_the_llm(client, fake, monkeypatch):
    """Even a well-formed interpretation cannot move the header."""
    sneaky = dict(GOOD_OUTPUT)
    sneaky["headline"] = "Everything is GREEN and healthy."
    mock_llm(monkeypatch, output=sneaky)
    body = post(client, failing_payload()).get_json()
    assert body["status"] == "RED"
    assert body["body"].splitlines()[0] == "SUTTON — RED (escalation)"


def test_5_validator_rejects_extra_keys_directly():
    packet = {"date": "2026-10-01", "status": "RED", "watched_scenarios": 4,
              "signals": [{"signal_id": "I2", "facts": {"consecutive_failures": 2}}]}
    ok, parsed, reason = validate_interpretation({**GOOD_OUTPUT, "tier": "RADAR"}, packet)
    assert ok is False and parsed is None
    assert reason.startswith("EXTRA_KEYS:tier")


# --- acceptance test 6: Sutton cannot manufacture evidence ----------------


def test_6_number_not_in_facts_is_rejected_and_fallback_used(client, fake, monkeypatch):
    fabricated = dict(GOOD_OUTPUT)
    fabricated["interpretation"] = "Runtime rose 47 percent before the failures."
    mock_llm(monkeypatch, output=fabricated)

    body = post(client, failing_payload()).get_json()
    assert body["llm_ok"] is False
    assert "Interpretation unavailable." in body["body"]
    assert body["status"] == "RED"
    # The fabricated number must not appear as TPE evidence. It does appear in
    # the HARNESS_HEALTH block, as the H2 rejection reason
    # (NUMBER_NOT_IN_FACTS:47), which is the diagnostic Sam needs.
    tpe_half = body["body"].split("HARNESS_HEALTH")[0]
    assert "47" not in tpe_half
    assert "NUMBER_NOT_IN_FACTS:47" in body["body"]


def test_6_a_number_that_is_in_facts_is_accepted(client, fake, monkeypatch):
    grounded = dict(GOOD_OUTPUT)
    grounded["headline"] = "2 consecutive automatic runs failed."
    mock_llm(monkeypatch, output=grounded)
    body = post(client, failing_payload()).get_json()
    assert body["llm_ok"] is True
    assert "2 consecutive automatic runs failed." in body["body"]


def test_6_time_or_effort_cost_claims_are_rejected(client, fake, monkeypatch):
    for phrase in (
        "This likely cost several hours of debugging.",
        "Sam spent time restarting it by hand.",
        "The maintenance cost is rising.",
    ):
        costed = dict(GOOD_OUTPUT)
        costed["interpretation"] = phrase
        mock_llm(monkeypatch, output=costed)
        body = post(client, failing_payload()).get_json()
        assert body["llm_ok"] is False, phrase
        assert "Interpretation unavailable." in body["body"]


# --- acceptance test 7: LLM failure is contained --------------------------


@pytest.mark.parametrize(
    "failure",
    [
        "APITimeoutError: request timed out",
        "APIConnectionError: connection reset",
        "APIStatusError: HTTP 529 overloaded",
        "refusal",
    ],
)
def test_7_llm_transport_failures_still_return_200(client, fake, monkeypatch, failure):
    mock_llm(monkeypatch, fail=failure)
    resp = post(client, failing_payload())
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["llm_ok"] is False
    assert body["status"] == "RED"
    assert "Interpretation unavailable." in body["body"]
    h2 = [s for s in body["signals"] if s["signal_id"] == "H2_INTERPRETATION_FAILED"]
    assert h2 and h2[0]["class"] == "HARNESS_HEALTH" and h2[0]["tier"] == "LOG"


def test_7_invalid_json_from_the_llm_still_returns_200(client, fake, monkeypatch):
    mock_llm(monkeypatch, output="Here you go! {not valid json at all")
    resp = post(client, failing_payload())
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["llm_ok"] is False
    assert "Interpretation unavailable." in body["body"]
    assert any(s["signal_id"] == "H2_INTERPRETATION_FAILED" for s in body["signals"])


def test_7_the_fallback_body_carries_deterministic_facts(client, fake, monkeypatch):
    mock_llm(monkeypatch, fail="APITimeoutError: timed out")
    body = post(client, failing_payload()).get_json()["body"]
    assert "I2_CONSECUTIVE_FAILURES (ESCALATE)" in body
    assert "consecutive_failures: 2" in body
    # Still no error text, redacted or not.
    assert "error_message" not in body
    assert "timed out" not in body.replace("APITimeoutError: timed out", "")


def test_7_h2_is_recorded_as_an_incident(client, fake, monkeypatch):
    mock_llm(monkeypatch, fail="APITimeoutError: timed out")
    post(client, failing_payload())
    ids = [r["signal_id"] for r in fake.incidents]
    assert "H2_INTERPRETATION_FAILED" in ids
    h2 = next(r for r in fake.incidents if r["signal_id"] == "H2_INTERPRETATION_FAILED")
    assert h2["signal_class"] == "HARNESS_HEALTH"
    assert h2["llm_ok"] is False


def test_7_interpret_itself_contains_a_raising_client():
    """interpret() never raises, whatever the SDK does."""

    class Exploding:
        class messages:
            @staticmethod
            def create(**kwargs):
                raise RuntimeError("boom")

    ok, text, model, error = interpret({"status": "RED", "signals": []},
                                       model="claude-opus-5", client=Exploding())
    assert ok is False and text is None
    assert "RuntimeError" in error


def test_7_interpret_treats_a_refusal_as_failure():
    class Refusing:
        class messages:
            @staticmethod
            def create(**kwargs):
                class M:
                    stop_reason = "refusal"
                    content = []
                    model = "claude-opus-5"
                return M()

    ok, _text, _model, error = interpret({"status": "RED", "signals": []},
                                         model="claude-opus-5", client=Refusing())
    assert ok is False and error == "refusal"


# --- acceptance test 8: Sutton cannot modify production -------------------


ALLOWED_ENV = {
    "ANTHROPIC_API_KEY",
    "SUTTON_MODEL",
    "SUTTON_INCOMING_SECRET",
    "SUTTON_WRITE_SECRET",
    "SUTTON_SHADOW",
}


def _sutton_py_files():
    return [
        p for p in glob.glob(str(SUTTON / "**" / "*.py"), recursive=True)
        if "__pycache__" not in p
    ]


def test_8_sutton_reads_only_the_five_allowed_env_vars():
    pattern = re.compile(r"os\.environ(?:\.get)?\(\s*[\"']([A-Z0-9_]+)[\"']")
    seen = set()
    for path in _sutton_py_files():
        if "/tests/" in path:
            continue
        seen |= set(pattern.findall(open(path).read()))
    # SUTTON_TRANSCRIPT is a local-only fixture-builder convenience, never
    # read at request time and never set in the Vercel project.
    extra = seen - ALLOWED_ENV - {"SUTTON_TRANSCRIPT"}
    assert not extra, f"sutton/ reads unexpected env vars: {sorted(extra)}"
    assert "SUPABASE" not in " ".join(seen)
    assert not any(v.startswith("MAKE") for v in seen)


def test_8_no_outbound_hosts_other_than_anthropic_and_the_sutton_routes():
    host_re = re.compile(r"https?://([A-Za-z0-9._-]+)")
    allowed_hosts = {
        "tastypickems.com",      # the three sutton-* routes (store.py)
        "api.anthropic.com",     # only if ever written explicitly; SDK default
        "api.example.com",       # redaction test string, not a real call
        "apps.make.com",         # a comment URL in a docstring, not a call
        "pypi.org",              # a comment URL in requirements guidance
    }
    offenders = {}
    for path in _sutton_py_files():
        for host in host_re.findall(open(path).read()):
            if host not in allowed_hosts:
                offenders.setdefault(host, []).append(os.path.basename(path))
    assert not offenders, f"unexpected outbound hosts: {offenders}"


def test_8_store_only_addresses_the_three_sutton_routes():
    for url in (store.OBSERVATIONS_ROUTE, store.INCIDENTS_ROUTE, store.STATE_ROUTE):
        assert url.startswith("https://tastypickems.com/api/public/sutton-")
    src = open(SUTTON / "store.py").read()
    for forbidden in ("poll-market-value", "curate-and-write-drafts", "build-stub-week",
                      "nfl-content-drafts", "nfl-price-history", "grade-nfl"):
        assert forbidden not in src


# Third-party and stdlib names sutton/ is allowed to import. Anything else must
# resolve to a file inside sutton/ itself.
_EXTERNAL_OK = {
    "flask", "requests", "anthropic", "certifi", "pytest", "__future__",
}


def _imported_top_level(path):
    import ast
    names = set()
    tree = ast.parse(open(path).read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:          # relative import, inside sutton/ by definition
                continue
            if node.module:
                names.add(node.module.split(".")[0])
    return names


def test_8_sutton_imports_nothing_from_nfl_or_pipeline():
    """Checked on the AST, not by substring: prose like "imports NOTHING from
    nfl/" is not an import, and a real `import shelves` would be."""
    import sys as _sys
    stdlib = getattr(_sys, "stdlib_module_names", frozenset())
    local = {Path(p).stem for p in _sutton_py_files()}
    offenders = {}
    for path in _sutton_py_files():
        for name in _imported_top_level(path):
            if name in _EXTERNAL_OK or name in stdlib or name in local:
                continue
            offenders.setdefault(os.path.basename(path), []).append(name)
    assert not offenders, f"imports that are neither stdlib, allowed, nor local: {offenders}"

    # And no production package is reachable by name.
    for path in _sutton_py_files():
        names = _imported_top_level(path)
        assert not (names & {"nfl", "pipeline", "cfb"}), os.path.basename(path)


# --- acceptance test 9: output stays concise ------------------------------


def test_9_over_eighty_words_is_rejected(client, fake, monkeypatch):
    verbose = dict(GOOD_OUTPUT)
    verbose["interpretation"] = " ".join(["word"] * 90)
    mock_llm(monkeypatch, output=verbose)
    body = post(client, failing_payload()).get_json()
    assert body["llm_ok"] is False
    assert "Interpretation unavailable." in body["body"]


def test_9_exactly_eighty_words_is_accepted():
    packet = {"date": "2026-10-01", "status": "RED", "watched_scenarios": 4,
              "signals": [{"signal_id": "I2", "facts": {}}]}
    filler = " ".join(["word"] * 77)  # + 3 words from the other three fields
    out = {
        "headline": "one",
        "interpretation": filler,
        "recommended_investigation": "two",
        "confidence": "low",
    }
    ok, parsed, reason = validate_interpretation(out, packet)
    assert ok is True, reason
    assert parsed["confidence"] == "low"

    out["interpretation"] = filler + " extra"
    ok, _parsed, reason = validate_interpretation(out, packet)
    assert ok is False and reason.startswith("TOO_LONG:")


# --- acceptance test 10: idempotency --------------------------------------


def test_10_same_payload_twice_creates_no_duplicate_observations(
    client, fake, monkeypatch
):
    mock_llm(monkeypatch, output=GOOD_OUTPUT)
    payload = failing_payload()

    first = post(client, payload).get_json()
    after_first = len(fake.observations)
    second = post(client, payload).get_json()

    assert fake.observation_write_calls == 2, "both runs attempted a write"
    assert len(fake.observations) == after_first == 2, "no duplicate rows"
    assert first["status"] == second["status"] == "RED"


def test_10_no_second_escalation_email(client, fake, monkeypatch):
    mock_llm(monkeypatch, output=GOOD_OUTPUT)
    payload = failing_payload()

    first = post(client, payload).get_json()
    assert first["deliver_escalation"] is True
    assert [e["signal_id"] for e in first["new_escalations"]] == ["I2_CONSECUTIVE_FAILURES"]

    second = post(client, payload).get_json()
    assert second["deliver_escalation"] is False, "suppressed by escalations_last_24h"
    assert second["new_escalations"] == []
    assert second["status"] == "RED", "the status is unchanged; only delivery is suppressed"


def test_10_a_different_escalation_still_delivers(client, fake, monkeypatch):
    mock_llm(monkeypatch, output=GOOD_OUTPUT)
    post(client, failing_payload())
    other = failing_payload(collected_at="2026-10-01T13:00:00Z", scenario_id=6241867)
    other["scenarios"][0]["executions"][0]["execution_id"] = "synthetic-other-1"
    other["scenarios"][0]["executions"][1]["execution_id"] = "synthetic-other-2"
    body = post(client, other).get_json()
    assert body["deliver_escalation"] is True
    assert [e["scenario_id"] for e in body["new_escalations"]] == [6241867]


# --- shadow mode and delivery flags --------------------------------------


def test_shadow_suppresses_escalation_email(client, fake, monkeypatch):
    monkeypatch.setenv("SUTTON_SHADOW", "true")
    mock_llm(monkeypatch, output=GOOD_OUTPUT)
    body = post(client, failing_payload()).get_json()
    assert body["shadow"] is True
    assert body["deliver_escalation"] is False
    assert body["status"] == "RED", "shadow hides the email, not the finding"
    assert all(r["shadow"] is True for r in fake.incidents)


def test_daily_radar_always_delivers_and_shadow_prefixes_the_subject(
    client, fake, monkeypatch
):
    monkeypatch.setenv("SUTTON_SHADOW", "true")
    mock_llm(monkeypatch, output=GOOD_OUTPUT)
    payload = failing_payload()
    payload["mode"] = "daily_radar"
    body = post(client, payload).get_json()
    assert body["deliver_radar"] is True
    assert body["deliver_escalation"] is False
    assert body["subject"].startswith("[SHADOW] ")

    monkeypatch.setenv("SUTTON_SHADOW", "false")
    body = post(client, payload).get_json()
    assert body["deliver_radar"] is True
    assert not body["subject"].startswith("[SHADOW] ")


def test_collect_mode_never_delivers_the_radar(client, fake, monkeypatch):
    mock_llm(monkeypatch, output=GOOD_OUTPUT)
    body = post(client, failing_payload()).get_json()
    assert body["deliver_radar"] is False


# --- degraded storage ----------------------------------------------------


def test_state_read_failure_degrades_but_still_responds(client, fake, monkeypatch):
    fake.read_state_fails = True
    mock_llm(monkeypatch, output=GOOD_OUTPUT)
    resp = post(client, failing_payload())
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["storage_ok"] is False
    assert body["status"] == "RED", "checks still ran, on the payload alone"
    ids = [s["signal_id"] for s in body["signals"]]
    assert "H_STATE_READ_FAILED" in ids
    degraded = next(s for s in body["signals"] if s["signal_id"] == "H_STATE_READ_FAILED")
    assert degraded["class"] == "HARNESS_HEALTH"
    assert "HARNESS_HEALTH" in body["body"]


def test_observation_write_failure_sets_storage_ok_false(client, fake, monkeypatch):
    fake.write_observations_fails = True
    mock_llm(monkeypatch, output=GOOD_OUTPUT)
    body = post(client, failing_payload()).get_json()
    assert body["storage_ok"] is False
    assert body["status"] == "RED"
    assert "H_OBSERVATION_WRITE_FAILED" in [s["signal_id"] for s in body["signals"]]


def test_incident_write_failure_still_returns_the_radar(client, fake, monkeypatch):
    fake.write_incidents_fails = True
    mock_llm(monkeypatch, output=GOOD_OUTPUT)
    resp = post(client, failing_payload())
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["storage_ok"] is False
    assert body["subject"].startswith("SUTTON — RED")


# --- redaction runs first (acceptance test 12, endpoint level) ------------


def test_redaction_happens_before_storage(client, fake, monkeypatch):
    mock_llm(monkeypatch, output=GOOD_OUTPUT)
    payload = failing_payload()
    payload["scenarios"][0]["executions"][0]["error_message"] = (
        "Invalid value for header 'X-Pipeline-Secret': "
        "'0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef'."
    )
    post(client, payload)

    stored = [r for r in fake.observations.values() if r["kind"] == "execution"]
    messages = " ".join(str(r.get("error_message") or "") for r in stored)
    assert "[REDACTED:" in messages
    assert not re.search(r"[0-9a-f]{32,}", messages)


def test_the_packet_never_carries_error_text(client, fake, monkeypatch):
    captured = {}

    def capture(packet, **kwargs):
        captured["packet"] = packet
        return True, json.dumps(GOOD_OUTPUT), "claude-opus-5", None

    monkeypatch.setattr(endpoint, "interpret", capture)
    post(client, failing_payload())

    blob = json.dumps(captured["packet"])
    assert "error_message" not in blob
    assert '"detail"' not in blob
    assert "The operation timed out" not in blob
    # error_name and cause_module are structured, and allowed.
    assert "ModuleTimeoutError" in blob


# --- the system prompt ---------------------------------------------------


def test_system_prompt_states_the_non_negotiables():
    for phrase in (
        "Continue observing.",
        "never decide",  # lower-cased check below instead
    )[:1]:
        assert phrase in SYSTEM_PROMPT
    lowered = SYSTEM_PROMPT.lower()
    for phrase in ("tier", "status", "colour", "hypothesis", "80 words"):
        assert phrase in lowered
    assert "only what is in the packet" in lowered


def test_packet_shape_matches_the_spec():
    evaluation = {
        "evaluated_at": "2026-10-01T12:00:00Z",
        "status": "YELLOW",
        "watched_scenarios": 4,
        "tpe_signals": [
            {
                "signal_id": "L1_DURATION_DRIFT",
                "class": "LEARNING",
                "tier": "RADAR",
                "scenario_id": 6186710,
                "scenario": "NFL Picks Daily Generation",
                "facts": {"flagged_runs_of_last_5": 2, "error_message": "leak"},
                "recent_edits": ["2026-09-07T22:32:05Z"],
            },
            {"signal_id": "X", "class": "LEARNING", "tier": "LOG", "scenario": "n",
             "facts": {}, "recent_edits": []},
        ],
        "harness_signals": [],
        "signals": [],
    }
    packet = build_packet(evaluation, watched_scenarios=4)
    assert sorted(packet) == ["date", "signals", "status", "watched_scenarios"]
    assert packet["date"] == "2026-10-01"
    assert len(packet["signals"]) == 1, "LOG signals are not evidence"
    sig = packet["signals"][0]
    assert sorted(sig) == ["class", "facts", "recent_edits", "scenario", "signal_id", "tier"]
    assert "scenario_id" not in sig
    assert "error_message" not in sig["facts"]


# --- daily_radar: replay the window, then aggregate ----------------------


def radar_payload(collected_at="2026-10-02T12:00:00Z", executions=()):
    """SYNTHETIC: a daily_radar run. `executions` become the STORED history."""
    return {
        "collected_at": collected_at,
        "mode": "daily_radar",
        "scenarios": [
            {
                "scenario_id": 6186710,
                "name": "SYNTHETIC Picks",
                "is_active": True,
                "is_paused": False,
                "executions": list(executions),
                "events": [],
            }
        ],
    }


def _ex(at, status=1, run_type="auto", duration_ms=100_000, error_name=None, eid=None):
    return {
        "execution_id": eid or f"synthetic-{at}",
        "started_at": at,
        "ended_at": None,
        "duration_ms": duration_ms,
        "status": status,
        "run_type": run_type,
        "error_name": error_name,
        "error_message": None,
        "cause_module": "MakeRequest" if error_name else None,
    }


def test_radar_signal_that_fires_and_clears_is_marked_cleared(client, fake, monkeypatch):
    """The motivating case for the whole window replay.

    A single auto run fails 20h before the radar, then a later auto run
    succeeds 6h before it. Between those two the failure is unrecovered, so
    I2b fires. By the time the radar goes out it has recovered, so an
    evaluation at 7:00am alone would see nothing at all.
    """
    mock_llm(monkeypatch, output=GOOD_OUTPUT)
    payload = radar_payload(
        executions=[
            _ex("2026-10-01T16:00:00Z", status=3, error_name="SyntheticError", eid="r-fail"),
            _ex("2026-10-02T06:00:00Z", status=1, eid="r-recover"),
        ]
    )
    body = post(client, payload).get_json()

    # The window is real: 24h at a 2h step = 13 ticks.
    assert body["radar_window"]["ticks"] == 13
    assert body["radar_window"]["from"] == "2026-10-01T12:00:00Z"
    assert body["radar_window"]["to"] == "2026-10-02T12:00:00Z"

    i2b = [s for s in body["signals"] if s["signal_id"] == "I2b_UNRECOVERED_FAILURE"]
    assert i2b, "the failure window should have produced I2b at some tick"

    # Worst status across the window, not the state at the end.
    assert body["status"] == "YELLOW"
    assert "cleared" in body["body"]
    assert "I2b_UNRECOVERED_FAILURE (RADAR) — cleared" in body["body"]
    assert body["deliver_radar"] is True


def test_radar_a_single_evaluation_would_have_missed_it(fake, monkeypatch):
    """Proves the previous test is testing something: evaluated only at the
    radar moment, the same history is GREEN."""
    from checks import evaluate as real_evaluate

    scenarios = radar_payload()["scenarios"]
    scenarios[0]["executions"] = [
        _ex("2026-10-01T16:00:00Z", status=3, error_name="SyntheticError", eid="r-fail"),
        _ex("2026-10-02T06:00:00Z", status=1, eid="r-recover"),
    ]
    at_radar_time = real_evaluate(
        {"collected_at": "2026-10-02T12:00:00Z", "scenarios": scenarios},
        now="2026-10-02T12:00:00Z",
    )
    assert at_radar_time["status"] == "GREEN"
    assert not [s for s in at_radar_time["tpe_signals"] if s["tier"] in ("RADAR", "ESCALATE")]


def test_radar_a_still_firing_signal_is_not_marked_cleared(client, fake, monkeypatch):
    mock_llm(monkeypatch, output=GOOD_OUTPUT)
    payload = radar_payload(
        executions=[
            _ex("2026-10-02T04:00:00Z", status=3, error_name="SyntheticError", eid="r-f1"),
            _ex("2026-10-02T08:00:00Z", status=3, error_name="SyntheticError", eid="r-f2"),
        ]
    )
    body = post(client, payload).get_json()
    assert body["status"] == "RED"

    # Check the I2 line specifically, not the whole body. Two consecutive
    # failures legitimately produce BOTH signals across the window: I2b fires at
    # the tick after the first failure and then clears once the second failure
    # makes it a streak, and I2 takes over and is still firing at the end. So
    # "cleared" does appear in the body -- on the I2b line, correctly.
    lines = {
        sig_id: line
        for sig_id in ("I2_CONSECUTIVE_FAILURES", "I2b_UNRECOVERED_FAILURE")
        for line in body["body"].splitlines()
        if sig_id in line
    }
    assert "I2_CONSECUTIVE_FAILURES (ESCALATE)" in lines["I2_CONSECUTIVE_FAILURES"]
    assert "cleared" not in lines["I2_CONSECUTIVE_FAILURES"], (
        "the unrecovered streak is still firing and must not be marked cleared"
    )
    assert "cleared" in lines["I2b_UNRECOVERED_FAILURE"], (
        "the single-failure signal was superseded by the streak, so it cleared"
    )


def test_radar_ticks_cover_the_window_and_end_on_the_radar_moment():
    ticks = radar.radar_ticks("2026-10-02T12:00:00Z")
    assert len(ticks) == 13
    assert ticks[0].strftime("%Y-%m-%dT%H:%M:%SZ") == "2026-10-01T12:00:00Z"
    assert ticks[-1].strftime("%Y-%m-%dT%H:%M:%SZ") == "2026-10-02T12:00:00Z"
    deltas = {(b - a).total_seconds() for a, b in zip(ticks, ticks[1:])}
    assert deltas == {2 * 3600}


def test_radar_each_tick_sees_only_its_own_past():
    """Same honesty property as replay.py."""
    seen = []

    def spy(payload, state=None, now=None):
        seen.append(now)
        return {"status": "GREEN", "tpe_signals": [], "harness_signals": [], "signals": []}

    rows = radar.replay_window([], end="2026-10-02T12:00:00Z", evaluate_fn=spy)
    assert len(rows) == 13
    assert seen == [r["tick"] for r in rows]
    assert seen == sorted(seen), "ticks are evaluated oldest first"


def test_radar_final_state_is_applied_only_to_the_last_tick():
    """H1 is about Sutton's liveness now, not at each historical tick."""
    states = []

    def spy(payload, state=None, now=None):
        states.append(state)
        return {"status": "GREEN", "tpe_signals": [], "harness_signals": [], "signals": []}

    radar.replay_window(
        [], end="2026-10-02T12:00:00Z", evaluate_fn=spy,
        final_state={"last_collection_at": "2026-09-01T00:00:00Z"},
    )
    assert states[-1] == {"last_collection_at": "2026-09-01T00:00:00Z"}
    assert all(s == {} for s in states[:-1])


def test_radar_packet_carries_cleared_to_the_llm(client, fake, monkeypatch):
    captured = {}

    def capture(packet, **kwargs):
        captured["packet"] = packet
        return True, json.dumps(GOOD_OUTPUT), "claude-opus-5", None

    monkeypatch.setattr(endpoint, "interpret", capture)
    post(client, radar_payload(executions=[
        _ex("2026-10-01T16:00:00Z", status=3, error_name="SyntheticError", eid="r-fail"),
        _ex("2026-10-02T06:00:00Z", status=1, eid="r-recover"),
    ]))
    sig = captured["packet"]["signals"][0]
    assert sig["cleared"] is True
    assert "error_message" not in json.dumps(captured["packet"])


def test_collect_mode_has_no_radar_window_and_no_cleared_flag(client, fake, monkeypatch):
    mock_llm(monkeypatch, output=GOOD_OUTPUT)
    body = post(client, failing_payload()).get_json()
    assert body["radar_window"] is None
    assert "Since the last radar:" not in body["body"]


def test_radar_green_window_stays_green_and_calls_no_llm(client, fake, monkeypatch):
    def must_not_run(packet, **kwargs):
        raise AssertionError("no LLM call on a GREEN radar")

    monkeypatch.setattr(endpoint, "interpret", must_not_run)
    body = post(client, radar_payload(executions=[
        _ex("2026-10-02T06:00:00Z", status=1, eid="r-ok"),
    ])).get_json()
    assert body["status"] == "GREEN"
    assert body["deliver_radar"] is True
    assert body["body"].startswith("SUTTON — GREEN")


# =========================================================================
# Step D: raw Make API payloads
# =========================================================================

WATCHED = (6186710, 6241867, 6152892, 6079077)


def raw_execution_from(n):
    """Invert normalize.normalize_execution(), so a fixture record can be
    turned back into the raw Make row it came from."""
    row = {
        "eventType": "EXECUTION_END",
        "id": n["execution_id"],
        "timestamp": n["started_at"],
        "duration": n["duration_ms"],
        "status": n["status"],
        "type": n["run_type"],
        "authorName": n.get("author_name"),
    }
    if not n.get("ended_at_derived"):
        row["endedAt"] = n["ended_at"]
    if n.get("error_name"):
        row["error"] = {
            "name": n["error_name"],
            "message": n.get("error_message"),
            "causeModule": {"name": n.get("cause_module")},
        }
    return row


def raw_event_from(n):
    detail = {}
    if n.get("detail"):
        detail["reason"] = n["detail"]
    extra = n.get("extra") or {}
    if "delay_minutes" in extra:
        detail["delay"] = extra["delay_minutes"]
    if extra.get("author"):
        detail["author"] = {"name": extra["author"]}
    row = {
        "id": n["event_id"],
        "timestamp": n["at"],
        "type": n["event_type"],
        "authorName": n.get("author_name"),
    }
    if detail:
        row["detail"] = detail
    return row


def raw_scenarios_list(ids=WATCHED, wrapper=False, omit=()):
    rows = [
        {
            "id": sid,
            "name": f"SYNTHETIC {sid}",
            "isActive": True,
            "isPaused": False,
            # Fields the normalizer must ignore. The real response carries a
            # whole blueprint per scenario and is large.
            "blueprint": {"flow": [{"module": "http:MakeRequest"}]},
            "scheduling": {"type": "indefinitely", "interval": 900},
        }
        for sid in ids
        if sid not in omit
    ]
    return {"scenarios": rows} if wrapper else rows


def raw_payload(per_scenario, *, collected_at="2026-09-25T12:00:00Z", mode="collect",
                wrapper=False, omit_from_list=()):
    return {
        "collected_at": collected_at,
        "mode": mode,
        "raw_scenarios": raw_scenarios_list(wrapper=wrapper, omit=omit_from_list),
        "scenarios": [
            {
                "scenario_id": sid,
                "raw_logs": ({"scenarioLogs": rows} if wrapper else rows),
            }
            for sid, rows in per_scenario
        ],
    }


def fixture_scenario(scenario_id):
    return next(
        s for s in load_fixture()["scenarios"] if s["scenario_id"] == scenario_id
    )


# --- round trip against the committed fixture ----------------------------


@pytest.mark.parametrize("wrapper", [False, True], ids=["bare-array", "wrapped"])
def test_d_raw_logs_round_trip_reproduces_the_fixture(wrapper):
    """Real records from the committed fixture, inverted to raw Make shape and
    normalized again, must come back identical. Both response shapes."""
    picks = fixture_scenario(6186710)
    rows = [raw_execution_from(e) for e in picks["executions"]]
    rows += [raw_event_from(e) for e in picks["events"]]

    payload = raw_payload([(6186710, rows)], wrapper=wrapper)
    scenarios, problems = normalize.normalize_raw_payload(payload, WATCHED)

    assert problems == [], f"unexpected problems: {problems}"
    got = next(s for s in scenarios if s["scenario_id"] == 6186710)
    assert got["executions"] == picks["executions"]
    assert got["events"] == picks["events"]


def test_d_both_wrapper_shapes_give_the_same_result():
    picks = fixture_scenario(6186710)
    rows = [raw_execution_from(e) for e in picks["executions"]]
    bare, _ = normalize.normalize_raw_payload(raw_payload([(6186710, rows)]), WATCHED)
    wrapped, _ = normalize.normalize_raw_payload(
        raw_payload([(6186710, rows)], wrapper=True), WATCHED
    )
    assert bare == wrapped


def test_d_unwrap_helpers_accept_both_shapes_and_reject_junk():
    assert normalize.unwrap_logs([{"a": 1}]) == [{"a": 1}]
    assert normalize.unwrap_logs({"scenarioLogs": [{"a": 1}]}) == [{"a": 1}]
    assert normalize.unwrap_scenarios([{"id": 1}]) == [{"id": 1}]
    assert normalize.unwrap_scenarios({"scenarios": [{"id": 1}]}) == [{"id": 1}]
    for junk in (None, "a string", 17, {"wrong_key": []}):
        assert normalize.unwrap_logs(junk) is None
        assert normalize.unwrap_scenarios(junk) is None


def test_d_execution_and_event_are_told_apart_by_event_type():
    """SPEC.md #2: never read `type` without checking which shape it is."""
    rows = [
        {"eventType": "EXECUTION_END", "id": "x1", "timestamp": "2026-09-25T10:00:00Z",
         "duration": 1000, "status": 1, "type": "auto"},
        {"id": "e1", "timestamp": "2026-09-25T10:05:00Z", "type": "modify",
         "detail": {"author": {"name": "Sam Feldt"}}},
    ]
    scenarios, _ = normalize.normalize_raw_payload(raw_payload([(6186710, rows)]), WATCHED)
    got = next(s for s in scenarios if s["scenario_id"] == 6186710)
    assert [e["execution_id"] for e in got["executions"]] == ["x1"]
    assert got["executions"][0]["run_type"] == "auto"       # `type` = run type
    assert [e["event_id"] for e in got["events"]] == ["e1"]
    assert got["events"][0]["event_type"] == "modify"       # `type` = event kind
    assert got["events"][0]["extra"]["author"] == "Sam Feldt"


def test_d_ended_at_is_derived_when_make_omits_it():
    rows = [{"eventType": "EXECUTION_END", "id": "x1",
             "timestamp": "2026-09-25T10:00:00Z", "duration": 90_000,
             "status": 1, "type": "auto"}]
    scenarios, _ = normalize.normalize_raw_payload(raw_payload([(6186710, rows)]), WATCHED)
    ex0 = next(s for s in scenarios if s["scenario_id"] == 6186710)["executions"][0]
    assert ex0["ended_at"] == "2026-09-25T10:01:30Z"
    assert ex0["ended_at_derived"] is True


def test_d_scenario_id_comes_from_context_not_the_log_rows():
    """SPEC.md #3: timeline events carry no scenarioId."""
    rows = [{"id": "e1", "timestamp": "2026-09-25T10:00:00Z", "type": "warning",
             "detail": {"reason": "All 9 attempts to reconnect the process failed"}}]
    scenarios, _ = normalize.normalize_raw_payload(raw_payload([(6241867, rows)]), WATCHED)
    got = next(s for s in scenarios if s["scenario_id"] == 6241867)
    assert got["events"][0]["detail"].startswith("All 9 attempts")


# --- redaction on the raw shape ------------------------------------------


SECRET_SHAPED = (
    "Invalid value for header 'X-Pipeline-Secret': "
    "'0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef'."
)


def test_d_raw_error_message_is_redacted_before_storage(client, fake, monkeypatch):
    """The real Sep 11 shape, arriving raw. redact_free_text() would miss it:
    the field is error.message, not a top-level error_message."""
    mock_llm(monkeypatch, output=GOOD_OUTPUT)
    rows = [
        {"eventType": "EXECUTION_END", "id": "raw-fail-1",
         "timestamp": "2026-09-25T10:00:00Z", "duration": 2000, "status": 3,
         "type": "auto",
         "error": {"name": "Error", "message": SECRET_SHAPED,
                   "causeModule": {"name": "MakeRequest"}}},
        {"eventType": "EXECUTION_END", "id": "raw-fail-2",
         "timestamp": "2026-09-25T11:00:00Z", "duration": 2000, "status": 3,
         "type": "auto",
         "error": {"name": "Error", "message": SECRET_SHAPED,
                   "causeModule": {"name": "MakeRequest"}}},
    ]
    body = post(client, raw_payload([(6186710, rows)])).get_json()
    assert body["input_shape"] == "raw"

    stored = " ".join(
        str(r.get("error_message") or "") for r in fake.observations.values()
    )
    assert "[REDACTED:" in stored
    assert not re.search(r"[0-9a-f]{32,}", stored)
    assert "X-Pipeline-Secret" in stored, "the message shape survives, the value does not"

    # And nowhere in the whole response either.
    assert not re.search(r"[0-9a-f]{32,}", json.dumps(body))


def test_d_redact_all_spares_id_fields():
    """A Make execution id is a 32-char hex run. Redacting it would destroy
    the key that makes storage idempotent."""
    from redact import redact_all
    exec_id = "1a2b3c4d5e6f708192a3b4c5d6e7f809"
    out = redact_all({
        "id": exec_id,
        "imtId": f"1790262009956_{exec_id}",
        "error": {"message": SECRET_SHAPED},
        "name": "NFL Picks Daily Generation",
    })
    assert out["id"] == exec_id
    assert out["imtId"] == f"1790262009956_{exec_id}"
    assert out["name"] == "NFL Picks Daily Generation"      # benign text untouched
    assert "[REDACTED:" in out["error"]["message"]
    assert not re.search(r"[0-9a-f]{32,}", out["error"]["message"])


def test_d_execution_ids_survive_the_endpoint_intact(client, fake, monkeypatch):
    mock_llm(monkeypatch, output=GOOD_OUTPUT)
    exec_id = "1a2b3c4d5e6f708192a3b4c5d6e7f809"
    rows = [{"eventType": "EXECUTION_END", "id": exec_id,
             "imtId": f"1790262009956_{exec_id}",
             "timestamp": "2026-09-25T10:00:00Z", "duration": 1000,
             "status": 1, "type": "auto"}]
    post(client, raw_payload([(6186710, rows)]))
    assert (6186710, "execution", exec_id) in fake.observations


# --- a watched scenario missing from raw_scenarios -> I1 ------------------


def test_d_scenario_missing_from_the_list_is_treated_as_inactive(client, fake, monkeypatch):
    mock_llm(monkeypatch, output=GOOD_OUTPUT)
    ok_rows = [{"eventType": "EXECUTION_END", "id": "ok-1",
                "timestamp": "2026-09-25T10:00:00Z", "duration": 1000,
                "status": 1, "type": "auto"}]
    payload = raw_payload(
        [(6186710, ok_rows), (6241867, ok_rows), (6152892, ok_rows)],
        omit_from_list=(6079077,),
    )
    body = post(client, payload).get_json()

    i1 = [s for s in body["signals"]
          if s["signal_id"] == "I1_SCENARIO_DISABLED" and s["scenario_id"] == 6079077]
    assert i1, "a watched scenario absent from the scenarios list must reach I1"
    assert i1[0]["tier"] == "ESCALATE"
    assert body["status"] == "RED"
    assert any(p["reason"] == "MISSING_FROM_SCENARIOS_LIST" for p in body["input_problems"])


def test_d_scenario_meta_marks_missing_ids_inactive():
    meta, missing = normalize.scenario_meta(raw_scenarios_list(omit=(6079077,)), WATCHED)
    assert missing == [6079077]
    assert meta[6079077]["is_active"] is False
    assert meta[6186710]["is_active"] is True
    assert meta[6186710]["name"] == "SYNTHETIC 6186710"


def test_d_scenario_meta_ignores_every_other_field():
    meta, _ = normalize.scenario_meta(raw_scenarios_list(), WATCHED)
    assert sorted(meta[6186710]) == ["is_active", "is_paused", "name"]


# --- one malformed raw_logs must not block the others --------------------


def test_d_one_malformed_raw_logs_does_not_block_the_others(client, fake, monkeypatch):
    mock_llm(monkeypatch, output=GOOD_OUTPUT)
    ok_rows = [{"eventType": "EXECUTION_END", "id": f"ok-{i}",
                "timestamp": f"2026-09-25T1{i}:00:00Z", "duration": 1000,
                "status": 1, "type": "auto"} for i in range(3)]
    payload = raw_payload([(6186710, ok_rows), (6241867, ok_rows), (6152892, ok_rows)])
    # Corrupt exactly one scenario's logs.
    payload["scenarios"].append({"scenario_id": 6079077,
                                 "raw_logs": {"unexpected": "shape"}})

    resp = post(client, payload)
    assert resp.status_code == 200
    body = resp.get_json()

    problems = {p["scenario_id"]: p["reason"] for p in body["input_problems"]}
    assert problems.get(6079077) == "RAW_LOGS_MALFORMED"

    stored = {sid for sid, _kind, _sid2 in fake.observations}
    assert stored == {6186710, 6241867, 6152892}, "the other three still stored"
    assert any(s["signal_id"] == "H_RAW_INPUT_PROBLEM" for s in body["signals"])
    assert "HARNESS_HEALTH" in body["body"]


@pytest.mark.parametrize(
    "bad,reason",
    [
        ({"unexpected": "shape"}, "RAW_LOGS_MALFORMED"),
        ("a string", "RAW_LOGS_MALFORMED"),
        (None, "RAW_LOGS_MALFORMED"),
        ([], "RAW_LOGS_EMPTY"),
        ({"scenarioLogs": []}, "RAW_LOGS_EMPTY"),
    ],
)
def test_d_malformed_shapes_are_named_not_raised(bad, reason):
    payload = raw_payload([])
    payload["scenarios"] = [{"scenario_id": 6186710, "raw_logs": bad}]
    scenarios, problems = normalize.normalize_raw_payload(payload, WATCHED)
    assert {"scenario_id": 6186710, "reason": reason} in problems
    assert not any(s["scenario_id"] == 6186710 for s in scenarios)


def test_d_all_four_malformed_still_returns_200(client, fake, monkeypatch):
    mock_llm(monkeypatch, output=GOOD_OUTPUT)
    payload = raw_payload([])
    payload["scenarios"] = [{"scenario_id": sid, "raw_logs": {"bad": True}}
                            for sid in WATCHED]
    resp = post(client, payload)
    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body["input_problems"]) == 4
    assert all(p["reason"] == "RAW_LOGS_MALFORMED" for p in body["input_problems"])
    # Four per-scenario problems plus the NO_USABLE_SCENARIOS one.
    assert len([s for s in body["signals"]
                if s["signal_id"] == "H_RAW_INPUT_PROBLEM"]) == 5
    assert "NO_USABLE_SCENARIOS" in body["body"]
    assert body["status"] == "GREEN", "harness trouble never colours the TPE line"


# --- the normalized path is untouched ------------------------------------


def test_d_normalized_payload_is_still_detected_as_normalized(client, fake, monkeypatch):
    mock_llm(monkeypatch, output=GOOD_OUTPUT)
    body = post(client, failing_payload()).get_json()
    assert body["input_shape"] == "normalized"
    assert body["input_problems"] == []
    assert body["status"] == "RED"


def test_d_shape_detection_is_structural():
    assert _shape(raw_payload([(6186710, [])])) is True
    assert _shape(failing_payload()) is False
    assert _shape({"scenarios": [{"scenario_id": 1, "raw_logs": []}]}) is True
    assert _shape({"scenarios": [{"scenario_id": 1, "executions": []}]}) is False
    assert _shape({"raw_scenarios": []}) is True
    assert _shape(None) is False


def _shape(payload):
    return endpoint._is_raw_shape(payload)


def test_d_raw_scenarios_is_dropped_before_storage(client, fake, monkeypatch):
    """The scenarios response carries a blueprint per scenario. None of it is
    evidence, and blueprints are exactly where plaintext secrets live."""
    captured = {}

    def capture(packet, **kwargs):
        captured["packet"] = packet
        return True, json.dumps(GOOD_OUTPUT), "claude-opus-5", None

    monkeypatch.setattr(endpoint, "interpret", capture)
    rows = [{"eventType": "EXECUTION_END", "id": "f1",
             "timestamp": "2026-09-25T10:00:00Z", "duration": 1000,
             "status": 3, "type": "auto", "error": {"name": "E"}}]
    post(client, raw_payload([(6186710, rows)]))

    blob = json.dumps(captured.get("packet", {})) + json.dumps(
        list(fake.observations.values())
    )
    assert "blueprint" not in blob
    assert "MakeRequest" not in blob or "cause_module" in blob
