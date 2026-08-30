"""
Signs a payload and forwards it to a Lovable backend webhook, so Make.com
never has to compute a signature itself.

COPIED from nfl/api/lovable_forward.py (itself copied from pipeline/api/
lovable_forward.py), not imported — see cfb/api/index.py's module
docstring, and the "duplicate rather than cross-import" rule stated
throughout this codebase, for why the CFB deployment doesn't depend on
nfl/'s or pipeline/'s code. Logic is byte-for-byte identical; only this
comment block differs.

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
once the same secret is configured on both sides — same open item the
MLB pipeline's copy of this module has.
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
    that happens.

    REAL BUG THIS CLOSES, confirmed twice in the MLB pipeline's copy of
    this function, in two different real environments — same risk applies
    here: os.environ.get(name, default) only substitutes `default` when
    `name` is entirely ABSENT — a variable that exists but resolves empty
    silently passes '' all the way down into forward_to_lovable() ->
    requests.post(), which fails deep inside urllib3 with a confusing
    `MissingSchema: Invalid URL ''` that points nowhere near the real
    cause. Two real, distinct ways this happens: (1) Vercel "Sensitive"
    env vars are write-only after creation — their value can never be
    read back to confirm what was actually saved, so a blank save goes
    undetected; (2) GitHub Actions' `${{ secrets.X }}` evaluates to an
    EMPTY STRING, not "unset", when the referenced secret doesn't exist at
    all. Catching it right here, at read time, turns a confusing
    multi-layer-deep failure into an immediate, specific, greppable log
    line.
    """
    value = os.environ.get(name)
    if value is None:
        return default
    if value.strip() == "":
        print(
            f"[env-config] WARNING: {name} is set but blank — falling back to "
            f"default {default!r}. Either the referenced secret/env var was "
            f"never actually created, or it was saved with an empty value.",
            flush=True,
        )
        return default
    return value


def truncate_for_log(text, limit: int = 2000):
    """
    The truncation forward_to_lovable() used to do internally, moved
    here so a caller can apply it ONLY at the point something is
    actually printed/logged for a human — never to a value that gets
    parsed programmatically (see forward_to_lovable's own docstring for
    the real bug this split fixes). Not exported as anything fancier
    than a plain slice: this is exactly what a print site needs, no
    more.
    """
    if text is None:
        return None
    return text[:limit]


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
    Signs and POSTs rows to a Lovable webhook. Returns {"success": bool,
    "status_code": int|None, "error": str|None, "response_body": str|None}
    — never includes the secret or the signature in the return value.

    `response_body` is captured on EVERY response, not just failures, so a
    caller can surface Lovable's own real received/upserted/deduped counts
    on success, not just an opaque "success: true".

    FULL, UNTRUNCATED — a real, confirmed production bug this used to be
    truncated to 2000 chars right here, which broke read_shelf_signal_
    history() (api/curate_home_shelves.py): it json.loads()'s this exact
    field, and a real week's worth of rows (confirmed: as few as 9 real
    shelf-signal rows) already exceeds 2000 characters, silently handing
    it a truncated, invalid JSON string every time. Truncating a value
    BEFORE something downstream parses it as JSON is wrong regardless of
    where the size threshold is set — raising the cap only delays the
    same failure at a slightly larger real data volume, it doesn't fix
    it. `error` is untruncated for the same reason: nothing here can
    know in advance whether a caller needs the full text (debugging a
    truncated-mid-sentence error message is its own real annoyance) or
    just a preview.

    The ORIGINAL reason a cap existed at all (confirmed via api/index.py's
    own comment on this, at its poll-market-value endpoint's print site):
    keeping this project's own Vercel function logs bounded and readable,
    since Vercel's logs otherwise only show a bare status code, not
    Lovable's actual response text. That's a real, legitimate concern —
    just the wrong LAYER to solve it at. It belongs at the point
    something is actually printed/logged for a human, not baked into the
    one shared function every programmatic caller (including one that
    parses the result as real JSON) also depends on. See api/index.py's
    print sites for where that truncation now actually happens.
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
        return {"success": True, "status_code": response.status_code, "error": None, "response_body": response.text}

    return {
        "success": False,
        "status_code": response.status_code,
        "error": response.text,  # never contains our secret — the signature/secret are never echoed by Lovable's own response
        "response_body": response.text,
    }
