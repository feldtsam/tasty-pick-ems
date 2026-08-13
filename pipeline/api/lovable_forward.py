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
import os

import requests

SIGNATURE_PREFIX = "sha256="
REQUEST_TIMEOUT_SECONDS = 10


def resolve_url_env(name: str, default: str) -> str:
    """
    Same fallback intent as os.environ.get(name, default), but treats a
    PRESENT-but-blank value the same as an absent one, logging loudly when
    that happens. Lives here (not in index.py, where it originated) so
    every caller that talks to Lovable shares ONE implementation —
    including standalone scripts like recent_statcast_form.py, which
    import this module already but don't (and shouldn't) import index.py's
    Flask app just to get this one function.

    REAL BUG THIS CLOSES, confirmed TWICE now in two different real
    environments: os.environ.get(name, default) only substitutes `default`
    when `name` is entirely ABSENT — a variable that exists but resolves
    empty silently passes '' all the way down into forward_to_lovable() ->
    requests.post(), which fails deep inside urllib3 with a confusing
    `MissingSchema: Invalid URL ''` that points nowhere near the real
    cause. Two real, distinct ways this happens, not one:
      1. Vercel "Sensitive" env vars are write-only after creation — their
         value can never be read back to confirm what was actually saved,
         so a blank save goes undetected (the original real case this was
         built for).
      2. GitHub Actions' `${{ secrets.X }}` evaluates to an EMPTY STRING,
         not "unset", when the referenced secret doesn't exist at all —
         confirmed by recent_statcast_form.py's real workflow run failing
         this exact way when RECENT_STATCAST_FORM_WRITE_URL was never
         added as a repo secret. There is no way to distinguish "secret
         doesn't exist" from "secret is empty" from inside a workflow's
         `env:` block — the env var is always present either way.
    Catching it right here, at read time, turns a confusing multi-layer-
    deep failure into an immediate, specific, greppable log line — and
    means "forgot to add a repo secret" degrades to the real default URL
    instead of crashing.
    """
    value = os.environ.get(name)
    if value is None:
        return default
    if value.strip() == "":
        print(
            f"[env-config] WARNING: {name} is set but blank — falling back to "
            f"default {default!r}. Either the referenced secret/env var was "
            f"never actually created (GitHub Actions: {name!r} isn't a real "
            f"repo secret yet), or it was saved with an empty value.",
            flush=True,
        )
        return default
    return value


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
    Returns {"success": bool, "status_code": int|None, "error": str|None,
    "response_body": str|None} — never includes the secret or the
    signature in the return value.

    `response_body` is captured on EVERY response, not just failures —
    added specifically so a caller can surface Lovable's own real
    received/upserted/deduped counts (or whatever it reports) on success,
    not just an opaque "success: true". Truncated the same as the error
    text, for the same reason: never let one webhook response balloon the
    caller's own response body.
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
        return {"success": False, "status_code": None, "error": f"{type(e).__name__}: {e}", "response_body": None}

    if 200 <= response.status_code < 300:
        return {"success": True, "status_code": response.status_code, "error": None, "response_body": response.text[:2000]}

    return {
        "success": False,
        "status_code": response.status_code,
        "error": response.text[:500],  # truncated, for debugging — never contains our secret
        "response_body": response.text[:2000],
    }
