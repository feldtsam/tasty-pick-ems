"""Secret redaction for untrusted free text coming out of Make.

WHY THIS EXISTS: Make's execution history embeds the value of whatever the
failing HTTP module was holding into `error.message`. A real record on
scenario 6241867 (2026-09-11T18:12:59Z) carries the full X-Pipeline-Secret
value in plain text:

    Invalid value for header 'X-Pipeline-Secret': '<64 hex chars>\\n'.

`error_message` and `detail` are therefore untrusted free text, not metadata.
Per SPEC.md "Redaction", this module runs:

  - in the fixture builder, before any fixture touches disk
  - as the FIRST step of /api/sutton-run, before the payload is stored,
    logged, or used

and the endpoint never prints or logs the raw request body.

Redacted values become `[REDACTED:<first 8 hex of sha256>]`. The fingerprint
is stable, so two occurrences of the same secret group together without the
value ever being carried. It is not reversible.

Note the division of labour: this module keeps error text *storable*.
Keeping it out of the LLM call is a separate guarantee enforced in
packet.py, which never emits `error_message` or `detail` at all.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

__all__ = [
    "FREE_TEXT_FIELDS",
    "ID_FIELDS",
    "REDACTED_UNPARSED",
    "fingerprint",
    "redact_all",
    "redact_text",
    "redact_free_text",
]

REDACTED_UNPARSED = "[REDACTED:unparsed]"

# Fields that are untrusted free text. Everything else in a normalized record
# is a typed scalar produced by our own normalizer.
FREE_TEXT_FIELDS = ("error_message", "detail")

# SPEC.md: "except when the whole field is exactly a Make execution ID in an ID
# field (ID fields are never free text)". This matters for raw Make payloads,
# where redact_all() sweeps everything: a Make execution id IS a 32-char hex
# run, and `imtId` embeds one, so redacting them would destroy the very key
# that makes storage idempotent.
ID_FIELDS = frozenset(
    {
        "id",
        "imtId",
        "execution_id",
        "event_id",
        "source_id",
        "scenarioId",
        "scenario_id",
        "teamId",
        "organizationId",
        "authorId",
        "replayOfExecutionId",
        "hookId",
        "deviceId",
    }
)


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:8]


def _tag(value: str) -> str:
    return f"[REDACTED:{fingerprint(value)}]"


# --- patterns, applied in this order -------------------------------------
#
# Most specific first. Each earlier pattern removes a whole secret-shaped
# value, so the broad hex sweep at the end only ever sees leftovers.

# 1. The Make header-value message shape:
#       Invalid value for header 'X-Pipeline-Secret': '<value>'
#    Captures the *second* quoted run (the value), not the header name.
_HEADER_VALUE_MSG = re.compile(
    r"(header\s*['\"][^'\"]{0,64}['\"]\s*:\s*)(['\"])([^'\"]{20,}?)(\2)",
    re.IGNORECASE | re.DOTALL,
)

# 2. Header-style assignment: X-Anything-Secret: value, Authorization: value
_AUTH_HEADER = re.compile(
    r"((?:X-[A-Za-z0-9_\-]*(?:Secret|Key|Token)|Authorization)\s*[:=]\s*)"
    r"(['\"]?)([^\s'\",;]{8,})",
    re.IGNORECASE,
)

# 3. Bearer tokens
_BEARER = re.compile(r"(Bearer\s+)([A-Za-z0-9._\-+/=]{8,})", re.IGNORECASE)

# 4. Query-string style credentials
_QUERY_CRED = re.compile(
    r"((?:apikey|api_key|access_token|token|secret|password|passwd|pwd|key)"
    r"\s*=\s*)([^\s&'\"]{8,})",
    re.IGNORECASE,
)

# 5. Any bare run of 32+ hex characters. Free text only -- ID fields are
#    never passed through this module, so a 32-char execution id in an
#    `execution_id` field is untouched.
_HEX_RUN = re.compile(r"\b[0-9a-fA-F]{32,}\b")


def _sub_group(pattern: re.Pattern, text: str, value_group: int) -> str:
    def repl(m: re.Match) -> str:
        groups = list(m.groups())
        secret = groups[value_group - 1]
        if not secret:
            return m.group(0)
        prefix = "".join(g for g in groups[: value_group - 1] if g)
        suffix = "".join(g for g in groups[value_group:] if g)
        return f"{prefix}{_tag(secret)}{suffix}"

    return pattern.sub(repl, text)


def redact_text(value: Any) -> Any:
    """Redact secret-shaped substrings. Non-strings pass through untouched.

    Never raises: any failure yields REDACTED_UNPARSED, per SPEC.md.
    """
    if value is None or not isinstance(value, str):
        return value
    try:
        out = _sub_group(_HEADER_VALUE_MSG, value, 3)
        out = _sub_group(_AUTH_HEADER, out, 3)
        out = _sub_group(_BEARER, out, 2)
        out = _sub_group(_QUERY_CRED, out, 2)
        out = _HEX_RUN.sub(lambda m: _tag(m.group(0)), out)
        return out
    except Exception:  # noqa: BLE001 - redaction must never break a run
        return REDACTED_UNPARSED


def redact_free_text(obj: Any, fields: tuple[str, ...] = FREE_TEXT_FIELDS) -> Any:
    """Walk a nested structure and redact the named free-text fields.

    Everything under an `extra` key is redacted wholesale, since `extra` is a
    catch-all that may hold arbitrary upstream text.
    """
    try:
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                if k == "extra":
                    out[k] = _redact_everything(v)
                elif k in fields:
                    out[k] = redact_text(v)
                else:
                    out[k] = redact_free_text(v, fields)
            return out
        if isinstance(obj, list):
            return [redact_free_text(v, fields) for v in obj]
        return obj
    except Exception:  # noqa: BLE001
        return REDACTED_UNPARSED


def redact_all(obj: Any, id_fields: frozenset[str] = ID_FIELDS) -> Any:
    """Redact EVERY string in a structure, except values under an ID-named key.

    For raw Make API payloads. `redact_free_text()` is not enough there: the
    secret-bearing field is `error.message`, nested under `error`, not a
    top-level `error_message`, and raw `detail` is an object rather than a
    string. Rather than enumerate raw Make's shape -- which we do not control
    and which can change -- this sweeps the whole tree.

    Benign strings are unaffected: redact_text() only rewrites secret-shaped
    substrings, so scenario names and timestamps pass through untouched.

    Never raises.
    """
    try:
        if isinstance(obj, dict):
            return {
                k: (v if k in id_fields else redact_all(v, id_fields))
                for k, v in obj.items()
            }
        if isinstance(obj, list):
            return [redact_all(v, id_fields) for v in obj]
        return redact_text(obj)
    except Exception:  # noqa: BLE001 - redaction must never break a run
        return REDACTED_UNPARSED


def _redact_everything(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _redact_everything(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_everything(v) for v in obj]
    return redact_text(obj)
