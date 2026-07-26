"""
Signs the flattened HR-prop payload and forwards it to the Lovable backend
webhook, so Make.com never has to compute a signature itself.

Assumptions about what Lovable's endpoint expects — flagged clearly
because they're genuinely unverified, not because the code is uncertain
about its own behavior:

  1. Header name "X-Signature", value format "sha256=<hex digest>". This
     matches GitHub's own webhook convention (X-Hub-Signature-256) and is
     the most common pattern for this kind of HMAC verification, but it's
     a guess, not something read from Lovable's actual implementation —
     nobody here has visibility into that code. If Lovable expects the
     raw hex with no "sha256=" prefix, flip SIGNATURE_PREFIX to "" below.
  2. The receiving end verifies against the exact raw request body bytes
     (the standard, correct way to do HMAC webhook verification — GitHub,
     Stripe, etc. all work this way), not a re-serialized version of the
     parsed JSON. This module is careful to serialize the payload exactly
     once and reuse that same string for both signing and sending, so if
     Lovable follows the same standard practice, this will match byte for
     byte. If Lovable instead re-serializes before checking, key order can
     matter — this module uses sort_keys=True specifically to make that
     comparison exact even in that case.

Neither of these can be confirmed from this side alone. The real
confirmation is the round-trip test against the live Lovable endpoint,
once the same secret is configured on both sides.
"""
import hashlib
import hmac
import json

import requests

SIGNATURE_PREFIX = "sha256="
REQUEST_TIMEOUT_SECONDS = 10


def serialize_payload(rows: list) -> str:
    """
    The exact JSON string that gets both signed and sent — built once, used
    twice, so "signed" and "sent" can never drift apart. sort_keys=True
    removes key-order as a possible source of cross-implementation mismatch.
    """
    return json.dumps(rows, sort_keys=True, separators=(",", ":"))


def compute_signature(secret: str, payload_str: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload_str.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{SIGNATURE_PREFIX}{digest}"


def forward_to_lovable(rows: list, secret: str, url: str) -> dict:
    """
    Signs and POSTs the flattened rows to the Lovable webhook.
    Returns {"success": bool, "status_code": int|None, "error": str|None}
    — never includes the secret or the signature in the return value.
    """
    payload_str = serialize_payload(rows)
    signature = compute_signature(secret, payload_str)

    try:
        response = requests.post(
            url,
            data=payload_str.encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Signature": signature,
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as e:
        return {"success": False, "status_code": None, "error": f"{type(e).__name__}: {e}"}

    if 200 <= response.status_code < 300:
        return {"success": True, "status_code": response.status_code, "error": None}

    return {
        "success": False,
        "status_code": response.status_code,
        "error": response.text[:500],  # truncated, for debugging — never contains our secret
    }
