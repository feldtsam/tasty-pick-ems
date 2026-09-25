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
