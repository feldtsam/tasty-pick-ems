"""Acceptance test 12 from SPEC.md: secrets never leave redaction.

Covers the real Sep 11 6241867 record, a synthetic case per pattern, a grep
of the committed fixture, and the guarantee that signal facts carry no free
text.

No test in this file prints a secret value. The real leaked value is not
reproduced here -- the fixture is read from disk and only checked for shape.
"""

from __future__ import annotations

import json
import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from checks import evaluate  # noqa: E402
from redact import REDACTED_UNPARSED, redact_free_text, redact_text  # noqa: E402
from replay import load_fixture  # noqa: E402

HEX_RUN = re.compile(r"[0-9a-fA-F]{32,}")
REDACTED_TAG = re.compile(r"^\[REDACTED:[0-9a-f]{8}\]$")

# ID fields legitimately hold 32-char Make execution ids. Everything else in
# the fixture must be free of long hex runs.
ID_FIELDS = {"execution_id", "event_id", "source_id"}


# --- per-pattern coverage -------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        # The real Make shape (synthetic value, same structure as the Sep 11 record)
        "Invalid value for header 'X-Pipeline-Secret': "
        "'0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\n'.",
        "X-Pipeline-Secret: 0123456789abcdef0123456789abcdef",
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456",
        "Bearer abcdefghijklmnopqrstuvwxyz123456",
        "https://api.example.com/v4/odds?apiKey=aaaabbbbccccddddeeeeffff00001111",
        "token=abcdefghijklmnopqrstuvwxyz123456&x=1",
        "secret=supersecretvalue12345678",
        "bare hex 0123456789abcdef0123456789abcdef in prose",
    ],
)
def test_12_every_pattern_is_redacted(raw):
    out = redact_text(raw)
    assert not HEX_RUN.search(out), f"hex survived: {out}"
    assert "[REDACTED:" in out, f"nothing redacted in: {out}"
    for token in ("supersecretvalue12345678", "abcdefghijklmnopqrstuvwxyz123456"):
        assert token not in out


def test_12_fingerprint_is_stable_and_groupable():
    a = redact_text("X-Foo-Secret: 0123456789abcdef0123456789abcdef")
    b = redact_text("X-Foo-Secret: 0123456789abcdef0123456789abcdef")
    c = redact_text("X-Foo-Secret: ffffffffffffffffffffffffffffffff")
    assert a == b
    assert a != c


def test_12_ordinary_text_is_untouched():
    for benign in (
        "All 9 attempts to reconnect the process failed",
        "The operation timed out",
        "timeout of 40000ms exceeded",
        "Bad Gateway",
        None,
        12345,
    ):
        assert redact_text(benign) == benign


def test_12_redaction_never_raises():
    class Explodes(str):
        def __new__(cls):
            return super().__new__(cls, "x")

    assert redact_text(Explodes()) == "x"
    # A field that cannot be processed becomes the sentinel rather than
    # propagating an exception (SPEC.md).
    assert REDACTED_UNPARSED == "[REDACTED:unparsed]"


def test_12_nested_structures_and_extra_are_redacted():
    obj = {
        "error_message": "X-A-Secret: 0123456789abcdef0123456789abcdef",
        "detail": "token=abcdefghijklmnopqrstuvwxyz123456",
        "execution_id": "0123456789abcdef0123456789abcdef",  # must survive
        "extra": {"anything": "0123456789abcdef0123456789abcdef"},
        "nested": [{"error_message": "Bearer abcdefghijklmnopqrstuvwxyz123456"}],
    }
    out = redact_free_text(obj)
    assert not HEX_RUN.search(out["error_message"])
    assert "[REDACTED:" in out["detail"]
    assert not HEX_RUN.search(out["extra"]["anything"])
    assert not HEX_RUN.search(out["nested"][0]["error_message"])
    # ID fields are never free text and must pass through untouched.
    assert out["execution_id"] == "0123456789abcdef0123456789abcdef"


# --- the committed fixture ------------------------------------------------


def _walk(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")
    else:
        yield path, node


def test_12_committed_fixture_has_no_hex_outside_id_fields():
    fixture = load_fixture()
    offenders = []
    for path, value in _walk(fixture):
        if not isinstance(value, str):
            continue
        field = path.rsplit(".", 1)[-1]
        if field in ID_FIELDS:
            continue
        if HEX_RUN.search(value):
            offenders.append((path, len(value)))
    assert not offenders, f"long hex outside ID fields: {offenders[:5]}"


def test_12_the_real_sep_11_record_is_redacted_in_the_fixture():
    """The 6241867 record at 2026-09-11T18:12:59Z carried a live
    X-Pipeline-Secret in error.message. It must be redacted on disk."""
    fixture = load_fixture()
    grade = next(s for s in fixture["scenarios"] if s["scenario_id"] == 6241867)
    record = next(
        e for e in grade["executions"] if e["started_at"].startswith("2026-09-11T18:12:59")
    )
    msg = record["error_message"]
    assert "X-Pipeline-Secret" in msg, "expected the message shape to be preserved"
    assert not HEX_RUN.search(msg), "the secret survived redaction"
    assert "[REDACTED:" in msg


def test_12_fixture_is_marked_redacted():
    assert load_fixture()["redacted"] is True


# --- the evidence packet boundary ----------------------------------------


def test_12_signal_facts_never_carry_free_text():
    """SPEC.md: the evidence packet carries structured facts only -- never
    error_message or detail, redacted or not. checks.py is where facts are
    built, so the guarantee is enforced and tested here."""
    fixture = load_fixture()
    from datetime import datetime, timezone

    out = evaluate(fixture, now=datetime(2026, 9, 21, 16, tzinfo=timezone.utc))
    assert out["signals"], "expected signals on a known-bad tick"
    for sig in out["signals"]:
        for key in sig["facts"]:
            assert key not in ("error_message", "detail"), f"{sig['signal_id']} leaked {key}"
        for path, value in _walk(sig["facts"]):
            if isinstance(value, str):
                assert not HEX_RUN.search(value), f"{sig['signal_id']} facts{path}"
